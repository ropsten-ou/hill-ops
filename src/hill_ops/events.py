"""The event log: what happens in a session, kept for Claude, which reads it
in the strip to help with settings, keys and commands.

It holds what apps report over the channel and what the strip does for
them, one JSON object per line, and never the text on screen or what you
type: a command keeps its name, and its argument only when that's one of the
choices the command lists; a question from the app keeps neither its
wording, which can hold a note's title, nor its answer; a question you ask
Claude keeps neither, nor Claude's answer.

Each session writes a file of its own in state_home()/events, named for when
it started; a session removes the files older than KEEP_DAYS when it starts.
Hill → Event log turns it off.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import config_home, state_home

EVENTS = (
    "start", "hello", "bye", "run", "settings.open", "settings.changed", "help.open", "command.open",
    "command", "ask", "answer", "claude.open", "claude.ask", "claude.take", "tip", "close", "problem",
    "error", "panes", "panes.end", "over", "over.done", "overview.pick", "search", "work.filed", "resize", "end",
)
"""What the log records."""
KEEP_DAYS = 30
SETTING = "events"
"""The key of Hill → Event log in hill-ops's settings.json."""


def log_dir() -> Path:
    return state_home() / "events"


def is_on() -> bool:
    """Hill → Event log: on unless it's been turned off. Read from the file,
    since the host doesn't load the settings panel."""
    try:
        return json.loads((config_home() / "settings.json").read_text()).get(SETTING) is not False
    except (OSError, ValueError, AttributeError):
        return True


def prune(days: int = KEEP_DAYS) -> None:
    """Remove the logs of sessions that ended more than `days` ago."""
    cutoff = time.time() - days * 86400
    for path in log_dir().glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def command_words(line: str, commands: list[list]) -> dict[str, Any]:
    """What the log keeps of a command line: the command's name, or None if
    the app has no such command, and its argument as `choice` when it's one
    of the command's choices. The rest, such as a title or a path, is yours."""
    name, _, rest = line.strip().partition(" ")
    rest = rest.strip()
    for command in commands:
        if command[0] == name:
            choices = command[3] if len(command) > 3 and isinstance(command[3], list) else []
            return {"name": name, "choice": rest} if rest and rest in map(str, choices) else {"name": name}
    return {"name": None}


class EventLog:
    """A session's log, at `path` (None records nothing). Each event goes on
    a line of its own as it happens, while the log is on; a log that can't
    be written is skipped, so it never gets in the way."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    @classmethod
    def new(cls) -> EventLog:
        """A log for a session starting now."""
        return cls(log_dir() / f"{datetime.now().astimezone():%Y-%m-%dT%H-%M-%S}-{os.getpid()}.jsonl")

    def record(self, event: str, app: str | None = None, **details: Any) -> None:
        """Add an event, with the app it concerns and its own fields."""
        if self.path is None or not is_on():
            return
        entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, "app": app}
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry | details, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
