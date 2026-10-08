import asyncio
import json
import sys
from pathlib import Path

import pytest

from acp_client import Agent, AgentError

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent.py"))]


@pytest.fixture
def agent_log(tmp_path, monkeypatch):
    """What the stand-in agent gets, a message a line."""
    monkeypatch.setenv("FAKE_AGENT_LOG", str(tmp_path / "agent.jsonl"))
    return tmp_path / "agent.jsonl"


def received(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def ask(*questions, meta=None, permission=None, command=FAKE):
    async def go():
        agent = Agent(command, Path.cwd(), meta, permission)
        try:
            return [await agent.ask(q) for q in questions]
        finally:
            await agent.stop()

    return asyncio.run(go())


def test_one_session_answers_each_question(agent_log):
    meta = {"systemPrompt": "tips", "claudeCode": {"options": {"tools": []}}}
    assert ask("first", "second", meta=meta) == ["first", "second"]
    methods = [m.get("method") for m in received(agent_log)]
    assert methods == ["initialize", "session/new", "session/prompt", "session/prompt"]
    new = received(agent_log)[1]["params"]
    assert new == {"cwd": str(Path.cwd()), "mcpServers": [], "_meta": meta}
    first = received(agent_log)[0]["params"]["clientCapabilities"]
    assert first == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


def test_the_agent_runs_outside_hill(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "env")
    monkeypatch.setenv("HILL_SOCKET", "/tmp/hill.sock")  # the app is inside hill
    monkeypatch.setenv("HILL_CONFIG_HOME", "/tmp/hill")
    assert ask("HILL_SOCKET", "HILL_CONFIG_HOME") == ["unset", "/tmp/hill"]


def carry_on(session, *questions):
    """Start an agent on an earlier session, then ask: what it told again,
    its answers, and its session."""
    async def go():
        agent = Agent(FAKE, Path.cwd(), session=session)
        heard = []
        try:
            await agent.start(on_update=heard.append)
            return heard, [await agent.ask(q) for q in questions], agent.session
        finally:
            await agent.stop()

    return asyncio.run(go())


def test_an_earlier_session_is_carried_on(agent_log):
    heard, answers, session = carry_on("earlier", lambda fresh: f"fresh: {fresh}")
    assert [(u["sessionUpdate"], u["content"]["text"]) for u in heard] == [
        ("user_message_chunk", "earlier"), ("agent_message_chunk", "said earlier")]
    assert answers == ["fresh: False"] and session == "earlier"
    load, prompt = [m for m in received(agent_log) if m.get("method") in ("session/load", "session/prompt")]
    assert load["params"] == {"sessionId": "earlier", "cwd": str(Path.cwd()), "mcpServers": []}
    assert prompt["params"]["sessionId"] == "earlier"


@pytest.mark.parametrize("mode", ["gone", "noload"])
def test_a_session_that_cant_be_carried_on_gives_way_to_a_new_one(agent_log, monkeypatch, mode):
    monkeypatch.setenv("FAKE_AGENT", mode)
    heard, answers, session = carry_on("earlier", lambda fresh: f"fresh: {fresh}")
    assert heard == [] and answers == ["fresh: True"] and session == "s1"
    methods = [m.get("method") for m in received(agent_log) if m.get("method")]
    tried = ["session/load"] if mode == "gone" else []
    assert methods == ["initialize", *tried, "session/new", "session/prompt"]


def test_without_meta_the_session_has_the_agents_own_settings(agent_log):
    ask("hello")
    assert "_meta" not in received(agent_log)[1]["params"]


def test_permissions_are_turned_down_without_a_handler(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "tool")
    ask("hello")
    (reply,) = [m for m in received(agent_log) if m.get("id") == 99]
    assert reply["result"] == {"outcome": {"outcome": "cancelled"}}


def test_a_handler_chooses_a_permissions_option(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "tool")
    asked = []

    async def permission(request):
        asked.append((request["toolCall"]["title"], [o["kind"] for o in request["options"]]))
        await asyncio.sleep(0.05)  # as a person would, a while later
        return "yes"

    assert ask("hello", permission=permission) == ["hello"]
    assert asked == [("Edit notes.md", ["allow_once", "reject_once"])]
    (reply,) = [m for m in received(agent_log) if m.get("id") == 99]
    assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "yes"}}


def test_updates_are_heard_as_they_come(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "tool")
    heard, grown = [], []

    async def go():
        agent = Agent(FAKE, Path.cwd())
        try:
            return await agent.ask("hello", grown.append, heard.append)
        finally:
            await agent.stop()

    assert asyncio.run(go()) == "hello"
    assert [u["sessionUpdate"] for u in heard] == ["tool_call", "tool_call_update", "agent_message_chunk", "agent_message_chunk"]
    assert grown == ["he", "hello"]


def test_a_logged_out_agent_says_so_in_the_clients_words(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "login")
    with pytest.raises(AgentError) as e:
        ask("hello")
    assert str(e.value) == "Claude needs a login: run claude, then /login"  # not the agent's message, with its path


def test_an_agent_that_stops_or_is_missing_is_an_error(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "exit")
    with pytest.raises(AgentError, match="stopped"):
        ask("hello")
    with pytest.raises(AgentError, match="no-such-agent-here"):
        ask("hello", command=["no-such-agent-here"])


def test_questions_wait_their_turn_and_the_first_hears_the_session_is_new(agent_log):
    fresh = []

    def question(text):
        def make(new):
            fresh.append(new)
            return text
        return make

    async def go():
        agent = Agent(FAKE, Path.cwd())
        try:
            return await asyncio.gather(agent.ask(question("one")), agent.ask(question("two")))
        finally:
            await agent.stop()

    assert asyncio.run(go()) == ["one", "two"]
    assert fresh == [True, False]


def test_giving_up_on_an_answer_stops_the_agents_turn(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "hang")

    async def go():
        agent = Agent(FAKE, Path.cwd())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(agent.ask("tip"), 0.5)
            for _ in range(100):
                if any(m.get("method") == "session/cancel" for m in received(agent_log)):
                    return
                await asyncio.sleep(0.02)
        finally:
            await agent.stop()

    asyncio.run(go())
    (cancel,) = [m for m in received(agent_log) if m.get("method") == "session/cancel"]
    assert cancel["params"] == {"sessionId": "s1"}


def test_cancel_ends_the_turn_with_the_answer_so_far(agent_log, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "hang")

    async def go():
        agent = Agent(FAKE, Path.cwd())
        try:
            asking = asyncio.create_task(agent.ask("hello"))
            while not agent.busy or agent.session is None:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.1)
            agent.cancel()
            return await asyncio.wait_for(asking, 5), agent.busy
        finally:
            await agent.stop()

    assert asyncio.run(go()) == ("", False)
