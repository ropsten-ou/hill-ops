from hill_ops.keytree import check_tree, opener, sequences

COMMANDS = [["set-status", "STATUS", "set the status", ["open", "done"]], ["work", "", "work on it"], ["sync", ""]]


def test_check_tree_keeps_what_runs_a_command_and_says_what_doesnt():
    tree, problems = check_tree([
        ["t", "status", [["o", "open", "set-status open"], ["f", "done", "set-status done"], ["f", "again", "set-status open"]]],
        ["w", "work", "work"],
        ["x", "nothing", "explode"],
        ["e", "empty", [["z", "nothing", "explode"]]],
        ["bad"],
    ], COMMANDS)
    assert tree == [["t", "status", [["o", "open", "set-status open"], ["f", "done", "set-status done"]]], ["w", "work", "work"]]
    assert problems == [
        "key “t f” is used twice",
        "key “x” runs “explode”, not a command",
        "key “e z” runs “explode”, not a command",
        "a node under “the top” isn't [key, label, line or nodes]",
    ]
    assert check_tree("not a list", COMMANDS) == ([], [])


def test_sequences_start_with_the_key_that_opens_the_tree():
    tree = [
        ["t", "status", [["o", "open", "set-status open"], ["f", "done", "set-status done"]]],
        ["s", "scripts", [["s", "sync", "sync"]]],
        ["w", "work", "work"],
    ]
    first = opener([[",", "settings", "app.settings"], ["Space", "keys", "hill.tree"]])
    assert first == "Space"
    assert sequences(tree, first) == {"set-status": "Space t …", "sync": "Space s s", "work": "Space w"}
    assert sequences(tree) == {"set-status": "t …", "sync": "s s", "work": "w"}
    assert opener([]) is None
