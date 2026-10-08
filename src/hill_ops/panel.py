"""hill-ops's strip: the pane under the app, or under the panes it runs beside
it. It shows the app-wide keys of the app in the pane with the focus, and
grows, when asked, into the panel, which holds the settings with one line
under them for the app's commands and for Claude, or the app's help, or
its key tree (see keytree.py), or holds a question from the app; then it shrinks back. An app can send
an overview, lines to pick from, which the panel shows as its first tab. Claude offers tips on
the panel's tip line, and on the strip's line once a session, and answers
in the panel what you ask there (see claude.py). What it does goes in the session's event log (see
events.py).

It also lays out hill-ops's window (see layout.py): the panes an app runs
beside it, a program it runs over them, and their widths; and it follows
the window through tmux's control mode, for the pane with the focus and the
window's size. The open panel takes the focus as the mouse moves over it,
as palace's panes do, and is lighter while it has it.

The pane is resized through tmux, and what it shows appears once the new size
has arrived, so it never flashes squeezed into one row.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.screen import Screen
from textual.widgets import Input

from acp_client import Agent, AgentError
from keyline import Key, Keyline
from settings_panel import OFF_ON, Entry, Group, JsonStore, Listing, OverlayStore, Setting, SettingsScreen, StoreError, panel_settings
from hill_client import SOCKET, Hover

from . import clock
from .claude import Answer, Context, command, meta, parse_answer, parse_tip, question_prompt, shown, tip_prompt
from .events import SETTING as EVENT_LOG_KEY
from .events import EventLog, command_words, is_on
from .hub import Hub, Peer
from .index import AppDocs, Found, build, remember, remembered
from .layout import APP_WIDTH, CLAUDE_WIDTH, PANEL_HEIGHT, SESSION, Layout, Side, Tmux, window_size
from .instances import INSTANCE
from .paths import config_home
from .profiles import ProfileError, app_theme, known, register
from .work import Project, file_item, hill_project
from .runner import over_channel
from .keytree import OPEN_ACTION as TREE_ACTION
from .keytree import check_tree, opener, sequences
from .screens import HelpScreen, PanelScreen, QuestionScreen, Said, Talk, TreeScreen

PANEL_FRACTIONS = {"1/4": 1 / 4, "1/3": 1 / 3, "1/2": 1 / 2}
STRIP_ROWS = 1
TREE_ROWS = 7
"""The key tree's height, at most: its path, five rows of entries and its keys."""
STRIP_HELP = [
    ("click", "run a hint on the strip"),
    ("Esc", "close what the strip shows and go back to the app; so does a click on the app"),
    (":", "panel's line: a command, which the app runs; anything else searches, and Ret asks Claude"),
    ("↓ Tab", "panel's line, with matches shown: pick one, which Ret takes: a setting or a key shows, a command is typed"),
    ("key tree", "a key goes down a group or runs its command, as a click on it does; Bksp goes up a level"),
    (":set :toggle", "panel's line: change a setting to a value, or to its next one, GROUP.KEY completed with Tab"),
    ("Tab", "panel's line, after \":\": complete the highlighted command"),
    ("↑ ↓", "panel's line, after \":\": pick a command; else between the settings and the line"),
    ("Ret", "panel's line: run the command, ask Claude, or take what it offers; question: answer it"),
    ("PgUp PgDn", "panel: scroll Claude's answer"),
    ("drag a border", "resize the panes beside it; the widths snap to Layout's steps, and are kept"),
    ("Alt-c", "panel: another tip from Claude, as a click on the tip does; help: a tip about what's shown"),
]
"""The strip's own keys, at the end of every app's help."""
DEFAULT_THEME = "textual-dark"
"""Textual's own theme, for an app with no theme setting."""
STRIP_SCREENS = (PanelScreen, HelpScreen, QuestionScreen, TreeScreen)
ERROR_LENGTH = 200
"""An app's error is a short phrase; the log keeps this much of one."""
EVENT_LOG = Setting(
    (EVENT_LOG_KEY,), "Event log", "Keep a month of commands, settings and help you use, for Claude; never your text.",
    OFF_ON, True,
)
TIPS = Setting(
    ("claude_tips",), "Claude's tips",
    "When Claude offers tips: always in the panel, and once a session on the strip's line; only when asked; "
    "or never. Questions work either way.",
    ["once a session", "on Alt-c", "off"], "once a session", names={"once a session": "always", "on Alt-c": "only when asked"},
)
MODEL = Setting(
    ("claude_model",), "Claude's model", "The model for Claude in the strip: sonnet, the faster haiku, or opus.",
    ["sonnet", "haiku", "opus"], "sonnet",
)
CLOCK = Setting(
    ("clock",), "Clock",
    "The time at the strip's right end: in the locale's format, 12-hour, 24-hour, or off. "
    "On a narrow strip it goes before the app's keys do.",
    [clock.LOCALE, clock.H12, clock.H24, clock.OFF], clock.LOCALE,
)
TIP_AFTER = 120.0
"""Seconds from the first hello to the tip offered once a session on the
strip's line."""
TIP_SHOWN = 60.0
"""Seconds a tip stays on the strip's line."""
TIP_WAIT = 90.0
"""Seconds to wait for a tip."""
ANSWER_WAIT = 120.0
"""Seconds to wait for an answer to a question, the time it waits its turn
included."""
SETTLE = 0.3
"""Seconds after the last change of the window's layout to look at it: a
pane that has gone, or a border dragged."""
RESIZE_SETTLE = 2.0
"""Seconds a new size of the window must last for the event log to have it."""
RESIZE_CHANGE = 0.2
"""How much the window's width or height must change, as a share, for
Claude to offer a tip about the layout: another monitor, or maximized."""
RESIZE_TIP_EVERY = 600.0
"""Seconds between two tips about the window's size, at least."""
FOCUS_TINT = 0.05
"""How much of the text's color tints the background of what has the focus."""
FOCUSED = f"background: $surface; background-tint: $foreground {FOCUS_TINT:.0%};"
"""The background of the panel and the help while the strip has the focus:
lighter than without it (in a light theme, darker), as palace's panes are."""
FOCUS_KEYS = "#settings-keys, #help-keys"
"""The key lines lit while the strip has the focus: the panel's and the help's."""


def settings_groups(
    prefs: JsonStore,
    active: str | None,
    themes: Callable[[], list[str]],
    notify: Callable[[str, Group, Setting, Any], None],
) -> tuple[list[Group], list[str]]:
    """Every known app's settings groups, the active app's first, then
    hill-ops's own, Layout and Hill; and a note for each profile that can't be
    read. `notify(app, group, setting, value)` hears of each change, with
    "hill" for hill-ops's own."""
    groups, notes = app_groups(prefs, active, themes, notify)
    return [group for _, group in groups], notes


LAYOUT = [APP_WIDTH, CLAUDE_WIDTH, PANEL_HEIGHT]
"""The Layout group's settings, which each instance keeps of its own."""


def hill_prefs() -> JsonStore | OverlayStore:
    """hill-ops's own settings: settings.json, with the Layout group kept in the
    instance (layout.json in $HILL_INSTANCE) when there is one, so that
    each hill-ops keeps its own widths."""
    prefs = JsonStore(config_home() / "settings.json")
    if instance := os.environ.get(INSTANCE):
        return OverlayStore(prefs, Path(instance) / "layout.json", {s.key: s.default for s in LAYOUT})
    return prefs


