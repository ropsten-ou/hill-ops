"""Claude in the strip: what it's told, the tips it offers, and its answers
to your questions.

Claude gets what the event log holds (see events.py), what the app said in
hello, its README included, its overview and the settings, never the text on
screen; and
the questions you ask it, which the log leaves out. It runs with no tools of
its own and none of your Claude Code settings, hooks or CLAUDE.md. One
session holds both tips and answers, so you can ask about a tip; its first
message tells Claude everything, and each one after only what's new.

A tip is one short line, for the panel's tip line or the strip's line; an
answer is a few lines, which the panel shows above the tip. Either may offer
one setting to change, to one of that setting's own choices, one setting to
show without changing it, one of the app's commands to run, or one section
of the help to open; an answer may instead offer a work item to file, in a
project hill-ops can file in (see work.py), for what doesn't exist yet.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from settings_panel import Group, Setting, StoreError

from .events import log_dir
from .hub import Peer
from .index import AppDocs
from .work import Item, Project, clean_title, open_items

COMMAND = "claude-agent-acp"
"""Claude's agent, found on the PATH; $HILL_CLAUDE_AGENT names another."""
SESSIONS = 5
"""How many sessions of the log Claude reads first, this one included."""
EVENTS = 400
"""At most this many events at a time, the latest."""
TIP_LENGTH = 80
"""A longer tip is cut."""
DOCS_LENGTH = 40_000
"""How much of an app's README Claude reads, at most."""
DOCS_SUFFIXES = (".md", ".markdown", ".txt")
"""What a README can be: hill-ops reads text files only."""

SYSTEM = """\
You are Claude in hill-ops's strip, the line under a terminal app such as palace \
(a notes screen) or micro (an editor): the support desk for hill-ops and the \
apps on it. You help someone get more out of the app, and steer it for \
them: you offer tips, you answer what they ask, you say where a setting, \
key, command or help section is and take them there, and you offer the \
app's commands and settings that do what they want. When what they want \
doesn't exist yet, you say so, look for it among the open work items, and \
else offer to file one. The strip \
grows into a panel with the app's settings; under them is your tip, on a \
line of its own, and under that one line for both commands and you: what \
they type after ":" is one of the app's commands, which the app runs; \
anything else comes to you. Your answer shows above the tip. An app may run panes beside it, in columns: a \
view in the middle, such as palace's preview, and a Claude pane on the right, \
with the strip under those two; hill-ops's Layout settings set their widths.

hill-ops tells you the app's keys, commands and help, and its README when it has \
one; the keys, commands and help of the other apps it knows, as they last \
ran; the open work items of the projects you can file in (hill-ops, and apps \
whose profile names a work folder), each with its number, status, title \
and the start of its Goal; the lines of its overview, the panel's first tab, when it sends one \
(palace's: the work items that want something, each with its id); every \
setting, with its value and choices; and an event log of their \
last few sessions: what they did through the strip (settings, help, the \
command line, hints they clicked, questions they asked you, borders they \
dragged), the questions the app asked them, errors, the programs the app ran \
beside it, and the window's size as it changed (resize). The log never holds their own text: no \
titles, paths or what they typed; the overview and the README may. Your \
first message has all of it, and each message after that only what's new.

A message that starts with "Tip" asks for a tip, and says where it shows. \
Offer at most one, and only one that fits what they actually did, such as:
- a key for something they reach by clicking a hint or a longer way round,
- a setting that suits what they keep changing,
- help on something they look up again and again,
- a way round an error that keeps coming back,
- after the window changed size (another monitor, or maximized), a layout \
that suits it: Layout's widths, the Claude pane off or back, the panel's \
height; mind how many columns each pane has left.
- a command for something they do the long way round.
Don't repeat a tip you gave or one in the log (its tip events), and don't \
tell them what they already do.
Answer with one JSON object and nothing else:
{"tip": "the tip: at most 70 characters, plain and friendly", \
"setting": {"group": "<group id>", "key": ["<key>", ...], "value": <one of its choices>}, \
"command": "<one of the app's commands, with its arguments, without the colon>", \
"help": "<the title of a section of the app's help>"}
"setting" is a change they can accept with one key, "command" a command \
they can run with one key, "help" a section of the help to open; give one \
of them at most, or none. With nothing worth \
saying, answer {"tip": null}.

A message that starts with "Question" ends with what they typed: a \
question, or a request, about the app or not. Answer it in a few short \
lines of plain text, without Markdown, since it shows in a few rows of the \
panel, and name keys as the app's help does. Answer from what hill-ops tells \
you, and say so when that doesn't cover it. Act as a support desk: find \
what they're after and take them there; when it takes several steps, list \
them briefly and offer the first. Asked where something is, say where (the \
group and setting, the key and its pane, the command) and offer to show it. \
When what they ask for doesn't exist in the app or in hill-ops, say so plainly; \
if an open work item already covers it, name it (\"palace 012, waiting on \
your decision\") rather than filing another; else offer to file one, in \
the project it belongs to: the app's own when it's about the app, hill-ops's \
when it's about the strip, the panel, the layout or you. You have no tools: you can't \
read or change their files, and real work on what's in the app, such as \
writing a note or working on a work item, is for the Claude the app runs \
beside it, if any; say so, and offer what gets them there. When one of the \
app's commands, one setting changed or one section of the help would do \
what they want, end with a line holding just a JSON object, \
{"command": "<the command, with its arguments, without the colon>"}, \
{"setting": {"group": "<group id>", "key": ["<key>", ...], "value": <one of \
its choices>}}, {"show": {"group": "<group id>", "key": ["<key>", ...]}} (a \
setting to show them, unchanged), {"help": "<the title of a section of the \
app's help>"} or {"work": {"project": "<a project you can file in>", \
"title": "<what's wanted, in a few words>", "goal": "<what it should do, a \
few plain sentences>", "done_when": "<how to tell it's done, a sentence>"}}: \
hill-ops offers it, and they take it with one key. A work item is written to \
the project's work folder, not committed; offer one only for something \
that doesn't exist and isn't an open item already. Offer only commands the app \
lists, with arguments as its help describes them, and never a setting's \
value now."""


