import asyncio
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual import events

from keyline import Keyline
from settings_panel import SettingsScreen
import hill_ops.panel
from hill_ops.events import EVENTS, EventLog
from hill_ops.panel import Panel, SettingsApp
from hill_ops.screens import HelpScreen, PanelScreen
from hill_client import Client

PROFILE = """\
app = "demo"

[[groups]]
id = "list"
name = "List"
file = "${DEMO_HOME}/settings.json"

  [[groups.settings]]
  key = ["notes", "sort"]
  label = "Sort by"
  choices = ["title", "modified"]
  default = "title"

[[groups]]
id = "preview"
name = "Preview"
file = "${DEMO_HOME}/settings.json"

  [[groups.settings]]
  key = ["notes", "preview"]
  label = "Preview"
  choices = [false, true]
  default = true
"""


class FakeTmux:
    """Records tmux commands; the window is 30 rows, and the pane with the
    focus is the one last selected, the app's (%0) to start with. A new
    window's pane is %10, %11 and so on. What tmux's control mode reports
    comes from `report()`, and `signal()` ends a `wait()`."""

    def __init__(self):
        self.calls = []
        self.active = "%0"
        self.next_pane = 10
        self.reports = asyncio.Queue()
        self.signals = {}

    def run(self, *args):
        self.calls.append(args)
        if args[:2] == ("select-pane", "-t"):
            self.active = args[2]
        if args[:1] == ("new-window",):
            self.next_pane += 1
            return f"%{self.next_pane - 1}"
        if args[:3] == ("display", "-p", "-t") and args[4:] == ("#{pane_active}",):
            return "1" if args[3] == self.active else "0"
        if args[:3] == ("display", "-p", "-t") and args[4:] == ("#{window_id}",):
            return "@0"
        if args[:3] == ("display", "-p", "-t") and args[4:] == ("#{pane_id}",):
            return self.active
        if args[-1:] == ("#{@hill-status}",):
            return "0"
        if args[-1:] == ("#{window_width} #{window_height}",):
            return "80 30"
        return "30" if args[:2] == ("display", "-p") else ""

    def report(self, name, *args):
        self.reports.put_nowait((name, list(args)))

    async def notifications(self):
        while True:
            yield await self.reports.get()

    def signal(self, channel):
        self.signals.setdefault(channel, asyncio.Event()).set()

    async def wait(self, channel):
        await self.signals.setdefault(channel, asyncio.Event()).wait()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HILL_CONFIG_HOME", str(tmp_path / "hill"))
    monkeypatch.setenv("DEMO_HOME", str(tmp_path / "demo"))
    monkeypatch.setenv("TMUX_PANE", "%1")
    (tmp_path / "demo.toml").write_text(PROFILE)
    # Unix socket paths are short (104 bytes on macOS); pytest's tmp_path isn't.
    folder = Path(tempfile.mkdtemp(prefix="wp", dir="/tmp"))
    yield SimpleNamespace(root=tmp_path, socket=str(folder / "c.sock"), profile=str(tmp_path / "demo.toml"))
    shutil.rmtree(folder)


async def until(condition, what="condition"):
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def run_panel(env, script, event_log=None):
    """Run `script(panel, pilot, tmux)` against a strip in a 80×1 terminal."""
    async def go():
        tmux = FakeTmux()
        panel = Panel(Path(env.socket), app_pane="%0", tmux=tmux, event_log=event_log)
        async with panel.run_test(size=(80, 1)) as pilot:
            await pilot.pause()
            return await script(panel, pilot, tmux)

    return asyncio.run(go())


def labels(panel):
    return [k.label for k in panel.query_one("#strip", Keyline).keys]


def test_the_strip_shows_the_keys_of_the_last_app_to_say_hello(env):
    async def script(panel, pilot, tmux):
        seen = [labels(panel)]
        palace = Client(env.socket, "demo", env.profile, [(",", "settings", "app.settings"), ("q", "quit", "app.quit")])
        tasks = [asyncio.create_task(palace.run())]
        await until(lambda: panel.hub.active is not None, "hello")
        seen.append(labels(panel))
        micro = Client(env.socket, "micro", None, [("Alt-,", "settings", "settings")])
        tasks.append(asyncio.create_task(micro.run()))
        await until(lambda: panel.hub.active.app == "micro", "micro")
        seen.append(labels(panel))
        micro.close()  # micro quits: palace's keys are back
        await until(lambda: panel.hub.active.app == "demo", "micro gone")
        seen.append(labels(panel))
        palace.close()
        await asyncio.gather(*tasks)
        return seen

    assert run_panel(env, script) == [["settings"], ["settings", "quit"], ["settings"], ["settings", "quit"]]


def test_settings_grow_the_strip_open_on_the_group_and_shrink_back(env):
    async def script(panel, pilot, tmux):
        heard, elsewhere = [], []
        other = Client(env.socket, "other")
        other.on("settings.changed", elsewhere.append)
        demo = Client(env.socket, "demo", env.profile)
        demo.on("settings.changed", heard.append)
        tasks = [asyncio.create_task(other.run())]
        await until(lambda: panel.hub.active is not None, "other")
        tasks.append(asyncio.create_task(demo.run()))
        await until(lambda: panel.hub.active.app == "demo", "demo")
        demo.send("settings.open", group="preview")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "zoom")
        opened = list(tmux.calls)
        await pilot.resize_terminal(80, 10)  # what tmux does to the pane
        await until(lambda: isinstance(panel.screen, SettingsScreen), "settings")
        group = panel.screen.group.id
        await pilot.press("enter")  # Preview: on -> off
        await until(lambda: heard, "settings.changed")
        tmux.calls.clear()
        await pilot.press("escape")
        await pilot.pause()
        closed = list(tmux.calls)
        for client in (demo, other):
            client.close()
        await asyncio.gather(*tasks)
        return opened, group, heard, elsewhere, closed

    opened, group, heard, elsewhere, closed = run_panel(env, script)
    assert ("set", "-g", "@hill-height", "10") in opened  # 1/3 of 30 rows
    assert ("select-pane", "-t", "%1") in opened
    assert group == "preview"
    assert heard == [{"group": "preview", "key": ["notes", "preview"], "value": False}]
    assert elsewhere == []  # only the app whose setting it is hears of it
    saved = json.loads((Path(env.profile).parent / "demo" / "settings.json").read_text())
    assert saved == {"notes": {"preview": False}}
    assert closed == [("set", "-g", "@hill-height", "1"), ("resize-pane", "-t", "%1", "-y", "1"), ("select-pane", "-t", "%0")]


