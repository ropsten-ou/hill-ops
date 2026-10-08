import asyncio
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from hill_ops.hub import Hub
from hill_client import connect, line


@pytest.fixture
def path():
    folder = Path(tempfile.mkdtemp(prefix="wh", dir="/tmp"))
    yield folder / "run" / "c.sock"
    shutil.rmtree(folder)


def run_hub(path, script):
    async def go():
        events = []
        hub = Hub(path, lambda p: events.append(("hello", p.app)), lambda: events.append("change"),
                  lambda p, method, params: events.append((method, p.app, params)),
                  lambda p: events.append(("leave", p.app)),
                  lambda p, method, params: events.append(("report", method, p.app, params)))
        await hub.start()
        try:
            return await script(hub, events)
        finally:
            await hub.stop()

    return asyncio.run(go())


async def settle():
    await asyncio.sleep(0.1)


def test_the_socket_is_private(path):
    async def script(hub, events):
        return stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(path.parent.stat().st_mode)

    assert run_hub(path, script) == (0o600, 0o700)
    assert not path.exists()  # removed on stop


def test_a_stale_socket_file_is_replaced(path):
    path.parent.mkdir(parents=True)
    path.write_text("left over")

    async def script(hub, events):
        return path.is_socket()

    assert run_hub(path, script)


def test_bad_lines_and_requests_before_hello_are_ignored(path):
    async def script(hub, events):
        reader, writer = await connect(str(path))
        writer.write(b"not json\n" + line("settings.open", group="list") + b'{"no": "method"}\n')
        writer.write(line("hello", app="demo", keys=[["q", "quit", "app.quit"], "junk"]))
        writer.write(line("settings.open", group="list"))
        await settle()
        keys = hub.active.keys
        writer.close()
        await settle()
        return events, keys, hub.active

    events, keys, active = run_hub(path, script)
    assert events == [("hello", "demo"), "change", ("settings.open", "demo", {"group": "list"}), ("leave", "demo"), "change"]
    assert keys == [["q", "quit", "app.quit"]]
    assert active is None  # it left


def test_a_second_hello_moves_an_app_back_on_top(path):
    async def script(hub, events):
        a_reader, a = await connect(str(path))
        b_reader, b = await connect(str(path))
        a.write(line("hello", app="a"))
        await settle()
        b.write(line("hello", app="b"))
        await settle()
        a.write(line("hello", app="a"))
        await settle()
        order = [p.app for p in hub.stack]
        a.close()
        b.close()
        await settle()
        return order

    assert run_hub(path, script) == ["b", "a"]


def test_hello_brings_commands_and_help_and_requests_pass_on(path):
    async def script(hub, events):
        reader, writer = await connect(str(path))
        writer.write(line(
            "hello", app="demo",
            commands=[["new", "TITLE", "a new note"], "junk", [3]],
            help=[["List", [["n", "new"]]], ["broken"], "junk"],
        ))
        for method in ("command.open", "help.open", "ask", "unknown"):
            writer.write(line(method, id=1))
        await settle()
        peer = hub.active
        writer.close()
        await settle()
        return peer.commands, peer.help, [e[0] for e in events if isinstance(e, tuple)]

    commands, help, seen = run_hub(path, script)
    assert commands == [["new", "TITLE", "a new note"]]
    assert help == [["List", [["n", "new"]]]]
    assert seen == ["hello", "command.open", "help.open", "ask", "leave"]


def test_only_an_app_that_said_hello_is_heard_leaving(path):
    async def script(hub, events):
        _, quiet = await connect(str(path))
        _, demo = await connect(str(path))
        demo.write(line("hello", app="demo"))
        await settle()
        quiet.close()
        demo.close()
        await settle()
        return [e for e in events if e[0] == "leave"]

    assert run_hub(path, script) == [("leave", "demo")]


def test_an_app_reports_errors_once_it_has_said_hello(path):
    async def script(hub, events):
        _, writer = await connect(str(path))
        writer.write(line("error", what="too early") + line("hello", app="demo") + line("error", what="push failed"))
        await settle()
        writer.close()
        await settle()
        return [e for e in events if e[0] == "report"]

    assert run_hub(path, script) == [("report", "error", "demo", {"what": "push failed"})]
