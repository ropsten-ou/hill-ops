# Design

Why hill-ops is built the way it is. What it does is in the [README](README.md).

The idea: split what helps you use an app (settings, key hints, help, a
command line) from the app's main work, as nano and micro keep their key
menu and command bar at the bottom, and give it a place of its own: a strip
under the app, where Claude nudges and teaches while the work stays above.

## Principles

- **The app stays the app.** hill runs apps as they are, each in a terminal
  of its own, and never draws over them; when the strip grows, the app gets
  fewer rows and redraws.
- **Programs own their settings.** An app describes its settings in a
  profile; hill writes values straight into the program's own config file
  and tells the app, which applies them.
- **Keys by scope.** Keys for one pane stay in the app, next to what they
  act on; keys for the whole app, settings, help and the command line live
  in hill's strip.
- **A thin strip that zooms.** The strip is always there but small, and
  grows only while it's in use.
- **A small channel.** One JSON object per line over a local socket, whose
  path every program started inside hill inherits. An app that doesn't use
  it still runs, without the strip knowing what it does.
- **tmux underneath, out of sight.** hill runs its own tmux server with its
  own configuration; your tmux and `~/.tmux.conf` stay untouched.
- **Tips wait.** Claude's tips sit on the strip's line until you act on
  them or they go; nothing takes the focus from the app unless you ask.
- **Record events, not screens.** What hill keeps for Claude is what apps
  report and what the strip does for them (commands, settings changed, help
  opened, errors), never the text on screen or what you type.
- **Tests never touch real configs or your tmux.**
- **The docs change with the code.** A change updates the README that
  says what it does, in the same commit.

## Decisions

Oldest first; new ones go at the end.

**2026-09-30 · hill splits off palace's settings panel.** palace's settings
rose over its bottom third, and micro reached them by running `palace
settings` full-screen. They become a program of their own that runs any
app above a strip, like nano's key lines and command bar split from the
text, with the two talking over a channel.

**2026-09-30 · tmux underneath.** Also considered: a terminal widget inside
one Textual program (ghostty-textual, at v0.0.4, not on Termux, keys to
translate), a pass-through wrapper like ptyline (fast, but the strip can't
use Textual) and Zellij (more of its own interface to tame). tmux is solid,
quick to build on and runs on macOS, Linux and Termux; since apps only see
the channel, the engine can change later.

**2026-09-30 · Key hints split by scope.** Keys for one pane stay under that
pane, where palace already shows them; the strip shows the keys for the
whole app.

**2026-09-30 · A thin strip that zooms.** Always visible, it grows to the
panel height for settings and shrinks back. tmux 3.3a draws a border row
between panes, so the strip takes two rows.

**2026-09-30 · Record events only.** For the later step where Claude helps
from the strip: what apps report, with no note text or screen contents.

**2026-09-30 · Profiles in TOML.** Apps are installed apart (their own tool
environments, or Lua in micro), so hill can't import their code; each
describes its settings in a TOML file, which hill keeps a link to once the
app has said hello, so its settings stay reachable when it isn't running.

**2026-09-30 · A runner, not tmux hooks, ends the session.** The app runs
under a small runner that keeps what the app left on screen and its exit
status, then detaches with `-E`, which prints no "[detached]" line. A
`pane-died` hook would have written "Pane is dead" into the pane first.

**2026-09-30 · The command line, questions and help in the strip.** The
second half of splitting what helps you use an app from its work. An app
lists its commands and its help in hello; the command line narrows the
commands as you type and sends the line back for the app to run, a
question takes the strip's line, and help grows the strip like the
settings. In micro, Ctrl-e's command line moves to the strip while Ctrl-g
keeps micro's own, much fuller help pages. palace's prompts for a new note
and a rename move too; search stays at the top of its list, where it
filters as you type, next to what it acts on. What an app asks for while
the strip is busy waits its turn.