def command() -> list[str]:
    """The agent's command line: $HILL_CLAUDE_AGENT, else claude-agent-acp."""
    return shlex.split(os.environ.get("HILL_CLAUDE_AGENT") or COMMAND)


def meta(model: str) -> dict[str, Any]:
    """session/new's _meta for claude-agent-acp: Claude's system prompt and
    `model`, with no tools, none of your Claude Code settings, and nothing
    saved among your Claude Code sessions."""
    options = {"tools": [], "settingSources": [], "model": model, "persistSession": False}
    return {"systemPrompt": SYSTEM, "claudeCode": {"options": options}}


@dataclass(eq=False)
class Answer:
    """What Claude said, a tip or an answer to a question, and what it
    offers: a setting to change, a command to run, or a section of the help
    to open."""

    text: str
    setting: tuple[Group, Setting, Any] | None = None
    """A change to offer: the group, the setting and its new value."""
    help: str | None = None
    """A section of the app's help to open."""
    command: str | None = None
    """One of the app's commands to run, as typed after ":"."""
    show: tuple[Group, Setting] | None = None
    """A setting to show in the panel, unchanged."""
    work: Draft | None = None
    """A work item to file."""

    @property
    def offer(self) -> str | None:
        """What it offers, in words: "set List → Sort by to newest first"."""
        if self.setting is not None:
            group, setting, value = self.setting
            return f"set {group.name} → {setting.label} to {setting.show(value)}"
        if self.command is not None:
            return f"run :{self.command}"
        if self.help is not None:
            return f"open the help on {self.help}"
        if self.show is not None:
            group, setting = self.show
            return f"show {group.name} → {setting.label}"
        if self.work is not None:
            return f"file a {self.work.project.name} work item: {self.work.title}"
        return None


