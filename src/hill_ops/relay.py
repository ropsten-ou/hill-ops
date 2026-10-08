"""A program over the side panes, such as micro, on a terminal of its own:
`hill-ops _over` runs it through this relay, so that it follows the mouse as
palace's panes do, though it can't see the mouse move itself (micro asks
tmux for clicks and drags only).

The relay passes on everything as it comes, but the mouse. While the
program uses the mouse, the relay asks tmux for the mouse's moves as well,
and for the focus coming and going; it keeps to itself what the program
didn't ask for, and takes the focus as the mouse moves over the pane,
lazily (hill-client's Hover). A program that doesn't use the mouse, such
as a shell, is left as it is, so tmux's own selection and scrolling go on
working there.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import subprocess
import termios
import tty

from hill_client import Hover

BUTTONS, DRAGS, MOVES, FOCUS, SGR = 1000, 1002, 1003, 1004, 1006
"""The terminal modes the relay follows: which of the mouse's doings are
reported (clicks; clicks and drags; all, moves too), the focus coming and
going, and mouse reports as text (SGR)."""
MOUSE = {BUTTONS, DRAGS, MOVES}
FOLLOWED = {*MOUSE, FOCUS, SGR}
MODE = re.compile(rb"\x1b\[\?([\d;]+)([hl])")
"""A program setting (h) or resetting (l) terminal modes."""
MODE_CUT = re.compile(rb"\x1b(\[(\?[\d;]*)?)?$")
"""The start of one, cut off at the end of what the program wrote."""
REPORT = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)[Mm]|\x1b\[[IO]")
"""What tmux reports: the mouse, as text, or the focus coming (I) or going (O)."""
REPORT_CUT = re.compile(rb"\x1b\[<[\d;]*$")
"""The start of a mouse report, cut off at the end of what tmux sent."""
ASK = b"\x1b[?1003h\x1b[?1004h"
"""The relay's requests: all the mouse's doings, and the focus."""
HIGH_WATER = 65536
"""Bytes waiting to be written, either way, before the relay stops reading
more."""
WAIT = 0.5
"""Seconds between looks at whether the program has exited, while nothing
passes."""
PANE = object()
"""The pane, as the part of the program that takes the focus (Hover)."""


class Filter:
    """What passes between tmux and the program, but the bytes themselves:
    the program's own modes, from what it writes; what the relay asks of
    tmux after it; and which of tmux's reports go on to the program."""

    def __init__(self) -> None:
        self.modes: set[int] = set()
        """The modes the program has set, of those the relay follows."""
        self.asking = False
        """Whether the relay asks tmux for the mouse's moves and the focus."""
        self._out = b""
        self._in = b""

    def wrote(self, data: bytes) -> bytes:
        """The program wrote `data`, which goes on to tmux as it is: what the
        relay asks of tmux after it. tmux keeps one mouse mode a pane, so the
        relay asks for the moves again each time the program sets one, while
        the program uses the mouse, as text; it stops asking for the focus
        once the program no longer does."""
        text, self._out = self._out + data, b""
        changed = False
        for numbers, how in MODE.findall(text):
            for mode in (int(n) for n in numbers.split(b";") if n.isdigit()):
                if mode in FOLLOWED:
                    (self.modes.add if how == b"h" else self.modes.discard)(mode)
                    changed = True
        if cut := MODE_CUT.search(text):
            self._out = text[cut.start():]
        if not changed:
            return b""
        if self.modes & MOUSE and SGR in self.modes:
            self.asking = True
            return ASK
        if self.asking:
            self.asking = False
            return b"" if FOCUS in self.modes else b"\x1b[?1004l"
        return b""

    def heard(self, data: bytes) -> tuple[bytes, list[tuple]]:
        """tmux sent `data` for the program: what of it goes on to the
        program, and what the relay heard: ("move", x, y) for a move of the
        mouse without a button, and ("focus", True or False)."""
        text, self._in = self._in + data, b""
        passed = bytearray()
        heard: list[tuple] = []
        at = 0
        for report in REPORT.finditer(text):
            passed += text[at:report.start()]
            at = report.end()
            if report[1] is None:
                heard.append(("focus", report[0] == b"\x1b[I"))
                wanted = FOCUS in self.modes
            else:
                button = int(report[1])
                if button & 32 and (button & 3) == 3:
                    heard.append(("move", int(report[2]), int(report[3])))
                    wanted = MOVES in self.modes
                elif button & 32:  # a drag
                    wanted = bool(self.modes & {DRAGS, MOVES})
                else:
                    wanted = bool(self.modes & MOUSE)
            if wanted:
                passed += report[0]
        rest = text[at:]
        if cut := REPORT_CUT.search(rest):
            self._in, rest = rest[cut.start():], rest[:cut.start()]
        passed += rest
        return bytes(passed), heard


