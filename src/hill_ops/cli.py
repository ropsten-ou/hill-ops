"""The hill-ops command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

USAGE = """\
usage: hill-ops [--] COMMAND [ARGS...]   run COMMAND above hill-ops's strip, in
                                         the instance of COMMAND used last
                                         that isn't running
       hill-ops --resume [--] COMMAND ...  pick the instance from a list
       hill-ops --new [--] COMMAND ...     in a new instance
       hill-ops --as ID [--] COMMAND ...   in instance ID, made if there's none
       hill-ops settings [GROUP]         the settings on their own, open on
                                         GROUP
       hill-ops docs [APP]               APP's reference in Markdown, from
                                         what it declares (hill-ops's own without
                                         APP)
       hill-ops connect                  the channel over stdin and stdout,
                                         for apps that can't open a socket
"""


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, end="", file=sys.stdout if args else sys.stderr)
        sys.exit(0 if args else 2)
    command, rest = args[0], args[1:]
    if command == "settings" and len(rest) <= 1:
        from .panel import SettingsApp

        SettingsApp(rest[0] if rest else None).run()
    elif command == "docs" and len(rest) <= 1:
        from .reference import Unknown, reference

        try:
            text, gaps = reference(rest[0] if rest else "hill")
        except Unknown as e:
            print(f"hill-ops docs: {e}", file=sys.stderr)
            sys.exit(1)
        print(text, end="")
        for gap in gaps:
            print(f"hill-ops docs: undocumented: {gap}", file=sys.stderr)
    elif command == "connect" and not rest:
        from .connect import main as bridge

        sys.exit(bridge())
    elif command == "_run":
        from .runner import main as run_app

        sys.exit(run_app())
    elif command == "_side":
        from .runner import side

        sys.exit(side())
    elif command == "_over":
        from .runner import over

        sys.exit(over())
    elif command == "_strip":
        from hill_client import SOCKET

        from .events import EventLog
        from .panel import Panel

        log = os.environ.get("HILL_EVENTS")
        Panel(Path(os.environ[SOCKET]), os.environ.get("HILL_APP_PANE"), event_log=EventLog(Path(log) if log else None)).run()
    else:
        choice = name = None
        if command in ("--resume", "--new"):
            choice, args = command[2:], rest
        elif command == "--as" and rest:
            name, args = rest[0], rest[1:]
        argv = args[1:] if args[:1] == ["--"] else args
        if not argv:
            print(USAGE, end="", file=sys.stderr)
            sys.exit(2)
        from .host import run

        sys.exit(run(argv, choice, name))
