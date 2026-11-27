"""The window in the system tray (desktop/tray.py, main.py; Sinan's choice, 2026-11-23): X puts it there and
tabmancer goes on (it reads, holds the actor lock and acts, its door answers); q, or Quit in the icon's menu, quits
and lets the lock go; with handovers queued here, Quit from the tray shows the window and asks first. run() outlives
its window while it is in the tray, and a logoff closes it for good. Where the desktop has no tray, X quits, as
before. QSystemTrayIcon stands in: no test puts an icon in the real tray. Offscreen over the made-up sessions of
`tabmancer --demo`, the lock in the test's own config folder.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import time
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

import shiboken6
from PySide6.QtCore import QPoint
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
from qt_later import later  # noqa: F401  (a fixture)
from test_desktop_acting import HOUR, world  # noqa: F401  (the made-up world, a fixture)
from test_desktop_front import read
from test_desktop_handover import until

from tabmancer import actor, toast
from tabmancer.config import read_json
from tabmancer.desktop import frame, main, tray
from tabmancer.desktop.closing import CloseDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def icons(monkeypatch) -> SimpleNamespace:
    """A desktop with a tray, whose icon only says what it was asked to do."""
    kept = SimpleNamespace(shown=[], hidden=[], said=[])
    monkeypatch.setattr(tray, "available", lambda: True)
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda icon: kept.shown.append(icon))
    monkeypatch.setattr(QSystemTrayIcon, "hide", lambda icon: kept.hidden.append(icon))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda icon, title, words, *rest: kept.said.append((title, words)))
    return kept


@pytest.fixture
def window(qapp, world):  # noqa: F811
    made = []

    def make(start: bool = True):
        shown = main.MainWindow(start=start, poll_ms=HOUR, in_tray=True)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
        made.append(shown)
        read(qapp, shown)
        return shown
    yield make
    for shown in made:
        shown.settle()
        if not shown.closed:
            shown.leaving = True  # closing anyway: never the tray
            shown.close()
            assert shown.closed
        shown.deleteLater()
    qapp.processEvents()


class Spontaneous(QCloseEvent):
    """The close the window system sends for X or Alt+F4. QWidget.close() and app.quit's are not spontaneous,
    and no Qt call a test can make sends one that is."""

    def spontaneous(self) -> bool:
        return True


def x(qapp, shown) -> None:
    """X on the window (its close event, as the window system sends it), and what that posted handled."""
    shown.closeEvent(Spontaneous())
    qapp.processEvents()


def test_x_puts_the_window_in_the_tray_and_tabmancer_goes_on(qapp, window, icons):
    shown = window()
    assert shown.tray is not None and icons.shown == [shown.tray.icon] and shown.tray.icon.toolTip() == tray.TIP
    assert [a.text() for a in shown.tray.menu.actions() if a.text()] == [tray.OPEN, tray.QUIT]
    assert shown.engine.state.acts and actor.holds()
    dock = next(iter(shown.docks.values()))
    dock.setFloating(True)  # a panel of its own
    shown.show_keys()  # ? : the keys, a window of the window's
    (keys,) = [w for w in qapp.topLevelWidgets() if w.isVisible() and w.windowTitle() == "Keys · tabmancer"]
    menu = QMenu(shown)
    menu.addAction("a menu")
    menu.popup(QPoint(20, 20))
    assert dock.isVisible() and keys.isVisible() and menu.isVisible()
    x(qapp, shown)
    assert shown.isHidden() and not shown.closed and shown.engine.state.acts and actor.holds()
    assert not (dock.isVisible() or keys.isVisible() or menu.isVisible())  # they went with it
    assert not shown.front.looked_at()  # the desktop notes go out
    assert shown.timer.isActive() and shown.actor_timer.isActive() and shown.acting  # it reads and acts on
    assert "main" in read_json(frame.LAYOUT_FILE)  # its layout kept as it went
    assert icons.said == [(tray.KEPT_TITLE, tray.KEPT)] and icons.hidden == []
    assert main.knock()  # `tabmancer desktop` again: the door answers, and the window comes back
    until(qapp, lambda: shown.isVisible())
    assert dock.isVisible() and dock.isFloating() and keys.isVisible() and not menu.isVisible()  # back with it
    keys.close()
    dock.close()  # its own X: that panel put away
    x(qapp, shown)
    assert shown.isHidden() and len(icons.said) == 1  # said once a run
    shown.tray.clicked(QSystemTrayIcon.ActivationReason.Context)  # the right button: the menu, not the window
    assert shown.isHidden()
    shown.tray.clicked(QSystemTrayIcon.ActivationReason.Trigger)  # a click on the icon
    assert shown.isVisible() and not dock.isVisible()  # put away before: not back
    x(qapp, shown)
    shown.tray.clicked(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert shown.isVisible()
    x(qapp, shown)
    shown.tray.open_picked()  # Open in its menu
    assert shown.isVisible() and not shown.closed


def test_q_and_the_trays_quit_quit_and_let_the_lock_go(qapp, window, icons, monkeypatch):
    shown = window()
    ended = []
    shown.quit_now.connect(lambda: ended.append(True))
    shown.front.handing = {object(): "a project"}  # h, being written
    shown.actions_by_key["q"].trigger()
    assert not shown.closed and shown.close_after  # it waits for it
    x(qapp, shown)  # X meanwhile: in front still, never the tray
    assert shown.isVisible() and not shown.closed
    shown.front.handing = {}  # written
    shown.actions_by_key["q"].trigger()
    assert shown.closed and not actor.holds() and ended == [True] and icons.hidden == [shown.tray.icon]
    assert not shown.timer.isActive() and not shown.actor_timer.isActive() and shown.acting == {}
    assert main.knock() is False  # its door shut with it
    other = window()  # the lock free again: this one takes it
    assert other.engine.state.acts and actor.holds()
    x(qapp, other)  # to the tray
    came = []
    monkeypatch.setattr(other, "come_forward", lambda: came.append(True))
    other.tray.quit_picked()  # Quit in the icon's menu: nothing to ask, so it never comes forward
    assert other.closed and not actor.holds() and came == [] and other.isHidden()


def test_quit_from_the_tray_with_handovers_queued_shows_the_window_and_asks_first(qapp, window, icons,
                                                                                   monkeypatch):
    shown = window(start=False)
    shown.engine.state.queued = {next(iter(shown.snap.sessions)): "self"}  # h on a busy one
    monkeypatch.setattr(toast, "enabled", lambda: False)  # `tabmancer notifications off`
    x(qapp, shown)  # to the tray, nothing asked (tabmancer goes on, the queue with it)
    assert shown.isHidden() and not shown.findChildren(CloseDialog)
    assert icons.said == []  # the desktop notes off: where it went goes unsaid
    shown.tray.quit_picked()
    (asked,) = [d for d in shown.findChildren(CloseDialog) if d.isVisible()]
    assert shown.isVisible() and not shown.closed  # in front, so what it asks is seen
    asked.reject()  # kept open
    qapp.processEvents()
    assert not shown.quitting and shown.engine.state.queued
    x(qapp, shown)  # again: the tray, not a quit
    assert shown.isHidden() and not shown.closed
    shown.tray.open_picked()
    shown.actions_by_key["Esc"].trigger()  # nothing to clear: Esc closes, and asks first
    (asked,) = [d for d in shown.findChildren(CloseDialog) if d.isVisible()]
    assert not shown.quitting
    shown.front.handing = {object(): "a project"}  # and h on another one, being written meanwhile
    asked.accept()  # closed anyway: the handovers queued here dropped
    qapp.processEvents()
    assert shown.engine.state.queued == {} and shown.close_after and not shown.closed  # the one written waited for
    x(qapp, shown)  # X while it waits: in front still, never the tray
    assert shown.isVisible() and not shown.closed
    shown.front.handing = {}
    shown.finish_closing()
    assert shown.closed


def test_quit_from_the_tray_while_a_handover_is_written_shows_the_window_and_waits_for_it(qapp, window, icons):
    shown = window(start=False)
    x(qapp, shown)
    shown.front.handing = {object(): next(iter(shown.snap.sessions.values())).project}  # h, on its thread
    shown.tray.quit_picked()
    assert shown.isVisible() and not shown.closed and shown.close_after  # in front: it says it waits
    x(qapp, shown)  # X while it waits to quit: in front still, never the tray
    assert shown.isVisible() and not shown.closed
    shown.front.handing = {}  # written, its fresh session opened
    shown.finish_closing()
    assert shown.closed and not shown.isVisible()


def test_only_runs_window_goes_to_the_tray(qapp, world, icons):  # noqa: F811
    """A window not run's (a test's, say) never has an icon there, tray or not."""
    shown = main.MainWindow(start=False, poll_ms=HOUR)
    assert shown.tray is None and icons.shown == []
    shown.close()
    assert shown.closed
    shown.deleteLater()


def test_esc_with_nothing_to_clear_quits_as_in_the_terminal(qapp, window, icons):
    shown = window()
    shown.actions_by_key["Esc"].trigger()
    assert shown.closed and not actor.holds() and icons.hidden == [shown.tray.icon]


def test_without_a_tray_x_quits_as_before(qapp, window, monkeypatch):
    monkeypatch.setattr(tray, "available", lambda: False)  # a desktop with no tray (as conftest has it)
    shown = window()
    assert shown.tray is None and actor.holds()
    shown.close()
    assert shown.closed and not actor.holds()


def test_run_outlives_its_window_in_the_tray_and_ends_at_quit(qapp, world, icons, later):  # noqa: F811
    seen = []

    def to_tray():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow)]
        if not windows or windows[0].snap is None:
            later(100, to_tray)
            return
        shown = windows[0]
        seen.append((qapp.quitOnLastWindowClosed(), shown.tray is not None, actor.holds()))
        x(qapp, shown)
        other = QWidget()  # a monitor, say: the last window shown, closed: no quit
        other.show()
        other.close()
        other.deleteLater()
        later(300, quit_from_the_tray)

    def quit_from_the_tray():
        (shown,) = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow)]
        seen.append((shown.isHidden(), shown.closed))
        seen.append(shown)
        shown.tray.quit_picked()

    later(100, to_tray)
    later(30_000, lambda: qapp.exit(1))  # never hangs the suite (exit: no close to call it off)
    began = time.monotonic()
    assert main.run() == 0
    assert time.monotonic() - began < 20  # ended by Quit, not by the safety net
    assert seen[:2] == [(False, True, True), (True, False)]
    assert not shiboken6.isValid(seen[2]) and not actor.holds() and len(icons.hidden) == 1
    assert qapp.quitOnLastWindowClosed()  # as it was before run


def test_told_to_quit_with_the_window_up_it_closes_for_good(qapp, world, icons, later):  # noqa: F811
    """The app told to quit (a logoff, say) while the window is up: Qt closes it first, and that close is for
    good, never the tray (an ignored close would call the quit off, and nothing would let the lock go)."""
    seen = []

    def quit_once_read():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow)]
        if not windows or windows[0].snap is None:
            later(100, quit_once_read)
            return
        seen.append((windows[0].tray is not None, windows[0].isVisible(), actor.holds()))
        qapp.quit()

    later(100, quit_once_read)
    later(30_000, lambda: qapp.exit(1))
    assert main.run() == 0
    assert seen == [(True, True, True)] and not actor.holds() and len(icons.hidden) == 1 and icons.said == []


def test_told_to_quit_while_in_the_tray_it_closes_for_good(qapp, world, icons, later):  # noqa: F811
    """A logoff, say: the app told to quit while the window is in the tray; it is closed, its lock let go, the
    handovers queued here dropped (nothing can be asked now)."""
    def to_tray_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow)]
        if not windows or windows[0].snap is None:
            later(100, to_tray_then_quit)
            return
        windows[0].engine.state.queued = {next(iter(windows[0].snap.sessions)): "self"}  # h on a busy one
        x(qapp, windows[0])
        qapp.quit()

    later(100, to_tray_then_quit)
    later(30_000, lambda: qapp.exit(1))
    assert main.run() == 0
    assert not actor.holds() and len(icons.hidden) == 1 and "main" in read_json(frame.LAYOUT_FILE)
    assert not [w for w in qapp.topLevelWidgets() if isinstance(w, CloseDialog) and w.isVisible()]  # none asked
