"""The relay a program over the side panes runs through: what it passes on
between tmux and the program, what it keeps, and what it asks of tmux."""

import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

from hill_ops.relay import ASK, Filter

MOVE, CLICK, RELEASE, DRAG = b"\x1b[<35;10;5M", b"\x1b[<0;3;4M", b"\x1b[<0;3;4m", b"\x1b[<32;4;4M"


def micro():
    """A Filter for a program that asked for clicks and drags, as text, as micro does."""
    relay = Filter()
    relay.wrote(b"\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1006h")
    return relay


def test_the_relay_asks_for_the_mouse_moves_while_the_program_uses_the_mouse():
    relay = Filter()
    assert relay.wrote(b"\x1b[?1049h\x1b[?1000h\x1b[?1002h") == b""  # not as text yet
    assert relay.wrote(b"text\x1b[?10") == b""  # cut off
    assert relay.wrote(b"06h") == ASK
    assert relay.wrote(b"\x1b[?1002h") == ASK  # tmux keeps one mouse mode: again
    assert relay.wrote(b"\x1b[?25h more text") == b""
    assert relay.wrote(b"\x1b[?1000l\x1b[?1002l\x1b[?1006l") == b"\x1b[?1004l"  # no more, nor the focus
    assert relay.wrote(b"\x1b[?1004h\x1b[?1000;1006h") == ASK
    assert relay.wrote(b"\x1b[?1000l") == b""  # it asked for the focus itself
    shell = Filter()
    assert shell.wrote(b"$ \x1b[?2004h") == b"" and not shell.asking  # no mouse: left as it is


def test_the_relay_keeps_what_the_program_didnt_ask_for():
    relay = micro()
    assert relay.heard(b"a" + MOVE + b"b") == (b"ab", [("move", 10, 5)])
    assert relay.heard(CLICK + DRAG + RELEASE) == (CLICK + DRAG + RELEASE, [])
    assert relay.heard(b"\x1b[O\x1b[I") == (b"", [("focus", False), ("focus", True)])
    assert relay.heard(b"\x1b") == (b"\x1b", [])  # Esc goes on at once
    assert relay.heard(b"x\x1b[<35;1") == (b"x", [])  # cut off
    assert relay.heard(b"2;3M") == (b"", [("move", 12, 3)])
    relay.wrote(b"\x1b[?1003h\x1b[?1004h")  # it asks for the moves and the focus itself
    assert relay.heard(MOVE + b"\x1b[I") == (MOVE + b"\x1b[I", [("move", 10, 5), ("focus", True)])
    clicks = Filter()
    clicks.wrote(b"\x1b[?1000h\x1b[?1006h")
    assert clicks.heard(DRAG + CLICK) == (CLICK, [])  # clicks only


CHILD = r'''
import os, sys, tty
tty.setraw(0)
os.write(1, b"\x1b[?1000h\x1b[?1002h\x1b[?1006h")
seen = b""
while not seen.endswith(b"q"):
    got = os.read(0, 1024)
    if got == b"s":
        size = os.get_terminal_size(0)
        os.write(1, b"[%d %d]" % (size.columns, size.lines))
    else:
        seen += got
os.write(1, b"<" + seen.hex().encode() + b">")
sys.exit(3)
'''


def read_until(fd, marker, timeout=10):
    seen = b""
    end = time.monotonic() + timeout
    while marker not in seen:
        assert time.monotonic() < end, f"no {marker!r} in {seen!r}"
        if select.select([fd], [], [], 0.1)[0]:
            try:
                seen += os.read(fd, 4096)
            except OSError:
                break
    return seen


def size(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def test_a_program_runs_through_the_relay_on_a_terminal_of_its_own():
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE") and not k.startswith("HILL_")}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-c", "import sys; from hill_ops.relay import run; sys.exit(run(sys.argv[1:]))",
                                   sys.executable, "-c", CHILD], env)
    try:
        size(fd, 80, 24)
        read_until(fd, ASK)  # the program uses the mouse: the relay asks for its moves
        os.write(fd, b"s")
        assert b"[80 24]" in read_until(fd, b"]")
        size(fd, 100, 30)  # the pane is resized
        time.sleep(0.2)
        os.write(fd, b"s")
        assert b"[100 30]" in read_until(fd, b"]")
        os.write(fd, MOVE + CLICK + b"\x1b[I" + RELEASE + b"q")
        seen = read_until(fd, b">")
        assert bytes.fromhex(seen[seen.index(b"<") + 1:seen.index(b">")].decode()) == CLICK + RELEASE + b"q"
    finally:
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    assert os.waitstatus_to_exitcode(status) == 3
