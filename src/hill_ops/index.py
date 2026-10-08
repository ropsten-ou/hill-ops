"""What the panel's line searches: every setting of every known app and of
hill-ops, and every command, key and help section of the apps on the channel,
or, for an app that isn't running, as it last said hello.

Each app's hello is kept in state_home()/apps, so its commands, keys and
help can be found while it isn't running. Typing words on the line lists
what holds them all, the app on the strip first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from settings_panel import Group, StoreError

from .paths import state_home

SHOWN = 30
"""At most this many matches show."""


@dataclass
class AppDocs:
    """What an app said in hello that tells what it does: its strip's
    keys, its commands, its help and its key tree. A running app's Peer
    serves too."""

    app: str
    keys: list[list] = field(default_factory=list)
    commands: list[list] = field(default_factory=list)
    help: list[list] = field(default_factory=list)
    tree: list[list] = field(default_factory=list)


@dataclass(frozen=True)
class Found:
    """Something the line can find. `kind` is "setting", "command", "key"
    or "help" (a section of the help); `name` what it's called, `what` what
    it does, `where` the group or the help section it's in. `target` says
    what taking it does: a setting's (group id, key), a command's name, a
    hint's action, or the help section to open."""

    kind: str
    app: str
    name: str
    what: str
    where: str
    target: Any
    words: str = ""


def apps_dir() -> Path:
    return state_home() / "apps"


def remember(docs: AppDocs) -> None:
    """Keep what an app said in hello, for when it isn't running."""
    if not docs.app or "/" in docs.app or docs.app.startswith("."):
        return
    try:
        apps_dir().mkdir(parents=True, exist_ok=True)
        data = {"app": docs.app, "keys": docs.keys, "commands": docs.commands, "help": docs.help, "tree": docs.tree}
        (apps_dir() / f"{docs.app}.json").write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass


def remembered() -> list[AppDocs]:
    """What each app said in hello when it last ran, skipping what can't be read."""
    found = []
    for path in sorted(apps_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text())
            found.append(AppDocs(str(data["app"]), _lists(data.get("keys")), _lists(data.get("commands")), _lists(data.get("help")),
                                 _lists(data.get("tree"))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def _lists(value: Any) -> list[list]:
    return [v for v in value if isinstance(v, list)] if isinstance(value, list) else []


def build(groups: list[tuple[str, Group]], apps: list[AppDocs], strip_help: list[tuple[str, str]]) -> list[Found]:
    """Everything to search: `groups` as (app, group), the apps' keys,
    commands and help, and the strip's own keys, which are hill-ops's."""
    found = []
    for app, group in groups:
        for setting in group.settings:
            try:
                now = f" Now {setting.show(group.value(setting))}."
            except (StoreError, OSError):
                now = ""
            choices = ", ".join(setting.show(o) for o in setting.options())
            found.append(_found(
                "setting", app, setting.label, f"{setting.help}{now}".strip(), group.name, (group.id, setting.key), choices,
            ))
    for docs in apps:
        for key in docs.keys:
            if len(key) > 2 and key[2]:
                found.append(_found("key", docs.app, str(key[0]), str(key[1]), "strip", str(key[2])))
        for section in docs.help:
            if not (len(section) > 1 and isinstance(section[0], str) and isinstance(section[1], list)):
                continue
            found.append(_found("help", docs.app, section[0], "", "help", section[0]))
            for entry in section[1]:
                if isinstance(entry, list) and len(entry) >= 2:
                    found.append(_found("key", docs.app, str(entry[0]), str(entry[1]), section[0], section[0]))
        for command in docs.commands:
            if command and isinstance(command[0], str):
                args, what = (str(c or "") for c in (list(command) + ["", ""])[1:3])
                choices = " ".join(map(str, command[3])) if len(command) > 3 and isinstance(command[3], list) else ""
                found.append(_found("command", docs.app, f":{command[0]} {args}".strip(), what, "commands", command[0], choices))
    for key, what in strip_help:
        found.append(_found("key", "hill", key, what, "Strip", "Strip"))
    return found


def _found(kind: str, app: str, name: str, what: str, where: str, target: Any, more: str = "") -> Found:
    words = " ".join((kind, app, name, what, where, more)).casefold()
    return Found(kind, app, name, what, where, target, words)


def search(index: list[Found], text: str, first: str | None = None) -> list[Found]:
    """What holds every word of `text`, in any order: the app `first`'s
    first, then what's named so before what only says so."""
    words = text.casefold().split()
    if not words:
        return []
    whole = " ".join(words)

    def rank(found: Found) -> tuple:
        name = found.name.casefold().lstrip(":")
        named = 0 if name.startswith(whole) else 1 if all(w in name for w in words) else 2
        return (found.app != first, named, found.kind != "setting", name)

    return sorted((f for f in index if all(w in f.words for w in words)), key=rank)[:SHOWN]
