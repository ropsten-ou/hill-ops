import asyncio

from textual.app import App, ComposeResult

from keyline import Key, Keyline


class Demo(App):
    def __init__(self, width_keys, right=""):
        super().__init__()
        self.width_keys = width_keys
        self.right = right
        self.ran = []

    def compose(self) -> ComposeResult:
        yield Keyline(*self.width_keys, right=self.right, id="line")

    def action_note(self, name: str) -> None:
        self.ran.append(name)


def run(keys, size=(40, 3), clicks=(), right=""):
    result = {}

    async def go():
        app = Demo(keys, right)
        async with app.run_test(size=size) as pilot:
            line = app.query_one(Keyline)
            await pilot.pause()
            for x in clicks:
                await pilot.click(Keyline, offset=(x, 0))
                await pilot.pause()
            result["text"] = str(line.render())
            result["ran"] = app.ran

    asyncio.run(go())
    return result


def test_shows_key_label_pairs():
    text = run([("n", "new"), ("q", "quit")])["text"]
    assert text == "  n  new   q  quit  "


def test_drops_hints_that_do_not_fit():
    keys = [(str(i), "something long") for i in range(5)]
    text = run(keys, size=(40, 3))["text"]
    assert "0" in text and "1" in text and "4" not in text


def test_click_runs_action():
    # " n  new" starts at column 1; clicking the label runs the action.
    ran = run([Key("n", "new", "app.note('new')"), Key("q", "quit", "app.note('quit')")], clicks=[5, 12])["ran"]
    assert ran == ["new", "quit"]


def test_click_between_hints_does_nothing():
    assert run([Key("n", "new", "app.note('new')")], clicks=[0])["ran"] == []


def test_a_hint_without_a_key_is_just_its_label():
    text = run([("", "help"), ("q", "quit")])["text"]
    assert text == "  help   q  quit  "


def test_right_text_sits_at_the_right_end():
    text = run([("q", "quit")], size=(30, 3), right="14:05")["text"]
    assert text == "  q  quit" + " " * 15 + "14:05 "
    assert len(text) == 30


def test_right_text_goes_before_any_hint_does():
    keys = [(str(i), "something long") for i in range(5)]
    assert "14:05" not in run(keys, size=(40, 3), right="14:05")["text"]
    # Room for the hints but not the text: the hints stay.
    assert run([("q", "quit")], size=(13, 3), right="14:05")["text"] == "  q  quit  "
