"""Settings, grouped, and the files they live in.

Every setting has a fixed list of values to step through, so changing one
never needs typing. Values are written straight into each program's own
config file, so the program stays the owner of its settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import tomlkit
from tomlkit.exceptions import TOMLKitError

OFF_ON = [False, True]


class StoreError(Exception):
    """A settings file can't be read, so it won't be written either."""


def _write_atomically(path: Path, text: str) -> None:
    # Write through symlinks (e.g. from a dotfiles manager) rather than
    # replacing them.
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.settings-tmp")
    tmp.write_text(text)
    os.replace(tmp, target)


class JsonStore:
    """A JSON settings file, such as micro's settings.json.

    Keys are tuples naming the path to a value, because keys can contain
    dots ("claude.autosave") and settings can be nested ("ft:markdown" ->
    "softwrap"). By default the file only keeps values that differ from the
    default, as micro does. With `keep_defaults`, a value set back to its
    default is written out too, so a program that re-reads the file while
    running (micro, through a plugin) sees the change.
    """

    def __init__(self, path: Path, keep_defaults: bool = False) -> None:
        self.path = path
        self.keep_defaults = keep_defaults

    def _load(self) -> dict:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as e:
            raise StoreError(f"can't read {self.path}: {e.strerror}") from e
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise StoreError(f"{self.path} isn't valid JSON (line {e.lineno})") from e
        if not isinstance(data, dict):
            raise StoreError(f"{self.path} doesn't hold a JSON object")
        return data

    def get(self, key: tuple[str, ...]) -> Any:
        node: Any = self._load()
        for part in key:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def set(self, key: tuple[str, ...], value: Any, default: Any) -> None:
        data = self._load()
        *parents, last = key
        node = data
        chain = []
        for part in parents:
            child = node.get(part)
            if not isinstance(child, dict):
                child = node[part] = {}
            chain.append((node, part))
            node = child
        if value is None or (value == default and not self.keep_defaults):
            node.pop(last, None)
            for parent, part in reversed(chain):
                if parent[part] == {}:
                    del parent[part]
        else:
            node[last] = value
        _write_atomically(self.path, json.dumps(data, indent=4, sort_keys=True) + "\n")


class TomlStore:
    """A TOML settings file.

    Edits keep the file's comments and layout. Values are set even when they
    equal the default, since many programs write out every value; None
    removes the key.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> tomlkit.TOMLDocument:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return tomlkit.document()
        except OSError as e:
            raise StoreError(f"can't read {self.path}: {e.strerror}") from e
        try:
            return tomlkit.parse(text)
        except TOMLKitError as e:
            raise StoreError(f"{self.path} isn't valid TOML ({e})") from e

    def get(self, key: tuple[str, ...]) -> Any:
        node: Any = self._load()
        for part in key:
            if not hasattr(node, "keys") or part not in node:
                return None
            node = node[part]
        return node.unwrap() if hasattr(node, "unwrap") else node

    def set(self, key: tuple[str, ...], value: Any, default: Any) -> None:
        doc = self._load()
        *parents, last = key
        node: Any = doc
        for part in parents:
            if part not in node:
                node[part] = tomlkit.table()
            node = node[part]
        if value is None:
            if last in node:
                del node[last]
        else:
            node[last] = value
        _write_atomically(self.path, tomlkit.dumps(doc))


class OverlayStore:
    """A store with some of its keys kept in a JSON file of their own, such
    as what one instance of a program keeps apart from the others.

    `defaults` maps each of those keys to its default. They're read from
    `path`, and a change to one is written there and to `base` too, for a
    new file to start from: a key `path` doesn't have yet takes `base`'s
    value, else its default, once, so that later changes to `base` don't
    reach it. The other keys are `base`'s.
    """

    def __init__(self, base: JsonStore | TomlStore, path: Path, defaults: dict[tuple[str, ...], Any]) -> None:
        self.base = base
        self.path = path
        self.own = JsonStore(path, keep_defaults=True)
        self.defaults = dict(defaults)
        try:
            for key, default in self.defaults.items():
                if self.own.get(key) is None:
                    value = base.get(key)
                    self.own.set(key, default if value is None else value, default)
        except (StoreError, OSError):
            pass  # read from base, then, until a change writes path

    def get(self, key: tuple[str, ...]) -> Any:
        if key in self.defaults:
            value = self.own.get(key)
            return self.base.get(key) if value is None else value
        return self.base.get(key)

    def set(self, key: tuple[str, ...], value: Any, default: Any) -> None:
        if key in self.defaults:
            self.own.set(key, value, default)
        self.base.set(key, value, default)


Store = JsonStore | TomlStore | OverlayStore


@dataclass
class Setting:
    key: tuple[str, ...]
    label: str
    help: str
    choices: list[Any] | Callable[[], list[Any]]
    default: Any
    names: dict[Any, str] = field(default_factory=dict)
    """Display names for values that aren't clear on their own."""

    def options(self) -> list[Any]:
        return self.choices() if callable(self.choices) else self.choices

    def show(self, value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        if value in self.names:
            return self.names[value]
        return "off" if value is None else str(value)

    def step(self, current: Any, delta: int) -> Any:
        """The value `delta` places from `current`, wrapping around."""
        options = self.options()
        if current in options:
            return options[(options.index(current) + delta) % len(options)]
        # A value set by hand that isn't offered: start from an end.
        return options[0] if delta > 0 else options[-1]


@dataclass
class Group:
    id: str
    name: str
    where: str
    """Which file the group's settings live in, and when they apply."""
    store: Store
    settings: list[Setting]
    on_change: Callable[[Setting, Any], None] | None = None
    """Called after a setting in the group changes, to apply it right away."""

    def value(self, setting: Setting) -> Any:
        value = self.store.get(setting.key)
        return setting.default if value is None else value

    def change(self, setting: Setting, delta: int) -> Any:
        value = setting.step(self.value(setting), delta)
        self.store.set(setting.key, value, setting.default)
        if self.on_change is not None:
            self.on_change(setting, value)
        return value


@dataclass
class Entry:
    """A line of a `Listing`: what it says, a string or Rich Text; what the
    description under the list says of it; and an `id` for whoever picks it.
    A `separator` is a rule across the list instead, never selected."""

    text: Any
    help: str = ""
    id: str = ""
    separator: bool = False


@dataclass
class Listing:
    """A group of lines to pick from rather than settings, such as what's
    due across projects: a tab of its own, always a list. Ret, or a click
    on the selected line, picks it."""

    id: str
    name: str
    where: str
    """What the lines are, shown under one that has no help of its own."""
    entries: list[Entry]
    on_pick: Callable[[Entry], None] | None = None
    """Called with the line picked."""
    verb: str = "open"
    """What picking a line does, for its key."""
    empty: str = "Nothing here."
    """What the list says while it has no lines."""


def panel_settings() -> list[Setting]:
    """Settings for the panel itself. Put them in a group whose store is the
    panel's `prefs` store, and they apply as soon as they change."""
    return [
        Setting(("panel_height",), "Panel height", "How much of the window this panel covers.", ["1/4", "1/3", "1/2"], "1/3"),
        Setting(("tabs",), "Group tabs", "Show the group tabs above the settings, or below them.", ["top", "bottom"], "top"),
    ]
