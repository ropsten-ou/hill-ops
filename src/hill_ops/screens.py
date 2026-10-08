"""What the strip shows besides its hints: the panel, which holds the
settings with one line under them for the app's commands and for Claude, a
question from the app, the app's help, and its key tree."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.table import Table
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from keyline import Key, Keyline
from settings_panel import Group, JsonStore, SettingsScreen

from .index import Found, search

if TYPE_CHECKING:
    from .claude import Answer

LINE_CSS = """
    #line { height: 1; }
    #prompt { width: auto; padding: 0 1; color: $text-muted; }
    #line Input, #line Input:focus { border: none; height: 1; padding: 0; background: $panel; }
"""


class QuestionScreen(Screen[str | None]):
    """A question from the app, answered on the strip's line: Ret answers,
    Esc cancels."""

    DEFAULT_CSS = "QuestionScreen { background: $panel; }" + LINE_CSS
    BINDINGS = [Binding("escape", "close", priority=True)]

    def __init__(self, question: str, value: str = "") -> None:
        super().__init__()
        self.question = question
        self.value = value

    def compose(self) -> ComposeResult:
        with Horizontal(id="line"):
            yield Static(self.question, id="prompt")
            yield Input(self.value, id="input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(Screen[None]):
    """An app's help, a tab per section: its keys, part by part, then its
    commands and the strip's own keys. `sections` are (title, [(key, what)])."""

    DEFAULT_CSS = """
    HelpScreen { background: $surface; }
    HelpScreen #tabs { height: 1; background: $panel; }
    HelpScreen .tab { width: auto; padding: 0 1; }
    HelpScreen .tab.-current { background: $accent; color: $text; text-style: bold; }
    HelpScreen #body { height: 1fr; padding: 0 1; }
    HelpScreen #note { display: none; padding: 0 1; color: $text-muted; }
    HelpScreen #note.-shown { display: block; }
    """
    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("right,tab", "section(1)", priority=True),
        Binding("left,shift+tab", "section(-1)", priority=True),
        *[Binding(str(n), f"show({n - 1})", priority=True) for n in range(1, 10)],
    ]

    def __init__(self, sections: list[tuple[str, list[tuple[str, str]]]], start: str | None = None) -> None:
        super().__init__()
        self.sections = sections or [("Help", [])]
        titles = [title.casefold() for title, _ in self.sections]
        self.current = titles.index(start.casefold()) if start and start.casefold() in titles else 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="tabs"):
            for i, (title, _) in enumerate(self.sections):
                yield _Tab(i, title)
        with VerticalScroll(id="body"):
            yield Static(id="entries")
        yield Static(id="note")
        yield Keyline(
            Key("←→", "section", "section(1)"), Key("↑↓", "scroll"), Key("Esc", "close", "close"),
            id="help-keys", classes="-active",
        )

    def on_mount(self) -> None:
        self.action_show(self.current)
        self.query_one("#body").focus()

    def action_show(self, index: int) -> None:
        if not 0 <= index < len(self.sections):
            return
        self.current = index
        for tab in self.query(_Tab):
            tab.set_class(tab.index == index, "-current")
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for key, what in self.sections[index][1]:
            table.add_row(key, what)
        self.query_one("#entries", Static).update(table)
        self.query_one("#body", VerticalScroll).scroll_home(animate=False)

    def action_section(self, delta: int) -> None:
        self.action_show((self.current + delta) % len(self.sections))

    @property
    def section(self) -> str:
        """The title of the section shown."""
        return self.sections[self.current][0]

    def show_note(self, text: str) -> None:
        """A line under the section, such as a tip from Claude."""
        note = self.query_one("#note", Static)
        note.update(text)
        note.set_class(bool(text), "-shown")

    def action_close(self) -> None:
        self.dismiss(None)


class _Tab(Static):
    def __init__(self, index: int, title: str) -> None:
        super().__init__(Text.assemble((str(index + 1), "bold"), f" {title}"), classes="tab")
        self.index = index

    def on_click(self) -> None:
        self.screen.action_show(self.index)


