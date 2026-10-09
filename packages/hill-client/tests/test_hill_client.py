import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import hill_client
from hill_client import Client, Hover, connect, copy, line, take_focus


@pytest.fixture
def path():
    # Unix socket paths are short (104 bytes on macOS); pytest's tmp_path isn't.
    folder = tempfile.mkdtemp(prefix="wc", dir="/tmp")
    yield str(Path(folder) / "c.sock")
    shutil.rmtree(folder)


class Server:
    """A stand-in for hill-ops's strip: records what clients send."""

    def __init__(self, path):
        self.path = path
        self.received = []
        self.writers = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self.serve, path=self.path)

    async def serve(self, reader, writer):
        self.writers.append(writer)
        while raw := await reader.readline():
            self.received.append(json.loads(raw))

    async def until(self, count):
        for _ in range(200):
            if len(self.received) >= count:
                return self.received
            await asyncio.sleep(0.01)
        raise AssertionError(f"only {self.received}")

    async def stop(self):
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()


def test_a_message_is_a_json_rpc_notification_on_one_line():
    assert json.loads(line("settings.open", group="list")) == {
        "jsonrpc": "2.0", "method": "settings.open", "params": {"group": "list"},
    }
    assert line("hello").endswith(b"\n")


def test_connect_waits_for_the_strip_to_start(path):
    async def go():
        server = Server(path)
        asyncio.get_running_loop().call_later(0.2, lambda: asyncio.ensure_future(server.start()))
        reader, writer = await connect(path, wait=2)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)  # let the server see the connection before it stops
        await server.stop()

    asyncio.run(go())


