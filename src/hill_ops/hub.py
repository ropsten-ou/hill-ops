"""The channel's hub: a local socket where apps say hello, ask the strip for
their settings, their help, the command line, their key tree, an answer to
a question or Claude, report their errors, send an overview for the
panel's first tab, and hear back when a setting changed, a hint was
clicked, a command was entered, a question answered or a line of their
overview picked.

The app in the pane with the focus owns the strip: the one whose own pane
it is, or the one hill-ops runs it for, beside it. Apps in the same pane form a
stack: the last one to say hello owns the strip, and when it leaves, the
one before it is back (palace once ran micro in its own pane, so micro's
keys showed while it ran). The hub never waits on an app.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hill_client import line


@dataclass(eq=False)
class Peer:
    """An app on the channel."""

    writer: asyncio.StreamWriter
    app: str = ""
    profile: str | None = None
    keys: list[list] = field(default_factory=list)
    """App-wide keys for the strip, as [key, label, action] lists."""
    commands: list[list] = field(default_factory=list)
    """For the command line: [name, args, what it does, choices?] lists."""
    help: list[list] = field(default_factory=list)
    """For the help: [title, [[key, what it does], ...]] sections."""
    docs: str | None = None
    """The path to the app's README, for Claude."""
    tree: list[list] = field(default_factory=list)
    """Its key tree: [key, label, line] for a command, [key, label, [nodes]]
    for a group (keytree)."""
    pane: str | None = None
    """The tmux pane the app runs in, as it says in hello."""
    side: set[str] = field(default_factory=set)
    """The panes hill-ops runs beside the app, for it."""
    overview: dict | None = None
    """The app's overview, for the panel's first tab, as it last sent it."""


REQUESTS = ("settings.open", "command.open", "help.open", "tree.open", "ask", "claude.open", "panes", "panes.end", "over", "overview")
"""What an app can ask the strip for."""
REPORTS = ("error",)
"""What an app tells hill-ops for the event log alone."""
REPLIES = ("settings.changed", "run", "command", "answer", "over.done", "overview.pick")
"""What hill-ops tells apps."""


class Hub:
    def __init__(
        self,
        path: Path,
        on_hello: Callable[[Peer], None],
        on_change: Callable[[], None],
        on_request: Callable[[Peer, str, dict], None],
        on_leave: Callable[[Peer], None] = lambda peer: None,
        on_report: Callable[[Peer, str, dict], None] = lambda peer, method, params: None,
    ) -> None:
        self.path = path
        self.on_hello = on_hello
        self.on_change = on_change
        """The stack changed: an app said hello or left."""
        self.on_request = on_request
        """An app asked for one of REQUESTS."""
        self.on_leave = on_leave
        """An app that had said hello left."""
        self.on_report = on_report
        """An app told hill-ops one of REPORTS."""
        self.stack: list[Peer] = []
        self.server: asyncio.Server | None = None
        self.focused: str | None = None
        """The tmux pane with the focus, as the strip last heard."""

    @property
    def active(self) -> Peer | None:
        """The app that owns the strip: the last to say hello of those in
        the pane with the focus, or that hill-ops runs it for; else the last to
        say hello."""
        if self.focused is not None:
            for peer in reversed(self.stack):
                if self.focused == peer.pane or self.focused in peer.side:
                    return peer
        return self.stack[-1] if self.stack else None

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)  # left by a hill-ops that didn't clean up
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path))
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        for peer in self.stack:
            peer.writer.close()
        if self.server is not None:
            self.server.close()
        self.path.unlink(missing_ok=True)

    def send(self, app: str, method: str, **params) -> None:
        """Tell every connection of `app` something, without waiting."""
        for peer in self.stack:
            if peer.app == app:
                self.reply(peer, method, **params)

    def reply(self, peer: Peer, method: str, **params) -> None:
        """Tell one connection something, without waiting; nothing if it has gone."""
        if not peer.writer.is_closing():
            peer.writer.write(line(method, **params))

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = Peer(writer)
        try:
            while raw := await reader.readline():
                self._handle(peer, raw)
        except (OSError, ValueError):
            pass  # dropped, or a line too long
        finally:
            if peer in self.stack:
                self.stack.remove(peer)
                self.on_leave(peer)
                self.on_change()
            writer.close()

    def _handle(self, peer: Peer, raw: bytes) -> None:
        try:
            message = json.loads(raw)
            method, params = message["method"], message.get("params") or {}
        except (ValueError, KeyError, TypeError):
            return
        if method == "hello":
            peer.app = str(params.get("app") or "")
            peer.profile = params.get("profile")
            peer.keys = [k for k in params.get("keys") or [] if isinstance(k, list) and len(k) >= 2]
            peer.commands = [c for c in params.get("commands") or [] if isinstance(c, list) and c and isinstance(c[0], str)]
            peer.help = [
                s for s in params.get("help") or []
                if isinstance(s, list) and len(s) == 2 and isinstance(s[0], str) and isinstance(s[1], list)
            ]
            peer.tree = params["tree"] if isinstance(params.get("tree"), list) else []
            peer.docs = params["docs"] if isinstance(params.get("docs"), str) else None
            peer.pane = params["pane"] if isinstance(params.get("pane"), str) else None
            if peer in self.stack:
                self.stack.remove(peer)
            self.stack.append(peer)
            self.on_hello(peer)
            self.on_change()
        elif method in REQUESTS and peer in self.stack:
            self.on_request(peer, method, params)
        elif method in REPORTS and peer in self.stack:
            self.on_report(peer, method, params)
