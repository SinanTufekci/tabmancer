"""The main window's Team panel (desktop/teampanel.py): a tab a team, its Orbit over its lanes, on
made-up teams with a clock of the test's own. Skipped without the desktop extra (pip install
"tabmancer[desktop]")."""

import html
import importlib
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtWidgets import QApplication

from tabmancer import teams, teamwords
from tabmancer import viewmodel as VM
from tabmancer.desktop import look, teamgraph, teampanel

look.fonts_for_offscreen()
T0 = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
ACCENT = "#d97757"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


class Clock:
    def __init__(self) -> None:
        self.at = 100.0

    def __call__(self) -> float:
        return self.at


@pytest.fixture
def panel(qapp):
    made = []

    def make():
        clock = Clock()
        shown = teampanel.TeamPanel(ACCENT, now=clock)
        shown.resize(760, 820)
        shown.show()
        shown.clock = clock
        made.append(shown)
        return shown

    yield make
    for shown in made:
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def member(name, state="idle", label="idle", tone="done", **more):
    return VM.MemberView(name, name == "lead", "", f"id-{name}", state, label, tone, model="sonnet", **more)


def team(name, *names, needs=(), goal="Ship the checkout"):
    """A made-up team: its saved form, as the dashboard shows it, and what was said."""
    saved = teams.Team(name, f"C:/work/{name}", goal, [teams.Member(n, lead=n == names[0]) for n in names])
    members = [member(n, "needs-you", "needs you", "your-turn!", needs_you=True) if n in needs else member(n)
               for n in names]
    view = VM.TeamView(name, f"C:/work/{name}", goal, len(names), 0, members, [], [], lead=names[0])
    return saved, view


def snap(*made, said=None):
    """The dashboard's read, as far as the panel looks: the teams shown, each one's state."""
    said = said or {}
    states = {saved.name: teams.TeamState(saved, {}, list(said.get(saved.name, []))) for saved, _view in made}
    return SimpleNamespace(view=SimpleNamespace(teams=[view for _saved, view in made]), team_states=states)


def msg(k, sender, receiver, text=""):
    return teams.Message(T0 + timedelta(minutes=k), sender, receiver, text or f"message {k}")


SHOP = team("shop", "lead", "api", "ui", needs=("ui",))
DOCS = team("docs", "writer", "checker")


def test_no_team_yet_says_how_one_starts(panel):
    shown = panel()
    shown.set(snap())
    assert shown.pages.currentWidget() is shown.empty and shown.empty.text() == teampanel.NO_TEAM
    assert shown.tabs.isHidden() and shown.team is None


def test_a_tab_a_team_the_first_shown(panel):
    shown = panel()
    said = [msg(0, "lead", "api"), msg(1, "api", "ui")]
    shown.set(snap(SHOP, DOCS, said={"shop": said}))
    assert [shown.tabs.tabText(i) for i in range(shown.tabs.count())] == ["shop" + teampanel.NEEDS, "docs"]
    assert shown.tabs.tabToolTip(0) == "Ship the checkout\n3 of 3 open\nneeds you: ui"
    assert shown.team == "shop" and shown.pages.currentWidget() is shown.split
    assert shown.colours == teamwords.colours(SHOP[0], ACCENT)
    assert shown.graph.view is SHOP[1] and [m.sender for m in shown.lanes.said] == ["lead", "api"]
    assert shown.lanes.lanes == ["lead", "api", "ui"] and shown.graph.flights == []  # the first look: as it was


def test_the_team_shown_stays_and_another_starts_afresh(panel):
    shown = panel()
    said = [msg(0, "lead", "api")]
    shown.set(snap(SHOP, DOCS, said={"shop": said}))
    shown.tabs.setCurrentIndex(1)
    assert shown.team == "docs" and shown.graph.view is DOCS[1] and shown.lanes.said == []
    shown.set(snap(DOCS, SHOP, said={"shop": [*said, msg(1, "api", "lead")]}))  # read again, in another order
    assert shown.team == "docs" and shown.tabs.currentIndex() == 0 and shown.tabs.tabText(0) == "docs"
    shown.tabs.setCurrentIndex(1)
    assert shown.team == "shop" and shown.graph.flights == [] and shown.lanes.drawing is None  # nothing flies
    shown.set(snap(SHOP, said={"shop": said}))  # docs gone: shop stays
    assert shown.team == "shop" and shown.tabs.count() == 1
    shown.set(snap(DOCS))  # shop gone: the first there
    assert shown.team == "docs"


def test_a_new_message_flies_and_draws_itself(panel):
    shown = panel()
    said = [msg(0, "lead", "api")]
    shown.set(snap(SHOP, said={"shop": said}))
    new = msg(1, "api", "ui", "made-up: the endpoint is in")
    shown.set(snap(SHOP, said={"shop": [*said, new]}))
    assert [(f.sender, f.receiver, f.words) for f in shown.graph.flights] == [("api", "ui", new.text)]
    assert shown.lanes.drawing is not None and shown.lanes.drawing[0] is new


def test_a_click_lights_a_members_messages_and_says_so(panel):
    shown = panel()
    shown.set(snap(SHOP, said={"shop": [msg(0, "lead", "api"), msg(1, "api", "ui")]}))
    shown.graph.member_clicked.emit("api")
    assert shown.picked == "api" and shown.graph.picked == "api" and shown.lanes.picked == "api"
    shown.graph.member_clicked.emit("api")  # again: everyone's
    assert shown.picked is None and shown.lanes.picked is None
    shown.graph.grab()  # placed: its members have spots
    spot = shown.graph.drawn["api"]
    words = shown.graph.words_at(spot)
    assert html.escape(teampanel.HINT) in words and "right-click" not in words  # the window has no actions for it


def test_it_moves_as_the_monitor_says(panel):
    teamgraph.save_motion("off")
    shown = panel()
    shown.set(snap(SHOP))
    assert shown.graph.mode == "off" and shown.lanes.mode == "off"