def test_connect_gives_up_after_waiting(path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(connect(path, wait=0.1))


def test_client_says_hello_then_sends_what_waited(path, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%7")  # the tmux pane it runs in, inside hill-ops

    async def go():
        client = Client(path, "palace", "/p/hill.toml", [(",", "settings", "app.settings")],
                        [("new", "[TITLE]", "a new note")], [("List", [("n", "new")])], "/p/README.md",
                        [("s", "scripts", [("s", "sync", "sync")]), ("w", "work", "work")])
        client.send("settings.open", group="list")  # before the strip is up
        task = asyncio.create_task(client.run())
        server = Server(path)
        await server.start()
        received = await server.until(2)
        client.close()
        await server.stop()
        await task
        return received

    hello, opened = asyncio.run(go())
    assert hello["method"] == "hello"
    assert hello["params"] == {
        "app": "palace", "profile": "/p/hill.toml", "keys": [[",", "settings", "app.settings"]],
        "commands": [["new", "[TITLE]", "a new note"]], "help": [["List", [["n", "new"]]]], "docs": "/p/README.md",
        "tree": [["s", "scripts", [["s", "sync", "sync"]]], ["w", "work", "work"]], "pane": "%7",
    }
    assert opened["params"] == {"group": "list"}


def test_client_calls_handlers_and_says_hello_again_after_a_restart(path):
    async def go():
        heard = []
        client = Client(path, "palace")
        client.on("settings.changed", heard.append)
        task = asyncio.create_task(client.run())
        server = Server(path)
        await server.start()
        await server.until(1)
        server.writers[0].write(line("settings.changed", group="list", key=["notes", "sort"], value="modified"))
        server.writers[0].write(b"not json\n")  # ignored
        for _ in range(100):
            if heard:
                break
            await asyncio.sleep(0.01)
        await server.stop()
        restarted = Server(path)
        await restarted.start()
        again = await restarted.until(1)
        client.close()
        await restarted.stop()
        await task
        return heard, again

    heard, again = asyncio.run(go())
    assert heard == [{"group": "list", "key": ["notes", "sort"], "value": "modified"}]
    assert again[0]["method"] == "hello"


def test_outside_hill_there_is_no_client(monkeypatch):
    monkeypatch.delenv("HILL_SOCKET", raising=False)
    assert Client.from_env("palace") is None
    monkeypatch.setenv("HILL_SOCKET", "/tmp/x.sock")
    assert Client.from_env("palace").path == "/tmp/x.sock"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_take_focus_gives_a_pane_the_focus_but_not_while_the_strip_keeps_it(monkeypatch):
    name = f"hill-client-test-{os.getpid()}"

    def tmux(*args):
        return subprocess.run(["tmux", "-L", name, *args], capture_output=True, text=True).stdout.strip()

    app = tmux("-f", os.devnull, "new-session", "-d", "-s", "hill", "-x", "80", "-y", "24", "-P", "-F", "#{pane_id}", "sleep 60")
    socket = tmux("display", "-p", "#{socket_path}")
    try:
        side = tmux("split-window", "-h", "-d", "-t", app, "-P", "-F", "#{pane_id}", "sleep 60")
        strip = tmux("split-window", "-v", "-d", "-l", "3", "-t", side, "-P", "-F", "#{pane_id}", "sleep 60")
        tmux("set", "-g", "@hill-strip", strip)
        monkeypatch.setenv("TMUX", f"{socket},1,0")
        monkeypatch.setenv("TMUX_PANE", side)  # as the side program has it

        assert take_focus() and tmux("display", "-p", "-t", "hill", "#{pane_id}") == side
        tmux("select-pane", "-t", strip)  # the strip grows and takes the focus
        assert not take_focus(app) and tmux("display", "-p", "-t", "hill", "#{pane_id}") == strip
        tmux("set", "-g", "@hill-keep", "1")  # for the help, say
        assert not take_focus(app) and tmux("display", "-p", "-t", "hill", "#{pane_id}") == strip
        tmux("set", "-g", "@hill-keep", "0")  # the panel alone lets it go
        assert take_focus(app) and tmux("display", "-p", "-t", "hill", "#{pane_id}") == app
        tmux("select-pane", "-t", side)
        assert take_focus(app) and tmux("display", "-p", "-t", "hill", "#{pane_id}") == app
    finally:
        tmux("kill-server")
        Path(socket).unlink(missing_ok=True)
    monkeypatch.delenv("TMUX")
    assert not take_focus()  # outside tmux


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_copy_hands_the_text_to_tmuxs_copy_command(monkeypatch, tmp_path):
    name = f"hill-client-copy-{os.getpid()}"

    def tmux(*args):
        return subprocess.run(["tmux", "-L", name, *args], capture_output=True, text=True).stdout.strip()

    tmux("-f", os.devnull, "new-session", "-d", "-s", "hill", "sleep 60")
    socket = tmux("display", "-p", "#{socket_path}")
    clipboard = tmp_path / "clipboard"
    try:
        monkeypatch.setenv("TMUX", f"{socket},1,0")
        assert not copy("hello")  # no copy-command: OSC 52 alone
        tmux("set", "-g", "copy-command", f"cat > {clipboard}")  # as xclip would
        assert copy("hello, world") and clipboard.read_text() == "hello, world"
    finally:
        tmux("kill-server")
        Path(socket).unlink(missing_ok=True)
    monkeypatch.delenv("TMUX")
    assert not copy("hello")  # outside tmux


def test_hover_takes_the_focus_lazily(monkeypatch):
    hover, part, other = Hover(), object(), object()
    assert hover.moved((5, 5), part, focused=False)  # over a part without the focus: it takes it
    assert not hover.moved((6, 5), part, focused=True)
    hover.left(part)  # a key took the focus elsewhere, the mouse resting over the part
    assert not hover.moved((8, 6), part, focused=False)  # a nudge
    assert hover.moved((9, 6), part, focused=False)  # a move
    hover.moved((9, 6), part, focused=True)
    hover.moved((30, 6), other, focused=True)  # on into another part, which has the focus
    hover.left(part)  # the focus left a part the mouse isn't over
    assert hover.moved((9, 6), part, focused=False)  # back over it: no nudge
    hover = Hover()
    hover.left(part)  # never seen: it rests where it's first seen
    assert not hover.moved((1, 1), part, focused=False)
    assert hover.moved((1, 3), part, focused=False)
    assert not hover.moved((1, 3), None, focused=False)  # over nothing that takes the focus

    asked = []
    monkeypatch.setattr(hill_client, "take_focus", lambda: asked.append(1) or False)
    assert not hover.take_focus() and not hover.take_focus()  # hill-ops's strip keeps it
    assert asked == [1]  # not asked again at once
