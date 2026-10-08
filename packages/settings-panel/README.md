# settings-panel

A settings panel for Textual apps that works like a game's options menu:
settings rise over the bottom of the screen, grouped as numbered tabs; each
group shows as tiles or as a list, with the selected setting's description
right under them; every setting steps through a fixed list of values, so
nothing needs typing. Values are written straight into the config files of
the programs they belong to, JSON or TOML (comments kept).

```python
from pathlib import Path

from settings_panel import OFF_ON, Group, JsonStore, Setting, SettingsScreen, panel_settings

micro = JsonStore(Path("~/.config/micro/settings.json").expanduser())
prefs = JsonStore(Path("~/.config/myapp/settings.json").expanduser())
groups = [
    Group("editor", "Editor", "micro · settings.json", micro, [
        Setting(("softwrap",), "Wrap lines", "Wrap long lines.", OFF_ON, False),
        Setting(("tabsize",), "Tab width", "Columns per tab.", [2, 4, 8], 4),
    ]),
    Group("panel", "Panel", "applies right away", prefs, panel_settings()),
]
app.push_screen(SettingsScreen(groups, prefs, close_keys=("comma",), start="editor"))
```

- **Keys:** arrows move (in a list, ←→ change), Ret/Space next value,
  Backspace previous, Tab or 1–9 switch group, v tiles/list, Esc closes.
  Clicking selects; clicking again changes.
- **Opening and closing:** `start=` names the group to open on, e.g. the one
  for the part of the app that had focus (an unknown id opens the first).
  `close_keys=` are keys that close the panel besides Esc, such as the one
  that opened it; a click above the panel closes it too. `full=True` fills
  the screen, for running the panel on its own.
- **Stores:** `store.get(key)` reads a value, None if it isn't set, and
  `store.set(key, value, default)` writes one. Keys are tuples, since option
  names can contain dots. `JsonStore` keeps only values that differ from the
  default, as micro does, unless `keep_defaults=True` (then a program that
  re-reads the file while running also sees a change back to a default);
  `TomlStore` edits in place and keeps comments. Files are replaced in one
  step, through symlinks (from a dotfiles manager, say). A file that can't
  be read or parsed is never written: `get` and `set` raise `StoreError`,
  and the panel says why.
- **Overlays:** `OverlayStore(base, path, defaults)` keeps the keys of
  `defaults` (key → default) in a JSON file of their own at `path`, such as
  what one instance of a program keeps apart from the others, and the rest
  in `base`. A change to one of them goes to both files, for a new `path`
  to start from: a key `path` doesn't have yet takes `base`'s value, else
  its default, once, so later changes to `base` don't reach it.
- **The model** works without the panel: `group.value(setting)` is a
  setting's value, or its default; `group.change(setting, delta)` steps it
  and saves it. A `Setting`'s `choices` can be a function, for values known
  only at run time, and its `names` show values that aren't clear on their
  own; `options()`, `show(value)` and `step(value, delta)` list, show and
  step them, wrapping around.
- **Live changes:** give a `Group` an `on_change(setting, value)` callback;
  it runs after each change.
- **While open:** `group` is the group shown and `setting` its selected
  setting. `show_help(note)` shows a note after the setting's description,
  in place of where it's saved and its default, as the panel does after
  saving; `error=True` shows it as a problem. The tiles take the rows they
  need, so the description sits right under them; with less room, they
  scroll.
- **Adding to it:** a subclass can add widgets under the settings, above
  their keys, by overriding `compose_under()`, and keys of its own by
  overriding `panel_keys()`; `show_keys()` shows them again, e.g. once the
  focus has moved. While one of its widgets has the focus, such as an input,
  the keys for the tiles (arrows, Ret, Space, Backspace, v, 1–9) are that
  widget's; Tab and Esc still switch group and close.
- **Lists to pick from:** a `Listing` in `groups` is a tab of lines rather
  than settings, such as what's due across projects: `Listing(id, name,
  where, entries, on_pick, verb="open", empty="Nothing here.")`. Each line
  is an `Entry(text, help="", id="", separator=False)`, its text a string
  or Rich Text; the description under the list is the selected line's
  `help`, or `where`. A `separator` is a rule across the list's width,
  redrawn as it changes, to split the lines: ↑↓ step over it, a click on
  it does nothing, and it's never `entry`.
  A Listing is always a list: ↑↓ move, and Ret (shown as `verb`), or a
  click on the selected line, calls `on_pick(entry)`; the settings' other
  keys do nothing there, and `empty` shows while it has no lines. While
  open, `entry` is the selected line (None elsewhere), `count()` how many
  lines or settings the group shown has, and `show_entries(id, entries)`
  gives a Listing new lines, keeping the selected one by its `id`. A
  Listing has no `setting`.
- **Panel state:** `prefs` holds the panel's height and whether the group
  tabs go above the settings or below them (see
  `panel_settings()`) and each group's tiles/list choice.
