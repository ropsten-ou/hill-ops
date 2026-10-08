"""hill-ops for real: the outer hill-ops runs in a pseudo-terminal, with its own tmux
server, and the test drives it through tmux and reads the panes back."""

import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from pathlib import Path

import pytest

from hill_ops.host import tmux_socket

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")

PROFILE = """\
app = "fake"

[[groups]]
id = "preview"
name = "Preview"
file = "${DEMO_HOME}/settings.json"

  [[groups.settings]]
  key = ["preview"]
  label = "Preview"
  choices = [false, true]
  default = true
"""


class Terminal:
    """A command in a pseudo-terminal of a given size, its output drained."""

    def __init__(self, argv, env, cols, rows):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe(argv[0], argv, env)
        self.resize(cols, rows)
        self.output = bytearray()
        threading.Thread(target=self._drain, daemon=True).start()

    def resize(self, cols, rows):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _drain(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.output.extend(data)

    def wait(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                time.sleep(0.2)  # the last output
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.05)
        os.kill(self.pid, 9)
        raise AssertionError("hill-ops didn't exit")


def until(check, what, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if result := check():
            return result
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def session(tmp_path):
    name = f"hill-test-{os.getpid()}"
    run_dir = Path(tempfile.mkdtemp(prefix="wt", dir="/tmp"))  # socket paths must be short
    (tmp_path / "fake.toml").write_text(PROFILE)
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "HILL_SOCKET")}
    env.update(
        TERM="xterm-256color", HILL_TMUX_NAME=name, HILL_RUNTIME_DIR=str(run_dir),
        HILL_CONFIG_HOME=str(tmp_path / "hill"), DEMO_HOME=str(tmp_path / "demo"),
        FAKE_PROFILE=str(tmp_path / "fake.toml"),
    )

    def tmux(*args):
        return subprocess.run(["tmux", "-L", name, *args], capture_output=True, text=True).stdout

    yield env, tmux, run_dir
    tmux("kill-server")
    tmux_socket(name).unlink(missing_ok=True)
    shutil.rmtree(run_dir, ignore_errors=True)