**2026-09-30 · An event log per session, kept a month.** For Claude in the
strip, the next step: it will look for patterns across sessions, such as a
setting changed back and forth or the same help opened again, so each
session keeps a JSONL file for 30 days, and Hill → Event log turns it off.
It keeps the apps' own words only: a command's name, and its argument when
it's one of the command's choices, since apps list their own vocabulary
there (an app that listed note titles as choices would need to say so); a
question's wording stays out, as palace's rename question holds the note's
title. The host picks the file and writes the session's start and end, the
strip the rest. Errors from inside apps come next, with a message of their
own.

**2026-09-30 · Apps report their errors, for the log.** Claude can help
with a push that keeps failing or an agent that won't start, but only the
app knows it went wrong. An app already shows its errors, so `error {what}`
only goes in the event log. `what` is a short phrase in the app's words,
with nothing of yours: an error's own text, such as git's, can name a note
or a path. Like every message, it counts once the app has said hello, so
palace, whose sync runs after it has quit, says hello again to report a
failed commit or push.

**2026-09-30 · Claude's tips first.** Claude in the strip starts with tips:
from the event log, one short tip on the strip's line, once a session and
whenever you ask with Alt-c; asking Claude questions comes later. A tip
never takes the focus, since the app's keys are yours while you work. One
with a setting changes it only when you say yes, and hill keeps a setting
only if it's one of that setting's own choices, so Claude can't set
anything the panel couldn't.

**2026-09-30 · Claude runs bare.** The strip's Claude is claude-agent-acp
with no tools, its own system prompt and none of your Claude Code settings,
hooks or CLAUDE.md, and its sessions aren't saved with yours: it needs only
what hill gives it, a hook of yours shouldn't run for every tip, and tips
shouldn't crowd your list of sessions. It answers in JSON, which hill checks; hill
turns down anything it asks for, and says its errors in hill's own words,
since what the strip shows goes in the event log.

**2026-10-01 · Asking Claude in the strip.** Claude's view grows the strip
like the help: the session's conversation, tips included, above a line to
ask on. Alt-c, which acted on the tip on the line, opens it, with the tip
there and its offer one ⏎ away, so a tip you don't follow is a question
away from an explanation. An answer is a few lines of plain text that may
end with one offer, a setting or a section of the help, which hill checks
as it checks a tip's. Claude still runs bare: an MCP server of hill's own
would let it change settings, open help and run commands in one answer,
through ACP's permission requests, but adds a process and a way for Claude
to act, for little that one offer doesn't already do. Claude also reads the
README an app names in hello, for the how-to questions its keys and
settings don't answer. Tips and answers share one session; its first
message tells Claude everything, and each later one only what's new, so a
conversation doesn't resend the log with every question. What you ask
stays out of the log, like everything you type.

**2026-10-01 · Claude's view stays until Esc.** An answer takes a few
seconds, and you read it while you work, so the view stays open when you
go back to the app, and the rest of the strip (the settings, the help, the
command line, a question) opens over it and goes back to it. A click on the
grown strip, or the app's key for Claude, gives it the focus again; a tip
is a click away, on tip. Switching to another window no longer closes
anything in the strip: tmux tells the strip it lost the focus either way,
so hill asks tmux whether the app's pane has it, which only a click in the
app gives.

**2026-10-01 · One panel for the settings and Claude.** The app's settings
key and its key for Claude open the same panel: the settings, with Claude's
tip line and a line to ask on under them. A tip or an answer is often about
a setting, and the panel shows both, where Claude's view hid the settings it
talked about. It's mostly settings: their group tabs sit above them, since
they name what's below, and the tiles take only the rows they need, so a
setting's description sits right under them; Claude's lines take the rest. The tip line always has a tip, served when the panel first opens
and kept until you ask for another, so the panel costs one call to Claude a
session unless you ask. Your question and its answer show above the tip, the
latest only, and go when the panel closes; with little room, the tiles and
the answer scroll rather than the strip growing further. The panel stays
until Esc, as Claude's view did, so what you read and change stays in sight
while you try it in the app.

