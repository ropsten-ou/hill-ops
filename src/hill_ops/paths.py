"""Where hill-ops keeps things: its settings and the links to app profiles, the
event log, and each session's socket. Each has an environment variable that
points it elsewhere, which the tests use.

It imports nothing heavy, so the host can use it: the settings panel takes
0.4 s to import.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def config_home() -> Path:
    """hill's own folder: $HILL_CONFIG_HOME, else $XDG_CONFIG_HOME/hill, else
    ~/.config/hill."""
    if env := os.environ.get("HILL_CONFIG_HOME"):
        return Path(env)
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hill"


def state_home() -> Path:
    """What hill records, such as the event log: $HILL_STATE_HOME, else
    $XDG_STATE_HOME/hill, else ~/.local/state/hill."""
    if env := os.environ.get("HILL_STATE_HOME"):
        return Path(env)
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "hill"


def runtime_dir() -> Path:
    """Where hill-ops keeps each session's socket and exit record:
    $HILL_RUNTIME_DIR, else a private folder in the temporary folder."""
    if env := os.environ.get("HILL_RUNTIME_DIR"):
        return Path(env)
    return Path(tempfile.gettempdir()) / f"hill-{os.getuid()}"
