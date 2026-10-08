"""A settings panel that rises over the bottom of the screen, like a game's
options menu: numbered group tabs, and each group as tiles or as a list,
with the selected setting's description right under them. A tab can hold
a list of lines to pick from instead (a Listing)."""

from __future__ import annotations

from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from keyline import Key, Keyline

from .model import Entry, Group, JsonStore, Listing, Setting, StoreError

TILE_WIDTH = 22  # columns per tile, gutter included
PANEL_FRACTIONS = {"1/4": 1 / 4, "1/3": 1 / 3, "1/2": 1 / 2}
PANEL_KEYS = {"panel_height", "tabs"}

TILES_KEYS = [
    Key("←↑↓→", "move"),
    Key("Ret", "change", "change(1)"),
    Key("⌫", "back", "change(-1)"),
    Key("Tab", "group", "next_group(1)"),
    Key("v", "list", "toggle_view"),
    Key("Esc", "close", "close"),
]
LIST_KEYS = [
    Key("↑↓", "move"),
    Key("←→", "change", "change(1)"),
    Key("Tab", "group", "next_group(1)"),
    Key("v", "tiles", "toggle_view"),
    Key("Esc", "close", "close"),
]
def listing_keys(verb: str) -> list[Key]:
    """The keys of a Listing, whose Ret does `verb`."""
    return [
        Key("↑↓", "move"),
        Key("Ret", verb, "change(1)"),
        Key("Tab", "group", "next_group(1)"),
        Key("Esc", "close", "close"),
    ]


TILE_ACTIONS = {"move", "change", "toggle_view", "select_group"}
"""The panel's actions on the tiles: their keys go to a widget with the
focus instead, such as an input a subclass adds."""


class Tab(Static):
    def __init__(self, index: int, name: str) -> None:
        super().__init__(Text.assemble((str(index + 1), "bold"), f" {name}"), classes="tab")
        self.index = index

    def on_click(self) -> None:
        self.screen.select_group(self.index)


class Item(Static):
    """A setting, as a tile or a list row. Clicking selects it; clicking it
    again changes it, like tapping a button in a game's menu."""

    def __init__(self, index: int, kind: str) -> None:
        super().__init__(classes=kind)
        self.index = index

    def on_click(self) -> None:
        self.screen.item_clicked(self.index)


