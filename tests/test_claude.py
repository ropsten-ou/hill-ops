import json

from settings_panel import OFF_ON, Group, JsonStore, Setting

from hill_ops.claude import (
    DOCS_LENGTH, EVENTS, SESSIONS, Context, meta, parse_answer, parse_tip, question_prompt, read_docs,
    read_sessions, shown, tip_prompt,
)
from hill_ops.events import log_dir
from hill_ops.hub import Peer


def groups(tmp_path):
    store = JsonStore(tmp_path / "demo.json")
    sort = Setting(("notes", "sort"), "Sort by", "Order of the notes list.", ["title", "modified"], "title",
                   names={"modified": "newest first"})
    preview = Setting(("preview",), "Preview", "Show the preview.", OFF_ON, True)
    return [Group("list", "List", "demo", store, [sort, preview])]


def test_a_tip_can_offer_one_of_the_settings_own_choices(tmp_path):
    g = groups(tmp_path)
    answer = '```json\n{"tip": "Newest notes first?", "setting": {"group": "list", "key": ["notes", "sort"], "value": "modified"}}\n```'
    tip = parse_tip(answer, g, ["List"])
    assert tip.text == "Newest notes first?"
    group, setting, value = tip.setting
    assert (group.id, setting.key, value) == ("list", ("notes", "sort"), "modified")
    assert tip.offer == "set List → Sort by to newest first"


def test_a_setting_that_isnt_offered_is_dropped_but_the_tip_stays(tmp_path):
    g = groups(tmp_path)
    for setting in (
        {"group": "list", "key": ["notes", "sort"], "value": "size"},  # not a choice
        {"group": "list", "key": ["notes", "sort"], "value": "title"},  # already so
        {"group": "list", "key": ["preview"], "value": 0},  # 0 isn't False here
        {"group": "map", "key": ["notes", "sort"], "value": "modified"},  # no such group
        "sort",
    ):
        tip = parse_tip(json.dumps({"tip": "Try this", "setting": setting}), g, [])
        assert tip.text == "Try this" and tip.setting is None and tip.offer is None


def test_help_must_be_one_of_the_sections(tmp_path):
    assert parse_tip('{"tip": "See the map", "help": "map"}', [], ["List", "Map"]).help == "Map"
    assert parse_tip('{"tip": "See the map", "help": "Atlas"}', [], ["List", "Map"]).help is None


def test_no_tip_and_bad_answers_give_none(tmp_path):
    for answer in ('{"tip": null}', '{"tip": "  "}', "Here's a tip: use keys", '{"tip": ', ""):
        assert parse_tip(answer, [], []) is None


def test_a_long_tip_is_cut(tmp_path):
    tip = parse_tip(json.dumps({"tip": "word " * 40}), [], [])
    assert len(tip.text) == 80 and tip.text.endswith("…")


def test_an_answer_ends_with_what_it_offers(tmp_path):
    g = groups(tmp_path)
    offer = '{"setting": {"group": "list", "key": ["notes", "sort"], "value": "modified"}}'
    for reply in (
        f"Sort by modified puts new notes first.\nPress , to change it.\n{offer}",
        f"Sort by modified puts new notes first.\nPress , to change it.\n```json\n{offer}\n```\n",
        "Sort by modified puts new notes first.\nPress , to change it.\n" + json.dumps(json.loads(offer), indent=2),
    ):
        answer = parse_answer(reply, g, ["List"])
        assert answer.text == "Sort by modified puts new notes first.\nPress , to change it."
        assert answer.offer == "set List → Sort by to newest first"
    help = parse_answer('The map shows links.\n{"help": "map"}', g, ["List", "Map"])
    assert (help.text, help.help, help.offer) == ("The map shows links.", "Map", "open the help on Map")


def test_a_command_is_offered_only_if_its_one_of_the_apps(tmp_path):
    g = groups(tmp_path)
    commands = [["status", "[STATUS]", "work items", ["open", "ready"]], ["map", "", "the links map"]]
    answer = parse_answer('Only the ready items.\n{"command": ":status  ready"}', g, [], commands)
    assert (answer.text, answer.command, answer.offer) == ("Only the ready items.", "status ready", "run :status ready")
    assert parse_answer('Gone.\n{"command": "delete everything"}', g, [], commands).command is None
    assert parse_answer('No commands.\n{"command": "map"}', g, []).command is None
    tip = parse_tip('{"tip": "The map shows links", "command": "map"}', g, [], commands)
    assert (tip.command, tip.offer) == ("map", "run :map")
    setting = '{"setting": {"group": "list", "key": ["notes", "sort"], "value": "modified"}, "command": "map"}'
    assert parse_answer(f"Both.\n{setting}", g, [], commands).command is None  # one offer, the setting first


