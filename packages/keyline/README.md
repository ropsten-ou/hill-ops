# keyline

A one-line strip of key hints for Textual apps, like micro's or nano's key
menu. Put one under each pane so the hints sit next to what they act on.

```python
from keyline import Key, Keyline

Keyline(
    Key("Ret", "open", "open"),
    Key("n", "new", "new_note"),
    Key("q", "quit", "app.quit"),
)
```

- Hints are `Key`s or plain `(key, label, action)` tuples; one without an
  action is only shown, and one with an empty key shows just its label, for
  something only a click does.
- Hints that don't fit are dropped from the end, so list the most useful
  first.
- `right=` is text at the line's right end, such as a clock. It shows only
  while every hint fits, so on a narrow line it goes first.
  `set_right(text)` changes it, and `right` gives it back.
- Clicking a hint runs its action in the screen's namespace (`app.` and
  other prefixes work as in bindings).
- `set_keys(...)` changes the hints, e.g. when a mode changes; `keys` gives
  them back, and `key_at(x)` the one drawn at column `x`, if any.
- The `-active` class highlights the line, e.g. while its pane has focus.
  Style the parts with the `keyline--key` and `keyline--label` component
  classes.
