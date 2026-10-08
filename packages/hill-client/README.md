# hill-client

Join hill-ops's channel from a Python app. hill-ops runs an app above a strip for its
settings, help, command line and questions; over the channel the app says
hello, asks for any of those, hears back what happened there, and reports
its errors for hill-ops's event log.

```python
from hill_client import Client

client = Client.from_env("palace", profile="/path/to/hill.toml",
                         keys=[(",", "settings", "app.settings")],
                         commands=[("new", "[TITLE]", "write a new note")],
                         help=[("List", [("n", "a new note")])],
                         docs="/path/to/README.md",
                         tree=[("s", "scripts", [("s", "sync", "sync")]), ("w", "work", "work")])
if client is not None:                       # None outside hill-ops
    client.on("settings.changed", apply)     # apply({"group", "key", "value"})
    client.on("run", lambda p: run_action(p["action"]))
    client.on("command", lambda p: run_command(p["line"]))
    client.on("answer", lambda p: answered(p["id"], p["value"]))
    asyncio.create_task(client.run())        # stays connected until closed
    client.send("settings.open", group="list")
    client.send("tree.open")                  # the key tree, in the strip
    client.send("ask", id=1, question="New note title", value="")
    client.send("error", what="push failed")  # your words, never a title or a path
    client.send("panes", view={"name": "preview", "argv": ["palace", "_preview"]}, claude=None)
```

- **Messages** are JSON-RPC notifications, one per line; `line(method,
  **params)` makes one. The channel's path is in `$HILL_SOCKET` (`SOCKET`
  names the variable), which every program started inside hill-ops inherits.
- **Client(path, app, profile=None, keys=(), commands=(), help=(),
  docs=None, tree=())** says hello once connected (`connected` says whether it is),
  and again after reconnecting if the strip restarts. Hello carries the
  app's name, its settings profile, its app-wide keys as `(key, label,
  action)`, its commands as `(name, args, what it does[, choices])`, its
  help as `(title, [(key, what it does), ...])` sections, and `docs`, the
  path to its README, which Claude reads in the strip to answer questions
  about the app; its key tree, nodes `(key, label, line)` for a
  command, `line` being what the strip sends back in `command`, or `(key,
  label, [nodes...])` for a group (see The key tree in hill-ops's README); and
  the tmux pane it runs in, from `$TMUX_PANE`, so the
  strip shows its keys while that pane has the focus. `Client.from_env(...)`
  gives one for `$HILL_SOCKET`, or None outside hill-ops. The messages
  themselves are listed in hill-ops's README.
- **send(method, **params)** never waits: what's sent while not connected
  goes out after the next hello.
- **on(method, handler)** calls `handler(params)` for each message of that
  kind from hill-ops; a handler may be async.
- **run()** keeps the client connected until **close()**.
- **connect(path, wait=5.0)** opens the channel, retrying every 50 ms while
  hill-ops's strip starts, for apps that handle the stream themselves.
- **take_focus(pane=None)** gives the focus in hill-ops's window to the tmux
  pane the program runs in (`$TMUX_PANE`), or to `pane`, as a click on it
  would: for focus that follows the mouse, call it when the mouse moves
  over the program while it doesn't have the focus. It never takes the
  focus from hill-ops's strip while the strip keeps it, for the help, the
  command line or a question, which close when it goes: a click or Esc
  gives it back. The panel lets it go, and stays open. It returns whether
  the pane has the focus now, and False outside tmux. It needs no channel,
  so programs an app runs beside it can use it too.
- **Hover()** makes that lazy, as palace's panes and hill-ops's panel are:
  where the focus left from under the mouse, by a key say, a nudge doesn't
  take it back, so a hand brushing the mouse while you type never sends
  your keys elsewhere. Call **moved(at, part, focused)** for each move of
  the mouse, with where it is as `(x, y)`, the part of the program it's
  over (a pane, or a part of one; None for nothing that takes the focus)
  and whether that part has the focus: it says whether the part should
  take it now. Call **left(part)** when the focus leaves a part.
  **take_focus()** asks hill-ops for the focus with `take_focus()`, and after
  a no doesn't ask again for `Hover.RETRY` seconds (0.25). The mouse must
  move more than `Hover.NUDGE` (2 columns or 1 row) from where it rested.

```python
hover = Hover()

def on_mouse_move(event):                    # in a Textual app
    if hover.moved(event.screen_offset, part_under(event), has_focus) and hover.take_focus():
        give_focus(part_under(event))
```
