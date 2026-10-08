"""Instances: what a hill-ops keeps of its own from one run to the next.

Each hill-ops runs in an instance, a folder in state_home()/instances that it
gives its apps as $HILL_INSTANCE. hill keeps its Layout group there, and an
app what it likes, such as where it was. The folder outlasts hill-ops, so a hill-ops
started again can take it up where it was left, as Claude resumes a session:
by default the most recently used instance of the same program that isn't
running, or one picked from a list (`hill-ops --resume`). An app can label its
instance (label.json, a list of a few words, such as its last projects), and
the list shows that.

What's in the folder is written as it changes, so a crash or a reboot keeps
it. hill-ops writes instance.json: the program, its own process, its tmux
server, and when it ended. An instance is running while that process runs
and its tmux server's socket is there; a reboot removes the socket.

It imports nothing heavy, so the host can use it.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import state_home

INSTANCE = "HILL_INSTANCE"
"""The environment variable that gives hill-ops's apps their instance's folder."""
RECORD = "instance.json"
LABEL = "label.json"
KEEP = 5
"""How many ended instances of each program are kept."""


def instances_dir() -> Path:
    return state_home() / "instances"


@dataclass
class Instance:
    """One instance's folder."""

    path: Path

    @property
    def id(self) -> str:
        return self.path.name

    def record(self) -> dict:
        """What hill-ops wrote of it in instance.json, or nothing."""
        return _read(self.path / RECORD, dict) or {}

    def label(self) -> list[str]:
        """What its app said of it, in label.json: a few words."""
        return [str(word) for word in _read(self.path / LABEL, list) or []]

    def used(self) -> float:
        """When it last changed: its newest file's time."""
        times = [self.path.stat().st_mtime]
        for child in self.path.iterdir():
            try:
                times.append(child.stat().st_mtime)
            except OSError:
                pass
        return max(times)

    def running(self) -> bool:
        record = self.record()
        if record.get("ended") is not None or not isinstance(record.get("pid"), int):
            return False
        try:
            os.kill(record["pid"], 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        tmux = record.get("socket")
        return not tmux or Path(tmux).exists()

    def claim(self, program: str, socket: Path) -> None:
        """Mark it as this process's, running `program` on the tmux server
        whose socket is `socket`."""
        record = self.record()
        record.update(program=program, pid=os.getpid(), socket=str(socket), started=_now(), ended=None, cwd=os.getcwd())
        _write(self.path / RECORD, record)

    def end(self) -> None:
        """Mark it as ended, now."""
        record = self.record()
        record["ended"] = _now()
        _write(self.path / RECORD, record)

    def describe(self) -> str:
        """Its label, else the folder it ran in, for the list to pick from."""
        if words := self.label():
            return " · ".join(words)
        cwd = self.record().get("cwd") or "?"
        home = str(Path.home())
        return "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + "/") else cwd


def of(program: str) -> list[Instance]:
    """The instances of `program`, most recently used first."""
    try:
        folders = [p for p in instances_dir().iterdir() if p.is_dir()]
    except OSError:
        return []
    found = [i for i in map(Instance, folders) if i.record().get("program") == program]
    return sorted(found, key=Instance.used, reverse=True)


def resumable(program: str) -> list[Instance]:
    """The instances of `program` that aren't running, most recently used
    first."""
    return [i for i in of(program) if not i.running()]


def new() -> Instance:
    instance = Instance(instances_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}")
    instance.path.mkdir(mode=0o700, parents=True)
    return instance


def named(id: str) -> Instance:
    """The instance called `id`, made if there's none."""
    if not id or "/" in id or id.startswith("."):
        raise ValueError(f"{id!r} can't name an instance")
    instance = Instance(instances_dir() / id)
    instance.path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return instance


def prune(program: str, keep: int = KEEP) -> None:
    """Remove the ended instances of `program` beyond the `keep` most
    recently used."""
    import shutil

    for instance in resumable(program)[keep:]:
        shutil.rmtree(instance.path, ignore_errors=True)


def ago(seconds: float) -> str:
    """How long ago, in a word or two."""
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())


def _read(path: Path, kind: type) -> object:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, kind) else None


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=4, sort_keys=True) + "\n")
    os.replace(tmp, path)
