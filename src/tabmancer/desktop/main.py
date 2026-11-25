"""The dashboard in a desktop window: `tabmancer desktop`.

The terminal dashboard in its black, with room: monospace, the accent, panels you can drag, split,
float and close (Panels puts them back), menus that name every key of the dashboard, and the key
bar under the window. The top bar says what the terminal dashboard's says, in the same words
(viewmodel): the sessions listed, who each CLI is signed in as, the plan limits and agy's chain,
a live dot and the clock. The Sessions panel is the Board (board.py): the sessions as cards in
lanes by what they want from you, the cards' rules (a switch under the top bar picks the view; the
table comes later).

It only watches, as a second dashboard does: the dashboard's store (store.DashboardStore) is read
on a thread of its own every two seconds, and nothing here claims the actor lock, records a plan
limit or an account, opens, starts or closes a session. The panels fill in slice by slice; till
then a key not done here is greyed in its menu and in the key bar, and done in the terminal
dashboard (tabmancer). The Team panel (teampanel), a tab beside the list, shows each team's
picture over its conversation; T brings it to the front. Ctrl+K finds any command by a few of its
words (commandbar): every key as the menus name it, and a team or a session by name, to pick its card.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, bitart, clis, commands
from .. import viewmodel as VM
from ..keys import KEYS
from ..palette import mix
from ..store import DashboardStore, DashSnapshot
from . import commandbar, frame, look, teamgraph
from .board import Board
from .frame import KeysDialog, made_up_world
from .monitor import sequences
from .teampanel import TeamPanel
from .widgets import KeyBar, bit_pixmap, text_label

POLL_MS = 2000  # as often as the terminal dashboard reads its pid files (LIVE_REFRESH_SECONDS)
TICK_MS = 100  # the live dot breathes and the clock goes on
INFO_LEAST = 20  # characters the info line keeps before the accounts give way
LAYOUT_KEY = "main"  # desktop.json's
ELSEWHERE = "In the terminal dashboard for now: tabmancer"
# The dashboard's keys that work here (KEYS names them all); the rest are the terminal dashboard's.
HERE = {"?", "q", "T"}
MENUS = {"Pick and open": "&Open", "Keep the list tidy": "&Tidy", "Work with a session": "&Session",
         "Views": "&Views", "Workspaces": "&Workspaces", "Settings and the rest": "Se&ttings"}
KEY_BAR = [("Enter", "open"), ("n", "new"), ("T", "teams"), ("h", "hand over"), ("c", "continue"), ("M", "monitor"),
           ("V", "split"), ("/", "filter"), ("A", "accounts"), (commandbar.KEY, "commands"), ("?", "all keys"),
           ("q", "quit")]
# The top bar's tones (viewmodel.usage_parts, account_parts) as colours.
TONES = {"muted": look.MUTED, "text": look.TEXT, "empty": "#2a2a2a", "ok": look.GREEN, "low": look.AMBER,
         "out": look.RED}
PANELS = ("Sessions", "Preview", "Projects", "Bit", "Team")
SOON = {
    "Preview": "The highlighted session: how it stands, what it said last",
    "Projects": "The projects, with where each stands in git, and the week's tokens",
}
VIEWS = (("board", "Board"), ("table", "Table"), ("live", "Live"))  # what the Sessions panel shows
SHOWN_HERE = {"board"}
BOARD_HINT = f"↑ ↓ ← → pick a card · a click on DONE opens it · {commandbar.KEY} any command"


def parts_html(parts: list[tuple[str, str]]) -> str:
    """The top bar's (words, tone) parts as HTML for a QLabel."""
    shown = []
    for words, tone in parts:
        bold, _, name = tone.rpartition(" ")
        colour = TONES.get(name, look.TEXT)
        shown.append(look.span(words, f"bold {colour}" if bold else colour))
    return f'<span style="white-space:pre">{"".join(shown)}</span>'


def watching_store() -> DashboardStore:
    # As the terminal dashboard that watches (another one acts): it asks for the plan limits when it
    # has no fresh reading of the acting dashboard's, and records neither them nor an account.
    return DashboardStore(ask_limits=True)


