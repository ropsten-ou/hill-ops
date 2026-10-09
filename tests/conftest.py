import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    """Every test keeps its event logs, hill-ops's settings, its instances and
    hill-ops's work items in its own folder, never the real ones, and no test runs the real Claude; a test can still
    point any of them elsewhere, such as at the stand-in agent (agent_log)."""
    monkeypatch.setenv("HILL_CONFIG_HOME", str(tmp_path / "hill"))
    monkeypatch.setenv("HILL_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HILL_CLAUDE_AGENT", "no-claude-in-tests")
    monkeypatch.setenv("HILL_WORK_DIR", str(tmp_path / "work"))  # hill's own work items: none, unless a test makes the folder
    monkeypatch.delenv("HILL_INSTANCE", raising=False)  # inside hill, the instance is yours; a test that wants one sets its own
    monkeypatch.delenv("TMUX", raising=False)  # nor your tmux: a copy would go to your clipboard (hill-client's copy)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    return tmp_path / "state"


@pytest.fixture
def agent_log(tmp_path, monkeypatch):
    """A stand-in agent (fake_agent.py) runs instead of claude-agent-acp;
    what it gets is written here."""
    monkeypatch.setenv("HILL_CLAUDE_AGENT", f"{sys.executable} {Path(__file__).with_name('fake_agent.py')}")
    monkeypatch.setenv("FAKE_AGENT_LOG", str(tmp_path / "agent.jsonl"))
    return tmp_path / "agent.jsonl"
