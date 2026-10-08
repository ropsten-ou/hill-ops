import asyncio
import json

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from settings_panel import OFF_ON, Entry, Group, JsonStore, Listing, Setting, SettingsScreen, TomlStore, panel_settings

TOML = """\
# Tool configuration.

[list]
default_view = "grid"

[backup]
# kept as is
auto_push = false
"""


@pytest.fixture
def files(tmp_path):
    (tmp_path / "editor.json").write_text(json.dumps({"ft:markdown": {"softwrap": True}}))
    (tmp_path / "tool.toml").write_text(TOML)
    return tmp_path


class Demo(App):
    def __init__(self, files, full=False, start=None, screen=SettingsScreen):
        super().__init__()
        self.prefs = JsonStore(files / "prefs.json")
        self.changes = []
        editor = JsonStore(files / "editor.json")
        tool = TomlStore(files / "tool.toml")
        self.groups = [
            Group("editor", "Editor", "editor.json", editor, [
                Setting(("keymenu",), "Key menu", "Show the key menu.", OFF_ON, False),
                Setting(("softwrap",), "Wrap", "Wrap lines.", OFF_ON, False),
                Setting(("wordwrap",), "At spaces", "Wrap at spaces.", OFF_ON, False),
                Setting(("ft:markdown", "softwrap"), "Wrap notes", "Wrap Markdown.", OFF_ON, False),
                Setting(("ruler",), "Numbers", "Line numbers.", OFF_ON, True),
                Setting(("mouse",), "Mouse", "Mouse.", OFF_ON, True),
                Setting(("scrollbar",), "Scroll bar", "Scroll bar.", OFF_ON, False),
            ]),
            Group("tool", "Tool", "tool.toml", tool, [
                Setting(("list", "default_view"), "Layout", "Layout.", ["grid", "tree"], "grid"),
                Setting(("backup", "auto_backup_interval"), "Every", "Timer.", [None, 15, 30], None, {None: "off"}),
            ], on_change=lambda s, v: self.changes.append((s.key, v))),
            Group("panel", "Panel", "prefs.json", self.prefs, panel_settings()),
        ]
        self.full = full
        self.start = start
        self.screen_class = screen

    def compose(self) -> ComposeResult:
        yield Static("text behind the panel")

    def on_mount(self) -> None:
        self.push_screen(self.screen_class(self.groups, self.prefs, full=self.full, close_keys=("comma",), start=self.start))


class WithInput(SettingsScreen):
    """A panel with an input under the settings, as a subclass adds one."""

    def compose_under(self) -> ComposeResult:
        yield Input(id="ask")


def drive(files, *keys, size=(100, 36), full=False, start=None, screen=SettingsScreen):
    """Run the panel, press `keys` ("focus" gives the focus to the input of
    WithInput), and report what it shows."""
    result = {}

    async def go():
        app = Demo(files, full, start, screen)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                if key == "focus":
                    app.screen.query_one("#ask").focus()
                else:
                    await pilot.press(key)
                await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            result["open"] = isinstance(app.screen, SettingsScreen)
            if result["open"]:
                panel = app.screen.query_one("#panel")
                result["panel_height"] = panel.outer_size.height
                result["panel_order"] = [child.id for child in panel.children]
                result["help"] = str(app.screen.query_one("#help").render())
                result["group"] = app.screen.group.id
                result["view"] = app.screen.view
                result["rows"] = {w.id: (w.region.y, w.region.height) for w in app.screen.query("#body, #help, #tabs, #ask, #settings-keys")}
                result["typed"] = next((w.value for w in app.screen.query("#ask")), None)
            result["screenshot"] = app.export_screenshot()
            result["changes"] = app.changes

    asyncio.run(go())
    return result


def editor(files):
    return json.loads((files / "editor.json").read_text())


def test_change_and_back_to_default(files):
    drive(files, "enter")  # first tile: Key menu
    assert editor(files) == {"keymenu": True, "ft:markdown": {"softwrap": True}}
    drive(files, "backspace")  # back to the default, so the file drops it
    assert editor(files) == {"ft:markdown": {"softwrap": True}}


def test_nested_key_is_removed_with_its_empty_parent(files):
    drive(files, "right", "right", "right", "backspace")  # Wrap notes: on -> off (default)
    assert editor(files) == {}


