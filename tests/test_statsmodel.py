"""The stats screen's counting and words, with no UI (statsmodel): one copy for the terminal's u screen and the
desktop window's stats dialog. Empty made-up reports; the history, the files and the report writer faked."""

from datetime import date, timedelta
from pathlib import Path

from tabmancer import history, stats, summarize
from tabmancer import report as reports
from tabmancer import statsmodel as SM
from tabmancer.actions import Note
from tabmancer.palette import MUTED, TEXT
from tabmancer.tokens import Report

ACCENT = "#d97757"


def empty(days: int) -> Report:
    return Report(date.today() - timedelta(days=days - 1), days)


def test_the_head_marks_the_period_and_names_the_project():
    parts = SM.head_parts(14, "", False, ACCENT)
    assert parts[0] == ("look back  ", MUTED) and parts[3] == (" 2 ", f"bold #000000 on {ACCENT}")
    assert parts[4] == (f"{stats.period_words(14)}  ", f"bold {TEXT}") and parts[1] == (" 1 ", MUTED)
    assert parts[-1] == ("all", MUTED) and len(SM.PERIODS) == 5 and SM.PERIODS[-1] == stats.ALL_TIME
    named = SM.head_parts(0, "shop", True, ACCENT)
    assert named[-2:] == [("shop", f"bold {ACCENT}"), ("   counting…", f"italic {MUTED}")]


def test_p_steps_through_the_projects_then_all_again():
    projects = [("c--work-shop", "shop"), ("c--work-blog", "blog")]
    assert SM.stepped(projects, "", 1) == "c--work-shop" and SM.stepped(projects, "c--work-blog", 1) == ""
    assert SM.stepped(projects, "", -1) == "c--work-blog" and SM.stepped(projects, "gone", 1) == "c--work-shop"
    assert SM.stepped([], "", 1) == ""
    assert SM.project_name(projects, "c--work-blog") == "blog" and SM.project_name(projects, "") == ""


def test_a_recount_counts_the_period_and_the_one_before(monkeypatch):
    asked = []
    monkeypatch.setattr(history, "collect", lambda days=None: asked.append(days) or empty(days or 30))
    counted = SM.recount(7, "", {}, ACCENT)
    assert asked == [7, 14] and counted.period == 7 and counted.shown is counted.report
    assert counted.longer is not None and counted.before is not None and counted.projects == []
    assert set(counted.parts) == {"overview", "tokens", "work", "tools"}
    asked.clear()
    whole = SM.recount(stats.ALL_TIME, "", {}, ACCENT)
    assert asked == [None] and whole.longer is None and whole.before is None


def test_export_and_the_report_are_said_as_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(stats, "write_files", lambda report, sessions: (tmp_path / "s.json", tmp_path / "s.csv"))
    assert SM.export(empty(7), {}) == Note(f"Saved s.json and s.csv in {tmp_path}", seconds=8)

    def full(report, sessions):
        raise OSError("disk full")

    monkeypatch.setattr(stats, "write_files", full)
    assert SM.export(empty(7), {}) == Note("Export failed: disk full", "error")

    monkeypatch.setattr("tabmancer.sessions.find_sessions", lambda days, quick=False: [])
    monkeypatch.setattr(reports, "write", lambda sessions, report: {"text": "words"})
    monkeypatch.setattr(reports, "save", lambda entry: Path("r.md"))
    assert SM.write_report(empty(7), {}) == Note("Report saved as r.md", seconds=8)

    def no_claude(sessions, report):
        raise summarize.SummaryError("no claude")

    monkeypatch.setattr(reports, "write", no_claude)
    assert SM.write_report(empty(7), {}) == Note("The report could not be written: no claude", "error", 10)

    monkeypatch.setattr(reports, "latest", lambda days: None)
    assert SM.report_text(empty(7)) is None and SM.NO_REPORT.severity == "warning"
    assert SM.latest(empty(7), {}) == (None, False)
    monkeypatch.setattr(reports, "latest", lambda days: {"text": f"{days} days", "key": "k"})
    assert SM.report_text(empty(30)) == "30 days"
