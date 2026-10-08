"""Runs the app in its pane, then ends the session: `hill-ops _run`. And runs
a program beside the app, such as Claude: `hill-ops _side`, or over its side
panes, such as an editor: `hill-ops _over`.

`_side` runs the program through the relay (relay.py), so that it follows
the mouse if it uses the mouse, and exits with its exit status.

`_run` keeps what the app left on screen (a report, a traceback) and its
exit status in the session's folder, then detaches the client, which ends
`tmux attach` in the outer hill-ops without a "[detached]" line.

`_over` waits for the strip to put its pane in place, so the program
starts at the size it keeps, and runs it through the relay (relay.py), so
that it follows the mouse; once the program has exited, it keeps its exit
status on its pane and tells the strip, which puts the side panes back and
closes the pane.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .relay import run as relay

OVER_WAIT = 10.0
"""Seconds `_over` waits, once its program has exited, for the strip to
close its pane."""
PLACED_WAIT = 3.0
"""Seconds `_over` waits for its pane to be put in place, at most."""


def exit_status(returncode: int) -> int:
    """A process's exit status as a shell gives it: 128 + n for signal n."""
    return 128 - returncode if returncode < 0 else returncode


def call(argv: list[str]) -> int:
    """Run `argv` in this pane and return its exit status. Ctrl-C and Ctrl-\\
    are for it: a handler, unlike SIG_IGN, isn't passed on to it."""
    for signum in (signal.SIGINT, signal.SIGQUIT):
        signal.signal(signum, lambda *_: None)
    try:
        return exit_status(subprocess.call(argv))
    except OSError as e:
        print(f"hill-ops: {argv[0]}: {e.strerror}", file=sys.stderr)
        return 127


def over_channel(pane: str) -> str:
    """The tmux wait-for channel on which `_over` says its program has exited."""
    return f"hill-over-{pane.lstrip('%')}"


def placed_channel(pane: str) -> str:
    """The tmux wait-for channel on which the strip says `_over`'s pane is in
    place, beside the app."""
    return f"hill-placed-{pane.lstrip('%')}"


def placed(pane: str) -> None:
    """Wait for the strip to put `pane` in place, and for its terminal to
    have the size tmux gives it there, which comes a moment later: an
    editor sizes its own splits as it starts, so it starts at the size it
    keeps. At most PLACED_WAIT seconds."""
    try:
        subprocess.run(["tmux", "wait-for", placed_channel(pane)], capture_output=True, timeout=PLACED_WAIT)
    except subprocess.TimeoutExpired:
        return
    size = subprocess.run(["tmux", "display", "-p", "-t", pane, "#{pane_width} #{pane_height}"], capture_output=True, text=True)
    want = tuple(int(n) for n in size.stdout.split() if n.isdigit())
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        try:
            if tuple(os.get_terminal_size(sys.stdout.fileno())) == want:
                return
        except OSError:
            return
        time.sleep(0.01)


def side() -> int:
    """`hill _side`: run the program in HILL_ARGV beside the app, through
    the relay: its exit status."""
    argv = json.loads(os.environ["HILL_ARGV"])
    return exit_status(relay(argv)) if os.isatty(0) else call(argv)


def over() -> int:
    """`hill-ops _over`: once its pane is in place, run the program in
    HILL_ARGV over the side panes, through the relay; once it has exited,
    keep its status on the pane (@hill-status), tell the strip, and wait
    for the strip to close the pane."""
    pane = os.environ.get("TMUX_PANE", "")
    placed(pane)
    argv = json.loads(os.environ["HILL_ARGV"])
    status = exit_status(relay(argv)) if os.isatty(0) else call(argv)
    subprocess.run(["tmux", "set", "-p", "-t", pane, "@hill-status", str(status)], capture_output=True)
    subprocess.run(["tmux", "wait-for", "-S", over_channel(pane)], capture_output=True)
    time.sleep(OVER_WAIT)  # the strip closes the pane before this ends
    return status


def main() -> int:
    status = call(json.loads(os.environ["HILL_ARGV"]))
    pane = os.environ.get("TMUX_PANE", "")
    text = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", pane], capture_output=True, text=True).stdout
    Path(os.environ["HILL_RUN"], "exit.json").write_text(json.dumps({"status": status, "text": text}))
    subprocess.run(["tmux", "detach-client", "-s", "hill", "-E", "true"], capture_output=True)
    return status
