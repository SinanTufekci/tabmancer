"""The team's conversation as lanes, under the Orbit (desktop/conversation.py), offscreen on a made-up
team with a clock of the test's own. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from tabmancer import teams, teamwords
from tabmancer import viewmodel as VM
from tabmancer.desktop import conversation, look
from tabmancer.palette import mix

look.fonts_for_offscreen()
TODAY = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
COLOURS = {"lead": "#d97757", "api": "#56b6c2", "ui": "#98c379", "tests": "#e5c07b", teams.YOU: look.TEXT}
TEAM = teams.Team("shop", "C:/work/shop", "g", [teams.Member("lead", lead=True), teams.Member("api"),
                                                teams.Member("ui"), teams.Member("tests")])


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


class Clock:
    def __init__(self) -> None:
        self.at = 100.0

    def __call__(self) -> float:
        return self.at


@pytest.fixture
def lanes(qapp):
    made = []

    def make(mode="full"):
        clock = Clock()
        shown = conversation.Conversation(now=clock)
        shown.set_motion(mode)
        shown.resize(900, 260)
        shown.show()
        shown.clock = clock
        made.append(shown)
        return shown

    yield make
    for shown in made:
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def msg(k, sender, receiver, text="", at=None, delivered=True):
    return teams.Message(at or TODAY + timedelta(minutes=k), sender, receiver,
                         text or f"message {k} from {sender} to {receiver}", delivered=delivered)


def member(name, state="idle", label="idle", tone="done", **more):
    return VM.MemberView(name, name == "lead", "", f"id-{name}", state, label, tone, model="sonnet", **more)


def view(*members):
    return VM.TeamView("shop", "C:/work/shop", "", len(members), 0, list(members), [], [], lead="lead")


def near(image, x, y, colour, within=60):
    """Whether a pixel, or one just over or under it, is about this colour (a line is antialiased)."""
    want = QColor(colour)
    for dy in (-1, 0, 1):
        got = image.pixelColor(x, y + dy)
        if abs(got.red() - want.red()) + abs(got.green() - want.green()) + abs(got.blue() - want.blue()) < within:
            return True
    return False


def test_a_lane_a_member_a_row_a_message(lanes):
    shown = lanes()
    said = [msg(0, "lead", "api"), msg(1, teams.TABMANCER, "ui"), msg(2, "stranger", "lead", "lead: can you look?")]
    shown.set(TEAM, said, None, COLOURS)
    assert shown.lanes == [teams.YOU, "lead", "api", "ui", "tests", VM.OTHER] == VM.lanes_of(TEAM, said)
    assert [r.words for r in shown.rows] == [teamwords.gist(m, m.receiver) for m in said]
    assert shown.rows[2].words == "can you look?"  # the receiver's name left out where its lane shows it
    xs = shown.xs()
    assert xs[teams.YOU] < xs["lead"] < xs["api"] < xs["ui"] < xs["tests"] < xs[VM.OTHER]
    assert shown.lane_x(teams.TABMANCER, xs) == xs[teams.YOU]  # as the terminal's: tabmancer on your lane
    assert shown.lane_x("stranger", xs) == xs[VM.OTHER]


def test_a_days_line_before_another_days_messages(lanes):
    shown = lanes()
    before = TODAY - timedelta(days=1)
    said = [msg(0, "lead", "api", at=before), msg(1, "api", "lead", at=before + timedelta(minutes=1)),
            msg(2, "lead", "ui")]
    shown.set(TEAM, said, None, COLOURS)
    assert [r.message is None for r in shown.rows] == [True, False, False, True, False]
    assert shown.rows[0].words == f"── {before:%a %d %b} ──"
    assert shown.rows[3].words == f"── {TODAY:%a %d %b} ──"
    shown.set(TEAM, said[2:], None, COLOURS)
    assert [r.message is None for r in shown.rows] == [False]  # today's alone: no line


def test_a_new_message_draws_itself_in_full_motion_only(lanes):
    full, calm = lanes(), lanes(mode="calm")
    said = [msg(0, "lead", "api")]
    more = [*said, msg(1, "api", "ui", "endpoint is in")]
    for shown in (full, calm):
        shown.set(TEAM, said, None, COLOURS)
        assert shown.drawing is None and not shown.frames.isActive()  # the first look: as it was
        shown.set(TEAM, more, None, COLOURS)
    assert calm.drawing is None and not calm.frames.isActive()
    assert full.drawing is not None and full.drawing[0] is more[-1] and full.frames.isActive()
    assert full.share(more[-1], full.clock.at) == 0.0 and full.share(more[0], full.clock.at) == 1.0
    full.clock.at += conversation.DRAW_SECONDS / 2
    assert full.share(more[-1], full.clock.at) == pytest.approx(0.5)
    full.clock.at += conversation.DRAW_SECONDS
    full.on_frame()
    assert full.drawing is None and not full.frames.isActive()  # drawn: nothing runs
    full.set(TEAM, more, None, COLOURS, picked="api")  # the same messages again, a member picked
    assert full.drawing is None and not full.frames.isActive()


def test_it_keeps_to_the_newest_unless_scrolled_up(lanes):
    shown = lanes()
    many = [msg(k, "lead", "api") for k in range(30)]
    shown.set(TEAM, many, None, COLOURS)
    bar = shown.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    shown.set(TEAM, [*many, msg(30, "api", "lead")], None, COLOURS)
    assert bar.value() == bar.maximum()  # the new one in sight
    bar.setValue(0)
    shown.set(TEAM, [*many, msg(30, "api", "lead"), msg(31, "api", "ui")], None, COLOURS)
    assert bar.value() == 0  # you were reading further up: left there


def test_a_picked_member_stays_lit_and_one_on_its_way_shows_it(lanes):
    shown = lanes()
    said = [msg(0, "lead", "api"), msg(1, "ui", "tests"), msg(2, "api", "ui", delivered=False)]
    shown.set(TEAM, said, None, COLOURS, picked="api")
    assert shown.colour_of(said[0]) == COLOURS["lead"]
    assert shown.colour_of(said[1]) == look.MUTED  # not the picked one's
    assert shown.colour_of(said[2]) == mix(look.MUTED, COLOURS["api"], 0.5)
    row = shown.rows[0]
    words = shown.words_at(row.top - shown.verticalScrollBar().value() + 10)
    assert "lead" in words and "api" in words and said[0].text in words
    assert "on its way" in shown.words_at(shown.rows[2].top - shown.verticalScrollBar().value() + 10)
    assert shown.words_at(5) == ""  # the lanes' names


def test_the_arrow_grows_from_the_sender_to_the_receiver(lanes):
    shown = lanes()
    said = [msg(0, "ui", "api")]
    shown.set(TEAM, said, None, COLOURS)
    more = [*said, msg(1, "lead", "tests", "run the payment suite")]
    shown.set(TEAM, more, None, COLOURS)
    xs, shift = shown.xs(), shown.verticalScrollBar().value()
    line = int(shown.rows[-1].top - shift + conversation.ARROW_Y)
    start, end = int(xs["lead"]) + 24, int(xs["tests"]) - 24
    shown.clock.at += conversation.DRAW_SECONDS / 2
    half = shown.viewport().grab().toImage()
    assert near(half, start, line, COLOURS["lead"])
    assert near(half, end, line, look.SUNKEN, within=12)  # not there yet
    shown.clock.at += conversation.DRAW_SECONDS
    whole = shown.viewport().grab().toImage()
    assert near(whole, end, line, COLOURS["lead"])


def test_how_each_stands_since_its_last_message(lanes):
    shown = lanes()
    said = [msg(0, "lead", "tests"), msg(1, "tests", "lead")]
    team = view(member("lead"), member("api", "working", "working", "working"), member("ui"),
                member("tests", "needs-you", "needs you", "your-turn!", needs_you=True))
    shown.set(TEAM, said, team, COLOURS)
    image = shown.viewport().grab().toImage()
    xs, bottom = shown.xs(), shown.viewport().height() - 4
    needs, works = (image.pixelColor(int(xs[name]), bottom) for name in ("tests", "api"))
    assert needs.red() > needs.blue() + 80  # waiting on you since it wrote: red
    assert works.blue() > works.red() + 40  # at work, nothing said yet: blue from the top
    assert near(image, int(xs["lead"]), bottom, conversation.LANE_LINE, within=40)  # resting: a plain lane
    top = image.pixelColor(int(xs["api"]), conversation.HEAD_H + 6)
    assert top.blue() > top.red() + 40


def test_another_team_starts_afresh(lanes):
    shown = lanes()
    said = [msg(0, "lead", "api")]
    shown.set(TEAM, said, None, COLOURS)
    shown.clear()
    assert (shown.team, shown.said, shown.rows, shown.lanes, shown.keys) == (None, [], [], [], None)
    shown.set(TEAM, [*said, msg(1, "api", "lead")], None, COLOURS)
    assert shown.drawing is None  # its messages so far are not new