def test_an_app_runs_above_the_strip_with_settings_commands_and_questions(session, tmp_path, state_home):
    env, tmux, run_dir = session
    fake = str(Path(__file__).with_name("fake_app.py"))
    terminal = Terminal([sys.executable, "-m", "hill_ops", "--", sys.executable, fake], env, 100, 30)

    def panes():
        rows = [line.split() for line in tmux("list-panes", "-t", "hill", "-F", "#{pane_id} #{pane_height} #{pane_active}").splitlines()]
        return rows if len(rows) == 2 else None

    (app, _, _), (strip, _, _) = until(panes, "two panes")
    until(lambda: "quit" in tmux("capture-pane", "-p", "-t", strip), "the app's keys on the strip")
    assert panes()[1][1:] == ["1", "0"]  # one row, not focused

    tmux("send-keys", "-t", app, "open", "Enter")
    until(lambda: panes()[1][1:] == ["10", "1"], "the strip grown to a third, and focused")
    until(lambda: "Preview" in tmux("capture-pane", "-p", "-t", strip), "the settings")
    # Under them, Claude: the tip line, which says Claude can't start in tests, and the line for commands and Claude.
    until(lambda: "can't start" in tmux("capture-pane", "-p", "-t", strip), "the tip line")
    assert "ask Claude, or : for fake's commands" in tmux("capture-pane", "-p", "-t", strip)

    tmux("send-keys", "-t", strip, "Enter")  # Preview: on -> off
    until(lambda: "changed preview False" in tmux("capture-pane", "-p", "-t", app), "the app told")
    assert (tmp_path / "demo" / "settings.json").read_text().strip() == '{\n    "preview": false\n}'

    tmux("send-keys", "-t", strip, "Escape")
    until(lambda: panes()[1][1:] == ["1", "0"] and panes()[0][2] == "1", "the strip shrunk, the app focused")

    terminal.resize(120, 40)
    time.sleep(1)
    assert panes()[1][1] == "1"  # still one row

    def logged(event):
        return any(f'"event": "{event}"' in log.read_text() for log in (state_home / "events").glob("*.jsonl"))

    until(lambda: logged("resize"), "the new size in the event log, once it has lasted")

    tmux("send-keys", "-t", app, "cmd", "Enter")
    until(lambda: panes()[1][1:] == ["13", "1"], "the panel, as the command line, focused")
    until(lambda: "greet" in tmux("capture-pane", "-p", "-t", strip), "the app's command")
    tmux("send-keys", "-t", strip, "gr", "Tab", "Pierre", "Enter")
    until(lambda: "command greet Pierre" in tmux("capture-pane", "-p", "-t", app), "the command run")
    until(lambda: panes()[1][1:] == ["1", "0"], "the strip back")

    tmux("send-keys", "-t", app, "ask", "Enter")
    until(lambda: "Your name?" in tmux("capture-pane", "-p", "-t", strip) and panes()[1][2] == "1", "the question")
    tmux("send-keys", "-t", strip, "Ada", "Enter")
    until(lambda: "answer Ada" in tmux("capture-pane", "-p", "-t", app), "the answer")
    until(lambda: panes()[1][1:] == ["1", "0"], "the strip back again")

    tmux("send-keys", "-t", app, "fail", "Enter")  # an error: logged, and the strip stays as it is
    time.sleep(0.5)
    assert panes()[1][1:] == ["1", "0"]

    tmux("send-keys", "-t", app, "quit", "Enter")
    assert terminal.wait() == 3
    output = terminal.output.decode(errors="replace")
    after_tmux = output.rsplit("\x1b[?1049l", 1)[-1]  # once tmux has left the screen
    assert "fake: ready" in after_tmux and "fake: bye" in after_tmux  # reprinted
    assert "[detached" not in output and "[exited" not in output
    assert subprocess.run(["tmux", "-L", env["HILL_TMUX_NAME"], "has-session"], capture_output=True).returncode != 0
    assert not tmux_socket(env["HILL_TMUX_NAME"]).exists()  # tmux's socket file too
    assert list(run_dir.glob("*/channel.sock")) == []

    # The session's event log: what happened, without what was typed.
    (log,) = (state_home / "events").glob("*.jsonl")
    events = [json.loads(line) for line in log.read_text().splitlines()]
    # The strip may be stopped before it hears the app leave, and Claude's
    # failing to start for the panel's tip comes when it comes.
    assert [e["event"] for e in events if e["event"] not in ("bye", "problem")] == [
        "start", "hello", "settings.open", "settings.changed", "close", "resize",
        "command.open", "command", "close", "ask", "answer", "close", "error", "end",
    ]
    start, end = events[0], next(e for e in events if e["event"] == "end")
    assert (start["command"], start["cols"], start["rows"], end["status"]) == (Path(sys.executable).name, 100, 30, 3)
    assert next((e["cols"], e["rows"]) for e in events if e["event"] == "resize") == (120, 40)
    assert next(e for e in events if e["event"] == "command")["name"] == "greet"
    assert next(e for e in events if e["event"] == "error")["what"] == "fake failure"
    assert "Pierre" not in log.read_text() and "Ada" not in log.read_text()


def test_the_panes_draw_in_true_colour_without_colorterm(session):
    env, tmux, run_dir = session
    env = {k: v for k, v in env.items() if k != "COLORTERM"}  # as over ssh; tmux 3.7 sets it anyway, 3.4 doesn't
    app = 'echo "colour: $COLORTERM"; read line'
    terminal = Terminal([sys.executable, "-m", "hill_ops", "--", "sh", "-c", app], env, 100, 30)
    until(lambda: "colour: truecolor" in tmux("capture-pane", "-p", "-t", "hill"), "COLORTERM in the app's pane")
    tmux("send-keys", "-t", "hill", "Enter")
    assert terminal.wait() == 0