@dataclass(frozen=True)
class Draft:
    """A work item Claude offers to file: where, and what it says."""

    project: Project
    title: str
    goal: str
    done_when: str = ""


def read_sessions(current: Path | None) -> list[tuple[str, list[dict]]]:
    """The events of the last SESSIONS sessions, oldest first, as (name,
    events): the logs in log_dir(), then `current`, this session's; at most
    EVENTS of them, the latest."""
    earlier = [p for p in sorted(log_dir().glob("*.jsonl")) if p != current][-(SESSIONS - 1):]
    sessions = []
    for path in [*earlier, *([current] if current is not None else [])]:
        events = _read_log(path)
        if events is not None:
            sessions.append((path.stem, events))
    kept, room = [], EVENTS
    for name, events in reversed(sessions):
        if room <= 0:
            break
        events = events[-room:]
        kept.append((name, events))
        room -= len(events)
    return kept[::-1]


def _read_log(path: Path) -> list[dict] | None:
    """A session's events, or None if its log can't be read."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def read_docs(path: str | None) -> str | None:
    """An app's README, as its hello names it: at most DOCS_LENGTH
    characters of a text file, or None."""
    if not path or Path(path).suffix.lower() not in DOCS_SUFFIXES:
        return None
    try:
        with open(Path(path).expanduser(), encoding="utf-8", errors="replace") as f:
            text = f.read(DOCS_LENGTH + 1)
    except OSError:
        return None
    return text if len(text) <= DOCS_LENGTH else text[:DOCS_LENGTH] + "\n(cut here)"


class Context:
    """What Claude has been told in its session, so that each message tells
    it only what's new: the app on the strip (its hello and README), the
    settings, and the event log."""

    def __init__(self) -> None:
        self.hellos: set[str] = set()
        """The hellos told, each as JSON."""
        self.app: str | None = None
        """The app on the strip, as Claude last heard."""
        self.settings: dict[tuple, str] = {}
        """Each setting's value as told, as JSON, by (group, key)."""
        self.events = 0
        """How many of this session's events Claude has had."""
        self.overviews: dict[str, str] = {}
        """Each app's overview as told, as JSON, by the app."""
        self.others: dict[str, str] = {}
        """Each other app's keys, commands and help as told, as JSON."""
        self.work: dict[str, str] = {}
        """Each project's open work items as told, as JSON."""

    def news(
        self, peer: Peer | None, groups: list[Group], current: Path | None, fresh: bool,
        others: list[AppDocs] | None = None, projects: list[Project] | None = None,
    ) -> list[str]:
        """What to tell Claude: everything if its session is `fresh`, else
        what's changed since its last message. `peer` is the app on the
        strip, `current` this session's event log, `others` the other apps
        hill-ops knows and `projects` those Claude can file work items in."""
        if fresh:
            self.__init__()
        lines = [
            *self._app(peer), *self._others(peer, others or []), *self._overview(peer), *self._work(projects or []),
            *self._settings(groups), *self._events(current, fresh),
        ]
        return lines if fresh or not lines else ["Since your last message:", *lines]

    def _app(self, peer: Peer | None) -> list[str]:
        if peer is None:
            return []
        hello = json.dumps([peer.app, peer.keys, peer.commands, peer.help, peer.docs], default=str)
        if hello in self.hellos:
            lines = [] if peer.app == self.app else [f"\nThe app on the strip: {peer.app} again."]
        else:
            self.hellos.add(hello)
            lines = _hello(peer)
        self.app = peer.app
        return lines

    def _others(self, peer: Peer | None, others: list[AppDocs]) -> list[str]:
        lines = []
        for docs in others:
            if peer is not None and docs.app == peer.app:
                continue
            told = json.dumps([docs.keys, docs.commands, docs.help], default=str)
            if self.others.get(docs.app) == told:
                continue
            self.others[docs.app] = told
            lines += _docs_lines(docs)
        return lines

    def _work(self, projects: list[Project]) -> list[str]:
        lines = []
        for project in projects:
            items = open_items(project)
            told = json.dumps([[i.number, i.status, i.title, i.goal] for i in items])
            if self.work.get(project.name) == told:
                continue
            self.work[project.name] = told
            lines += _work_lines(project, items)
        return lines

    def _overview(self, peer: Peer | None) -> list[str]:
        if peer is None or peer.overview is None:
            return []
        told = json.dumps(peer.overview, sort_keys=True, default=str)
        if self.overviews.get(peer.app) == told:
            return []
        self.overviews[peer.app] = told
        return _overview_lines(peer.app, peer.overview)

    def _settings(self, groups: list[Group]) -> list[str]:
        lines = []
        for group in groups:
            rows = []
            for setting in group.settings:
                value, told = _value(group, setting), self.settings.get((group.id, setting.key))
                if value == told:
                    continue
                self.settings[(group.id, setting.key)] = value
                if told is None:
                    choices = ", ".join(json.dumps(c, default=str) for c in setting.options())
                    rows.append(f'- key {json.dumps(list(setting.key))} "{setting.label}" is {value}; choices {choices}: {setting.help}')
                else:
                    rows.append(f'- key {json.dumps(list(setting.key))} "{setting.label}" is now {value}')
            if rows:
                lines += [f'\nSettings group "{group.id}" ({group.name}):', *rows]
        return lines

    def _events(self, current: Path | None, fresh: bool) -> list[str]:
        mine = (_read_log(current) or []) if current is not None else []
        if fresh:
            self.events = len(mine)
            sessions = [(name, events) for name, events in read_sessions(current) if events]
            if not sessions:
                return ["\nThe event log: nothing yet."]
            lines = ["\nThe event log (time, event, app, details), oldest session first:"]
            for name, events in sessions:
                lines.append(f"# Session {name}")
                lines += [_event_line(e) for e in events]
            return lines
        new, self.events = mine[self.events:], len(mine)
        return ["\nNew in the event log:", *[_event_line(e) for e in new[-EVENTS:]]] if new else []