def test_an_apps_overview_is_the_panels_first_tab_and_a_pick_goes_back(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        picked = []
        demo = Client(env.socket, "demo", env.profile)
        demo.on("overview.pick", picked.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        lines = [
            {"id": "a/1.md", "text": [["● ", "red"], ["first item", ""]], "help": "waits for your go"},
            {"separator": True},
            {"id": "b/2.md", "text": "second item", "help": ""},
            "not a line",
        ]
        demo.send("overview", title="Overview", where="work items", verb="select", lines=lines)
        await until(lambda: panel.hub.active.overview is not None, "overview")
        demo.send("command.open")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, PanelScreen), "panel")
        await pilot.pause()
        screen = panel.screen
        tabs = [str(t.render()) for t in screen.query(".tab")]
        group = screen.group.id
        rows = [str(r.render()) for r in screen.query("#body .row") if not r.has_class("-separator")]
        rule = screen.query_one("#body .row.-separator").render_line(0).text
        screen.set_focus(None)  # the settings, not the line
        await pilot.press("down", "enter")
        await until(lambda: picked, "overview.pick")
        demo.send("overview", lines=[{"id": "c/3.md", "text": "third item"}])
        await until(lambda: [str(r.render()) for r in screen.query("#body .row")] == ["third item"], "new lines")
        demo.close()
        await task
        return tabs, group, rows, rule, picked, isinstance(panel.screen, PanelScreen)

    tabs, group, rows, rule, picked, still_open = run_panel(env, script, log)
    assert tabs[0] == "1 Overview" and "2 List" in tabs
    assert group == "demo:overview"
    assert rows == ["● first item", "second item"]
    assert set(rule) == {"─"} and len(rule) > 60  # across the panel, at 80 columns
    assert picked  # down stepped over the separator == [{"id": "b/2.md"}]
    assert still_open
    events = [json.loads(line) for line in (env.root / "events.jsonl").read_text().splitlines()]
    assert {"event": "overview.pick", "app": "demo"} in [{k: e[k] for k in ("event", "app")} for e in events]
    assert "b/2.md" not in (env.root / "events.jsonl").read_text()


def test_a_click_on_a_hint_runs_it_in_the_app(env):
    async def script(panel, pilot, tmux):
        ran = []
        demo = Client(env.socket, "demo", env.profile, [(",", "settings", "app.settings")])
        demo.on("run", ran.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await pilot.pause()
        await pilot.click("#strip", offset=(3, 0))
        await until(lambda: ran, "run")
        demo.close()
        await task
        return ran

    assert run_panel(env, script) == [{"action": "app.settings"}]


def test_a_taller_panel_applies_while_open(env):
    async def script(panel, pilot, tmux):
        panel.open_panel("layout")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, SettingsScreen), "settings")
        panel.screen.select(2)  # Layout → Panel height, after the widths
        tmux.calls.clear()
        await pilot.press("enter")  # Panel height: 1/3 -> 1/2
        await pilot.pause()
        return tmux.calls

    assert ("resize-pane", "-t", "%1", "-y", "15") in run_panel(env, script)


def test_a_profile_is_remembered_after_hello(env):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.close()
        await task

    run_panel(env, script)
    link = Path(env.profile).parent / "hill" / "profiles" / "demo.toml"
    assert link.is_symlink()


def test_settings_on_their_own_open_on_a_group(env, monkeypatch):
    (Path(env.profile).parent / "hill" / "profiles").mkdir(parents=True)
    (Path(env.profile).parent / "hill" / "profiles" / "demo.toml").symlink_to(env.profile)

    async def go():
        app = SettingsApp("preview")
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            return app.screen.group.id, [g.id for g in app.screen.groups]

    group, groups = asyncio.run(go())
    assert group == "preview"
    assert groups == ["list", "preview", "layout", "hill"]


COMMANDS = [
    ["new", "TITLE", "write a new note"],
    ["rename", "TITLE", "rename the selected note"],
    ["settings", "[GROUP]", "open the settings", ["list", "preview"]],
]
HELP = [["List", [["n", "new"], ["/", "search"]]], ["Map", [["g", "map from here"]]]]


def test_the_command_line_narrows_completes_and_runs(env):
    async def script(panel, pilot, tmux):
        lines = []
        demo = Client(env.socket, "demo", env.profile)
        demo.hello["commands"] = COMMANDS
        demo.on("command", lines.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("command.open")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "grown to the panel")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, PanelScreen) and panel.screen.focused is not None, "the line")
        assert ("set", "-g", "@hill-keep", "1") in tmux.calls  # the mouse doesn't take the focus from it
        matches = panel.screen.query_one("#matches")
        shown = [panel.screen.query_one("Input").value, matches.option_count, matches.display]
        await pilot.press("r")
        shown.append(matches.option_count)  # just rename
        await pilot.press("backspace", "s", "tab")  # completes "settings "
        shown.append(panel.screen.query_one("Input").value)
        shown.append(matches.option_count)  # its choices: list, preview
        await pilot.press("p", "tab", "enter")
        await until(lambda: lines, "the command")
        await until(lambda: not isinstance(panel.screen, PanelScreen), "the panel closed")
        demo.close()
        await task
        return shown, lines

    shown, lines = run_panel(env, script)
    assert shown == [":", 5, True, 1, ":settings ", 2]  # hill-ops's :set and :toggle too
    assert lines == [{"line": "settings preview"}]


def test_the_panels_line_runs_a_command_after_a_colon_and_stays_open(env, agent_log):
    async def script(panel, pilot, tmux):
        lines = []
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["commands"] = COMMANDS
        demo.on("command", lines.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        screen = panel.screen
        talk_hidden = []
        await pilot.press(":")
        talk_hidden.append(screen.has_class("-commanding"))
        await pilot.press("up", "up", "up", "tab", "enter")  # settings, before hill-ops's :set and :toggle
        await until(lambda: lines, "the command")
        said = [[(s.who, s.text) for s in panel.talk.said]]
        await pilot.press(*"hi", "enter")  # no colon: a question
        await until(lambda: panel.talk.said[0].who == "you" and not panel.talk.said[-1].waiting, "the answer")
        said.append([(s.who, s.text) for s in panel.talk.said])
        demo.close()
        await task
        return lines, said, panel.screen is screen, talk_hidden

    lines, said, still_open, talk_hidden = run_panel(env, script)
    assert lines == [{"line": "settings"}]
    assert said == [[("hill", "ran :settings")], [("you", "hi"), ("claude", "hi")]]
    assert still_open and talk_hidden == [True]


TREE = [
    ["s", "settings", [["l", "list", "settings list"], ["p", "preview", "settings preview"]]],
    ["n", "new note", "new"],
    ["x", "broken", "explode"],
]
TREE_KEYS = [(",", "settings", "app.settings"), ("Space", "keys", "hill.tree")]


def test_the_key_tree_goes_down_by_keys_and_sends_the_command_picked(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        lines = []
        demo = Client(env.socket, "demo", env.profile, TREE_KEYS, COMMANDS, HELP, tree=TREE)
        demo.on("command", lines.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        notes = list(panel.notes)
        demo.send("tree.open")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "7") in tmux.calls, "grown")
        await pilot.resize_terminal(80, 7)
        await until(lambda: type(panel.screen).__name__ == "TreeScreen", "the tree")
        screen = panel.screen
        shown = [[str(e.render()) for e in screen.query(".entry")]]
        await pilot.press("s")
        shown.append(str(screen.query_one("#path").render()))
        shown.append([str(e.render()) for e in screen.query(".entry")])
        await pilot.press("backspace", "s")
        await pilot.click(screen.query(".entry").last())  # preview
        await until(lambda: lines, "the command")
        await until(lambda: type(panel.screen).__name__ != "TreeScreen", "the tree closed")
        demo.send("help.open", section="commands")
        await until(lambda: type(panel.screen).__name__ == "HelpScreen", "the help")
        commands = dict(panel.screen.sections[2][1])
        demo.close()
        await task
        return notes, shown, lines, commands

    notes, shown, lines, commands = run_panel(env, script, event_log=log)
    assert notes == ["demo's key tree: key “x” runs “explode”, not a command"]
    assert shown == [["s  +settings", "n  new note"], "demo  Space s", ["l  list", "p  preview"]]
    assert lines == [{"line": "settings preview"}]
    assert commands["settings [GROUP]"] == "open the settings (Space s …)"
    assert commands["new TITLE"] == "write a new note (Space n)"
    events = [(e["event"], e.get("name"), e.get("choice")) for e in events_of(log) if e["event"].startswith("tree")]
    assert events == [("tree.open", None, None), ("tree", "settings", "preview")]


def test_the_command_line_shows_each_commands_keys_in_the_tree(env):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, TREE_KEYS, COMMANDS, tree=TREE)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("command.open")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "grown to the panel")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, PanelScreen) and panel.screen.focused is not None, "the line")
        matches = panel.screen.query_one("#matches")
        shown = [str(matches.get_option_at_index(i).prompt) for i in range(2)]
        demo.close()
        await task
        return shown

    assert run_panel(env, script) == [":new TITLE  Space n  write a new note", ":rename TITLE  rename the selected note"]


