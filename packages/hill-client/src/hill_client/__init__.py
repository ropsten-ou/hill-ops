"""Join hill-ops's channel from a Python app.

hill-ops runs an app above a strip for its settings, help, command line and
questions. Over the channel the app says hello (its name, settings profile,
app-wide keys, commands, help, README and key tree), asks for any of those, reports
its errors for hill-ops's event log, and hears when a setting changed, a hint
was clicked, a command was entered or a question answered.
Messages are JSON-RPC notifications, one per line, over the local socket
whose path is in $HILL_SOCKET.

A program in hill-ops's window can also take the focus when the mouse moves
over it (`take_focus`), for focus that follows the mouse, lazily (`Hover`),
and put text in the system clipboard (`copy`).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterable
from typing import Any

__all__ = ["SOCKET", "Client", "Hover", "connect", "copy", "line", "take_focus"]

SOCKET = "HILL_SOCKET"
"""The environment variable holding the channel's path, set for every
program started inside hill-ops."""


def take_focus(pane: str | None = None) -> bool:
    """Give the focus in hill-ops's window to `pane`, the tmux pane this program
    runs in unless another is named, as a click on it would: for a program
    whose focus follows the mouse. Not while hill-ops's strip keeps the focus,
    which it does until you're done with the help, the command line or a
    question there; the panel lets it go. Whether `pane` has the focus now;
    False outside tmux."""
    pane = pane or os.environ.get("TMUX_PANE")
    if not pane or not os.environ.get("TMUX"):
        return False
    # One call: ":" is the active pane of the window, @hill-strip the
    # strip's, and @hill-keep 0 while the strip lets the focus go.
    try:
        done = subprocess.run(
            ["tmux", "if", "-F", "-t", ":", "#{||:#{!=:#{pane_id},#{@hill-strip}},#{==:#{@hill-keep},0}}",
             f"select-pane -t {pane}", ";", "display", "-p", "-t", ":", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.stdout.strip() == pane


def copy(text: str) -> bool:
    """Put `text` in the system clipboard with the command hill-ops's tmux
    copies with (copy-command: pbcopy, wl-copy, xclip or xsel), for a
    terminal that ignores OSC 52, such as Konsole or GNOME Terminal. Call
    it as well as writing OSC 52, which reaches the terminal over ssh.
    Whether it ran; False outside tmux, or where tmux has no such
    command."""
    if not os.environ.get("TMUX"):
        return False
    try:
        command = subprocess.run(
            ["tmux", "show", "-gv", "copy-command"], capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if not command:
            return False
        # The clipboard tool may stay behind to serve the clipboard (xclip,
        # wl-copy): nothing waits on its output.
        done = subprocess.run(
            command, shell=True, input=text.encode(), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=2, start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


class Hover:
    """Focus that follows the mouse, lazily: the part the mouse moves over
    takes the focus, but where the focus left from under the mouse, by a
    key say, a nudge doesn't take it back: the mouse has to move a few
    cells first. So a hand brushing the mouse or the trackpad while you
    type never sends your keys elsewhere. A part is whatever the program
    says it is: its pane as a whole, or a part of it, such as palace's list
    or calendar."""

    NUDGE = (2, 1)
    """How far the mouse may move, in columns and rows, from where it rested
    when the focus left from under it, without taking the focus back."""
    RETRY = 0.25
    """Seconds before asking hill-ops for the focus again after it said no,
    while its strip keeps the focus."""

    def __init__(self) -> None:
        self.seen: tuple[int, int] | None = None
        """Where the mouse was last seen, as (x, y) in the program's pane."""
        self.part: object = None
        """What it was over then; None for nothing that takes the focus."""
        self.resting = False
        """Whether the mouse rests where the focus left from under it."""
        self.rest: tuple[int, int] | None = None
        """Where it rests; None until it's seen."""
        self.refused = 0.0
        """When hill-ops last said no to taking the focus."""

    def moved(self, at: tuple[int, int], part: object, focused: bool) -> bool:
        """The mouse moved to `at`, (x, y) such as a Textual event's
        `screen_offset`, over `part`, which has the focus or not: whether
        `part` should take it now."""
        self.seen, self.part = at, part
        if part is None:
            return False
        if focused:
            self.resting = False
            return False
        if self.resting:
            if self.rest is None:
                self.rest = at
            if abs(at[0] - self.rest[0]) <= self.NUDGE[0] and abs(at[1] - self.rest[1]) <= self.NUDGE[1]:
                return False
            self.resting = False
        return True

    def left(self, part: object) -> None:
        """The focus left `part`. If the mouse was over it, or hasn't been
        seen over anything since, it rests there until it moves away."""
        if self.part is None or part is self.part:
            self.resting = True
            self.rest = self.seen if self.part is not None else None

    def take_focus(self) -> bool:
        """Take hill-ops's focus for the pane this program runs in (see
        `take_focus`); hill-ops says no while its strip keeps the focus, and
        isn't asked again for a moment."""
        if time.monotonic() - self.refused < self.RETRY:
            return False
        if take_focus():
            return True
        self.refused = time.monotonic()
        return False


