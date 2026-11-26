"""The dashboard in a desktop window: `tabmancer desktop`.

The terminal dashboard in its black, with room: monospace, the accent, panels you can drag, split,
float and close (Panels puts them back), menus that name every key of the dashboard, and the key
bar under the window. The top bar says what the terminal dashboard's says, in the same words
(viewmodel): the sessions listed, who each CLI is signed in as, the plan limits and agy's chain,
a live dot and the clock. The Sessions panel shows the sessions three ways, picked by a switch under
the top bar: the Board (board.py), as cards in lanes by what they want from you; the Table
(table.py), the terminal dashboard's list row for row (its teams unfold under their rows); and the
Live view, the open sessions by what each does (l, as in the terminal, and l again goes back). The
pick is one across them: the row or card picked stays picked when the view changes.

It watches, as a second dashboard does: the dashboard's store (store.DashboardStore) is read on a
thread of its own every two seconds, and nothing here claims the actor lock or records a plan limit or
an account; what happens by itself (a limit lifting) is the acting dashboard's. What a key asks for, it
does as the terminal's keys do. Enter opens the ticked sessions, or the picked one, in new tabs, and
brings an open one's tab to the front (a team's card: its Team panel tab); j goes to the next open
session waiting for you, and m changes how the picked one opens (opening.py, each on a thread of its
own); p opens a closed one with a prompt, in a new tab or in the background, and r renames one (naming.py,
in prompting.py's and rename.py's words); B lists the background sessions, to attach to, stop, remove
and clean up (backgrounds.py, in backgroundwords.py's), and , picks which CLIs are listed and the main one
(clisettings.py, in clis.py's). M opens the picked team's monitor (a member's: its team's) in a window of its own (monitor.py), on
another screen when there is one; open already, here or from the terminal, it comes to the front. Space
and a tick (a click on a row's [ ] too), f pins, x hides (with the keys on the rail, the project; on a
team with members open, x twice closes their tabs, as on /exit), X shows the hidden ones, e and E unfold teams in the Table, Tab moves between the rail and
the sessions, Esc clears what the list is narrowed to. Pins and hides go to marks.json as every
dashboard changes it (marks.change): what another one changed meanwhile stays, and each shows them at
its next read; Bit says what a key did. The panels fill in slice by slice; till then a key not done here is greyed in its
menu and in the key bar, and done in the terminal dashboard (tabmancer). The Team panel (teampanel), a tab beside the list, shows each team's
picture over its conversation; T brings it to the front. Ctrl+K finds any command by a few of its
words (commandbar): every key as the menus name it, and a team, a project or a session by name.

The board narrows as the terminal dashboard's list does (viewmodel.visible): to a project (a click
on the rail, rail.py), to what a card counts (1 2 3, or a click on a lane's heading) and to words (/,
the box over the board: a project, a title, or anything said in a session, as search.py indexes the
conversations, on a thread of its own). The top bar says what it is narrowed to. The rail also holds
each team as a small picture (a click picks its card and its Team panel tab) and the week's tokens.

The Preview panel is the drawer (drawer.py): the picked card as the terminal dashboard's preview says
it, with a button for each key its footer offers there (greyed, saying why, while this window does
not do that key). A right-click on a card or a row picks it and offers the same as a menu, its keys
beside them; the menu key (or Shift+F10) offers it for the one picked.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import (
    __version__,
    actions,
    backgroundwords,
    bitart,
    clis,
    commands,
    engine,
    handoverwords,
    marks,
    prompting,
    rename,
    search,
    teams,
)
from .. import viewmodel as VM
from ..bgjobs import Job
from ..keys import KEYS
from ..palette import mix
from ..sessions import Exit, Session, Status, folder_key
from ..store import DAYS, DashboardStore, DashSnapshot
from . import commandbar, frame, look, monitor, teamgraph
from .backgrounds import OPEN, BackgroundDialog, Jobs
from .board import Board
from .clisettings import CliSettingsDialog
from .closing import CloseDialog
from .drawer import BUTTONS, ROOM, Drawer
from .frame import KeysDialog, made_up_world
from .front import WindowFront
from .monitor import MonitorWindow, sequences
from .naming import NameDialog, PromptDialog, Renamer
from .opening import Opener
from .rail import Rail
from .table import Table, drawer_key
from .teampanel import TeamPanel
from .widgets import KeyBar, bit_pixmap, text_label

POLL_MS = 2000  # as often as the terminal dashboard reads its pid files (LIVE_REFRESH_SECONDS)
TICK_MS = 100  # the live dot breathes and the clock goes on
INFO_LEAST = 20  # characters the info line keeps before the accounts give way
LAYOUT_KEY = "main"  # desktop.json's
ELSEWHERE = "In the terminal dashboard for now: tabmancer"
# The dashboard's keys that work here (KEYS names them all); the rest are the terminal dashboard's.
HERE = {"?", "q", "T", "/", "1", "2", "3", "l", "Space", "a", "e", "E", "x", "X", "f", "Tab", "Esc", "Enter", "m",
        "j", "M", "h", "r", "p", "B", ","}
SAID_MS = 7000  # how long Bit says what a key did before it says who acts again
AFTER_OPEN_MS = 5000  # the sessions read again this soon after tabs opened: they show open by then
CLOSING_MS = 6000  # x again this soon on the same team closes its members' tabs, as in the terminal
MENU_TITLE = 48  # a right-click menu's heading: the title clipped to this
SEVERITY = {"warning": look.AMBER, "error": look.RED}  # how loud what Bit says is (actions.Note's)
MENUS = {"Pick and open": "&Open", "Keep the list tidy": "&Tidy", "Work with a session": "&Session",
         "Views": "&Views", "Workspaces": "&Workspaces", "Settings and the rest": "Se&ttings"}
KEY_BAR = [("Enter", "open"), ("n", "new"), ("T", "teams"), ("h", "hand over"), ("c", "continue"), ("M", "monitor"),
           ("V", "split"), ("/", "filter"), ("A", "accounts"), (commandbar.KEY, "commands"), ("?", "all keys"),
           ("q", "quit")]
# The top bar's tones (viewmodel.usage_parts, account_parts) as colours.
TONES = {"muted": look.MUTED, "text": look.TEXT, "empty": "#2a2a2a", "ok": look.GREEN, "low": look.AMBER,
         "out": look.RED}
PANELS = ("Sessions", "Preview", "Projects", "Bit", "Team")
VIEWS = (("board", "Board"), ("table", "Table"), ("live", "Live"))  # what the Sessions panel shows
HINTS = {
    "board": f"↑ ↓ ← → pick a card · 1 2 3 or a lane's heading narrows it · / finds · {commandbar.KEY} any command",
    "table": f"↑ ↓ pick a row · Enter opens · e or a click on ▸ unfolds a team · 1 2 3 narrows · / finds · {commandbar.KEY} any command",
    "live": f"the open sessions by what each does · ↑ ↓ pick · l again goes back · / finds · {commandbar.KEY} any command",
}
VIEW_TIPS = {"board": "The sessions as cards, by what they want from you",
             "table": "The sessions as the terminal dashboard lists them, its teams under their rows",
             "live": "Only the open sessions and what each is doing (l)"}
FILTER_WORDS = "Find by project, title or anything said  ( / )"


def parts_html(parts: list[tuple[str, str]]) -> str:
    """The top bar's (words, tone) parts as HTML for a QLabel."""
    shown = []
    for words, tone in parts:
        bold, _, name = tone.rpartition(" ")
        colour = TONES.get(name, look.TEXT)
        shown.append(look.span(words, f"bold {colour}" if bold else colour))
    return f'<span style="white-space:pre">{"".join(shown)}</span>'


