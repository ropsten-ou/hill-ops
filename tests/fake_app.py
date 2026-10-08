"""A stand-in app for the end-to-end test. It says hello, then acts on lines
typed into its pane: "open" asks for its settings, "cmd" for the command
line, "ask" asks a question, "fail" reports an error, "panes" runs two
programs beside it and "nopanes" none, "over" runs one over them, "quit"
exits with 3. It prints what hill-ops sends."""

import asyncio
import os
import sys

from hill_client import Client


async def main() -> int:
    client = Client.from_env(
        "fake", os.environ["FAKE_PROFILE"], [(",", "settings", "open"), ("q", "quit", "quit")],
        commands=[("greet", "NAME", "say hello to NAME")],
    )
    client.on("settings.changed", lambda p: print("changed", p["group"], p["value"], flush=True))
    client.on("command", lambda p: print("command", p["line"], flush=True))
    client.on("answer", lambda p: print("answer", p["value"], flush=True))
    client.on("over.done", lambda p: print("over.done", p["status"], flush=True))
    task = asyncio.create_task(client.run())
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    print("fake: ready", flush=True)
    while line := await reader.readline():
        word = line.decode().strip()
        if word == "open":
            client.send("settings.open", group="preview")
        elif word == "cmd":
            client.send("command.open")
        elif word == "ask":
            client.send("ask", id=1, question="Your name?")
        elif word == "fail":
            client.send("error", what="fake failure")
        elif word == "panes":
            client.send("panes", view={"name": "preview", "argv": ["sleep", "600"]},
                        claude={"name": "Claude", "argv": ["sleep", "600"]})
        elif word == "nopanes":
            client.send("panes")
        elif word == "over":
            client.send("over", name="editor", argv=["sh", "-c", "sleep 1.5; exit 5"])
        elif word == "quit":
            break
    print("fake: bye", flush=True)
    client.close()
    await task
    return 3


sys.exit(asyncio.run(main()))