def run(argv: list[str]) -> int:
    """Run `argv` on a terminal of its own, the size of this one, which
    must be a terminal, passing everything on but what Filter keeps, until
    it exits: its return code, as subprocess gives it (minus the signal
    that ended it), and 127 if it couldn't start."""
    size = fcntl.ioctl(0, termios.TIOCGWINSZ, bytes(8))
    pid, fd = pty.fork()
    if pid == 0:  # the program, on its terminal
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, size)
            os.execvp(argv[0], argv)
        except OSError as e:
            os.write(2, f"hill-ops: {argv[0]}: {e.strerror}\n".encode())
        os._exit(127)

    def resized(*_: object) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, fcntl.ioctl(0, termios.TIOCGWINSZ, bytes(8)))
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, resized)
    for signum in (signal.SIGINT, signal.SIGQUIT):
        signal.signal(signum, lambda *_: None)  # Ctrl-C and Ctrl-\ are the program's
    saved = termios.tcgetattr(0)
    tty.setraw(0)
    os.set_blocking(fd, False)
    try:
        status = _relay(fd, pid)
    finally:
        termios.tcsetattr(0, termios.TCSAFLUSH, saved)
        os.close(fd)
    return os.waitstatus_to_exitcode(status)


def _relay(fd: int, pid: int) -> int:
    """Pass bytes both ways between this terminal and the program's, `fd`,
    taking the focus as the mouse moves over the pane, until the program,
    `pid`, has exited: its wait status. A program it started may hold its
    terminal still, such as a shell's job left running, so it's the
    program's exit that counts."""
    filter, hover = Filter(), Hover()
    focused = True  # its pane had the focus as it started
    to_tmux, to_program = bytearray(), bytearray()
    tmux_open = True
    while True:
        reading = [fd] if len(to_tmux) < HIGH_WATER else []
        if tmux_open and len(to_program) < HIGH_WATER:
            reading.append(0)
        writing = ([1] if to_tmux else []) + ([fd] if to_program else [])
        readable, writable, _ = select.select(reading, writing, [], WAIT)
        if 1 in writable:
            del to_tmux[:_write(1, to_tmux)]
        if fd in writable:
            del to_program[:_write(fd, to_program)]
        if 0 in readable:
            data = _read(0) or b""
            if not data:  # the pane has gone
                tmux_open = False
            passed, heard = filter.heard(data)
            to_program += passed
            for what, *details in heard:
                if what == "focus":
                    focused = details[0]
                    if not focused:
                        hover.left(PANE)
                elif hover.moved((details[0], details[1]), PANE, focused) and hover.take_focus():
                    focused = True
        if fd in readable and (data := _read(fd)) != b"":
            if data:
                asking = filter.asking
                to_tmux += data + filter.wrote(data)
                if filter.asking and not asking:
                    focused = _has_focus()  # tmux only says so when it changes
            continue
        if fd in readable:  # its terminal has closed
            _, status = os.waitpid(pid, 0)
        else:
            done, status = os.waitpid(pid, os.WNOHANG)
            if not done:
                continue
        while data := _read(fd):  # what it wrote last
            to_tmux += data
        while to_tmux:
            del to_tmux[:_write(1, to_tmux)]
        return status


def _read(fd: int) -> bytes | None:
    """What a terminal has now: b"" once it has closed, None for nothing
    yet."""
    try:
        return os.read(fd, 65536)
    except BlockingIOError:
        return None
    except OSError:
        return b""


def _write(fd: int, data: bytearray) -> int:
    """Write what `fd` takes of `data` now: how much."""
    try:
        return os.write(fd, data)
    except (BlockingIOError, InterruptedError):
        return 0


def _has_focus() -> bool:
    """Whether the pane this runs in has the focus, as tmux says; True
    outside tmux."""
    pane = os.environ.get("TMUX_PANE")
    if not pane or not os.environ.get("TMUX"):
        return True
    try:
        done = subprocess.run(["tmux", "display", "-p", "-t", pane, "#{pane_active}#{window_active}"],
                              capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return True
    return done.stdout.strip() in ("11", "")