**2026-10-01 · Panes beside the app.** Pierre wanted palace, its preview
and Claude side by side, the strip (the settings, with Claude's lines)
under the preview and Claude only, and palace's calendar under its list.
tmux panes are rectangles, so the preview can't stay inside palace: an app
now runs programs of its own beside it, and hill, whose layout it is, lays
them out, the app on the left at the window's full height and the strip
under the side panes. The widths are settings, a share of the window each,
and a tmux hook applies them again whenever the window changes size, since
tmux shares a new size out unevenly: shrinking the window took it all from
the first pane. A program can run over the side panes until it exits, such
as an editor; they wait out of sight, in windows of their own, so the
Claude pane keeps its conversation, and come back as they were. The strip
shows the keys of the app whose pane has the focus, and follows the window
through tmux's control mode: hooks with `wait-for` were tried first, but
two signals with nobody waiting cancel each other out. Several such units
side by side, once the window is large enough, may come later.

**2026-10-01 · A dragged border sets a width.** Pierre wanted to size the
panes with the mouse as well as in the settings. tmux resizes them as the
border moves; when it's let go, hill snaps the width to the nearest of the
setting's steps and saves it, so a setting still steps through a fixed
list, the panel shows what the mouse did, and the event log has it, marked
as a drag, for Claude's tips. A drag of the app's border also takes columns
from the Claude pane, so the app's width is the one that counts.

**2026-10-01 · The window's size in the log, and a tip when it changes.**
Pierre moves hill's window between monitors and maximizes it, and wants
Claude to see that, with the settings he changes, to suggest a layout
that suits. The event log has the window's size at start and each new size
that lasts two seconds, so a drag of the window's edge doesn't fill it;
when the width or height changed by a fifth or more, Claude offers a tip
on the strip's line, which may offer a Layout setting, at most every ten
minutes, as tips wait. The panes keep their shares meanwhile; laying out
several units side by side on a large window is for later.

**2026-10-02 · Focus that follows the mouse is the programs' own.**
Pierre wanted the focus to follow the mouse across palace's panes. tmux
can't bind a mouse move, and its own `focus-follows-mouse` selects any pane
the mouse moves over: the strip's line, which a click never gives the focus,
and, while the strip has the focus, the pane next to it, which closes the
command line or a question as soon as the mouse moves. So a program that
wants the focus to follow the mouse takes it itself when the mouse moves
over it, with hill-client's `take_focus()`, which leaves the strip alone
while it has the focus. micro can't see the mouse move, so it still takes
the focus with a click.

**2026-10-02 · The panel follows the mouse, and shows the focus.** With
the focus following the mouse across palace's panes, Pierre found it
didn't visibly go to the settings: the panel looked the same with the
focus as without it, and only a click gave it the focus. The panel stays
until Esc, so it's a pane like palace's: the mouse moving over it gives it
the focus, by palace's lazy rule, which moved to hill-client (`Hover`) for
both to share, and it's lighter, its keys lit, while it has it. For the
mouse to take the focus back out, `take_focus()` now refuses only while
the strip keeps the focus, which the strip says in a tmux option,
`@hill-keep`: for the help, the command line and a question, which close
when the focus goes, and not for the panel alone. Typing in the panel is
as safe as in any pane: a nudge of the mouse resting over the app doesn't
take the focus, and what's typed stays on the line when a move does.

**2026-10-02 · A program over the side panes follows the mouse.** Pierre
wanted micro to follow the mouse as the panes it covers do. micro asks
tmux for clicks and drags only, so tmux never tells its pane that the
mouse moves, and no plugin of micro's could see it; tmux can't bind a
move, and its own focus-follows-mouse takes the focus from the strip. So
hill runs a program over the side panes through a relay of its own, on a
terminal of its own: while the program uses the mouse, the relay asks
tmux for the moves too, keeps to itself what the program didn't ask for,
and takes the focus as the mouse moves over the pane, by hill-client's
lazy rule; everything else passes as it comes. A program that doesn't use
the mouse, such as a shell, is left as it is: asking for the moves would
send it the mouse, and tmux's selection and scrolling would stop working
there. The pane is lighter while it has the focus, with tmux's pane
styles in the panel's colors, which shows where the program leaves its
background to the terminal, as micro's simple colorscheme does. micro's
own splits, the editor and its chat, still take a click.

**2026-10-03 · Ret, not ⏎.** The strip's key lines, the settings panel and
the help write the Return key as Ret, the way they write Esc and Tab. Menlo
has ⏎, but at the 8 or 9 points Pierre's terminal uses it's too thin to
make out; ⎆, the enter symbol, isn't in Menlo, so macOS takes it from Apple
Symbols, half a cell too wide. A word reads in any font, at any size.

**2026-10-04 · The line to ask takes the focus with the panel.** Pierre
wanted to type to Claude as soon as the panel had the focus, as in palace's
Claude pane. The panel's settings have keys of their own (the arrows,
Space, Ret, `v`, the digits), so the line can't simply always have it:
when the panel gets the focus back, the line has it, unless you chose the
settings since it last had it, by the app's key for the settings, ↑ from
the line, or a click on a setting. Typing ahead from the settings into the
line was the other way, but `v`, the digits and Space would start a
question only sometimes. A click on Claude's answer, to scroll it, no
longer takes the focus.

