"""A one-line strip of key hints for Textual apps, like micro's or nano's
key menu: `key label` pairs, clickable, trimmed to fit."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.widget import Widget

__all__ = ["Key", "Keyline"]


@dataclass(frozen=True)
class Key:
    key: str
    """What to press, as shown: "n", "Ret", "Ctrl+e"; empty for a hint only a
    click runs."""
    label: str
    """What it does: "new", "open"."""
    action: str | None = None
    """Textual action run when the hint is clicked, e.g. "app.quit"."""


class Keyline(Widget):
    """Key hints on one line. Hints that don't fit are dropped from the end,
    so list the most useful first. Clicking a hint runs its action. `right`
    is text at the line's right end, such as a clock; it shows only while
    every hint fits, so it goes before any hint does.

    Add the `-active` class to highlight the line, e.g. while its pane has
    focus; style the parts with the `keyline--key` and `keyline--label`
    component classes.
    """

    DEFAULT_CSS = """
    Keyline {
        height: 1;
        background: $panel;
        color: $text-muted;
    }
    Keyline > .keyline--key {
        text-style: bold;
        color: $text-muted;
        background: $boost;
    }
    Keyline.-active {
        color: $text;
    }
    Keyline.-active > .keyline--key {
        color: $text;
        background: $accent 60%;
    }
    """
    COMPONENT_CLASSES = {"keyline--key", "keyline--label"}

    def __init__(self, *keys: Key | tuple, right: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._keys = [self._key(k) for k in keys]
        self._right = right
        self._spans: list[tuple[int, int, Key]] = []

    @staticmethod
    def _key(key: Key | tuple) -> Key:
        return key if isinstance(key, Key) else Key(*key)

    @property
    def keys(self) -> list[Key]:
        return list(self._keys)

    def set_keys(self, *keys: Key | tuple) -> None:
        self._keys = [self._key(k) for k in keys]
        self.refresh()

    @property
    def right(self) -> str:
        return self._right

    def set_right(self, text: str) -> None:
        self._right = text
        self.refresh()

    def render(self) -> Text:
        key_style = self.get_component_rich_style("keyline--key")
        label_style = self.get_component_rich_style("keyline--label")
        width = self.size.width or 10_000
        text = Text(" ", no_wrap=True)
        x = 1
        self._spans = []
        for key in self._keys:
            # A hint without a key is just its label: something only a click does.
            shown_key = f" {key.key} " if key.key else ""
            shown_label = f" {key.label}"
            used = cell_len(shown_key) + cell_len(shown_label)
            if x + used > width:
                return text
            text.append(shown_key, key_style)
            text.append(shown_label, label_style)
            self._spans.append((x, x + used, key))
            x += used
            if x + 2 <= width:
                text.append("  ")
                x += 2
        # Right-aligned, a space from the edge, at least two from the last hint.
        right = cell_len(self._right)
        if self._right and x + right + 1 <= width:
            text.append(" " * (width - x - right - 1))
            text.append(self._right, label_style)
            text.append(" ")
        return text

    def key_at(self, x: int) -> Key | None:
        """The hint drawn at column `x`, if any."""
        for start, end, key in self._spans:
            if start <= x < end:
                return key
        return None

    async def on_click(self, event: events.Click) -> None:
        key = self.key_at(event.x)
        if key is not None and key.action:
            event.stop()
            await self.app.run_action(key.action, self.screen)
