"""The team monitor in a desktop window: `tabmancer desktop --monitor [team]`.

The terminal monitor's team (`tabmancer team watch`) in the dashboard's black, with room: a card a
member, the conversation as lanes, the messages, the team's hours and the plan limits as rings,
each a panel you can drag, split, float and close (View puts them back). The menus name every key
of the monitor and the key bar stays under the window.

It only watches. The team is read through the one-team store on a thread of its own, every two
seconds; nothing here claims the actor lock, opens, starts, sends or closes a session. Enter goes
to a member's open tab, as in the terminal monitor; what changes the team (s a d g h H x p, and D
for the dashboard) is named in the menus and done in the terminal monitor for now.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, agy, bitart, focus, teams, teamview
from ..config import config_dir, merge_json, read_json
from ..keys import MONITOR_KEYS
from ..sessions import Exit, Status
from ..store import Snapshot, TeamStore
from ..tokens import human
from . import look
from .widgets import (
    HourChart,
    KeyBar,
    Lanes,
    LimitRing,
    MemberCard,
    Pill,
    bit_pixmap,
    facts_html,
    last_words,
    text_label,
    tone_colour,
)

POLL_MS = 2000  # as often as the terminal monitor reads its team
SAID_MS = 7000  # how long Bit says what just happened before it says how the team stands again
KNOCK_MS = 500  # how long a second monitor waits for the first to answer
CARD_WIDTH = 340  # a card's least width: as many columns as fit
LAYOUT_FILE = "desktop.json"
LAYOUT_VERSION = 1
DEMO_ENV = ("CLAUDE_CONFIG_DIR", "TABMANCER_CONFIG_DIR", "CODEX_HOME")  # what demo.prepare points elsewhere
# The monitor's keys that work here (MONITOR_KEYS names them all); the rest are the terminal monitor's.
HERE = {"← →", "Enter", "j", "Esc", "T", "?", "q"}
MENUS = {"Members": "&Members", "The team": "&Team", "Windows": "&Windows"}
KEY_BAR = [("← →", "member"), ("Enter", "its tab"), ("j", "needs you"), ("Esc", "everyone"), ("T", "teams"),
           ("?", "keys"), ("q", "quit")]
AGY_TONE = {"ok": look.CYAN, "low": look.AMBER, "out": look.RED, "unknown": look.MUTED}
GROUND = {look.AMBER: "#221b0c", look.RED: "#2a0f0e"}  # a pill's patch under amber or red words


def short(words: str) -> str:
    """A key's words for a menu: up to the first ':', ';', ',' or '(' of what MONITOR_KEYS says it does."""
    cut = len(words)
    for mark in (":", ";", ",", " ("):
        at = words.find(mark)
        if at != -1:
            cut = min(cut, at)
    text = words[:cut].strip()
    return text[:1].upper() + text[1:]


def sequences(key: str) -> list[QKeySequence]:
    """The shortcuts of a key as MONITOR_KEYS writes it: 'j', 'T' (Shift+T), 'Enter', 'Esc', '?'."""
    # '?' as typed: with Shift on most layouts, which some platforms' key maps keep in the key.
    named = {"Enter": ["Return", "Enter"], "Esc": ["Escape"], "?": ["?", "Shift+?"], "← →": []}
    if key in named:
        return [QKeySequence(k) for k in named[key]]
    if len(key) == 1 and key.isalpha() and key.isupper():
        return [QKeySequence(f"Shift+{key}")]
    return [QKeySequence(key.upper())]


def elsewhere(team: str) -> str:
    return f"In the terminal monitor for now: tabmancer team watch {team}"


def tip(words: str, key: str, team: str) -> str:
    """What a key does, and for a key this window does not do yet, where it is done."""
    return words if key in HERE else f"{words}. {elsewhere(team or '<team>')}"


class Relay(QObject):
    """Brings what a worker thread found back to the window's thread."""

    polled = Signal(object, object, object, str)  # the store asked, its Snapshot (None: no such team), agy's bar, an error
    said = Signal(str, str)  # words, colour


def bit_pose(snap: Snapshot | None) -> bitart.Pose:
    """Bit beside the team: alert when someone needs you, at work with it, glad when all is quiet."""
    if snap is not None and any(m.needs_you for m in snap.view.members):
        return bitart.Pose(eyes="wide", beacon=1.0, beacon_colour=(248, 81, 73), right=bitart.Arm("raise", 20))
    if snap is not None and any(m.tone == "working" for m in snap.view.members):
        return bitart.Pose(eyes="focused", look=(0.4, 0.0))
    return bitart.Pose(eyes="happy")


def bit_words(snap: Snapshot | None, team: str, bar: dict | None = None) -> tuple[str, str]:
    """What Bit says of the team, and in which colour: who needs you first, then agy's chain when
    all of it is out (agy.bar_view's words), then who is at work."""
    if snap is None:
        return f"Reading {team}…" if team else "No team picked: T picks one.", look.MUTED
    waiting = [m.name for m in snap.view.members if m.needs_you]
    if waiting:
        who = ", ".join(waiting)
        return f"{who} {'needs' if len(waiting) == 1 else 'need'} you: j picks them in turn.", look.RED
    if bar and bar.get("said"):
        return str(bar["said"]), look.AMBER
    working = [m.name for m in snap.view.members if m.tone == "working"]
    if working:
        return f"{', '.join(working)} at work.", look.BLUE
    if not snap.view.open:
        return f"Nobody of {team} is open.", look.MUTED
    return "All quiet.", look.MUTED


class MonitorWindow(QMainWindow):
    def __init__(self, team: str, *, store: Callable[[str], TeamStore] = TeamStore, poll_ms: int = POLL_MS,
                 start: bool = True) -> None:
        super().__init__()
        self.make_store = store
        self.team = team
        self.store: TeamStore | None = store(team) if team else None
        self.snap: Snapshot | None = None
        self.picked: str | None = None
        self.needs_turn = -1
        self.waiting = False
        self.reader: threading.Thread | None = None
        self.accent = look.accent()
        self.colours: dict[str, str] = {}
        self.cards: dict[str, MemberCard] = {}
        self.columns = 0
        self.bar: dict = {}
        self.shown_pills: list = []
        self.ring_widgets: list[LimitRing] = []
        self.relay = Relay()
        self.relay.polled.connect(self.take)
        self.relay.said.connect(self.say)
        self.said_timer = QTimer(self)
        self.said_timer.setSingleShot(True)
        self.said_timer.timeout.connect(self.show_bit)
        self.door = QLocalServer(self)  # a second monitor of this team knocks here instead of opening
        self.door.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.door.newConnection.connect(self.answer_door)
        self.open_door()
        self.setStyleSheet(look.sheet(self.accent))
        self.resize(1500, 950)
        self.build()
        self.default_state = self.saveState(LAYOUT_VERSION)
        self.restore_layout()
        self.name_window()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        if start:
            self.timer.start(poll_ms)
            self.poll()

    # --- building ---------------------------------------------------------------------------------

    def build(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        box = QVBoxLayout(central)
        box.setContentsMargins(16, 12, 16, 8)
        box.setSpacing(8)
        top = QHBoxLayout()
        self.head = text_label(size=11)
        self.head.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.head)
        top.addStretch()
        self.pills = QHBoxLayout()
        self.pills.setSpacing(6)
        top.addLayout(self.pills)
        box.addLayout(top)
        self.goal = text_label(size=9.5, wrap=True)
        self.goal.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.goal)
        self.needs = text_label(size=9.5, wrap=True)
        self.needs.setTextFormat(Qt.TextFormat.RichText)
        self.needs.setStyleSheet(f"background:#1f0d0c; border-left:3px solid {look.RED}; padding:5px 10px;")
        self.needs.hide()
        box.addWidget(self.needs)
        self.grid_host = QWidget()
        column = QVBoxLayout(self.grid_host)
        column.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        column.addLayout(self.grid)
        column.addStretch(1)  # the cards as tall as they need, the room left under them
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.grid_host)
        scroll.setStyleSheet(f"QScrollArea {{ background:{look.BLACK}; }} QWidget {{ background:{look.BLACK}; }}")
        self.cards_area = scroll
        box.addWidget(scroll, 1)
        self.picked_lines = text_label(size=9.5, wrap=True)
        self.picked_lines.setTextFormat(Qt.TextFormat.RichText)
        self.picked_lines.setStyleSheet(f"background:{look.SURFACE}; border-radius:4px; padding:6px 10px;")
        box.addWidget(self.picked_lines)
        floor = QHBoxLayout()
        self.bit = QLabel()
        self.bit.setStyleSheet("background:transparent;")
        self.bit_line = text_label(size=9.5)
        floor.addWidget(self.bit, 0, Qt.AlignmentFlag.AlignBottom)
        floor.addWidget(self.bit_line, 1, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(floor)
        self.setCentralWidget(central)

        self.lanes = Lanes()
        self.messages = QTextBrowser()
        self.messages.setFont(look.font(9))
        self.messages.setOpenLinks(False)
        usage = QWidget()
        usage.setStyleSheet(f"background:{look.SUNKEN};")
        usage_box = QVBoxLayout(usage)
        usage_box.setContentsMargins(8, 8, 8, 8)
        self.hours = HourChart(self.accent)
        usage_box.addWidget(self.hours, 1)
        self.rings = QHBoxLayout()
        self.rings.setSpacing(4)
        usage_box.addLayout(self.rings)
        self.docks = {
            "conversation": self.dock("Conversation", self.lanes, Qt.DockWidgetArea.BottomDockWidgetArea),
            "messages": self.dock("Messages", self.messages, Qt.DockWidgetArea.RightDockWidgetArea),
            "usage": self.dock("Usage", usage, Qt.DockWidgetArea.RightDockWidgetArea),
        }
        self.resizeDocks([self.docks["messages"]], [560], Qt.Orientation.Horizontal)
        self.resizeDocks([self.docks["conversation"]], [240], Qt.Orientation.Vertical)
        self.build_menus()
        self.keybar = KeyBar(KEY_BAR, "read-only · the rest: tabmancer team watch")
        self.statusBar().addWidget(self.keybar, 1)
        self.show_bit()

    def dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        made = QDockWidget(title, self)
        made.setObjectName(f"dock-{title.lower()}")  # saveState keeps each panel by it
        made.setWidget(widget)
        made.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        made.setFont(look.font(9))
        self.addDockWidget(area, made)
        return made

    def build_menus(self) -> None:
        """A menu a group of the monitor's keys, every key in it: the ones done here, and the ones the
        terminal monitor does (greyed, saying so). View brings closed panels back."""
        bar = self.menuBar()
        bar.setFont(look.font(9.5))
        self.actions_by_key: dict[str, list[QAction]] = {}
        doing = {"← →": None, "Enter": self.go_to_tab, "j": self.next_needs, "Esc": self.clear_pick,
                 "T": self.choose_team, "?": self.show_keys, "q": self.close}
        for group, keys in MONITOR_KEYS:
            menu = bar.addMenu(MENUS.get(group, group))
            menu.setToolTipsVisible(True)
            for key, _action, words in keys:
                if key == "← →":
                    made = [self.action(menu, "Next member", [QKeySequence("Right")], lambda: self.pick(1), words),
                            self.action(menu, "Previous member", [QKeySequence("Left")], lambda: self.pick(-1), words)]
                else:
                    made = [self.action(menu, short(words), sequences(key), doing.get(key), words)]
                for action in made:
                    action.setData(words)
                    action.setEnabled(key in HERE)
                    action.setToolTip(tip(words, key, self.team))
                    action.setStatusTip(tip(words, key, self.team))
                self.actions_by_key[key] = made
        view = bar.addMenu("&View")
        for dock in self.docks.values():
            view.addAction(dock.toggleViewAction())
        view.addSeparator()
        reset = QAction("Put the panels back", self)
        reset.triggered.connect(self.reset_layout)
        view.addAction(reset)
        helping = bar.addMenu("&Help")
        keys = QAction("All keys…", self)
        keys.triggered.connect(self.show_keys)
        helping.addAction(keys)
        about = QAction(f"tabmancer {__version__}", self)
        about.setEnabled(False)
        helping.addAction(about)

    def action(self, menu, text: str, keys: list[QKeySequence], does, words: str) -> QAction:
        made = QAction(text, self)
        made.setShortcuts(keys)
        made.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        made.setStatusTip(words)
        made.setToolTip(words)
        if does is not None:
            made.triggered.connect(does)
        menu.addAction(made)
        self.addAction(made)  # the key works with the menu closed
        return made

    # --- reading ----------------------------------------------------------------------------------

    def poll(self) -> None:
        """Read the team on a thread of its own (one read at a time): the window never waits on it."""
        if self.waiting or self.store is None:
            return
        self.waiting = True
        self.reader = threading.Thread(target=self.read, args=(self.store,), daemon=True, name="tabmancer-monitor")
        self.reader.start()

    def read(self, store: TeamStore) -> None:
        try:
            snap, error = store.poll(), ""
            bar = agy_bar() if snap is not None and snap.agy_quota else {}
        except Exception as caught:  # noqa: BLE001 - said in the window, tried again at the next poll
            snap, bar, error = None, {}, f"{type(caught).__name__}: {caught}"
        try:
            self.relay.polled.emit(store, snap, bar, error)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def settle(self, seconds: float = 10) -> None:
        """Wait for a read under way (on closing, and in tests before what it reads goes)."""
        if self.reader is not None:
            self.reader.join(seconds)

    def take(self, store: TeamStore, snap: Snapshot | None, bar: dict, error: str) -> None:
        self.waiting = False
        if store is not self.store:
            return  # another team picked meanwhile
        self.bar = bar
        if error:
            self.say(f"Could not read {self.team}: {error}", look.AMBER)
            return
        if snap is None:
            self.snap = None
            self.say(f"There is no team {self.team} (any more). T picks another.", look.AMBER)
            return
        if self.snap is None:
            team = teams.load().get(self.team)
            self.colours = teamview.colours(team, self.accent) if team is not None else {}
        self.snap = snap
        if [m.name for m in snap.view.members] != list(self.cards):
            team = teams.load().get(self.team)
            self.colours = teamview.colours(team, self.accent) if team is not None else self.colours
        self.show_team()

    # --- showing ----------------------------------------------------------------------------------

    def name_window(self) -> None:
        self.setWindowTitle(f"{self.team} · tabmancer monitor" if self.team else "tabmancer monitor")

    def show_team(self) -> None:
        snap = self.snap
        if snap is None:
            return
        view = snap.view
        if self.picked is not None and self.picked not in {m.name for m in view.members}:
            self.picked = None
        said = len(snap.state.said)
        head = (look.span("TEAM ", f"bold {look.TEXT}") + look.span(view.name, f"bold {self.accent}")
                + look.span(f"  ·  {view.open} of {len(view.members)} open  ·  {said} message{'s' * (said != 1)}",
                            look.MUTED))
        if view.tokens:
            head += look.span(f"  ·  {human(view.tokens)} tokens", look.MUTED)
        self.head.setText(head)
        self.goal.setText(look.span("Goal  ", look.MUTED) + look.span(view.goal or "no goal written", look.TEXT))
        waiting = [m for m in view.members if m.needs_you]
        self.needs.setVisible(bool(waiting))
        if waiting:
            # As the terminal monitor says it (monitorview.needs_line).
            self.needs.setText(look.span("NEEDS YOU  ", f"bold {look.RED}") + look.span(" · ", look.MUTED).join(
                look.span(m.name, f"bold {self.colours.get(m.name, look.TEXT)}")
                + look.span(f": {m.label}", tone_colour(m.tone))
                for m in waiting) + look.span("   j picks them in turn", look.MUTED))
        self.show_pills(snap)
        self.show_cards(view)
        self.show_picked()
        self.lanes.set(view, self.colours, self.picked)
        self.show_messages(view)
        self.hours.set(snap.hours)
        self.show_rings(snap)
        if not self.said_timer.isActive():
            self.show_bit()

    def show_pills(self, snap: Snapshot) -> None:
        """The plan limits ('Claude session 34%', 'week 61%', as the terminal monitor's quota line says
        them; when each lifts in its tip) and agy's chain as the dashboard's top bar says it."""
        pills = []
        for i, limit in enumerate(snap.limits):
            colour = look.percent_colour(limit.percent)
            lifts = f"lifts {limit.resets:%a %H:%M}" if limit.resets else limit.resets_text or ""
            pills.append((f"{'' if i else 'Claude '}{limit.label} {limit.percent}%", colour,
                          GROUND.get(colour, "#1c1c1c"), lifts))
        for words, tone in self.bar.get("parts") or []:
            pills.append((words, AGY_TONE.get(tone, look.MUTED), "#0f2326", ""))
        if pills == self.shown_pills:
            return
        self.shown_pills = pills
        clear(self.pills)
        for words, colour, ground, tip_words in pills:
            pill = Pill(words, colour, ground)
            pill.setToolTip(tip_words)
            self.pills.addWidget(pill)

    def show_cards(self, view) -> None:
        names = [m.name for m in view.members]
        columns = max(1, min(len(names) or 1, self.cards_area.viewport().width() // CARD_WIDTH))
        if names != list(self.cards) or columns != self.columns:
            for card in self.cards.values():
                card.deleteLater()
            self.cards = {name: MemberCard() for name in names}
            for i, card in enumerate(self.cards.values()):
                self.grid.addWidget(card, i // columns, i % columns)
            self.columns = columns
        for member in view.members:
            self.cards[member.name].set(member, self.colours, member.name == self.picked, self.accent)

    def show_picked(self) -> None:
        snap = self.snap
        if snap is None:
            return
        view = snap.view
        member = next((m for m in view.members if m.name == self.picked), None)
        if member is None:
            first = look.span("everyone", f"bold {look.TEXT}") + look.span(
                f"  ·  {view.open} of {len(view.members)} open", look.MUTED)
            newest = view.latest[-1] if view.latest else None
            second = (look.span(f"last {newest.at:%H:%M}  ", look.DIM)
                      + look.span(f"{newest.sender} → {newest.receiver}: {newest.text}", look.TEXT)
                      if newest is not None else look.span("Nothing said yet.", look.MUTED))
        else:
            first = (look.span(member.name, f"bold {self.colours.get(member.name, look.TEXT)}")
                     + look.span("  ·  ", look.DIM) + look.span(member.label, tone_colour(member.tone))
                     + look.span("  ·  ", look.DIM) + facts_html(member, leave_out_model=False))
            second = last_words(member, self.colours)
        self.picked_lines.setText(f"{first}<br>{second}")

    def show_messages(self, view) -> None:
        rows = []
        for message in reversed(view.latest):
            when = f"{message.at:%H:%M}" if message.at else "     "
            rows.append(look.span(when + "  ", look.DIM)
                        + look.span(message.sender, f"bold {self.colours.get(message.sender, look.TEXT)}")
                        + look.span(" → ", look.MUTED)
                        + look.span(message.receiver, f"bold {self.colours.get(message.receiver, look.TEXT)}")
                        + "<br>" + look.span(message.text, look.TEXT))
        html = "<br><br>".join(rows) or look.span("Nothing said yet.", look.MUTED)
        if html != getattr(self, "messages_html", None):
            self.messages_html = html
            self.messages.setHtml(f'<div style="font-family:{look.mono_family()}">{html}</div>')

    def show_rings(self, snap: Snapshot) -> None:
        """A ring a Claude plan limit (how much is used) and a model of agy's chain (how much is left)."""
        rings = []
        for limit in snap.limits:
            under = f"lifts {limit.resets:%a %H:%M}" if limit.resets else limit.resets_text or ""
            colour = look.percent_colour(limit.percent)
            rings.append((f"Claude {limit.label}", limit.percent / 100, f"{limit.percent}%", under,
                          colour if colour != look.TEXT else self.accent))
        today = datetime.now().date()
        for standing in snap.agy_quota.get("chain") or []:
            name, left, back = standing.get("name") or standing.get("model", "?"), standing.get("left"), standing.get("back")
            if back is not None:  # 'Neon out till 14:00', as the terminal monitor says it
                when = f"{back:%H:%M}" if back.date() == today else f"{back:%a %H:%M}"
                rings.append((f"agy {name}", 0.0, "out", f"till {when}", look.RED))
            elif left is None:
                rings.append((f"agy {name}", 0.0, "?", "quota not known now", look.MUTED))
            else:  # its colour as the terminal monitor's: amber from 60% used, red from 85%
                colour = look.percent_colour(100 - left)
                rings.append((f"agy {name}", left / 100, f"{left:.0f}%", f"left · {standing.get('span') or 'limit'}",
                              look.CYAN if colour == look.TEXT else colour))
        if len(rings) != len(self.ring_widgets):
            clear(self.rings)
            self.ring_widgets = [LimitRing() for _ in rings]
            for ring in self.ring_widgets:
                self.rings.addWidget(ring)
            self.rings.addStretch()
        for ring, shown in zip(self.ring_widgets, rings, strict=True):
            ring.set(*shown)

    def show_bit(self) -> None:
        words, colour = bit_words(self.snap, self.team, self.bar)
        self.put_bit(words, colour)

    def put_bit(self, words: str, colour: str) -> None:
        pixmap = bit_pixmap(bit_pose(self.snap), 0.6)
        self.bit.setPixmap(pixmap)
        self.bit_line.setText(words)
        self.bit_line.setStyleSheet(f"color:{colour}; background:transparent;")

    def say(self, words: str, colour: str = look.TEXT) -> None:
        """Bit says what just happened for a few seconds, then how the team stands again."""
        self.put_bit(words, colour)
        self.said_timer.start(SAID_MS)

    # --- keys -------------------------------------------------------------------------------------

    def members(self) -> list:
        return list(self.snap.view.members) if self.snap is not None else []

    def pick(self, step: int) -> None:
        members = self.members()
        if not members:
            return
        names = [m.name for m in members]
        at = names.index(self.picked) if self.picked in names else (-1 if step > 0 else 0)
        self.picked = names[(at + step) % len(names)]
        self.refresh_pick()

    def clear_pick(self) -> None:
        self.picked = None
        self.refresh_pick()

    def next_needs(self) -> None:
        waiting = [m.name for m in self.members() if m.needs_you]
        if not waiting:
            self.say("Nobody needs you right now.", look.MUTED)
            return
        self.needs_turn = (self.needs_turn + 1) % len(waiting)
        self.picked = waiting[self.needs_turn]
        self.refresh_pick()

    def refresh_pick(self) -> None:
        if self.snap is None:
            return
        self.show_cards(self.snap.view)
        self.show_picked()
        self.lanes.set(self.snap.view, self.colours, self.picked)

    def go_to_tab(self) -> None:
        """Enter: the picked member's tab to the front, where it is open in one."""
        if self.snap is None or self.picked is None:
            self.say("Pick a member with ← → first.", look.MUTED)
            return
        session = self.snap.state.sessions.get(self.picked)
        name = self.picked
        if session is None or session.exit is not Exit.RUNNING:
            self.say(f"{name} is not open. {elsewhere(self.team)} opens it.", look.AMBER)
            return
        if session.status is Status.BACKGROUND:
            self.say(f"{name} runs in the background, with no tab: B in the dashboard attaches to it.", look.MUTED)
            return

        def switch() -> None:
            found = focus.focus_tab(session.title)
            self.relay.said.emit(f"Switched to {name}'s tab." if found else
                                 f"Could not find {name}'s tab. It may be open outside Windows Terminal, or renamed.",
                                 look.TEXT if found else look.AMBER)

        threading.Thread(target=switch, daemon=True, name="tabmancer-focus").start()

    def choose_team(self) -> None:
        names = sorted(teams.load())
        if not names:
            self.say("There is no team yet: T in the dashboard makes one.", look.AMBER)
            return
        at = names.index(self.team) if self.team in names else 0
        name, ok = QInputDialog.getItem(self, "Teams", "Watch which team?", names, at, False)
        if ok and name:
            self.switch_team(name)

    def switch_team(self, name: str) -> None:
        if name == self.team and self.store is not None:
            return
        if knock(name):  # watched in a window of its own already: that one comes forward, this one stays
            self.say(f"{name} is open in another window: brought to the front.", look.MUTED)
            return
        self.team, self.store, self.snap, self.picked, self.needs_turn = name, self.make_store(name), None, None, -1
        for card in self.cards.values():
            card.deleteLater()
        self.cards, self.columns = {}, 0
        for key, actions in self.actions_by_key.items():
            for action in actions:
                action.setToolTip(tip(str(action.data()), key, name))
                action.setStatusTip(tip(str(action.data()), key, name))
        self.name_window()
        self.open_door()
        self.show_bit()
        self.poll()

    def show_keys(self) -> None:
        KeysDialog(self, self.team).show()

    # --- one window a team ------------------------------------------------------------------------

    def open_door(self) -> None:
        """Listen under the team's name (door), so M or the command again brings this window forward."""
        self.door.close()
        if not self.team:
            return
        name = door(self.team)
        if not self.door.listen(name) and self.door.serverError() == QAbstractSocket.SocketError.AddressInUseError:
            QLocalServer.removeServer(name)  # left by a monitor that ended without closing (Unix); knock found none
            self.door.listen(name)

    def answer_door(self) -> None:
        while (caller := self.door.nextPendingConnection()) is not None:
            caller.disconnectFromServer()
            caller.deleteLater()
        self.come_forward()

    def come_forward(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # --- the window -------------------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.snap is not None:
            self.show_cards(self.snap.view)

    def restore_layout(self) -> None:
        saved = read_json(LAYOUT_FILE)
        kept = saved.get("monitor") if isinstance(saved, dict) else None
        if not isinstance(kept, dict):
            return
        try:
            if kept.get("geometry"):
                self.restoreGeometry(QByteArray(base64.b64decode(kept["geometry"])))
            if kept.get("state"):
                self.restoreState(QByteArray(base64.b64decode(kept["state"])), LAYOUT_VERSION)
        except (TypeError, ValueError):
            pass  # a layout written by hand or cut short: the panels as they start

    def save_layout(self) -> None:
        merge_json(LAYOUT_FILE, monitor={
            "geometry": base64.b64encode(bytes(self.saveGeometry().data())).decode("ascii"),
            "state": base64.b64encode(bytes(self.saveState(LAYOUT_VERSION).data())).decode("ascii")})

    def reset_layout(self) -> None:
        self.restoreState(self.default_state, LAYOUT_VERSION)
        for dock in self.docks.values():
            dock.setFloating(False)
            dock.show()

    def closeEvent(self, event) -> None:
        self.door.close()
        self.timer.stop()
        self.said_timer.stop()
        self.save_layout()
        super().closeEvent(event)


def door(team: str) -> str:
    """The name a team's open monitor listens on: one per team and tabmancer folder (the demo's is
    a folder of its own), so the same team's monitor opened again comes forward instead."""
    key = f"{config_dir()}\0{team}".encode()
    return f"tabmancer-monitor-{hashlib.sha256(key).hexdigest()[:20]}"


def knock(team: str) -> bool:
    """Whether a window watches this team already; if one does, it comes to the front."""
    if not team:
        return False
    if sys.platform == "win32":  # this process may take the front (started from what you typed in): let that window
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    caller = QLocalSocket()
    caller.connectToServer(door(team))
    if not caller.waitForConnected(KNOCK_MS):
        return False
    caller.disconnectFromServer()
    return True


def agy_bar() -> dict:
    """agy's chain as the dashboard's top bar says it (agy.bar_view: from the saved quota, never
    running agy); {} where this agy module has no bar_view yet."""
    bar_view = getattr(agy, "bar_view", None)
    return bar_view() if bar_view is not None else {}


def clear(layout) -> None:
    """Take every widget out of a layout (and drop it)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()


class KeysDialog(QDialog):
    """?: every key of the monitor, what it does, and where."""

    def __init__(self, parent: QWidget, team: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keys · tabmancer monitor")
        self.resize(760, 560)
        box = QVBoxLayout(self)
        text = QTextBrowser()
        text.setFont(look.font(9.5))
        rows = []
        for group, keys in MONITOR_KEYS:
            rows.append(look.span(group.upper(), f"bold {look.MUTED}"))
            for key, _action, words in keys:
                where = "" if key in HERE else look.span(f"   ({elsewhere(team or '<team>')})", look.DIM)
                rows.append(look.span(f"{key:<6}", f"bold {look.TEXT if key in HERE else look.DIM}")
                            + look.span(words, look.TEXT if key in HERE else look.MUTED) + where)
            rows.append("")
        text.setHtml(f'<pre style="font-family:{look.mono_family()}">' + "<br>".join(rows) + "</pre>")
        box.addWidget(text)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}")


def run(team: str, demo: bool = False) -> int:
    """The window, till it is closed. With demo, over the made-up sessions and team of `tabmancer
    --demo`, in a folder of its own that goes when it closes: nothing real is read, and what the
    demo put in place of the world is put back after it."""
    look.fonts_for_offscreen()
    app = QApplication.instance() or QApplication(["tabmancer"])
    home, kept, environ = None, [], {}
    if demo:
        from .. import demo as made_up

        environ = {name: os.environ.get(name) for name in DEMO_ENV}
        home = Path(tempfile.mkdtemp(prefix="tabmancer-demo-"))
        live = made_up.prepare(home)
        told: list[str] = []
        for module, name, value in made_up.stand_ins(home, live, told.append):
            kept.append((module, name, getattr(module, name)))
            setattr(module, name, value)
        team = team or "shop"  # the demo's team
    window = None
    try:
        if knock(team):  # watched already (M again, or the command twice): that window comes forward
            return 0
        window = MonitorWindow(team)
        window.show()
        if not team:
            QTimer.singleShot(0, window.choose_team)
        code = app.exec()
        if window.isVisible():  # the app was told to quit, the window not closed: its layout is kept too
            window.close()
        return code
    finally:
        if window is not None:
            window.timer.stop()
            window.settle()
        for module, name, value in reversed(kept):
            setattr(module, name, value)
        for name, value in environ.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
