"""c in the window (desktop/limit.py): the stopped sessions and the ways on, in the terminal's words
(limitwords); a window that does not act offers no "when the limit lifts" and says why; Space and Tab tick and
move as the terminal's dialog does; Enter chooses the picked way for the ticked ones, once; none ticked: it says
to tick one first. Offscreen. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import html
import importlib
import os
import re
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from tabmancer import limitwords
from tabmancer.desktop import look
from tabmancer.desktop.limit import LimitDialog
from tabmancer.sessions import Exit, Session, Status

SOON = datetime.now() + timedelta(hours=2)


@pytest.fixture(scope="module")
def qapp():
    look.fonts_for_offscreen()
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def stopped(sid: str, context: int, project: str) -> Session:
    return Session(session_id=sid, path=None, cwd=f"C:/work/{project}", exit=Exit.CLEAN, status=Status.LIMIT,
                   resets=SOON, context=context, title=f"Fix {sid}")


def asked(away: bool = False, armed: bool = True, room: bool = False) -> limitwords.Ask:
    small, big = stopped("small", 20_000, "docs"), stopped("big", 612_000, "shop")
    return limitwords.Ask([small, big], "at 16:00", armed, "Your plan now: session 100% · week 70%\nb@example.com: 12%",
                          room, {"big": ["Gmail"]} if away else {}, "the Example Labs organisation")


def words(dialog) -> str:
    """What its labels say, as read: no markup."""
    shown = " | ".join(label.text() for label in dialog.findChildren(QLabel) if not label.isHidden())
    return html.unescape(re.sub("<[^>]+>", "", shown.replace("<br>", "\n"))).replace("\xa0", " ")


def chosen_of(dialog) -> list:
    got: list = []
    dialog.chosen.connect(lambda way, ids: got.append((way, ids)))
    return got


def test_a_window_that_does_not_act_offers_now_and_the_handover_and_says_why(qapp):
    parent = QWidget()
    dialog = LimitDialog(parent, asked(), look.ORANGE)
    said = words(dialog)
    assert "Stopped at a usage limit" in said and "2 sessions · it lifts at 16:00" in said
    assert "Your plan now: session 100% · week 70%\nb@example.com: 12%" in said
    assert said.index("Fix big") < said.index("Fix small")  # the biggest context first
    assert "[x] shop" in said and "612k context" in said
    assert "Continue the ticked now" in said and "Hand the ticked over" in said
    assert "when the limit lifts" not in said.split(limitwords.NOT_HERE)[0].lower()
    assert "Do not continue them by themselves" not in said and limitwords.NOT_HERE in said
    assert "632k tokens" in said  # what going on on another account costs, for the ticked
    assert "prompt goes on the clipboard" in said and "gets the prompt in its tab" not in said  # it types into none
    assert [way for way, _ in dialog.ways] == ["now", "handover"]
    parent.deleteLater()


def test_given_acts_every_way_is_offered_as_in_the_terminal(qapp):
    parent = QWidget()
    dialog = LimitDialog(parent, asked(), look.ORANGE, acts=True, types=True)
    assert [way for way, _ in dialog.ways] == ["lift", "now", "handover", "off"]
    assert "gets the prompt in its tab" in words(dialog)  # given it types into tabs, as the terminal says it
    assert limitwords.NOT_HERE not in words(dialog)
    parent.deleteLater()


def test_space_and_tab_tick_and_enter_chooses_the_picked_way_once(qapp):
    parent = QWidget()
    dialog = LimitDialog(parent, asked(), look.ORANGE)
    got = chosen_of(dialog)
    QTest.keyClick(dialog, Qt.Key.Key_Tab)  # to the sessions
    QTest.keyClick(dialog, Qt.Key.Key_Space)  # big unticked: small alone costs little
    assert dialog.ticked == ["small"] and "632k" not in words(dialog) and "20k tokens" in words(dialog)
    QTest.keyClick(dialog, Qt.Key.Key_Tab)  # back to the ways
    QTest.keyClick(dialog, Qt.Key.Key_Down)  # the handover
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert got == [("handover", ["small"])]
    parent.deleteLater()


def test_with_none_ticked_it_says_so_and_stays(qapp):
    parent = QWidget()
    dialog = LimitDialog(parent, asked(), look.ORANGE)
    got = chosen_of(dialog)
    dialog.click_session(0)
    dialog.click_session(1)
    assert dialog.ticked == []
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert got == [] and limitwords.TICK_FIRST.words in words(dialog)
    dialog.click_session(1)  # ticked again: the note goes
    assert limitwords.TICK_FIRST.words not in words(dialog)
    dialog.choose_way(0)  # a double click on a way
    assert got == [("now", ["small"])]
    parent.deleteLater()


def test_a_conversation_from_another_organisation_picks_the_handover_first_and_says_where(qapp):
    parent = QWidget()
    dialog = LimitDialog(parent, asked(away=True, room=True), look.ORANGE)
    assert dialog.ways[dialog.way][0] == "handover"
    said = words(dialog)
    assert "One of the ticked ran on another organisation" in said and "the Example Labs organisation" in said
    assert "Gmail content" in said and "only a digest goes" in said
    got = chosen_of(dialog)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert got == [] and not dialog.isVisible()
    parent.deleteLater()
