from datetime import date

import pytest

from hill_ops.work import Project, file_item, hill_project, open_items, slug


def item(folder, name, status, title, goal="Do it."):
    (folder / name).write_text(f"---\nstatus: {status}\nsince: 2026-10-01\n---\n# {title}\n\n## Goal\n{goal}\n\n## Notes\n- x\n")


def test_open_items_skip_what_is_over(tmp_path):
    item(tmp_path, "001-a.md", "done", "Finished")
    item(tmp_path, "002-b.md", "waiting", "Search the strip", "Find   every\nsetting.")
    item(tmp_path, "003-c.md", "dropped", "Gone")
    (tmp_path / "README.md").write_text("# Work items\n")
    found = open_items(Project("demo", tmp_path))
    assert [(i.number, i.status, i.title, i.goal) for i in found] == [("002", "waiting", "Search the strip", "Find every setting.")]


def test_a_filed_item_is_numbered_after_the_last_and_listed(tmp_path):
    item(tmp_path, "007-a.md", "open", "Earlier")
    (tmp_path / "README.md").write_text("# Work items\n\nOpen items.\n\n- [007](007-a.md) open: Earlier\n")
    project = Project("demo", tmp_path)
    path = file_item(project, "Dark theme for the map", "The map follows the theme.", "", "palace", date(2026, 10, 6))
    assert path.name == "008-dark-theme-for-the-map.md"
    text = path.read_text()
    assert text.startswith("---\nstatus: open\nsince: 2026-10-06\ncreated: 2026-10-06\n---\n# Dark theme for the map\n")
    assert "## Goal\nThe map follows the theme.\n" in text and "on palace" in text and "## Done when\nTo decide.\n" in text
    assert (tmp_path / "README.md").read_text() == (
        "# Work items\n\nOpen items.\n\n- [007](007-a.md) open: Earlier\n- [008](008-dark-theme-for-the-map.md) open: Dark theme for the map\n"
    )
    assert [i.title for i in open_items(project)] == ["Earlier", "Dark theme for the map"]


def test_a_first_item_starts_the_list(tmp_path):
    path = file_item(Project("demo", tmp_path), "First", "Goal.", "When it works.", None)
    assert path.name == "001-first.md" and "## Done when\nWhen it works.\n" in path.read_text()
    assert "- [001](001-first.md) open: First" in (tmp_path / "README.md").read_text()


def test_slugs_and_hills_own_folder(tmp_path, monkeypatch):
    assert slug("Search: every setting, key & help section (soon)") == "search-every-setting-key-help-section"
    assert slug("!!!") == "item"
    assert hill_project() is None  # the tests' folder doesn't exist
    monkeypatch.setenv("HILL_WORK_DIR", str(tmp_path))
    assert hill_project() == Project("hill-ops", tmp_path)


def test_a_work_folder_two_projects_name_is_listed_once(tmp_path):
    from hill_ops.panel import _one_per_folder

    hill, palace, demo = Project("hill-ops", tmp_path / "w"), Project("palace", tmp_path / "w" / ".." / "w"), Project("demo", tmp_path / "d")
    assert _one_per_folder([hill, palace, demo]) == [hill, demo]
