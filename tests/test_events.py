import json
import os
import stat
import time

from hill_ops.events import EventLog, command_words, log_dir, prune
from hill_ops.paths import state_home


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_each_event_goes_on_a_line_of_its_own(state_home):
    log = EventLog.new()
    log.record("start", command="palace")
    log.record("settings.changed", "palace", group="list", key=["notes", "sort"], value="modified")
    first, second = lines(log.path)
    assert log.path.parent == state_home / "events" and log.path.suffix == ".jsonl"
    assert first["event"] == "start" and first["app"] is None and first["command"] == "palace"
    assert second | {"time": ""} == {
        "time": "", "event": "settings.changed", "app": "palace", "group": "list", "key": ["notes", "sort"], "value": "modified",
    }
    assert time.strptime(second["time"][:19], "%Y-%m-%dT%H:%M:%S")
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.path.parent.stat().st_mode) == 0o700


def test_hill_settings_turn_the_log_off(tmp_path):
    (tmp_path / "hill").mkdir()
    (tmp_path / "hill" / "settings.json").write_text('{"events": false}')
    log = EventLog.new()
    log.record("hello", "palace")
    assert not log.path.exists()


def test_a_log_that_cant_be_written_is_skipped(tmp_path):
    (tmp_path / "file").write_text("")
    EventLog(tmp_path / "file" / "events.jsonl").record("hello", "palace")  # no error
    EventLog(None).record("hello", "palace")


def test_logs_older_than_a_month_are_removed(state_home):
    log_dir().mkdir(parents=True)
    old, recent, other = log_dir() / "old.jsonl", log_dir() / "recent.jsonl", log_dir() / "notes.txt"
    for path in (old, recent, other):
        path.write_text("{}\n")
    month_ago = time.time() - 31 * 86400
    for path in (old, other):
        os.utime(path, (month_ago, month_ago))
    prune()
    assert sorted(p.name for p in log_dir().iterdir()) == ["notes.txt", "recent.jsonl"]


COMMANDS = [["rename", "TITLE", "rename the note"], ["settings", "[GROUP]", "the settings", ["list", "sync"]]]


def test_a_command_keeps_only_the_apps_own_words():
    assert command_words("settings sync", COMMANDS) == {"name": "settings", "choice": "sync"}
    assert command_words("  settings  ", COMMANDS) == {"name": "settings"}
    assert command_words("rename Secret plans", COMMANDS) == {"name": "rename"}
    assert command_words("settings my folder", COMMANDS) == {"name": "settings"}
    assert command_words("Dear diary", COMMANDS) == {"name": None}


def test_the_state_folder_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HILL_STATE_HOME", str(tmp_path / "a"))
    assert state_home() == tmp_path / "a"
    monkeypatch.delenv("HILL_STATE_HOME")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "b"))
    assert state_home() == tmp_path / "b" / "hill"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "c"))
    assert state_home() == tmp_path / "c" / ".local" / "state" / "hill"
