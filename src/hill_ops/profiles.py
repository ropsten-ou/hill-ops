"""App profiles: the settings an app offers, described in TOML, as
settings-panel groups.

A profile names its app and lists its groups; each group names the file its
settings live in (JSON or TOML, with ${VAR:-default} in the path) and its
settings, each with a fixed list of choices. When an app says hello, hill-ops
links its profile into its own profiles folder, so the app's settings stay
reachable while it isn't running.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from settings_panel import Group, JsonStore, OverlayStore, Setting, StoreError, TomlStore

from .instances import INSTANCE
from .paths import config_home

THEMES = "textual-themes"
"""`choices` meaning Textual's themes, which only a running Textual app knows."""


class ProfileError(Exception):
    """A profile that can't be read."""


@dataclass
class Profile:
    app: str
    path: Path
    groups: list[Group]
    theme: tuple[Group, Setting] | None = None
    """The app's theme: its first setting whose choices are Textual's themes."""
    work: Path | None = None
    """The app's work folder, where Claude in the strip can file work items."""


def profiles_dir() -> Path:
    return config_home() / "profiles"


def expand(text: str) -> str:
    """A path with its variables filled in: ${NAME}, and ${NAME:-default},
    which takes the default (itself expanded) when NAME is unset or empty.
    A leading ~ is the home folder."""
    out, i = [], 0
    while i < len(text):
        if not text.startswith("${", i):
            out.append(text[i])
            i += 1
            continue
        depth, j = 1, i + 2
        while j < len(text) and depth:
            if text.startswith("${", j):
                depth, j = depth + 1, j + 2
                continue
            if text[j] == "}":
                depth -= 1
            j += 1
        if depth:
            raise ProfileError(f"no closing }} in {text!r}")
        name, has_default, default = text[i + 2 : j - 1].partition(":-")
        value = os.environ.get(name, "")
        out.append(expand(default) if has_default and not value else value)
        i = j
    return os.path.expanduser("".join(out))


def load(path: Path | str, themes: Callable[[], list[str]] | None = None) -> Profile:
    """Read a profile. `themes` gives the choices for settings whose choices
    are "textual-themes"."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{path.name} isn't valid TOML ({e})") from e
    try:
        app = data["app"]
        groups = [_group(g, themes, app) for g in data.get("groups", [])]
        theme = next((
            (group, setting) for group, g in zip(groups, data.get("groups", []))
            for setting, s in zip(group.settings, g.get("settings", [])) if s["choices"] == THEMES
        ), None)
        # A relative work folder is the profile's own: next to the file the link points to.
        work = path.resolve().parent / expand(data["work"]) if isinstance(data.get("work"), str) else None
        return Profile(app, path, groups, theme, work)
    except KeyError as e:
        raise ProfileError(f"{path.name}: missing {e.args[0]!r}") from e
    except (TypeError, ValueError) as e:
        raise ProfileError(f"{path.name}: {e}") from e


def _group(data: dict, themes: Callable[[], list[str]] | None, app: str) -> Group:
    file = Path(expand(data["file"]))
    store: JsonStore | TomlStore | OverlayStore
    if data.get("format", "toml" if file.suffix == ".toml" else "json") == "toml":
        store = TomlStore(file)
    else:
        store = JsonStore(file, keep_defaults=bool(data.get("keep_defaults", False)))
    settings = [_setting(s, themes) for s in data.get("settings", [])]
    # Inside hill-ops, the settings marked `instance` are each instance's own.
    own = {s.key: s.default for s, d in zip(settings, data.get("settings", [])) if d.get("instance") is True}
    if own and (instance := os.environ.get(INSTANCE)):
        store = OverlayStore(store, Path(instance) / f"{app}.json", own)
    return Group(data["id"], data["name"], data.get("where", str(file)), store, settings)


def _setting(data: dict, themes: Callable[[], list[str]] | None) -> Setting:
    choices = data["choices"]
    if choices == THEMES:
        default = data["default"]
        choices = themes or (lambda: [default])
    elif not isinstance(choices, list) or not choices:
        raise ValueError(f"{data['label']!r} needs a list of choices")
    names = {value: name for value, name in data.get("names", [])}
    return Setting(tuple(data["key"]), data["label"], data.get("help", ""), choices, data["default"], names)


def register(app: str, path: Path | str) -> Path:
    """Link an app's profile into hill-ops's profiles folder, replacing an older
    link, so its settings stay reachable while it isn't running."""
    if not app or "/" in app or app.startswith("."):
        raise ProfileError(f"{app!r} isn't a usable app name")
    link = profiles_dir() / f"{app}.toml"
    link.parent.mkdir(parents=True, exist_ok=True)
    target = Path(path).resolve()
    if link.is_symlink() and link.readlink() == target:
        return link
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    return link


def app_theme(app: str) -> str | None:
    """The theme an app has chosen, from its linked profile's theme setting,
    or None if it has none or its profile can't be read."""
    try:
        profile = load(profiles_dir() / f"{app}.toml")
    except (ProfileError, OSError):
        return None
    if profile.theme is None:
        return None
    group, setting = profile.theme
    try:
        return group.value(setting)
    except StoreError:
        return None


def known(themes: Callable[[], list[str]] | None = None) -> tuple[list[Profile], list[str]]:
    """Every linked profile that can be read, and a note for each that can't
    (its app was moved or removed, or the file is broken)."""
    profiles, notes = [], []
    for link in sorted(profiles_dir().glob("*.toml")):
        try:
            profiles.append(load(link, themes))
        except FileNotFoundError:
            notes.append(f"{link.stem}'s profile is gone ({link.resolve()})")
        except (ProfileError, OSError) as e:
            notes.append(str(e))
    return profiles, notes
