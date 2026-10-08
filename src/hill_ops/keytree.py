"""An app's key tree: its commands reached by short sequences of keys, from
a menu labelled with the keys that reach each one, so reading the menu is
pressing the fast way's keys (after ExNovo, and Emacs's which-key).

The app sends the tree in its hello, nodes `[key, label, line]` for a
command, `line` being what the strip sends back in `command`, or `[key,
label, [nodes...]]` for a group. The key that opens it is the app's: the
app-wide key whose action is `hill.tree`, if it has one.
"""

from __future__ import annotations

OPEN_ACTION = "hill.tree"
"""The action of an app-wide key that opens the tree, so hill-ops knows its key."""


def check_tree(nodes: object, commands: list[list]) -> tuple[list[list], list[str]]:
    """The tree as hill-ops shows it, and what's wrong with what the app sent:
    a command the app doesn't have, a key used twice at one level, a node
    that isn't one. What's wrong is left out; a group left empty too."""
    names = {c[0] for c in commands if c and isinstance(c[0], str)}
    problems: list[str] = []

    def level(nodes: object, path: str) -> list[list]:
        kept: list[list] = []
        for node in nodes if isinstance(nodes, list) else []:
            if not (isinstance(node, list) and len(node) == 3 and isinstance(node[0], str) and node[0]
                    and isinstance(node[1], str) and isinstance(node[2], (str, list))):
                problems.append(f"a node under “{path or 'the top'}” isn't [key, label, line or nodes]")
                continue
            key, label, below = node
            if any(k[0] == key for k in kept):
                problems.append(f"key “{(path + ' ' + key).strip()}” is used twice")
                continue
            if isinstance(below, str):
                if below.split(" ", 1)[0] not in names:
                    problems.append(f"key “{(path + ' ' + key).strip()}” runs “{below}”, not a command")
                    continue
                kept.append([key, label, below])
            elif below := level(below, (path + " " + key).strip()):
                kept.append([key, label, below])
        return kept

    return level(nodes, ""), problems


def opener(keys: list[list]) -> str | None:
    """The app's key that opens its tree: the one whose action is hill.tree."""
    return next((str(k[0]) for k in keys if len(k) > 2 and k[2] == OPEN_ACTION), None)


def sequences(tree: list[list], first: str | None = None) -> dict[str, str]:
    """Each command's keys in the tree, after `first`, the key that opens
    it: `Space w`, or `Space t …` for one reached by several, such as each
    of its choices, up to where their sequences part."""
    found: dict[str, list[list[str]]] = {}

    def walk(nodes: list[list], path: list[str]) -> None:
        for key, _, below in nodes:
            if isinstance(below, str):
                found.setdefault(below.split(" ", 1)[0], []).append([*path, key])
            else:
                walk(below, [*path, key])

    walk(tree, [first] if first else [])
    shown = {}
    for name, paths in found.items():
        common = paths[0]
        for p in paths[1:]:
            n = next((i for i, (a, b) in enumerate(zip(common, p)) if a != b), min(len(common), len(p)))
            common = common[:n]
        shown[name] = " ".join(common) if len(paths) == 1 else " ".join([*common, "…"])
    return shown