class SettingsScreen(ModalScreen[None]):
    """Settings over the bottom of whatever screen is below.

    `groups` are Groups of settings, and Listings, lines to pick from.
    `prefs` is a JSON store for the panel's own state: its height and tab
    position (see `panel_settings`) and whether each group shows as tiles or
    a list. `start` names the group to open on, e.g. the one for the part of
    the app that had focus. With `full=True` the panel fills the screen, for
    running it on its own.

    A subclass can add widgets under the settings, above their keys, with
    `compose_under()`, and keys of its own with `panel_keys()`. While one of
    its widgets has the focus, such as an input, the keys that act on the
    tiles are that widget's.
    """

    DEFAULT_CSS = """
    SettingsScreen { background: transparent; }
    SettingsScreen #bottom { dock: bottom; height: auto; }
    SettingsScreen.-full #bottom { height: 100%; }
    SettingsScreen #panel { height: 12; background: $surface; border-top: solid $accent; }
    SettingsScreen.-full #panel { height: 1fr; }
    SettingsScreen #tabs { height: 1; background: $panel; }
    SettingsScreen #tabs.-bottom { dock: bottom; }
    SettingsScreen .tab { width: auto; padding: 0 1; }
    SettingsScreen .tab.-current { background: $accent; color: $text; text-style: bold; }
    SettingsScreen #body { height: 1fr; padding: 0 1; }
    SettingsScreen #tiles { layout: grid; grid-size: 1; grid-gutter: 1 1; grid-rows: 2; height: auto; }
    SettingsScreen .tile { height: 2; padding: 0 1; background: $boost; }
    SettingsScreen .row { height: 1; padding: 0 1; max-width: 60; }
    SettingsScreen .row.-entry { max-width: 100%; }
    SettingsScreen .row.-empty { color: $text-muted; }
    SettingsScreen .row.-separator { padding: 0; }
    SettingsScreen Item.-selected { background: $accent 50%; }
    SettingsScreen #help { height: auto; max-height: 3; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("up", "move('up')", priority=True),
        Binding("down", "move('down')", priority=True),
        Binding("left", "move('left')", priority=True),
        Binding("right", "move('right')", priority=True),
        Binding("enter,space", "change(1)", priority=True),
        Binding("backspace", "change(-1)", priority=True),
        Binding("tab", "next_group(1)", priority=True),
        Binding("shift+tab", "next_group(-1)", priority=True),
        Binding("v", "toggle_view", priority=True),
        *[Binding(str(n), f"select_group({n - 1})", priority=True) for n in range(1, 10)],
    ]

    def __init__(
        self,
        groups: list[Group | Listing],
        prefs: JsonStore,
        *,
        full: bool = False,
        close_keys: tuple[str, ...] = (),
        start: str | None = None,
    ) -> None:
        super().__init__(classes="-full" if full else None)
        self.groups = groups
        self.prefs = prefs
        self.full = full
        self.close_keys = close_keys
        self.group_index = next((i for i, g in enumerate(groups) if g.id == start), 0)
        self.selected = {group.id: 0 for group in groups}
        self.cols = 1

    # -- the panel's own state, read fresh so changes apply right away --

    def pref(self, name: str, default: str) -> str:
        try:
            value = self.prefs.get((name,))
        except StoreError:
            value = None
        return value if isinstance(value, str) else default

    @property
    def group(self) -> Group | Listing:
        """The group shown."""
        return self.groups[self.group_index]

    @property
    def setting(self) -> Setting:
        """The group's selected setting; a Listing has none."""
        assert isinstance(self.group, Group)
        return self.group.settings[self.selected[self.group.id]]

    @property
    def entry(self) -> Entry | None:
        """A Listing's selected line, if it's shown and has lines."""
        group = self.group
        if not isinstance(group, Listing) or not group.entries:
            return None
        entry = group.entries[min(self.selected[group.id], len(group.entries) - 1)]
        return None if entry.separator else entry

    def _pickable(self, index: int, step: int) -> int | None:
        """The first line of a Listing from `index` on, going by `step`,
        that isn't a separator; None past the end."""
        entries = self.group.entries if isinstance(self.group, Listing) else []
        while 0 <= index < len(entries) and entries[index].separator:
            index += step
        return index if 0 <= index < len(entries) else None

    def count(self) -> int:
        """How many settings, or lines, the group shown has."""
        group = self.group
        return len(group.entries if isinstance(group, Listing) else group.settings)

    @property
    def view(self) -> str:
        if isinstance(self.group, Listing):
            return "list"
        try:
            view = self.prefs.get(("views", self.group.id))
        except StoreError:
            view = None
        return view if view in ("tiles", "list") else "tiles"

    # -- layout --

    def compose(self) -> ComposeResult:
        # Nothing may sit above the panel: any widget there, even a
        # transparent one, would blank out the screen behind it.
        with Vertical(id="bottom"):
            yield Vertical(id="panel")
            yield from self.compose_under()
            yield Keyline(*TILES_KEYS, id="settings-keys", classes="-active")

    def compose_under(self) -> ComposeResult:
        """Widgets under the settings, above their keys: none here, for a
        subclass to add its own."""
        yield from ()

    async def on_mount(self) -> None:
        await self.build_panel()

    def on_click(self, event: events.Click) -> None:
        # A click above the panel lands on the screen itself.
        if event.widget is self:
            self.dismiss()

    def on_key(self, event: events.Key) -> None:
        if event.key in self.close_keys:
            event.stop()
            self.dismiss()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # The keys for the tiles go to a widget with the focus, if any.
        return not (action in TILE_ACTIONS and self.focused is not None)

    async def build_panel(self) -> None:
        panel = self.query_one("#panel")
        await panel.remove_children()
        tabs = Horizontal(*[Tab(i, g.name) for i, g in enumerate(self.groups)], id="tabs")
        body = VerticalScroll(id="body", can_focus=False)
        help_line = Static(id="help")
        top = self.pref("tabs", "top") == "top"
        # Tabs below the settings stay at the bottom, under the description,
        # which sits right under the tiles.
        tabs.set_class(not top, "-bottom")
        await panel.mount_all([tabs, body, help_line] if top else [body, help_line, tabs])
        self.size_panel()
        await self.show_group()

    def size_panel(self) -> None:
        if self.full:
            return  # the panel takes the screen, but what's under it
        fraction = PANEL_FRACTIONS.get(self.pref("panel_height", "1/3"), 1 / 3)
        self.query_one("#panel").styles.height = max(9, round(self.size.height * fraction))

    def on_resize(self, event: events.Resize) -> None:
        self.size_panel()
        if self.view == "tiles":
            self.set_columns()

    def set_columns(self) -> None:
        width = self.query_one("#body").size.width or self.size.width
        self.cols = max(1, width // TILE_WIDTH)
        for grid in self.query("#tiles"):
            grid.styles.grid_size_columns = self.cols
        self._fit_body()

    def _fit_body(self) -> None:
        """The tiles, or the list, take the rows they need and no more, so the
        description sits right under them; with less room they scroll."""
        count = max(1, self.count())
        rows = -(-count // self.cols) * 3 - 1 if self.view == "tiles" else count
        body = self.query_one("#body")
        body.styles.max_height = max(1, rows)
        body.styles.min_height = max(1, min(2, rows))

    async def show_group(self) -> None:
        for tab in self.query(Tab):
            tab.set_class(tab.index == self.group_index, "-current")
        body = self.query_one("#body", VerticalScroll)
        await body.remove_children()
        listing = isinstance(self.group, Listing)
        kind = "tile" if self.view == "tiles" else "row -entry" if listing else "row"
        items = [Item(i, kind) for i in range(self.count())]
        if listing and not items:
            await body.mount(Static(self.group.empty, classes="row -empty"))
            self._fit_body()
        elif kind == "tile":
            await body.mount(Container(*items, id="tiles"))
            self.set_columns()
        else:
            await body.mount_all(items)
            self._fit_body()
        self.show_keys()
        for item in items:
            self.render_item(item)
        if items:
            self.select(self.selected[self.group.id])
        else:
            self.show_help()

    def panel_keys(self) -> list[Key]:
        """The keys shown under the panel: the tiles' or the list's; a
        subclass can add its own."""
        if isinstance(self.group, Listing):
            return listing_keys(self.group.verb)
        return TILES_KEYS if self.view == "tiles" else LIST_KEYS

    def show_keys(self) -> None:
        """Show `panel_keys()` under the panel, e.g. once what has the focus
        has changed."""
        self.query_one("#settings-keys", Keyline).set_keys(*self.panel_keys())

    # -- items --

    def render_item(self, item: Item) -> None:
        if isinstance(self.group, Listing):
            entry = self.group.entries[item.index]
            if entry.separator:
                # A Rule takes the width it's drawn at, so it follows a resize.
                item.add_class("-separator")
                item.update(Rule(style=""))
                return
            text = entry.text
            item.update(text if isinstance(text, Text) else Text(str(text)))
            return
        group, setting = self.group, self.group.settings[item.index]
        try:
            value = group.value(setting)
        except StoreError:
            item.update(Text(setting.label, style="dim"))
            return
        shown = setting.show(value)
        style = "bold" if value != setting.default else ""
        if item.has_class("tile"):
            item.update(Text.assemble((setting.label, "bold"), "\n", (shown, style)))
            return
        if item.index == self.selected[group.id]:
            shown = f"◀ {shown} ▶"
        row = Table.grid(expand=True)
        row.add_column(ratio=1)
        row.add_column(justify="right")
        row.add_row(setting.label, Text(shown, style=style))
        item.update(row)

    def items(self) -> list[Item]:
        return list(self.query(Item))

    def select(self, index: int) -> None:
        items = self.items()
        if not items:
            return
        index = max(0, min(index, len(items) - 1))
        if isinstance(self.group, Listing):
            index = next((i for i in (self._pickable(index, 1), self._pickable(index, -1)) if i is not None), index)
        previous = self.selected[self.group.id]
        self.selected[self.group.id] = index
        for item in items:
            item.set_class(item.index == index, "-selected")
        if items[0].has_class("row") and not items[0].has_class("-entry"):
            for i in {previous, index}:
                if i < len(items):
                    self.render_item(items[i])
        self.query_one("#body", VerticalScroll).scroll_to_widget(items[index], animate=False)
        self.show_help()

    def show_help(self, note: str | None = None, error: bool = False) -> None:
        """The selected setting's help, then `note`, or where the group is
        saved and the default; `error` shows the note as a problem. In a
        Listing: the selected line's help, or what the lines are."""
        if isinstance(self.group, Listing):
            entry = self.entry
            text = Text(entry.help if entry is not None and entry.help else "")
            if not text:
                text.append(self.group.where, style="dim")
            if note:
                text.append(f"  {note}", style="bold red" if error else "dim")
            self.query_one("#help", Static).update(text)
            return
        setting = self.setting
        text = Text(f"{setting.help}  " if setting.help else "")
        if error:
            text.append(note or "", style="bold red")
        else:
            text.append(note or f"{self.group.where} · default {setting.show(setting.default)}", style="dim")
        self.query_one("#help", Static).update(text)

    def item_clicked(self, index: int) -> None:
        if isinstance(self.group, Listing) and self.group.entries[index].separator:
            return
        if index == self.selected[self.group.id]:
            self.action_change(1)
        else:
            self.select(index)

    # -- actions --

    def action_close(self) -> None:
        self.dismiss()

    def action_move(self, direction: str) -> None:
        index, count = self.selected[self.group.id], self.count()
        if isinstance(self.group, Listing):
            step = 1 if direction == "down" else -1
            if direction in ("up", "down") and (to := self._pickable(index + step, step)) is not None:
                self.select(to)
            return
        if self.view == "list":
            if direction in ("left", "right"):
                self.action_change(1 if direction == "right" else -1)
                return
            self.select(index + (1 if direction == "down" else -1))
            return
        cols = self.cols
        if direction == "left" and index % cols:
            index -= 1
        elif direction == "right" and (index + 1) % cols and index + 1 < count:
            index += 1
        elif direction == "up" and index >= cols:
            index -= cols
        elif direction == "down" and index // cols < (count - 1) // cols:
            index = min(index + cols, count - 1)
        self.select(index)

    def action_change(self, delta: int) -> None:
        group = self.group
        if isinstance(group, Listing):
            if delta > 0 and (entry := self.entry) is not None and group.on_pick is not None:
                group.on_pick(entry)
            return
        setting = group.settings[self.selected[group.id]]
        try:
            group.change(setting, delta)
        except (StoreError, OSError) as e:
            self.show_help(f"Not saved: {e}", error=True)
            return
        self.render_item(self.items()[self.selected[group.id]])
        self.show_help(f"Saved. {group.where}")
        if group.store is self.prefs and setting.key[0] in PANEL_KEYS:
            if setting.key[0] == "tabs":
                self.run_worker(self.build_panel(), exclusive=True)
            else:
                self.size_panel()

    async def action_next_group(self, delta: int) -> None:
        await self.action_select_group((self.group_index + delta) % len(self.groups))

    async def action_select_group(self, index: int) -> None:
        if 0 <= index < len(self.groups) and index != self.group_index:
            self.group_index = index
            await self.show_group()

    def select_group(self, index: int) -> None:
        self.run_worker(self.action_select_group(index), exclusive=True)

    async def action_toggle_view(self) -> None:
        if isinstance(self.group, Listing):
            return  # always a list
        new = "list" if self.view == "tiles" else "tiles"
        try:
            self.prefs.set(("views", self.group.id), new, "tiles")
        except (StoreError, OSError) as e:
            self.show_help(f"Not saved: {e}", error=True)
            return
        await self.show_group()

    def show_entries(self, listing: str, entries: list[Entry]) -> None:
        """Give the Listing with the id `listing` new lines, and show them if
        it's shown, the same line selected (by its `id`) if it's still
        there."""
        group = next((g for g in self.groups if isinstance(g, Listing) and g.id == listing), None)
        if group is None:
            return
        old = self.entry if self.group is group else None
        group.entries = entries
        at = next((i for i, e in enumerate(entries) if old is not None and old.id and e.id == old.id), None)
        self.selected[listing] = at if at is not None else min(self.selected[listing], max(0, len(entries) - 1))
        if self.group is group:
            self.run_worker(self.show_group(), exclusive=True)