def test_an_app_runs_programs_beside_it_with_the_strip_under_them(session, state_home):
    env, tmux, run_dir = session
    fake = str(Path(__file__).with_name("fake_app.py"))
    terminal = Terminal([sys.executable, "-m", "hill_ops", "--", sys.executable, fake], env, 100, 30)

    def panes(count=None):
        rows = tmux("list-panes", "-t", "hill", "-F", "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height} #{pane_active}")
        found = {pane: tuple(map(int, rest)) for pane, *rest in (line.split() for line in rows.splitlines())}
        return found if count is None or len(found) == count else None

    app, strip = until(lambda: panes(2), "two panes")
    until(lambda: "quit" in tmux("capture-pane", "-p", "-t", strip), "the app's keys on the strip")
    tmux("send-keys", "-t", app, "panes", "Enter")
    side = until(lambda: panes(4), "the panes beside the app")
    assert side[app] == (0, 0, 25, 30, 1)  # 25% at the window's full height, and the focus
    view, claude = sorted((p for p in side if p not in (app, strip)), key=lambda p: side[p][0])
    assert side[claude][2] == 30 and side[view][:2] == (26, 0)
    assert side[strip] == (26, 29, 74, 1, 0)  # under the two

    tmux("send-keys", "-t", app, "open", "Enter")  # the panel grows under the side panes
    until(lambda: panes()[strip][3] == 10 and panes()[strip][2] == 74, "the panel under the side panes")
    assert panes()[app][3] == 30
    tmux("send-keys", "-t", strip, "Escape")
    until(lambda: panes()[strip][3] == 1, "the strip back")

    tmux("select-pane", "-t", view)  # the focus beside the app: still the app's keys, and back there
    tmux("send-keys", "-t", app, "open", "Enter")
    until(lambda: panes()[strip][4] == 1, "the panel focused")
    tmux("send-keys", "-t", strip, "Escape")
    until(lambda: panes()[view][4] == 1, "the focus back beside the app")

    terminal.resize(140, 40)  # the widths follow the window
    until(lambda: panes()[app][2] == 35 and panes()[claude][2] == 42, "the widths again")

    tmux("send-keys", "-t", app, "over", "Enter")
    editor = until(lambda: next((p for p in panes(3) or {} if p not in side), None), "the editor over the side panes")
    assert panes()[editor][:2] == (36, 0) and panes()[editor][4] == 1
    until(lambda: "over.done 5" in tmux("capture-pane", "-p", "-t", app), "the app told it exited")
    until(lambda: set(panes(4) or ()) == set(side), "the side panes back")
    assert panes()[app][4] == 1

    tmux("send-keys", "-t", app, "nopanes", "Enter")
    until(lambda: panes(2), "the strip back under the app")
    assert panes()[strip] == (0, 39, 140, 1, 0)

    tmux("send-keys", "-t", app, "quit", "Enter")
    assert terminal.wait() == 3
    (log,) = (state_home / "events").glob("*.jsonl")
    events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
    assert [e for e in events if e in ("panes", "over", "over.done")] == ["panes", "over", "over.done", "panes"]


def test_dragging_a_border_sets_the_width_snapped_to_its_steps(session, state_home):
    env, tmux, run_dir = session
    fake = str(Path(__file__).with_name("fake_app.py"))
    terminal = Terminal([sys.executable, "-m", "hill_ops", "--", sys.executable, fake], env, 100, 30)

    def widths():
        rows = tmux("list-panes", "-t", "hill", "-F", "#{pane_id} #{pane_width}").splitlines()
        return dict(row.split() for row in rows)

    until(lambda: len(widths()) == 2, "two panes")
    app = next(iter(widths()))
    until(lambda: "fake: ready" in tmux("capture-pane", "-p", "-t", app), "the app")
    tmux("send-keys", "-t", app, "panes", "Enter")
    until(lambda: len(widths()) == 4 and widths()[app] == "25", "the panes beside the app, sized")
    # The border right of the app is column 26 (counting from 1): drag it to 37, let go at 37.
    os.write(terminal.fd, b"\x1b[<0;26;10M")
    time.sleep(0.1)
    for x in range(27, 38, 2):
        os.write(terminal.fd, f"\x1b[<32;{x};10M".encode())
        time.sleep(0.05)
    os.write(terminal.fd, b"\x1b[<0;37;10m")
    prefs = Path(env["HILL_CONFIG_HOME"]) / "settings.json"
    until(lambda: prefs.exists() and json.loads(prefs.read_text()) == {"layout": {"app": "35%"}}, "the width saved")
    until(lambda: widths()[app] == "35", "the width snapped to 35%")
    tmux("send-keys", "-t", app, "quit", "Enter")
    assert terminal.wait() == 3
    (log,) = (state_home / "events").glob("*.jsonl")
    changed = [json.loads(line) for line in log.read_text().splitlines() if '"settings.changed"' in line]
    assert [(e["group"], e["key"], e["value"], e["by"]) for e in changed] == [("layout", ["layout", "app"], "35%", "drag")]