class Relay(QObject):
    """Brings what the worker thread read back to the window's thread."""

    polled = Signal(object, object, bool, str)  # the store asked, its DashSnapshot, agy first in the bar, an error


class MainWindow(QMainWindow):
    def __init__(self, *, store: Callable[[], DashboardStore] = watching_store, poll_ms: int = POLL_MS,
                 start: bool = True, brand: str = "◆ tabmancer") -> None:
        super().__init__()
        self.store = store()
        self.snap: DashSnapshot | None = None
        self.agy_first = False
        self.waiting = False
        self.reader: threading.Thread | None = None
        self.accent = look.accent()
        self.brand_words = brand
        self.relay = Relay()
        self.relay.polled.connect(self.take)
        self.door = QLocalServer(self)  # `tabmancer desktop` again knocks here instead of opening a second window
        self.door.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.door.newConnection.connect(self.answer_door)
        frame.listen(self.door, door())
        self.setStyleSheet(look.sheet(self.accent))
        self.setWindowTitle("tabmancer")
        self.resize(1500, 950)
        self.build()
        self.default_state = self.saveState(frame.LAYOUT_VERSION)
        known = frame.restore_layout(self, LAYOUT_KEY)
        frame.place_new(self, known, self.docks["Team"], [self.docks["Sessions"], self.docks["Preview"]],
                        Qt.DockWidgetArea.RightDockWidgetArea)
        self.started = time.monotonic()
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.tick()
        self.board.setFocus()  # the arrows pick a card from the start
        if start:
            self.ticker.start(TICK_MS)
            self.timer.start(poll_ms)
            self.poll()

    # --- building ---------------------------------------------------------------------------------

    def build(self) -> None:
        central = QWidget()  # the panels are all docks: the middle is theirs
        central.setObjectName("central")
        central.setMaximumSize(0, 0)
        self.setCentralWidget(central)
        self.setDockNestingEnabled(True)
        self.setMenuWidget(self.build_top())  # the menus, and the top bar under them
        self.docks = {name: self.dock(name) for name in PANELS}
        # The projects beside the board, Bit under it, the preview beside them all: in the side areas,
        # which run the window's height (the top one would take what is left over).
        docks = self.docks
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, docks["Projects"])
        self.splitDockWidget(docks["Projects"], docks["Sessions"], Qt.Orientation.Horizontal)
        self.splitDockWidget(docks["Sessions"], docks["Bit"], Qt.Orientation.Vertical)
        self.tabifyDockWidget(docks["Sessions"], docks["Team"])  # a tab beside the list: T, or its tab
        docks["Sessions"].raise_()
        docks["Team"].visibilityChanged.connect(self.team_panel.wake)  # behind a tab: its picture rests
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, docks["Preview"])
        self.resizeDocks([docks["Projects"], docks["Sessions"], docks["Preview"]], [210, 900, 390],
                         Qt.Orientation.Horizontal)
        self.resizeDocks([docks["Sessions"], docks["Bit"]], [700, 100], Qt.Orientation.Vertical)
        self.build_menus()
        self.keybar = KeyBar(KEY_BAR, "greyed: in the terminal dashboard for now (tabmancer)",
                             dim={key for key, _ in KEY_BAR if key not in HERE and key != commandbar.KEY})
        self.statusBar().addWidget(self.keybar, 1)

    def build_top(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("central")  # the sheet's black
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.menus = QMenuBar()  # its own, not menuBar(): setMenuWidget would drop that one
        column.addWidget(self.menus)
        self.topbar = QWidget()
        self.topbar.setStyleSheet(f"background:{look.SURFACE};")
        row = QHBoxLayout(self.topbar)
        row.setContentsMargins(12, 5, 12, 5)
        row.setSpacing(18)
        self.brand = text_label(self.brand_words, self.accent, 10, bold=True)
        self.info = text_label("Reading your sessions…", look.MUTED, 9.5)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # gives way first
        self.pills = text_label("", self.accent, 9.5)  # what the list is narrowed to: with the list
        self.account = self.rich_label()
        self.usage = self.rich_label()
        self.live = self.rich_label()
        self.clock = text_label("", look.MUTED, 9.5)
        row.addWidget(self.brand)
        row.addWidget(self.info, 1)
        for part in (self.pills, self.account, self.usage, self.live, self.clock):
            row.addWidget(part)
        column.addWidget(self.topbar)
        column.addWidget(self.build_toolbar())
        return holder

    def build_toolbar(self) -> QWidget:
        """Under the top bar: the switch of what the Sessions panel shows (the board; the table and the
        live view are the terminal dashboard's for now)."""
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setStyleSheet(f"QWidget#toolbar {{ background:{look.SUNKEN}; }}")  # its own: the buttons keep theirs
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 5, 12, 5)
        row.setSpacing(0)
        self.hint = text_label(BOARD_HINT, look.DIM, 9)
        row.addWidget(self.hint)
        row.addStretch(1)
        self.views = QButtonGroup(self)
        for name, words in VIEWS:
            button = QPushButton(words)
            button.setObjectName("view")
            button.setFont(look.font(9))
            button.setCheckable(True)
            button.setChecked(name == "board")
            button.setEnabled(name in SHOWN_HERE)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip("The sessions as cards, by what they want from you" if name in SHOWN_HERE
                              else f"{words}: {ELSEWHERE.lower()}")
            self.views.addButton(button)
            row.addWidget(button)
        return bar

    def rich_label(self) -> QLabel:
        made = text_label("", look.TEXT, 9.5)
        made.setTextFormat(Qt.TextFormat.RichText)
        return made

    def dock(self, title: str) -> QDockWidget:
        made = QDockWidget(title, self)
        made.setObjectName(f"dock-{title.lower()}")  # saveState keeps each panel by it
        made.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        made.setFont(look.font(9))
        inside = QWidget()
        inside.setStyleSheet(f"background:{look.BLACK};")
        box = QVBoxLayout(inside)
        box.setContentsMargins(12, 10, 12, 10)
        if title == "Team":
            self.team_panel = TeamPanel(self.accent)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(self.team_panel)
        elif title == "Sessions":
            self.board = Board(self.accent)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(self.board)
        elif title == "Bit":
            line = QHBoxLayout()
            self.bit = QLabel()
            self.bit.setStyleSheet("background:transparent;")
            self.bit.setPixmap(bit_pixmap(bitart.Pose(eyes="happy"), 0.6))
            self.bit_line = text_label("", look.MUTED, 9.5, wrap=True)
            line.addWidget(self.bit, 0, Qt.AlignmentFlag.AlignBottom)
            line.addWidget(self.bit_line, 1, Qt.AlignmentFlag.AlignVCenter)
            box.addLayout(line)
        else:
            box.addWidget(text_label(SOON[title], look.MUTED, 9.5, wrap=True))
            box.addWidget(text_label(ELSEWHERE, look.DIM, 9, wrap=True))
            box.addStretch(1)
        made.setWidget(inside)
        return made

    def build_menus(self) -> None:
        """A menu a group of the dashboard's keys, every key in it: the ones done here, and the ones
        the terminal dashboard does (greyed, saying so). Panels brings closed panels back."""
        bar = self.menus
        bar.setFont(look.font(9.5))
        self.actions_by_key: dict[str, QAction] = {}
        doing = {"?": self.show_keys, "q": self.close, "T": self.show_teams}
        self.menus_by_group = {}
        for group, keys in KEYS:
            menu = bar.addMenu(MENUS.get(group, group))
            self.menus_by_group[group] = menu
            menu.setToolTipsVisible(True)
            for key, _action, words in keys:
                made = QAction(commands.short(words), self)
                made.setShortcuts(sequences(key))
                made.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
                said = words if key in HERE else f"{words}. {ELSEWHERE}"
                made.setToolTip(said)
                made.setStatusTip(said)
                made.setData(key)
                made.setEnabled(key in HERE)
                if key in doing:
                    made.triggered.connect(doing[key])
                menu.addAction(made)
                self.addAction(made)  # the key works with the menu closed
                self.actions_by_key[key] = made
        views = self.menus_by_group["Views"]
        views.addSeparator()
        self.motion = teamgraph.MotionMenu(self, views.addMenu("Reduce motion"))
        self.motion.changed.connect(self.motion_changed)
        panels = bar.addMenu("&Panels")
        for dock in self.docks.values():
            panels.addAction(dock.toggleViewAction())
        panels.addSeparator()
        reset = QAction("Put the panels back", self)
        reset.triggered.connect(self.reset_layout)
        panels.addAction(reset)
        helping = bar.addMenu("&Help")
        finding = QAction("Find a command…", self)
        finding.setShortcut(QKeySequence(commandbar.KEY))
        finding.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        finding.triggered.connect(self.open_commands)
        helping.addAction(finding)
        self.addAction(finding)
        self.command_bar = commandbar.CommandBar(self, self.accent)
        self.command_bar.chosen.connect(self.run_command)
        keys = QAction("All keys…", self)
        keys.triggered.connect(self.show_keys)
        helping.addAction(keys)
        about = QAction(f"tabmancer {__version__}", self)
        about.setEnabled(False)
        helping.addAction(about)

    # --- reading ----------------------------------------------------------------------------------

    def poll(self) -> None:
        """Read the dashboard on a thread of its own (one read at a time): the window never waits on it."""
        if self.waiting:
            return
        self.waiting = True
        self.reader = threading.Thread(target=self.read, args=(self.store,), daemon=True, name="tabmancer-main")
        self.reader.start()

    def read(self, store: DashboardStore) -> None:
        try:
            snap, error = store.poll(), ""
            first = bool(snap.agy_bar) and clis.main_cli() == "agy"  # agy the main CLI: its chain before the limits
        except Exception as caught:  # noqa: BLE001 - said in the window, tried again at the next poll
            snap, first, error = None, False, f"{type(caught).__name__}: {caught}"
        try:
            self.relay.polled.emit(store, snap, first, error)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def settle(self, seconds: float = 10) -> None:
        """Wait for a read under way (on closing, and in tests before what it reads goes)."""
        if self.reader is not None:
            self.reader.join(seconds)

    def take(self, store: DashboardStore, snap: DashSnapshot | None, first: bool, error: str) -> None:
        self.waiting = False
        if error or snap is None:
            self.info.setText(f"Could not read the sessions: {error}")
            self.info.setStyleSheet(f"color:{look.AMBER}; background:transparent;")
            return
        self.snap, self.agy_first = snap, first
        self.show_top()
        self.show_bit()
        self.board.set(snap)
        self.team_panel.set(snap)

    # --- showing ----------------------------------------------------------------------------------

    def show_top(self) -> None:
        snap = self.snap
        if snap is None:
            return
        info, _line = VM.info_words(len(snap.sessions), len(snap.hidden), self.store.days)
        self.info.setText(info)
        self.info.setStyleSheet(f"color:{look.MUTED}; background:transparent;")
        self.info.setToolTip(info)
        self.usage.setText(parts_html(VM.usage_parts(snap.limits, snap.agy_bar.get("parts", []), self.agy_first)))
        self.fit_accounts()

    def fit_accounts(self) -> None:
        """The accounts in the room the rest of the top bar leaves (as the terminal dashboard's: Codex's
        goes first, then Claude Code's plan, then its email is cut in the middle); the info line keeps
        a little room, unless Claude Code's account would not fit at all."""
        snap = self.snap
        if snap is None:
            return
        row = self.topbar.layout()
        margins = row.contentsMargins()
        taken = (sum(part.sizeHint().width() for part in (self.brand, self.pills, self.usage, self.live, self.clock))
                 + row.spacing() * 6 + margins.left() + margins.right())
        cell = max(1, self.account.fontMetrics().horizontalAdvance("0"))
        room = (self.topbar.width() - taken) // cell
        parts: list[tuple[str, str]] = []
        for least in (INFO_LEAST, 0):
            parts, claude_shown = VM.account_parts(snap.accounts, snap.shared_emails, room - least)
            if claude_shown or not any(account.cli == "claude" for account in snap.accounts):
                break
        shown = parts_html(parts)
        if shown != self.account.text():
            self.account.setText(shown)

    def show_bit(self) -> None:
        if self.snap is None:
            words = "Reading your sessions…"
        elif self.snap.acting is not None:
            words = "The terminal dashboard acts: this window watches."
        else:
            words = "This window only watches; no dashboard acts now (tabmancer in a terminal does)."
        self.bit_line.setText(words)

    def tick(self) -> None:
        """The live dot breathes (a slow sine between dim and bright green), the clock goes on."""
        phase = (math.sin((time.monotonic() - self.started) * 2.2) + 1) / 2
        self.live.setText(look.span("● ", mix("#163d1f", look.GREEN, phase)) + look.span("live", look.MUTED))
        now = datetime.now().strftime("%H:%M:%S")
        if self.clock.text() != now:
            self.clock.setText(now)

    def show_keys(self) -> None:
        KeysDialog(self, "Keys · tabmancer", KEYS, HERE, ELSEWHERE).show()

    def open_commands(self) -> None:
        """Ctrl+K: the palette, over every key of the dashboard (the ones done in the terminal dashboard
        greyed, saying so), the teams and the sessions in sight."""
        entries = commands.commands(KEYS, HERE, ELSEWHERE)
        sessions = {}
        if self.snap is not None:
            entries += commands.team_jumps(self.snap.view.teams)
            entries += commands.session_jumps(view for view in self.snap.view.sessions if not view.hidden)
            sessions = self.snap.sessions
        self.command_bar.offer(entries, sessions)

    def run_command(self, command: commands.Command) -> None:
        """What the palette picked: a key runs its menu action; a team or a session picks its card."""
        if command.kind == "key":
            action = self.actions_by_key.get(command.key)
            if action is not None and action.isEnabled():
                action.trigger()
            return
        dock = self.docks["Sessions"]
        dock.show()
        dock.raise_()
        self.board.reveal(VM.TEAM_CARD + command.target if command.kind == "team" else command.target)
        self.board.setFocus()

    def motion_changed(self) -> None:
        """View ▸ Reduce motion picked: the Team panel moves as it says now."""
        self.team_panel.show_team()

    def show_teams(self) -> None:
        """T: the Team panel to the front (back where it was, if it was closed), its tabs to pick with."""
        dock = self.docks["Team"]
        dock.show()
        dock.raise_()
        self.team_panel.tabs.setFocus()

    # --- one window -------------------------------------------------------------------------------

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
        self.fit_accounts()

    def reset_layout(self) -> None:
        self.restoreState(self.default_state, frame.LAYOUT_VERSION)
        for dock in self.docks.values():
            dock.setFloating(False)
            dock.show()

    def closeEvent(self, event) -> None:
        self.door.close()
        self.timer.stop()
        self.ticker.stop()
        frame.save_layout(self, LAYOUT_KEY)
        super().closeEvent(event)


def door() -> str:
    """The name the open dashboard window listens on: one per tabmancer folder (the demo's is one
    of its own), so `tabmancer desktop` again brings it forward."""
    return frame.door("main")


def knock() -> bool:
    """Whether the dashboard window is open already; if it is, it comes to the front."""
    return frame.knock(door())


def run(demo: bool = False) -> int:
    """The window, till it is closed. With demo, over the made-up sessions of `tabmancer --demo`:
    nothing real is read (frame.made_up_world)."""
    look.fonts_for_offscreen()
    app = QApplication.instance() or QApplication(["tabmancer"])
    with made_up_world(demo):
        window = None
        try:
            if knock():  # open already (the command twice): that window comes forward
                return 0
            window = MainWindow(brand="◆ tabmancer demo" if demo else "◆ tabmancer")
            window.show()
            code = app.exec()
            if window.isVisible():  # the app was told to quit, the window not closed: its layout is kept too
                window.close()
            return code
        finally:
            if window is not None:
                window.timer.stop()
                window.ticker.stop()
                window.settle()