def _hello(peer: Peer) -> list[str]:
    """What the app said in hello, and its README."""
    lines = [f"\nThe app on the strip: {peer.app}"]
    if peer.keys:
        lines.append("Its keys for the whole app: " + "; ".join(f"{k[0]} {k[1]}".strip() for k in peer.keys))
    if peer.commands:
        lines.append("Its commands:")
        lines += [f"- {c[0]} {c[1] if len(c) > 1 else ''}: {c[2] if len(c) > 2 else ''}" for c in peer.commands]
    for title, entries in peer.help:
        lines.append(f"Its help, {title}:")
        lines += [f"- {e[0]}: {e[1]}" for e in entries if isinstance(e, list) and len(e) >= 2]
    if docs := read_docs(peer.docs):
        lines += ["Its README, up to (end of the README):", docs.rstrip(), "(end of the README)"]
    return lines


def _docs_lines(docs: AppDocs) -> list[str]:
    """Another app's keys, commands and help, as it last said hello: what
    Claude needs to say where something is, though it's not on the strip."""
    lines = [f"\nAnother app hill-ops knows, not on the strip now: {docs.app}"]
    if docs.keys:
        lines.append("Its keys for the whole app: " + "; ".join(f"{k[0]} {k[1]}".strip() for k in docs.keys if len(k) > 1))
    if docs.commands:
        lines.append("Its commands: " + "; ".join(
            f"{c[0]} {c[1] if len(c) > 1 else ''}".strip() + (f" ({c[2]})" if len(c) > 2 and c[2] else "") for c in docs.commands if c
        ))
    for section in docs.help:
        if len(section) > 1 and isinstance(section[1], list):
            lines.append(f"Its help, {section[0]}: " + "; ".join(f"{e[0]} {e[1]}" for e in section[1] if isinstance(e, list) and len(e) >= 2))
    return lines