def test_a_question_is_answered_or_cancelled_on_the_line(env):
    async def script(panel, pilot, tmux):
        answers = []
        demo = Client(env.socket, "demo", env.profile)
        demo.on("answer", answers.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("ask", id=7, question="Rename “Root” to", value="Root")
        await until(lambda: type(panel.screen).__name__ == "QuestionScreen", "the question")
        value = panel.screen.query_one("Input").value
        await pilot.press("end", "backspace", "backspace", "backspace", "backspace", *"Home", "enter")
        await until(lambda: answers, "the answer")
        demo.send("ask", id=8, question="New note title")
        await until(lambda: type(panel.screen).__name__ == "QuestionScreen", "the second question")
        await pilot.press("escape")
        await until(lambda: len(answers) == 2, "the cancel")
        demo.close()
        await task
        return value, answers

    value, answers = run_panel(env, script)
    assert value == "Root"
    assert answers == [{"id": 7, "value": "Home"}, {"id": 8, "value": None}]


def test_help_opens_on_the_section_asked_for(env):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        demo.hello["help"] = HELP
        demo.hello["commands"] = COMMANDS
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("help.open", section="map")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "grown")
        await pilot.resize_terminal(80, 10)
        await until(lambda: type(panel.screen).__name__ == "HelpScreen", "the help")
        titles = [title for title, _ in panel.screen.sections]
        opened_on = panel.screen.current
        await pilot.press("right")
        after = panel.screen.current
        await pilot.press("escape")
        await pilot.pause()
        demo.close()
        await task
        return titles, opened_on, after, type(panel.screen).__name__

    titles, opened_on, after, screen = run_panel(env, script)
    assert titles == ["List", "Map", "Commands", "Strip"]
    assert (opened_on, after) == (1, 2)
    assert screen != "HelpScreen"


def test_a_request_while_the_strip_is_busy_waits_its_turn(env):
    async def script(panel, pilot, tmux):
        answers = []
        demo = Client(env.socket, "demo", env.profile)
        demo.on("answer", answers.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("help.open")
        await until(lambda: type(panel.screen).__name__ == "HelpScreen", "the help")
        demo.send("ask", id=1, question="Sure?")
        await asyncio.sleep(0.2)
        first = type(panel.screen).__name__
        await pilot.press("escape")  # closes the help: the question comes next
        await until(lambda: type(panel.screen).__name__ == "QuestionScreen", "the question")
        await pilot.press("y", "enter")
        await until(lambda: answers, "the answer")
        demo.close()
        await task
        return first, answers

    assert run_panel(env, script) == ("HelpScreen", [{"id": 1, "value": "y"}])


def test_hills_own_hints_run_in_the_strip(env):
    async def script(panel, pilot, tmux):
        ran = []
        micro = Client(env.socket, "micro", None, [("", "help", "hill.help"), ("Ctrl-e", "command", "hill.command")])
        micro.hello["help"] = HELP
        micro.on("run", ran.append)
        task = asyncio.create_task(micro.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await pilot.pause()
        await pilot.click("#strip", offset=(2, 0))  # "help"
        await pilot.resize_terminal(80, 10)
        await until(lambda: type(panel.screen).__name__ == "HelpScreen", "the help")
        await pilot.press("escape")
        micro.close()
        await task
        return ran

    assert run_panel(env, script) == []  # hill-ops ran it; nothing went to the app


def typed(text):
    return ["space" if c == " " else c for c in text]


def test_the_strip_logs_what_it_does_but_not_your_text(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        def showing(name):
            return type(panel.screen).__name__ == name

        def typing_in(name):  # once the screen has its input, which may take a moment
            return showing(name) and any(line.has_focus for line in panel.screen.query("Input"))

        demo = Client(env.socket, "demo", env.profile, [(",", "settings", "app.settings")])
        demo.hello["commands"] = COMMANDS
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await pilot.pause()
        await pilot.click("#strip", offset=(3, 0))  # the settings hint, which the app runs
        demo.send("settings.open", group="preview")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "zoom")
        await pilot.resize_terminal(80, 10)
        await until(lambda: showing("PanelScreen"), "settings")
        await pilot.press("enter", "escape")  # Preview: on -> off, and back
        await until(lambda: not showing("PanelScreen"), "the strip back")
        for keys in (["s", "tab", "l", "tab"], typed("rename rome trip")):
            demo.send("command.open")
            await until(lambda: typing_in("PanelScreen"), "the command line")
            await pilot.press(*keys, "enter")
            await until(lambda: not showing("PanelScreen"), "the command sent")
        demo.send("ask", id=1, question="Rename “Rome trip” to", value="Rome trip")
        await until(lambda: typing_in("QuestionScreen"), "the question")
        await pilot.press("escape")
        await until(lambda: not showing("QuestionScreen"), "the question gone")
        demo.close()
        await task
        await until(lambda: "bye" in log.path.read_text(), "bye")

    run_panel(env, script, log)
    text = log.path.read_text()
    # Claude doesn't run in tests, so the tip the panel asks for doesn't come.
    events = [e for e in map(json.loads, text.splitlines()) if e["event"] != "problem"]
    assert {e["app"] for e in events} == {"demo"}
    assert [{k: v for k, v in e.items() if k not in ("time", "app")} for e in events] == [
        {"event": "hello"},
        {"event": "run", "action": "app.settings"},
        {"event": "settings.open", "group": "preview"},
        {"event": "settings.changed", "group": "preview", "key": ["notes", "preview"], "value": False},
        {"event": "close"},
        {"event": "command.open"},
        {"event": "command", "name": "settings", "choice": "list"},
        {"event": "close"},
        {"event": "command.open"},
        {"event": "command", "name": "rename"},
        {"event": "close"},
        {"event": "ask"},
        {"event": "answer", "answered": False},
        {"event": "close"},
        {"event": "bye"},
    ]
    assert "rome" not in text.lower()  # neither the title typed nor the one in the question
    assert {e["event"] for e in events} <= set(EVENTS)


def test_the_event_log_turns_off_in_hills_settings(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        panel.open_panel("hill")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, SettingsScreen), "settings")
        labels = [s.label for s in panel.screen.group.settings]
        panel.screen.select(labels.index("Event log"))
        await pilot.press("enter", "escape")  # on -> off
        await pilot.pause()
        return labels

    assert "Event log" in run_panel(env, script, log)
    events = [json.loads(line)["event"] for line in log.path.read_text().splitlines()]
    assert [e for e in events if e != "problem"] == ["settings.open"]  # the tip asked for may come first
    assert json.loads((env.root / "hill" / "settings.json").read_text()) == {"events": False}


def test_an_apps_error_is_logged_not_shown(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, [("q", "quit", "app.quit")])
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("error", what="push failed")
        demo.send("error", what="x" * 500)
        await until(lambda: log.path.read_text().count('"error"') == 2, "the errors logged")
        shown = labels(panel)
        demo.close()
        await task
        return shown

    assert run_panel(env, script, log) == ["quit"]  # no note on the strip: the app shows its errors itself
    errors = [json.loads(line) for line in log.path.read_text().splitlines() if '"error"' in line]
    assert [(e["app"], e["what"]) for e in errors] == [("demo", "push failed"), ("demo", "x" * 200)]


CLAUDE_KEYS = [(",", "settings", "app.settings"), ("Alt-c", "Claude", "hill.claude")]


def events_of(log):
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def tip_label(panel):
    """The tip, or what Claude has to say, on the strip's line."""
    return next((k for k in panel.query_one("#strip", Keyline).keys if k.label.startswith("✦")), None)


def prompts(agent_log):
    return [m["params"]["prompt"][0]["text"] for m in map(json.loads, agent_log.read_text().splitlines())
            if m.get("method") == "session/prompt"]


async def open_panel(demo, panel, pilot, tmux, method="claude.open", **params):
    """Alt-c in the app (or `method`, such as settings.open): the panel,
    grown to its height."""
    demo.send(method, **params)
    await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "grown")
    await pilot.resize_terminal(80, 10)
    await until(lambda: isinstance(panel.screen, PanelScreen), "the panel")