def test_an_answer_without_an_offer_or_with_a_bad_one_is_all_text(tmp_path):
    g = groups(tmp_path)
    assert parse_answer("  Press n for a new note.\n", g, []).text == "Press n for a new note."
    bad = parse_answer('Keep it.\n{"setting": {"group": "list", "key": ["notes", "sort"], "value": "title"}}', g, [])
    assert (bad.text, bad.setting) == ("Keep it.", None)  # the value it has now: nothing to offer
    only = parse_answer('{"help": "List"}', g, ["List"])
    assert only.text == "Open the help on List?"


def test_an_answer_shows_without_its_offer_while_it_comes():
    assert shown("Press , to see the settings.\n") == "Press , to see the settings."
    assert shown('Press , for them.\n{"setting": {"gro') == "Press , for them."
    assert shown("Press , for them.\n```") == "Press , for them."


def test_claude_runs_with_no_tools_and_none_of_your_settings():
    options = meta("haiku")["claudeCode"]["options"]
    assert options == {"tools": [], "settingSources": [], "model": "haiku", "persistSession": False}
    system = meta("haiku")["systemPrompt"]
    assert '"Tip"' in system and '"Question"' in system and "JSON" in system


PEER = dict(app="palace", keys=[[",", "settings", "app.settings"]],
            commands=[["search", "[TEXT]", "search titles"]], help=[["List", [["n", "a new note"]]]])