**2026-10-05 · One line for commands and for Claude.** The panel's line
to ask Claude and the command line become one: after `:` it's a command,
which the app runs; anything else goes to Claude, which may offer a
command back, taken with Ret as a setting is. A command line of its own,
a few rows tall, was quicker to open, but two lines in the strip meant
deciding first whether to ask or to command, and Claude couldn't hand
over what it suggested. So the app's `:` opens the panel on its line with
`:` typed, and closes it once the command has run, as the command line
did. Claude in the strip is now the one that steers the app: it answers
any request, not only questions about the app, and offers the command,
the setting or the help that does it. It still has no tools, so the work
itself, on a note or a work item, stays with the Claude the app runs
beside it; a command, like a setting, is an offer hill checks against
what the app listed, never something Claude runs.

**2026-10-05 · Claude in the strip reads the app's overview.** To steer
palace, Claude has to name what a command acts on, such as a work item,
and the event log never holds titles or paths. The overview already lists
what wants something, each line with an id, so hill tells Claude its
lines, ids, text and help, and again when it changes. That's titles going
to Claude, which the README already did; the log still keeps none, so the
rule stays the log's: what's written down never holds your text, while
what Claude is told in a session may.

**2026-10-06 · wrap is now hill.** "wrap" also meant the end of a
session: the `wrap` skill, "wrap up", palace wrapping an item's session
once it's pushed. A word that meant two things made instructions and notes
ambiguous. "Palace" comes from the Palatine Hill in Rome, so the hill is
what the palace stands on, as palace stands on hill. The name is short,
isn't a command on the machines it runs on, and no project used the word, so it can be
searched for. Everything that named the tool changes: the repo, the
command, `hill-client`, the `HILL_*` variables, the config, state and
runtime folders, tmux's names and the strip's own settings group. For a
while `wrap` still runs hill, with a warning, and the old config and state
folders move the first time hill runs. No `WRAP_*` fallback: nothing
outside the code sets them.

**2026-10-06 · hill runs from its own .venv.** A `uv tool install` copy
of the dependencies doesn't follow `uv.lock`: after the rename, palace's
copy still had wrap-client and failed to start. `~/.local/bin/hill` now
links to `bin/shim`, which runs `uv sync` and then the command from hill's
`.venv`, so a changed dependency is installed at the next start. It costs
about 30 ms when nothing has changed. uv rebuilds hill when
`pyproject.toml` changes, so a new command such as `wrap` also shows up in
`.venv/bin`.

**2026-10-06 · The old name goes.** Both machines run hill: their
folders, shims, `.venv`s and config moved the same day. So the `wrap`
command and the first-run move of `~/.config/wrap` and
`~/.local/state/wrap` are gone, as the rename planned (work item 003,
step 6). Keeping them would let a stale `wrap` call or an old folder
come back unnoticed. Without them, it fails loudly or starts fresh.