def keys_shown(panel):
    """The keys under the panel."""
    return [(k.key, k.label) for k in panel.screen.query_one("#settings-keys", Keyline).keys]


def tip_line(panel):
    return [(k.key, k.label) for k in panel.screen.query_one("#tip", Keyline).keys]


def rows(panel):
    """Where the panel's parts are: their first row and how many rows."""
    return {w.id: (w.region.y, w.region.height) for w in panel.screen.query("#body, #help, #tabs, #talk, #tip, #line")}


def test_claude_offers_a_tip_once_a_session_and_the_panel_takes_it(env, agent_log, monkeypatch):
    monkeypatch.setattr(hill_ops.panel, "TIP_AFTER", 0.2)
    monkeypatch.setenv("FAKE_TIP", json.dumps(
        {"tip": "You turn the preview off: keep it off?", "setting": {"group": "preview", "key": ["notes", "preview"], "value": False}}
    ))
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        heard = []
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.on("settings.changed", heard.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: tip_label(panel) is not None, "the tip")
        shown = tip_label(panel)
        await open_panel(demo, panel, pilot, tmux)
        line = tip_label(panel)  # the tip has left the strip's line for the panel
        tip = tip_line(panel)
        offered = keys_shown(panel)[0]  # the focus is on the line to ask on
        await pilot.press("enter")
        await until(lambda: heard and panel.screen.group.id == "preview", "the setting changed and shown")
        await pilot.pause()
        after = keys_shown(panel)[0], tip_line(panel), panel.screen.setting.label, str(panel.screen.query_one("#help").render())
        await pilot.press("escape")
        await pilot.pause()
        demo.close()
        await task
        return shown, line, tip, offered, after, heard

    shown, line, tip, offered, after, heard = run_panel(env, script, log)
    assert (shown.key, shown.label) == ("Alt-c", "✦ You turn the preview off: keep it off?")
    assert line is None
    assert tip == [("✦", "You turn the preview off: keep it off?"), ("✓", "set Preview → Preview to off")]
    assert offered == ("Ret", "set Preview → Preview to off")
    keys, tip, label, help = after
    assert keys == ("Ret", "ask") and tip == [("✦", "You turn the preview off: keep it off?")]  # taken once
    assert label == "Preview" and "Saved." in help  # the panel shows what changed
    assert heard == [{"group": "preview", "key": ["notes", "preview"], "value": False}]
    assert [e["event"] for e in events_of(log)] == ["hello", "tip", "claude.open", "claude.take", "settings.changed", "close", "bye"]
    assert prompts(agent_log) == [prompts(agent_log)[0]] and prompts(agent_log)[0].startswith("Tip, unasked")


