"""The channel over stdin and stdout, for apps that can't open a socket, such
as micro's Lua plugins: `hill-ops connect`.

Lines read from stdin go to hill-ops, and lines from hill-ops go to stdout. If the
strip restarts, the bridge connects again and repeats the app's last hello.
It exits when stdin closes, so it never outlives its app.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from hill_client import SOCKET, connect


def is_hello(raw: bytes) -> bool:
    try:
        return json.loads(raw).get("method") == "hello"
    except (ValueError, AttributeError):
        return False


def with_pane(raw: bytes) -> bytes:
    """A hello with the tmux pane the app runs in, if it didn't say: the
    bridge runs in the app's pane, so it knows."""
    pane = os.environ.get("TMUX_PANE")
    try:
        message = json.loads(raw)
        params = message.setdefault("params", {})
        if not pane or not isinstance(params, dict) or params.get("pane"):
            return raw
        params["pane"] = pane
        return (json.dumps(message) + "\n").encode()
    except (ValueError, AttributeError):
        return raw


async def bridge(path: str) -> None:
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)
    writer: asyncio.StreamWriter | None = None
    hello: bytes | None = None
    waiting: list[bytes] = []

    async def from_hill() -> None:
        nonlocal writer
        while True:
            try:
                reader, writer_ = await connect(path, wait=5)
            except OSError:
                await asyncio.sleep(1)
                continue
            if hello is not None and hello not in waiting:
                writer_.write(hello)  # the strip restarted: say hello again
            for raw in waiting:
                writer_.write(raw)
            waiting.clear()
            writer = writer_
            try:
                while raw := await reader.readline():
                    sys.stdout.buffer.write(raw)
                    sys.stdout.buffer.flush()
            except (OSError, ValueError):
                pass
            writer = None
            writer_.close()

    task = asyncio.create_task(from_hill())
    while raw := await stdin.readline():
        if is_hello(raw):
            raw = hello = with_pane(raw)
        if writer is not None:
            writer.write(raw)
        else:
            waiting.append(raw)
    task.cancel()  # the app has gone


def main() -> int:
    path = os.environ.get(SOCKET)
    if not path:
        print(f"hill-ops connect: ${SOCKET} isn't set; run it inside hill-ops", file=sys.stderr)
        return 2
    asyncio.run(bridge(path))
    return 0