def test_tiles_move_in_two_dimensions(files):
    # 100 columns fit 4 tiles a row: right, right, down lands on tile 6.
    drive(files, "right", "right", "down", "enter")
    assert editor(files)["scrollbar"] is True


def test_list_view_is_remembered(files):
    drive(files, "v")
    assert json.loads((files / "prefs.json").read_text()) == {"views": {"editor": "list"}}
    drive(files, "down", "down", "right")  # the list: third setting, changed
    assert editor(files)["wordwrap"] is True


def test_toml_keeps_comments_and_none_removes(files):
    result = drive(files, "2", "enter")  # Tool group: Layout -> tree
    text = (files / "tool.toml").read_text()
    assert 'default_view = "tree"' in text and "# kept as is" in text and "# Tool configuration." in text
    assert result["changes"] == [(("list", "default_view"), "tree")]
    drive(files, "2", "right", "enter")
    assert "auto_backup_interval = 15" in (files / "tool.toml").read_text()
    drive(files, "2", "right", "backspace")
    assert "auto_backup_interval" not in (files / "tool.toml").read_text()


def test_panel_settings_apply_right_away(files):
    assert drive(files)["panel_order"] == ["tabs", "body", "help"]
    assert drive(files, "3", "enter")["panel_height"] == 18  # 1/3 -> 1/2 of 36
    assert drive(files, "3", "right", "enter")["panel_order"] == ["body", "help", "tabs"]


def test_screen_behind_stays_visible(files):
    assert "text&#160;behind&#160;the&#160;panel" in drive(files)["screenshot"]


def test_full_panel_fills_the_screen(files):
    assert drive(files, full=True, size=(80, 24))["panel_height"] == 23


def test_the_description_sits_right_under_the_tiles(files):
    rows = drive(files)["rows"]  # 7 settings in 4 columns: two rows of tiles
    assert rows["body"][1] == 5 and rows["help"][0] == rows["body"][0] + 5
    assert rows["body"][0] == rows["tabs"][0] + 1  # the tabs above the settings
    rows = drive(files, "3", "right", "enter")["rows"]  # Group tabs: below them
    assert rows["tabs"][0] == rows["settings-keys"][0] - 1  # at the bottom, the description still under the tiles
    assert rows["help"][0] == rows["body"][0] + rows["body"][1]
    rows = drive(files, "2")["rows"]  # Tool: one row
    assert rows["body"][1] == 2 and rows["help"][0] == rows["body"][0] + 2
    rows = drive(files, "2", "v")["rows"]  # as a list
    assert rows["body"][1] == 2 and rows["help"][0] == rows["body"][0] + 2
    rows = drive(files, full=True, size=(100, 7))["rows"]  # too few rows: the tiles scroll, the description stays
    assert rows["body"][1] == 3 and rows["help"][0] == rows["body"][0] + 3


def test_a_subclass_adds_widgets_under_the_settings(files):
    result = drive(files, "focus", "v", "1", "space", "enter", "down", full=True, size=(80, 24), screen=WithInput)
    assert result["rows"]["ask"] == (20, 3) and result["panel_height"] == 20  # above the keys, the panel above it
    assert (result["typed"], result["view"], result["group"]) == ("v1 ", "tiles", "editor")  # the input's keys
    assert editor(files) == {"ft:markdown": {"softwrap": True}}  # nothing changed


def test_close_keys(files):
    assert drive(files)["open"]
    assert not drive(files, "escape")["open"]
    assert not drive(files, "comma")["open"]


def test_unreadable_file_is_left_alone(files):
    (files / "editor.json").write_text("{ not json")
    result = drive(files, "enter")
    assert (files / "editor.json").read_text() == "{ not json"
    assert "Not saved" in result["help"]


def test_keep_defaults_writes_them_out(tmp_path):
    store = JsonStore(tmp_path / "s.json", keep_defaults=True)
    store.set(("keymenu",), True, False)
    store.set(("keymenu",), False, False)
    store.set(("ft:markdown", "softwrap"), False, False)
    assert json.loads((tmp_path / "s.json").read_text()) == {"keymenu": False, "ft:markdown": {"softwrap": False}}


