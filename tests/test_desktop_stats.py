"""u in the window (desktop/stats.py): the stats dialog shows what it was given, counts the period again off the
window's thread and shows that; 1-5, p, the arrows, x, r, c and u as the terminal's stats screen has them, its
words statsmodel.py's. Offscreen, over empty made-up reports; the history, the limits, the files and the report
writer faked, every config folder in tmp. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from rich.style import Style
from rich.text import Text

from tabmancer import history, stats, statsmodel, usage
from tabmancer import report as reports
from tabmancer.desktop import stats as S
from tabmancer.statstext import TABS, BarChart, Trend, WeekChart
from tabmancer.tokens import Report

ACCENT = "#d97757"


def empty(days: int) -> Report:
    return Report(date.today() - timedelta(days=days - 1), days)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def faked(tmp_path, monkeypatch):
    """Every config folder in tmp; the kept days, the limits and the latest report faked. asked: the days
    history.collect was asked for."""
    for name in ("TABMANCER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    asked: list = []
    monkeypatch.setattr(history, "collect", lambda days=None: asked.append(days) or empty(days or 60))
    monkeypatch.setattr(usage, "chart_history", lambda days=7, now=None: [])
    monkeypatch.setattr(reports, "latest", lambda days: None)
    return asked


def settle(qapp, dialog) -> None:
    for _ in range(4):
        dialog.worker.settle(20)
        qapp.processEvents()


def opened(qapp, days: int = 14) -> S.StatsDialog:
    dialog = S.StatsDialog(None, empty(days), {}, ACCENT)
    dialog.show()
    settle(qapp, dialog)
    return dialog


def shown_words(widget) -> str:
    return " ".join(child.text() for child in widget.findChildren(QLabel))


def test_rich_words_become_html_in_their_colours():
    made = S.html_of(Text.assemble(("big", "bold #ff0000"), " <&> ", ("dim", "dim #e6e6e6")), 20)
    assert '<span style="color:#ff0000;font-weight:600;">big</span>' in made
    assert "&nbsp;&lt;&amp;&gt;&nbsp;" in made and "dim" in made and "<br>" not in made
    assert S.css(None) == "" and "background:#e6e6e6" in S.css(Style.parse("reverse #e6e6e6"))


def test_it_shows_what_it_was_given_then_counts_the_period_again(qapp, faked):
    dialog = opened(qapp)
    assert faked == [14, 28] and not dialog.counting and dialog.period == 14
    assert "14 days" in dialog.head.text() and "counting" not in dialog.head.text()
    assert dialog.tabs.count() == len(TABS) and dialog.scrolls["overview"].widget().findChildren(QLabel)
    assert "Press&nbsp;r" in shown_words(dialog.scrolls["report"])  # no report yet: how to get one
    dialog.recounted(dialog.turn - 1, None)  # an older count, back late: dropped, never read
    assert dialog.period == 14
    dialog.close()


def test_the_keys_pick_a_period_a_tab_and_a_project(qapp, faked):
    dialog = opened(qapp)
    faked.clear()
    QTest.keyClick(dialog, "1")
    settle(qapp, dialog)
    assert faked == [7, 14] and dialog.period == 7 and "7 days" in dialog.head.text()
    faked.clear()
    QTest.keyClick(dialog, "5")
    settle(qapp, dialog)
    assert faked == [None] and dialog.period == stats.ALL_TIME and dialog.longer is None
    QTest.keyClick(dialog, Qt.Key.Key_Left)
    assert dialog.tabs.currentIndex() == len(TABS) - 1
    QTest.keyClick(dialog, Qt.Key.Key_Right)
    QTest.keyClick(dialog, Qt.Key.Key_Right)
    assert dialog.tabs.currentIndex() == 1
    QTest.keyClick(dialog, Qt.Key.Key_End)
    QTest.keyClick(dialog, Qt.Key.Key_Home)
    dialog.projects = [("c--work-shop", "shop")]
    QTest.keyClick(dialog, "p")
    settle(qapp, dialog)
    assert dialog.project == "c--work-shop" and "shop" in dialog.head.text() and not dialog.counting
    QTest.keyClick(dialog, "p")
    settle(qapp, dialog)
    assert dialog.project == ""
    QTest.keyClick(dialog, "u")
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_export_the_report_and_copying_it(qapp, faked, monkeypatch, tmp_path):
    dialog = opened(qapp)
    monkeypatch.setattr(stats, "write_files", lambda report, sessions: (tmp_path / "s.json", tmp_path / "s.csv"))
    QTest.keyClick(dialog, "x")
    settle(qapp, dialog)
    assert "Saved s.json and s.csv" in dialog.note.text()

    QTest.keyClick(dialog, "c")
    assert statsmodel.NO_REPORT.words.split(":")[0] in dialog.note.text()
    monkeypatch.setattr(reports, "latest", lambda days: {"text": f"{days} days of words", "key": "k", "at": "2026-11-27T09:00:00"})
    QTest.keyClick(dialog, "c")
    assert QApplication.clipboard().text() == "14 days of words" and "Report copied" in dialog.note.text()

    monkeypatch.setattr("tabmancer.sessions.find_sessions", lambda days, quick=False: [])
    monkeypatch.setattr(reports, "write", lambda sessions, report: {"text": "words"})
    monkeypatch.setattr(reports, "save", lambda entry: Path("r.md"))
    QTest.keyClick(dialog, "r")
    assert dialog.tabs.currentIndex() == TABS.index("report")
    settle(qapp, dialog)
    assert not dialog.writing and "Report saved as r.md" in dialog.note.text()
    dialog.close()


def test_the_charts_are_painted(qapp):
    bars = S.BarsChart(BarChart("BY DAY", ["a", "b"], [3, 0], [("3",), ("0",)], justify=("right", "right")), ACCENT)
    week = S.WeekGrid(WeekChart([[1] * 24] + [[0] * 24] * 6, "HOURS"), ACCENT)
    trend = S.TrendChart(Trend([0, 2, 5], date(2026, 11, 1), date(2026, 11, 3), ACCENT, 2))
    for widget in (bars, week, trend):
        widget.resize(400, 160)
        assert not widget.grab().isNull()
    assert "BY DAY" in shown_words(bars)
