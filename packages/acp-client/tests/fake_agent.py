"""A stand-in ACP agent for acp-client's tests. It writes every message it
gets to $FAKE_AGENT_LOG, one per line, and answers each prompt with the
prompt's text, in two chunks. $FAKE_AGENT changes what it does: "login"
turns session/new down as a logged-out agent would, "tool" reports a tool
call and asks permission for it first, "exit" stops mid-answer, "hang"
answers only once the turn is cancelled, "env" answers with the
environment variable the prompt names, or "unset". session/load tells an
earlier question and its answer again, unless the mode is "gone", which
turns it down, or "noload", whose agent can't load sessions."""

import json
import os
import sys


def send(message):
    message["jsonrpc"] = "2.0"
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def update(update):
    send({"method": "session/update", "params": {"sessionId": "s1", "update": update}})


def log(line):
    with open(os.environ["FAKE_AGENT_LOG"], "a") as f:
        f.write(line)


mode = os.environ.get("FAKE_AGENT", "")
hung = None
for line in sys.stdin:
    log(line)
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        capabilities = {} if mode == "noload" else {"loadSession": True}
        send({"id": message["id"], "result": {"protocolVersion": 1, "agentCapabilities": capabilities}})
    elif method == "session/load":
        if mode == "gone":
            send({"id": message["id"], "error": {"code": -32002, "message": "Resource not found: /home/x/s.jsonl"}})
            continue
        update({"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "earlier"}})
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "said earlier"}})
        send({"id": message["id"], "result": {}})
    elif method == "session/new":
        if mode == "login":
            send({"id": message["id"], "error": {"code": -32000, "message": "Authentication required for /home/x/notes"}})
        else:
            send({"id": message["id"], "result": {"sessionId": "s1"}})
    elif method == "session/cancel" and hung is not None:
        send({"id": hung, "result": {"stopReason": "cancelled"}})
        hung = None
    elif method == "session/prompt":
        if mode == "exit":
            sys.exit(1)
        if mode == "hang":
            hung = message["id"]
            continue
        if mode == "tool":
            call = {"toolCallId": "t1", "title": "Edit notes.md", "kind": "edit"}
            update({"sessionUpdate": "tool_call", "status": "pending", **call})
            options = [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                       {"optionId": "no", "name": "Reject", "kind": "reject_once"}]
            send({"id": 99, "method": "session/request_permission",
                  "params": {"sessionId": "s1", "toolCall": call, "options": options}})
            reply = sys.stdin.readline()
            log(reply)
            update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
        text = message["params"]["prompt"][0]["text"]
        if mode == "env":
            text = os.environ.get(text, "unset")
        for part in (text[: len(text) // 2], text[len(text) // 2:]):
            update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": part}})
        send({"id": message["id"], "result": {"stopReason": "end_turn"}})