def test_opens_on_the_start_group(files):
    assert drive(files)["group"] == "editor"
    assert drive(files, start="tool")["group"] == "tool"
    assert drive(files, start="no such group")["group"] == "editor"


class WithListing(App):
    """A Listing of lines to pick from, then a group of settings."""

    def __init__(self, files, entries):
        super().__init__()
        self.prefs = JsonStore(files / "prefs.json")
        self.picked = []
        self.listing = Listing("due", "Due", "work items due", entries, self.picked.append, verb="select", empty="Nothing due.")
        self.groups = [self.listing, Group("panel", "Panel", "prefs.json", self.prefs, panel_settings())]

    def on_mount(self) -> None:
        self.push_screen(SettingsScreen(self.groups, self.prefs))


def pick(files, entries, *keys, then=None):
    """Run a panel with a Listing, press `keys`, call `then(screen)` if
    given, and report what it shows and what was picked."""
    result = {}

    async def go():
        app = WithListing(files, entries)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            if then is not None:
                then(app.screen)
                await app.workers.wait_for_complete()
                await pilot.pause()
            result["picked"] = [e.id for e in app.picked]
            result["help"] = str(app.screen.query_one("#help").render())
            result["keys"] = str(app.screen.query_one("#settings-keys").render())
            result["rows"] = [str(w.render()) for w in app.screen.query("#body .row")]
            result["view"] = app.screen.view
            result["selected"] = app.screen.entry.id if app.screen.entry else None

    asyncio.run(go())
    return result


LINES = [Entry("first line", "about the first", "a"), Entry("second line", "", "b"), Entry("third line", "about the third", "c")]


def test_a_listing_picks_its_lines(files):
    result = pick(files, LINES, "down", "enter")
    assert result["picked"] == ["b"]
    assert result["rows"] == ["first line", "second line", "third line"]
    assert result["view"] == "list"
    assert "Ret  select" in result["keys"]
    assert "work items due" in result["help"]  # a line without help says what the lines are
    assert pick(files, LINES)["help"].startswith("about the first")


def test_a_listing_ignores_the_settings_keys(files):
    result = pick(files, LINES, "right", "backspace", "v")
    assert result["picked"] == []
    assert result["view"] == "list"
    assert not (files / "prefs.json").exists()


def test_an_empty_listing_says_so(files):
    result = pick(files, [], "enter")
    assert result["picked"] == []
    assert result["rows"] == ["Nothing due."]


def test_new_lines_keep_the_selected_one(files):
    def update(screen):
        screen.show_entries("due", [Entry("new", "", "z"), *LINES])

    result = pick(files, LINES, "down", "down", then=update)
    assert result["rows"][0] == "new"
    assert result["selected"] == "c"


def test_a_separator_draws_across_the_list_and_is_never_selected(files):
    lines = [LINES[0], Entry("", separator=True), LINES[1], Entry("", separator=True)]
    result = {}

    def rule(app):
        item = app.screen.query("#body .row")[1]
        return item.render_line(0).text, app.screen.query_one("#body").content_region.width

    async def go():
        app = WithListing(files, lines)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            result["wide"] = rule(app)
            await pilot.press("down")
            result["down"] = app.screen.entry.id
            await pilot.press("down")
            result["past"] = app.screen.entry.id  # nothing to pick under it
            await pilot.click("#body .row", offset=(5, 1))  # nothing on a separator
            await pilot.pause()
            result["clicked"] = app.screen.entry.id
            await pilot.press("up")
            result["up"] = app.screen.entry.id
            await pilot.resize_terminal(60, 36)
            await pilot.pause()
            result["narrow"] = rule(app)
            app.screen.show_entries("due", [Entry("", separator=True), LINES[2]])
            await app.workers.wait_for_complete()
            await pilot.pause()
            result["first"] = app.screen.entry.id
            await pilot.press("enter")
            result["picked"] = [e.id for e in app.picked]

    asyncio.run(go())
    for text, width in (result["wide"], result["narrow"]):
        assert text == "─" * width
    assert result["narrow"][1] < result["wide"][1]
    assert (result["down"], result["past"], result["clicked"], result["up"]) == ("b", "b", "b", "a")
    assert result["first"] == "c" and result["picked"] == ["c"]