class TreeScreen(Screen[str | None]):
    """An app's key tree (keytree): a level's entries, each with the key that
    picks it, and above them the keys pressed so far. A key goes down a
    group, or picks a command, which the screen gives back as its line;
    Backspace goes up, Esc closes, a click on an entry is its key."""

    DEFAULT_CSS = """
    TreeScreen { background: $surface; }
    TreeScreen #path { height: 1; background: $panel; padding: 0 1; }
    TreeScreen #entries { layout: grid; grid-size: 3; grid-gutter: 0 2; height: 1fr; padding: 0 1; }
    TreeScreen .entry { height: 1; }
    TreeScreen .entry:hover { background: $boost; }
    """
    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("backspace", "up", priority=True),
    ]
    WIDTH = 26
    """Cells for an entry; the entries fill as many columns as fit."""

    def __init__(self, app: str, tree: list[list], first: str | None = None) -> None:
        super().__init__()
        self.about = app
        self.nodes = tree
        self.first = first
        """The key that opened the tree, shown first in the path."""
        self.pressed: list[str] = []
        """The keys pressed so far."""

    def compose(self) -> ComposeResult:
        yield Static(id="path")
        yield Horizontal(id="entries")
        yield Keyline(
            Key("Bksp", "up", "up"), Key("Esc", "close", "close"),
            id="tree-keys", classes="-active",
        )

    def on_mount(self) -> None:
        self.show()

    def on_resize(self, _event: events.Resize) -> None:
        self.query_one("#entries").styles.grid_size_columns = max(1, (self.size.width - 2) // (self.WIDTH + 2))

    @property
    def level(self) -> list[list]:
        """The entries at the keys pressed so far."""
        nodes = self.nodes
        for key in self.pressed:
            nodes = next(n[2] for n in nodes if n[0] == key)
        return nodes

    def show(self) -> None:
        keys = " ".join([*([self.first] if self.first else []), *self.pressed])
        self.query_one("#path", Static).update(Text.assemble((self.about, "italic"), "  ", (keys, "bold")))
        entries = self.query_one("#entries")
        entries.remove_children()
        entries.mount_all(_Entry(key, label, isinstance(below, list)) for key, label, below in self.level)

    def press(self, key: str) -> bool:
        """The entry with this key: down its group, or its command picked."""
        node = next((n for n in self.level if n[0] == key), None)
        if node is None:
            return False
        if isinstance(node[2], str):
            self.dismiss(node[2])
        else:
            self.pressed.append(key)
            self.show()
        return True

    def on_key(self, event: events.Key) -> None:
        if self.press(event.character or event.key):
            event.stop()
            event.prevent_default()

    def action_up(self) -> None:
        if self.pressed:
            self.pressed.pop()
            self.show()

    def action_close(self) -> None:
        self.dismiss(None)


class _Entry(Static):
    def __init__(self, key: str, label: str, group: bool) -> None:
        super().__init__(Text.assemble((key, "bold"), "  ", ("+" if group else "", "bold"), label,
                                       no_wrap=True, overflow="ellipsis"), classes="entry")
        self.key = key

    def on_click(self) -> None:
        self.screen.press(self.key)


@dataclass(eq=False)
class Said:
    """Something said under the panel's settings, by "you", "claude" (an
    answer) or "hill" (hill-ops itself: what it did for you, or what went
    wrong). `answer` is
    Claude's, with what it offers; `waiting` is true while the answer is
    still coming."""

    who: str
    text: str
    answer: Answer | None = None
    waiting: bool = False


MARKS = {"you": ("›", "bold"), "claude": ("✦", ""), "hill": ("·", "dim")}
"""How each part of what's said shows: its mark and its style."""


@dataclass(eq=False)
class Talk:
    """Claude's part of the panel, which the strip fills and the panel
    shows: your latest question and what was said after it, and the tip
    line's tip, or what that line says instead, such as that Claude is
    looking for one. `last` is what Claude said last, the tip or an answer,
    whose offer Ret on an empty line takes."""

    said: list[Said] = field(default_factory=list)
    tip: Answer | None = None
    note: str | None = None
    last: Answer | None = None

    def offer(self) -> str | None:
        """What Ret on an empty line takes, in words, if anything."""
        return self.last.offer if self.last is not None else None


class PanelScreen(SettingsScreen):
    """The strip's panel: the settings of every known app, with Claude under
    them. The tip line holds Claude's tip, and a click on it, or Alt-c
    (app.claude), asks for another; a click on its offer takes it. Under it
    is one line for the app's commands and for Claude, and above it your
    latest question and Claude's answer. On the line, words search: what
    holds them all shows above the line, in the place of the answer, and ↓
    or Tab, then ↑↓, pick one, which Ret takes; Ret with none picked asks
    Claude. What starts with ":" is a command: the app's commands show above the line, narrowed as you type,
    Tab completes the highlighted one (and, after a command's name, its
    argument's choices), ↑↓ pick one, and Ret runs the line. Anything else
    Ret asks Claude, and on an empty line it takes what Claude said last
    offers. ↓ from the last row of settings goes to the line, ↑ back; PgUp
    PgDn scroll the answer; Esc closes.

    `talk` is shared with whoever fills it: call `show_talk()` or
    `show_tip()` once it has changed. `ask(text)` hears each question,
    `take(answer)` each offer taken, and `run(line)` each command, without
    its ":". `commands` are the app's, [name, args, what it does, choices
    for its first argument (optional)]. `index` is what the line searches,
    and `go(found)` hears each match taken. `ask_first` opens with the focus
    on the line, with `text` typed."""

    DEFAULT_CSS = """
    PanelScreen #talk { height: auto; padding: 0 1; background: $surface; }
    PanelScreen #talk.-empty, PanelScreen.-commanding #talk { display: none; }
    PanelScreen #matches { height: auto; border: none; padding: 0; background: $panel; display: none; }
    PanelScreen.-commanding #matches { display: block; }
    PanelScreen.-commanding #matches.-empty { display: none; }
    PanelScreen.-searching #talk { display: none; }
    PanelScreen.-searching #matches { display: block; }
    PanelScreen #line { background: $panel; }
    """ + LINE_CSS
    BINDINGS = [
        Binding("up", "up", priority=True),
        Binding("pageup", "page(-1)", priority=True),
        Binding("pagedown", "page(1)", priority=True),
    ]
    AUTO_FOCUS = ""
    """Nothing takes the focus on its own: the settings have it, unless
    `ask_first` gives it to the line."""

    def __init__(
        self,
        groups: list[Group],
        prefs: JsonStore,
        talk: Talk,
        app: str,
        ask: Callable[[str], None],
        take: Callable[[Answer | None], None],
        start: str | None = None,
        ask_first: bool = False,
        commands: list[list] | None = None,
        run: Callable[[str], None] | None = None,
        text: str = "",
        index: list[Found] | None = None,
        go: Callable[[Found], None] | None = None,
    ) -> None:
        super().__init__(groups, prefs, full=True, start=start)
        self.index = index or []
        self.go = go
        self.found: list[Found] = []
        """The matches shown for the words on the line."""
        self.talk = talk
        self.about = app
        """The app on the strip, whose commands the line runs."""
        self.commands = _commands(commands)
        self.ask = ask
        self.take = take
        self.run = run
        self.text = text
        self.narrowed_for: str | None = None
        """What the list of matches was made for."""
        self.ask_first = ask_first
        self.settings_chosen = not ask_first
        """Whether you chose the settings since the panel last got the
        focus (`,`, ↑ from the line, a click on a setting); if not, the line
        takes the focus with the panel (focus_returned)."""

    def compose_under(self) -> ComposeResult:
        # A click there to scroll leaves the focus where it was.
        with VerticalScroll(id="talk", classes="-empty", can_focus=False):
            yield Static(id="said")
        matches = OptionList(id="matches", classes="-empty")
        matches.can_focus = False  # the line keeps the focus; ↑↓ and Tab pick
        yield matches
        yield Keyline(id="tip")
        with Horizontal(id="line"):
            yield Static("✦", id="prompt")
            # Selected on focus, the ":" typed for you would go with the first key.
            yield _Line(self.text, placeholder=self.placeholder(), select_on_focus=False, id="input")

    def placeholder(self) -> str:
        own = any(name not in ("set", "toggle") for name, *_ in self.commands)
        return f"Search, ask Claude, or : for {self.about}'s commands" if own else "Search or ask Claude, : to set"

    def on_mount(self) -> None:
        # The settings' own on_mount builds the panel after this.
        self.show_talk()
        self.show_tip()
        self.narrow(self.text)
        line = self.query_one(Input)
        line.cursor_position = len(line.value)
        if self.ask_first:
            line.focus()

    def on_screen_resume(self) -> None:
        self.show_talk()
        self.show_tip()

    def on_resize(self, _event: events.Resize) -> None:
        self.show_tip()
        self.fit()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self.show_keys()

    def on_descendant_blur(self, _event: events.DescendantBlur) -> None:
        self.show_keys()

    # -- where the focus is --

    def focus_line(self) -> None:
        """The focus on the line to ask on."""
        self.settings_chosen = False
        self.query_one(Input).focus()

    def focus_settings(self, group: str | None = None) -> None:
        """The focus on the settings, on `group` if it's one of them."""
        self.settings_chosen = True
        self.set_focus(None)
        index = next((i for i, g in enumerate(self.groups) if g.id == group), None)
        if index is not None:
            self.select_group(index)

    def focus_returned(self, focus: bool) -> None:
        """The panel got the focus (`focus`) or lost it. Getting it, you can
        type a question at once, unless you chose the settings meanwhile;
        losing it, the next time is the line's again."""
        if not focus:
            self.settings_chosen = False
        elif not self.settings_chosen:
            self.focus_line()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Typing a command, or with matches shown, the line takes ↑ and Tab itself.
        if action in ("up", "next_group") and (self.commanding or self.found) and self.focused is not None:
            return False
        return super().check_action(action, parameters)

    def action_up(self) -> None:
        """↑: from the line back to the settings; in them, up a row."""
        if self.focused is not None:
            self.settings_chosen = True
            self.set_focus(None)
        else:
            self.action_move("up")

    def action_move(self, direction: str) -> None:
        index, count = self.selected[self.group.id], self.count()
        last = index == count - 1 if self.view == "list" else index // self.cols == (count - 1) // self.cols
        if direction == "down" and last:
            self.focus_line()
        else:
            super().action_move(direction)

    def item_clicked(self, index: int) -> None:
        self.settings_chosen = True
        self.set_focus(None)
        super().item_clicked(index)

    def panel_keys(self) -> list[Key]:
        """The settings' keys while they have the focus; the line's while it
        has: Ret asks, or takes what's offered while nothing is typed."""
        if self.focused is None:
            return [*super().panel_keys(), Key("Alt-c", "tip", "app.claude")]
        if self.commanding:
            return [Key("Ret", "run", "enter"), Key("Tab", "complete"), Key("↑↓", "pick"), Key("Esc", "close", "close")]
        if self.found:
            picked = self.picked()
            take = "ask" if picked is None else _TAKE[picked.kind]
            return [Key("Ret", take, "enter"), Key("↑↓ Tab", "pick"), Key("Esc", "close", "close")]
        typed = bool(self.query_one(Input).value.strip())
        offer = None if typed else self.talk.offer()
        keys = [Key("Ret", offer or "ask", "enter"), Key("↑", "settings", "up"), Key("Alt-c", "tip", "app.claude"),
                Key("Esc", "close", "close")]
        return [*keys, Key("PgUp PgDn", "scroll", "page(1)")] if self.talk.said else keys

    # -- Claude --

    def set_about(self, app: str, commands: list[list] | None = None) -> None:
        """Another app is on the strip: the line runs its `commands` now."""
        if app != self.about:
            self.about, self.commands = app, _commands(commands)
            self.query_one(Input).placeholder = self.placeholder()
            self.narrow(self.query_one(Input).value)

    def set_index(self, index: list[Found], commands: list[list] | None = None) -> None:
        """What the line searches, and the commands it runs, now."""
        self.index = index
        if commands is not None:
            self.commands = _commands(commands)
        if self.query("#input"):
            self.narrow(self.query_one(Input).value)

    # -- commands --

    @property
    def commanding(self) -> bool:
        """Whether the line holds a command: what's typed starts with ":"."""
        lines = self.query("#input")
        return bool(lines) and lines.first(Input).value.lstrip().startswith(":")

    def narrow(self, text: str) -> None:
        """After ":", show the commands that start with what's typed, or,
        after a command's name, the choices for its argument; else what
        holds the words typed, none picked."""
        self.narrowed_for = text
        commanding = text.lstrip().startswith(":")
        self.found = [] if commanding else search(self.index, text, self.about)
        if not commanding:
            self.set_class(bool(self.found), "-searching")
            self.set_class(False, "-commanding")
            self._show_matches([_match(f, self.about) for f in self.found], highlight=False)
            return
        self.set_class(False, "-searching")
        word, space, rest = text.lstrip().removeprefix(":").lstrip().partition(" ")
        options = []
        for name, args, what, choices, keys in self.commands if commanding else []:
            if space and name == word:
                # A choice may be two words, as `:set`'s SETTING VALUE: Tab completes one at a time.
                for c in [c for c in choices if c.startswith(rest)]:
                    first, more, _ = c.partition(" ")
                    done = c if " " in rest or not more else f"{first} "
                    if all(o.id != f":{name} {done}" for o in options):
                        options.append(Option(Text.assemble((f":{name} ", "bold"), done), id=f":{name} {done}"))
            elif not space and name.startswith(word):
                prompt = Text.assemble((f":{name}", "bold"), " ", (args, "italic"), "  ",
                                       *([(keys, "bold"), "  "] if keys else []), (what, "dim"))
                options.append(Option(prompt, id=f":{name} "))
        self.set_class(commanding, "-commanding")
        self._show_matches(options, highlight=True)

    def _show_matches(self, options: list[Option], highlight: bool) -> None:
        matches = self.query_one("#matches", OptionList)
        matches.clear_options()
        matches.add_options(options)
        matches.set_class(not options, "-empty")
        if options and highlight:
            matches.highlighted = 0
        self.fit()
        self.show_keys()

    def picked(self) -> Found | None:
        """The match picked, if any."""
        if not self.found or not self.query("#matches"):
            return None
        index = self.query_one("#matches", OptionList).highlighted
        return self.found[index] if index is not None and index < len(self.found) else None

    def _catch_up(self) -> OptionList:
        """The matches, for what's typed now, which may be newer than the list."""
        value = self.query_one(Input).value
        if value != self.narrowed_for:
            self.narrow(value)
        return self.query_one("#matches", OptionList)

    def action_pick(self, delta: int) -> None:
        matches = self._catch_up()
        if self.found:
            # None picked to start with, so Ret asks; ↑ from the first goes back there.
            at = matches.highlighted
            at = (0 if delta > 0 else None) if at is None else at + delta
            if at is None or at < 0:
                matches.highlighted = None
            else:
                matches.highlighted = min(at, matches.option_count - 1)
            self.show_keys()
        elif matches.option_count:
            matches.highlighted = ((matches.highlighted or 0) + delta) % matches.option_count

    def action_complete(self) -> None:
        matches = self._catch_up()
        if matches.highlighted is None or not matches.option_count:
            return
        line = self.query_one(Input)
        line.value = matches.get_option_at_index(matches.highlighted).id or ""
        line.cursor_position = len(line.value)

    # -- Claude --

    def show_talk(self) -> None:
        """Your latest question and what was said after it, as `talk` has
        it now, scrolled to its end; nothing while there's none."""
        if not self.query("#said"):
            return  # not composed yet: it shows them once mounted
        talk = self.query_one("#talk", VerticalScroll)
        talk.set_class(not self.talk.said, "-empty")
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column()
        for said in self.talk.said:
            mark, style = MARKS.get(said.who, ("", ""))
            text = said.text
            if said.waiting:
                text, style = (f"{text} …", style) if text else ("Claude is thinking…", "dim")
            table.add_row(Text(mark, style=style), Text(text, style=style))
        self.query_one("#said", Static).update(table)
        self.call_after_refresh(talk.scroll_end, animate=False)  # once the new text is laid out
        self.fit()
        self.show_keys()

    def show_tip(self) -> None:
        """The tip line as `talk` has it now: the tip and its offer, or what
        the line says instead, cut to fit."""
        if not self.query("#tip"):
            return  # not composed yet: it shows them once mounted
        width = self.size.width or 80
        tip, offer = self.talk.tip, self.talk.tip.offer if self.talk.tip is not None else None
        text, room = self.talk.note or (tip.text if tip is not None else ""), width - 7
        text = text if cell_len(text) <= room else text[:max(1, room - 1)] + "…"
        # The offer shows after the tip when there's room; Ret on the line takes it either way.
        offered = [Key("✓", offer, "take_tip")] if offer and not self.talk.note else []
        self.query_one("#tip", Keyline).set_keys(Key("✦", text, "app.claude"), *offered)
        self.show_keys()

    def fit(self) -> None:
        """The answer takes the rows it needs, but leaves the settings their
        tabs, a row of tiles and the description; with less room, it
        scrolls."""
        description = next(iter(self.query("#help")), None)
        rows = 3 + 1 + 2 + (description.outer_size.height if description is not None else 1)
        for part in self.query("#talk, #matches"):
            part.styles.max_height = max(1, self.size.height - rows)

    def show_help(self, note: str | None = None, error: bool = False) -> None:
        super().show_help(note, error)
        self.call_after_refresh(self.fit)  # once the description is laid out

    async def show_setting(self, group: str, key: tuple[str, ...], problem: str | None = None, saved: bool = True) -> None:
        """Show a setting that changed outside the panel, such as one Claude
        offered: its group, the setting selected, and that it was saved, or
        `problem`; with `saved` false, one found on the line, as it is."""
        index = next((i for i, g in enumerate(self.groups) if g.id == group), None)
        if index is None:
            return
        await self.action_select_group(index)
        for item in self.items():
            self.render_item(item)
        at = next((i for i, s in enumerate(self.group.settings) if s.key == key), None)
        if at is not None:
            self.select(at)
            if saved or problem:
                self.show_help(problem or f"Saved. {self.group.where}", error=problem is not None)

    @on(Input.Changed)
    def changed(self, event: Input.Changed) -> None:
        if event.value != self.narrowed_for:
            self.narrow(event.value)
        self.show_keys()

    @on(Input.Submitted)
    def submitted(self, _event: Input.Submitted) -> None:
        self.action_enter()

    def action_enter(self) -> None:
        line = self.query_one(Input)
        text = line.value.strip()
        if text.startswith(":"):
            line.value = ""
            if (command := text[1:].strip()) and self.run is not None:
                self.run(command)
        elif (found := self.picked()) is not None and line.value == self.narrowed_for:
            line.value = ""
            if self.go is not None:
                self.go(found)
        elif text:
            line.value = ""
            self.ask(text)
        elif self.talk.offer():
            self.take(self.talk.last)

    def action_take_tip(self) -> None:
        self.take(self.talk.tip)

    def action_page(self, pages: int) -> None:
        talk = self.query_one("#talk", VerticalScroll)
        talk.scroll_relative(y=pages * max(1, talk.size.height - 1), animate=False)


_TAKE = {"setting": "show it", "command": "type it", "key": "show it", "help": "open it"}
"""What Ret does with a match picked, by its kind, for the keys."""


def _match(found: Found, about: str) -> Option:
    """A match, as the list above the line shows it: its name, where it is,
    and what it does, dim."""
    app = "" if found.app == about else f"{found.app} · "
    where = f"{app}{found.where}" if found.kind != "help" else f"{app}help"
    return Option(Text.assemble(
        (found.name, "bold"), "  ", (where, "italic"), "  ", (found.what, "dim"), no_wrap=True, overflow="ellipsis",
    ))


def _commands(commands: list[list] | None) -> list[tuple[str, str, str, list[str], str]]:
    """An app's commands, as (name, args, what it does, choices, keys), the
    keys being the command's in the app's key tree, if any."""
    parts = []
    for command in commands or []:
        if command and isinstance(command[0], str):
            name, args, what, choices, keys = (list(command) + ["", "", [], ""])[:5]
            parts.append((name, str(args or ""), str(what or ""), [str(c) for c in choices or []], str(keys or "")))
    return parts


class _Line(Input):
    """The panel's line. Typing a command, it handles Tab, ↑↓ and Ret
    itself, in order with the keys typed before them, however fast they
    came: as bindings, they would act only once the key had gone round the
    app."""

    async def _on_key(self, event: events.Key) -> None:
        screen = self.screen
        if event.key in ("tab", "up", "down", "enter") and isinstance(screen, PanelScreen) and screen.found:
            event.stop()
            event.prevent_default()
            if event.key == "enter":
                await self.action_submit()
            elif event.key == "up" and screen.picked() is None:
                screen.action_up()  # none picked: back to the settings
            else:
                screen.action_pick(-1 if event.key == "up" else 1)
        elif event.key in ("tab", "up", "down", "enter") and isinstance(screen, PanelScreen) and screen.commanding:
            event.stop()
            event.prevent_default()
            if event.key == "tab":
                screen.action_complete()
            elif event.key == "enter":
                await self.action_submit()
            else:
                screen.action_pick(-1 if event.key == "up" else 1)
        else:
            await super()._on_key(event)
