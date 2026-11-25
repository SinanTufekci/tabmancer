"""The desktop window's team widget (the Orbit, desktop/teamgraph.py), offscreen on a made-up team with
a clock of the test's own. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tabmancer import teams
from tabmancer import viewmodel as VM
from tabmancer.config import read_json
from tabmancer.desktop import look, teamgraph

look.fonts_for_offscreen()
T0 = datetime(2026, 1, 5, 10, 0)
COLOURS = {"lead": "#d97757", "api": "#56b6c2", "ui": "#98c379", "tests": "#e5c07b", "docs": "#c678dd",
           teams.YOU: look.TEXT, teams.TABMANCER: look.MUTED}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


class Clock:
    def __init__(self) -> None:
        self.at = 100.0

    def __call__(self) -> float:
        return self.at


def member(name, state="idle", label="idle", tone="done", **more):
    return VM.MemberView(name, name == "lead", "", f"id-{name}", state, label, tone, model="sonnet", **more)


def team(*members, wires=(), peers=False):
    names = [m.name for m in members]
    return VM.TeamView("shop", "C:/work/shop", "", len(members), 0, list(members), list(wires), [],
                       peers=peers, lead=None if peers else ("lead" if "lead" in names else None))


def resting_team():
    return team(member("lead"), member("api"), member("ui"), member("tests"),
                wires=[{"from": "lead", "to": "api", "count": 2}, {"from": "api", "to": "lead", "count": 1}])


def busy_team():
    return team(member("lead", "working", "working", "working"), member("api"), member("ui"),
                member("tests", "needs-you", "needs you", "your-turn!", needs_you=True))


def said(*pairs):
    return [teams.Message(T0 + timedelta(seconds=k), a, b, f"message {k} from {a} to {b}")
            for k, (a, b) in enumerate(pairs)]


@pytest.fixture
def graph(qapp):
    made = []

    def make(compact=False, mode="full"):
        clock = Clock()
        shown = teamgraph.TeamGraph(compact=compact, now=clock)
        shown.set_motion(mode)
        shown.resize(380, 300) if compact else shown.resize(900, 560)
        shown.show()
        shown.clock = clock
        made.append(shown)
        return shown

    yield make
    for shown in made:
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def test_motion_is_full_until_picked_and_calm_with_bit():
    assert teamgraph.motion() == "full"
    assert teamgraph.save_motion("off")
    assert read_json("config.json") == {"motion": "off"}
    assert teamgraph.motion() == "off"
    teamgraph.save_motion("full")
    from tabmancer.config import merge_json

    merge_json("config.json", mascot="calm")  # b in the dashboard calms Bit: the team widget too
    assert teamgraph.motion() == "calm"
    teamgraph.save_motion("off")
    assert teamgraph.motion() == "off"  # calm Bit never brings motion back
    merge_json("config.json", motion="sideways")
    assert teamgraph.saved_motion() == "full"


def test_how_a_member_is_drawn():
    assert teamgraph.standing(member("a", "needs-you", "needs you", "your-turn!", needs_you=True)) == "needs"
    assert teamgraph.standing(member("a", "waiting", "waiting for you", "your-turn")) == "needs"
    assert teamgraph.standing(member("a", "working", "working", "working")) == "working"
    assert teamgraph.standing(member("a", "limit", "◐ limit · 16:00", "to-continue")) == "stopped"
    assert teamgraph.standing(member("a", "idle", "idle", "done")) == "resting"  # open, done for now
    assert teamgraph.standing(member("a", "shell", "background command", "aside")) == "resting"
    assert teamgraph.standing(member("a", "done", "✓ done", "done")) == "closed"  # its session ended
    assert teamgraph.standing(member("a", "not-started", "not started", "done")) == "closed"


def test_the_first_look_flies_nothing_then_each_new_message_flies(graph):
    g = graph()
    old = said(("lead", "api"), ("api", "lead"))
    g.set(resting_team(), old, COLOURS)
    assert g.flights == []  # history stays put
    g.clock.at += 1
    new = [*old, teams.Message(T0 + timedelta(minutes=1), "lead", "ui", "Take the cart page"),
           teams.Message(T0 + timedelta(minutes=2), teams.YOU, "lead", "Ship it after the tests"),
           teams.Message(T0 + timedelta(minutes=3), teams.TABMANCER, "api", "continue"),
           teams.Message(T0 + timedelta(minutes=4), "lead", "stranger", "not ours")]
    g.set(resting_team(), new, COLOURS)
    assert [(f.sender, f.receiver, f.words) for f in g.flights] == [
        ("lead", "ui", "Take the cart page"), (teamgraph.YOU, "lead", "Ship it after the tests")]
    assert g.flights[0].colour == COLOURS["lead"]
    assert g.flights[1].start - g.flights[0].start == pytest.approx(teamgraph.STAGGER)  # one after another
    assert g.you  # you wrote: your corner is drawn
    g.set(resting_team(), new, COLOURS)
    assert len(g.flights) == 2  # the same messages read again fly once


def test_a_burst_flies_only_its_newest(graph):
    g = graph()
    g.set(resting_team(), [], COLOURS)
    burst = [teams.Message(T0 + timedelta(seconds=k), "lead", "api", f"step {k}") for k in range(10)]
    g.set(resting_team(), burst, COLOURS)
    assert [f.words for f in g.flights] == [f"step {k}" for k in range(4, 10)]


def test_calm_starts_them_together_and_off_flies_none(graph):
    calm = graph(mode="calm")
    calm.set(resting_team(), [], COLOURS)
    calm.set(resting_team(), said(("lead", "api"), ("api", "ui")), COLOURS)
    assert {f.start for f in calm.flights} == {calm.clock.at}
    off = graph(mode="off")
    off.set(resting_team(), [], COLOURS)
    off.set(resting_team(), said(("lead", "api")), COLOURS)
    assert off.lit(off.flights[0], off.clock.at) == 1.0  # its wire lights
    assert not off.frames.isActive() and not off.breaths.isActive()  # nothing moves
    lit_ms = (teamgraph.FLY_SECONDS + teamgraph.LIT_SECONDS) * 1000
    assert off.lights.isActive() and lit_ms <= off.lights.interval() <= lit_ms + 50  # one paint puts it out
    off.clock.at += teamgraph.FLY_SECONDS + teamgraph.LIT_SECONDS
    assert off.lit(off.flights[0], off.clock.at) == 0.0  # then goes out
    off.lights.stop()  # as when it fires: a single shot
    off.lights_out()
    assert not off.lights.isActive() and off.flights == []  # nothing lit is left: nothing more to paint
    calm.clock.at += teamgraph.FLY_SECONDS + 0.01  # landed: no frames, but its wire still lit a while
    calm.on_frame()
    assert not calm.frames.isActive() and calm.lights.isActive()
    assert calm.lights.interval() <= teamgraph.LIT_SECONDS * 1000 + 30


def test_the_clock_runs_only_while_something_moves(graph):
    g = graph()
    g.set(resting_team(), [], COLOURS)
    assert not g.frames.isActive() and not g.breaths.isActive()  # a resting team: no timer at all
    g.set(resting_team(), said(("lead", "api")), COLOURS)
    assert g.frames.isActive()  # a message on its way
    g.clock.at += teamgraph.FLY_SECONDS + teamgraph.GLOW_SECONDS + 0.01
    g.on_frame()
    assert not g.frames.isActive() and not g.breaths.isActive()  # landed and faded: stopped again


def test_breathing_ticks_slowly_and_never_while_hidden_or_calm(graph):
    g = graph()
    g.set(busy_team(), [], COLOURS)
    assert g.breaths.isActive() and not g.frames.isActive()
    assert g.breaths.interval() == teamgraph.BREATH_MS
    g.hide()
    assert not g.breaths.isActive()
    g.show()
    assert g.breaths.isActive()
    g.set_motion("calm")
    assert not g.breaths.isActive()  # calm: nothing breathes
    g.set_motion("full")
    g.window().showMinimized()
    QApplication.processEvents()
    g.wake()
    assert not g.breaths.isActive()


def test_a_member_who_joins_grows_in_and_the_others_make_room(graph):
    g = graph()
    g.set(resting_team(), [], COLOURS)
    g.grab()  # drawn once: where each stands
    before = dict(g.drawn)
    grown = team(*resting_team().members, member("docs"))
    g.set(grown, [], COLOURS)
    assert g.came == {"docs"} and g.frames.isActive()
    g.clock.at += teamgraph.MOVE_SECONDS / 2
    spots, _, grow = g.placed(g.clock.at)
    assert 0 < grow["docs"] < 1  # growing in
    assert spots["api"] != before["api"]  # on its way to its new place
    g.clock.at += teamgraph.MOVE_SECONDS
    spots, _, grow = g.placed(g.clock.at)
    assert grow["docs"] == 1 and spots == g.stands()[0]
    g.set(resting_team(), [], COLOURS)  # docs leaves: shrinks where it was
    assert "docs" in g.went


def test_hover_says_who_and_click_and_right_click_ask_the_window(graph):
    g = graph()
    view = busy_team()
    view.members[1].last = VM.MessageView(T0, "lead", "api", "Split the checkout endpoint")
    g.set(view, said(("lead", "api")), COLOURS)
    g.grab()
    spot = g.drawn["api"]
    words = g.words_at(spot)
    assert "api" in words and "idle" in words and "Split the checkout endpoint" in words
    clicked, menus = [], []
    g.member_clicked.connect(clicked.append)
    g.member_menu.connect(lambda name, _at: menus.append(name))
    QTest.mouseClick(g, Qt.MouseButton.LeftButton, pos=spot.toPoint())
    assert clicked == ["api"]
    QApplication.sendEvent(g, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, spot.toPoint(),
                                                g.mapToGlobal(spot.toPoint())))
    assert menus == ["api"]
    QTest.mouseClick(g, Qt.MouseButton.LeftButton, pos=QPoint(2, g.height() - 2))  # nothing there
    assert clicked == ["api"]


def test_a_wire_says_how_much_it_carried(graph):
    g = graph()
    g.set(resting_team(), [], COLOURS)
    g.grab()
    middle = teamgraph.wire_path(g.drawn["api"], g.drawn["lead"], g.stands()[1]).pointAtPercent(0.5)
    assert "3 messages" in g.words_at(middle)  # both ways counted


def test_it_draws_in_each_motion_and_size(graph):
    for compact in (False, True):
        for mode in teamgraph.MOTIONS:
            g = graph(compact=compact, mode=mode)
            g.set(busy_team(), [], COLOURS, picked="api")
            g.set(busy_team(), said(("lead", "api"), (teams.YOU, "tests")), COLOURS, picked="api")
            g.clock.at += 0.5
            image = g.grab().toImage()
            assert image.width() > 0
            spot = g.drawn["tests"].toPoint()
            assert image.pixelColor(spot) != QColor(look.SUNKEN)  # the member is there
    empty = graph()
    assert empty.grab().toImage().width() > 0  # before anything is read: "Reading the team…"


def test_peers_have_no_middle(graph):
    g = graph()
    view = team(member("a"), member("b"), member("c"), peers=True)
    g.set(view, [], COLOURS)
    g.grab()
    centre = g.stands()[1]
    assert all((spot - centre).manhattanLength() > 40 for spot in g.drawn.values())
    assert isinstance(centre, QPointF)


def bubble_pixels(g):
    """How many pixels the bubbles' fill covers (nothing else in the picture is this colour)."""
    image = g.grab().toImage()
    want = QColor("#191919").rgb()
    return sum(image.pixel(x, y) == want for y in range(0, image.height(), 2) for x in range(0, image.width(), 2))


