"""A, in the window (desktop/signin.py): the Claude accounts you use, read on a thread and listed as the terminal
dashboard's A dialog lists them, in accountwords.py's words; ←/→ picks where an account's sign-in page opens
(kept at once), and Enter opens Claude Code's own sign-in (`claude auth login --email`) in a new tab, on a
thread, its page where picked. Offscreen over the made-up world of `tabmancer --demo` (signed in as
you@example.com, the work account seen this morning, made-up browser profiles). Skipped without the desktop
extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from test_desktop_front import read
from test_desktop_handover import until

from tabmancer import accounts, accountwords, browsers, claudeupdate, config, demo, launch, shutdowns
from tabmancer import viewmodel as VM
from tabmancer.config import config_dir
from tabmancer.desktop import frame, look, signin
from tabmancer.desktop.clisettings import parts_html
from tabmancer.desktop.formparts import keys_html
from tabmancer.desktop.signin import Accounts, AccountsDialog

WORK = demo._account(demo.WORK)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> list:
    """The demo's made-up world; the tabs it would open are kept here (the list), none opens."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    opened: list = []
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: opened.extend(launches) or [])
    return opened


@pytest.fixture
def window(qapp, world):
    from tabmancer.desktop import main

    made = []

    def make():
        shown = main.MainWindow(start=False)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
        made.append(shown)
        read(qapp, shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def shown_dialogs(shown) -> list[AccountsDialog]:
    return [d for d in shown.findChildren(AccountsDialog) if d.isVisible()]


def press_a(qapp, shown) -> AccountsDialog:
    shown.actions_by_key["A"].trigger()
    until(qapp, lambda: shown_dialogs(shown))  # read on a thread first
    (asked,) = shown_dialogs(shown)
    return asked


def press(qapp, asked: AccountsDialog, *keys: Qt.Key) -> None:
    for key in keys:
        QTest.keyClick(asked, key)
        qapp.processEvents()


def lit(asked: AccountsDialog) -> list[int]:
    return [at for at, row in enumerate(asked.rows) if asked.accent in row.styleSheet()]


def signed_in(qapp, shown, world: list, count: int = 1) -> None:
    until(qapp, lambda: len(world) >= count)
    shown.settle()
    qapp.processEvents()


def test_a_lists_each_account_as_the_terminals_dialog_words_it(qapp, window, world):
    shown = window()
    asked = press_a(qapp, shown)
    places = browsers.places()  # the demo's made-up profiles
    reading = signin.read_seen()
    you = reading.current
    assert you is not None and you.email == "you@example.com" and you.plan == "Max 5x"
    assert asked.ids == [you.key, WORK.key, accountwords.ANOTHER] and asked.isModal()
    assert accounts.opens_in() == {}  # nothing picked yet: the profile syncing with its email, a guess
    assert [row.text() for row in asked.rows] == [
        parts_html(accountwords.option_parts(you, "", current=True, shared=False, last=None)),
        parts_html(accountwords.option_parts(WORK, reading.seen[1][1], current=False, shared=False,
                                             last=reading.last[WORK.key], place="Chrome · Work")),
        parts_html(accountwords.another_parts("default browser"))]
    assert "session reset since" in asked.rows[1].text()  # its last limits, as Bit says them
    assert lit(asked) == [1]  # the first one to switch to: not the one signed in now
    assert asked.heading.text() == (look.span("Claude accounts", f"bold {look.TEXT}") + "&nbsp;&nbsp;"
                                    + look.span("signed in as you@example.com", look.MUTED))
    stopped = sum(1 for s in shown.snap.sessions.values() if VM.at_limit(s))  # the demo's two: c continues them here
    assert asked.note.text() == look.span(accountwords.note(True, stopped), look.MUTED)
    assert asked.keys.text() == keys_html(accountwords.key_pairs(True)) and len(places) == 4
    press(qapp, asked, Qt.Key.Key_Escape)
    shown.account.mousePressEvent(QMouseEvent(  # the top bar's accounts, clicked: A too
        QMouseEvent.Type.MouseButtonPress, QPointF(4, 4), QPointF(4, 4), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    until(qapp, lambda: shown_dialogs(shown))
    assert world == []


def test_the_arrows_pick_past_the_account_signed_in_now_and_where_its_page_opens_is_kept_at_once(qapp, window, world):
    shown = window()
    asked = press_a(qapp, shown)
    press(qapp, asked, Qt.Key.Key_Down)
    assert lit(asked) == [2]
    press(qapp, asked, Qt.Key.Key_Down)  # round, past the one signed in now
    assert lit(asked) == [1]
    press(qapp, asked, Qt.Key.Key_Up, Qt.Key.Key_Up)
    assert lit(asked) == [1]
    asked.rows[0].clicked.emit(0)  # a click on the one signed in now picks nothing
    assert lit(asked) == [1]
    press(qapp, asked, Qt.Key.Key_Right)  # Chrome · Work, then the private window
    assert asked.rows[1].text().endswith(look.styled("Chrome · private window", look.CYAN))
    assert accounts.opens_in() == {"you@work.example.com": "chrome:private"}  # kept at once, Esc or not
    press(qapp, asked, Qt.Key.Key_Left, Qt.Key.Key_Left)
    assert accounts.opens_in() == {"you@work.example.com": "chrome:Default"}
    press(qapp, asked, Qt.Key.Key_Escape)
    shown.settle()
    assert shown_dialogs(shown) == [] and world == []  # nothing signed in
    asked = press_a(qapp, shown)  # the next time: as picked
    assert asked.rows[1].text().endswith(look.styled("Chrome · Personal", look.CYAN))
    press(qapp, asked, Qt.Key.Key_Escape)


def test_a_place_not_kept_is_said_in_the_dialog_and_accounts_json_left_as_it_is(qapp, window, world):
    shown = window()
    path = config_dir() / accounts.KNOWN
    path.write_text("{cut short", encoding="utf-8")
    asked = press_a(qapp, shown)
    assert asked.ids == [asked.ids[0], accountwords.ANOTHER] and not asked.not_kept.isVisibleTo(asked)
    press(qapp, asked, Qt.Key.Key_Right)  # another account's page: Chrome · Personal, this time
    assert asked.not_kept.isVisibleTo(asked) and asked.not_kept.text() == look.span(accountwords.NOT_KEPT, look.AMBER)
    assert asked.rows[1].text().endswith(look.styled("Chrome · Personal", look.CYAN))
    assert path.read_text(encoding="utf-8") == "{cut short"
    path.unlink()
    press(qapp, asked, Qt.Key.Key_Right)  # kept now: said no more
    assert not asked.not_kept.isVisibleTo(asked) and accounts.opens_in() == {accounts.ANY_OTHER: "chrome:Profile 1"}
    press(qapp, asked, Qt.Key.Key_Escape)


def test_a_place_turned_while_another_tabmancer_changes_accounts_json_waits_a_moment_and_says_not_kept(
        qapp, window, world, monkeypatch):
    """Its read-then-write is one process's at a time (config.locked): held by another, ←/→ waits ~75 ms
    (UI_TRIES), not a second, and says it was not kept, the file left as that process writes it."""
    shown = window()
    asked = press_a(qapp, shown)
    path = config_dir() / accounts.KNOWN
    before = path.read_text(encoding="utf-8")
    waits: list[float] = []
    monkeypatch.setattr(config, "time", SimpleNamespace(time=time.time, sleep=waits.append))  # config's clock only
    with config.locked(accounts.KNOWN):  # another window, or the terminal, changing it
        press(qapp, asked, Qt.Key.Key_Right)
    assert asked.not_kept.isVisibleTo(asked) and waits == [config.BUSY_WAIT] * (config.UI_TRIES - 1)
    # The work account's page, from its own profile (Chrome · Work) to the private window, for this sign-in.
    assert asked.ids[1] == WORK.key and asked.rows[1].text().endswith(look.styled("Chrome · private window", look.CYAN))
    assert path.read_text(encoding="utf-8") == before
    press(qapp, asked, Qt.Key.Key_Right)  # let go of: kept, said no more
    assert not asked.not_kept.isVisibleTo(asked) and accounts.opens_in() == {WORK.email: browsers.DEFAULT}
    press(qapp, asked, Qt.Key.Key_Escape)


def test_enter_opens_claude_codes_own_sign_in_where_its_page_was_picked_once(qapp, window, world):
    shown = window()
    asked = press_a(qapp, shown)
    QTest.keyClick(asked, Qt.Key.Key_Return)
    QTest.keyClick(asked, Qt.Key.Key_Return)  # once is enough
    signed_in(qapp, shown, world)
    (login,) = world
    assert login.argv == ["claude", "auth", "login", "--email", "you@work.example.com"]  # its own sign-in
    env = dict(login.env)
    assert env[browsers.PLACE] == "chrome:Profile 1"  # the profile syncing with its email: a guess
    assert env["BROWSER"].endswith(("open-in.cmd", "open-in.sh"))
    assert shown.bit_line.text() == ("Signing in as you@work.example.com in a new tab: finish it in Chrome · Work. "
                                     "The top bar says when it is done.")
    asked = press_a(qapp, shown)
    asked.rows[2].chosen.emit(2)  # a double click on another account: opens as links do
    signed_in(qapp, shown, world, 2)
    assert world[1].argv == ["claude", "auth", "login"] and world[1].env == ()
    assert shown.bit_line.text().startswith("Signing in in a new tab: finish it in your browser.")


def test_the_window_settles_a_sign_in_under_way_as_it_does_its_other_threads(qapp, window, world, monkeypatch):
    shown = window()
    real = accountwords.signing_in_note

    def slow(*args):
        time.sleep(0.5)  # the tab opened, its words not said yet
        return real(*args)

    monkeypatch.setattr(accountwords, "signing_in_note", slow)
    press(qapp, press_a(qapp, shown), Qt.Key.Key_Return)
    until(qapp, lambda: world)
    shown.settle()  # as on closing: the sign-in's thread is waited for
    qapp.processEvents()
    assert shown.bit_line.text().startswith("Signing in as you@work.example.com")

def test_a_tab_that_cannot_open_is_said_and_with_no_terminal_the_command_is_given(qapp, window, monkeypatch):
    shown = window()

    def refused(launches):
        raise OSError("no such terminal")

    monkeypatch.setattr(launch, "open_tabs", refused)
    press(qapp, press_a(qapp, shown), Qt.Key.Key_Return)
    until(qapp, lambda: "Could not open" in shown.bit_line.text())
    assert shown.bit_line.text() == "Could not open the tab: no such terminal"
    assert look.RED in shown.bit_line.styleSheet()
    monkeypatch.setattr(launch, "open_tabs", lambda launches: ["claude auth login --email you@work.example.com"])
    press(qapp, press_a(qapp, shown), Qt.Key.Key_Return)
    until(qapp, lambda: "No terminal" in shown.bit_line.text())
    assert shown.bit_line.text() == ("No terminal to open it in. Run it yourself: "
                                     "claude auth login --email you@work.example.com")


def test_accounts_that_cannot_be_read_are_said_in_red_and_a_pressed_again_reads_them_again(qapp, window, monkeypatch):
    """A file A reads, broken (a raise on its thread): said, no dialog, and A not left waiting for it for the
    window's life."""
    shown = window()
    read_seen = signin.read_seen

    def broken() -> signin.Seen:
        raise ValueError("made-up broken file")

    monkeypatch.setattr(signin, "read_seen", broken)
    raised: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: raised.append(error))  # a slot's raise, kept
    shown.actions_by_key["A"].trigger()
    until(qapp, lambda: "Could not read" in shown.bit_line.text())
    shown.signin.settle()
    qapp.processEvents()
    assert shown.bit_line.text() == "Could not read the Claude accounts: made-up broken file"
    assert look.RED in shown.bit_line.styleSheet() and not shown_dialogs(shown) and not shown.reading_accounts
    assert not raised
    monkeypatch.setattr(signin, "read_seen", read_seen)  # mended
    press(qapp, press_a(qapp, shown), Qt.Key.Key_Escape)


def test_the_sessions_stopped_at_a_limit_are_counted_as_c_continues_them_here(qapp, window, monkeypatch):
    from tabmancer.desktop import main

    shown = window()
    stopped = sum(1 for s in shown.snap.sessions.values() if VM.at_limit(s))
    assert stopped and "c" in main.HERE
    asked = press_a(qapp, shown)
    assert asked.note.text() == look.span(accountwords.note(True, stopped), look.MUTED)
    press(qapp, asked, Qt.Key.Key_Escape)
    monkeypatch.setattr(main, "HERE", main.HERE - {"c"})  # were c the terminal's only: not counted here
    asked = press_a(qapp, shown)
    assert "stopped at a usage limit" not in asked.note.text()
    press(qapp, asked, Qt.Key.Key_Escape)


def test_a_twice_before_the_accounts_are_read_opens_one_dialog(qapp, window, monkeypatch):
    shown = window()
    gate = threading.Event()
    real = signin.read_seen

    def slow():
        gate.wait(10)
        return real()

    monkeypatch.setattr(signin, "read_seen", slow)
    shown.actions_by_key["A"].trigger()
    shown.actions_by_key["A"].trigger()
    gate.set()
    until(qapp, lambda: shown_dialogs(shown))
    shown.settle()
    qapp.processEvents()
    assert len(shown_dialogs(shown)) == 1 and not shown.reading_accounts


def test_the_sign_in_waits_for_todays_claude_update_and_the_demo_says_only_what_would_have_opened(qapp, monkeypatch):
    ran, said, opened = [], [], []
    monkeypatch.setattr(claudeupdate, "before_opening", lambda say=None: ran.append(say))
    monkeypatch.setattr(launch, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(browsers, "launcher", lambda: Path("open-in.cmd"))
    for is_demo in (False, True):
        asked = Accounts(qapp, demo=is_demo)
        asked.said.connect(lambda words, seconds, severity: said.append(words))
        asked.sign_in_now("b@example.com", browsers.DEFAULT, [])
        asked.deleteLater()
    assert len(ran) == 1 and len(opened) == 2  # the demo's accounts are made up: no update for them
    assert said == [("Signing in as b@example.com in a new tab: finish it in your browser. The top bar says when "
                     "it is done.")]  # the demo's stand-in says what would have opened, not this
    qapp.processEvents()


def test_an_email_in_two_organisations_is_listed_once_for_each_and_opens_in_one_place(qapp):
    from tabmancer.accounts import Account

    holder = QWidget()
    labs = Account("claude", "s@example.com", "Team", "me", "org-labs", "Example Labs", "claude_team")
    mine = Account("claude", "s@example.com", "Pro", "me", "org-mine", "s@example.com's Organization", "claude_pro")
    places = [browsers.Place(browsers.DEFAULT, "default browser"),
              browsers.Place("chrome:Default", "Chrome · Personal", ("chrome",)),
              browsers.Place("chrome:private", "Chrome · private window", ("chrome", "--incognito"))]
    seen = [(labs, "2026-11-22T10:00:00"), (mine, "2026-11-21T09:00:00")]
    asked = AccountsDialog(holder, signin.Seen(None, seen, places, {}, {}), 0, look.GREEN)  # signed in to none
    chosen: list = []
    asked.chosen.connect(lambda *picked: chosen.append(picked))
    assert asked.heading.text().endswith(look.span("not signed in", look.MUTED)) and lit(asked) == [0]
    assert "Example Labs" in asked.rows[0].text() and "personal" in asked.rows[1].text()
    QTest.keyClick(asked, Qt.Key.Key_Right)
    for row in asked.rows[:2]:  # the same sign-in page: its other organisation opens there too
        assert row.text().endswith(look.styled("Chrome · Personal", look.CYAN))
    assert accounts.opens_in() == {"s@example.com": "chrome:Default"}
    asked.rows[1].chosen.emit(1)
    asked.rows[0].chosen.emit(0)  # once is enough
    assert chosen == [("s@example.com", "chrome:Default", places)] and asked.result() == QDialog.DialogCode.Accepted
    holder.deleteLater()
    qapp.processEvents()


def test_with_no_browser_but_the_systems_own_way_there_is_no_place_to_pick(qapp):
    from tabmancer.accounts import Account

    holder = QWidget()
    now = Account("claude", "a@example.com", "Pro", "a", "org-a", "", "claude_pro")
    other = Account("claude", "b@example.com", "Team", "b", "org-b", "Example Labs", "claude_team")
    only = [browsers.Place(browsers.DEFAULT, "default browser")]
    asked = AccountsDialog(holder, signin.Seen(now, [(now, ""), (other, "2026-11-22T10:00:00")], only, {}, {}), 0,
                           look.GREEN)
    chosen: list = []
    asked.chosen.connect(lambda *picked: chosen.append(picked))
    assert asked.keys.text() == keys_html(accountwords.key_pairs(False))
    assert asked.note.text() == look.span(accountwords.note(False, 0), look.MUTED)
    assert "opens in" not in asked.rows[1].text()
    QTest.keyClick(asked, Qt.Key.Key_Right)
    assert accounts.opens_in() == {}  # nothing to pick, nothing kept
    asked.rows[0].chosen.emit(0)  # a double click on the one signed in now: nothing to sign in to
    assert chosen == [] and asked.result() == QDialog.DialogCode.Rejected  # not answered
    QTest.keyClick(asked, Qt.Key.Key_Return)
    assert chosen == [("b@example.com", browsers.DEFAULT, [])]
    holder.deleteLater()
    qapp.processEvents()