def test_the_panel_serves_a_tip_and_a_click_or_alt_c_gets_another(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_TIP", '{"tip": "Press m for the map"}')
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open", group="preview")
        await until(lambda: tip_line(panel) == [("✦", "Press m for the map")], "the tip served")
        focused = panel.screen.focused  # the settings have the focus
        await pilot.click("#tip", offset=(2, 0))
        await until(lambda: len(prompts(agent_log)) == 2 and not panel.asking, "another tip")
        await pilot.press("alt+c")
        await until(lambda: len(prompts(agent_log)) == 3 and not panel.asking, "and another")
        await pilot.press("escape")
        await pilot.pause()
        await open_panel(demo, panel, pilot, tmux, "settings.open", group="list")
        again = tip_line(panel)
        demo.close()
        await task
        return focused, again

    focused, again = run_panel(env, script, log)
    assert focused is None
    assert again == [("✦", "Press m for the map")]  # it stays until another is asked for
    asked = prompts(agent_log)
    assert len(asked) == 3
    assert asked[0].startswith("Tip, for the panel as it opened, on Preview → Preview.")
    assert asked[1].startswith("Tip, asked in the panel, on Preview → Preview.") and asked[2].startswith("Tip, asked")
    assert [(e["event"], e.get("about")) for e in events_of(log) if e["event"] in ("claude.open", "tip")] == [
        ("tip", None), ("claude.open", "settings"), ("tip", None), ("claude.open", "settings"), ("tip", None),
    ]


def test_the_tip_line_says_so_while_claude_is_looking(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "hang")  # no tip until the turn is cancelled

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open")
        await until(lambda: prompts(agent_log), "the tip asked for")
        looking = tip_line(panel)
        demo.close()
        await task
        return looking

    assert run_panel(env, script) == [("✦", "Claude is looking for a tip…")]


def test_a_question_in_the_panel_is_answered_above_the_tip_and_its_help_opens(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWER", 'g shows the map from the selected note.\n{"help": "map"}')
    readme = env.root / "README.md"
    readme.write_text("# demo\n\nThe map shows how notes link.\n")
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS, docs=str(readme))
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        await pilot.press(*typed("how do I see links from rome?"), "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        await pilot.pause()
        said = [(s.who, s.text) for s in panel.talk.said]
        where = rows(panel)
        offered = keys_shown(panel)[0]
        await pilot.press("r")
        typing = keys_shown(panel)[0]  # Ret asks while something's typed
        await pilot.press("backspace", "enter")
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help")
        section = panel.screen.section
        await pilot.press("escape")
        await until(lambda: isinstance(panel.screen, PanelScreen), "back to the panel")
        await pilot.pause()
        back = tmux.active, keys_shown(panel)[0], [(s.who, s.text) for s in panel.talk.said]
        await pilot.press("escape")
        await pilot.pause()
        gone = panel.talk.said
        demo.close()
        await task
        return said, where, offered, typing, section, back, gone

    said, where, offered, typing, section, back, gone = run_panel(env, script, log)
    assert said == [("you", "how do I see links from rome?"), ("claude", "g shows the map from the selected note.")]
    assert where["talk"] == (where["tip"][0] - 2, 2) and where["line"][0] == where["tip"][0] + 1  # above the tip
    assert where["help"][0] == where["body"][0] + where["body"][1]  # the description under the tiles
    assert offered == ("Ret", "open the help on Map")
    assert typing == ("Ret", "ask")
    assert section == "Map"
    assert back == ("%1", ("Ret", "ask"), said)  # still in the strip, the offer taken
    assert gone == []  # your question and its answer go with the panel
    assert [e["event"] for e in events_of(log)] == [
        "hello", "claude.open", "claude.ask", "claude.take", "help.open", "close", "close", "bye",
    ]
    assert "rome" not in log.path.read_text()  # neither the question nor the answer
    tip, question = prompts(agent_log)  # the panel asked for a tip as it opened
    assert question.startswith("Question.") and question.endswith("Their question: how do I see links from rome?")
    assert "The map shows how notes link." in tip  # the app's README, in the first message


def test_claude_offers_a_command_and_ret_runs_it(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWER", 'Show the preview settings:\n{"command": "settings preview"}')
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        lines = []
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["commands"] = COMMANDS
        demo.on("command", lines.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        await pilot.press(*typed("where is the preview?"), "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        await pilot.pause()
        offered = keys_shown(panel)[0]
        await pilot.press("enter")
        await until(lambda: lines, "the command")
        said = [(s.who, s.text) for s in panel.talk.said]
        demo.close()
        await task
        return offered, lines, said, isinstance(panel.screen, PanelScreen)

    offered, lines, said, still_open = run_panel(env, script, log)
    assert offered == ("Ret", "run :settings preview")
    assert lines == [{"line": "settings preview"}]
    assert said[-1] == ("hill", "ran :settings preview") and still_open
    assert [e["event"] for e in events_of(log)][-3:] == ["claude.take", "command", "bye"]


def test_a_long_answer_leaves_the_settings_room(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWER", "\n".join(f"line {i}" for i in range(12)))

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        await pilot.press("h", "i", "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        await pilot.pause()
        where = rows(panel)
        keys = keys_shown(panel)
        demo.close()
        await task
        return where, keys

    where, keys = run_panel(env, script)
    assert where["body"] == (where["tabs"][0] + 1, 2)  # the tabs, then a row of tiles
    assert where["help"][0] == where["body"][0] + 2  # the description under them
    assert where["talk"] == (where["help"][0] + where["help"][1], 2)  # then the answer, which scrolls
    assert ("PgUp PgDn", "scroll") in keys


def test_an_answer_that_comes_after_the_panel_closed_is_announced_on_the_line(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWER", "Press , for the settings.")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        panel.ask_question("where are the settings?")
        panel.screen.dismiss(None)  # closed before Claude could answer
        await until(lambda: tip_label(panel) is not None, "the line")
        line = tip_label(panel)
        await open_panel(demo, panel, pilot, tmux)
        said = [(s.who, s.text) for s in panel.talk.said]
        demo.close()
        await task
        return line, said

    line, said = run_panel(env, script, EventLog(env.root / "events.jsonl"))
    assert (line.key, line.label) == ("Alt-c", "✦ Claude has answered")
    assert said == [("you", "where are the settings?"), ("claude", "Press , for the settings.")]


def test_alt_c_in_the_help_gives_a_tip_about_whats_shown(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_TIP", '{"tip": "g maps from the selected note"}')

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("help.open", section="map")
        await until(lambda: ("resize-pane", "-t", "%1", "-y", "10") in tmux.calls, "zoom")
        await pilot.resize_terminal(80, 10)
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help")
        await pilot.press("alt+c")
        await until(lambda: "g maps" in str(panel.screen.query_one("#note").render()), "the tip")
        demo.close()
        await task
        return panel.talk.tip.text

    assert run_panel(env, script, EventLog(env.root / "events.jsonl")) == "g maps from the selected note"  # the panel's too
    assert prompts(agent_log)[0].startswith("Tip, asked in the help, on Map.")


def test_tips_that_are_off_and_a_claude_that_cant_start_say_so(env, agent_log, monkeypatch):
    (env.root / "hill").mkdir()
    (env.root / "hill" / "settings.json").write_text('{"claude_tips": "off"}')
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open", group="preview")
        await pilot.press("alt+c")
        off = tip_line(panel)
        await pilot.press("escape")
        await pilot.pause()
        monkeypatch.setenv("HILL_CLAUDE_AGENT", "no-such-agent-here")
        await open_panel(demo, panel, pilot, tmux)  # questions don't need tips
        await pilot.press("h", "i", "enter")
        await until(lambda: not panel.talk.said[-1].waiting, "the problem")
        said = panel.talk.said[-1]
        demo.close()
        await task
        return off, (said.who, said.text)

    off, said = run_panel(env, script, log)
    assert off == [("✦", "Claude's tips are off: Hill → Claude's tips")]
    assert said == ("hill", "Claude needs no-such-agent-here, which can't start")
    assert {"event": "problem", "text": said[1]} in [{k: e.get(k) for k in ("event", "text")} for e in events_of(log)]
    assert not agent_log.exists()  # no agent ran


def test_down_and_up_move_between_the_settings_and_the_line(env):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open", group="list")
        seen = [keys_shown(panel)[0]]
        await pilot.press("down")  # from the last row: the line
        seen.append(keys_shown(panel)[0])
        await pilot.press("v", "1", "space")  # typed, not the settings' keys
        typed_in = panel.screen.query_one("#input").value
        await pilot.press("up")  # back to the settings
        seen.append(keys_shown(panel)[0])
        await pilot.press("v")  # the list view
        seen.append(keys_shown(panel)[0])
        demo.close()
        await task
        return seen, typed_in, panel.screen.view, panel.screen.group.id

    seen, typed_in, view, group = run_panel(env, script)
    assert seen == [("←↑↓→", "move"), ("Ret", "ask"), ("←↑↓→", "move"), ("↑↓", "move")]
    assert (typed_in, view, group) == ("v1 ", "list", "list")


def test_the_panel_stays_open_until_esc(env, agent_log):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        seen = []
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        seen.append(panel.screen.focused is not None)  # Alt-c: on the line to ask
        panel.post_message(events.AppBlur())  # the terminal lost the focus to another window
        await pilot.pause()
        tmux.active = "%0"  # a click in the app
        panel.post_message(events.AppBlur())
        await pilot.pause()
        seen.append(type(panel.screen).__name__)
        demo.send("settings.open", group="preview")  # , in the app: the focus back, on the settings
        await until(lambda: tmux.active == "%1" and panel.screen.group.id == "preview", "the focus back")
        seen.append(panel.screen.focused is not None)
        tmux.active = "%0"
        demo.send("claude.open")  # Alt-c in the app: back on the line
        await until(lambda: tmux.active == "%1" and panel.screen.focused is not None, "on the line")
        await pilot.press("escape")
        await pilot.pause()
        seen.append(type(panel.screen).__name__)
        demo.send("help.open", section="map")
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help")
        tmux.active = "%0"
        panel.post_message(events.AppBlur())  # a click in the app closes the help
        await until(lambda: not isinstance(panel.screen, HelpScreen), "the help closed")
        demo.close()
        await task
        return seen

    assert run_panel(env, script, log) == [True, "PanelScreen", False, "Screen"]
    assert [e["event"] for e in events_of(log)][:5] == ["hello", "claude.open", "close", "help.open", "close"]


def lit(panel):
    """How the strip shows whether it has the focus: the background of the
    panel's settings and Claude's part, or of the help, and whether its
    key line is lit."""
    screen = panel.screen
    parts = ["#panel", "#talk"] if isinstance(screen, PanelScreen) else ["#body"]
    backgrounds = {screen.query_one(part).background_colors[1].hex for part in parts}
    return backgrounds.pop() if len(backgrounds) == 1 else backgrounds, screen.query_one(hill_ops.panel.FOCUS_KEYS).has_class("-active")


def test_the_panel_shows_the_focus_and_takes_it_as_the_mouse_moves_over_it(env, agent_log):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open")
        seen = [(lit(panel), panel.kept)]  # the panel lets the focus go
        tmux.active = "%0"  # the mouse moved over the app, which took the focus
        panel.post_message(events.AppBlur())
        await pilot.pause()
        seen.append(lit(panel))
        await pilot.hover("#body", offset=(5, 1))  # rests here: the focus left from under it
        seen.append(tmux.active)
        await pilot.hover("#body", offset=(5, 3))
        await pilot.pause()
        seen.append((tmux.active, lit(panel)))
        demo.send("help.open", section="map")
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help over the panel")
        seen.append((lit(panel), panel.kept))  # the help keeps the focus
        await pilot.press("escape")
        await until(lambda: isinstance(panel.screen, PanelScreen), "the panel back")
        await pilot.press("escape")
        await until(lambda: not isinstance(panel.screen, PanelScreen), "the strip's line")
        await pilot.resize_terminal(80, 1)
        panel.post_message(events.AppBlur())
        await pilot.pause()
        tmux.calls.clear()
        await pilot.hover("#strip", offset=(5, 0))
        await pilot.hover("#strip", offset=(20, 0))
        seen.append([call for call in tmux.calls if call[0] == "select-pane"])  # the line never takes it
        demo.close()
        await task
        return seen

    assert run_panel(env, script) == [
        (("#272727", True), "0"),
        ("#121212", False),
        "%0",
        ("%1", ("#272727", True)),
        (("#272727", True), "1"),
        [],
    ]


def test_text_selected_in_the_panel_goes_to_the_clipboard(env, agent_log):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, "settings.open")
        await pilot.pause()
        described = str(panel.screen.query_one("#help").render())
        await pilot.mouse_down("#help", offset=(0, 0))
        await pilot.hover("#help", offset=(30, 0))
        await pilot.mouse_up("#help", offset=(30, 0))
        await pilot.pause()
        demo.close()
        await task
        return described, panel._clipboard

    described, copied = run_panel(env, script)
    assert len(copied) > 10 and copied in described


def test_the_line_takes_the_focus_with_the_panel_unless_you_chose_the_settings(env, agent_log):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")

        async def away_and_back():
            panel.post_message(events.AppBlur())
            await pilot.pause()
            panel.post_message(events.AppFocus())
            await pilot.pause()
            return panel.screen.focused is panel.screen.query_one("Input")

        await open_panel(demo, panel, pilot, tmux, "settings.open")
        panel.post_message(events.AppFocus())  # tmux says so, a moment later
        await pilot.pause()
        seen = [panel.screen.focused is None]  # opened for the settings: they keep it
        seen.append(await away_and_back())  # back: you can type a question at once
        await pilot.press("h", "i")
        seen.append(panel.screen.query_one("Input").value)
        await pilot.press("up")  # you chose the settings...
        panel.post_message(events.AppFocus())
        await pilot.pause()
        seen.append(panel.screen.focused is None)  # ...and they keep it while it stays
        seen.append(await away_and_back())  # until the focus went and came back
        demo.close()
        await task
        return seen

    assert run_panel(env, script) == [True, True, "hi", True, True]


def test_what_opens_over_the_panel_goes_back_to_it(env, agent_log):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["commands"] = COMMANDS
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        await pilot.press("h", "i", "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        tmux.active = "%0"  # back in the app, the panel open
        demo.send("help.open", section="map")  # not kept waiting for the panel to close
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help over the panel")
        tmux.calls.clear()
        await pilot.press("escape")
        await until(lambda: isinstance(panel.screen, PanelScreen), "the panel back")
        closed = list(tmux.calls)
        demo.send("command.open", text="se")  # the panel's own line, with ":se" typed
        await until(lambda: panel.screen.query_one("Input").value == ":se", "the command on the panel's line")
        said = [(s.who, s.text) for s in panel.talk.said]
        await pilot.press("escape")
        await until(lambda: not isinstance(panel.screen, PanelScreen), "the panel closed")
        demo.close()
        await task
        return closed, said

    closed, said = run_panel(env, script, log)
    assert closed == [("display", "-p", "#{window_height}"), ("set", "-g", "@hill-height", "10"),
                      ("resize-pane", "-t", "%1", "-y", "10"), ("set", "-g", "@hill-keep", "0"),
                      ("select-pane", "-t", "%0")]
    assert said == [("you", "hi"), ("claude", "hi")]
    assert [e["event"] for e in events_of(log)][:8] == [
        "hello", "claude.open", "claude.ask", "help.open", "close", "close", "bye",
    ]


def test_the_panel_follows_the_app_on_the_strip(env, agent_log):
    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["commands"] = COMMANDS
        tasks = [asyncio.create_task(demo.run())]
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        placeholder = lambda: panel.screen.query_one("Input").placeholder
        seen = [placeholder()]
        micro = Client(env.socket, "micro", None, [("Alt-c", "Claude", "claude")])
        tasks.append(asyncio.create_task(micro.run()))  # micro, started from the app, with no commands
        await until(lambda: panel.screen.about == "micro", "micro")
        seen.append(placeholder())
        micro.close()
        await until(lambda: panel.screen.about == "demo", "demo again")
        seen.append(placeholder())
        demo.close()
        await asyncio.gather(*tasks)
        return seen

    assert run_panel(env, script) == ["Search, ask Claude, or : for demo's commands", "Search or ask Claude, : to set",
                                      "Search, ask Claude, or : for demo's commands"]


THEMED = PROFILE + """
[[groups]]
id = "look"
name = "Look"
file = "${DEMO_HOME}/settings.json"

  [[groups.settings]]
  key = ["theme"]
  label = "Theme"
  choices = "textual-themes"
  default = "textual-dark"
"""


def test_the_strip_follows_the_theme_of_the_app(env):
    Path(env.profile).write_text(THEMED)
    settings = env.root / "demo" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"theme": "rose-pine"}))

    async def script(panel, pilot, tmux):
        seen = [panel.theme]
        demo = Client(env.socket, "demo", env.profile)
        tasks = [asyncio.create_task(demo.run())]
        await until(lambda: panel.hub.active is not None, "hello")
        seen.append(panel.theme)
        micro = Client(env.socket, "micro", None)  # no theme of its own: the strip keeps demo's
        tasks.append(asyncio.create_task(micro.run()))
        await until(lambda: len(panel.hub.stack) == 2, "micro")
        seen.append(panel.theme)
        settings.write_text(json.dumps({"theme": "nord"}))  # as the panel writes it
        groups, _ = hill_ops.panel.settings_groups(panel.prefs, "demo", lambda: ["nord"], panel.setting_changed)
        look = next(g for g in groups if g.id == "look")
        look.on_change(look.settings[0], "nord")
        seen.append(panel.theme)
        demo.close()
        await until(lambda: len(panel.hub.stack) == 1, "demo gone")
        seen.append(panel.theme)
        micro.close()
        await asyncio.gather(*tasks)
        return seen

    assert run_panel(env, script) == ["textual-dark", "rose-pine", "rose-pine", "nord", "textual-dark"]


def test_an_app_runs_programs_beside_it_and_one_over_them(env):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile)
        heard = []
        demo.on("over.done", heard.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        demo.send("panes", view={"name": "preview", "argv": ["demo", "preview"], "cwd": "/v"},
                  claude={"name": "Claude", "argv": ["demo", "claude"]})
        await until(lambda: panel.layout.side.get("claude") is not None, "the side panes")
        started = [c for c in tmux.calls if c[0] == "new-window"]
        assert [c[c.index("--") + 1:] for c in started] == [(sys.executable, "-m", "hill_ops", "_side")] * 2  # through the relay
        assert [next(a for a in c if a.startswith("HILL_ARGV=")) for c in started] == [
            f"HILL_ARGV={json.dumps(['demo', 'preview'])}", f"HILL_ARGV={json.dumps(['demo', 'claude'])}"]
        assert ("-c", "/v") == started[0][5:7]
        assert panel.hub.active.side == {"%10", "%11"}
        demo.send("over", name="micro", argv=["micro", "a.md"])
        await until(lambda: panel.layout.over is not None, "micro over them")
        assert ("select-pane", "-t", "%12") in tmux.calls
        assert ("set", "-p", "-t", "%12", "window-style", "bg=#121212", ";",
                "set", "-p", "-t", "%12", "window-active-style", "bg=#272727") in tmux.calls  # as the panel
        tmux.signal("hill-over-12")  # micro has exited
        await until(lambda: heard, "the app told")
        assert panel.layout.over is None and ("kill-pane", "-t", "%12") in tmux.calls
        demo.close()
        await task
        return heard

    assert run_panel(env, script, log) == [{"status": 0}]
    events = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert [{k: v for k, v in e.items() if k not in ("time", "app")} for e in events if e["event"] != "hello"][:3] == [
        {"event": "panes", "view": "preview", "claude": "Claude"},
        {"event": "over", "name": "micro"},
        {"event": "over.done", "status": 0},
    ]
    assert "a.md" not in log.path.read_text()  # the program's name, not its arguments


def test_the_strip_shows_the_keys_of_the_app_in_the_pane_with_the_focus(env):
    async def script(panel, pilot, tmux):
        palace = Client(env.socket, "demo", env.profile, [("q", "quit", "app.quit")])
        palace.hello["pane"] = "%0"
        tasks = [asyncio.create_task(palace.run())]
        await until(lambda: panel.hub.active is not None, "hello")
        palace.send("panes", view={"name": "preview", "argv": ["demo", "preview"]})
        await until(lambda: panel.layout.side.get("view") is not None, "the preview")
        micro = Client(env.socket, "micro", None, [("Alt-,", "settings", "settings")])
        micro.hello["pane"] = "%20"  # a pane of its own
        tasks.append(asyncio.create_task(micro.run()))
        await until(lambda: len(panel.hub.stack) == 2, "micro")
        seen = []
        for pane in ("%20", "%0", "%10"):  # micro's, the app's, the preview
            tmux.report("%window-pane-changed", "@0", pane)
            await until(lambda: panel.hub.focused == pane, pane)
            seen.append(labels(panel))
        tmux.report("%window-pane-changed", "@0", "%1")  # the strip's own
        await pilot.pause(0.1)
        seen.append(labels(panel))
        micro.close()
        palace.close()
        await asyncio.gather(*tasks)
        return seen, panel.back_to

    seen, back_to = run_panel(env, script)
    # The preview is the app's; the strip, focused, stays with the app it took the focus from.
    assert seen == [["settings"], ["quit"], ["quit"], ["quit"]]
    assert back_to == "%10"  # where the focus goes when the strip shrinks


def test_a_new_window_size_is_logged_and_claude_offers_a_tip_about_the_layout(env, agent_log, monkeypatch):
    monkeypatch.setattr(hill_ops.panel, "RESIZE_SETTLE", 0.1)
    monkeypatch.setenv("FAKE_TIP", json.dumps(
        {"tip": "Wide window: a wider Claude pane?", "setting": {"group": "layout", "key": ["layout", "claude"], "value": "40%"}}
    ))
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None and panel.window_seen == (80, 30), "hello, and the window")
        tmux.report("%layout-change", "@0", "abcd,90x32,0,0,0", "abcd,90x32,0,0,0", "*")  # a little wider
        await until(lambda: "resize" in log.path.read_text(), "the size logged")
        tmux.report("%layout-change", "@0", "abcd,200x60,0,0,0", "abcd,200x60,0,0,0", "*")  # another monitor
        await until(lambda: tip_label(panel) is not None, "a tip on the strip's line")
        tmux.report("%layout-change", "@0", "abcd,80x30,0,0,0", "abcd,80x30,0,0,0", "*")  # and back, soon after
        await until(lambda: log.path.read_text().count('"resize"') == 3, "the size logged again")
        await pilot.pause(0.2)
        demo.close()
        await task
        return tip_label(panel)

    shown = run_panel(env, script, log)
    assert shown.label == "✦ Wide window: a wider Claude pane?"
    sizes = [(e["cols"], e["rows"]) for e in events_of(log) if e["event"] == "resize"]
    assert sizes == [(90, 32), (200, 60), (80, 30)]
    (asked,) = prompts(agent_log)  # one tip: a tenth wider isn't enough, and the next comes too soon
    assert asked.startswith("Tip, unasked, on the strip's line, after the window changed size from 80×30 to 200×60")


def test_words_on_the_line_find_settings_and_commands_and_ret_takes_one(env, agent_log):
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.hello["commands"] = COMMANDS
        demo.hello["help"] = HELP
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        screen = panel.screen
        matches = screen.query_one("#matches")
        await pilot.press(*"sort")
        seen = [(screen.has_class("-searching"), matches.option_count, screen.picked(), keys_shown(panel)[0])]
        await pilot.press("down")
        seen.append((screen.picked().name, keys_shown(panel)[0]))
        await pilot.press("enter")  # Sort by: shown selected in its tab, the focus on the settings
        await pilot.pause()
        seen.append((screen.focused, screen.group.id, screen.setting.label, screen.query_one("Input").value))
        screen.focus_line()
        await pilot.press(*"rename", "down", "enter")  # a command: typed on the line, to finish
        seen.append(screen.query_one("Input").value)
        screen.query_one("Input").value = ""
        await pilot.press(*"from here", "tab", "enter")  # a key from the help: the help, on its section
        await until(lambda: isinstance(panel.screen, HelpScreen), "the help")
        seen.append(panel.screen.section)
        await pilot.press("escape")
        await until(lambda: panel.screen is screen, "back to the panel")
        await pilot.press(*"zzz", "enter")  # nothing found: Claude is asked
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        seen.append([s.who for s in panel.talk.said])
        demo.close()
        await task
        return seen

    seen = run_panel(env, script, log)
    assert seen[0] == (True, 1, None, ("Ret", "ask"))  # none picked: Ret would ask
    assert seen[1] == ("Sort by", ("Ret", "show it"))
    assert seen[2] == (None, "list", "Sort by", "")
    assert seen[3] == ":rename "
    assert seen[4] == "Map"
    assert seen[5] == ["you", "claude"]
    searched = [e for e in events_of(log) if e["event"] == "search"]
    assert [e["kind"] for e in searched] == ["setting", "command", "key"]
    assert "sort" not in log.path.read_text().casefold().replace('"notes", "sort"', "")  # what's typed isn't kept


def test_set_changes_a_setting_from_the_line(env):
    async def script(panel, pilot, tmux):
        heard = []
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        demo.on("settings.changed", heard.append)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        screen = panel.screen
        await pilot.press(*":set list.n", "tab")  # completes the setting
        completed = screen.query_one("Input").value
        await pilot.press(*"mod", "tab", "enter")  # and its value
        await until(lambda: heard, "settings.changed")
        said = [panel.talk.said[-1].text]
        await pilot.press(*":set list.notes.sort sideways", "enter")
        said.append(panel.talk.said[-1].text)
        await pilot.press(*":tog", "tab", *"list.notes.s", "tab", "enter")  # round to the first
        await until(lambda: len(heard) == 2, "settings.changed, toggled")
        said.append(panel.talk.said[-1].text)
        await pilot.pause()
        demo.close()
        await task
        return completed, heard, said, screen.setting.label

    completed, heard, said, selected = run_panel(env, script)
    assert completed == ":set list.notes.sort "
    assert heard == [
        {"group": "list", "key": ["notes", "sort"], "value": "modified"},
        {"group": "list", "key": ["notes", "sort"], "value": "title"},
    ]
    assert said == [
        "List → Sort by is now modified",
        "List → Sort by takes one of title, modified",
        "List → Sort by is now title",
    ]
    assert selected == "Sort by"
    saved = json.loads((Path(env.profile).parent / "demo" / "settings.json").read_text())
    assert saved == {}  # back to its default, which isn't kept


def test_claude_files_a_work_item_in_a_project(env, agent_log, monkeypatch):
    work = env.root / "demo-work"
    work.mkdir()
    Path(env.profile).write_text('work = "${DEMO_HOME}-work"\n' + PROFILE)
    draft = {"project": "demo", "title": "Dark map", "goal": "The map follows the theme."}
    monkeypatch.setenv("FAKE_ANSWER", "demo can't yet.\n" + json.dumps({"work": draft}))
    log = EventLog(env.root / "events.jsonl")

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        await pilot.press(*typed("a dark map please"), "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        await pilot.pause()
        offered = keys_shown(panel)[0]
        await pilot.press("enter")
        await until(lambda: panel.talk.said[-1].who == "hill", "filed")
        demo.close()
        await task
        return offered, panel.talk.said[-1].text

    offered, filed = run_panel(env, script, log)
    assert offered == ("Ret", "file a demo work item: Dark map")
    assert filed.startswith("Filed demo 001, Dark map:") and filed.endswith("not committed")
    assert "# Dark map" in (work / "001-dark-map.md").read_text()
    assert "- [001](001-dark-map.md) open: Dark map" in (work / "README.md").read_text()
    assert {"event": "work.filed", "project": "demo"} in [{k: e.get(k) for k in ("event", "project")} for e in events_of(log)]
    assert "Dark map" not in log.path.read_text()
    assert "Open work items of demo" in "\n".join(prompts(agent_log))


def test_claude_shows_where_a_setting_is(env, agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWER", 'In List.\n{"show": {"group": "list", "key": ["notes", "sort"]}}')

    async def script(panel, pilot, tmux):
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux, group="preview")
        await pilot.press(*typed("where is sorting"), "enter")
        await until(lambda: panel.talk.said and not panel.talk.said[-1].waiting, "the answer")
        await pilot.pause()
        shown = [panel.screen.group.id, keys_shown(panel)[0]]
        await pilot.press("enter")
        await pilot.pause()
        shown.append((panel.screen.focused, panel.screen.group.id, panel.screen.setting.label))
        saved = (Path(env.profile).parent / "demo" / "settings.json").exists()
        demo.close()
        await task
        return shown, saved

    shown, saved = run_panel(env, script)
    assert shown == ["preview", ("Ret", "show List → Sort by"), (None, "list", "Sort by")]
    assert not saved  # shown, not changed


def test_the_strip_shows_the_time_as_its_setting_says(env):
    async def script(panel, pilot, tmux):
        strip = panel.query_one("#strip", Keyline)
        shown = [strip.right]
        demo = Client(env.socket, "demo", env.profile, CLAUDE_KEYS)
        task = asyncio.create_task(demo.run())
        await until(lambda: panel.hub.active is not None, "hello")
        await open_panel(demo, panel, pilot, tmux)
        for form in ("24-hour", "12-hour", "off"):
            await pilot.press(*f":set hill.clock {form}", "enter")
            await pilot.pause()
            shown.append(strip.right)
        demo.close()
        await task
        return shown

    first, h24, h12, off = run_panel(env, script)
    assert first  # the locale's, by default
    assert re.fullmatch(r"\d\d:\d\d", h24)
    assert re.fullmatch(r"\d{1,2}:\d\d [AP]M", h12)
    assert off == ""
