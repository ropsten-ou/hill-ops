"""A game-style settings panel for Textual apps, and the small model behind
it: settings with fixed choices, grouped, stored in JSON or TOML files;
and lists to pick from, as tabs beside them."""

from .model import OFF_ON, Entry, Group, JsonStore, Listing, OverlayStore, Setting, StoreError, TomlStore, panel_settings
from .screen import SettingsScreen

__all__ = [
    "OFF_ON",
    "Entry",
    "Group",
    "JsonStore",
    "Listing",
    "OverlayStore",
    "Setting",
    "SettingsScreen",
    "StoreError",
    "TomlStore",
    "panel_settings",
]
