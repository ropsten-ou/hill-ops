import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from hill_client import line


@pytest.fixture
def path():
    folder = Path(tempfile.mkdtemp(prefix="wb", dir="/tmp"))
    yield str(folder / "c.sock")
    shutil.rmtree(folder)


class Strip:
    """A stand-in for hill-ops's strip: records lines, can talk back."""

    def __init__(self, path):
        self.path, self.received, self.writers = path, [], []

    async def start(self):
        self.server = await asyncio.start_unix_server(self.serve, path=self.path)

    async def serve(self, reader, writer):
        self.writers.append(writer)
        while raw := await reader.readline():
            self.received.append(json.loads(raw))

    async def until(self, count):
        for _ in range(300):
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


def test_the_bridge_carries_lines_both_ways_and_says_hello_again(path):
    async def go():
        strip = Strip(path)
        await strip.start()
        env = dict(os.environ, HILL_SOCKET=path)
        bridge = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "hill_ops", "connect", env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        bridge.stdin.write(line("hello", app="micro", profile=None, keys=[]))
        await bridge.stdin.drain()
        first = await strip.until(1)
        strip.writers[0].write(line("settings.changed", group="editor", key=["tabsize"], value=8))
        heard = json.loads(await asyncio.wait_for(bridge.stdout.readline(), 5))
        await strip.stop()  # the strip restarts
        again = Strip(path)
        await again.start()
        repeated = await again.until(1)
        bridge.stdin.write(line("settings.open", group="editor"))
        await bridge.stdin.drain()
        await again.until(2)
        bridge.stdin.close()  # the app has gone
        status = await asyncio.wait_for(bridge.wait(), 5)
        await again.stop()
        return first, heard, repeated, again.received, status

    first, heard, repeated, received, status = asyncio.run(go())
    assert first[0]["method"] == "hello"
    assert heard["params"] == {"group": "editor", "key": ["tabsize"], "value": 8}
    assert repeated[0]["params"]["app"] == "micro"
    assert [m["method"] for m in received] == ["hello", "settings.open"]
    assert status == 0


def test_outside_hill_the_bridge_says_so():
    env = {k: v for k, v in os.environ.items() if k != "HILL_SOCKET"}
    done = subprocess.run([sys.executable, "-m", "hill_ops", "connect"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 2 and "inside hill-ops" in done.stderr