def _work_lines(project: Project, items: list[Item]) -> list[str]:
    """A project's open work items, as Claude reads them."""
    lines = [f"\nOpen work items of {project.name}, which you can file in, as they are now:"]
    lines += [f"- {project.name} {i.number} ({i.status}): {i.title}. {i.goal}".rstrip() for i in items]
    if not items:
        lines.append("- none")
    return lines


def _overview_lines(app: str, overview: dict) -> list[str]:
    """An app's overview as Claude reads it: a line each, with its id."""
    title = str(overview.get("title") or "Overview")
    lines = [f"\n{app}'s {title}, the panel's first tab, as it is now ({overview.get('where') or ''}):"]
    for line in overview.get("lines") or []:
        if not isinstance(line, dict) or not line.get("id") or line.get("separator"):
            continue
        text = line.get("text")
        if isinstance(text, list):
            text = "".join(str(span[0]) for span in text if isinstance(span, list) and span)
        help = f" ({line['help']})" if line.get("help") else ""
        lines.append(f"- id {json.dumps(str(line['id']))}: {' '.join(str(text or '').split())}{help}")
    if len(lines) == 1:
        lines.append(f"- nothing: {overview.get('empty') or 'no lines'}")
    return lines


def _value(group: Group, setting: Setting) -> str:
    try:
        return json.dumps(group.value(setting), default=str)
    except (StoreError, OSError):
        return "unreadable"


def _event_line(event: dict) -> str:
    details = " ".join(
        f"{k}={json.dumps(v, ensure_ascii=False, default=str)}"
        for k, v in event.items() if k not in ("time", "event", "app") and v is not None
    )
    parts = (str(event.get("time") or "")[11:19], str(event.get("event") or ""), str(event.get("app") or ""), details)
    return " ".join(p for p in parts if p)


def tip_prompt(why: str | None, news: list[str]) -> str:
    """A message asking Claude for a tip, with what's new (Context.news):
    `why` says who asked and where, such as "asked in the help, on Map";
    None for the tip offered once a session on the strip's line."""
    first = f"Tip, {why}." if why else "Tip, unasked: the one offered once a session on the strip's line, a while after it starts."
    return "\n".join([first, *(news or ["Nothing new since your last message."])])


def question_prompt(question: str, news: list[str]) -> str:
    """A message with your question, after what's new (Context.news)."""
    return "\n".join(["Question.", *news, "", f"Their question: {question}"])


def parse_tip(text: str, groups: list[Group], sections: list[str], commands: list | None = None) -> Answer | None:
    """The tip in Claude's answer, or None. A setting is kept only if it's
    one of `groups`' with one of its own choices, other than its value now;
    a command only if it's one of `commands` (the app's, as in hello); a
    help section only if it's one of `sections`."""
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    if not isinstance(answer, dict) or not isinstance(answer.get("tip"), str) or not answer["tip"].strip():
        return None
    tip = " ".join(answer["tip"].split())
    if len(tip) > TIP_LENGTH:
        tip = tip[:TIP_LENGTH - 1].rstrip() + "…"
    return Answer(tip, *_offer(answer, groups, sections, commands, []))


def parse_answer(
    text: str, groups: list[Group], sections: list[str], commands: list | None = None, projects: list[Project] | None = None,
) -> Answer:
    """Claude's answer to a question, and what it offers, from the JSON
    object it may end with: a setting, a command, a section of the help or a
    setting to show, kept as parse_tip keeps them, or a work item to file in
    one of `projects`."""
    body, offer = _split(text)
    answer = Answer(body, *_offer(offer or {}, groups, sections, commands, projects or []))
    if not answer.text and answer.offer:
        answer.text = answer.offer[:1].upper() + answer.offer[1:] + "?"
    return answer


