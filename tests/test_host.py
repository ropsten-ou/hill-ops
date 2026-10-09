import json
import os
import subprocess
import sys

import pytest

from hill_ops import host, runner
from hill_ops.host import CONFIG, Session


def test_inside_hill_the_command_just_runs(monkeypatch):
    ran = []

    def execvp(file, argv):
        ran.append(argv)
        raise SystemExit(0)

    monkeypatch.setenv("HILL_SOCKET", "/tmp/x.sock")
    monkeypatch.setattr(host.os, "execvp", execvp)
    with pytest.raises(SystemExit):
        host.run(["palace", "~/notes"])
    assert ran == [["palace", "~/notes"]]


def test_a_missing_command_says_so(monkeypatch, capsys):
    monkeypatch.setenv("HILL_SOCKET", "/tmp/x.sock")
    assert host.run(["no-such-command-here"]) == 127
    assert "no-such-command-here" in capsys.readouterr().err


def test_the_session_starts_the_app_above_a_one_row_strip(monkeypatch, tmp_path):
    monkeypatch.setenv("HILL_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HILL_TMUX_NAME", "hill-t")
    calls = []

    def tmux(self, *args):
        calls.append(args)
        pane = "%0\n" if "new-session" in args else "%1\n" if "split-window" in args else ""
        return subprocess.CompletedProcess(args, 0, pane, "")

    monkeypatch.setattr(Session, "tmux", tmux)
    session = Session(["palace", "a;"])
    session.start(100, 30)
    new, split, *rest = calls
    assert new[:2] == ("-f", str(CONFIG)) and "new-session" in new
    assert new[new.index("-x") + 1] == "100" and new[new.index("-y") + 1] == "30"
    assert f"HILL_SOCKET={session.socket}" in new
    assert f"HILL_ARGV={json.dumps(['palace', 'a;'])}" in new
    assert new[-5:] == ("--", sys.executable, "-m", "hill_ops", "_run")
    assert split[split.index("-l") + 1] == "1" and "HILL_APP_PANE=%0" in split and split[-1] == "_strip"
    assert f"HILL_EVENTS={session.log.path}" in split  # the strip writes to the session's log
    assert ("set", "-g", "@hill-strip", "%1") in rest
    assert any(c[:3] == ("set-hook", "-g", "window-resized") for c in rest)
    # tmux would take an argument ending in ";" for a command separator.
    assert not [a for c in calls for a in c if a.endswith(";")]


def test_the_session_logs_its_start_and_end(monkeypatch, tmp_path, state_home):
    monkeypatch.setenv("HILL_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("HILL_TMUX_NAME", "hill-t")
    monkeypatch.setattr(Session, "tmux", lambda self, *args: subprocess.CompletedProcess(args, 0, "%0\n", ""))
    monkeypatch.setattr(host.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(host.signal, "signal", lambda *args: None)
    old = state_home / "events" / "2026-08-01T09-00-00-1.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}\n")
    os.utime(old, (0, 0))
    session = Session(["/usr/bin/python3", "-m", "palace", "~/notes/secret.md"])  # as palace starts itself
    session.start(100, 30)
    (session.run_dir / "exit.json").write_text(json.dumps({"status": 2, "text": ""}))
    assert session.attach() == 2
    events = [json.loads(line) for line in session.log.path.read_text().splitlines()]
    assert [(e["event"], e.get("command"), e.get("status")) for e in events] == [("start", "palace", None), ("end", None, 2)]
    assert "secret" not in session.log.path.read_text()  # the program, not its arguments
    assert host._program(["/usr/local/bin/micro", "notes.md"]) == "micro"
    assert not old.exists()  # a month old: removed when a session starts


def test_exit_status_counts_signals_as_the_shell_does():
    assert runner.exit_status(3) == 3
    assert runner.exit_status(-15) == 143


def test_the_runner_keeps_the_screen_and_the_status(monkeypatch, tmp_path):
    tmux_calls = []

    def fake_run(argv, **kwargs):
        tmux_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "report\n\n\n" if "capture-pane" in argv else "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setenv("HILL_RUN", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setenv("HILL_ARGV", json.dumps([sys.executable, "-c", "import sys; sys.exit(4)"]))
    assert runner.main() == 4
    assert json.loads((tmp_path / "exit.json").read_text()) == {"status": 4, "text": "report\n\n\n"}
    assert tmux_calls[-1][:4] == ["tmux", "detach-client", "-s", "hill"]


def test_the_runner_reports_a_command_that_cannot_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setenv("HILL_RUN", str(tmp_path))
    monkeypatch.setenv("HILL_ARGV", json.dumps(["no-such-command-here"]))
    assert runner.main() == 127
    assert "no-such-command-here" in capsys.readouterr().err


def test_a_program_beside_the_app_exits_with_its_status(monkeypatch):
    monkeypatch.setenv("HILL_ARGV", json.dumps([sys.executable, "-c", "import sys; sys.exit(5)"]))
    assert runner.side() == 5  # not on a terminal here: without the relay


def test_the_clipboard_command_is_the_systems(monkeypatch):
    def having(*tools):
        monkeypatch.setattr(host.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None)

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    having("pbcopy")
    assert host._copy_command() == "pbcopy"
    having("wl-copy", "xclip", "xsel")
    assert host._copy_command() is None  # over ssh, no desktop: OSC 52 alone
    monkeypatch.setenv("DISPLAY", ":20")
    assert host._copy_command() == "xclip -selection clipboard"
    having("xsel")
    assert host._copy_command() == "xsel --clipboard --input"
    having("wl-copy", "xclip")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert host._copy_command() == "wl-copy"
