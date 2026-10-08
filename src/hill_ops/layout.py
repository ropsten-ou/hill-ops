"""The panes of hill-ops's window and their widths.

The app runs on the left. Beside it, it can run programs of its own: one in
the middle (the view, such as palace's preview) and one on the right (for
Claude), with the strip under those two; or one program over both until it
exits, such as an editor. With nothing beside it, the app has the window's
width and the strip goes under it.

Panes that leave the window for a while, such as the view while an editor
runs over it, go to windows of their own, which are never shown, and come
back as they were. New panes start in such a window too, then join the
window, so nothing jumps while they start.

The widths are hill-ops's Layout settings, a share of the window's width each;
when the window changes size, a tmux hook applies them again at once. The
strip follows what happens in the window through tmux's control mode: which
pane has the focus, and every change of the layout.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from settings_panel import Setting, panel_settings

from .runner import placed_channel

SESSION = "hill"
"""The tmux session hill-ops runs in."""
WIDTHS = [f"{n}%" for n in range(15, 55, 5)]
"""The widths a pane can take, as a share of the window's: 15% to 50%."""
APP_WIDTH = Setting(
    ("layout", "app"), "App width",
    "How much of the window's width the app takes, on the left, when it runs panes beside it.",
    WIDTHS, "25%",
)
CLAUDE_WIDTH = Setting(
    ("layout", "claude"), "Claude width",
    "How much of the window's width the Claude pane takes, on the right of the app's panes; off hides it.",
    ["off", *WIDTHS], "30%",
)
PANEL_HEIGHT = panel_settings()[0]
"""How much of the window's height the strip's panel takes: settings-panel's own setting."""