def test_the_first_message_holds_the_apps_hello_and_readme_the_settings_and_the_log(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# palace\n\nA notes screen.\n")
    log_dir().mkdir(parents=True)
    (log_dir() / "2026-09-30T10-00-00-1.jsonl").write_text(json.dumps(
        {"time": "2026-09-30T10:00:05+02:00", "event": "settings.changed", "app": "palace", "group": "list", "key": ["notes", "sort"], "value": "modified"}
    ) + "\n")
    current = tmp_path / "current.jsonl"
    current.write_text(json.dumps({"time": "2026-10-01T09:00:00+02:00", "event": "hello", "app": "palace"}) + "\n")
    peer = Peer(writer=None, docs=str(readme), **PEER)
    prompt = tip_prompt("asked in the help, on Map", Context().news(peer, groups(tmp_path), current, fresh=True))
    assert prompt.startswith("Tip, asked in the help, on Map.")
    for part in (", settings", "- search [TEXT]: search titles", "- n: a new note", "A notes screen.",
                 'Settings group "list" (List)', 'key ["notes", "sort"] "Sort by" is "title"; choices "title", "modified"',
                 "# Session 2026-09-30T10-00-00-1", '10:00:05 settings.changed palace group="list" key=["notes", "sort"] value="modified"',
                 "# Session current", "09:00:00 hello palace"):
        assert part in prompt
    assert tip_prompt(None, []).startswith("Tip, unasked")


def test_later_messages_hold_only_whats_new(tmp_path):
    g = groups(tmp_path)
    current = tmp_path / "current.jsonl"
    current.write_text(json.dumps({"event": "hello", "app": "palace"}) + "\n")
    context = Context()
    peer = Peer(writer=None, **PEER)
    context.news(peer, g, current, fresh=True)
    assert context.news(peer, g, current, fresh=False) == []
    assert question_prompt("Why?", []) == "Question.\n\nTheir question: Why?"
    g[0].store.set(("notes", "sort"), "modified", "title")
    with current.open("a") as f:
        f.write(json.dumps({"time": "2026-10-01T09:01:00+02:00", "event": "settings.changed", "app": "palace"}) + "\n")
    micro = Peer(writer=None, app="micro", keys=[["Ctrl-e", "command", "hill.command"]])
    news = "\n".join(context.news(micro, g, current, fresh=False))
    assert news.startswith("Since your last message:")
    assert "The app on the strip: micro" in news and "Ctrl-e command" in news
    assert '"Sort by" is now "modified"' in news and "Preview" not in news
    assert "New in the event log:\n09:01:00 settings.changed palace" in news and "hello" not in news
    back = context.news(peer, g, current, fresh=False)
    assert back == ["Since your last message:", "\nThe app on the strip: palace again."]
    again = "\n".join(context.news(peer, g, current, fresh=True))  # a new session hears it all
    assert "- search [TEXT]" in again and '"Sort by" is "modified"; choices' in again and "hello palace" in again


def test_claude_reads_the_last_sessions_and_the_latest_events(tmp_path):
    log_dir().mkdir(parents=True)
    for n in range(SESSIONS + 2):
        lines = [json.dumps({"event": "hello", "n": n, "i": i}) for i in range(100)]
        (log_dir() / f"2026-09-{n + 10}T10-00-00-1.jsonl").write_text("\n".join([*lines, "not json"]) + "\n")
    current = tmp_path / "current.jsonl"
    current.write_text(json.dumps({"event": "start"}) + "\n")
    sessions = read_sessions(current)
    assert sessions[-1] == ("current", [{"event": "start"}])
    assert sum(len(events) for _, events in sessions) == EVENTS
    assert [name for name, _ in sessions][:-1] == [f"2026-09-{n + 10}T10-00-00-1" for n in range(3, SESSIONS + 2)]


def test_a_readme_is_read_only_if_its_text_and_up_to_a_length(tmp_path):
    (tmp_path / "README.md").write_text("x" * (DOCS_LENGTH + 10))
    (tmp_path / "id_rsa").write_text("secret")
    assert read_docs(str(tmp_path / "README.md")).endswith("x\n(cut here)")
    assert len(read_docs(str(tmp_path / "README.md"))) == DOCS_LENGTH + len("\n(cut here)")
    assert read_docs(str(tmp_path / "id_rsa")) is None
    assert read_docs(str(tmp_path / "gone.md")) is None
    assert read_docs(None) is None


def test_claude_hears_the_apps_overview_and_again_when_it_changes(tmp_path):
    current = tmp_path / "current.jsonl"
    current.write_text("")
    context = Context()
    overview = {"title": "Overview", "where": "work items; last scan 16:43", "lines": [
        {"id": "palace/work/020-x.md", "text": [["● ", "red"], ["palace 020 ", "dim"], ["Commands", ""]],
         "help": "Waits for your go."},
        {"text": "no id, not a line"},
        {"id": "rule", "separator": True},
    ]}
    peer = Peer(writer=None, overview=overview, **PEER)
    news = "\n".join(context.news(peer, [], current, fresh=True))
    assert "palace's Overview, the panel's first tab, as it is now (work items; last scan 16:43):" in news
    assert '- id "palace/work/020-x.md": ● palace 020 Commands (Waits for your go.)' in news
    assert "no id" not in news and "rule" not in news
    assert context.news(peer, [], current, fresh=False) == []
    peer.overview = {"lines": [], "empty": "Nothing waits."}
    assert context.news(peer, [], current, fresh=False)[-1] == "- nothing: Nothing waits."


def test_an_answer_can_show_a_setting_or_file_a_work_item(tmp_path):
    from hill_ops.work import Project

    g = groups(tmp_path)
    shown_ = parse_answer('It\'s in List.\n{"show": {"group": "list", "key": ["notes", "sort"]}}', g, [])
    assert shown_.show == (g[0], g[0].settings[0]) and shown_.offer == "show List → Sort by"
    assert parse_answer('{"show": {"group": "list", "key": ["nope"]}}', g, []).show is None
    projects = [Project("palace", tmp_path)]
    work = '{"work": {"project": "palace", "title": "Dark map.", "goal": "The map follows the theme.", "done_when": "It does."}}'
    answer = parse_answer(f"palace can't yet.\n{work}", g, [], None, projects)
    assert answer.work.project == projects[0] and answer.work.title == "Dark map" and answer.work.done_when == "It does."
    assert answer.offer == "file a palace work item: Dark map"
    assert parse_answer(f"No.\n{work.replace('palace', 'elsewhere')}", g, [], None, projects).work is None
    assert parse_answer(f"Untitled.\n{work.replace('Dark map.', ' ')}", g, [], None, projects).work is None
    assert parse_tip('{"tip": "x", ' + work[1:], g, []).work is None  # a tip never files one


def test_claude_hears_the_other_apps_and_the_open_work_items_and_again_when_they_change(tmp_path):
    from hill_ops.index import AppDocs
    from hill_ops.work import Project

    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "012-dark-map.md").write_text("---\nstatus: waiting\n---\n# Dark map\n\n## Goal\nThe map follows the theme.\n")
    (folder / "013-old.md").write_text("---\nstatus: done\n---\n# Old\n")
    peer = Peer(writer=None, **PEER)
    micro = AppDocs("micro", [["Alt-,", "settings", "settings"]], [["claude", "", "ask Claude"]], [["Editor", [["Ctrl-s", "save"]]]])
    context, projects = Context(), [Project("palace", folder)]
    first = "\n".join(context.news(peer, [], None, True, [micro, AppDocs("palace")], projects))
    for part in ("Another app hill-ops knows, not on the strip now: micro", "claude (ask Claude)", "Its help, Editor: Ctrl-s save",
                 "Open work items of palace", "- palace 012 (waiting): Dark map. The map follows the theme."):
        assert part in first
    assert "Old" not in first and "not on the strip now: palace" not in first
    assert context.news(peer, [], None, False, [micro], projects) == []
    (folder / "014-new.md").write_text("---\nstatus: open\n---\n# New\n\n## Goal\nIt.\n")
    again = context.news(peer, [], None, False, [micro], projects)
    assert again[0] == "Since your last message:" and "- palace 014 (open): New. It." in again
