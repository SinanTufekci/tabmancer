"""The main window's Team panel: a tab a team, its Orbit (teamgraph) over its conversation's lanes
(conversation), from what the dashboard read (store.DashSnapshot: view.teams and team_states).

It watches, as the window does: a click on a member lights its messages (again: everyone's); its
tab, its actions and the rest stay the terminal dashboard's and the monitor's (M). A team with a
member who needs you says so on its tab."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QSplitter, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from .. import teamwords
from .. import viewmodel as VM
from ..store import DashSnapshot
from . import look, teamgraph
from .conversation import Conversation
from .teamgraph import TeamGraph
from .widgets import text_label

NO_TEAM = "No team yet. T starts one; it shows here once a member has run."
HINT = "click: its messages lit · again: everyone's"
NEEDS = " ●"  # on a team's tab while a member needs you


def tab_words(view: VM.TeamView) -> str:
    return view.name + (NEEDS if any(m.needs_you for m in view.members) else "")


def tab_tip(view: VM.TeamView) -> str:
    needing = [m.name for m in view.members if m.needs_you]
    lines = [view.goal] if view.goal else []
    lines.append(f"{view.open} of {len(view.members)} open")
    if needing:
        lines.append(f"needs you: {', '.join(needing)}")
    return "\n".join(lines)


class TeamPanel(QWidget):
    def __init__(self, accent: str, now: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.accent = accent
        self.snap: DashSnapshot | None = None
        self.team: str | None = None  # the team shown
        self.picked: str | None = None
        self.colours: dict[str, str] = {}
        self.coloured: list[str] = []  # the members the colours were picked for
        self.names: list[str] = []  # the tabs', in order
        self.tabs = QTabBar()
        self.tabs.setObjectName("teams")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setFont(look.font(9))
        self.tabs.currentChanged.connect(self.pick_team)
        self.graph = TeamGraph(now=now)
        self.graph.hint = HINT
        self.graph.member_clicked.connect(self.pick_member)
        self.lanes = Conversation(now=now)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.graph)
        self.split.addWidget(self.lanes)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.empty = text_label(NO_TEAM, look.MUTED, 9.5, wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty.setContentsMargins(12, 10, 12, 10)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.split)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.tabs)
        column.addWidget(self.pages, 1)

    def set(self, snap: DashSnapshot) -> None:
        """What the dashboard read: the tabs, then the team shown (the same one while it is there)."""
        self.snap = snap
        views = snap.view.teams
        names = [view.name for view in views]
        self.tabs.blockSignals(True)
        try:
            if names != self.names:
                while self.tabs.count():
                    self.tabs.removeTab(0)
                for view in views:
                    self.tabs.addTab(tab_words(view))
                self.names = names
            for index, view in enumerate(views):
                self.tabs.setTabText(index, tab_words(view))
                self.tabs.setTabToolTip(index, tab_tip(view))
            if self.team in names:
                self.tabs.setCurrentIndex(names.index(self.team))
        finally:
            self.tabs.blockSignals(False)
        self.tabs.setVisible(bool(names))
        if self.team not in names:
            self.start(names[0] if names else None)
        self.show_team()

    def start(self, name: str | None) -> None:
        """Another team (or none): picked afresh, and its messages so far stay put."""
        self.team, self.picked = name, None
        self.graph.clear()
        self.lanes.clear()
        self.colours, self.coloured = {}, []

    def pick_team(self, index: int) -> None:
        if 0 <= index < len(self.names) and self.names[index] != self.team:
            self.start(self.names[index])
            self.show_team()

    def wake(self, shown: bool = True) -> None:
        """Its panel's tab to the front or away: the clocks run again, or stop (once it has moved)."""
        QTimer.singleShot(0, self, self.graph.wake)
        QTimer.singleShot(0, self, self.lanes.wake)

    def pick_member(self, name: str) -> None:
        self.picked = None if self.picked == name else name
        self.show_team()

    def shown_view(self) -> VM.TeamView | None:
        if self.snap is None:
            return None
        return next((view for view in self.snap.view.teams if view.name == self.team), None)

    def show_team(self) -> None:
        view = self.shown_view()
        state = self.snap.team_states.get(self.team or "") if self.snap is not None else None
        if view is None or state is None:
            self.pages.setCurrentWidget(self.empty)
            return
        self.pages.setCurrentWidget(self.split)
        names = [m.name for m in state.team.members]
        if names != self.coloured:  # a member joined or left: the colours picked again, as the monitor does
            self.colours, self.coloured = teamwords.colours(state.team, self.accent), names
        if self.picked not in [m.name for m in view.members]:
            self.picked = None
        motion = teamgraph.motion()  # the monitor's View ▸ Reduce motion, and b's calm
        self.graph.set_motion(motion)
        self.lanes.set_motion(motion)
        self.graph.set(view, state.said, self.colours, self.picked)
        self.lanes.set(state.team, state.said, view, self.colours, self.picked)
