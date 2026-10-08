import json
from pathlib import Path

import pytest

from settings_panel import JsonStore, TomlStore
from hill_ops.profiles import ProfileError, expand, known, load, register

PROFILE = """\
app = "demo"

[[groups]]
id = "list"
name = "List"
where = "demo · applies right away"
file = "${DEMO_HOME:-${XDG_CONFIG_HOME:-~/.config}/demo}/settings.json"

  [[groups.settings]]
  key = ["notes", "sort"]
  label = "Sort by"
  help = "Order of the notes list."
  choices = ["title", "modified"]
  default = "title"
  names = [["title", "by title"], ["modified", "newest first"]]

  [[groups.settings]]
  key = ["theme"]
  label = "Theme"
  choices = "textual-themes"
  default = "textual-dark"

[[groups]]
id = "editor"
name = "Editor"
file = "${DEMO_HOME}/editor.json"
keep_defaults = true

  [[groups.settings]]
  key = ["autosave"]
  label = "Autosave"
  choices = [0, 10, 60]
  default = 0
  names = [[0, "off"], [10, "10 s"]]

[[groups]]
id = "tool"
name = "Tool"
file = "${DEMO_HOME}/tool.toml"

  [[groups.settings]]
  key = ["ui", "wrap"]
  label = "Wrap"
  choices = [false, true]
  default = false
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HILL_CONFIG_HOME", str(tmp_path / "hill"))
    monkeypatch.setenv("DEMO_HOME", str(tmp_path / "demo"))
    return tmp_path


def write(path, text=PROFILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_paths_fill_in_variables_and_their_defaults(monkeypatch):
    monkeypatch.setenv("A", "/a")
    monkeypatch.setenv("EMPTY", "")
    monkeypatch.delenv("UNSET", raising=False)
    assert expand("${A}/x") == "/a/x"
    assert expand("${UNSET:-/d}/x") == "/d/x"
    assert expand("${EMPTY:-/d}") == "/d"  # empty counts as unset
    assert expand("${UNSET:-${A}/b}/c") == "/a/b/c"
    assert expand("${UNSET:-~/.config}/x") == str(Path.home() / ".config/x")
    with pytest.raises(ProfileError):
        expand("${A/x")


def test_a_profile_becomes_groups(home):
    profile = load(write(home / "demo.toml"), themes=lambda: ["nord", "textual-dark"])
    assert profile.app == "demo"
    assert [g.id for g in profile.groups] == ["list", "editor", "tool"]
    listing, editor, tool = profile.groups
    assert listing.store.path == home / "demo" / "settings.json" and listing.where == "demo · applies right away"
    sort, theme = listing.settings
    assert sort.key == ("notes", "sort") and sort.show("modified") == "newest first"
    assert theme.options() == ["nord", "textual-dark"]
    assert isinstance(editor.store, JsonStore) and editor.store.keep_defaults
    assert editor.settings[0].show(0) == "off" and editor.where == str(home / "demo" / "editor.json")
    assert isinstance(tool.store, TomlStore)


def test_theme_choices_without_textual_are_just_the_default(home):
    theme = load(write(home / "demo.toml")).groups[0].settings[1]
    assert theme.options() == ["textual-dark"]


@pytest.mark.parametrize("text, problem", [
    ("app = ", "isn't valid TOML"),
    ('[[groups]]\nid = "x"', "missing 'app'"),
    ('app = "a"\n[[groups]]\nid = "x"\nname = "X"', "missing 'file'"),
    ('app = "a"\n[[groups]]\nid = "x"\nname = "X"\nfile = "/f.json"\n[[groups.settings]]\n'
     'key = ["k"]\nlabel = "K"\nchoices = []\ndefault = 1', "needs a list of choices"),
])
def test_a_broken_profile_says_why(home, text, problem):
    with pytest.raises(ProfileError, match=problem):
        load(write(home / "broken.toml", text))


def test_registered_profiles_stay_known(home):
    first = write(home / "apps" / "demo.toml")
    link = register("demo", first)
    assert link.is_symlink() and link.resolve() == first.resolve()
    moved = write(home / "elsewhere" / "demo.toml")
    register("demo", moved)  # a newer place replaces the old link
    profiles, notes = known()
    assert [p.app for p in profiles] == ["demo"] and notes == []
    assert (home / "hill" / "profiles" / "demo.toml").resolve() == moved.resolve()


def test_a_relative_work_folder_is_the_profiles_own(home, monkeypatch):
    clone = home / "clone"
    register("demo", write(clone / "demo.toml", 'work = "work"\n' + PROFILE))
    monkeypatch.chdir(home)  # not hill-ops's working folder: the folder the link points to
    assert load(home / "hill" / "profiles" / "demo.toml").work == clone.resolve() / "work"
    write(clone / "demo.toml", 'work = "~/items"\n' + PROFILE)
    assert load(clone / "demo.toml").work == Path("~/items").expanduser()


def test_gone_or_broken_profiles_are_noted_not_fatal(home):
    register("gone", write(home / "gone.toml"))
    (home / "gone.toml").unlink()
    register("broken", write(home / "broken.toml", "app = "))
    register("demo", write(home / "demo.toml"))
    profiles, notes = known()
    assert [p.app for p in profiles] == ["demo"]
    assert len(notes) == 2 and any("gone's profile is gone" in n for n in notes)


def test_app_names_cannot_leave_the_profiles_folder(home):
    with pytest.raises(ProfileError):
        register("../evil", write(home / "demo.toml"))


def test_settings_go_to_the_apps_own_files(home):
    listing = load(write(home / "demo.toml")).groups[0]
    listing.change(listing.settings[0], 1)
    saved = json.loads((home / "demo" / "settings.json").read_text())
    assert saved == {"notes": {"sort": "modified"}}


def test_a_setting_marked_instance_is_each_instances_own(tmp_path, monkeypatch):
    path = tmp_path / "demo.toml"
    path.write_text("""\
app = "demo"

[[groups]]
id = "view"
name = "View"
file = "${DEMO_HOME}/settings.json"

  [[groups.settings]]
  key = ["notes", "preview"]
  label = "Preview"
  choices = [false, true]
  default = true
  instance = true

  [[groups.settings]]
  key = ["notes", "weeks"]
  label = "Weeks"
  choices = [8, 16]
  default = 16
""")
    monkeypatch.setenv("DEMO_HOME", str(tmp_path / "demo"))
    assert isinstance(load(path).groups[0].store, JsonStore)  # outside hill-ops
    monkeypatch.setenv("HILL_INSTANCE", str(tmp_path / "one"))
    (tmp_path / "one").mkdir()
    group = load(path).groups[0]
    preview, weeks = group.settings
    group.change(preview, 1)
    group.change(weeks, -1)
    assert json.loads((tmp_path / "one" / "demo.json").read_text()) == {"notes": {"preview": False}}
    monkeypatch.setenv("HILL_INSTANCE", str(tmp_path / "two"))
    (tmp_path / "two").mkdir()
    other = load(path).groups[0]
    other.change(other.settings[0], 1)  # false, from what the first left in settings.json, back to true
    assert group.value(preview) is False and other.value(other.settings[0]) is True
    assert group.value(weeks) == other.value(other.settings[1]) == 8  # shared
