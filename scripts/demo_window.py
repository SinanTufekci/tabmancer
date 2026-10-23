"""The dashboard over the README's made-up sessions, in a real terminal window, going through the
scenes the README shows by itself. Run by scripts/window_shots.py, which takes the pictures: for
each scene this writes <signals>/<n>.ready and waits for <signals>/<n>.done.

usage: python scripts/demo_window.py <home> <signals>
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME, SIGNALS = Path(sys.argv[1]), Path(sys.argv[2])
os.environ["CLAUDE_CONFIG_DIR"] = str(HOME / "claude")
os.environ["TABMANCER_CONFIG_DIR"] = str(HOME / "config")
os.environ.pop("NO_COLOR", None)  # a window started from a test shell may have it: every colour grey
sys.path.insert(0, str(HERE))

import demo_data  # the made-up sessions

from tabmancer import app as app_module
from tabmancer import focus, report, sessions, summarize, teams, toast, usage
from tabmancer.app import TEAM_ROW, SessionPicker
from tabmancer.teamview import TeamScreen

LONGEST = 240  # seconds: whatever happens, the window closes by then


def nothing(*args, **kwargs):
    return []


class Demo(SessionPicker):
    """The scenes, one after another, each held until its picture is taken."""

    def on_mount(self) -> None:
        super().on_mount()
        self.started = time.monotonic()
        self.scene = 0
        self.steps = [self.first, self.card, self.card_off_team_row, self.unfold, self.team_view, self.back_live,
                      self.live_off_stats, self.stats_work, self.stats_limits, self.stats_report, self.stats_off_keys]
        self.set_timer(4.0, self.next_scene)  # the cards count up, the list fades in
        self.set_interval(0.2, self.check)

    def next_scene(self) -> None:
        if self.scene >= len(self.steps):
            self.exit()
            return
        self.steps[self.scene]()
        self.set_timer(1.2, self.ready)

    def notify(self, *args, **kwargs) -> None:
        """No notifications in the pictures: they would cover what the scene is about."""

    def ready(self) -> None:
        (SIGNALS / f"{self.scene}.ready").write_text(self.steps[self.scene].__name__, encoding="utf-8")

    def check(self) -> None:
        if time.monotonic() - self.started > LONGEST:
            self.exit()
        elif (SIGNALS / f"{self.scene}.done").exists():
            (SIGNALS / f"{self.scene}.done").unlink()
            self.scene += 1
            self.next_scene()

    # --- the scenes --------------------------------------------------------------------------

    def first(self) -> None:
        self.table.move_cursor(row=self.table.get_row_index("pcb-router"))

    def card(self) -> None:
        self.action_card("waiting")

    def card_off_team_row(self) -> None:
        self.action_card("waiting")
        self.selected.clear()
        self.table.move_cursor(row=self.table.get_row_index(f"{TEAM_ROW}shop"))
        flying = teams.Message(datetime.now(), "lead", "tests", "Mock the webhook meanwhile")
        self.team_flights["shop"] = [(flying, time.monotonic() + 0.4)]

    def unfold(self) -> None:
        self.team_flights.clear()
        self.toggle_folded("shop")

    def team_view(self) -> None:
        self.push_screen(TeamScreen(self.team_states["shop"].team, self.accent()))

    def back_live(self) -> None:
        self.pop_screen()
        self.toggle_folded("shop")
        self.action_toggle_live()

    def live_off_stats(self) -> None:
        self.action_toggle_live()
        self.table.move_cursor(row=self.table.get_row_index("pcb-router"))
        self.action_tokens()

    def stats_work(self) -> None:
        self.screen.action_tab(1)  # as the right arrow does

    def stats_limits(self) -> None:
        self.screen.action_tab(1)
        self.screen.action_tab(1)  # past the tools and files

    def stats_report(self) -> None:
        self.screen.action_tab(1)
        self.call_after_refresh(self.screen.action_write_report)  # the made-up one: no model call

    def stats_off_keys(self) -> None:
        self.pop_screen()
        self.action_keys()


def main() -> None:
    SIGNALS.mkdir(parents=True, exist_ok=True)
    _, team_live = demo_data.build_team(HOME / "claude")
    sessions._agents_json = lambda include_finished=False: demo_data.LIVE + team_live
    app_module.recent_shutdowns = lambda: [demo_data.SHUTDOWN]
    app_module.open_tabs = nothing  # whatever happens, no real tabs
    app_module.run_in_background = nothing
    focus.focus_tab = lambda title: False
    focus.close_tab = lambda title: False
    toast.show = lambda title, body, dashboard: toast.FAILED
    usage.read_usage = list
    demo_data.made_up_limits()
    pcb = next((HOME / "claude").glob("projects/*/pcb-router.jsonl"))
    summarize._store("pcb-router", {"summary": demo_data.SUMMARY, "size": pcb.stat().st_size, "model": "haiku"})
    summarize.ask = lambda prompt, model, timeout=180: demo_data.DEMO_REPORT  # no model call
    report.save = lambda entry, folder=None: HOME / "report.md"
    Demo(days=14).run()


if __name__ == "__main__":
    main()