**2026-10-06 · hill takes the app's theme.** palace has a theme setting,
and the strip under it drew in Textual's default, so the window was in two
themes. hill follows the app rather than having a theme setting of its
own, so one choice paints the whole window (work item 002). The theme is
the profile's setting whose choices are `"textual-themes"`: hill reads its
value from the app's settings file, so the channel doesn't change and
neither do palace and micro. An app with no theme, such as micro beside
palace, leaves the theme as it is, so it doesn't flip as the focus moves.

**2026-10-06 · hill sets true colour in its tmux.** On a Linux box over ssh,
rose-pine looked much more saturated than in a local session: ssh doesn't
send `COLORTERM`, so Rich, which Textual draws with, picked 256 colours
and rounded the theme's muted ones to brighter ones, though tmux and
iTerm2 could show true colour (work item 001). hill's tmux.conf sets
`COLORTERM=truecolor` for every pane rather than each ssh config sending
it: one line covers every machine and every pane hill starts, and tmux
converts colours down for a terminal without true colour, so it's safe
there too. tmux 3.7 sets it itself; 3.4, on that Linux box, doesn't.

**2026-10-06 · Programs beside the app run through the relay too.** The
relay that lets micro follow the mouse (2026-10-02) now runs every program
an app puts beside it, as `hill _side`.
`claude` in its fullscreen mode asks for the mouse's moves itself but
doesn't know about hill, so without the relay only a click gave its pane
the focus (work item 006). A program that doesn't use the mouse is left as
it is, so a shell, or `claude` otherwise, keeps tmux's selection and
scrolling and takes the focus with a click. The pane's process is now the
relay, not the program: palace records the pane's pid from tmux rather
than its own.

**2026-10-06 · Search is typing on the panel's line, over every app.**
Finding a setting meant knowing its tab (work item 008). Helix is the
model: its pickers search what the commands and options declare, so
nothing is documented twice and everything documented can be found. The
line was already "type what you want", so words typed there search, and
Ret with none picked asks Claude: the matches are what's known, Claude what
isn't, and no prefix stands between a question and its matches. Every app
hill knows is searched, the one on the strip first, not just the running
one, since the settings you're after are often another app's; hill keeps
each app's last hello so a closed app's commands and keys can be found
too. `:set` and `:toggle` are hill's own commands, named as Helix's, for
changing a setting without browsing for it; the panel's tiles stay the way
to browse.

**2026-10-06 · Claude in the strip files work items, though it has no
tools.** The strip's Claude is the support desk for hill and its apps (work
item 009): it says where things are and takes you there, and when what you
want doesn't exist, the next step is a work item, not an apology. It still
has no tools: it offers a draft, checked as a setting is (a project hill
can file in, a title, a Goal), and hill writes the file when you take it,
as it changes a setting. Only projects whose profile names a work folder,
and hill, so it never writes elsewhere under `~/projects`. It reads their
open items first, to name one rather than file a duplicate. hill writes
but never commits: the item is a note for its project's own session to
pick up, and palace's list shows it at once.

**2026-10-06 · The reference is generated from what apps declare.** Each
setting, command and key is written once, where it's declared, the profile
or the hello, and `hill docs` writes the reference from it (work item
010), as Helix's `cargo xtask docgen` does. It reads the same profiles and
kept hellos as the panel's search, so the reference and the search can't
disagree. A committed reference has a test that it's current, and
undocumented is a failure, as a missing name is in the READMEs' tests.
Values now aren't in it, only defaults, so it doesn't change as you use
the app.

