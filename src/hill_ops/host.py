"""Run an app above hill-ops's strip, in hill-ops's own tmux.

hill-ops starts a private tmux server with its own configuration, so your tmux
and ~/.tmux.conf stay out of it. The app runs in the top pane under a small
runner (see runner.py), the strip in a one-row pane below, and hill-ops attaches.
When the app exits, the runner saves what it left on screen and its exit
status, and detaches the client; hill-ops then reprints that text, stops the
server and exits with the app's status.

Before it starts, hill-ops picks the instance the session runs in (see
instances.py): the one it's asked for, else the most recently used one of
the same program that isn't running, else a new one.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from hill_client import SOCKET

from . import instances
from .events import EventLog, prune
from .instances import INSTANCE, Instance
from .paths import runtime_dir

CONFIG = Path(__file__).with_name("tmux.conf")
SESSION = "hill"


def run(argv: list[str], choice: str | None = None, name: str | None = None) -> int:
    """Run `argv` above the strip and return its exit status. Inside hill-ops
    already, without a terminal, or without tmux, it just runs `argv`.
    `choice` says which instance: "new", "resume" (a list to pick from), or
    None for the last one used; or `name` names it."""
    if SOCKET in os.environ or not (sys.stdin.isatty() and sys.stdout.isatty()) or not shutil.which("tmux"):
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            print(f"hill-ops: {argv[0]}: {e.strerror}", file=sys.stderr)
            return 127
    try:
        instance = choose(_program(argv), choice, name)
    except (ValueError, OSError) as e:
        print(f"hill-ops: {e}", file=sys.stderr)
        return 2
    if instance is None:
        return 1
    session = Session(argv, instance)
    cols, rows = shutil.get_terminal_size()
    session.start(cols, rows)
    return session.attach()


def choose(program: str, choice: str | None = None, name: str | None = None) -> Instance | None:
    """The instance to run `program` in, as `choice` or `name` say (see
    run()); None if you picked none from the list."""
    if choice == "new":
        return instances.new()
    if choice == "resume":
        return pick(program, instances.resumable(program))
    if name is not None:
        found = instances.named(name)
        if found.running():
            raise ValueError(f"instance {name} is running")
        return found
    ended = instances.resumable(program)
    return ended[0] if ended else instances.new()


def pick(program: str, ended: list[Instance]) -> Instance | None:
    """Ask which of the `ended` instances to resume, in the terminal."""
    if not ended:
        print(f"hill-ops: no instance of {program} to resume; starting a new one", file=sys.stderr)
        return instances.new()
    now = time.time()
    shown = ended[:instances.KEEP]
    width = max(len(i.describe()) for i in shown)
    print(f"Resume {program}:")
    for n, instance in enumerate(shown, 1):
        print(f"  {n}  {instance.describe():<{width}}  {instances.ago(now - instance.used())}")
    print("  n  a new one")
    while True:
        try:
            answer = input(f"Which (1-{len(shown)}, n; Enter for 1)? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer in ("", "1"):
            return shown[0]
        if answer == "n":
            return instances.new()
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1]


class Session:
    """One app in hill-ops's tmux, and its strip, in an instance."""

    def __init__(self, argv: list[str], instance: Instance | None = None) -> None:
        self.argv = argv
        self.instance = instance or instances.new()
        self.name = os.environ.get("HILL_TMUX_NAME") or f"hill-{os.getpid()}"
        self.run_dir = runtime_dir() / str(os.getpid())
        self.socket = self.run_dir / "channel.sock"
        self.log = EventLog.new()
        """The session's event log: the host writes its start and end, the
        strip the rest."""

    def tmux(self, *args: str) -> subprocess.CompletedProcess:
        """A tmux command on this session's server."""
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}  # works inside your own tmux too
        # tmux gives a new pane the PATH of the client that made it, over -e.
        env["PATH"] = _path_with_hill_ops()
        return subprocess.run(["tmux", "-L", self.name, *args], capture_output=True, text=True, env=env)

    def start(self, cols: int, rows: int) -> None:
        """Start the server, with the app on top and the strip below."""
        prune()
        program = _program(self.argv)
        self.instance.claim(program, tmux_socket(self.name))
        self.log.record("start", command=program, cols=cols, rows=rows, instance=self.instance.id)
        runtime_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = {SOCKET: str(self.socket), "HILL_RUN": str(self.run_dir), INSTANCE: str(self.instance.path)}
        # The command goes in the environment: tmux takes an argument ending
        # in ";" for a command separator.
        app = self.tmux(
            "-f", str(CONFIG), "new-session", "-d", "-s", SESSION, "-x", str(cols), "-y", str(rows),
            "-c", os.getcwd(), *_env_args(env | {"HILL_ARGV": json.dumps(self.argv)}),
            "-P", "-F", "#{pane_id}", "--", sys.executable, "-m", "hill_ops", "_run",
        ).stdout.strip()
        strip = self.tmux(
            "split-window", "-v", "-d", "-l", "1", "-t", app,
            *_env_args(env | {"HILL_APP_PANE": app, "HILL_EVENTS": str(self.log.path)}),
            "-P", "-F", "#{pane_id}", "--", sys.executable, "-m", "hill_ops", "_strip",
        ).stdout.strip()
        self.tmux("set", "-g", "@hill-strip", strip)
        # Resizing the window would share the new rows and columns out
        # between the panes as tmux likes; this sizes them as hill-ops asks
        # (@hill-relayout, which the strip keeps up to date as the panes
        # change), the strip at the height it asked for.
        self.tmux("set", "-g", "@hill-relayout", f"resize-pane -t {strip} -y #{{@hill-height}}")
        self.tmux("set-hook", "-g", "window-resized", "run-shell -C '#{E:@hill-relayout}'")
        if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
            self.tmux("set", "-as", "terminal-features", ",*:RGB")
        if shutil.which("pbcopy"):
            self.tmux("set", "-g", "copy-command", "pbcopy")
        instances.prune(program)  # now that the server is up, and this instance counts as running

    def attach(self) -> int:
        """Show the session until the app exits, then reprint what it left on
        screen and return its exit status."""

        def hang_up(signum: int, _frame: object) -> None:
            self.log.record("end", status=128 + signum)
            self.stop()
            os._exit(128 + signum)

        for signum in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(signum, hang_up)
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        subprocess.run(["tmux", "-L", self.name, "attach", "-t", SESSION], env=env)
        try:
            record = json.loads((self.run_dir / "exit.json").read_text())
        except (OSError, ValueError):
            record = {"status": 1, "text": ""}
        self.log.record("end", status=record["status"])
        self.stop()
        if text := record["text"].rstrip():
            print(text)
        return record["status"]

    def stop(self) -> None:
        self.instance.end()
        self.tmux("kill-server")
        # tmux leaves its socket file behind; one per session would pile up.
        tmux_socket(self.name).unlink(missing_ok=True)
        shutil.rmtree(self.run_dir, ignore_errors=True)


def tmux_socket(name: str) -> Path:
    """Where tmux keeps the socket of the server `tmux -L name` runs."""
    return Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}" / name


def _program(argv: list[str]) -> str:
    """The program's name, for the log, without its arguments, since file
    names are yours; `python -m hill` is hill."""
    name = Path(argv[0]).name
    if name.startswith("python") and len(argv) > 2 and argv[1] == "-m":
        return argv[2]
    return name


def _path_with_hill_ops() -> str:
    """PATH with the folder of hill-ops's own commands at its end, so an app in
    hill-ops finds `hill-ops` (micro runs `hill-ops connect`) when an install
    such as `uv tool install hill-ops` didn't put it on PATH."""
    path = os.environ.get("PATH", "")
    own = str(Path(sys.executable).parent)
    return path if own in path.split(os.pathsep) else os.pathsep.join(p for p in (path, own) if p)


def _env_args(env: dict[str, str]) -> list[str]:
    return [arg for name, value in env.items() for arg in ("-e", f"{name}={value}")]