def line(method: str, **params: Any) -> bytes:
    """One message: a JSON-RPC notification on a line of its own."""
    return (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode()


async def connect(path: str, wait: float = 5.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open the channel, retrying every 50 ms for up to `wait` seconds while
    hill-ops's strip starts."""
    loop = asyncio.get_running_loop()
    give_up = loop.time() + wait
    while True:
        try:
            return await asyncio.open_unix_connection(path)
        except (FileNotFoundError, ConnectionRefusedError):
            if loop.time() >= give_up:
                raise
            await asyncio.sleep(0.05)


def _tree(nodes: Iterable[tuple]) -> list[list]:
    """A key tree as lists: `[key, label, line]` for a command, `[key, label,
    [nodes...]]` for a group."""
    return [[key, label, line if isinstance(line, str) else _tree(line)] for key, label, line in nodes]


class Client:
    """A connection to hill-ops. It says hello once connected, and again after
    reconnecting if the strip restarts; messages sent while it isn't
    connected wait until it is. Handlers get each message's params."""

    def __init__(
        self,
        path: str,
        app: str,
        profile: str | None = None,
        keys: Iterable[tuple] = (),
        commands: Iterable[tuple] = (),
        help: Iterable[tuple] = (),
        docs: str | None = None,
        tree: Iterable[tuple] = (),
    ) -> None:
        self.path = path
        self.hello = {
            "app": app,
            "profile": profile,
            "keys": [list(k) for k in keys],
            "commands": [list(c) for c in commands],
            "help": [[title, [list(e) for e in entries]] for title, entries in help],
            "docs": docs,
            "tree": _tree(tree),
            "pane": os.environ.get("TMUX_PANE"),
        }
        self.handlers: dict[str, Callable[[dict], Any]] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._waiting: list[bytes] = []
        self._closed = False

    @classmethod
    def from_env(
        cls,
        app: str,
        profile: str | None = None,
        keys: Iterable[tuple] = (),
        commands: Iterable[tuple] = (),
        help: Iterable[tuple] = (),
        docs: str | None = None,
        tree: Iterable[tuple] = (),
    ) -> Client | None:
        """A client for the channel in $HILL_SOCKET, or None outside hill."""
        path = os.environ.get(SOCKET)
        return cls(path, app, profile, keys, commands, help, docs, tree) if path else None

    @property
    def connected(self) -> bool:
        return self._writer is not None

    def on(self, method: str, handler: Callable[[dict], Any]) -> None:
        """Call `handler(params)` for each `method` message from hill-ops."""
        self.handlers[method] = handler

    def send(self, method: str, **params: Any) -> None:
        """Send a message now, or once connected. Never waits."""
        message = line(method, **params)
        if self._writer is not None:
            self._writer.write(message)
        else:
            self._waiting = [*self._waiting[-99:], message]

    async def run(self) -> None:
        """Stay connected until closed: say hello, pass on what hill-ops sends,
        and reconnect when the connection drops."""
        delay = 0.05
        while not self._closed:
            try:
                reader, writer = await connect(self.path, wait=0)
            except OSError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 1.0)
                continue
            delay = 0.05
            self._writer = writer
            writer.write(line("hello", **self.hello))
            for message in self._waiting:
                writer.write(message)
            self._waiting = []
            try:
                while raw := await reader.readline():
                    await self._dispatch(raw)
            except (OSError, ValueError):
                pass  # dropped, or a line too long: connect again
            finally:
                self._writer = None
                writer.close()

    def close(self) -> None:
        """Stop `run()` and close the connection."""
        self._closed = True
        if self._writer is not None:
            self._writer.close()

    async def _dispatch(self, raw: bytes) -> None:
        try:
            message = json.loads(raw)
            handler = self.handlers.get(message["method"])
            params = message.get("params") or {}
        except (ValueError, KeyError, TypeError):
            return
        if handler is not None:
            result = handler(params)
            if inspect.isawaitable(result):
                await result