def test_a_bubble_shows_what_it_said_for_a_moment_once_it_lands(graph):
    g = graph()
    g.set(resting_team(), [], COLOURS)
    g.set(resting_team(), [teams.Message(T0, "lead", "ui", "Take the cart page")], COLOURS)
    flight = g.flights[0]
    assert g.bubbles(g.clock.at) == []  # on its way
    g.clock.at = flight.lands - 0.05
    before = bubble_pixels(g)
    g.clock.at = flight.lands + 0.5
    assert [(f.words, round(since, 2)) for f, since in g.bubbles(g.clock.at)] == [("Take the cart page", 0.5)]
    assert bubble_pixels(g) > before + 300  # drawn (counted on every other pixel each way)
    g.clock.at = flight.lands + teamgraph.BUBBLE_SECONDS
    assert g.bubbles(g.clock.at) == []
    assert bubble_pixels(g) <= before
    assert teamgraph.BUBBLE_SECONDS <= teamgraph.GLOW_SECONDS  # the glow's frames carry the bubble's


def test_one_bubble_a_member_the_newest(graph):
    for mode in ("full", "calm"):
        g = graph(mode=mode)
        g.set(resting_team(), [], COLOURS)
        g.set(resting_team(), [teams.Message(T0, "lead", "api", "first"),
                               teams.Message(T0 + timedelta(seconds=1), "ui", "api", "second"),
                               teams.Message(T0 + timedelta(seconds=2), "lead", "tests", "third")], COLOURS)
        g.clock.at = g.flights[-1].lands + 0.1
        assert sorted(f.words for f, _ in g.bubbles(g.clock.at)) == ["second", "third"], mode


