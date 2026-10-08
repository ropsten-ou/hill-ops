from settings_panel import Group, JsonStore, Setting

from hill_ops.index import AppDocs, build, remember, remembered, search


def groups(tmp_path):
    store = JsonStore(tmp_path / "settings.json")
    store.set(("notes", "sort"), "modified", "title")
    return [
        ("demo", Group("list", "List", "demo", store, [
            Setting(("notes", "sort"), "Sort by", "Order of the notes list.", ["title", "modified"], "title",
                    {"modified": "newest first"}),
        ])),
        ("hill", Group("layout", "Layout", "hill", store, [
            Setting(("app_width",), "App width", "The app's share of the window.", ["20%", "25%"], "25%"),
        ])),
    ]


DEMO = AppDocs(
    "demo", keys=[[",", "settings", "app.settings"]], commands=[["new", "TITLE", "write a new note", []]],
    help=[["List", [["n", "new note"], ["/", "search the notes"]]]],
)


def test_everything_is_found_by_any_of_its_words(tmp_path):
    index = build(groups(tmp_path), [DEMO], [("Esc", "close the panel")])
    found = {(f.kind, f.app, f.name) for f in index}
    assert found == {
        ("setting", "demo", "Sort by"), ("setting", "hill", "App width"), ("key", "demo", ","),
        ("help", "demo", "List"), ("key", "demo", "n"), ("key", "demo", "/"), ("command", "demo", ":new TITLE"),
        ("key", "hill", "Esc"),
    }
    sort = next(f for f in index if f.name == "Sort by")
    assert sort.target == ("list", ("notes", "sort"))
    assert "Now newest first." in sort.what  # its value, as the panel shows it
    assert [f.name for f in search(index, "notes order")] == ["Sort by"]  # words in any order, from its help
    assert [f.name for f in search(index, "newest")] == ["Sort by"]  # its choices' names too
    assert [f.name for f in search(index, "")] == []


def test_the_app_on_the_strip_and_names_come_first(tmp_path):
    index = build(groups(tmp_path), [DEMO], [])
    assert [f.name for f in search(index, "width")] == ["App width"]
    assert search(index, "new", "demo")[0].name == ":new TITLE"  # named so, the colon aside
    assert [f.name for f in search(index, "new")] == [":new TITLE", "Sort by", "n"]  # then settings, then the rest
    assert search(index, "a", "hill")[0].app == "hill"


def test_an_apps_hello_is_remembered_for_when_it_isnt_running():
    remember(DEMO)
    remember(AppDocs("../escape"))
    assert remembered() == [DEMO]