def watching_store(days: float = DAYS, include_all: bool = False) -> DashboardStore:
    # As the terminal dashboard that watches (another one acts): it asks for the plan limits when it
    # has no fresh reading of the acting dashboard's, and records neither them nor an account. days
    # and include_all as `tabmancer --days N --all` give them.
    return DashboardStore(days=days, include_all=include_all, ask_limits=True)


class Relay(QObject):
    """Brings what the worker thread read back to the window's thread."""

    polled = Signal(object, object, bool, str)  # the store asked, its DashSnapshot, agy first in the bar, an error
    indexed = Signal(object, bool)  # the conversations' search.Index (None: it failed), whether it read more


class Told(QObject):
    """What the demo's stand-ins say would have happened (demo.stand_ins' tell), from any thread."""

    said = Signal(str)


class FilterBox(QLineEdit):
    """The words the board is narrowed to (/): Esc clears them, Enter or ↓ goes back to the board."""

    left = Signal()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.clear()
            self.left.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
            self.left.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, *, store: Callable[[], DashboardStore] = watching_store, poll_ms: int = POLL_MS,
                 start: bool = True, brand: str = "◆ tabmancer", demo: bool = False) -> None:
        super().__init__()
        self.store = store()
        self.demo = demo  # over the made-up sessions: nothing opens, and Bit says what would have
        self.snap: DashSnapshot | None = None
        self.agy_first = False
        self.waiting = False
        self.reader: threading.Thread | None = None
        self.accent = look.accent()
        self.brand_words = brand
        self.relay = Relay()
        self.relay.polled.connect(self.take)
        self.relay.indexed.connect(self.take_index)
        self.opener = Opener(self, update=not demo)  # Enter, j and m: what they keep, and their threads
        self.opener.said.connect(self.say_note)
        self.opener.opened.connect(self.took_open)
        self.opener.prompted.connect(self.prompt_sent)
        self.writing_to: Session | None = None  # p: the session its dialog writes to
        self.front = WindowFront(self, update=not demo)  # what each reading turns up, and h: the terminal's one copy decides
        # Never acts (state.acts stays False). What opened from here is one copy: Enter's and the engine's (h's fresh one).
        self.engine = engine.Engine(self.front, datetime.now, engine.EngineState(openings=self.opener.openings))
        self.view = "board"  # what the Sessions panel shows (VIEWS)
        self.before_live = "board"  # the view l goes back to
        self.project = VM.ALL_PROJECTS  # what the board is narrowed to: a project (the rail),
        self.card: str | None = None  # what a card counts (1 2 3),
        self.words = ""  # and words (/)
        self.selected: set[str] = set()  # the sessions ticked (Space), by id
        self.preselected: set[str] = set()  # of those, the ones ticked at the start: open at the last shutdown
        self.show_hidden = False  # X: the hidden ones listed too
        self.remark: marks.Marks | None = None  # saved while a read was under way: its snapshot gets them too
        self.index: search.Index | None = None  # what was said in each session, read on a thread of its own
        self.finder: search.Finder | None = None
        self.indexing = False
        self.indexer: threading.Thread | None = None
        self.door = QLocalServer(self)  # `tabmancer desktop` again knocks here instead of opening a second window
        self.door.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.door.newConnection.connect(self.answer_door)
        frame.listen(self.door, door())
        self.setStyleSheet(look.sheet(self.accent))
        self.setWindowTitle("tabmancer")
        self.resize(1500, 950)
        self.build()
        self.drawer.modes = self.table.modes = self.opener.modes  # m: what the preview and Open as say
        self.default_state = self.saveState(frame.LAYOUT_VERSION)
        known = frame.restore_layout(self, LAYOUT_KEY)
        frame.place_new(self, known, self.docks["Team"], [self.docks["Sessions"], self.docks["Preview"]],
                        Qt.DockWidgetArea.RightDockWidgetArea)
        self.started = time.monotonic()
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.said_timer = QTimer(self)
        self.said_timer.setSingleShot(True)
        self.said_timer.timeout.connect(self.show_bit)
        self.said_timer.timeout.connect(self.front.said)  # after it: Bit's words that waited their turn
        self.after_open = QTimer(self)
        self.after_open.setSingleShot(True)
        self.after_open.timeout.connect(self.poll)
        self.closing_team: str | None = None  # x said what a second x does to this team's tabs
        self.closing_timer = QTimer(self)
        self.closing_timer.setSingleShot(True)
        self.closing_timer.timeout.connect(self.forget_closing)
        self.opener.closed.connect(self.closed_tabs)
        self.renamer = Renamer(self)  # r on a closed session: /rename run in it on a thread
        self.renamer.done.connect(self.renamed)
        self.renamer.failed.connect(self.rename_failed)
        self.naming: Session | None = None  # r: the session its dialog names
        self.jobs = Jobs(self)  # B: the background sessions read, and stopped or removed, on threads
        self.jobs.read.connect(self.background_read)
        self.listing_background = False  # B pressed, its list not read yet
        self.monitors: dict[str, MonitorWindow] = {}  # M: each team's monitor opened from here
        self.popup: QMenu | None = None  # a card's or a row's menu, while it is shown
        self.leaving = False  # closing anyway: the handovers queued here dropped (CloseDialog), or the app quit
        self.close_after = False  # closed while a handover was written: closes once it is (finish_closing)
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
        self.board.card.connect(self.toggle_card)
        self.board.picked.connect(self.show_pick)
        self.table.picked.connect(self.show_pick)
        self.table.ticking.connect(self.tick_key)
        self.board.menu.connect(self.pick_menu)
        self.table.menu.connect(self.pick_menu)
        self.rail.projects.left.connect(self.back_to_board)
        self.drawer.act.connect(self.run_key)
        self.rail.projects.picked.connect(self.set_project)
        self.rail.teams.picked.connect(self.pick_team)
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
        self.hint = text_label(HINTS["board"], look.DIM, 9)
        row.addWidget(self.hint)
        row.addStretch(1)
        self.filter_box = FilterBox()
        self.filter_box.setObjectName("filter")
        self.filter_box.setFont(look.font(9))
        self.filter_box.setPlaceholderText(FILTER_WORDS)
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.setMinimumWidth(360)
        self.filter_box.textChanged.connect(self.words_changed)
        self.filter_box.left.connect(self.back_to_board)
        row.addWidget(self.filter_box)
        row.addSpacing(12)
        self.views = QButtonGroup(self)
        for at, (name, words) in enumerate(VIEWS):
            button = QPushButton(words)
            button.setObjectName("view")
            button.setFont(look.font(9))
            button.setCheckable(True)
            button.setChecked(name == "board")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(VIEW_TIPS[name])
            self.views.addButton(button, at)
            row.addWidget(button)
        self.views.idClicked.connect(self.view_clicked)
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
            self.table = Table(self.accent)
            self.table.set_motion(teamgraph.motion())
            self.sessions_shown = QStackedWidget()  # the board, or the table (its live view too)
            self.sessions_shown.addWidget(self.board)
            self.sessions_shown.addWidget(self.table)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(self.sessions_shown)
        elif title == "Projects":
            self.rail = Rail(self.accent)
            box.setContentsMargins(6, 0, 0, 0)
            box.addWidget(self.rail)
        elif title == "Preview":
            self.drawer = Drawer(self.accent)
            box.setContentsMargins(12, 10, 6, 0)
            box.addWidget(self.drawer)
        elif title == "Bit":
            line = QHBoxLayout()
            self.bit = QLabel()
            self.bit.setStyleSheet("background:transparent;")
            self.bit.setPixmap(bit_pixmap(bitart.Pose(eyes="happy"), 0.6))
            self.bit_line = text_label("", look.MUTED, 9.5, wrap=True)
            line.addWidget(self.bit, 0, Qt.AlignmentFlag.AlignBottom)
            line.addWidget(self.bit_line, 1, Qt.AlignmentFlag.AlignVCenter)
            box.addLayout(line)
        made.setWidget(inside)
        return made

    def build_menus(self) -> None:
        """A menu a group of the dashboard's keys, every key in it: the ones done here, and the ones
        the terminal dashboard does (greyed, saying so). Panels brings closed panels back."""
        bar = self.menus
        bar.setFont(look.font(9.5))
        self.actions_by_key: dict[str, QAction] = {}
        doing = {"?": self.show_keys, "q": self.close, "T": self.show_teams, "/": self.find_words,
                 "1": self.only_open, "2": self.only_waiting, "3": self.only_continue, "l": self.toggle_live,
                 "Space": self.toggle, "a": self.toggle_all, "e": self.expand, "E": self.fold_teams, "x": self.hide,
                 "X": self.toggle_hidden, "f": self.pin, "Tab": self.switch_focus, "Esc": self.back,
                 "Enter": self.open_key, "m": self.cycle_mode, "j": self.next_waiting, "M": self.monitor_key,
                 "h": self.handover_key, "r": self.rename_key, "p": self.prompt_key,
                 "B": self.background_key, ",": self.settings_key}
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
        for thread in (self.reader, self.indexer):
            if thread is not None:
                thread.join(seconds)
        self.opener.settle(seconds)
        self.renamer.settle(seconds)
        self.jobs.settle(seconds)
        self.front.settle(seconds)
        for shown in self.monitors.values():
            shown.settle(seconds)  # a read of its own (Python's: there after the window is deleted too)

    def take(self, store: DashboardStore, snap: DashSnapshot | None, first: bool, error: str) -> None:
        self.waiting = False
        if error or snap is None:
            self.info.setText(f"Could not read the sessions: {error}")
            self.info.setStyleSheet(f"color:{look.AMBER}; background:transparent;")
            return
        if self.remark is not None:  # read before the window changed the marks: as they are now (the file
            now = marks.Marks.read(tries=1)  # held or unreadable now: as this window saved them; no wait here)
            snap, self.remark = snap.with_marks(now if now is not None else self.remark), None
        starting, self.snap, self.agy_first = self.snap is None, snap, first
        if starting:  # the ones open at the last shutdown come ticked, as in the terminal dashboard
            self.selected = VM.preselect(snap.sessions, snap.open_at_shutdown, lambda s: s.session_id in snap.hidden)
            self.preselected = set(self.selected)
        listed = snap.sessions.keys()
        self.selected, self.preselected = self.selected & listed, self.preselected & listed
        self.front.live(snap)  # what changed is said and lit, one open after all unticked (Engine.live)
        self.show_top()
        if starting and self.preselected:
            self.say(VM.shutdown_hello(len(self.preselected)), 6)
        elif not self.said_timer.isActive():
            self.show_bit()
        came_up = self.opener.seen(snap.sessions)  # opened from here a while ago, and never showed open?
        if came_up is not None:
            self.say_note(came_up.words, came_up.seconds or 4, came_up.severity)
        self.refilter()
        self.team_panel.set(snap)
        if len(self.words.strip()) >= search.SHORTEST:
            self.index_words()  # what the sessions said since

    # --- narrowing --------------------------------------------------------------------------------

    def shown(self) -> list[Session]:
        """The sessions shown: the terminal dashboard's list over the same filters (the open ones alone,
        by what they do, in the live view)."""
        snap = self.snap
        if snap is None:
            return []
        return VM.visible(snap.sessions.values(), lambda s: s.session_id in snap.hidden, show_hidden=self.show_hidden,
                          text=self.words, said_in=self.said_in, project=self.project, card=self.card,
                          live=self.view == "live")

    def said_in(self) -> set[str]:
        if self.finder is None or self.snap is None:
            return set()
        return self.finder.said_in(self.words, self.snap.sessions.keys())

    def narrowed(self) -> bool:
        return self.project != VM.ALL_PROJECTS or self.card is not None or bool(self.words.strip())

    def refilter(self) -> None:
        snap = self.snap
        if snap is None:
            return
        if self.view == "board":
            self.board.set(snap, self.shown(), self.card, self.selected)
        else:
            self.table.set(snap, self.shown(), self.selected)
        self.rail.set(snap, self.project, self.show_hidden)
        self.show_pick(self.picked_now())
        self.show_pills()

    def picked_now(self) -> str:
        """The card or row picked in the view shown, in the drawer's words ('' for none)."""
        if self.view == "board":
            return self.board.pick or ""
        return drawer_key(self.table.pick or "")

    def reveal(self, key: str) -> bool:
        """A session's card or row (or its team's) picked and in view, in the view shown."""
        return self.board.reveal(key) if self.view == "board" else self.table.show_key(key)

    def shown_now(self) -> QWidget:
        return self.board if self.view == "board" else self.table

    def view_clicked(self, at: int) -> None:
        self.set_view(VIEWS[at][0])

    def toggle_live(self) -> None:
        """l: the live view, and l again goes back to the view before it."""
        if self.view == "live":
            self.set_view(self.before_live)
        else:
            self.before_live = self.view
            self.set_view("live")

    def set_view(self, name: str) -> None:
        """What the Sessions panel shows: the board, the table or the live view; the pick goes with it."""
        if name == self.view:
            return
        picked = self.picked_now()
        self.view = name
        self.views.button(next(at for at, (view, _) in enumerate(VIEWS) if view == name)).setChecked(True)
        self.hint.setText(HINTS[name])
        self.table.set_live(name == "live")
        self.sessions_shown.setCurrentWidget(self.shown_now())
        self.refilter()
        if picked:
            self.reveal(picked)
        dock = self.docks["Sessions"]
        dock.show()
        dock.raise_()
        self.shown_now().setFocus()

    def show_pick(self, key: str) -> None:
        """The card picked on the board, in the drawer; the filter's words where they were said in it."""
        found = None
        words = self.words.strip()
        if key and len(words) >= search.SHORTEST and self.index is not None and key in self.said_in():
            found = self.index.snippet(key, words, width=2 * ROOM)
        why = {button: self.why_not(button) for button, _, _ in BUTTONS}
        self.drawer.ticked = frozenset(self.selected)  # what its Enter opens
        self.drawer.set(self.snap, key or None, found, why)

    def why_not(self, key: str) -> str | None:
        """None when this window does the key on what is picked; else why its button is greyed."""
        action = self.actions_by_key.get(key)
        if action is None or not action.isEnabled():
            return ELSEWHERE
        return None

    def run_key(self, key: str) -> None:
        """A drawer's button: the window's own action for its key, as the menu runs it."""
        action = self.actions_by_key.get(key)
        if action is not None and action.isEnabled():
            action.trigger()

    def pick_menu(self, at: QPoint) -> None:
        """A card or a row right-clicked (picked by it), or the menu key: the drawer's buttons for it as a
        menu at this place, each with its key, greyed as there and saying why."""
        offered = self.drawer.offered
        if not offered:
            return
        if self.popup is not None:
            self.popup.close()
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.setToolTipsVisible(True)
        title = menu.addAction(self.pick_name().replace("&", "&&"))
        title.setEnabled(False)
        menu.addSeparator()
        for key, words in offered:
            item = menu.addAction(f"{words.replace('&', '&&')}\t{key}")  # the key right of it, as menus put one
            item.setData(key)
            why = self.why_not(key)
            item.setEnabled(why is None)
            item.setToolTip(why or "")
        menu.triggered.connect(self.menu_chose)
        menu.aboutToHide.connect(self.popup_gone)  # at once, not when it is deleted later
        self.popup = menu
        menu.popup(at)

    def pick_name(self) -> str:
        """The menu's heading: the picked session's title, or the team's name."""
        key, snap = self.picked_now(), self.snap
        if key.startswith(VM.TEAM_CARD):
            return f"Team {key[len(VM.TEAM_CARD):]}"
        session = snap.sessions.get(key) if snap is not None else None
        return actions.clip(session.title if session is not None else key, MENU_TITLE)

    def menu_chose(self, item: QAction) -> None:
        key = item.data()
        if isinstance(key, str):
            self.run_key(key)

    def popup_gone(self) -> None:
        if self.popup is self.sender():  # not a newer one
            self.popup = None

    def show_pills(self) -> None:
        """What the board is narrowed to, in the top bar."""
        parts = []
        if self.project != VM.ALL_PROJECTS:
            name = next((row.name for row in self.rail.projects.rows if row.key == self.project), self.project)
            parts.append(f"in {name}")
        if self.card is not None:
            key, title = VM.CARDS[self.card]
            parts.append(f"only {title} · {key} shows all")
        if self.words.strip():
            parts.append(f"“{self.words.strip()}”")
        if self.selected:
            parts.append(VM.ticked_words(self.selected, self.preselected))
        shown = "  ·  ".join(parts)
        if shown != self.pills.text():
            self.pills.setText(shown)
            self.fit_accounts()

    def set_project(self, key: str) -> None:
        """A click on the rail: only that project's sessions, or every project again."""
        self.project = key or VM.ALL_PROJECTS
        self.refilter()

    def toggle_card(self, card: str) -> None:
        """1 2 3, or a click on a lane's heading: only what that card counts; the same again shows all."""
        self.card = VM.toggle_card(self.card, card)
        self.refilter()

    def only_open(self) -> None:
        self.toggle_card("open")

    def only_waiting(self) -> None:
        self.toggle_card("waiting")

    def only_continue(self) -> None:
        self.toggle_card("continue")

    def find_words(self) -> None:
        """/: the box over the board, its words picked to type over."""
        self.filter_box.setFocus()
        self.filter_box.selectAll()

    def words_changed(self, words: str) -> None:
        self.words = words
        self.refilter()
        if len(words.strip()) >= search.SHORTEST:
            self.index_words()

    def back_to_board(self) -> None:
        self.shown_now().setFocus()

    def back(self) -> None:
        """Esc: the words cleared, else a card's narrowing, else the project's; with nothing to clear, the
        window closes, as Esc quits the terminal dashboard."""
        step = VM.back_step(bool(self.words) or self.filter_box.hasFocus(), self.card)
        if step == "search":
            self.filter_box.clear()
            self.back_to_board()
        elif step == "card" and self.card is not None:
            self.toggle_card(self.card)
        elif self.project != VM.ALL_PROJECTS:
            self.set_project(VM.ALL_PROJECTS)
        else:
            self.close()

    def switch_focus(self) -> None:
        """Tab: the keys to the rail's projects (↑ ↓ narrow to one, x hides it), or back to the sessions."""
        if self.rail.projects.hasFocus():
            self.back_to_board()
            return
        dock = self.docks["Projects"]
        dock.show()
        dock.raise_()
        self.rail.projects.setFocus()

    def clear_filters(self) -> None:
        self.project, self.card = VM.ALL_PROJECTS, None
        self.filter_box.clear()  # its words too, and the board again
        self.refilter()

    def index_words(self) -> None:
        """What was said in each session, read on a thread of its own (only what was added since the last
        time, search.json keeps the rest); the board narrows again when it is read."""
        if self.indexing or self.snap is None:
            return
        self.indexing = True
        self.indexer = threading.Thread(target=self.read_index, args=(self.index, list(self.snap.sessions.values())),
                                        daemon=True, name="tabmancer-search")
        self.indexer.start()

    def read_index(self, index: search.Index | None, sessions: list[Session]) -> None:
        try:
            index = index if index is not None else search.Index.load()
            more = index.update(sessions)
        except Exception:  # noqa: BLE001 - the words are still found in titles and prompts
            index, more = None, False
        try:
            self.relay.indexed.emit(index, more)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def take_index(self, index: search.Index | None, more: bool) -> None:
        self.indexing = False
        if index is None:
            return
        if index is not self.index:
            self.index, self.finder = index, search.Finder(index)
        elif more and self.finder is not None:
            self.finder.renew()
        else:
            return
        if len(self.words.strip()) >= search.SHORTEST:
            self.refilter()

    # --- showing ----------------------------------------------------------------------------------

    def show_top(self) -> None:
        snap = self.snap
        if snap is None:
            return
        info, _line = VM.info_words(len(snap.sessions), len(snap.hidden), self.store.days, show_hidden=self.show_hidden)
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
        self.bit_line.setStyleSheet(f"color:{look.MUTED}; background:transparent;")

    def say(self, words: str, seconds: float = 4, colour: str = look.TEXT) -> None:
        """Bit says what a key just did for a while (longer for longer words), then who acts again."""
        self.bit_line.setText(words)
        self.bit_line.setStyleSheet(f"color:{colour}; background:transparent;")
        self.said_timer.start(max(SAID_MS, int(seconds * 1000)))

    def say_note(self, words: str, seconds: float, severity: str) -> None:
        """What came of a key that reached past the window (opening.py), as loud as it is."""
        self.say(words, seconds, SEVERITY.get(severity, look.TEXT))

    def demo_told(self, words: str) -> None:
        """The demo's word for what would have happened (Told)."""
        self.say(words, 10)

    # --- the keys that tidy the list --------------------------------------------------------------

    def picked_team(self) -> str | None:
        """The team picked (its card, or its row in the Table), by name."""
        key = self.picked_now()
        return key[len(VM.TEAM_CARD):] if key.startswith(VM.TEAM_CARD) else None

    def team_of_pick(self) -> str | None:
        """The team picked, or the team of the member picked (its row in the Table)."""
        team, snap = self.picked_team(), self.snap
        if team is not None or snap is None:
            return team
        key = self.picked_now()
        return next((name for name, state in snap.team_states.items()
                     if any(s is not None and s.session_id == key for s in state.sessions.values())), None)

    def monitor_key(self) -> None:
        """M: the picked team's monitor (a member's: its team's) in a window of its own, on another screen
        when there is one, as the terminal dashboard's M opens it; open already (from here, or from a
        terminal), it comes to the front. It only watches, as this window does."""
        team = self.team_of_pick()
        if team is None:
            self.say("M opens a team's monitor: pick a team, or one of its members.")
            return
        shown = self.monitors.get(team)
        if shown is not None and shown.isVisible():
            shown.come_forward()
            self.say(f"The monitor of {team} is in front.")
            return
        if monitor.knock(team):  # one a terminal opened (tabmancer desktop --monitor): it comes forward
            self.say(f"The monitor of {team} is in front.")
            return
        if shown is None:
            shown = self.monitors[team] = MonitorWindow(team)
            shown.show()
            elsewhere(shown, self)
        else:  # closed since: the same window again, where it was (none is deleted while this one lives)
            shown.reopen()
        self.say(f"The monitor of {team} opened in a window of its own.")

    def chosen(self) -> list[Session]:
        """The ticked sessions, or else the one picked (none for a team's card)."""
        snap = self.snap
        if snap is None:
            return []
        if self.selected:
            return [snap.sessions[sid] for sid in self.selected if sid in snap.sessions]
        session = snap.sessions.get(self.picked_now())
        return [session] if session is not None else []

    def tick_ids(self, key: str) -> set[str]:
        """What Space ticks on a card or row (its key in the drawer's words): the session, or a team's
        members not open, all at once; none for one open in a terminal."""
        snap = self.snap
        if snap is None:
            return set()
        if key.startswith(VM.TEAM_CARD):
            state = snap.team_states.get(key[len(VM.TEAM_CARD):])
            return VM.members_closed(state, snap.sessions) if state is not None else set()
        session = snap.sessions.get(key)
        return {key} if session is not None and VM.tickable(session) else set()

    def flip(self, ids: set[str]) -> None:
        """These ticked, or unticked when they all were."""
        self.selected = self.selected - ids if ids <= self.selected else self.selected | ids
        self.show_ticked()

    def show_ticked(self) -> None:
        if self.view == "board":
            self.board.set_ticked(self.selected)
        else:
            self.table.set_ticked(self.selected)
        self.show_pills()
        self.show_pick(self.picked_now())  # the drawer: what Enter opens now

    def toggle(self) -> None:
        """Space: the picked session ticked or unticked (a closed team's members as one), the pick on down."""
        key = self.picked_now()
        ids = self.tick_ids(key)
        if not ids and not key.startswith(VM.TEAM_CARD):
            return  # open in a terminal, or nothing picked
        if ids:
            self.flip(ids)
        self.shown_now().step(1)

    def tick_key(self, key: str) -> None:
        """A click on a row's [ ]: as Space there, the pick staying."""
        ids = self.tick_ids(key)
        if ids:
            self.flip(ids)

    def toggle_all(self) -> None:
        """a: every session shown ticked, or unticked when they all were; on a team, its new members."""
        if self.picked_team() is not None:
            self.say("Adding members is in the terminal dashboard for now: a on the team's row there.")
            return
        ids = {s.session_id for s in self.shown() if VM.tickable(s)}
        if ids:
            self.flip(ids)

    def expand(self) -> None:
        """e: a team's row in the Table unfolded or folded again; else the picked one in the Preview panel,
        which says how it ended (the terminal shows that under its title)."""
        team = self.picked_team()
        if team is not None and self.view != "board":
            self.table.fold(team)
            return
        dock = self.docks["Preview"]
        dock.show()
        dock.raise_()

    def fold_teams(self) -> None:
        """E: every team in the Table unfolded, or every one folded again."""
        if self.view == "board":
            self.say("The board shows each team as one card: E unfolds them in the Table.")
        elif not self.table.fold_teams():
            self.say("No team in the list to unfold.")

    def toggle_hidden(self) -> None:
        """X: the hidden sessions (and projects) listed too, or not."""
        self.show_hidden = not self.show_hidden
        self.show_top()
        self.refilter()

    def pin(self) -> None:
        """f: the ticked sessions, or the picked one, pinned above the rest; again, unpinned."""
        chosen = self.chosen()
        if chosen:
            self.remarked(marks.change(partial(marks.pin, chosen=chosen)))

    def hide(self) -> None:
        """x: the ticked sessions, or the picked one (a closed team's members), out of sight; one hidden
        already (listed with X), back in sight. With the keys on the rail, its project."""
        snap = self.snap
        if snap is None:
            return
        if self.rail.projects.hasFocus():
            self.hide_project()
            return
        team = self.picked_team() if not self.selected else None
        state = snap.team_states.get(team) if team is not None else None
        if state is not None and VM.members_open(state, snap.sessions):
            self.close_team(state)
            return
        chosen = ([snap.sessions.get(s.session_id, s) for s in state.sessions.values() if s is not None]
                  if state is not None else self.chosen())
        if chosen:
            self.remarked(marks.change(partial(marks.hide, chosen=chosen)))

    def close_team(self, state: teams.TeamState) -> None:
        """x on a team with members open says what x again does; x again on it within CLOSING_MS closes
        every open member's tab, as on /exit (the sessions stay: s starts them), on a thread of its own."""
        snap = self.snap
        if snap is None:
            return
        sessions = self.members_now(state)
        note = actions.closing_note(state.team, sessions)
        if note is None:
            self.say("No member is open.", 3)
            return
        if self.closing_team != state.team.name:
            self.closing_team = state.team.name
            self.closing_timer.start(CLOSING_MS)
            self.say(note, CLOSING_MS / 1000, look.AMBER)
            return
        self.forget_closing()
        self.opener.close_team(actions.open_names(state.team, sessions))

    def members_now(self, state: teams.TeamState) -> dict[str, Session | None]:
        """A team's members, each with its session as the last reading has it (one never started: None)."""
        snap = self.snap
        sessions = snap.sessions if snap is not None else {}
        return {name: sessions.get(s.session_id, s) if s is not None else None for name, s in state.sessions.items()}

    def forget_closing(self) -> None:
        self.closing_timer.stop()
        self.closing_team = None

    def closed_tabs(self) -> None:
        """The members' tabs closed: the sessions read again soon, to show them closed."""
        self.after_open.start(AFTER_OPEN_MS)

    def hide_project(self) -> None:
        """x on the rail: the project picked there out of sight (its open sessions still show), or back."""
        snap, key = self.snap, self.project
        if snap is None or key == VM.ALL_PROJECTS:
            self.say("Pick a project on the rail to hide it, or a session in the list.")
            return
        sessions = [s for s in snap.sessions.values() if folder_key(s.cwd) == key]
        done = marks.change(partial(marks.hide_project, key=key, sessions=sessions))
        if not isinstance(done, marks.Refused) and key in done[0].projects and not self.show_hidden:
            self.project = VM.ALL_PROJECTS  # gone from the rail: every project again
        self.remarked(done)

    def remarked(self, done: tuple[marks.Marks, marks.Changed] | marks.Refused) -> None:
        """The marks as just saved, shown at once (and over a read under way); what was hidden, unticked.
        Refused: marks.json is as it was (it could not be read, or the change written), and Bit says so."""
        if isinstance(done, marks.Refused):
            self.say(done.words, 8, look.AMBER)
            return
        now, changed = done
        self.selected -= changed.hid
        self.remark = now if self.waiting else None
        if self.snap is not None:
            self.snap = self.snap.with_marks(now)
            self.show_top()
            self.refilter()
        self.say(changed.words, changed.seconds)

    def tick(self) -> None:
        """The live dot breathes (a slow sine between dim and bright green), the clock goes on."""
        now = time.monotonic()
        phase = (math.sin((now - self.started) * 2.2) + 1) / 2
        self.live.setText(look.span("● ", mix("#163d1f", look.GREEN, phase)) + look.span("live", look.MUTED))
        clock = datetime.now().strftime("%H:%M:%S")
        if self.clock.text() != clock:
            self.clock.setText(clock)
        if self.view != "board":
            self.table.tick(now)  # its stars turn, its light sweeps, its red breathes

    def show_keys(self) -> None:
        KeysDialog(self, "Keys · tabmancer", KEYS, HERE, ELSEWHERE).show()

    def open_commands(self) -> None:
        """Ctrl+K: the palette, over every key of the dashboard (the ones done in the terminal dashboard
        greyed, saying so), the teams, the projects and the sessions in sight."""
        entries = commands.commands(KEYS, HERE, ELSEWHERE)
        sessions = {}
        if self.snap is not None:
            entries += commands.team_jumps(self.snap.view.teams)
            entries += commands.project_jumps(self.rail.projects.rows[1:])
            entries += commands.session_jumps(view for view in self.snap.view.sessions if not view.hidden)
            sessions = self.snap.sessions
        self.command_bar.offer(entries, sessions)

    def run_command(self, command: commands.Command) -> None:
        """What the palette picked: a key runs its menu action; a project narrows the board to it; a
        team or a session picks its card (the filters cleared if they kept it out)."""
        if command.kind == "key":
            action = self.actions_by_key.get(command.key)
            if action is not None and action.isEnabled():
                action.trigger()
            return
        dock = self.docks["Sessions"]
        dock.show()
        dock.raise_()
        if command.kind == "project":
            self.set_project(command.target)
        else:
            card = VM.TEAM_CARD + command.target if command.kind == "team" else command.target
            if not self.reveal(card) and self.narrowed():
                self.clear_filters()
                self.reveal(card)
        self.shown_now().setFocus()

    def pick_team(self, name: str) -> None:
        """A team on the rail: its card on the board (the filters cleared if they kept it out), and its tab
        in the Team panel, for when that one is brought to the front."""
        self.team_tab(name)
        self.run_command(commands.Command("team", name, target=name))

    def team_tab(self, name: str) -> None:
        """The team's tab picked in the Team panel."""
        if name in self.team_panel.names:
            self.team_panel.tabs.setCurrentIndex(self.team_panel.names.index(name))  # its pick_team

    def motion_changed(self) -> None:
        """View ▸ Reduce motion picked: the Team panel, the rail's teams and the table move as it says now."""
        self.team_panel.show_team()
        self.rail.teams.set_motion(teamgraph.motion())
        self.table.set_motion(teamgraph.motion())

    def show_teams(self) -> None:
        """T: the Team panel to the front (back where it was, if it was closed), its tabs to pick with."""
        dock = self.docks["Team"]
        dock.show()
        dock.raise_()
        self.team_panel.tabs.setFocus()

    # --- the keys that reach past the window -------------------------------------------------------

    def open_key(self) -> None:
        """Enter: from the words' box or the rail, back to the sessions. Else the ticked sessions open, or
        the picked one: an open one's tab comes to the front, and a team's card brings its Team panel tab
        (as Enter on its row opens the team's view in the terminal)."""
        if self.filter_box.hasFocus() or self.rail.projects.hasFocus():
            self.back_to_board()
            return
        snap = self.snap
        if snap is None:
            return
        team = self.picked_team()
        if team is not None and not self.selected:
            self.team_tab(team)
            self.show_teams()
            return
        chosen = self.chosen()
        if not chosen:
            self.say("Pick a session first, or tick some with Space.")
        elif not self.selected and chosen[0].status is Status.RUNNING:
            self.opener.go_to(chosen[0])
        else:
            self.opener.open(chosen, snap)

    def took_open(self, opened) -> None:
        """What an Enter came to (actions.Opened): the ones opened, or open already, unticked; said (the
        demo's tell says it there); with no terminal that opens tabs, the commands on the clipboard. Read
        again soon: the new ones show open."""
        self.selected -= opened.opened_ids | opened.skipped_ids
        self.show_ticked()
        if opened.leftover:
            QApplication.clipboard().setText("\n".join(opened.leftover))
        note = self.opener.took(opened)
        if note is not None and not (self.demo and opened.opened):
            self.say_note(note.words, note.seconds or 4, note.severity)
        if opened.opened or opened.skipped:
            self.after_open.start(AFTER_OPEN_MS)

    def cycle_mode(self) -> None:
        """m: how the picked session opens next (resume, auto-continue, fork, new), as its preview and the
        Table's Open as column say it."""
        snap = self.snap
        session = snap.sessions.get(self.picked_now()) if snap is not None else None
        if session is None or not self.opener.cycle(session):
            self.say("m changes how a closed session opens: pick one.")
            return
        self.refilter()

    def handover_key(self) -> None:
        """h: the picked session handed over to a fresh one that starts from a full handover, as the terminal
        dashboard's h does it (Engine.handover: the checks, the dialog, a busy one queued till it finishes,
        the handover written on a thread). h again on one queued from here: not after all. On a team: the
        whole team, twice (Engine.team_handover: its lead writes the handover and the line-up on a thread,
        the team handover dialog shows them, Ctrl+S there starts the new generation); a handover written
        and not taken yet is shown again."""
        snap = self.snap
        if snap is None:
            return
        team = self.picked_team()
        if team is not None:
            state = snap.team_states.get(team)
            if state is not None:
                self.engine.team_handover(state.team, self.members_now(state))
            return
        session = snap.sessions.get(self.picked_now())
        if session is None:
            self.say("h hands a session over to a fresh one: pick one.")
            return
        self.engine.handover(session)

    def rename_key(self) -> None:
        """r: the picked session renamed, as the terminal dashboard's r does it and in its words (rename.py):
        refused for another CLI, a team's member or a background session; else its name asked for."""
        snap = self.snap
        session = snap.sessions.get(self.picked_now()) if snap is not None else None
        if session is None:
            self.say("r renames a session: pick one.")
            return
        refused = rename.refusal(session)
        if refused:
            self.say_note(refused, 6, "warning")
            return
        self.naming = session
        asked = NameDialog(self, rename.heading(session), session.title, rename.PLACEHOLDER,
                           (("Enter", rename.enter_words(session)), ("Esc", "cancel")), self.accent,
                           rename.OPEN_WORDS if session.exit is Exit.RUNNING else "")
        asked.named.connect(self.rename_to)
        asked.open()

    def rename_to(self, name: str) -> None:
        """The name given: an open session renames itself (renamed from outside, its next turn would write the
        old name back), so /rename goes on the clipboard and its tab comes forward, to paste; a closed one is
        renamed on a thread (Renamer). As the list has it now: it may have opened while the dialog stood."""
        session, self.naming = self.naming, None
        if session is None or name == session.title:
            return
        snap = self.snap
        session = snap.sessions.get(session.session_id, session) if snap is not None else session
        if session.exit is Exit.RUNNING:
            told = rename.open_told(session, name)
            QApplication.clipboard().setText(told.clipboard)
            self.opener.go_to(session)
            self.say_note(told.words, float(told.seconds or 4), told.severity)
            return
        self.renamer.rename(session, name)

    def renamed(self, session: Session) -> None:
        """Renamed and read again: said, and in the next reading (no poll reads a closed session again)."""
        note = rename.renamed_note(session.title)
        self.say_note(note.words, float(note.seconds or 4), note.severity)
        self.store.take(session)
        self.poll()

    def rename_failed(self, note: actions.Note) -> None:
        self.say_note(note.words, float(note.seconds or 4), note.severity)

    def prompt_key(self) -> None:
        """p: a prompt for the picked session, as the terminal dashboard's p asks for it (prompting.py): an open
        one's tab comes to the front instead (nothing types into it from outside), one in the background is
        attached to, not prompted; a closed one opens with it, in a new tab (Enter) or the background (Ctrl+B)."""
        snap = self.snap
        session = snap.sessions.get(self.picked_now()) if snap is not None else None
        if session is None:
            self.say("p writes to a session: pick one.")
            return
        if session.status is Status.RUNNING:
            self.opener.go_to(session)
            return
        refused = prompting.refusal(session)
        if refused:
            self.say_note(refused, 6, "warning")
            return
        self.writing_to = session
        asked = PromptDialog(self, session, self.accent)
        asked.sent.connect(self.prompt_to)
        asked.open()

    def prompt_to(self, how: str, prompt: str) -> None:
        """The prompt given: sent on a thread (Opener.write), the session as the list has it now (it may have
        opened while the dialog stood: then nothing opens, said)."""
        session, self.writing_to = self.writing_to, None
        snap = self.snap
        if session is None or snap is None:
            return
        self.opener.write(snap.sessions.get(session.session_id, session), how, prompt, snap.background)

    def prompt_sent(self, sent: prompting.Sent) -> None:
        """What a p came to (prompting.Sent): said (the demo's tell says what would have opened), with no
        terminal that opens tabs the command on the clipboard; read again, soon where a tab opened or a
        background session started."""
        if sent.leftover:
            QApplication.clipboard().setText("\n".join(sent.leftover))
        if not (self.demo and sent.outcome == "opened"):
            self.say_note(sent.note.words, float(sent.note.seconds or 4), sent.note.severity)
        if sent.outcome == "elsewhere":
            self.poll()
        elif sent.outcome in ("opened", "started"):
            self.after_open.start(AFTER_OPEN_MS)

    def background_key(self) -> None:
        """B: the background sessions listed (BackgroundDialog), as the terminal dashboard's B lists them, read
        on a thread first (`claude agents --json`)."""
        if not self.listing_background:
            self.listing_background = True
            self.jobs.fetch(OPEN)

    def background_read(self, why: str, found: list[Job] | None) -> None:
        if why != OPEN:
            return  # the list shown, reading itself again
        self.listing_background = False
        if found is None:
            self.say_note(backgroundwords.NO_ANSWER, 8, "warning")
            return
        listed = BackgroundDialog(self, found, self.jobs, self.accent)
        listed.attach.connect(self.attach_job)
        listed.changed.connect(self.poll)  # stopped or removed: shown so at once
        listed.open()

    def attach_job(self, job: Job) -> None:
        """Enter on a background session: a tab attached to it (`claude attach`), opened as any other."""
        self.engine.open([backgroundwords.attach_launch(job)])

    def settings_key(self) -> None:
        """,: which CLIs are listed and the main one (CliSettingsDialog), as the terminal dashboard's ,
        picks them."""
        asked = CliSettingsDialog(self, self.accent)
        asked.saved.connect(self.settings_saved)
        asked.open()

    def settings_saved(self, saved: clis.Saved) -> None:
        """Saved: other CLIs listed now are said, and the list is read again with them (the terminal
        dashboard's reload_sessions); config.json there but unreadable, that nothing was saved."""
        if saved.words:
            self.say_note(saved.words, 4 if saved.kept else 8, "" if saved.kept else "warning")
        if saved.changed:
            self.store.relist()
            self.poll()

    def next_waiting(self) -> None:
        """j: the next open session that needs you, else the next one waiting for you: picked in the list
        (on the board, its team's card for a member) and its tab brought to the front."""
        snap = self.snap
        if snap is None:
            return
        session = self.opener.next_waiting(snap.sessions.values())
        if session is not None and not self.reveal(session.session_id) and self.narrowed():
            self.clear_filters()  # narrowed away: shown again
            self.reveal(session.session_id)

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
        """Handovers queued here go with the window, so it asks first (CloseDialog). One being written is never
        cut short (a session closed to write its own would lose it): the window says so and stays till it is
        written and its fresh session opened (finish_closing)."""
        if self.engine.state.queued and not self.leaving:
            event.ignore()
            self.ask_to_close()
            return
        if self.front.handing_now() or (self.close_after and self.engine.state.updating_claude):
            event.ignore()
            self.close_after = True
            if handing := self.front.handing_now():
                note = handoverwords.finishing_note(handing)
                self.say_note(note.words, float(note.seconds or 4), note.severity)
            return
        self.door.close()
        self.timer.stop()
        self.ticker.stop()
        self.closing_timer.stop()
        if self.popup is not None:
            self.popup.close()
        for shown in self.monitors.values():  # the monitors M opened go with the window
            shown.close()
            shown.deleteLater()
        frame.save_layout(self, LAYOUT_KEY)
        super().closeEvent(event)

    def ask_to_close(self) -> None:
        """The handovers queued here, by name: closed anyway (accepted), they are dropped; else nothing changes."""
        if any(asked.isVisible() for asked in self.findChildren(CloseDialog)):
            return  # asked already
        sessions = self.engine.state.sessions
        names = [self.front.who(sessions[sid]) if sid in sessions else sid[:8] for sid in self.engine.state.queued]
        asked = CloseDialog(self, names, self.accent)
        asked.accepted.connect(self.close_anyway)
        asked.open()

    def close_anyway(self) -> None:
        """The handovers queued here dropped, the other dashboards told (they showed them queued), and closed."""
        self.leaving = True
        self.engine.state.queued.clear()
        actions.save_queued({})
        self.close()

    def finish_closing(self) -> None:
        """A handover's thread over, or a claude update done: a window closed meanwhile closes now, unless
        another is still written, or the fresh session still waits for the update (closeEvent decides)."""
        if self.close_after:
            self.close()