class Tmux:
    """tmux commands on hill-ops's own server, the one this pane runs in."""

    def run(self, *args: str) -> str:
        """Run a command, or several separated by ";", and return what it printed."""
        done = subprocess.run(["tmux", *args], capture_output=True, text=True)
        return done.stdout.strip()

    async def wait(self, channel: str) -> None:
        """Wait until `channel` is signalled (tmux wait-for)."""
        process = await asyncio.create_subprocess_exec(
            "tmux", "wait-for", channel, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            await process.wait()
        finally:
            if process.returncode is None:
                process.kill()

    async def notifications(self) -> AsyncIterator[tuple[str, list[str]]]:
        """What tmux's control mode reports, as (name, arguments), such as
        ("%window-pane-changed", ["@0", "%3"]), until the session ends. The
        control client never changes the window's size, and takes no output."""
        socket = os.environ.get("TMUX", "").split(",")[0]
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        process = await asyncio.create_subprocess_exec(
            "tmux", *(["-S", socket] if socket else []), "-C", "attach", "-t", SESSION, ";",
            "refresh-client", "-f", "no-output",
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            limit=1024 * 1024,
        )
        assert process.stdout is not None
        try:
            while raw := await process.stdout.readline():
                name, _, rest = raw.decode(errors="replace").rstrip("\n").partition(" ")
                if name.startswith("%") and name not in ("%begin", "%end", "%error"):
                    yield name, rest.split(" ")
        finally:
            if process.returncode is None:
                process.kill()


@dataclass(frozen=True)
class Side:
    """A program an app runs beside it: its name, for the log and Claude,
    its command line, and the folder it runs in."""

    name: str
    argv: tuple[str, ...]
    cwd: str | None = None

    @classmethod
    def parse(cls, value: Any) -> Side | None:
        """A side program as an app sends it, {name, argv, cwd}, or None."""
        if not isinstance(value, dict):
            return None
        argv = value.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            return None
        cwd = value.get("cwd") if isinstance(value.get("cwd"), str) else None
        return cls(str(value.get("name") or os.path.basename(argv[0])), tuple(argv), cwd)


def share(width: Any, default: str) -> float:
    """A width setting, such as "25%", as a fraction of the window."""
    text = width if isinstance(width, str) and width in WIDTHS else default
    return int(text.rstrip("%")) / 100


def nearest(fraction: float) -> str:
    """The width setting nearest to `fraction` of the window."""
    return min(WIDTHS, key=lambda w: abs(int(w.rstrip("%")) / 100 - fraction))


def window_size(layout: str) -> tuple[int, int] | None:
    """The window's size in a tmux layout, such as "89c0,120x39,0,0{...}"."""
    match = re.match(r"[0-9a-f]{4},(\d+)x(\d+),", layout)
    return (int(match[1]), int(match[2])) if match else None


def layout_panes(layout: str) -> set[str]:
    """The panes in a tmux layout, as pane ids ("%3")."""
    return {f"%{n}" for n in re.findall(r"\d+x\d+,\d+,\d+,(\d+)", layout)}


class Layout:
    """The panes of hill-ops's window. `app` and `strip` are the panes of the app
    and of the strip; `env` goes to every program it starts, such as the
    channel's path. `rows` is the strip's height, which the strip sets."""

    def __init__(self, tmux: Tmux, app: str, strip: str, env: dict[str, str]) -> None:
        self.tmux = tmux
        self.app = app
        self.strip = strip
        self.env = env
        self.rows = 1
        self.side: dict[str, tuple[Side, str]] = {}
        """What runs beside the app, by place ("view", "claude"): the program
        and its pane, shown or hidden."""
        self.parked: dict[tuple[str, str], tuple[Side, str]] = {}
        """The programs out of sight that still run, by place and name:
        those that one of another name took the place of."""
        self.over: tuple[Side, str] | None = None
        """The program over the side panes, and its pane."""
        self.claude_shown = True
        """Whether the Claude pane shows (Layout → Claude width isn't off)."""
        self.app_width = APP_WIDTH.default
        self.claude_width = CLAUDE_WIDTH.default
        self.row: list[str] = []
        """The panes above the strip, beside the app, left to right; none
        while the strip is under the app."""

    # -- what runs where --

    def set_side(self, view: Side | None, claude: Side | None) -> None:
        """Run `view` in the middle and `claude` on the right, or nothing
        there for None. A program already running stays as it is; one that
        changed starts again. One of another name takes the place, and the
        one there keeps running out of sight, until one of its name comes
        back; None stops the one there. While a program runs over them,
        they show once it has exited."""
        gone = []
        for place, side in (("view", view), ("claude", claude)):
            running = self.side.get(place)
            if running is not None and running[0] == side:
                continue
            if running is not None:
                del self.side[place]
                if side is not None and side.name != running[0].name:
                    self.parked[(place, running[0].name)] = running
                else:
                    gone.append(running[1])
            if side is None:
                continue
            parked = self.parked.pop((place, side.name), None)
            if parked is not None and parked[0] == side:
                self.side[place] = parked
                continue
            if parked is not None:
                gone.append(parked[1])
            pane = self.start(side)
            if pane:
                self.side[place] = (side, pane)
        self.arrange()
        for pane in gone:
            self.tmux.run("kill-pane", "-t", pane)

    def end(self, names: set[str]) -> None:
        """Stop the programs of these names, out of sight or shown."""
        gone = []
        for key, (side, pane) in list(self.parked.items()):
            if side.name in names:
                del self.parked[key]
                gone.append(pane)
        for place, (side, pane) in list(self.side.items()):
            if side.name in names:
                del self.side[place]
                gone.append(pane)
        if gone:
            self.arrange()
        for pane in gone:
            self.tmux.run("kill-pane", "-t", pane)

    def open_over(self, side: Side, colors: tuple[str, str] | None = None) -> str | None:
        """Run `side` over the side panes, which leave the window until it
        exits; its pane gets the focus. It's told how wide the Claude pane
        was, in HILL_CLAUDE_COLUMNS, so an editor can put a chat of its own
        there. `colors` are its pane's background without the focus and with
        it, as tmux styles, for a program that leaves its background to the
        terminal, as micro does. Its pane, or None if it couldn't start or
        one runs already."""
        if self.over is not None:
            return None
        extra = {}
        if (claude := self.side.get("claude")) is not None and claude[1] in self.row:
            width = self.tmux.run("display", "-p", "-t", claude[1], "#{pane_width}")
            if width.isdigit():
                extra["HILL_CLAUDE_COLUMNS"] = width
        pane = self.start(side, extra, over=True)
        if not pane:
            return None
        if colors is not None:
            self.tmux.run("set", "-p", "-t", pane, "window-style", colors[0], ";",
                          "set", "-p", "-t", pane, "window-active-style", colors[1])
        self.over = (side, pane)
        self.arrange()
        self.tmux.run("select-pane", "-t", pane)
        self.tmux.run("wait-for", "-S", placed_channel(pane))  # its program can start
        return pane

    def close_over(self) -> None:
        """The program over the side panes has exited: they come back."""
        if self.over is None:
            return
        _, pane = self.over
        self.over = None
        self.arrange()
        self.tmux.run("kill-pane", "-t", pane)

    def start(self, side: Side, extra: dict[str, str] | None = None, over: bool = False) -> str:
        """Start `side` in a window of its own, out of sight; its pane, or ""
        if tmux couldn't. It runs under hill-ops's runner, through the relay, so
        that it follows the mouse; over the side panes, the runner says when
        it has exited."""
        env = self.env | (extra or {}) | {"HILL_ARGV": json.dumps(list(side.argv))}
        argv = [sys.executable, "-m", "hill_ops", "_over" if over else "_side"]
        return self.tmux.run(
            "new-window", "-d", "-P", "-F", "#{pane_id}", *(["-c", side.cwd] if side.cwd else []),
            *[arg for name, value in env.items() for arg in ("-e", f"{name}={value}")], "--", *argv,
        )

    # -- the window --

    def wanted(self) -> list[str]:
        """The panes that belong above the strip now, left to right."""
        if self.over is not None:
            return [self.over[1]]
        view, claude = self.side.get("view"), self.side.get("claude")
        return [p for p in (view and view[1], claude and self.claude_shown and claude[1]) if p]

    def arrange(self) -> None:
        """Put the panes that belong above the strip there, hide those that
        don't, and size them all."""
        row = self.wanted()
        if row != self.row:
            self._arrange(row)
        self.apply()

    def _arrange(self, row: list[str]) -> None:
        if not row:
            # The strip back under the app, across the window; then the row goes.
            self.tmux.run("join-pane", "-v", "-f", "-d", "-s", self.strip, "-t", self.app, "-l", str(self.rows))
            for pane in self.row:
                self.tmux.run("break-pane", "-d", "-s", pane)
            self.row = []
            return
        current = list(self.row)
        if not current:
            # A column at the window's full height right of the app, then the strip under it.
            self.tmux.run("join-pane", "-h", "-f", "-d", "-s", row[0], "-t", self.app)
            self.tmux.run("join-pane", "-v", "-d", "-s", self.strip, "-t", row[0], "-l", str(self.rows))
            current = [row[0]]
        elif not set(current) & set(row):
            # Nothing stays: the first new pane takes the first old one's
            # place, which leaves for the new one's window.
            self.tmux.run("swap-pane", "-d", "-s", row[0], "-t", current[0])
            current[0] = row[0]
        for pane in [p for p in current if p not in row]:
            self.tmux.run("break-pane", "-d", "-s", pane)
            current.remove(pane)
        for i, pane in enumerate(row):
            if pane in current:
                continue
            right = next((p for p in row[i + 1:] if p in current), None)
            if right is not None:
                self.tmux.run("join-pane", "-h", "-b", "-d", "-s", pane, "-t", right)
                current.insert(current.index(right), pane)
            else:
                left = row[i - 1]
                self.tmux.run("join-pane", "-h", "-d", "-s", pane, "-t", left)
                current.insert(current.index(left) + 1, pane)
        self.row = current

    def relayout(self) -> list[str]:
        """The tmux commands that size the panes, for now and for the hook
        that runs them when the window changes size; the strip's height is
        #{@hill-height}."""
        commands = []
        if self.row:
            commands.append(["resize-pane", "-t", self.app, "-x", self.app_width])
            claude = self.side.get("claude")
            if claude is not None and len(self.row) > 1 and self.row[-1] == claude[1]:
                commands.append(["resize-pane", "-t", claude[1], "-x", self.claude_width])
        commands.append(["resize-pane", "-t", self.strip, "-y", "#{@hill-height}"])
        return [" ".join(command) for command in commands]

    def apply(self) -> None:
        """Size the panes as the settings say, now and whenever the window
        changes size."""
        commands = self.relayout()
        self.tmux.run("set", "-g", "@hill-relayout", " ; ".join(commands))
        now = [arg.replace("#{@hill-height}", str(self.rows)) for command in commands for arg in [*command.split(" "), ";"]]
        self.tmux.run(*now[:-1])

    def dragged(self, sizes: dict[str, tuple[int, int]], width: int) -> list[tuple[Setting, str]]:
        """The width a dragged border gave a pane, as the setting nearest to
        it: the app's, or else the Claude pane's, if it's more than a column
        from what its setting gives. tmux takes what the app's border gives
        partly from the Claude pane, so a change to the app's width is the
        one that counts. `sizes` are the panes' (width, height) and `width`
        the window's."""
        if not self.row or width <= 0:
            return []
        claude = self.side.get("claude")
        shown = [(APP_WIDTH, self.app, self.app_width)]
        if claude is not None and len(self.row) > 1 and self.row[-1] == claude[1]:
            shown.append((CLAUDE_WIDTH, claude[1], self.claude_width))
        for setting, pane, value in shown:
            if pane in sizes and abs(sizes[pane][0] - int(width * share(value, str(setting.default)))) > 1:
                return [(setting, nearest(sizes[pane][0] / width))]
        return []

    def shown(self) -> set[str]:
        """The panes in the window: the app's, the strip, and those beside the app."""
        return {self.app, self.strip, *self.row}

    def running(self) -> dict[str, str | None]:
        """The name of what runs in each place beside the app, or None."""
        return {place: self.side[place][0].name if place in self.side else None for place in ("view", "claude")}

    def panes(self) -> set[str]:
        """The panes it runs for the app, shown or not."""
        return ({pane for _, pane in [*self.side.values(), *self.parked.values()]}
                | ({self.over[1]} if self.over else set()))

    def names(self, app: str) -> dict[str, str]:
        """What each pane in the window holds, by pane: the app, the side
        programs' names, and the strip."""
        names = {self.app: app, self.strip: "the strip"}
        for side, pane in [*self.side.values(), *([self.over] if self.over else [])]:
            if pane in self.row:
                names[pane] = side.name
        return names

    def forget(self, panes: set[str]) -> list[str]:
        """Forget the side programs whose pane isn't among `panes`, every
        pane there is: they exited on their own. The names of those that
        went, but for those out of sight; the rest are sized again."""
        for key, (_, pane) in list(self.parked.items()):
            if pane not in panes:
                del self.parked[key]
        gone = []
        for place, (side, pane) in list(self.side.items()):
            if pane not in panes:
                del self.side[place]
                gone.append(side.name)
        if self.over is not None and self.over[1] not in panes:
            gone.append(self.over[0].name)
            self.over = None
        if gone:
            left = [p for p in self.row if p in panes]
            if self.row and not left:
                # The strip, alone beside the app now, goes back under it.
                self.tmux.run("join-pane", "-v", "-f", "-d", "-s", self.strip, "-t", self.app, "-l", str(self.rows))
            self.row = left
            self.arrange()
        return gone
