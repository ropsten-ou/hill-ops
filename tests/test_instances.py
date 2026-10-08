import json
import os
import subprocess

import pytest

from hill_ops import host, instances
from hill_ops.host import Session, choose, tmux_socket
from hill_ops.instances import INSTANCE, Instance
from hill_ops.layout import APP_WIDTH, CLAUDE_WIDTH
from hill_ops.panel import hill_prefs
from settings_panel import JsonStore


@pytest.fixture
def quiet_tmux(monkeypatch, tmp_path):
    """Sessions whose tmux does nothing; each hill-ops's socket is a file in
    tmp_path, there while it runs, as tmux's is."""
    monkeypatch.setenv("HILL_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "tmux"))
    calls = []

    def tmux(self, *args):
        calls.append(args)
        if "new-session" in args:
            tmux_socket(self.name).parent.mkdir(parents=True, exist_ok=True)
            tmux_socket(self.name).touch()
        return subprocess.CompletedProcess(args, 0, "%0\n", "")

    monkeypatch.setattr(Session, "tmux", tmux)
    return calls


def start(name, argv=("palace",), choice=None, label=None):
    os.environ["HILL_TMUX_NAME"] = name
    try:
        session = Session(list(argv), choose(host._program(list(argv)), choice))
        session.start(100, 30)
    finally:
        del os.environ["HILL_TMUX_NAME"]
    if label:
        (session.instance.path / "label.json").write_text(json.dumps(label))
    return session


def age(instance, seconds):
    """Make `instance` look last used `seconds` ago."""
    then = os.stat(instance.path).st_mtime - seconds
    for path in [instance.path, *instance.path.iterdir()]:
        os.utime(path, (then, then))


def test_the_app_gets_its_instance(quiet_tmux):
    session = start("hill-a")
    new = next(c for c in quiet_tmux if "new-session" in c)
    assert f"{INSTANCE}={session.instance.path}" in new
    record = session.instance.record()
    assert record["program"] == "palace" and record["pid"] == os.getpid() and record["ended"] is None
    assert session.instance.running()
    session.stop()
    assert not session.instance.running() and session.instance.record()["ended"]


def test_two_hills_started_again_each_take_up_an_instance_of_their_own(quiet_tmux):
    a, b = start("hill-a", label=["notes"]), start("hill-b", label=["palace", "hill"])
    assert a.instance != b.instance
    a.stop()
    b.stop()
    age(a.instance, 60)
    # The one used last first, then the other: it's the one not running.
    again_b = start("hill-c")
    again_a = start("hill-d")
    assert again_b.instance == b.instance and again_a.instance == a.instance
    # A third, with both running, is a new one; so is another program's.
    third, micro = start("hill-e"), start("hill-f", ["micro"])
    assert third.instance not in (a.instance, b.instance) and micro.instance not in (a.instance, b.instance)


def test_new_starts_a_new_instance_and_as_takes_the_one_named(quiet_tmux):
    a = start("hill-a")
    a.stop()
    assert start("hill-b", choice="new").instance != a.instance
    assert choose("palace", name=a.instance.id) == a.instance
    assert choose("palace", name="mine").path == instances.instances_dir() / "mine"
    running = start("hill-c", choice=None)
    with pytest.raises(ValueError, match="is running"):
        choose("palace", name=running.instance.id)
    with pytest.raises(ValueError):
        choose("palace", name="../out")


def test_resume_asks_which_by_its_label(quiet_tmux, monkeypatch, capsys):
    a, b = start("hill-a", label=["notes", "palace"]), start("hill-b")
    a.stop()
    b.stop()
    age(b.instance, 7200)
    answers = iter(["7", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert choose("palace", "resume") == b.instance
    shown = capsys.readouterr().out
    assert "1  notes · palace" in shown and "just now" in shown
    assert "2  " in shown and "2 h ago" in shown  # no label: the folder it ran in
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert choose("palace", "resume") not in (a.instance, b.instance)


def test_resume_with_nothing_to_resume_starts_a_new_one(quiet_tmux, capsys):
    assert choose("palace", "resume") is not None
    assert "no instance of palace to resume" in capsys.readouterr().err


def test_a_hill_gone_in_a_reboot_can_be_resumed(quiet_tmux):
    a = start("hill-a")
    tmux_socket("hill-a").unlink()  # a reboot clears the sockets; instance.json still says running
    assert a.instance.record()["ended"] is None and not a.instance.running()
    assert choose("palace") == a.instance


def test_only_the_last_few_ended_instances_are_kept(quiet_tmux):
    ended = []
    for n in range(instances.KEEP + 2):
        session = start(f"hill-{n}", choice="new")
        session.stop()
        age(session.instance, 100 * (instances.KEEP + 2 - n))
        ended.append(session.instance)
    start("hill-x", choice="new")  # pruned as a session starts
    assert [i.path.exists() for i in ended] == [False, False] + [True] * instances.KEEP


def test_each_instance_keeps_its_own_layout(tmp_path, monkeypatch):
    shared = tmp_path / "hill" / "settings.json"  # HILL_CONFIG_HOME's
    shared.parent.mkdir()
    shared.write_text(json.dumps({"layout": {"app": "40%"}, "tips": "off"}))

    def prefs(name):
        monkeypatch.setenv(INSTANCE, str(tmp_path / name))
        (tmp_path / name).mkdir(exist_ok=True)
        return hill_prefs()

    a, b = prefs("a"), prefs("b")
    assert a.get(APP_WIDTH.key) == "40%" and a.get(CLAUDE_WIDTH.key) == CLAUDE_WIDTH.default
    a.set(APP_WIDTH.key, "45%", APP_WIDTH.default)
    assert a.get(APP_WIDTH.key) == "45%" and b.get(APP_WIDTH.key) == "40%"  # the other keeps its own
    assert JsonStore(shared).get(APP_WIDTH.key) == "45%"  # for the next new one
    assert prefs("c").get(APP_WIDTH.key) == "45%"
    assert prefs("b").get(APP_WIDTH.key) == "40%"  # b, started again
    # The rest of hill-ops's settings are shared.
    b.set(("tips",), "on", "on")
    assert a.get(("tips",)) is None
    monkeypatch.delenv(INSTANCE)
    assert isinstance(hill_prefs(), JsonStore)  # outside hill-ops: settings.json alone


def test_describe_falls_back_on_the_folder(tmp_path):
    instance = Instance(tmp_path)
    (tmp_path / "instance.json").write_text(json.dumps({"cwd": "/srv/notes"}))
    assert instance.describe() == "/srv/notes"
    (tmp_path / "label.json").write_text('["cognate", "insight-distiller"]')
    assert instance.describe() == "cognate · insight-distiller"