def elsewhere(shown: QMainWindow, beside: QMainWindow) -> None:
    """A window on another screen than beside's when there is one and it opened on the same (a monitor
    placed on another screen before stays where it was put)."""
    here = beside.screen()
    other = next((screen for screen in QGuiApplication.screens() if screen is not here), None)
    if other is None or shown.screen() is not here:
        return
    room = other.availableGeometry()
    shown.move(room.left() + max(0, (room.width() - shown.width()) // 2),
               room.top() + max(0, (room.height() - shown.height()) // 2))


def door() -> str:
    """The name the open dashboard window listens on: one per tabmancer folder (the demo's is one
    of its own), so `tabmancer desktop` again brings it forward."""
    return frame.door("main")


def knock() -> bool:
    """Whether the dashboard window is open already; if it is, it comes to the front."""
    return frame.knock(door())


def run(demo: bool = False, days: float = DAYS, include_all: bool = False) -> int:
    """The window, till it is closed. With demo, over the made-up sessions of `tabmancer --demo`:
    nothing real is read (frame.made_up_world). days and include_all: the sessions it lists, as
    `tabmancer --days N --all` (the last days; sessions started by tools too)."""
    look.fonts_for_offscreen()
    app = QApplication.instance() or QApplication(["tabmancer"])
    told = Told()  # the demo's stand-ins say what would have happened, from any thread
    with made_up_world(demo, told.said.emit):
        window = None
        try:
            if knock():  # open already (the command twice): that window comes forward
                return 0
            window = MainWindow(store=partial(watching_store, days, include_all),
                                brand="◆ tabmancer demo" if demo else "◆ tabmancer", demo=demo)
            told.said.connect(window.demo_told)
            window.show()
            code = app.exec()
            if window.isVisible():  # the app was told to quit, the window not closed: its layout is kept too
                window.leaving = True  # nothing can be asked now; a handover being written: settle waits for it
                window.close()
            return code
        finally:
            if window is not None:
                window.timer.stop()
                window.ticker.stop()
                window.settle()