def hill_groups(prefs: JsonStore) -> list[Group]:
    """hill-ops's own settings, Layout and Hill, saved in `prefs`."""
    _, tabs = panel_settings()
    return [
        Group("layout", "Layout", "hill-ops · applies right away" + (" · this instance" if isinstance(prefs, OverlayStore) else ""), prefs, LAYOUT),
        Group("hill", "Hill", "hill-ops · applies right away", prefs, [tabs, CLOCK, EVENT_LOG, TIPS, MODEL]),
    ]


def app_groups(
    prefs: JsonStore,
    active: str | None,
    themes: Callable[[], list[str]],
    notify: Callable[[str, Group, Setting, Any], None],
) -> tuple[list[tuple[str, Group]], list[str]]:
    """settings_groups(), each group with its app."""
    profiles, notes = known(themes)
    profiles.sort(key=lambda p: (p.app != active, p.app))
    groups = []
    for app, group in [(p.app, g) for p in profiles for g in p.groups] + [("hill", g) for g in hill_groups(prefs)]:
        group.on_change = lambda setting, value, app=app, group=group: notify(app, group, setting, value)
        groups.append((app, group))
    return groups, notes


def setting_id(group: Group, setting: Setting) -> str:
    """How `:set` names a setting: its group's id and its key, by dots."""
    return ".".join((group.id, *setting.key))


def value_word(value: Any) -> str:
    """How `:set` writes a value: a string as it is, else as JSON (true, 4)."""
    return value if isinstance(value, str) else json.dumps(value)


def set_commands(groups: list[Group]) -> list[list]:
    """hill-ops's `:set` and `:toggle`, as the line lists them: `:set`'s
    choices each setting with each of its values, `:toggle`'s each setting."""
    settings = [(g, s) for g in groups if not isinstance(g, Listing) for s in g.settings]
    pairs = [f"{setting_id(g, s)} {value_word(o)}" for g, s in settings for o in s.options()]
    return [
        ["set", "SETTING VALUE", "change a setting", pairs],
        ["toggle", "SETTING", "change a setting to its next value", [setting_id(g, s) for g, s in settings]],
    ]


def next_value(setting: Setting, value: Any) -> Any:
    """What `:toggle` changes a setting to: the value after `value` in its
    choices, round to the first."""
    options = setting.options()
    at = options.index(value) if value in options else -1
    return options[(at + 1) % len(options)]


def find_setting(line: str, groups: list[Group]) -> tuple[Group, Setting, Any] | str:
    """The setting and value a `:set` or `:toggle` `line` names (without
    its ":"), or what's wrong with it. `:set`'s value is one of the
    setting's choices, as `value_word` writes it or as the panel shows it;
    `:toggle`'s is the one after the setting's value now."""
    verb, _, rest = line.strip().partition(" ")
    name, _, word = rest.strip().partition(" ")
    word = word.strip()
    for group in groups:
        for setting in group.settings if not isinstance(group, Listing) else []:
            if setting_id(group, setting) != name:
                continue
            if verb == "toggle":
                try:
                    return group, setting, next_value(setting, group.value(setting))
                except (StoreError, OSError) as error:
                    return f"{group.name} → {setting.label}: {error}"
            for option in setting.options():
                if word.casefold() in (value_word(option).casefold(), setting.show(option).casefold()):
                    return group, setting, option
            choices = ", ".join(value_word(o) for o in setting.options())
            return f"{group.name} → {setting.label} takes one of {choices}" if word else f"{group.name} → {setting.label}: which value? {choices}"
    return f"no setting {name}" if name else f"{verb} what? {'SETTING VALUE' if verb == 'set' else 'SETTING'}, completed with Tab"


def overview_listing(peer: Peer, pick: Callable[[Peer, Entry], None]) -> Listing | None:
    """The app's overview as a tab of the panel, if it sent one: its lines,
    each `{id, text, help}`, the text a string or `[text, style]` spans;
    `pick(peer, entry)` hears each line picked."""
    if peer.overview is None:
        return None
    overview = peer.overview
    return Listing(
        f"{peer.app}:overview", str(overview.get("title") or "Overview"), str(overview.get("where") or f"{peer.app}'s overview"),
        overview_entries(overview), lambda entry: pick(peer, entry), verb=str(overview.get("verb") or "open"),
        empty=str(overview.get("empty") or "Nothing here."),
    )


def overview_entries(overview: dict) -> list[Entry]:
    """The lines of an app's overview, skipping what isn't one."""
    entries = []
    for line in overview.get("lines") or []:
        if not isinstance(line, dict):
            continue
        if line.get("separator"):
            entries.append(Entry("", separator=True))
            continue
        text = Text()
        spans = line.get("text")
        for span in [[spans]] if isinstance(spans, str) else spans if isinstance(spans, list) else []:
            if not (isinstance(span, list) and span and isinstance(span[0], str)):
                continue
            try:
                style = Style.parse(str(span[1])) if len(span) > 1 and span[1] else ""
            except StyleSyntaxError:
                style = ""
            text.append(span[0], style=style)
        entries.append(Entry(text, str(line.get("help") or ""), str(line.get("id") or "")))
    return entries


def help_sections(peer: Peer | AppDocs | None) -> list[tuple[str, list[tuple[str, str]]]]:
    """An app's help: its own sections, then its commands, then the strip's keys."""
    sections = []
    if peer is not None:
        for title, entries in peer.help:
            sections.append((title, [(str(e[0]), str(e[1])) for e in entries if isinstance(e, list) and len(e) >= 2]))
        if peer.commands:
            keys = sequences(peer.tree, opener(peer.keys))
            rows = [
                (f"{c[0]} {c[1] if len(c) > 1 else ''}".strip(),
                 (str(c[2]) if len(c) > 2 else "") + (f" ({keys[c[0]]})" if c[0] in keys else ""))
                for c in peer.commands
            ]
            sections.append(("Commands", rows))
    sections.append(("Strip", STRIP_HELP))
    return sections


def _one_per_folder(projects: list[Project]) -> list[Project]:
    """The first project of each work folder: an app whose items are filed
    in hill-ops's, as palace's are, isn't a second project."""
    seen: set[Path] = set()
    kept = []
    for project in projects:
        if (folder := project.folder.expanduser().resolve()) not in seen:
            seen.add(folder)
            kept.append(project)
    return kept


