"""hill-ops's design docs are its READMEs: README.md says what hill-ops does, and each
package's README what it offers. These tests fail when something new isn't in
them yet. They only check names, so a change to what something does still
needs its README changed by hand; see CLAUDE.md."""

import inspect
import re
from dataclasses import is_dataclass
from pathlib import Path

import pytest
from textual.widget import Widget

import acp_client
import keyline
import settings_panel
import hill_client


def api(package) -> list[str]:
    """What a package offers: the names it exports (functions as `name(`)
    and, for its classes, their options (parameters with a default, as
    `name=`; a dataclass's fields are data, not options) and methods (as
    `name(`; a widget's are mostly Textual's own hooks, so not those)."""
    names = [f"{name}(" if inspect.isfunction(getattr(package, name)) else name for name in package.__all__]
    for cls in (getattr(package, name) for name in package.__all__):
        if not inspect.isclass(cls) or issubclass(cls, Exception):
            continue
        if not is_dataclass(cls):
            options = inspect.signature(cls).parameters.values()
            names += [f"{p.name}=" for p in options if p.default is not p.empty]
        if not issubclass(cls, Widget):
            names += [f"{name}(" for name, value in vars(cls).items() if callable(value) and not name.startswith("_")]
    return list(dict.fromkeys(names))


def mentions(text: str, name: str) -> bool:
    """Whether `text` has `name` as a word of its own: `get(`, not `widget(`."""
    end = "" if name.endswith(("(", "=")) else r"\b"
    return re.search(rf"\b{re.escape(name)}{end}", text) is not None


@pytest.mark.parametrize("package", [acp_client, keyline, settings_panel, hill_client], ids=lambda p: p.__name__)
def test_every_part_of_a_package_is_in_its_readme(package):
    readme = (Path(package.__file__).parents[2] / "README.md").read_text()
    assert [name for name in api(package) if not mentions(readme, name)] == []


def test_every_command_is_in_the_readme():
    from hill_ops.cli import USAGE

    readme = (Path(__file__).parents[1] / "README.md").read_text()
    commands = re.findall(r"^\s*(?:usage: )?(hill-ops \w+)", USAGE, re.M)
    assert commands and [c for c in commands if c not in readme] == []


def test_every_message_is_in_the_readme():
    from hill_ops.hub import REPLIES, REPORTS, REQUESTS

    channel = (Path(__file__).parents[1] / "README.md").read_text().split("\n## The channel\n")[1].split("\n## ")[0]
    assert [m for m in ("hello", *REQUESTS, *REPORTS, *REPLIES) if f"`{m} {{" not in channel] == []


def test_every_event_is_in_the_readme():
    from hill_ops.events import EVENTS

    log = (Path(__file__).parents[1] / "README.md").read_text().split("\n## The event log\n")[1].split("\n## ")[0]
    assert [e for e in EVENTS if f"`{e}`" not in log] == []
