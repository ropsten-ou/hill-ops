"""A stand-in for claude-agent-acp in the tests. It writes every message it
gets to $FAKE_AGENT_LOG, one per line, and answers each prompt in two
chunks: a tip with $FAKE_TIP (or no tip), a question with $FAKE_ANSWER (or
"echo", the question back). $FAKE_AGENT changes what it does: "login" turns
session/new down as a logged-out agent would, "permission" asks for a
permission before answering, "exit" stops mid-answer, "hang" answers only
once the turn is cancelled."""

import json
import os
import sys


def send(message):
    message["jsonrpc"] = "2.0"
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def chunk(text):
    update = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}
    send({"method": "session/update", "params": {"sessionId": "s1", "update": update}})


def answer(text):
    if text.startswith("Question"):
        reply = os.environ.get("FAKE_ANSWER", "echo")
        return text.rpartition("Their question: ")[2] if reply == "echo" else reply
    return os.environ.get("FAKE_TIP", '{"tip": null}')


mode = os.environ.get("FAKE_AGENT", "")
hung = None
for line in sys.stdin:
    with open(os.environ["FAKE_AGENT_LOG"], "a") as log:
        log.write(line)
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
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
        if mode == "permission":
            send({"id": 99, "method": "session/request_permission", "params": {"sessionId": "s1", "options": []}})
            reply = sys.stdin.readline()
            with open(os.environ["FAKE_AGENT_LOG"], "a") as log:
                log.write(reply)
        text = answer(message["params"]["prompt"][0]["text"])
        chunk(text[: len(text) // 2])
        chunk(text[len(text) // 2:])
        send({"id": message["id"], "result": {"stopReason": "end_turn"}})
