# hill-ops

Runs a terminal app above a thin strip that holds what isn't the app's own
work: its settings, its help, a command line, its questions, and Claude,
which offers tips, answers what you ask and offers the app's commands and
settings that do it. The app and the
strip talk over a channel, so opening settings in the app zooms the strip,
and a setting changed in the strip applies in the app at once. An app can
also run programs of its own beside it, such as palace's preview and its
Claude pane, with the strip under them.

![palace above hill-ops's strip, with its preview and Claude's pane beside it](docs/screenshot.svg)

## Getting started

Its name on PyPI, and the command you type, are `hill-ops` too.

### What it needs

- [uv](https://docs.astral.sh/uv/), which installs hill-ops and the Python
  it runs on (3.11 or later).
- [tmux](https://github.com/tmux/tmux): hill-ops runs a tmux server of its
  own, so your tmux and its settings stay out of it.
- For Claude in the strip:
  [`claude-agent-acp`](https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp),
  from npm, with [Claude Code](https://claude.com/claude-code) signed in.

On macOS, with Homebrew:

```bash
brew install uv tmux
npm install -g @agentclientprotocol/claude-agent-acp
```

On Debian or Ubuntu, `sudo apt install tmux`, and uv from its
[installer](https://docs.astral.sh/uv/getting-started/installation/).

### Quickstart

1. Install what it needs, above.
2. `uv tool install hill-ops`.
3. `hill-ops micro notes.md`, or any program that runs in a terminal:
   it runs above the strip, whose line ends with the time.
4. Click *settings* on the strip: it grows to the panel, with hill-ops's
   own settings, and a line under them to search them or to ask Claude
   about the app. Esc closes it.
5. An app that talks to hill-ops gets more: its settings, keys, help and
   commands in the strip. [palace](https://github.com/pierreb4/hill), a
   notes screen, is one (`uv tool install hill`, then `hill`); The
   channel and Profiles, below, say how to make another.

What follows is the reference.

## Running it

```bash
ln -s ~/projects/hill-ops/bin/shim ~/.local/bin/hill-ops
hill-ops palace              # palace above the strip, in its last instance
hill-ops -- micro notes.md   # -- when the command could be taken for one of hill-ops's
hill-ops --resume palace     # pick which instance from a list (see Instances)
hill-ops --new palace        # in a new instance
hill-ops settings [GROUP]    # the settings on their own, filling the terminal
hill-ops docs [APP]          # APP's reference, generated from what it declares
hill-ops connect             # the channel over stdin and stdout (see The channel)
```

On PyPI it's `hill-ops`, and so is its command: `hill` is palace, the
notes screen, which runs inside it (`uv tool install hill` brings
hill-ops along). The import is `hill_ops`.

`bin/shim` runs the command its link is named after from hill-ops's own
`.venv`, after `uv sync` has brought `.venv` in line with `uv.lock`, so a
changed dependency is installed at the next start; a `uv tool install` copy
doesn't update.

Inside hill-ops, `hill-ops COMMAND` just runs COMMAND, so an app can start itself
in hill-ops without looping; so does hill-ops without a terminal or without tmux.

hill-ops was called wrap until 2026-10-06, and hill until 2026-10-07,
when its command, folder, repo and import became `hill-ops` (`hill_ops`).
Its settings and state stay in `~/.config/hill` and `~/.local/state/hill`,
and its variables, messages and profiles keep `hill` too (`$HILL_*`,
`hill.tree`, `hill.toml`).

## How it runs

hill-ops starts a tmux server of its own, with its own configuration
(`src/hill_ops/tmux.conf`), so your tmux and `~/.tmux.conf` stay out of it, and
hill-ops works inside your tmux too. The app runs in the top pane and the strip
in the pane below, or, when the app runs programs beside it, the app on the
left and the strip under those programs (see Panes beside the app);
tmux's prefix key, status line, right-click menus,
Ctrl-click, which swaps panes, and selecting a word or a line with a double
or triple click are off. When the app exits, hill-ops exits with its exit
status and reprints what the app left on screen, such as a report or an
error, then stops its tmux server and removes the socket file tmux would
leave behind.

Its panes find `hill-ops` on PATH: hill-ops adds the folder of its own
commands at the end of PATH, for an app such as micro that runs `hill-ops
connect` when `uv tool install hill` put only palace's commands there.

Every pane in hill-ops's tmux has `COLORTERM=truecolor`, so the app, the strip
and the panes beside the app draw a theme in its own colours, over ssh
too, where `COLORTERM` doesn't come along and apps would otherwise round
their colours to the nearest of 256. tmux converts them down for a
terminal that can't show true colour.

## Instances

Each hill-ops runs in an instance, which keeps the layout it had from one run
to the next: Layout's widths and the panel's height, and what an app keeps
there, such as palace's place and the panes it shows. It's a folder in
`~/.local/state/hill/instances/` (`$HILL_STATE_HOME`), which hill-ops gives its
apps and their panes as `$HILL_INSTANCE`. So two hill-ops running at once each
keep their own: a border dragged in one doesn't move the other's.

- `hill-ops COMMAND` resumes the most recently used instance of the same
  program that isn't running, as Claude resumes a session, or starts a new
  one. Two hill-ops started again one after the other each take up one of
  their own.
- `hill-ops --resume COMMAND` lists them in the terminal first, newest first,
  each with its label and when it was last used, to pick one or a new one:

  ```
  Resume palace:
    1  notes · palace · hill  just now
    2  recipes                2 h ago
    n  a new one
  ```

  An app labels its instance by writing a few words to `label.json` in
  it, as a JSON list; palace writes its last active projects. Without a
  label, the line shows the folder hill-ops ran in.
- `hill-ops --new COMMAND` starts a new one, and `hill-ops --as ID COMMAND` takes
  instance ID, or makes it, for an app that starts hill-ops again and wants
  the same one; it refuses one that's running.

What's in the folder is written as it changes, so a crash or a reboot
keeps it. hill-ops writes `instance.json`: the program, hill-ops's process and
tmux server, and when it ended; an instance runs while both do. A change to
a setting kept per instance goes to the shared settings file too, so a new
instance starts from the last change in any of them. The five most recently
used ended instances of each program are kept, older ones removed.

## The strip

The strip sits under the app, or under the panes it runs beside it: a
border row, then one line with the keys for the whole app of the app in
the pane with the focus (keys for one of its panes stay in the app, next
to that pane). Clicking a hint runs it in that app. With no app on the
channel, the strip offers hill-ops's own settings. Whatever the strip holds,
Esc, or a click back in a pane, shrinks it and gives the focus back to the
pane that had it, but the panel, which stays until Esc. Switching to
another window leaves the strip as it is.

The strip's line ends with the time, so an app that fills the screen
doesn't hide it. Hill → Clock writes it in the locale's format (the
default), 12-hour, 24-hour, or turns it off; it changes on the minute. On a
narrow strip it goes before any of the app's keys does. The locale's format
is LC_TIME's: on macOS every locale writes the time 24-hour, whatever the
system's setting, so 12-hour is there for that.

**The panel** holds the settings, with one line under them for the app's
commands and for Claude. Opening the
settings, from the app or with a click, or the app's key for Claude, grows
the strip to the panel height (Layout → Panel height: a quarter, a third or
half of the window, at least 9 rows) and gives it the focus. The app gets
fewer rows and redraws, so a change shows as you make it. The settings open
on the group the app asked for, under a tab for each of every known app's
groups (the running app's first) and hill-ops's own, Layout and Hill (Hill →
Group tabs can put the tabs below them instead), with the selected setting's description right
under them. An app that sends an overview (see The channel) has it as the
panel's first tab, before the settings, and the panel opens on it unless
the app asks for a group: lines to pick from, such as palace's work items
that wait on you, kept current while the panel is open. ↑↓ move, and Ret,
or a click on the selected line, picks it: the app hears which, and the
panel stays open. Under the settings are Claude's tip line, which always has a
tip (see Claude in the strip), and the line for search, commands and
Claude: what you type after `:` is a command, and anything else searches,
then goes to Claude.
Your question and Claude's answer show above the tip.

The panel opens with the focus on the settings, or, from the app's key for
Claude, on the line to ask. ↓ from the last row of settings goes to the
line, ↑ back, and a click on either does too. On the line, Ret (Return)
asks what you typed, and Claude's answer shows as it comes; when what
Claude said last offers something, Ret on an empty line takes it. A click on
the tip, or Alt-c, asks for another; a click on its offer (✓) takes it.
PgUp and PgDn scroll a long answer. Text you select with the mouse in the
panel, such as Claude's answer, goes to the clipboard as you let go of the
button, as in palace's panes (tmux passes it on: `set-clipboard on`). The panel stays open while you go back
to the app: the app's key for the settings, for Claude or for its command
line gives it the focus again, as does moving the mouse over it, and the
help or a question open over it and go back to it. When the panel gets the
focus back, its line has it, so you can type a question at once, unless
you chose the settings since it last had it (the app's key for the
settings, ↑ from the line, or a click on a setting). While the strip has the focus,
the panel and the help are lighter, and their keys lit, as palace's pane
with the focus is. A problem, such as a profile that can't be read, shows
in the strip for a while.

**Search** is typing words on the panel's line. What holds them all, in
any order, shows above the line, in the place of Claude's answer: every
setting of every app hill-ops knows and of hill-ops itself, and every command, key
and help section of the apps, each with its app, what it is and what it
does. The app on the strip comes first, then what's named so before what
only says so. An app that isn't running is searched as it last said hello:
hill-ops keeps each app's hello in `~/.local/state/hill/apps/`. ↓ or Tab picks
the first match and ↑↓ another; Ret takes it: a setting shows selected in
its tab, a command of the app on the strip is typed on the line for you to
finish, a key on the strip runs as a click would, and a key or a section of
the help opens the help there. Ret with none picked asks Claude, so
searching and asking are one gesture: the matches are what's known, Claude
what isn't. As in Helix, nothing is documented twice: what's found is what
the apps declare.

**Help** grows the strip the same way, with a tab per section: the app's
keys, pane by pane, then its commands, then the strip's own keys. It opens
on the section the app asked for, such as the pane that had focus.

**The command line** is the panel's line, with `:` typed. Typing `:` on
the line lists the app's commands above it, in the place of Claude's
answer, narrowed as you type: Tab completes the highlighted one (and,
after a command's name, its argument's choices), ↑↓ pick one, and Ret sends
the line, without its `:`, to the app, which runs it. The app's key for its
command line (`:` in palace) opens the panel that way, or, if it's open,
types `:` on its line. A panel opened so keeps the focus, as the help
does, and closes once the command has run, or with a click in the app,
unless you asked Claude something meanwhile; one that was open stays,
saying what ran.

Two commands are hill-ops's own, after the app's, as Helix's are: `:set
SETTING VALUE` changes a setting, and `:toggle SETTING` changes it to its
next value, round to the first. A setting is named by its group's id and
its key, by dots (`:set list.notes.sort modified`); a value as the panel
shows it or as JSON (`true`, `4`). Tab completes the setting, then its
value. The setting shows selected in the panel, and the line says what it
is now.

**The key tree** is the app's commands reached by short sequences of
keys, from a menu that shows the key for each entry, so reading the menu
is pressing the fast way's keys (after ExNovo, and Emacs's which-key). The
app declares the tree in its hello and opens it with a key of its own
(Space in palace's list); the strip grows, takes the focus and shows the
top level's entries in columns, each with its key, a group marked `+`,
and above them the app and the keys pressed so far (`Space t`). A key
goes down a group, or picks a command, which goes to the app as one
entered on the command line does, and the strip closes. Backspace goes up
a level, Esc closes, and a click on an entry is its key. An app-wide key
whose action is `hill.tree` names the key that opens the tree: the
sequences start with it, a click on its hint opens the tree, and the
command line's list and the help's Commands tab show each command's
sequence beside it (`Space t …` where a command is reached by several,
such as one for each of its choices). The tree's leaves must name the
app's commands and keys must not repeat at a level: what doesn't is left
out, and the strip says what was wrong. An app with no tree has none, so
micro's keys are as they were.

**A question** from the app, such as a new note's title, takes the strip's
line: Ret answers it, Esc cancels. When the strip is busy, what an app asks
for waits its turn.

**A tip from Claude** shows on the strip's line for a minute, and never
takes the focus from the app; the app's key for Claude, or a click, opens
the panel, with the tip on its tip line.

## Panes beside the app

An app can run programs of its own beside it, each in a pane: one in the
middle, the view (palace's preview), and one on the right, for Claude
(palace's Claude pane). The app then takes the left of the window, at its
full height, and the strip goes under the view and the Claude pane, so the
panel grows there and the app keeps its rows. palace's calendar, under its
list, stays where it is.

```
┌─────────┬──────────────┬───────────┐
│ app     │ view         │ Claude    │
│         │              │           │
│         ├──────────────┴───────────┤
│         │ strip                    │
└─────────┴──────────────────────────┘
```

The widths are hill-ops's Layout settings, each a share of the window's width:
Layout → App width (25% to start with) and Layout → Claude width (30%; off
hides the Claude pane, which keeps running); the view takes the rest. When
the window changes size, such as on another monitor, they apply again at
once, so the panes keep their shares. Dragging a border with the mouse
sets them too: once you let go, the width snaps to the nearest step (5% of
the window) and is kept, as if set in the panel. Dragging the top of the
open panel sets Layout → Panel height the same way. Each instance keeps
its own Layout (see Instances).

An app can run several programs for one place and show one at a time,
such as a Claude session for each of palace's work items. hill-ops knows them
by name: one of another name takes the place, and the one that was there
keeps running out of sight; asking for that name again brings it back as
it was. Asking for a name with another command line starts it again. The
app stops one with `panes.end`, out of sight or shown; one out of sight
that exits on its own is forgotten without a note.

An app can also run a program over its side panes until it exits, such as
palace's editor: it takes the view's and the Claude pane's place, and gets
the focus. They keep running out of sight, and come back as they were when
it exits; the app hears its exit status. It's told how wide the Claude pane
was (`$HILL_CLAUDE_COLUMNS`), so an editor can put a chat of its own there,
as micro-claude does. Its pane is lighter while it has the focus, as the
panel is, where the program leaves its background to the terminal, as
micro's simple colorscheme does.

The strip follows the focus: it shows the keys of the app in the pane you
click, its own or one it runs beside it, and the panel and the help are
about that app. A side program that exits on its own leaves its place to
the others, with a note in the strip.

A program in the window can take the focus when the mouse moves over it,
for focus that follows the mouse, as palace's panes do, with hill-client's
`take_focus()`, and lazily with its `Hover`: where a key took the focus
away, a nudge of the mouse resting there doesn't take it back. The open
panel follows the mouse too, by the same rule: moving the mouse over it
gives it the focus, and moving on over such a program gives the focus to
that program, the panel staying open. So does a program that an app runs
beside it or over its side panes, if it uses the mouse, such as micro, or
`claude` in its fullscreen mode, though it doesn't know about hill-ops and
micro can't see the mouse move: hill-ops runs it on a terminal of its own and
asks tmux for the mouse's moves on its behalf, keeping to itself what the
program didn't ask for. One that doesn't use the mouse, such as a shell,
or `claude` otherwise, still takes the focus with a click, and keeps
tmux's own selection and scrolling. The strip's
line never takes the focus that way, and the help, a question and the
panel opened as the command line keep it while they're open, since they
close when it goes: a click elsewhere or Esc gives it back.

## The channel

Apps talk to hill-ops over a local socket whose path is in `$HILL_SOCKET`.
Every program started inside hill-ops inherits it, so micro started from palace
joins too. Messages are JSON-RPC notifications, one per line:

| From | Message | Meaning |
|---|---|---|
| app | `hello {app, profile, keys, commands, help, docs, tree, pane}` | I'm running: my name, my profile's path, my app-wide keys as `[key, label, action]`, my commands as `[name, args, what it does, choices?]`, my help as `[title, [[key, what it does], ...]]` sections, my README's path, for Claude, my key tree, its nodes `[key, label, line]` for a command (`line` is what `command` sends back) or `[key, label, [nodes...]]` for a group, and the tmux pane I run in (`$TMUX_PANE`) |
| app | `settings.open {group}` | Show the settings in the panel, on this group |
| app | `help.open {section}` | Show my help, on this section |
| app | `command.open {text}` | Open the command line, the panel's line with `:` and this text typed |
| app | `tree.open {}` | Show my key tree |
| app | `ask {id, question, value}` | Ask this on the strip's line, with this answer typed |
| app | `claude.open {group}` | My key for Claude was pressed: open the panel on this group, on the line to ask Claude |
| app | `error {what}` | This went wrong, for the event log: a short phrase in my own words |
| app | `panes {view, claude}` | Run these beside me (see Panes beside the app), each `{name, argv, cwd}`, or null for none; one already running stays, one that changed starts again, one of another name takes the place and the one there keeps running out of sight |
| app | `panes.end {names}` | Stop the programs of these names that run beside me, out of sight or shown |
| app | `over {name, argv, cwd}` | Run this over my side panes until it exits, such as an editor |
| app | `overview {title, where, verb, empty, lines}` | My overview, for the panel's first tab, replacing the last one: its tab's name (`Overview`), what the lines are, what picking one does (`open`), what shows while there are none, and its lines as `{id, text, help}`, the text a string or `[text, style]` spans in Rich's styles; a line `{separator: true}` is a rule across the panel instead, which the cursor steps over and Claude doesn't see |
| hill | `settings.changed {group, key, value}` | One of your settings changed: apply it |
| hill | `run {action}` | One of your hints was clicked: run its action |
| hill | `command {line}` | This was entered on the command line, or picked in the key tree: run it |
| hill | `answer {id, value}` | The answer to your question, or null if it was cancelled |
| hill | `over.done {status}` | What you ran over your side panes has exited, with this status |
| hill | `overview.pick {id}` | This line of your overview was picked |

An app shows its own errors, so the strip only logs an `error`. Its `what`
is the app's words, such as "push failed", with nothing of yours in it: not
a note's title, a path or another program's message, which may name either.

What an app sends counts once it has said hello. The app in the pane with
the focus owns the strip: the one whose pane it is, or that runs it beside
it. Apps in the same pane form a stack: the last to say hello owns the
strip, and when it leaves, the one before it is back. `settings.changed`
goes only to the app whose profile holds the group. A hint whose action is `hill.settings`,
`hill.help`, `hill.command`, `hill.tree` or `hill.claude` opens that in the strip without
going through the app. Python apps can use [hill-client](packages/hill-client/README.md).

An app that can't open a socket, such as a micro plugin written in Lua,
starts `hill-ops connect` and writes and reads the same lines on its stdin and
stdout. If the strip restarts, the bridge connects again and repeats the
app's last hello; it exits when its stdin closes, so it never outlives the
app.

## Profiles

An app describes its settings in a TOML profile, and names it when it says
hello. hill-ops keeps a link to each profile in `~/.config/hill/profiles/`
(`$HILL_CONFIG_HOME`), so an app's settings stay reachable while it isn't
running; a profile that has gone or can't be read is skipped, with a note.

```toml
app = "palace"

[[groups]]
id = "list"                      # the group an app asks for
name = "List"                    # its tab
where = "palace · applies right away"
file = "${PALACE_CONFIG_HOME:-${XDG_CONFIG_HOME:-~/.config}/palace}/settings.json"

  [[groups.settings]]
  key = ["notes", "sort"]        # where the value sits in the file
  label = "Sort by"
  help = "Order of the notes list."
  choices = ["title", "modified"]
  default = "title"
  names = [["modified", "newest first"]]   # for values that aren't clear on their own
```

- `file` is JSON unless it ends in `.toml` (or the group says `format =
  "toml"`). `${NAME}` and `${NAME:-default}` in it come from the
  environment, and a leading `~` is the home folder. A JSON file keeps only
  values that differ from the default, unless the group has `keep_defaults
  = true`.
- `where` says where the settings go and when they apply; it defaults to the
  file's path.
- `instance = true` on a setting keeps it per instance, inside hill-ops: in
  `APP.json` in the instance's folder (see Instances), and in `file` too,
  where a new instance takes it from. palace's Calendar and Preview are.
- `choices = "textual-themes"` offers Textual's themes. The first such
  setting is the app's theme, and hill-ops takes it too: the strip, the panel,
  the help and the command line are in the theme of the app that owns the
  strip, as it says hello, takes the focus or changes the setting in the
  panel. An app with none, such as micro, leaves the theme of the last app
  that has one; with none at all, hill-ops is in Textual's own (`textual-dark`).
- `work = "work"` names the app's work folder, where Claude in the strip
  can file work items (see Claude in the strip). A relative path is taken
  from the profile's real folder, the one its link in the profiles folder
  points to, so a profile in a clone names the clone's `work/` on anyone's
  machine; `~` and variables work as in `file`. A folder that isn't there
  is no work folder: an installed app whose items aren't with it names
  none. hill-ops's own is its repo's `work/` (`$HILL_WORK_DIR`), when it runs
  from its repo. A folder is listed once, under the first project with it,
  hill-ops's own first.

## The reference

`hill-ops docs APP` prints APP's reference in Markdown, generated from what it
declares, as Helix's book pages are generated from its code: its settings
by group, each with its `:set` name, help, choices and default, from its
profile; its commands, each with its keys in the key tree, and its keys on the strip and in each section of
its help, from its last hello (an app that never said hello has only its
settings). `hill-ops docs` alone prints hill-ops's own: Layout and Hill, `:set`
and `:toggle`, and the strip's keys. It's what the panel's line searches,
so what's documented is what can be found, and nothing is written twice.

What isn't documented, a setting with no `help`, a command that doesn't
say what it does or a key with no label, is listed on stderr. hill-ops
commits its own as [docs/reference.md](docs/reference.md), and a test
fails when it's out of date or something in it is undocumented; an app
can do the same.

## The event log

hill-ops keeps a log of each session for Claude, which reads it in the strip
to help with settings, keys and commands. It holds what apps report and
what the strip does for them, never the text on screen or what you type: a
command keeps its name, and its argument only when that's one of the
choices the command lists; a question from the app keeps neither its
wording, which can hold a note's title, nor its answer; a question you ask
Claude keeps neither, nor Claude's answer.

Each line is a JSON object with the `time`, the `event`, the `app` it
concerns, if any, and the event's own fields:

| Event | Fields | When |
|---|---|---|
| `start` | `command`, `cols`, `rows`, `instance` | hill-ops started a program: its name, not its arguments (`python -m hill` is hill), in a window this size, in this instance |
| `hello`, `bye` | | an app joined the channel, or left it |
| `run` | `action` | a hint on the strip was clicked |
| `settings.open` | `group` | the panel opened on the settings, on this group |
| `settings.changed` | `group`, `key`, `value`, `by` | a setting changed; `app` is the app whose setting it is, or `hill` (hill-ops's own); `by` is `drag` when you dragged a border to change it |
| `help.open` | `section` | the help opened, on this section |
| `command.open` | | the panel opened as the command line |
| `tree.open` | | the key tree opened |
| `tree` | `name`, `choice` | a command was picked in the key tree, kept as `command` keeps it |
| `command` | `name`, `choice` | a command was entered, or Claude's offer of one taken (after `claude.take`): its name, or null if the app has no such command, and `choice` when its argument is one of the command's choices |
| `ask`, `answer` | `answered` | the app asked a question, then it was answered or cancelled |
| `claude.open` | `about`, `group` | the panel opened on the line to ask Claude (`about` null), on this group; or you asked Claude for a tip, in the panel (`settings`) or in the help (`help`) |
| `claude.ask` | | you asked Claude a question in the panel: never what, nor its answer |
| `claude.take` | | you took what Claude offered: the setting changed or the help opened is next |
| `tip` | `text`, `setting`, `command`, `help` | Claude offered a tip, with a setting to change, a command to run or a section of the help to open |
| `close` | | what the strip showed closed, back to its line or to the panel |
| `problem` | `text` | the strip showed a problem, such as a profile it can't read |
| `error` | `what` | the app reported an error (see The channel) |
| `panes` | `view`, `claude` | the app runs programs beside it: their names, or null |
| `panes.end` | `names` | the app stops programs it ran beside it: their names |
| `over` | `name` | the app runs a program over its side panes: its name, not its arguments |
| `over.done` | `status` | that program exited, with this status |
| `overview.pick` | | a line of the app's overview was picked: never which |
| `search` | `kind` | a match was taken on the panel's line: a `setting`, `command`, `key` or `help` section, and `app` is whose; never what was typed |
| `work.filed` | `project` | a work item Claude offered was filed, in this project: never its title |
| `resize` | `cols`, `rows` | the window changed size, and kept it for two seconds, such as on another monitor |
| `end` | `status` | the app exited, with this status |

```json
{"time": "2026-09-30T16:05:12+02:00", "event": "settings.changed", "app": "palace", "group": "list", "key": ["notes", "sort"], "value": "modified"}
```

Each session writes a file of its own in `~/.local/state/hill/events/`
(`$HILL_STATE_HOME`, else `$XDG_STATE_HOME/hill`), named for when it
started, such as `2026-09-30T16-05-02-4242.jsonl`. A session starting
removes the logs of sessions that ended more than 30 days ago. Hill → Event
log turns the log off; the files already there stay until then, or until
you delete them.

## Claude in the strip

Claude is the support desk for hill-ops and the apps on it, and steers the app
for you: it offers tips, answers what you ask, says where a setting, key,
command or help section is and takes you there, and offers the app's
commands and settings that do what you want. When what you want doesn't
exist yet, it says so, names the open work item that covers it if there is
one, and else offers to file one. It reads the event log of your last few
sessions, the running app's keys, commands, help and README, the keys,
commands and help of the other apps hill-ops knows, as they last said hello,
the lines of its overview with their ids (palace's work items that want
something, so Claude can offer a command on one), the open work items of
the projects it can file in (number, status, title and the start of the
Goal), and every setting with its value and choices. It has no tools, so real work on what's in the app,
such as a note or a work item, is for the Claude the app runs beside it
(palace's Claude pane); Claude in the strip says so, and offers what gets
you there.

- **Questions:** the app's key for Claude (Alt-c in palace) opens the
  panel on its line. Type anything but a command (which starts with `:`):
  how to do something, what a setting is for, or something you want done,
  and Ret: Claude answers in a few lines, above the tip. The next
  question takes their place, and they go when the panel closes; close it
  before an answer comes, and the strip's line says when it has. The
  conversation lasts the session, so you can ask about an earlier answer,
  or a tip.
- **Offers:** an answer or a tip may offer a setting to change, one of the
  app's commands to run, or a section of the help to open, and an answer a
  setting to show or a work item to file; Ret on an empty line takes it (a
  click on a tip's offer too). hill-ops keeps a setting only if it's one of
  that setting's own choices, and the panel shows it changed; a command
  only if it's one the app listed, and it runs as if typed after `:`, the
  panel staying open. A setting to show opens selected in the panel,
  unchanged, so "where is X" gets "here".
- **Work items:** Claude can file in hill-ops and in each app whose profile
  names a work folder (see Profiles), so in hill-ops, palace and micro-claude.
  It offers one only for what doesn't exist and isn't an open item already:
  the app's project when it's about the app, hill-ops's when it's about the
  strip, the panel, the layout or Claude. Taken, hill-ops writes
  `work/NNN-slug.md`, numbered after the last, with status `open`, the Goal
  and Done when Claude drafted, and a note saying it came from the strip,
  on which app; adds its line to the folder's `README.md`; and says where on
  the panel. It never commits: the item's own session, or you, does.
- **Tips** come from what you do: a key for something you reach by
  clicking, a setting that suits what you keep changing, help on something
  you look up again and again, a layout that suits the window's new size. The panel's tip line always has one: Claude
  serves it when the panel first opens, and it stays until you ask for
  another, with a click on it or Alt-c, about the setting selected. Claude
  also offers one a session on its own, two minutes after the app starts,
  if there's none yet, on the strip's line, where it stays a minute and
  never takes the focus; and one about the layout after the window's
  width or height changed by a fifth or more, such as on another monitor
  or maximized (at most one every ten minutes), which may offer a Layout
  setting.
- In the strip's help, Alt-c asks for a tip about the section shown; it
  shows under it, and on the panel's tip line.
- Hill → Claude's tips offers them always (the default), only when you
  ask, or never; you can ask questions either way. Hill → Claude's model
  picks sonnet, haiku or opus. Tips need the event log (Hill → Event log);
  questions don't, though Claude knows less without it.

Claude runs as [`claude-agent-acp`](https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp),
over ACP as micro-claude does, on your Claude Code login, started for the
first tip or question and kept for the session. Its first message holds
all of the above, and each one after it only what's new: the events since,
the settings changed, an app that said hello, an overview that changed,
work items that changed. It
gets what the event log holds, what the apps said in hello, the README the
running one names, the app's overview, the open work items and the
settings, never the text on screen; your questions go to Claude, never to the log. It
runs with no tools and none of your Claude Code settings, hooks or
CLAUDE.md, isn't saved among your Claude Code sessions, and hill-ops turns down
anything it asks for. A tip is a JSON object, and an answer's offer a JSON
object on its last line, which hill-ops checks before offering anything. An
answer can take from a few seconds to half a minute; haiku is quicker.

```bash
npm install -g @agentclientprotocol/claude-agent-acp
```

## Parts

hill-ops is built from small packages in `packages/`, each usable on its own:

| Package | What it is |
|---|---|
| `keyline` | A one-line, clickable key hint strip for Textual apps |
| `settings-panel` | The game-style settings panel, with JSON and TOML stores |
| `hill-client` | Joins hill-ops's channel from a Python app |
| `acp-client` | Talks to an ACP agent, such as Claude's, from asyncio |

keyline and settings-panel came from palace, with their history.

## Developing

```bash
uv run pytest     # hill-ops and all packages
```

`HILL_CONFIG_HOME` (hill-ops's settings and profiles), `HILL_STATE_HOME` (the
event log), `HILL_RUNTIME_DIR` (each session's socket) and `HILL_TMUX_NAME`
(the tmux server's name) point hill-ops elsewhere; the tests use them so they
never touch your configs, your event log or your tmux. `HILL_CLAUDE_AGENT`
runs another agent for Claude in the strip, such as the tests' stand-in,
`tests/fake_agent.py`. The end-to-end test runs hill-ops for real in a pseudo-terminal, with a
stand-in app, and the layout's tests lay out a tmux server of their own;
both need tmux.

This README and the packages' own say what hill-ops does, and change in the same
commit as the code. `tests/test_docs.py` fails when they miss one of hill-ops's
commands, or part of a package's API: a name it exports, or an option or
method of one of its classes. It checks names only. Why hill-ops is built this way is in
[DESIGN.md](DESIGN.md).

Work in progress is in [work items](work/README.md), one file each.
