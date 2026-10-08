from pathlib import Path

import pytest
from settings_panel import Group, JsonStore, Setting

from hill_ops.index import AppDocs, remember
from hill_ops.profiles import register
from hill_ops.reference import Unknown, document, reference

DEMO = AppDocs(
    "demo", keys=[[",", "settings", "app.settings"], ["x", "", "app.x"]],
    commands=[["new", "TITLE", "write a new note", ["a", "b"]], ["mute"]],
    help=[["List", [["n", "new note"]]]],
)


def test_hills_reference_is_current_and_all_documented():
    text, gaps = reference()
    assert gaps == []
    assert (Path(__file__).parents[1] / "docs" / "reference.md").read_text() == text, "run: hill-ops docs > docs/reference.md"


def test_a_reference_says_everything_declared_and_what_isnt_documented(tmp_path):
    group = Group("list", "List", "demo · applies right away", JsonStore(tmp_path / "s.json"), [
        Setting(("notes", "sort"), "Sort by", "Order of the notes list", ["title", "modified"], "title", {"modified": "newest first"}),
        Setting(("notes", "calendar"), "Calendar", "", [False, True], True),
    ])
    text, gaps = document("demo", [group], DEMO)
    assert "- **Sort by** (`list.notes.sort`): Order of the notes list. Choices: `title`, `modified` (newest first). Default: `title`." in text
    assert "Choices: `false` (off), `true` (on). Default: `true`." in text
    assert "- `:new TITLE`: write a new note. Choices: `a`, `b`." in text
    assert "### Strip\n\n- `,`: settings" in text and "### List\n\n- `n`: new note" in text
    assert gaps == ["setting list.notes.calendar has no help", "command :mute doesn't say what it does", "key x in Strip has no label"]


def test_a_reference_gives_each_commands_keys_in_the_tree(tmp_path):
    docs = AppDocs("demo", keys=[["Space", "keys", "hill.tree"]], commands=[["new", "TITLE", "write a new note"]],
                   tree=[["n", "new", "new"]])
    text, _ = document("demo", [], docs)
    assert "- `:new TITLE` (`Space n`): write a new note." in text
    assert "reached by the keys after them in the key tree" in text


def test_an_app_is_documented_from_its_profile_and_its_last_hello(tmp_path):
    with pytest.raises(Unknown):
        reference("demo")
    profile = tmp_path / "demo.toml"
    profile.write_text(f'''app = "demo"
[[groups]]
id = "look"
name = "Look"
file = "{tmp_path / 'demo.json'}"
  [[groups.settings]]
  key = ["theme"]
  label = "Theme"
  help = "demo's colours."
  choices = "textual-themes"
  default = "textual-dark"
''')
    register("demo", profile)
    text, _ = reference("demo")
    assert "Choices: Textual's themes. Default: `textual-dark`." in text
    assert "hasn't said hello" in text
    remember(DEMO)
    assert ":new TITLE" in reference("demo")[0]