def shown(text: str) -> str:
    """An answer as it shows while it comes: what may be the start of the
    JSON object it ends with, or of a code fence around it, stays out."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("{", "```")):
            return "\n".join(lines[:i]).strip()
    return text.strip()


def _split(text: str) -> tuple[str, dict | None]:
    """An answer's text, and the JSON object it ends with, if any: on a line
    of its own or over its last few, in a code fence or not."""
    lines = text.rstrip().split("\n")
    end = len(lines)
    while end and lines[end - 1].strip().startswith("```"):
        end -= 1
    for start in range(end - 1, max(end - 13, -1), -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        try:
            found = json.loads("\n".join(lines[start:end]))
        except ValueError:
            continue
        if not isinstance(found, dict):
            break
        while start and lines[start - 1].strip().startswith("```"):
            start -= 1
        return "\n".join(lines[:start]).strip(), found
    return text.strip(), None


def _offer(
    found: dict, groups: list[Group], sections: list[str], commands: list | None, projects: list[Project],
) -> tuple[Any, ...]:
    """The one offer in what Claude answered, as (setting, help, command,
    show, work): a setting first, then a command, a section of the help, a
    setting to show, and a work item."""
    if (setting := _setting(found.get("setting"), groups)) is not None:
        return setting, None, None, None, None
    if (line := _command(found.get("command"), commands or [])) is not None:
        return None, None, line, None, None
    if (section := _section(found.get("help"), sections)) is not None:
        return None, section, None, None, None
    if (show := _shown_setting(found.get("show"), groups)) is not None:
        return None, None, None, show, None
    return None, None, None, None, _draft(found.get("work"), projects)


def _shown_setting(proposed: Any, groups: list[Group]) -> tuple[Group, Setting] | None:
    if not isinstance(proposed, dict):
        return None
    key = proposed.get("key")
    key = [key] if isinstance(key, str) else key
    for group in groups:
        for setting in group.settings if group.id == proposed.get("group") else []:
            if list(setting.key) == key:
                return group, setting
    return None


def _draft(proposed: Any, projects: list[Project]) -> Draft | None:
    """A work item Claude offers, if it names a project hill-ops can file in,
    with a title and a goal."""
    if not isinstance(proposed, dict):
        return None
    project = next((p for p in projects if p.name == proposed.get("project")), None)
    title, goal, done = (proposed.get(k) for k in ("title", "goal", "done_when"))
    if project is None or not isinstance(title, str) or not title.strip() or not isinstance(goal, str) or not goal.strip():
        return None
    return Draft(project, clean_title(title), goal.strip(), done.strip() if isinstance(done, str) else "")


def _command(proposed: Any, commands: list) -> str | None:
    """A command line Claude offers, if its first word names one of
    `commands`; without a leading ":", and on one line."""
    if not isinstance(proposed, str):
        return None
    line = " ".join(proposed.strip().removeprefix(":").split())
    names = {c[0] for c in commands if isinstance(c, (list, tuple)) and c and isinstance(c[0], str)}
    return line if line.partition(" ")[0] in names else None


def _setting(proposed: Any, groups: list[Group]) -> tuple[Group, Setting, Any] | None:
    if not isinstance(proposed, dict):
        return None
    key, value = proposed.get("key"), proposed.get("value")
    key = [key] if isinstance(key, str) else key
    for group in groups:
        for setting in group.settings if group.id == proposed.get("group") else []:
            if list(setting.key) != key:
                continue
            try:
                current = group.value(setting)
            except (StoreError, OSError):
                return None
            # True == 1 in Python: a value must be one of the choices, type and all.
            offered = any(type(c) is type(value) and c == value for c in setting.options())
            return (group, setting, value) if offered and value != current else None
    return None


def _section(title: Any, sections: list[str]) -> str | None:
    if not isinstance(title, str):
        return None
    return next((s for s in sections if s.casefold() == title.strip().casefold()), None)