**2026-10-06 · Commands get a key tree, declared by the app.** A command
that has only a name is typed, and completion helps, but typing isn't a
gesture you learn. ExNovo (Blair, Klassen et al., 2026) puts commands in
a tree of keys whose menu shows the keys that reach each one, so a novice
reading the menu is already pressing the expert's keys; which-key does the
same in Emacs (work item 012). hill shows the tree, as the strip is
hill's; the app declares it in its hello, letters and all, rather than a
group per command, as only the app can choose letters that mean
something, for each of a command's choices too. Leaves are command lines,
so a picked one reaches the app as a typed one does, and nothing new
comes back. hill doesn't bind the key that opens it, which must not clash
with what each pane takes: the app does, and names it by an app-wide key
whose action is `hill.tree`, so the sequences, the command line's list,
the help and the reference can all show the fast way beside the slow one.
ExNovo's digit keys and tones stay out: palace has the whole keyboard,
and makes no sounds.

**2026-10-07 · Each hill resumes an instance, as Claude resumes a session.**
Two hills on one machine shared one Layout, and palace one place, so a
border dragged in one moved the other's at its next start (work item
013). A name you give at start lasts but has to be remembered; `$HILL_RUN`,
as palace 034 used for Claude's state, ends with hill. So each hill runs
in an instance folder that outlasts it, written as it changes, and a plain
start takes up the last one of its program that isn't running: two hills
restarted one after the other each get their own, with nothing to name.
`--resume` picks one from a list labelled by the app (palace's last
projects), for when the order matters. A per-instance setting is also
written to the shared file, so a new instance starts from the last change
anywhere, as before. The choice and the claim aren't atomic: two hills
started in the same instant could take the same instance, which costs one
of them its layout, not its work.

**2026-10-07 · On PyPI hill is hill-ops, and `hill` is palace.** Published,
the name a newcomer installs is the one that matters most, and what they
want first is the notes screen, so palace takes `hill` and the strip
becomes `hill-ops` (work item 014). The commands follow the packages
(015): what you install is what you type, rather than `uv tool install
hill` giving a command called `palace`. The import stays `hill`, so no
code that imports it changes, and palace keeps `palace` as a second name
for its command. hill's own command is `hill-ops` everywhere, also on
Pierre's machines, so the docs name one command.

**2026-10-07 · The public repos are an export.** hill's history and its
work items name private projects and machines, so the public hill,
palace and micro-claude are built from the private repos' last commits
rather than published as they are (work item 014): the tree without its
work items and instructions for Claude, plus one made-up sample item from
the repo's `public/` folder and an MIT licence, committed to a public repo
with a history of its own, one commit per release. The export checks
the tree before it writes anything, and refuses when a private name, a
session id or a link to what was left out is still in it; the list of
names is in the export, which isn't exported. Work goes on in the private
repos.

**2026-10-07 · The folders, repos and imports take the PyPI names.** One
name in all four places, folder, GitHub repo, import and PyPI, before the
first export (work item 018): the strip is `hill-ops` (import `hill_ops`),
and the notes screen, palace until now, becomes `hill`. Only those move:
the strip's variables (`HILL_*`), its folders (`~/.config/hill`), its
message names (`hill.tree`), tmux options and the profiles' `hill.toml`
keep `hill`, and the notes screen's keep `palace`, since renaming both
would clash and need a config move on both machines for nothing a user
sees. Text written before this date says "hill" for the strip.

**2026-10-08 · The strip shows the time.** An app that fills the
screen hides the system's clock, and the strip is always there, so its
line ends with the time (public work item 001). It's the first thing to
go on a narrow strip, before any of the app's keys, since the keys are
what the strip is for. It follows the locale (LC_TIME) by default, with
12-hour and 24-hour as choices: macOS's locales all write 24-hour
whatever the system says, and reading the system's own setting there
would be a second way of doing it for one platform.

**2026-10-08 · A profile's work folder is relative to the profile.** A
profile named its work folder as `~/projects/...`, right only on the
machines that wrote it (work item 019). A relative `work` is now taken
from the profile's real folder, the one its link points to, as hill-ops
finds its own `work/` next to its code: a clone names its own items on
anyone's machine, and a folder that isn't there, as in an installed
package or a public clone without items, is no work folder. palace names
none, since what it lacks goes in hill-ops's, which hill-ops already lists.