def test_no_bubble_in_the_drawer_or_with_motion_off(graph):
    for g in (graph(compact=True), graph(mode="off")):
        g.set(resting_team(), [], COLOURS)
        g.set(resting_team(), said(("lead", "api")), COLOURS)
        g.clock.at = g.flights[0].lands + 0.1
        assert g.bubbles(g.clock.at) == []


def test_calm_paints_once_more_when_its_bubble_goes(graph):
    calm = graph(mode="calm")
    calm.set(resting_team(), [], COLOURS)
    calm.set(resting_team(), said(("lead", "api")), COLOURS)
    calm.clock.at = calm.flights[0].lands + 0.01
    calm.on_frame()
    assert not calm.frames.isActive()  # nothing moves in calm once landed
    gone_ms = (teamgraph.BUBBLE_SECONDS - 0.01) * 1000
    assert calm.lights.isActive() and gone_ms <= calm.lights.interval() <= gone_ms + 50


def test_its_window_minimized_rests_it_and_back_wakes_it(qapp):
    """Minimized, its window hides it and its clock stops; back, it starts again. The window's own
    signals are followed too (no event filter left on a window), and out of sight it rests."""
    holder = QWidget()
    g = teamgraph.TeamGraph(now=Clock())
    g.setParent(holder)
    holder.resize(900, 560)
    g.resize(900, 560)
    holder.show()
    try:
        g.set(busy_team(), [], COLOURS)
        assert g.breaths.isActive() and g.watched is holder.windowHandle()
        holder.showMinimized()
        qapp.processEvents()
        assert not g.breaths.isActive()
        holder.showNormal()
        qapp.processEvents()
        assert g.breaths.isActive()
        g.move(2000, 0)  # out of its window's sight, as a panel behind another tab
        g.wake()
        assert not g.breaths.isActive()
    finally:
        holder.close()
        holder.deleteLater()
        qapp.processEvents()

