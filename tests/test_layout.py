"""hill-ops's window, laid out in a tmux server of the test's own: the panes an
app runs beside it, a program over them, and their widths."""

import asyncio
import os
import shutil
import subprocess

import pytest

from hill_ops.host import tmux_socket
from hill_ops.layout import APP_WIDTH, CLAUDE_WIDTH, Layout, Side, Tmux, layout_panes, nearest, share, window_size
from hill_ops.runner import over_channel

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")


class LabTmux(Tmux):
    """tmux commands on the test's own server."""

    def __init__(self, name):
        self.name = name

    def run(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        return subprocess.run(["tmux", "-L", self.name, *args], capture_output=True, text=True, env=env).stdout.strip()

    async def wait(self, channel):
        process = await asyncio.create_subprocess_exec("tmux", "-L", self.name, "wait-for", channel)
        await process.wait()


@pytest.fixture
def lab():
    """A 200×50 window with an app and a one-row strip under it, both
    sleeping, and the Layout of it."""
    name = f"hill-layout-test-{os.getpid()}"
    tmux = LabTmux(name)
    app = tmux.run("-f", os.devnull, "new-session", "-d", "-s", "hill", "-x", "200", "-y", "50", "-P", "-F", "#{pane_id}", "sleep 600")
    tmux.run("set", "-g", "status", "off")
    strip = tmux.run("split-window", "-v", "-d", "-l", "1", "-t", app, "-P", "-F", "#{pane_id}", "sleep 600")
    tmux.run("set", "-g", "@hill-height", "1")
    yield tmux, Layout(tmux, app, strip, {})
    tmux.run("kill-server")
    tmux_socket(name).unlink(missing_ok=True)


def geometry(tmux):
    """Each pane in hill-ops's window: (left, top, width, height), by pane."""
    rows = tmux.run("list-panes", "-t", "hill", "-F", "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height}")
    return {pane: tuple(map(int, rest)) for pane, *rest in (line.split() for line in rows.splitlines())}


def everywhere(tmux):
    return set(tmux.run("list-panes", "-a", "-F", "#{pane_id}").split())


SLEEP = ("sleep", "600")


def test_panes_beside_the_app_with_the_strip_under_them(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    view, claude = layout.side["view"][1], layout.side["claude"][1]
    panes = geometry(tmux)
    assert panes[layout.app] == (0, 0, 50, 50)  # 25%, the window's full height
    assert panes[claude][2] == 60  # 30%
    assert panes[view] == (51, 0, 88, 48)  # the rest, beside the app
    assert panes[layout.strip] == (51, 49, 149, 1)  # under the two
    assert len(panes) == 4


def test_the_claude_pane_hides_and_comes_back(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    claude = layout.side["claude"][1]
    layout.claude_shown = False
    layout.arrange()
    assert claude not in geometry(tmux) and claude in everywhere(tmux)  # out of sight, still running
    assert geometry(tmux)[layout.side["view"][1]][2] == 149
    layout.claude_shown = True
    layout.arrange()
    assert geometry(tmux)[claude][2] == 60


def test_a_program_over_the_side_panes_and_back(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    view, claude = layout.side["view"][1], layout.side["claude"][1]
    pane = layout.open_over(Side("micro", ("sh", "-c", "exit 3")), ("bg=#121212", "bg=#272727"))
    assert pane is not None
    asyncio.run(asyncio.wait_for(tmux.wait(over_channel(pane)), 10))  # it has exited, through the relay
    assert tmux.run("display", "-p", "-t", pane, "#{@hill-status}") == "3"
    assert [tmux.run("show", "-pv", "-t", pane, style) for style in ("window-style", "window-active-style")] == [
        "bg=#121212", "bg=#272727",  # lighter with the focus, as the panes it's over
    ]
    assert set(geometry(tmux)) == {layout.app, pane, layout.strip}
    assert geometry(tmux)[pane] == (51, 0, 149, 48)
    assert {view, claude} <= everywhere(tmux)
    layout.close_over()
    panes = geometry(tmux)
    assert set(panes) == {layout.app, view, claude, layout.strip}
    assert panes[view] == (51, 0, 88, 48) and panes[claude][2] == 60
    assert pane not in everywhere(tmux)


def test_one_program_over_the_side_panes_at_a_time(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), None)
    assert layout.open_over(Side("micro", SLEEP)) is not None
    assert layout.open_over(Side("micro", SLEEP)) is None


def test_the_editor_hears_how_wide_the_claude_pane_was(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    pane = layout.open_over(Side("micro", ("sh", "-c", 'tmux set -p @seen "$HILL_CLAUDE_COLUMNS"; sleep 600')))
    for _ in range(50):
        if tmux.run("display", "-p", "-t", pane, "#{@seen}"):
            break
        asyncio.run(asyncio.sleep(0.1))
    assert tmux.run("display", "-p", "-t", pane, "#{@seen}") == "60"


def test_panes_the_app_no_longer_runs_stop(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    view, claude = layout.side["view"][1], layout.side["claude"][1]
    layout.set_side(None, Side("Claude", SLEEP))  # the preview hidden
    assert view not in everywhere(tmux)
    assert geometry(tmux)[claude] == (51, 0, 149, 48)  # the Claude pane takes its place
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    view = layout.side["view"][1]
    assert geometry(tmux)[view][:2] == (51, 0) and geometry(tmux)[claude][2] == 60  # the preview back, on the left
    layout.set_side(None, None)
    assert geometry(tmux) == {layout.app: (0, 0, 200, 48), layout.strip: (0, 49, 200, 1)}
    assert {view, claude} & everywhere(tmux) == set()


def test_a_program_that_changed_starts_again(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), None)
    first = layout.side["view"][1]
    layout.set_side(Side("preview", SLEEP), None)
    assert layout.side["view"][1] == first  # the same: left running
    layout.set_side(Side("preview", ("sleep", "601")), None)
    assert layout.side["view"][1] != first and first not in everywhere(tmux)


def test_one_of_another_name_puts_the_one_there_out_of_sight(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("024", SLEEP))
    first = layout.side["claude"][1]
    layout.set_side(Side("preview", SLEEP), Side("022", SLEEP))
    second = layout.side["claude"][1]
    assert second != first and first in everywhere(tmux)  # still running
    assert set(geometry(tmux)) == {layout.app, layout.strip, layout.side["view"][1], second}
    assert geometry(tmux)[second] == (140, 0, 60, 48)  # in the Claude pane's place
    assert layout.panes() >= {first, second}
    layout.set_side(Side("preview", SLEEP), Side("024", SLEEP))
    assert layout.side["claude"][1] == first and second in everywhere(tmux)  # back as it was
    assert geometry(tmux)[first] == (140, 0, 60, 48)
    layout.set_side(Side("preview", SLEEP), Side("022", ("sleep", "601")))
    assert layout.side["claude"][1] not in (first, second) and second not in everywhere(tmux)  # it changed


def test_panes_end_stops_them_out_of_sight_or_shown(lab):
    tmux, layout = lab
    layout.set_side(None, Side("024", SLEEP))
    first = layout.side["claude"][1]
    layout.set_side(None, Side("022", SLEEP))
    second = layout.side["claude"][1]
    layout.end({"024"})
    assert first not in everywhere(tmux) and layout.side["claude"][1] == second
    layout.end({"022"})
    assert second not in everywhere(tmux) and "claude" not in layout.side
    assert geometry(tmux) == {layout.app: (0, 0, 200, 48), layout.strip: (0, 49, 200, 1)}


def test_one_out_of_sight_that_exits_is_forgotten_without_a_note(lab):
    tmux, layout = lab
    layout.set_side(None, Side("024", SLEEP))
    first = layout.side["claude"][1]
    layout.set_side(None, Side("022", SLEEP))
    tmux.run("kill-pane", "-t", first)
    assert layout.forget(everywhere(tmux)) == [] and layout.parked == {}


def test_a_side_program_that_exits_is_forgotten(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), None)
    tmux.run("kill-pane", "-t", layout.side["view"][1])
    assert layout.forget(everywhere(tmux)) == ["preview"]
    assert geometry(tmux) == {layout.app: (0, 0, 200, 48), layout.strip: (0, 49, 200, 1)}


def test_the_widths_apply_again_when_the_window_changes_size(lab):
    tmux, layout = lab
    tmux.run("set-hook", "-g", "window-resized", "run-shell -C '#{E:@hill-relayout}'")  # as hill-ops's host sets it
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    claude = layout.side["claude"][1]
    tmux.run("resize-window", "-t", "hill", "-x", "120", "-y", "40")
    panes = geometry(tmux)
    assert panes[layout.app][2] == 30 and panes[claude][2] == 36 and panes[layout.strip][3] == 1
    layout.app_width = "40%"
    layout.apply()
    assert geometry(tmux)[layout.app][2] == 48


def test_widths_and_layouts_in_numbers():
    assert share("25%", "30%") == 0.25 and share("nonsense", "30%") == 0.3
    assert nearest(0.27) == "25%" and nearest(0.9) == "50%" and nearest(0.01) == "15%"
    layout = "718d,200x50,0,0{50x50,0,0,0,149x50,51,0[149x48,51,0{88x48,51,0,2,60x48,140,0,3},149x1,51,49,1]}"
    assert window_size(layout) == (200, 50)
    assert layout_panes(layout) == {"%0", "%1", "%2", "%3"}


def test_sides_as_apps_send_them():
    assert Side.parse({"name": "preview", "argv": ["palace", "_preview"], "cwd": "/p"}) == Side("preview", ("palace", "_preview"), "/p")
    assert Side.parse({"argv": ["/usr/bin/micro", "a.md"]}).name == "micro"
    assert Side.parse({"argv": []}) is None and Side.parse(None) is None and Side.parse({"argv": [1]}) is None


def test_a_dragged_border_gives_the_nearest_widths(lab):
    tmux, layout = lab
    layout.set_side(Side("preview", SLEEP), Side("Claude", SLEEP))
    claude = layout.side["claude"][1]

    def sizes():
        return {pane: (w, h) for pane, (_, _, w, h) in geometry(tmux).items()}

    assert layout.dragged(sizes(), 200) == []  # as the settings say
    tmux.run("resize-pane", "-t", layout.app, "-x", "71")  # as a drag would; the Claude pane shrinks too
    assert layout.dragged(sizes(), 200) == [(APP_WIDTH, "35%")]
    layout.app_width = "35%"
    layout.apply()
    assert geometry(tmux)[layout.app][2] == 70 and geometry(tmux)[claude][2] == 60
    tmux.run("resize-pane", "-t", claude, "-x", "37")
    assert layout.dragged(sizes(), 200) == [(CLAUDE_WIDTH, "20%")]
