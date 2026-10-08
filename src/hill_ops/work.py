"""Work items, for Claude in the strip: the open ones of each project it can
file in, and a new one filed when what you want doesn't exist yet.

A project is hill-ops itself, or an app whose profile names a work folder
(`work = "~/projects/hill/work"`). An item is a Markdown file there,
`NNN-slug.md`, with `status:`, `since:` and `created:` frontmatter, a title
and the sections Goal, Notes and Done when; the folder's README lists the
open ones, a line each. hill-ops writes a new item and its line, and never
commits: the item's own session, or you, does.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CLOSED = ("done", "dropped")
"""The statuses of items that are over."""
GOAL_LENGTH = 200
"""How much of an item's Goal Claude reads."""
TITLE_LENGTH = 80


@dataclass(frozen=True)
class Project:
    """A project hill-ops can file work items in: its name and its work folder."""

    name: str
    folder: Path


@dataclass(frozen=True)
class Item:
    """A work item, as Claude reads it: its number, file, status, title
    and the start of its Goal."""

    number: str
    file: str
    status: str
    title: str
    goal: str


def hill_project() -> Project | None:
    """hill-ops's own work folder: $HILL_WORK_DIR, else its repo's, when
    hill-ops runs from its repo."""
    env = os.environ.get("HILL_WORK_DIR")
    folder = Path(env) if env else Path(__file__).resolve().parents[2] / "work"
    return Project("hill-ops", folder) if folder.is_dir() else None


def open_items(project: Project) -> list[Item]:
    """The project's items that aren't done or dropped, by number; a file
    that can't be read is skipped."""
    items = []
    for path in sorted(project.folder.glob("[0-9][0-9][0-9]-*.md")):
        try:
            item = read_item(path)
        except OSError:
            continue
        if item.status not in CLOSED:
            items.append(item)
    return items


def read_item(path: Path) -> Item:
    text = path.read_text(encoding="utf-8", errors="replace")
    status = re.search(r"^status:\s*(\S+)", text, re.M)
    title = re.search(r"^# (.+)$", text, re.M)
    goal = re.search(r"^## Goal\n(.+?)(?:\n## |\Z)", text, re.M | re.S)
    goal_text = " ".join(goal.group(1).split()) if goal else ""
    if len(goal_text) > GOAL_LENGTH:
        goal_text = goal_text[:GOAL_LENGTH - 1].rstrip() + "…"
    return Item(path.name[:3], path.name, status.group(1) if status else "", title.group(1).strip() if title else "", goal_text)


def slug(title: str) -> str:
    """A file name's words from a title: lower case, letters and digits, by hyphens, at most six words."""
    words = re.findall(r"[a-z0-9]+", title.casefold())
    return "-".join(words[:6]) or "item"


def file_item(project: Project, title: str, goal: str, done_when: str, app: str | None, today: date | None = None) -> Path:
    """Write a new open item, numbered after the last, and its line in the
    folder's README; the path written. Raises OSError if it can't."""
    today = today or date.today()
    numbers = [int(p.name[:3]) for p in project.folder.glob("[0-9][0-9][0-9]-*") if p.name[:3].isdigit()]
    number = f"{max(numbers, default=0) + 1:03d}"
    path = project.folder / f"{number}-{slug(title)}.md"
    where = f"on {app}" if app else "with no app on the channel"
    text = (
        f"---\nstatus: open\nsince: {today}\ncreated: {today}\n---\n# {title}\n\n"
        f"## Goal\n{goal}\n\n"
        f"## Notes\n- {today}: filed from hill-ops's strip by Claude, {where}; not committed yet.\n\n"
        f"## Done when\n{done_when or 'To decide.'}\n"
    )
    with open(path, "x", encoding="utf-8") as f:
        f.write(text)
    _list(project.folder / "README.md", f"- [{number}]({path.name}) open: {title}")
    return path


def _list(readme: Path, line: str) -> None:
    """Add the item's line to the README's list: after its last item, or at its end."""
    try:
        lines = readme.read_text(encoding="utf-8").split("\n")
    except FileNotFoundError:
        lines = ["# Work items", "", "Open items, one per file in this folder.", ""]
    last = max((i for i, text in enumerate(lines) if text.startswith("- [")), default=None)
    if last is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", line, ""]
    else:
        lines.insert(last + 1, line)
    readme.write_text("\n".join(lines), encoding="utf-8")


def clean_title(title: str) -> str:
    title = " ".join(title.split()).rstrip(".")
    return title if len(title) <= TITLE_LENGTH else title[:TITLE_LENGTH - 1].rstrip() + "…"