class Panel(App):
    """The strip, in the pane under the app."""

    CSS = f"""
    Screen {{ background: $panel; }}
    SettingsScreen #panel {{ border-top: none; }}
    PanelScreen #panel, PanelScreen #talk, HelpScreen #body, HelpScreen #note {{ background: $background; }}
    App:focus PanelScreen #panel, App:focus PanelScreen #talk, App:focus HelpScreen #body, App:focus HelpScreen #note {{
        {FOCUSED}
    }}
    """
    BINDINGS = [
        # The strip lasts as long as the app above it, and hill-ops ends both.
        Binding("ctrl+q", "nothing", show=False, priority=True),
        Binding("ctrl+c", "nothing", show=False, priority=True),
        Binding("alt+c", "claude", show=False, priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self, socket: Path, app_pane: str | None = None, tmux: Tmux | None = None, event_log: EventLog | None = None,
    ) -> None:
        super().__init__()
        self.hub = Hub(socket, self.hello, self.show_keys, self.request, self.bye, self.report)
        self.app_pane = app_pane
        """The pane the app runs in."""
        self.back_to = app_pane
        """The pane that gets the focus back when the strip shrinks: the one
        that had it last."""
        self.tmux = tmux or Tmux()
        self.event_log = event_log or EventLog(None)
        """The session's event log."""
        self.pane = os.environ.get("TMUX_PANE", "")
        env = {name: os.environ[name] for name in (SOCKET, "HILL_RUN", INSTANCE) if name in os.environ}
        self.layout = Layout(self.tmux, app_pane, self.pane, env | {SOCKET: str(socket)}) if app_pane else None
        """The panes of hill-ops's window."""
        self.window: str | None = None
        """hill-ops's window, as tmux names it ("@0")."""
        self.window_seen: tuple[int, int] | None = None
        """The window's size, as last seen."""
        self.size_logged: tuple[int, int] | None = None
        """The window's size, as the event log has it."""
        self.size_tipped: tuple[int, int] | None = None
        """The window's size when hill-ops started, or when it last changed
        enough for a tip about the layout."""
        self.resize_tip_at: float | None = None
        self.settle_timer: Any = None
        self.resize_timer: Any = None
        self.prefs = hill_prefs()
        self.notes: list[str] = []
        self.showing: Callable[[], None] | None = None
        """What to show once the strip has grown."""
        self.rows = STRIP_ROWS
        """The height the strip last asked for."""
        self.waiting: list[Callable[[], None]] = []
        """Requests that came while the strip was busy."""
        self.agent: Agent | None = None
        """Claude, over ACP, started for the first tip or question."""
        self.context = Context()
        """What Claude has been told in its session."""
        self.talk = Talk()
        """Claude's part of the panel: your latest question and its answer,
        and the tip."""
        self.tip_problem: str | None = None
        """Why the last tip asked for didn't come, until another is asked for."""
        self.tip_on_line = False
        """Whether the strip's line shows the tip."""
        self.line_note: str | None = None
        """What the strip's line says of Claude instead, such as that it has answered."""
        self.tip_timer: Any = None
        self.asking = False
        """Whether Claude is looking for a tip."""
        self.tip_planned = False
        self.hover = Hover()
        """Where the mouse is, for the focus that follows it into the panel."""
        self.kept: str | None = None
        """What the strip last told tmux of keeping the focus (@hill-keep)."""
        self.indexed_for: tuple | None = None
        """The apps on the channel when the open panel's search was made."""
        self.command_only = False
        """Whether the panel opened as the app's command line (`:`): it
        keeps the focus, as the help does, and closes once its command has
        run, or with a click in the app. A question to Claude makes it the
        panel."""

    def compose(self) -> ComposeResult:
        yield Keyline(id="strip")

    async def on_mount(self) -> None:
        await self.hub.start()
        self.show_keys()
        self.tick()
        if self.layout is not None:
            self.apply_layout()
            self.run_worker(self.watch(), exit_on_error=False)

    async def on_unmount(self) -> None:
        await self.hub.stop()
        if self.agent is not None:
            await self.agent.stop()

    def action_nothing(self) -> None:
        pass

    # -- the strip --

    def show_keys(self) -> None:
        peer = self.hub.active
        if peer is None:
            keys = [Key("hill", "settings", "app.remote('hill.settings')")]
        else:
            keys = [Key(k[0], k[1], f"app.remote({k[2]!r})" if len(k) > 2 and k[2] else None) for k in peer.keys]
        notes = [Key("!", text) for text in self.notes]
        self.query_one("#strip", Keyline).set_keys(*notes, *self.tip_keys(peer), *keys)
        if (panel := self.panel()) is not None:
            groups = [g for g in panel.groups if isinstance(g, Group)]
            panel.set_about(peer.app if peer else "hill", self.line_commands(peer, groups))
            apps = (peer.app if peer else None, *(p.app for p in self.hub.stack))
            if apps != self.indexed_for:
                self.indexed_for = apps
                panel.set_index(self.search_index())

    def tick(self) -> None:
        """Show the time, and again on the next minute."""
        self.show_clock()
        self.set_timer(clock.to_next_minute(), self.tick)

    def show_clock(self) -> None:
        self.query_one("#strip", Keyline).set_right(clock.clock_text(self.pref(CLOCK)))

    def tip_keys(self, peer: Peer | None) -> list[Key]:
        """Claude's tip, or what it has to say, for the strip's line, which
        opens the panel. Its key is the one the app gave for Claude, if any;
        a click works too."""
        text = self.talk.tip.text if self.tip_on_line and self.talk.tip is not None else self.line_note
        if not text:
            return []
        key = next((k[0] for k in peer.keys if len(k) > 2 and k[2] == "hill.claude"), "") if peer else ""
        room = max(10, self.size.width - len(key) - 8)
        text = text if len(text) <= room else text[:room - 1] + "…"
        return [Key(key, f"✦ {text}", "app.remote('hill.claude')")]

    def record(self, event: str, app: str | None = None, **details: Any) -> None:
        """Log an event for `app`, else for the app that owns the strip."""
        if app is None and self.hub.active is not None:
            app = self.hub.active.app
        self.event_log.record(event, app, **details)

    def note(self, text: str) -> None:
        """Show a problem in the strip for a while."""
        self.record("problem", text=text)
        self.notes.append(text)
        self.show_keys()

        def forget() -> None:
            self.notes.remove(text)
            self.show_keys()

        self.set_timer(10, forget)

    def hello(self, peer: Peer) -> None:
        self.record("hello", peer.app)
        peer.tree, problems = check_tree(peer.tree, peer.commands)
        for problem in problems:
            self.note(f"{peer.app}'s key tree: {problem}")
        remember(AppDocs(peer.app, peer.keys, peer.commands, peer.help, peer.tree))
        if not self.tip_planned:
            self.tip_planned = True
            self.set_timer(TIP_AFTER, lambda: self.when_free(self.offer_tip))
        if peer.profile:
            try:
                register(peer.app, peer.profile)
            except (ProfileError, OSError) as e:
                self.note(f"{peer.app}: {e}")
        self.follow_theme()

    def bye(self, peer: Peer) -> None:
        self.record("bye", peer.app)
        self.follow_theme()

    def follow_theme(self) -> None:
        """Take the theme of the app that owns the strip, from its profile's
        theme setting, so the window is in one theme. An app with none, such
        as micro, leaves it to the last app to say hello that has one;
        Textual's own when no app has one."""
        peers = [self.hub.active, *reversed(self.hub.stack)] if self.hub.active else []
        themes = (app_theme(peer.app) for peer in peers)
        self.theme = next((t for t in themes if t in self.available_themes), DEFAULT_THEME)

    def report(self, peer: Peer, method: str, params: dict) -> None:
        """An app's error, for the log only: the app shows it itself."""
        if method == "error":
            self.record("error", peer.app, what=str(params.get("what") or "")[:ERROR_LENGTH])

    def action_remote(self, action: str) -> None:
        """A hint was clicked. hill-ops runs its own (hill.settings, hill.help,
        hill.command, hill.tree, hill.claude); the app runs the rest."""
        peer = self.hub.active
        self.record("run", action=action)
        if action == "hill.settings":
            self.open_panel()
        elif action == "hill.help":
            self.open_help(peer)
        elif action == "hill.command":
            self.open_command()
        elif action == TREE_ACTION:
            if peer is not None:
                self.open_tree(peer)
        elif action == "hill.claude":
            self.action_claude()
        elif peer is not None:
            self.hub.reply(peer, "run", action=action)

    # -- growing and shrinking --

    def request(self, peer: Peer, method: str, params: dict) -> None:
        """An app asked for something; it waits if the strip is busy."""
        if method == "settings.open":
            self.when_free(lambda: self.open_panel(params.get("group")))
        elif method == "help.open":
            self.when_free(lambda: self.open_help(peer, params.get("section")))
        elif method == "command.open":
            self.when_free(lambda: self.open_command(str(params.get("text") or "")))
        elif method == "tree.open":
            self.when_free(lambda: self.open_tree(peer))
        elif method == "ask":
            self.when_free(lambda: self.ask(peer, params.get("id"), str(params.get("question") or ""), str(params.get("value") or "")))
        elif method == "claude.open":
            self.when_free(lambda: self.open_panel(params.get("group"), ask_first=True))
        elif method == "panes":
            self.set_side(peer, params)
        elif method == "panes.end":
            self.end_side(peer, params)
        elif method == "over":
            self.open_over(peer, params)
        elif method == "overview":
            self.set_overview(peer, params)

    @property
    def busy(self) -> bool:
        """Whether the strip holds something, other than the panel, which
        the rest opens over."""
        return self.showing is not None or (
            isinstance(self.screen, STRIP_SCREENS) and not isinstance(self.screen, PanelScreen)
        )

    def when_free(self, open_it: Callable[[], None]) -> None:
        if self.busy:
            self.waiting.append(open_it)
        else:
            open_it()

    def grow(self, rows: int, show: Callable[[], None]) -> None:
        """Grow the strip to `rows`, take the focus, then `show` what it holds."""
        self.showing = show
        self.keep_focus()
        self.resize_pane(rows)
        self.tmux.run("select-pane", "-t", self.pane)
        if self.size.height == rows:
            self.show_now()  # already that tall: no resize will come
        else:
            self.set_timer(0.5, self.show_now)  # in case the resize never comes

    def on_resize(self, event: events.Resize) -> None:
        if self.showing is not None and (event.size.height > STRIP_ROWS or event.size.height == self.rows):
            self.show_now()

    def show_now(self) -> None:
        show, self.showing = self.showing, None
        if show is not None:
            show()
            self.keep_focus()

    def shrink(self, _result: Any = None) -> None:
        """Back to the strip's line, or to the panel if what closed was open
        over it; the focus back to the app; then the next request."""
        self.record("close")
        # A screen's callback runs once it has gone: what's left is under it.
        self.resize_pane(self.zoom_rows() if self.panel() else STRIP_ROWS)
        self.keep_focus()
        if self.layout is not None and self.back_to not in self.layout.shown():
            self.back_to = self.app_pane  # its pane has gone, such as a preview turned off
        if self.back_to:
            self.tmux.run("select-pane", "-t", self.back_to)
        if self.waiting:
            self.call_after_refresh(self.waiting.pop(0))

    def on_text_selected(self, _event: events.TextSelected) -> None:
        """Text selected with the mouse, such as Claude's answer, goes to
        the clipboard as the button is let go, as in palace's panes:
        Textual writes it for the terminal (OSC 52), which tmux passes on
        (set-clipboard)."""
        if text := self.screen.get_selected_text():
            self.copy_to_clipboard(text)

    def on_app_blur(self) -> None:
        # A click back in the app closes what the strip shows, but the
        # panel, which stays until Esc. The terminal losing the focus to
        # another window closes nothing: the strip is still tmux's pane.
        if isinstance(self.screen, PanelScreen) and not self.command_only:
            self.hover.left(self.screen)
        elif isinstance(self.screen, STRIP_SCREENS) and self.in_app():
            self.screen.dismiss(None)

    def watch_app_focus(self, focus: bool) -> None:
        # The CSS lights the panel's background (App:focus), and this its keys.
        for screen in self.screen_stack:
            screen.query(FOCUS_KEYS).set_class(focus, "-active")
        if isinstance(self.screen, PanelScreen):
            self.screen.focus_returned(focus)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """The focus follows the mouse into the panel, lazily, as it does
        into palace's panes (hill-client's Hover). Not into the strip's
        line, and what opens over the panel has the focus already."""
        panel = self.screen if isinstance(self.screen, PanelScreen) else None
        if self.hover.moved(event.screen_offset, panel, self.app_focus) and self.pane:
            self.tmux.run("select-pane", "-t", self.pane)
            self.app_focus = True  # tmux says so too, a moment later

    def keep_focus(self) -> None:
        """Tell the programs that take the focus as the mouse moves over
        them (hill-client's take_focus) whether the strip keeps it: while
        it grows, and while it holds the help, a question or the panel
        opened as the command line, which close when it goes; not for the
        panel alone."""
        keep = "1" if self.busy or (self.command_only and isinstance(self.screen, PanelScreen)) else "0"
        if keep != self.kept:
            self.kept = keep
            self.tmux.run("set", "-g", "@hill-keep", keep)

    def in_app(self) -> bool:
        """Whether another pane than the strip has the focus: the app's, or
        one beside it."""
        return bool(self.app_pane) and self.tmux.run("display", "-p", "-t", self.pane, "#{pane_active}") == "0"

    def resize_pane(self, rows: int) -> None:
        # The option makes the window-resized hook keep this height.
        self.rows = rows
        if self.layout is not None:
            self.layout.rows = rows
        self.tmux.run("set", "-g", "@hill-height", str(rows))
        self.tmux.run("resize-pane", "-t", self.pane, "-y", str(rows))

    def zoom_rows(self) -> int:
        """The height for the panel and help: their share of the window (Hill
        → Panel height), at least 9 rows, and leaving the app a few."""
        try:
            window = int(self.tmux.run("display", "-p", "#{window_height}"))
        except ValueError:
            window = self.size.height
        try:
            share = self.prefs.get(("panel_height",))
        except StoreError:
            share = None
        rows = max(9, round(window * PANEL_FRACTIONS.get(share, 1 / 3)))
        return max(STRIP_ROWS, min(rows, window - 4))

    # -- the window: the panes beside the app, and the focus --

    def apply_layout(self) -> None:
        """Size the panes as Layout's settings say: the app's width, and the
        Claude pane's, or none."""
        if self.layout is None:
            return
        self.layout.app_width = self.pref(APP_WIDTH)
        claude = self.pref(CLAUDE_WIDTH)
        self.layout.claude_shown = claude != "off"
        self.layout.claude_width = CLAUDE_WIDTH.default if claude == "off" else claude
        self.layout.arrange()

    def set_side(self, peer: Peer, params: dict) -> None:
        """An app asked to run programs beside it, or no longer to."""
        if self.layout is None:
            return
        view, claude = Side.parse(params.get("view")), Side.parse(params.get("claude"))
        names = {"view": view.name if view else None, "claude": claude.name if claude else None}
        if names != self.layout.running():
            self.record("panes", peer.app, **names)
        self.layout.set_side(view, claude)
        peer.side = self.layout.panes()
        self.show_keys()

    def end_side(self, peer: Peer, params: dict) -> None:
        """An app asked to stop some of the programs it runs beside it, out
        of sight or shown."""
        names = params.get("names")
        if self.layout is None or not isinstance(names, list):
            return
        names = [name for name in names if isinstance(name, str)]
        self.record("panes.end", peer.app, names=names)
        self.layout.end(set(names))
        peer.side = self.layout.panes()
        self.show_keys()

    def open_over(self, peer: Peer, params: dict) -> None:
        """An app asked to run a program over its side panes until it
        exits; then they come back, and the app hears its exit status."""
        side = Side.parse(params)
        if self.layout is None or side is None:
            return
        pane = self.layout.open_over(side, self.pane_colors())
        if pane is None:
            return  # one runs already
        self.record("over", peer.app, name=side.name)
        peer.side.add(pane)
        self.run_worker(self.wait_over(peer, pane), exit_on_error=False)

    def pane_colors(self) -> tuple[str, str]:
        """The background of a program over the side panes, as tmux styles,
        without the focus and with it: the panel's (FOCUSED)."""
        colors = self.get_css_variables()
        surface, text = Color.parse(colors["surface"]), Color.parse(colors["foreground"])
        return f"bg={Color.parse(colors['background']).hex}", f"bg={surface.tint(text.with_alpha(FOCUS_TINT)).hex}"

    async def wait_over(self, peer: Peer, pane: str) -> None:
        """Once the program over the side panes has exited (hill-ops's runner
        says so), put them back, give the focus to the app if the program
        had it, and tell the app its exit status."""
        await self.tmux.wait(over_channel(pane))
        status = self.tmux.run("display", "-p", "-t", pane, "#{@hill-status}")
        focused = self.hub.focused == pane
        if self.layout is not None and self.layout.over and self.layout.over[1] == pane:
            self.layout.close_over()
        peer.side.discard(pane)
        if focused and self.app_pane:
            self.tmux.run("select-pane", "-t", self.app_pane)
        code = int(status) if status.lstrip("-").isdigit() else None
        self.record("over.done", peer.app, status=code)
        self.hub.reply(peer, "over.done", status=code)

    async def watch(self) -> None:
        """Follow hill-ops's window: which pane has the focus, and each change
        of its layout."""
        self.window = self.tmux.run("display", "-p", "-t", SESSION, "#{window_id}")
        self.focus_changed(self.tmux.run("display", "-p", "-t", SESSION, "#{pane_id}"))
        size = self.tmux.run("display", "-p", "-t", SESSION, "#{window_width} #{window_height}").split()
        if len(size) == 2 and all(n.isdigit() for n in size):
            self.window_seen = self.size_logged = self.size_tipped = (int(size[0]), int(size[1]))
        async for name, args in self.tmux.notifications():
            if name == "%window-pane-changed" and args[:1] == [self.window] and len(args) > 1:
                self.focus_changed(args[1])
            elif name == "%layout-change" and args[:1] == [self.window] and len(args) > 1:
                self.layout_changed(args[1])
            elif name == "%exit":
                break

    def focus_changed(self, pane: str) -> None:
        """Another pane has the focus: the strip shows its app's keys. The
        strip's own pane counts as the one it took the focus from, whose
        app the panel and the help are about."""
        if not pane or pane == self.pane or pane == self.hub.focused:
            return
        self.hub.focused = self.back_to = pane
        self.show_keys()
        self.follow_theme()

    def layout_changed(self, layout: str) -> None:
        """The window's layout changed: its size, a border dragged, a pane
        gone. A new size applies to the panel at once; the rest is looked
        at once the changes stop (settle)."""
        size = window_size(layout)
        if size is not None and size != self.window_seen:
            known, self.window_seen = self.window_seen, size
            if known is not None:
                self.window_resized()
        if self.settle_timer is not None:
            self.settle_timer.stop()
        self.settle_timer = self.set_timer(SETTLE, self.settle)

    def window_resized(self) -> None:
        """The window changed size: the hook has sized the panes again; the
        panel or the help takes its share of the new height. Once the size
        lasts, the event log has it (window_settled)."""
        if isinstance(self.screen, (PanelScreen, HelpScreen)):
            self.resize_pane(self.zoom_rows())
        if self.resize_timer is not None:
            self.resize_timer.stop()
        self.resize_timer = self.set_timer(RESIZE_SETTLE, self.window_settled)

    def window_settled(self) -> None:
        """The window has kept its new size a while: it goes in the event
        log, and, if it changed a lot since hill-ops started or since the last
        such change, such as on another monitor, Claude offers a tip about
        the layout on the strip's line (Hill → Claude's tips: always)."""
        self.resize_timer = None
        size, before = self.window_seen, self.size_logged
        if size is None or size == before:
            return
        self.size_logged = size
        self.record("resize", cols=size[0], rows=size[1])
        start = self.size_tipped or before or size
        if all(abs(new - old) < RESIZE_CHANGE * old for new, old in zip(size, start)):
            return
        self.size_tipped = size
        now = asyncio.get_running_loop().time()
        if (self.resize_tip_at is not None and now - self.resize_tip_at < RESIZE_TIP_EVERY) or self.asking:
            return
        if self.pref(TIPS) == "once a session" and is_on() and self.hub.active is not None:
            self.resize_tip_at = now
            self.find_tip(
                f"unasked, on the strip's line, after the window changed size from {start[0]}×{start[1]} "
                f"to {size[0]}×{size[1]}; its panes now: {self.describe_window()}",
                unasked=True,
            )

    def describe_window(self) -> str:
        """The panes in the window and how big they are, in words, for Claude."""
        peer = self.hub.active
        names = self.layout.names(peer.app if peer else "the app") if self.layout is not None else {}
        parts = []
        for row in self.tmux.run("list-panes", "-t", SESSION, "-F", "#{pane_id} #{pane_width} #{pane_height}").splitlines():
            pane, width, height = (row.split() + ["", "", ""])[:3]
            parts.append(f"{names.get(pane, 'a pane')} {width}×{height}")
        return ", ".join(parts) or "unknown"

    def settle(self) -> None:
        """The layout has stopped changing: forget the side programs that
        exited on their own, then, once a dragged border is let go, take
        the widths it gave as settings."""
        self.settle_timer = None
        if self.layout is None:
            return
        panes = set(self.tmux.run("list-panes", "-a", "-F", "#{pane_id}").split())
        if not panes:
            return  # tmux didn't answer
        for name in self.layout.forget(panes):
            self.note(f"{name} has stopped")
        state = self.tmux.run("display", "-p", "-t", SESSION, "#{window_width} #{window_height} #{window_zoomed_flag} #{@hill-dragging}").split()
        if state[3:] == ["1"]:
            self.settle_timer = self.set_timer(SETTLE, self.settle)  # still dragging
        elif len(state) >= 3 and state[2] != "1" and state[0].isdigit() and state[1].isdigit():
            self.snap(int(state[0]), int(state[1]))

    def snap(self, width: int, height: int) -> None:
        """Take what a dragged border did as settings, snapped to their
        steps: the app's and the Claude pane's widths, and the panel's
        height, if it's open; then size the panes as the settings say. A
        strip on its line goes back to it."""
        assert self.layout is not None
        rows = self.tmux.run("list-panes", "-t", SESSION, "-F", "#{pane_id} #{pane_width} #{pane_height}").splitlines()
        sizes = {pane: (int(w), int(h)) for pane, w, h in (row.split() for row in rows if len(row.split()) == 3)}
        changes = self.layout.dragged(sizes, width)
        grown = isinstance(self.screen, (PanelScreen, HelpScreen))
        strip = sizes.get(self.pane, (0, self.rows))[1]
        if grown and strip != self.rows and height > 0:
            fraction = min(PANEL_FRACTIONS, key=lambda f: abs(PANEL_FRACTIONS[f] - strip / height))
            changes.append((PANEL_HEIGHT, fraction))
        for setting, value in changes:
            if value == self.pref(setting):
                continue
            try:
                self.prefs.set(setting.key, value, setting.default)
            except (StoreError, OSError) as e:
                self.note(f"Not saved: {e}")
                continue
            self.record("settings.changed", "hill", group="layout", key=list(setting.key), value=value, by="drag")
            if (panel := self.panel()) is not None:
                panel.run_worker(panel.show_setting("layout", setting.key), exclusive=True)
        if strip != self.rows:
            self.resize_pane(self.zoom_rows() if grown else self.rows)
        self.apply_layout()

    # -- what the strip holds --

    def open_panel(self, group: str | None = None, ask_first: bool = False, command: str | None = None) -> None:
        """The panel: the settings of every known app, open on `group`, with
        the line for commands and Claude under them; `ask_first` puts the
        focus on that line, and `command` types ":" and it there, for the
        app's command line, which closes the panel once it has run. If it's
        open, it gets the focus: on the line, or on the settings, on
        `group`."""
        if (panel := self.panel()) is not None:
            self.tmux.run("select-pane", "-t", self.pane)
            if command is not None:
                line = panel.query_one("#input", Input)
                line.value = f":{command}"
                line.cursor_position = len(line.value)
            if ask_first or command is not None:
                panel.focus_line()
            else:
                panel.focus_settings(group)
            return
        if command is not None:
            self.record("command.open")
        elif ask_first:
            self.record("claude.open", about=None, group=group)
        else:
            self.record("settings.open", group=group)
        self.tip_on_line, self.line_note = False, None  # it's in the panel
        self.show_tip()

        def show() -> None:
            peer = self.hub.active
            groups, notes = settings_groups(
                self.prefs, peer.app if peer else None, lambda: sorted(self.available_themes), self.setting_changed,
            )
            for text in notes:
                self.note(text)
            overviews = [o for p in self.hub.stack if (o := overview_listing(p, self.overview_picked)) is not None]
            panel = PanelScreen(
                [*overviews, *groups], self.prefs, self.talk, peer.app if peer else "hill", self.ask_question, self.take_offer,
                start=group, ask_first=ask_first or command is not None, commands=self.line_commands(peer, groups),
                run=self.run_command, text=f":{command}" if command is not None else "",
                index=self.search_index(), go=self.take_found,
            )
            self.command_only = command is not None
            self.indexed_for = (peer.app if peer else None, *(p.app for p in self.hub.stack))
            self.push_screen(panel, self.panel_closed)
            self.serve_tip(panel)

        self.grow(self.zoom_rows(), show)

    def panel_closed(self, _result: Any = None) -> None:
        """The panel closed: your question and its answer go with it, unless
        the answer is still coming, and the tip is what Claude said last."""
        if not any(said.waiting for said in self.talk.said):
            self.talk.said, self.talk.last = [], self.talk.tip
        self.command_only = False
        self.shrink()

    def set_overview(self, peer: Peer, params: dict) -> None:
        """An app sent its overview: the panel shows it as its first tab,
        from the next time it opens, and at once if it shows it already."""
        peer.overview = params
        if (panel := self.panel()) is not None:
            panel.show_entries(f"{peer.app}:overview", overview_entries(params))

    def overview_picked(self, peer: Peer, entry: Entry) -> None:
        """A line of an app's overview was picked: the app hears its id,
        the log only that one was."""
        self.record("overview.pick", peer.app)
        self.hub.reply(peer, "overview.pick", id=entry.id)

    def open_help(self, peer: Peer | None, section: str | None = None, closed: Callable[[Any], None] | None = None) -> None:
        """The app's help, open on `section`; `closed` hears when it closes,
        else the strip shrinks."""
        self.record("help.open", peer.app if peer else None, section=section)
        self.grow(self.zoom_rows(), lambda: self.push_screen(HelpScreen(help_sections(peer), section), closed or self.shrink))

    def open_command(self, text: str = "") -> None:
        """The app's command line: the panel, on its line, with ":" and
        `text` typed."""
        self.open_panel(command=text)

    def open_tree(self, peer: Peer) -> None:
        """The app's key tree; the command picked in it goes to the app, as
        one entered on the command line does. An app with no tree hears
        nothing, and the strip says so."""
        if not peer.tree:
            self.note(f"{peer.app} has no key tree")
            return
        self.record("tree.open", peer.app)

        def picked(line: str | None) -> None:
            if line is not None:
                self.record("tree", peer.app, **command_words(line, peer.commands))
                self.hub.reply(peer, "command", line=line)
            self.shrink()

        rows = min(self.zoom_rows(), TREE_ROWS)
        self.grow(rows, lambda: self.push_screen(TreeScreen(peer.app, peer.tree, opener(peer.keys)), picked))

    def line_commands(self, peer: Peer | None, groups: list[Group]) -> list[list]:
        """The commands the panel's line runs: the app's, each with its keys
        in the app's key tree, then hill-ops's `:set` and `:toggle`."""
        if peer is None:
            return set_commands(groups)
        keys = sequences(peer.tree, opener(peer.keys))
        own = [[*(list(c) + ["", "", []])[:4], keys[c[0]]] if c[0] in keys else c for c in peer.commands]
        return [*own, *set_commands(groups)]

    def search_index(self) -> list[Found]:
        """What the panel's line searches: every known app's settings and
        hill-ops's, and the commands, keys and help of the apps on the channel,
        or as they last said hello."""
        peer = self.hub.active
        groups, _ = app_groups(self.prefs, peer.app if peer else None, lambda: sorted(self.available_themes), self.setting_changed)
        return build(groups, self.known_apps(), STRIP_HELP)

    def known_apps(self) -> list[AppDocs]:
        """The keys, commands and help of every app hill-ops knows: those on
        the channel, and the others as they last said hello."""
        running = {p.app: AppDocs(p.app, p.keys, p.commands, p.help, p.tree) for p in self.hub.stack if p.app}
        return [*running.values(), *(docs for docs in remembered() if docs.app not in running)]

    def projects(self) -> list[Project]:
        """Where Claude can file work items: hill-ops's own work folder, and
        each known app's whose profile names one."""
        profiles, _ = known()
        found = [Project(p.app, p.work) for p in profiles if p.work is not None and p.work.is_dir()]
        return _one_per_folder([*([own] if (own := hill_project()) is not None else []), *found])

    def take_found(self, found: Found) -> None:
        """A match on the panel's line, taken: a setting shows selected in
        the panel; a command is typed on the line, if it's the app's on the
        strip; a hint runs, as a click on it would; a key or a section of
        the help opens the help there. The log has only what kind it was."""
        self.record("search", found.app, kind=found.kind)
        panel = self.panel()
        peer = self.hub.active
        on_strip = found.app == (peer.app if peer else "hill")
        running = next((p for p in reversed(self.hub.stack) if p.app == found.app), None)
        if panel is None:
            return
        if found.kind == "setting":
            group, key = found.target
            panel.focus_settings()
            panel.run_worker(panel.show_setting(group, key, saved=False), exclusive=True)
        elif found.kind == "command" and on_strip:
            line = panel.query_one("#input", Input)
            line.value = f":{found.target} "
            line.cursor_position = len(line.value)
        elif found.kind == "command":
            self.talk.said = [Said("hill", f"{found.name} is {found.app}'s: run it from {found.app}")]
            self.show_talk()
        elif found.kind == "key" and found.where == "strip":
            if on_strip or str(found.target).startswith("hill."):
                self.action_remote(str(found.target))
            else:
                self.talk.said = [Said("hill", f"{found.name} is {found.app}'s key: {found.what}")]
                self.show_talk()
        else:
            docs = running or next((d for d in remembered() if d.app == found.app), None)
            self.open_help(docs if found.app != "hill" else peer, str(found.target), self.back_to_panel)

    def set_setting(self, line: str) -> None:
        """hill-ops's `:set` or `:toggle`, its `line` without ":": change the
        setting named, as the panel would, and show it there."""
        panel = self.panel()
        groups = [g for g in panel.groups if isinstance(g, Group)] if panel is not None else self.claude_groups(self.hub.active)
        found = find_setting(line, groups)
        if isinstance(found, str):
            self.talk.said = [Said("hill", found)]
            self.show_talk()
            return
        group, setting, value = found
        problem = self.change(group, setting, value)
        self.talk.said = [Said("hill", problem or f"{group.name} → {setting.label} is now {setting.show(value)}")]
        self.show_talk()
        if panel is not None:
            panel.run_worker(panel.show_setting(group.id, setting.key, problem), exclusive=True)

    def run_command(self, line: str, offered: bool = False) -> None:
        """A command for the app on the strip, typed on the panel's line
        after ":", or `offered` by Claude. The panel closes if it opened as
        the app's command line; else it says what ran. `:set` and `:toggle`
        are hill-ops's own."""
        peer = self.hub.active
        if line.split(" ", 1)[0] in ("set", "toggle"):
            panel = self.panel()
            groups = [g for g in panel.groups if isinstance(g, Group)] if panel is not None else []
            self.record("command", "hill", **command_words(line, set_commands(groups)))
            self.command_only = False  # it says what it changed
            self.set_setting(line)
            return
        if peer is None:
            return
        self.record("command", peer.app, **command_words(line, peer.commands))
        self.hub.reply(peer, "command", line=line)
        panel = self.panel()
        if panel is not None and self.command_only and not offered:
            if self.screen is panel:
                panel.dismiss(None)
            return
        self.talk.said.append(Said("hill", f"ran :{line}"))
        self.show_talk()

    def ask(self, peer: Peer, id: Any, question: str, value: str = "") -> None:
        """A question on the strip's line; the answer, or None, goes back to the app."""
        self.record("ask", peer.app)  # not the question, which can hold a note's title

        def answered(answer: str | None) -> None:
            self.record("answer", peer.app, answered=answer is not None)
            self.hub.reply(peer, "answer", id=id, value=answer)
            self.shrink()

        self.grow(STRIP_ROWS, lambda: self.push_screen(QuestionScreen(question, value), answered))

    def setting_changed(self, app: str, group: Group, setting: Setting, value: Any) -> None:
        if app == "hill":
            self.hill_setting_changed(group, setting, value)
            return
        self.record("settings.changed", app, group=group.id, key=list(setting.key), value=value)
        self.follow_theme()
        self.hub.send(app, "settings.changed", group=group.id, key=list(setting.key), value=value)

    def hill_setting_changed(self, group: Group, setting: Setting, value: Any) -> None:
        self.record("settings.changed", "hill", group=group.id, key=list(setting.key), value=value)
        if setting.key == ("panel_height",) and isinstance(self.screen, PanelScreen):
            self.resize_pane(self.zoom_rows())
        elif setting.key in (APP_WIDTH.key, CLAUDE_WIDTH.key):
            self.apply_layout()
        elif setting.key == MODEL.key and self.agent is not None:
            agent, self.agent = self.agent, None  # the next tip or question starts Claude with the new model
            self.run_worker(agent.stop())
        elif setting.key in (TIPS.key, EVENT_LOG.key):
            self.show_tip()
        elif setting.key == CLOCK.key:
            self.show_clock()

    # -- Claude --

    def pref(self, setting: Setting) -> Any:
        """One of hill-ops's own settings, or its default."""
        try:
            value = self.prefs.get(setting.key)
        except StoreError:
            value = None
        return value if value in setting.options() else setting.default

    def claude(self) -> Agent:
        """Claude, over ACP: started for the first tip or question, and kept
        for the session, so it remembers what was said."""
        if self.agent is None:
            self.agent = Agent(command(), Path.cwd(), meta(self.pref(MODEL)))
        return self.agent

    def claude_groups(self, peer: Peer | None) -> list[Group]:
        """The settings, as Claude reads them and changes them."""
        groups, _ = settings_groups(
            self.prefs, peer.app if peer else None, lambda: sorted(self.available_themes), self.setting_changed,
        )
        return groups

    def panel(self) -> PanelScreen | None:
        """The panel, if it's open, maybe under something else."""
        return next((s for s in self.screen_stack if isinstance(s, PanelScreen)), None)

    def action_claude(self) -> None:
        """Alt-c in the strip, or a click on a tip. In the panel, another
        tip; in the help, a tip about what's shown; on the strip's line, the
        panel, on the line to ask Claude."""
        screen = self.screen
        if isinstance(screen, PanelScreen):
            self.ask_tip(f"asked in the panel, on {_shown(screen)}", "settings")
        elif isinstance(screen, HelpScreen):
            self.ask_tip(f"asked in the help, on {screen.section}", "help", screen)
        elif isinstance(screen, STRIP_SCREENS):
            return  # typing on the strip's line
        else:
            self.open_panel(ask_first=True)

    # -- your questions --

    def ask_question(self, question: str) -> None:
        """A question typed in the panel: it goes to Claude, and only the
        fact that you asked goes in the log. It and its answer replace the
        last ones."""
        self.record("claude.ask")
        self.command_only = False
        self.keep_focus()
        said = Said("claude", "", waiting=True)
        self.talk.said = [Said("you", question), said]
        self.show_talk()
        self.run_worker(self.fetch_answer(question, said), exit_on_error=False)

    async def fetch_answer(self, question: str, said: Said) -> None:
        """Claude's answer, shown as it comes; once it's all there, what it
        offers. If the panel has closed meanwhile, the strip's line says so."""
        peer = self.hub.active
        groups = self.claude_groups(peer)
        projects = self.projects()

        def prompt(fresh: bool) -> str:
            return question_prompt(question, self.context.news(
                peer, groups, self.event_log.path, fresh, self.known_apps(), projects,
            ))

        def grew(text: str) -> None:
            said.text = shown(text)
            self.show_talk()

        try:
            reply = await asyncio.wait_for(self.claude().ask(prompt, grew), ANSWER_WAIT)
        except (AgentError, TimeoutError) as e:
            problem = str(e) if isinstance(e, AgentError) else "Claude took too long to answer"
            self.record("problem", text=problem)
            said.who, said.text, said.waiting = "hill", problem, False
        else:
            answer = parse_answer(
                reply, groups, [title for title, _ in help_sections(peer)], peer.commands if peer else None, projects,
            )
            said.text, said.answer, said.waiting = answer.text or "No answer from Claude just now", answer, False
            if said in self.talk.said:
                self.talk.last = answer
        if self.panel() is None:
            self.say_on_line("Claude has answered" if said.who == "claude" else said.text, seconds=TIP_SHOWN)
        self.show_talk()

    def show_talk(self) -> None:
        """Show your question and its answer as they are now, if the panel
        is in front; else it shows them when it's back."""
        if isinstance(self.screen, PanelScreen):
            self.screen.show_talk()

    def take_offer(self, answer: Answer | None) -> None:
        """Take what Claude offers with a tip or an answer, once: Ret on an
        empty line, or a click on the tip's offer. A setting changes as in
        the settings, and the panel shows it; a command goes to the app, as
        one typed after ":"; a section of the help opens over the panel, and
        Esc goes back to it. A setting to show opens selected in the panel,
        unchanged; a work item is filed, and the panel says where."""
        if answer is None or answer.offer is None:
            return
        self.record("claude.take")
        changing, section, command, showing, draft = answer.setting, answer.help, answer.command, answer.show, answer.work
        answer.setting = answer.help = answer.command = answer.show = answer.work = None
        if self.talk.last is answer:
            self.talk.last = None
        if draft is not None:
            peer = self.hub.active
            try:
                path = file_item(draft.project, draft.title, draft.goal, draft.done_when, peer.app if peer else None)
            except OSError as e:
                said = f"Not filed: {e}"
                self.record("problem", text=said)
            else:
                self.record("work.filed", project=draft.project.name)
                said = f"Filed {draft.project.name} {path.name[:3]}, {draft.title}: {path}, not committed"
            self.talk.said.append(Said("hill", said))
            self.show_talk()
            self.show_tip()
        elif showing is not None:
            group, setting = showing
            if (panel := self.panel()) is not None:
                panel.focus_settings()
                panel.run_worker(panel.show_setting(group.id, setting.key, saved=False), exclusive=True)
            self.show_tip()
        elif command is not None:
            self.run_command(command, offered=True)
            self.show_tip()
        elif changing is not None:
            group, setting, value = changing
            problem = self.change(group, setting, value)
            if any(said.answer is answer for said in self.talk.said):
                self.talk.said.append(Said("hill", problem or f"{group.name} → {setting.label} is now {setting.show(value)}"))
            if isinstance(self.screen, PanelScreen):
                self.screen.run_worker(self.screen.show_setting(group.id, setting.key, problem), exclusive=True)
            self.show_talk()
            self.show_tip()
        else:
            self.show_tip()
            self.open_help(self.hub.active, section, self.back_to_panel)

    def back_to_panel(self, _result: Any = None) -> None:
        """Back to the panel from the help Claude offered, the focus still in
        the strip; then the next request."""
        self.record("close")
        self.resize_pane(self.zoom_rows())
        self.keep_focus()
        if self.waiting:
            self.call_after_refresh(self.waiting.pop(0))

    def change(self, group: Group, setting: Setting, value: Any) -> str | None:
        """Change a setting as the settings panel would: save it, then tell
        whoever it belongs to. What went wrong, if it couldn't be saved."""
        try:
            group.store.set(setting.key, value, setting.default)
        except (StoreError, OSError) as e:
            self.record("problem", text=f"Not saved: {e}")
            return f"Not saved: {e}"
        if group.on_change is not None:
            group.on_change(setting, value)
        return None

    # -- Claude's tips --

    def tips_say(self) -> str | None:
        """Why there's no tip, when tips are off or can't come, or None."""
        if self.pref(TIPS) == "off":
            return "Claude's tips are off: Hill → Claude's tips"
        if not is_on():
            return "Claude's tips read the event log, which is off: Hill → Event log"
        return None

    def show_tip(self) -> None:
        """Put the tip, or what the tip line says instead, on the panel's tip
        line; and on the strip's line, if it shows there."""
        said = self.tips_say()
        if said is not None and (self.talk.tip is None or self.pref(TIPS) == "off"):
            note = said  # a tip that came before the event log was turned off stays
        elif self.asking:
            note = "Claude is looking for a tip…"
        elif self.talk.tip is None:
            note = self.tip_problem or "A tip from Claude: Alt-c, or a click here"
        else:
            note = None
        self.talk.note = note
        if (panel := self.panel()) is not None:
            panel.show_tip()
        self.show_keys()

    def serve_tip(self, panel: PanelScreen) -> None:
        """A tip for the panel that has none, if Hill → Claude's tips says
        so: it stays until another is asked for."""
        if (self.talk.tip is None and self.tip_problem is None and not self.asking
                and self.pref(TIPS) == "once a session" and is_on()):
            self.find_tip(f"for the panel as it opened, on {_shown(panel)}")

    def offer_tip(self) -> None:
        """The tip offered once a session on the strip's line, if Hill →
        Claude's tips says so and there's none yet."""
        if (self.pref(TIPS) == "once a session" and is_on() and self.talk.tip is None and not self.asking
                and self.hub.active is not None):
            self.find_tip(None, unasked=True)

    def ask_tip(self, why: str, about: str, help: HelpScreen | None = None) -> None:
        """Alt-c, or a click on the tip line: another tip, about what's shown
        in the panel, or in `help`, which shows it under its section."""
        if (said := self.tips_say()) is not None:
            self.talk.note = said  # until the line changes
            if help is not None:
                help.show_note(f"✦ {said}")
            elif (panel := self.panel()) is not None:
                panel.show_tip()
        elif not self.asking:
            self.record("claude.open", about=about)
            self.find_tip(why, help)

    def find_tip(self, why: str | None, help: HelpScreen | None = None, unasked: bool = False) -> None:
        """Ask Claude for a tip; `why` says why, None for the one offered
        once a session. An `unasked` tip, that one or one after the window
        changed size, goes on the strip's line while the panel is closed."""
        self.asking = True
        self.tip_problem = None
        if help is not None:
            help.show_note("✦ Claude is looking…")
        self.show_tip()
        self.run_worker(self.fetch_tip(why, help, unasked), exit_on_error=False)

    async def fetch_tip(self, why: str | None, help: HelpScreen | None = None, unasked: bool = False) -> None:
        """Claude's tip, for the tip line, and under the section of `help`
        if it was asked there, or on the strip's line if it's `unasked`;
        what went wrong instead."""
        said = None
        try:
            peer = self.hub.active
            groups = self.claude_groups(peer)

            def prompt(fresh: bool) -> str:
                return tip_prompt(why, self.context.news(
                    peer, groups, self.event_log.path, fresh, self.known_apps(), self.projects(),
                ))

            try:
                reply = await asyncio.wait_for(self.claude().ask(prompt), TIP_WAIT)
            except (AgentError, TimeoutError) as e:
                said = self.tip_problem = str(e) if isinstance(e, AgentError) else "Claude took too long for a tip"
                if unasked and self.panel() is None:
                    self.note(said)  # the strip's line
                else:
                    self.record("problem", text=said)
                return
            tip = parse_tip(reply, groups, [title for title, _ in help_sections(peer)], peer.commands if peer else None)
            if tip is None:
                if not unasked:  # an unasked one can come again when the panel opens
                    said = self.tip_problem = "No tip from Claude just now"
                return
            self.record("tip", text=tip.text, setting=_where(tip), command=tip.command, help=tip.help)
            self.talk.tip = self.talk.last = tip
            said = tip.text
            if unasked and self.panel() is None:
                self.put_tip_on_line()
        finally:
            self.asking = False
            if help is not None and self.screen is help:
                help.show_note(f"✦ {said}" if said else "")
            self.show_tip()

    def say_on_line(self, text: str | None, seconds: float = 0) -> None:
        """Say something from Claude on the strip's line, for `seconds` if
        given."""
        self.line_note = text
        self.show_keys()
        if seconds:
            self.set_timer(seconds, lambda: self.say_on_line(None) if self.line_note == text else None)

    def put_tip_on_line(self) -> None:
        """Put the tip on the strip's line for TIP_SHOWN seconds."""
        if self.tip_timer is not None:
            self.tip_timer.stop()
        tip, self.tip_on_line, self.line_note = self.talk.tip, True, None

        def done() -> None:
            if self.talk.tip is tip:
                self.tip_on_line = False
                self.show_keys()

        self.tip_timer = self.set_timer(TIP_SHOWN, done)
        self.show_keys()


def _shown(panel: SettingsScreen) -> str:
    """What the panel shows, for Claude: a setting, or a list's tab."""
    if isinstance(panel.group, Listing):
        return f"the {panel.group.name} tab"
    return f"{panel.group.name} → {panel.setting.label}"


def _where(tip: Answer) -> dict | None:
    """A tip's setting, for the log."""
    if tip.setting is None:
        return None
    group, setting, value = tip.setting
    return {"group": group.id, "key": list(setting.key), "value": value}


class SettingsApp(App):
    """The settings on their own, filling the terminal: `hill-ops settings [GROUP]`."""

    def __init__(self, start: str | None = None) -> None:
        super().__init__()
        self.start = start
        self.prefs = hill_prefs()

    def get_default_screen(self) -> Screen:
        return Screen()

    def on_mount(self) -> None:
        groups, notes = settings_groups(self.prefs, None, lambda: sorted(self.available_themes), lambda *_: None)
        for text in notes:
            self.notify(text, severity="warning")
        screen = SettingsScreen(groups, self.prefs, full=True, close_keys=("comma", "q"), start=self.start)
        self.push_screen(screen, lambda _: self.exit())
