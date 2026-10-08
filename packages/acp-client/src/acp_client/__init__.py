"""Talk to an ACP (Agent Client Protocol) agent from asyncio, such as
Claude's claude-agent-acp: run it as a child process and talk to it in
JSON-RPC, one message per line on its stdin and stdout, as micro-claude
does in micro.

The client offers the agent nothing of its own (no files, no terminal); the
agent's tools, if any, are its own. A permission the agent asks for goes to
a handler, if given, or is turned down. Errors are said in the client's own
words, never with the agent's message, which can name a file. The agent runs
without $HILL_SOCKET, so that nothing it starts joins the hill-ops it runs in.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

__all__ = ["Agent", "AgentError"]

LINE_LIMIT = 16 * 1024 * 1024
"""The longest line the agent may send: its updates can be long."""


class AgentError(Exception):
    """The agent couldn't start, stopped, or turned a request down."""


class Agent:
    """One agent and one session, started by the first `ask` (or `start`)
    and kept until `stop`, so it remembers what was said. `command` is the
    agent's command line and `cwd` the folder its session works in. `meta`
    goes with session/new: claude-agent-acp takes a system prompt and
    options there, such as its tools and model; None leaves it out.
    `permission(request)` hears each permission the agent asks for, the
    request's params with its tool call and options, and returns the
    optionId chosen, or None to turn it down; without it, every one is
    turned down. `session` is an earlier session to carry on, by its id:
    the agent loads it rather than starting a new one, so it remembers what
    was said in it, also after the app restarted; if it can't, it starts a
    new one. Questions are answered one at a time, in turn."""

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        meta: dict[str, Any] | None = None,
        permission: Callable[[dict], Awaitable[str | None]] | None = None,
        session: str | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.meta = meta
        self.permission = permission
        self.carry_on = session
        """An earlier session for the next start to carry on, by its id."""
        self.process: asyncio.subprocess.Process | None = None
        self.session: str | None = None
        """The session's id, once started: keep it to carry the session on
        in another Agent, after a restart say."""
        self.fresh = False
        """Whether the session has had no question yet."""
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 1
        self.reply: list[str] = []
        self.on_text: Callable[[str], None] | None = None
        self.on_update: Callable[[dict], None] | None = None
        self.reading: asyncio.Task | None = None
        self.turn = asyncio.Lock()

    async def ask(
        self,
        text: str | Callable[[bool], str],
        on_text: Callable[[str], None] | None = None,
        on_update: Callable[[dict], None] | None = None,
    ) -> str:
        """The agent's answer to `text`, in one piece, once the questions
        before it are answered. `text` may be a function that makes it, told
        whether the session is new, so that the agent hasn't heard anything
        yet; `on_text` hears the answer as it grows, all of it so far each
        time, and `on_update` each update the agent sends meanwhile, such as
        a tool call. Cancelling the wait stops the agent's turn."""
        async with self.turn:
            if self.session is None:
                await self._start()
            if callable(text):
                text = text(self.fresh)
            self.fresh = False
            self.reply, self.on_text, self.on_update = [], on_text, on_update
            try:
                await self._request("session/prompt", sessionId=self.session, prompt=[{"type": "text", "text": text}])
            except asyncio.CancelledError:
                self.cancel()
                raise
            finally:
                self.on_text = self.on_update = None
            return "".join(self.reply)

    async def start(self, on_update: Callable[[dict], None] | None = None) -> None:
        """Start the agent and its session now, if they aren't running,
        rather than at the first question. Carrying on an earlier session,
        the agent tells what was said in it again, and `on_update` hears it
        as it comes: each question as user_message_chunk updates, then the
        answer's updates, as `ask` has them."""
        async with self.turn:
            if self.session is None:
                await self._start(on_update)

    @property
    def busy(self) -> bool:
        """Whether a question is being answered, or waits its turn."""
        return self.turn.locked()

    def cancel(self) -> None:
        """Stop the turn under way, so that the next question needn't wait
        for it to end; the answer so far is what `ask` returns."""
        if self.session is not None:
            try:
                self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session}})
            except AgentError:
                pass

    async def stop(self) -> None:
        """End the agent, if it runs; the next `ask` starts another."""
        process, self.process, self.session = self.process, None, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.kill()

    async def _start(self, on_update: Callable[[dict], None] | None = None) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=LINE_LIMIT, cwd=self.cwd, env=_outside_hill(),
            )
        except OSError as e:
            raise AgentError(f"Claude needs {Path(self.command[0]).name}, which can't start") from e
        self.reading = asyncio.get_running_loop().create_task(self._read(self.process))
        try:
            hello = await self._request(
                "initialize", protocolVersion=1,
                clientCapabilities={"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            )
            meta = {"_meta": self.meta} if self.meta is not None else {}
            if self.carry_on is not None and (hello.get("agentCapabilities") or {}).get("loadSession"):
                await self._load(self.carry_on, meta, on_update)
            if self.session is None:
                result = await self._request("session/new", cwd=str(self.cwd), mcpServers=[], **meta)
                self.session, self.fresh = result.get("sessionId"), True
        except AgentError:
            await self.stop()
            raise
        if not self.session:
            await self.stop()
            raise AgentError("Claude's agent failed")
        self.carry_on = None

    async def _load(self, session: str, meta: dict, on_update: Callable[[dict], None] | None) -> None:
        """Carry on an earlier session: the agent tells what was said in it
        again, before it answers. One it turns down, gone say, is left for
        a new one."""
        self.on_update = on_update
        try:
            await self._request("session/load", sessionId=session, cwd=str(self.cwd), mcpServers=[], **meta)
        except AgentError:
            if self.process is None:
                raise  # the agent itself stopped
            return
        finally:
            self.on_update = None
        self.session, self.fresh = session, False

    async def _request(self, method: str, **params: Any) -> Any:
        id = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[id] = future
        self._send({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
        return await future

    def _send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None or self.process.stdin.is_closing():
            raise AgentError("Claude's agent stopped")
        self.process.stdin.write(json.dumps(message).encode() + b"\n")

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._handle(message)
        except (OSError, ValueError):
            pass  # gone, or a line over the limit
        if self.process is process:
            self.process = self.session = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(AgentError("Claude's agent stopped"))
        self.pending.clear()

    def _handle(self, message: dict) -> None:
        method = message.get("method")
        if method is None:  # an answer to one of ours
            future = self.pending.pop(message.get("id"), None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(AgentError(_describe(message["error"])))
            else:
                future.set_result(message.get("result") or {})
        elif method == "session/update":
            update = (message.get("params") or {}).get("update") or {}
            content = update.get("content") or {}
            if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                self.reply.append(str(content.get("text") or ""))
                if self.on_text is not None:
                    self.on_text("".join(self.reply))
            if self.on_update is not None and isinstance(update, dict):
                self.on_update(update)
        elif "id" in message:  # the agent asks us something
            if method == "session/request_permission" and self.permission is not None:
                asyncio.get_running_loop().create_task(self._permit(message))
            elif method == "session/request_permission":
                self._answer(message["id"], None)
            else:
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not offered"}})

    async def _permit(self, message: dict) -> None:
        """Ask the permission handler, then tell the agent what it chose."""
        assert self.permission is not None
        try:
            chosen = await self.permission(message.get("params") or {})
        except Exception:  # a handler that fails turns the permission down
            chosen = None
        self._answer(message["id"], chosen)

    def _answer(self, id: Any, chosen: str | None) -> None:
        outcome = {"outcome": "selected", "optionId": chosen} if chosen else {"outcome": "cancelled"}
        try:
            self._send({"jsonrpc": "2.0", "id": id, "result": {"outcome": outcome}})
        except AgentError:
            pass  # it has gone


def _outside_hill() -> dict[str, str]:
    """The agent's environment: the app's, without $HILL_SOCKET. Whatever
    the agent runs, such as palace or its tests, would otherwise join the
    hill-ops the app runs in, and its hello would take that hill-ops's panes over."""
    return {name: value for name, value in os.environ.items() if name != "HILL_SOCKET"}


def _describe(error: Any) -> str:
    """An error from the agent, in the client's words: its own message can
    name a file, and what an app shows may go in a log."""
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    if "auth" in message.casefold() or "login" in message.casefold():
        return "Claude needs a login: run claude, then /login"
    code = error.get("code") if isinstance(error, dict) else None
    return f"Claude's agent turned the request down ({code})" if code is not None else "Claude's agent failed"
