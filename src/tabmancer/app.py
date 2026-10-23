"""The dashboard: see every recent session, choose which to reopen, and how."""

from __future__ import annotations

import math
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Label, OptionList, Sparkline, Static
from textual.widgets.option_list import Option

from . import (
    background,
    clis,
    context,
    focus,
    gitstate,
    handover,
    marks,
    newsessions,
    rename,
    search,
    summarize,
    teams,
    teamtree,
    toast,
    tokens,
    typein,
    usage,
    workspaces,
)
from . import viewmodel as VM
from .clisettings import CliSettings
from .config import read_json, write_json
from .dialogs import frame, title
from .handoverview import HandoverDialog, fill
from .keysview import KEYS, KeysScreen
from .launch import Launch, Mode, continue_prompt, default_mode, open_tabs, run_in_background
from .mascot import Mascot
from .sessions import (
    Exit,
    Session,
    Status,
    _registry_sessions,
    find_sessions,
    live_sessions,
    mark_live,
    other_sessions,
    read_session,
    transcript_path,
)
from .shutdowns import open_at_shutdown, recent_shutdowns
from .statsview import StatsScreen, copy_text
from .teamview import (
    ADD_EXAMPLE,
    ADD_QUESTION,
    LineDialog,
    NewTeamDialog,
    TeamScreen,
    TeamsDialog,
    close_tabs,
    closing_note,
    open_names,
)
from .teamview import colours as team_colours
from .theme import (
    AMBER,
    BLUE,
    CYAN,
    DEFAULT_THEME,
    GREEN,
    MUTED,
    RED,
    SUNKEN,
    TEXT,
    THEMES,
    keys,
    mix,
    use_saved_theme,
)
from .tokens import duration, human
from .widgets import (
    ALL_PROJECTS,
    INTERRUPTED,
    HourBars,
    KeysFooter,
    ProjectList,
    StatCard,
    TopBar,
    folder_key,
    inline,
    tally,
)

# The view model's tones in this terminal's colours: quiet for what needs nothing from you, loud for
# what does. Amber is what TO CONTINUE counts, red what NEED YOU counts (viewmodel says it all).
TONE = {"your-turn!": f"bold {RED}", "your-turn": RED, "working": BLUE, "open": GREEN, "aside": CYAN,
        "to-continue!": f"bold {AMBER}", "to-continue": AMBER, "done": MUTED, "plain": ""}
STATUS_LABEL = {status: (label, TONE[tone]) for status, (label, tone) in VM.STATUS.items()}
STATUS_HELP, EXIT_HELP, TOOLTIP_STATUS = VM.STATUS_HELP, VM.EXIT_HELP, VM.OUTCOME
MODES = [Mode.RESUME, Mode.AUTO, Mode.FORK, Mode.NEW]
MODE_LABEL = {Mode.RESUME: "resume", Mode.AUTO: "▶ auto", Mode.FORK: "fork", Mode.NEW: "new",
              Mode.CONTINUE: "continue"}
# Raw states from `claude agents --json` -> label, style, sort rank (lowest first).
LIVE_STATES = {raw: (label, TONE[tone], rank) for raw, (label, tone, rank, _) in VM.LIVE.items()}
SPINNING = {"busy", "working"}
TEAM_GLYPHS = {0: ("!", f"bold {RED}"), 1: ("●", RED), 2: ("✶", BLUE), 3: ("◐", AMBER)}  # a team's row, by rank
TEAM_MESSAGES_FROM = 150  # columns from which a team's preview lists its messages beside the tree
# The sidebar's marks, said once under its projects (two lines of the sidebar's width).
LEGEND = Text.assemble(("● ", RED), ("your turn  ", MUTED), ("● ", GREEN), ("open\n", MUTED),
                       ("▲ ", AMBER), ("to continue  ", MUTED), ("± ", AMBER), ("↑ ", CYAN), ("git", MUTED))
BADGE = 0.22  # a loud state's background: its own colour this much over black


def badge(style: str) -> str:
    """A loud state (a bold style) as a badge: its colour tinted in behind it. Quiet ones stay plain."""
    if not style.startswith("bold "):
        return style
    return f"{style} on {mix('#000000', style.split()[-1], BADGE)}"
LIVE_REFRESH_SECONDS = 2  # quick poll of the pid files
FULL_POLL_EVERY = 8        # every 8th poll asks `claude agents --json` (background sessions too)
SPIN_SECONDS = 0.08
USAGE_SECONDS = 300  # plan limits change slowly; `claude -p /usage` is cheap but not free
TOKENS_SECONDS = 60  # token counts: only what transcripts gained since the last count is read
GIT_SECONDS = 60  # where each project's folder stands in git (git status: local, no network)
CHART_DAYS = 14
GROW_SECONDS = 0.8  # the token chart rises on load
FLASH_SECONDS = 1.6  # how long a row stays lit after its state changes
OPENING_SECONDS = 30  # a session just opened is not opened again while it starts up
FOCUS_CLICK = 0.5  # seconds after the window takes the focus in which a click only brought it to the front
QUIET_SECONDS = 5 * 60
PICKER_ONLY = {"toggle", "cycle_mode", "toggle_all", "save_workspace", "open", "hide", "toggle_hidden"}
SHIMMER = "#e8f3ff"  # the bright band that sweeps across "working"
STAR = "·✢✶✻✽✻✶✢"   # Claude Code's turning star; none of these are drawn as emoji
EXPAND_WIDTH = 64   # the Title column at its widest: the outcome wraps inside it
TITLE_MIN = 18      # ...and at its narrowest
# Every column but the title at its widest (an 18-character project name), with the title's padding
# and the scrollbar: the title gets the rest. tests/test_layout.py holds these to it.
OTHER_COLUMNS = {"picker": 72, "live": 84}
HEADING = "\x00"  # the keys of the rows that head the open sessions and the rest
TEAM_ROW = "\x01"  # ...and of the row that heads a team in OPEN NOW, which the cursor does stop on
CONTEXT_BAR = 4     # cells in the bar that shows how full a context is
SIDEBAR = 26 + 3    # the project sidebar with its margins; it steps aside when the table would not fit
MINI_BELOW = 40     # rows of terminal under which the cards take two lines each, not seven
KEY_GROUPS = {action: group for group, rows in KEYS for _, action, _ in rows if action}  # the ? screen's
# 1 2 3 or a click on a card: the list shows only what it counts. Its key, its card, its title.
CARDS = {"open": ("1", "card-open", "OPEN NOW"), "waiting": ("2", "card-waiting", "NEED YOU"),
         "continue": ("3", "card-continue", "TO CONTINUE")}
EXPAND_LINES = 4
LIMIT_MARGIN = timedelta(minutes=1)  # continue this long after a limit lifts, to be sure it has


at_limit, lifts, limit_help, ago = VM.at_limit, VM.lifts, VM.limit_help, VM.ago


def live_label(raw: str) -> tuple[str, str, int]:
    label, tone, rank = VM.live_label(raw)
    return label, TONE[tone], rank


def state_of(session: Session) -> tuple[str, str, int]:
    """What an open session is doing: (label, style, rank)."""
    label, tone, rank = VM.state_of(session)
    return label, TONE[tone], rank


def limit_cell(session: Session) -> Text:
    """'◐ limit · 16:00' until the limit lifts, then '◐ limit lifted'."""
    words, tone = VM.limit_words(session)
    return Text(words, style=TONE[tone])


def by_project(sessions: list[Session], style: str) -> list[tuple[str, str]]:
    """'web-app ×2', 'api-server': the projects of these sessions, the busiest first."""
    return [(name, style) for name in VM.by_project(sessions)]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class NamePrompt(ModalScreen[str | None]):
    DEFAULT_CSS = frame("NamePrompt", "small") + "NamePrompt Input { margin-top: 1; }"
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, value: str = "", placeholder: str = "e.g. PCB work",
                 hint: Text | None = None) -> None:
        super().__init__()
        self.question, self.value, self.placeholder = question, value, placeholder
        self.hint = hint if hint is not None else keys(("Enter", "save"), ("Esc", "cancel"))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title(self.question))
            yield Input(value=self.value, placeholder=self.placeholder)
            yield Label(self.hint, classes="hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PromptDialog(ModalScreen[tuple[str, str] | None]):
    """Write to a session: Enter opens it in a tab with the prompt, Ctrl+B runs it in the background."""

    DEFAULT_CSS = frame("PromptDialog") + "PromptDialog Input { margin-top: 1; }"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+b", "background", "Run in background"),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Prompt", (self.session.project, f"bold {CYAN}"), _clip(self.session.title, 50)))
            yield Input(placeholder="What should Claude do next?")
            yield Label(keys(("Enter", "open in a new tab"), ("Ctrl+B", "run in the background"), ("Esc", "cancel")),
                        classes="hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.dismiss(("tab", event.value.strip()))

    def action_background(self) -> None:
        text = self.query_one(Input).value.strip()
        if text:
            self.dismiss(("background", text))

    def action_cancel(self) -> None:
        self.dismiss(None)


class LimitDialog(ModalScreen[str | None]):
    """c: what becomes of the sessions stopped at a usage limit. Now, for when you have signed in
    with another account (or the limit has lifted); when the limit lifts; or no longer by themselves."""

    DEFAULT_CSS = frame("LimitDialog") + """
    LimitDialog OptionList { height: auto; margin-top: 1; background: $surface; border: none; }
    LimitDialog .plan { color: $text-muted; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, count: int, when: str, armed: bool, plan: str, room: bool) -> None:
        super().__init__()
        self.count, self.when, self.armed, self.plan, self.room = count, when, armed, plan, room

    def compose(self) -> ComposeResult:
        them = "it" if self.count == 1 else "them"
        now = Option(Text.assemble(
            (f"Continue {them} now", f"bold {TEXT}"), ("  you signed in with another account, or the limit has lifted\n", MUTED),
            ("  an open one gets the prompt in its tab, a closed one opens in a new tab", MUTED)), id="now")
        later = Option(Text.assemble(
            (f"Continue {them} when the limit lifts", f"bold {TEXT}"), (f"  {self.when}\n", MUTED),
            ("  leave the dashboard open; each goes on by itself then", MUTED)), id="lift")
        options = [now, later] if self.room else [later, now]
        if self.armed:
            options.append(Option(Text.assemble((f"Do not continue {them} by themselves", f"bold {TEXT}"),
                                                ("  they wait for you", MUTED)), id="off"))
        sessions_ = f"{self.count} session{'' if self.count == 1 else 's'}"
        with Vertical():
            yield Label(title("Stopped at a usage limit", sessions_, f"it lifts {self.when}"))
            if self.plan:
                yield Label(self.plan, classes="plan")
            yield OptionList(*options)
            yield Label(keys(("Enter", "choose"), ("↑↓", "the other ways"), ("Esc", "cancel")), classes="hint")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


def home_short(path: str) -> str:
    """A folder with the home folder written ~, so it fits beside the preview."""
    home = str(Path.home())
    return "~" + path[len(home):] if path and path.lower().startswith(home.lower()) else path


class SessionTable(DataTable):
    """A DataTable where clicking a row opens it up to show how that session ended,
    and clicking its [ ] box ticks it."""

    def on_click(self, event: events.Click) -> None:
        # The row comes from the click itself. hover_coordinate is only as fresh as the
        # last mouse move the terminal reported: none comes while the window is in the
        # background, and none for the empty space under the rows.
        meta = event.style.meta
        row = meta.get("row", -1)
        if not isinstance(self.app, SessionPicker) or not 0 <= row < self.row_count:
            return
        session_id = self.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        if session_id.startswith(TEAM_ROW):  # a team unfolds from its row, and back; a closed one's box ticks it
            name = session_id.removeprefix(TEAM_ROW)
            if meta.get("column") == 0 and not meta.get("out_of_bounds") and not self.app.team_open(name):
                self.app.toggle_team_selected(name)
            else:
                self.app.toggle_folded(name)
            return
        if session_id not in self.app.sessions:  # a heading
            return
        # The space past the last column also reports column 0, but out of bounds.
        if meta.get("column") == 0 and not meta.get("out_of_bounds") and not self.app.live_mode:
            self.app.toggle_selected(session_id)
        else:
            self.app.toggle_expanded(session_id)


class WorkspaceList(ModalScreen[str | None]):
    DEFAULT_CSS = frame("WorkspaceList", "small") + "WorkspaceList OptionList { margin-top: 1; }"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Close"),
        Binding("d,delete", "delete", "Delete"),
    ]

    def __init__(self, saved: dict[str, workspaces.Workspace]) -> None:
        super().__init__()
        self.saved = saved

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Workspaces"))
            yield OptionList(*[Option(Text.assemble((w.name, "bold"), "   ", (w.summary, "dim")), id=w.name)
                               for w in self.saved.values()])
            yield Label(keys(("Enter", "open"), ("d", "delete"), ("Esc", "close")), classes="hint")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_delete(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return
        name = options.get_option_at_index(options.highlighted).id
        saved = workspaces.load()
        saved.pop(name, None)
        workspaces.save(saved)
        options.remove_option(name)
        self.notify(f"Deleted workspace '{name}'.")
        if options.option_count == 0:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionPicker(App[list[str]]):
    """The dashboard. It stays open while sessions open in tabs beside it; it exits with
    shell commands only when no supported terminal can open the tabs itself."""

    TITLE = "tabmancer"
    CSS = """
    Screen { background: $background; }
    ModalScreen { background: $background 65%; }  /* dialogs: the dashboard stays visible, dimmed */
    /* Long lines in dialogs wrap instead of being cut off; labels side by side keep their widths. */
    ModalScreen Vertical > Label { width: 100%; }
    #cards { height: 5; margin: 1 1 1 1; }
    #body { height: 1fr; margin: 0 1; opacity: 0; }
    #sidebar { width: 26; margin-right: 1; background: $sunken; }
    #sidebar > .heading { color: $text-muted; text-style: bold; padding: 1 2 0 2; }
    #sidebar > .legend { height: 2; padding: 0 2; }
    #projects { height: 1fr; background: $sunken; }
    #projects:focus { background: $sunken; }
    #activity { height: 4; margin: 0 2 1 2; }
    #activity > .sparkline--max-color { color: $accent; }
    #activity > .sparkline--min-color { color: $panel-lighten-3; }
    #main { width: 1fr; }
    #filter { display: none; border: tall $accent; background: $surface; }
    #filter.visible { display: block; }
    #table { height: 1fr; background: $background; border: none; }
    #table > .datatable--header { background: $background; color: #6e7681; text-style: bold; }
    /* A fixed height, whatever the preview holds: sized to each one, its top edge (and Bit
       on it) jumped as the cursor moved through the list. */
    #inspector { height: 45%; max-height: 18; margin-top: 1; background: $surface; padding: 1 2 0 2; }
    #detail { width: 1fr; height: auto; padding: 0 1 0 0; }
    #meta { width: 46; height: auto; padding-left: 2; border-left: solid #262626; }
    .no-sidebar #sidebar { display: none; }
    .narrow #meta { display: none; }
    #inspector.team-only #meta { display: none; }
    /* A short screen: the cards in two lines each, so the list keeps its room; shorter still, Bit
       steps aside too. */
    .mini #cards { height: 2; margin: 1 1 0 1; }
    .mini StatCard { height: 2; }
    .mini StatCard > .title, .mini StatCard > Horizontal, .mini StatCard > .caption { display: none; }
    .mini StatCard > .head, .mini StatCard > .line { display: block; }
    .mini #body { margin-top: 1; }
    .short #mascot { display: none; }
    #mascot.hidden { display: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        # The footer shows them in the ? screen's groups (footer_group), each a little apart.
        Binding("question_mark", "keys", "Keys"),  # first, so a narrow footer still shows where the rest are
        Binding("enter", "open", "Open", priority=True),
        Binding("space", "toggle", "Select"),
        Binding("m", "cycle_mode", "Mode"),
        Binding("1", "card('open')", "Cards", key_display="1·2·3"),  # one place in the footer for the three
        Binding("2", "card('waiting')", "Need you", show=False),
        Binding("3", "card('continue')", "To continue", show=False),
        Binding("slash", "filter", "Filter"),
        # Kept out of the footer so the rest fits; ? lists every key.
        Binding("a", "toggle_all", "All/none", show=False),
        Binding("e", "expand", "Expand", show=False),
        Binding("p", "prompt", "Prompt"),
        Binding("j", "next_waiting", "Waiting"),
        Binding("s", "summarize", "Summary"),
        Binding("h", "handover", "Hand over"),
        Binding("c", "continue_at_reset", "Continue"),  # in the footer while a session is at a limit
        Binding("r", "rename", "Rename"),
        Binding("n", "new_sessions", "New"),
        Binding("B", "background", "Background"),
        Binding("x", "hide", "Hide"),
        Binding("f", "pin", "Pin"),
        Binding("l", "toggle_live", "Live"),
        Binding("u", "tokens", "Stats"),
        Binding("T", "teams", "Teams"),
        Binding("w", "save_workspace", "Save workspace", show=False),
        Binding("o", "open_workspace", "Workspaces", show=False),
        Binding("X", "toggle_hidden", "Show hidden", show=False),  # the status line says it when any are
        Binding("comma", "settings", "CLIs", show=False),
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("b", "toggle_mascot", "Bit", show=False),
        Binding("escape", "back", "Quit", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, days: float = 14, include_all: bool = False, live: bool = False) -> None:
        super().__init__()
        self.start_live = live
        self.live_mode = False
        self.days = days
        self.include_all = include_all
        self.sessions: dict[str, Session] = {}
        self.selected: set[str] = set()
        self.opening: dict[str, float] = {}  # session id -> when it was opened from here
        self.modes: dict[str, Mode] = {}
        self.preselected: set[str] = set()  # ticked automatically: open at the last shutdown
        self.filter_text = ""
        self.summaries: dict[str, dict] = {}
        self.summarizing: set[str] = set()
        self.project_filter = ALL_PROJECTS
        self.expanded: set[str] = set()  # rows opened to show how they ended
        self.title_width = EXPAND_WIDTH
        self.frame = 0  # spinner frame
        self.polls = 0
        self.background_live: dict[str, dict] = {}  # from the last full poll
        self.token_report: tokens.Report | None = None
        self.told_idle = False  # Bit points out idle background sessions once
        self.told_crowded: set[str] = set()  # sessions already said to be crowded
        self.last_row = 0  # where the cursor was, so it steps over a heading the same way
        self.waiting_turn = 0  # j goes round the sessions waiting for you
        self.armed: dict[str, datetime] = {}  # c: session id -> when it continues (its limit lifts)
        self.told_limit: set[str] = set()  # sessions Bit has said are stopped at a limit
        self.limited: set[str] = set()  # the sessions at a limit when the footer last heard of it
        self.search = search.Index.load()  # what was said in each session, for the filter
        self.search_version = 0  # goes up when the index has read more
        self.found: tuple[str, int, set[str]] = ("", -1, set())  # filter, version, sessions
        self.queued: dict[str, str] = {}  # h on a busy session: id -> how, handed over once it finishes
        self.full_poll_due = False  # a full live poll is on its way: quick ones wait (poll_live)
        self.marks = marks.Marks.load()  # hidden and pinned sessions
        self.git: dict[str, gitstate.GitState] = {}  # folder key -> where it stands in git
        self.show_hidden = False  # X: the hidden ones are listed too
        self.session_tokens: dict[str, tokens.Tokens] = {}  # each session over its whole life
        self.team_states: dict[str, teamtree.TeamState] = {}  # the teams with a member open, as last read
        self.team_seen: dict[str, set[tuple[str, str, str]]] = {}  # what each team said: newer messages fly
        self.team_flights: dict[str, list[tuple[teams.Message, float]]] = {}  # messages in flight, since when
        self.unfolded: set[str] = set()  # teams shown with their members under their row (e); else one row
        self.closing_team: str | None = None  # x once on an open team's row: again closes its members' tabs
        self.blocks: list[tuple[teamtree.TeamState, list[tuple[str, Session]]]] = []  # the teams in the list
        self.member_rows: dict[str, tuple[str, str]] = {}  # their members' session ids -> (team, member)
        self.limits: list[usage.Limit] = []  # the plan limits as last read
        self.card_filter: str | None = None  # 1 2 3: the list shows only what that card counts
        self.bar_info = ""  # the top bar's counts, apart from its pills (watch_sub_title)
        self.focused_at = float("-inf")  # when the terminal last said the window took the focus
        self.swallowing = False  # the press that brought the window to the front: its release goes too
        self.footer_was: tuple | None = None  # the row the footer's words and dimmed keys were last made for

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="cards"):
            yield StatCard("OPEN NOW", "sessions running", "running", id="card-open")
            yield StatCard("NEED YOU", "waiting for an answer", "need an answer", id="card-waiting")
            yield StatCard("TO CONTINUE", "cut off or unanswered", "interrupted", id="card-continue")
            yield StatCard("TOKENS TODAY", "counting…", "…", id="card-tokens")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("PROJECTS", classes="heading")
                yield ProjectList(id="projects")
                yield Static(LEGEND, classes="legend")
                yield Label(f"TOKENS · {CHART_DAYS} DAYS", classes="heading")
                yield Sparkline([0] * CHART_DAYS, id="activity")
            with Vertical(id="main"):
                yield Input(placeholder="Find by project, title or anything said in a session", id="filter")
                # The cursor lights the row but keeps each cell's own colours (status, mode).
                yield SessionTable(id="table", cursor_type="row", cursor_foreground_priority="renderable")
                yield Mascot(id="mascot")
                with Horizontal(id="inspector"):
                    yield Static("Loading sessions…", id="detail")
                    yield Static("", id="meta")
        # Compact, and Ctrl+P (which still works) left out: every key fits on one row from about 162
        # columns; narrower, the footer takes a second row rather than cut any off.
        yield KeysFooter(compact=True, show_command_palette=False)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """$sunken (the sidebar's tone) for the CSS, which is read before the themes are registered."""
        return {**super().get_theme_variable_defaults(), "sunken": SUNKEN}

    @property
    def _background_screens(self) -> list[Screen]:
        """The screens that show through the one in front: none when it is opaque (the team view).
        Textual counts the screen under it either way, and then every change down there (the
        clock, Bit at 20 frames a second) has the whole screen in front written again."""
        if self.screen.styles.background.a == 1:
            return []
        return super()._background_screens

    def on_mount(self) -> None:
        # Keep references: App.query_one searches the active screen, which is a
        # dialog while one is open.
        self.table = table = self.query_one(DataTable)
        self.detail = self.query_one("#detail", Static)
        self.meta = self.query_one("#meta", Static)
        self.topbar = self.query_one(TopBar)
        self.mascot = self.query_one(Mascot)
        self.inspector = self.query_one("#inspector")
        self.mascot.ground = table  # Bit walks on the list's own background
        self.activity_chart = self.query_one(Sparkline)
        self.body = self.query_one("#body")
        self.flash: dict[str, float] = {}  # session id -> when its highlight ends
        self.activity_target: list[int] = []
        self.grow_until = 0.0
        self.filter_box = self.query_one("#filter", Input)
        self.projects = self.query_one(ProjectList)
        self.cards = {card.id: card for card in self.query(StatCard)}
        self.setup_columns()
        use_saved_theme(self)
        saved = read_json("config.json")
        self.mascot.accent = THEMES[self.theme][1]
        self.mascot.set_class(isinstance(saved, dict) and saved.get("mascot") is False, "hidden")
        self.place_mascot()
        self.set_interval(SPIN_SECONDS, self.spin)
        self.refresh_usage()
        self.set_interval(USAGE_SECONDS, self.refresh_usage)
        self.refresh_tokens()
        self.set_interval(TOKENS_SECONDS, self.refresh_tokens)
        armed = saved.get("continue_at_reset") if isinstance(saved, dict) else None
        for sid, moment in (armed.items() if isinstance(armed, dict) else ()):
            try:
                self.armed[sid] = datetime.fromisoformat(moment)
            except (TypeError, ValueError):
                pass
        self.set_interval(10, self.continue_due)
        # The tab is named after the dashboard, whatever started it, so that a desktop
        # notification can tell whether you are looking at it (toast.show).
        driver = self._driver
        if driver is not None and not driver.is_headless:
            driver.write(f"\x1b]0;{self.TITLE}\x07")
        table.focus()
        self.load()

    # --- a click that only brings the window to the front --------------------------------------

    def on_app_focus(self) -> None:
        self.focused_at = time.monotonic()

    async def on_event(self, event: events.Event) -> None:
        """A click on the dashboard's window from another one brings it to the front, and only that:
        it does not open the row under it, tick it, or pick a project. The terminal says the window
        lost the focus (or took it just now); that press and its release go no further."""
        if isinstance(event, (events.MouseDown, events.MouseUp, events.Click)) and not event.is_forwarded:
            if isinstance(event, events.MouseDown):
                self.swallowing = not self.app_focus or time.monotonic() - self.focused_at < FOCUS_CLICK
                if self.swallowing:
                    self.app_focus = True  # as Textual would have it: a press means the window has the focus
            if self.swallowing:
                return
        await super().on_event(event)

    def watch_sub_title(self, sub_title: str) -> None:
        # The bar shows the counts where they may be cut short, and the card's filter and the ticked
        # sessions apart, as pills that never are (bar_pills); sub_title says it all in one line.
        if self.is_mounted:
            self.topbar.show_info(self.bar_info or sub_title)
            self.topbar.put("pills", self.bar_pills())

    def bar_pills(self) -> Text:
        """' only NEED YOU · 2 or Esc shows all '  ' 2 ticked · ⏎ opens ', in the theme's colour."""
        if self.live_mode:
            return Text("")
        accent = self.accent()
        pill = f"bold {accent} on {mix('#000000', accent, BADGE)}"
        out = Text()
        if self.card_filter:
            key, _, title = CARDS[self.card_filter]
            out.append(f" only {title} · {key} or Esc shows all ", style=pill)
        if self.selected:
            shutdown = " · open at the last shutdown" if self.preselected and self.selected <= self.preselected else ""
            out.append(" " if out else "")
            out.append(f" {len(self.selected)} ticked{shutdown} · ⏎ opens ", style=pill)
        return out

    @property
    def dashboard(self) -> Screen:
        """The dashboard's own screen. self.screen is the one in front: a resize while a dialog
        was open set the layout's classes on the dialog, and the dashboard kept its old ones."""
        return self.screen_stack[0]

    def on_resize(self, event) -> None:
        if not self.screen_stack:  # shutting down
            return
        self.dashboard.set_class(event.size.width < 110, "narrow")
        self.dashboard.set_class(event.size.height < MINI_BELOW, "mini")
        self.dashboard.set_class(event.size.height < 30, "short")
        self.fit_sidebar(event.size.width)
        self.place_mascot()

    def fit_sidebar(self, width: int | None = None) -> None:
        """The project sidebar steps aside when the session table would not fit beside it."""
        least = OTHER_COLUMNS["live" if self.live_mode else "picker"] + TITLE_MIN
        width = self.size.width if width is None else width
        self.dashboard.set_class(width - SIDEBAR < least, "no-sidebar")
        self.call_after_refresh(self.fit_title)

    @work(thread=True)
    def load(self) -> None:
        # Quick: open sessions from the pid files now, `claude agents` refines them below.
        sessions = find_sessions(self.days, self.include_all, quick=True)
        preselected = open_at_shutdown(sessions, recent_shutdowns())
        self.summaries = summarize.load_cache()
        self.call_from_thread(self.show_sessions, sessions, preselected)

    def show_sessions(self, sessions: list[Session], preselected: set[str]) -> None:
        self.sessions = {s.session_id: s for s in sessions}
        preselected = {sid for sid in preselected if sid in self.sessions and not self.marks.is_hidden(self.sessions[sid])}
        self.selected = set(preselected)
        self.preselected = set(preselected)
        if self.start_live:
            self.action_toggle_live()
        self.poll_live()  # refine the quick view with `claude agents --json`
        self.poll_teams()
        self.index_search()  # what was said in each, for the filter
        self.refresh_git()
        self.set_interval(GIT_SECONDS, self.refresh_git)
        self.set_interval(LIVE_REFRESH_SECONDS, self.poll_tick)
        self.refresh_rows()
        self.update_dashboard()
        # Faded in, every widget takes its colours afresh: those drawn during the fade kept them
        # blended towards black (the preview's facts column stayed black for good).
        self.body.styles.animate("opacity", 1.0, duration=0.5, easing="out_cubic",
                                 on_complete=lambda: self.refresh_css(animate=False))
        if not sessions:
            self.detail.update(
                f"No Claude Code sessions in the last {self.days:g} days.")
        if self.preselected:
            self.mascot.say(f"Hi! {len(self.preselected)} were open when you shut down.", seconds=6)
        else:
            self.mascot.say("Hi! Press Enter to open a session.", seconds=5)
        self.check_limits()

    # --- rows ---------------------------------------------------------------------

    def setup_columns(self) -> None:
        labels = (["", "Project", "Title", "State", "Kind", "Last activity", "ctx"] if self.live_mode
                  else ["", "Project", "Title", "Status", "Process", "Last", "Open as"])
        self.table.clear(columns=True)
        for key, label in zip(("sel", "project", "title", "status", "exit", "active", "mode"), labels):
            self.table.add_column(label, key=key)

    def visible(self) -> list[Session]:
        needle = search.fold(self.filter_text)
        said = self.said_in() if needle else set()  # the words anywhere in the conversation
        shown = [s for s in self.in_sight() if not needle or s.session_id in said or needle in
                 search.fold(f"{s.cwd} {s.title} {s.first_prompt} {s.last_prompt}")]
        if self.project_filter != ALL_PROJECTS:
            shown = [s for s in shown if folder_key(s.cwd) == self.project_filter]
        if self.card_filter:
            shown = [s for s in shown if self.counted_by(s, self.card_filter)]
        if self.live_mode:
            shown = [s for s in shown if s.exit is Exit.RUNNING]
            shown.sort(key=lambda s: (state_of(s)[2], s.project.casefold(), s.session_id))
            return shown
        # Open sessions first, in a fixed order; closed ones newest first (groups() puts the pinned
        # ones apart). Neither moves while a session merely keeps working, so rows stay put under the mouse.
        shown.sort(key=lambda s: (0, s.project.casefold(), s.session_id) if s.exit is Exit.RUNNING
                   else (1, -s.last_active.timestamp(), s.session_id))
        return shown

    @staticmethod
    def counted_by(session: Session, card: str) -> bool:
        """Whether a card counts this session, as update_dashboard counts them."""
        if card == "open":
            return session.exit is Exit.RUNNING
        if card == "waiting":
            return session.exit is Exit.RUNNING and state_of(session)[2] < 2
        return session.exit is not Exit.RUNNING and session.status in INTERRUPTED

    def action_card(self, card: str) -> None:
        """1 2 3, or a click on a card: only what that card counts; the same again shows everything."""
        self.card_filter = None if self.card_filter == card else card
        for name, (_, card_id, _) in CARDS.items():
            self.cards[card_id].pick(name == self.card_filter)
        self.refresh_rows()
        self.update_status()
        if self.card_filter and not self.visible():
            self.notify(f"Nothing for {CARDS[card][2]} right now.", timeout=3)

    def on_stat_card_picked(self, message: StatCard.Picked) -> None:
        card = next((name for name, (_, card_id, _) in CARDS.items() if card_id == message.card.id), None)
        if card is not None:
            self.action_card(card)
        elif message.card.id == "card-tokens":
            self.action_tokens()

    def in_sight(self) -> list[Session]:
        """Every session but the hidden ones (X shows them too)."""
        if self.show_hidden:
            return list(self.sessions.values())
        return [s for s in self.sessions.values() if not self.marks.is_hidden(s)]

    def mode(self, session: Session) -> Mode:
        return self.modes.get(session.session_id) or default_mode(session.status)

    def live_cells(self, session: Session) -> tuple[Text, ...]:
        rank = state_of(session)[2]
        last = Text(ago(session.last_active), style="dim")
        if rank == 2 and (datetime.now() - session.last_active).total_seconds() > QUIET_SECONDS:
            # Busy but writing nothing: a long command, or a prompt nobody has answered.
            last = Text(f"{ago(session.last_active)} · quiet", style="yellow")
        return (
            Text(" ! " if rank == 0 else "", style="bold red"),
            Text(_clip(session.project, 18)),
            Text(_clip(session.title, self.title_width)),
            self.status_cell(session),
            Text("background" if session.status is Status.BACKGROUND else "terminal", style="dim"),
            last,
            self.context_cell(session),
        )

    def context_cell(self, session: Session) -> Text:
        """'▰▰▰▱ 73%', amber from the point where a handover is worth it, red past 85%."""
        if not session.context or session.cli != "claude":
            return Text("")
        share = context.share(session)
        colour = MUTED if share < context.WARN_SHARE else AMBER if share < 0.85 else RED
        filled = min(CONTEXT_BAR, round(share * CONTEXT_BAR))
        return Text.assemble(("▰" * filled, colour), ("▱" * (CONTEXT_BAR - filled), "#2a2a2a"),
                             (f" {share:.0%}", colour))

    def cells(self, session: Session) -> tuple[Text, ...]:
        if self.live_mode:
            return self.live_cells(session)
        if session.status is Status.RUNNING:  # open in a terminal: nothing to tick
            mark = Text("")
        elif session.session_id in self.selected:
            mark = Text("[x]", style=f"bold {self.accent()}")
        else:
            mark = Text("[ ]", style="#5a5a5a")
        open_here = session.status is Status.RUNNING  # open in a terminal: cannot be selected
        dim = "dim" if open_here else ""
        if session.session_id in self.member_rows:  # in a team's tree
            place, title = self.member_cells(session, "" if session.exit is Exit.RUNNING else "dim")
        else:
            place, title = Text(_clip(session.project, 18), style=dim), self.title_cell(session, dim)
        return (
            mark,
            place,
            title,
            self.status_cell(session),
            self.process_cell(session),
            Text(ago(session.last_active), style=MUTED),
            self.mode_cell(session),
        )

    def process_cell(self, session: Session) -> Text:
        """How the process ended; other CLIs leave no record of that, so it names the CLI."""
        if session.cli != "claude":
            return Text(session.cli, style=CYAN)
        if session.exit is Exit.RUNNING:  # still going: how full its context is matters more
            return self.context_cell(session)
        if session.exit is Exit.CLEAN:  # the usual: said in the preview, not on every row
            return Text("")
        return Text(session.exit.value, style=AMBER if session.exit is Exit.KILLED else MUTED)

    def title_cell(self, session: Session, style: str) -> Text:
        if self.marks.is_pinned(session):
            title = Text.assemble(("★ ", self.accent()), (_clip(session.title, self.title_width - 2), style))
        elif self.marks.is_hidden(session):  # shown with X: says so
            title = Text.assemble(("hidden · ", MUTED), (_clip(session.title, self.title_width - 9), f"{style} {MUTED}"))
        else:
            title = Text(_clip(session.title, self.title_width), style=style)
        if session.session_id not in self.expanded:
            return title
        return Text("\n").join([title, *self.expansion(session)])

    def expansion(self, session: Session) -> list[Text]:
        """How the session ended, wrapped to fit under its title."""
        heading, body = self.outcome(session)
        lines = textwrap.wrap(body, self.title_width) or ["(nothing recorded)"]
        if len(lines) > EXPAND_LINES:
            lines = [*lines[:EXPAND_LINES - 1], _clip(lines[EXPAND_LINES - 1] + "…", self.title_width)]
        heading.truncate(self.title_width, overflow="ellipsis")  # never wider than the column
        return [heading, *(Text(line, style=TEXT) for line in lines)]

    def row_height(self, session: Session) -> int:
        return 1 + len(self.expansion(session)) if session.session_id in self.expanded else 1

    def fit_title(self) -> None:
        """Narrow windows get shorter titles, so every column fits without scrolling sideways."""
        if not hasattr(self, "table") or not self.table.size.width:
            return
        room = self.table.size.width - OTHER_COLUMNS["live" if self.live_mode else "picker"]
        width = max(TITLE_MIN, min(EXPAND_WIDTH, room))
        if width != self.title_width:
            self.title_width = width
            self.setup_columns()  # a column keeps the width of its widest cell so far
            self.refresh_rows()

    def toggle_expanded(self, session_id: str) -> None:
        self.expanded ^= {session_id}
        self.refresh_rows()

    def action_expand(self) -> None:
        team = self.team_at()
        if team is not None:  # a team unfolds from its row, and back
            self.toggle_folded(team.team.name)
            return
        session = self.current()
        if session is not None:
            self.toggle_expanded(session.session_id)

    def mode_cell(self, session: Session, plain: bool = False) -> Text:
        """How it opens; in the list only when not the plain resume (plain=True says it anyway)."""
        if session.session_id in self.armed:  # c: it continues when its limit lifts
            return Text(f"▶ at {lifts(self.armed[session.session_id])}", style=f"bold {self.accent()}")
        if session.session_id in self.queued:  # h on a busy one: handed over once it finishes
            return Text("▶ hand over", style=f"bold {self.accent()}")
        if session.status is Status.RUNNING:
            return Text("")
        mode = self.mode(session)
        if mode is Mode.RESUME and not plain:
            return Text("")
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        return Text(MODE_LABEL[mode], style=f"bold {accent}" if mode is Mode.AUTO else MUTED)

    def flash_background(self, session: Session) -> str:
        """While a row is lit after a change, a background that fades back to black."""
        left = self.flash.get(session.session_id, 0) - time.monotonic()
        if left <= 0:
            return ""
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        return f" on {mix('#000000', accent, 0.55 * left / FLASH_SECONDS)}"

    def status_cell(self, session: Session) -> Text:
        """Closed sessions say how they ended; open ones animate what they are doing."""
        lit = self.flash_background(session)
        if at_limit(session):
            cell = limit_cell(session)
            return Text(cell.plain, style=badge(str(cell.style)) + lit)
        if session.exit is not Exit.RUNNING:
            label, style = STATUS_LABEL[session.status]
            return Text(label, style=badge(style) + lit)
        label, style, rank = state_of(session)
        kind = " · bg" if session.status is Status.BACKGROUND else ""
        if session.live_state.lower() in SPINNING:
            return self.working(f"{label}{kind}", lit)
        if rank == 0:
            # A smooth pulse rather than a blink: the red breathes in and out.
            glow = (math.sin(self.frame * SPIN_SECONDS * 4) + 1) / 2
            return Text(f"! {label}{kind}", style=f"bold #ffffff on {mix('#3d0f0d', RED, glow)}")
        return Text(f"● {label}{kind}", style=style + lit)

    def working(self, label: str, background: str) -> Text:
        """Claude's own look: a turning star in the accent colour that glows softly,
        and a slow band of light sweeping across the word."""
        now = time.monotonic()
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        glow = (math.sin(now * 4.0) + 1) / 2
        star = STAR[int(now * 8) % len(STAR)]
        text = Text(f"{star} ", style=f"bold {mix(mix(accent, '#000000', 0.5), accent, glow)}{background}")
        band = (now * 7) % (len(label) + 12) - 6
        for index, char in enumerate(label):
            closeness = max(0.0, 1 - abs(index - band) / 4)
            text.append(char, style=f"{mix(BLUE, SHIMMER, closeness * closeness)}{background}")
        return text

    def spin(self) -> None:
        """Advance the spinners and pulses of open sessions; touch only their cells."""
        self.frame += 1
        now = time.monotonic()
        if self.grow_until > now:  # the token chart rises from zero after loading
            progress = 1 - (self.grow_until - now) / GROW_SECONDS
            eased = 1 - (1 - progress) ** 3
            self.activity_chart.data = [value * eased for value in self.activity_target]
        elif self.grow_until:
            self.activity_chart.data = self.activity_target
            self.grow_until = 0.0
        if self.team_flights:
            self.fly(now)
        for state, _ in self.blocks:  # a folded team's members at work: their stars turn on its row
            key = f"{TEAM_ROW}{state.team.name}"
            if (state.team.name not in self.unfolded and key in self.table.rows
                    and any(s.live_state.lower() in SPINNING for _, s in self.team_members(state.team.name))):
                self.table.update_cell(key, "title", self.team_chips(state))
        for session in self.visible():
            sid = session.session_id
            animated = session.exit is Exit.RUNNING or sid in self.flash
            if animated and sid in self.table.rows:
                self.table.update_cell(sid, "status", self.status_cell(session))
            if self.flash.get(sid, now) < now:
                del self.flash[sid]  # drawn one last time without the highlight

    def update_dashboard(self) -> None:
        sessions = self.in_sight()  # what is hidden is not counted either
        open_now = [s for s in sessions if s.exit is Exit.RUNNING]
        waiting = [s for s in open_now if state_of(s)[2] < 2]
        to_continue = [s for s in sessions if s.exit is not Exit.RUNNING and s.status in INTERRUPTED]
        # Beside each number, what it counts: the open ones by what they are doing, and
        # the projects of the ones waiting for you and of the ones to continue.
        doing: dict[tuple[str, str, int], int] = {}
        for session in open_now:
            key = state_of(session)
            if key[2] < 2:  # needs you is waiting for you too; the table tells them apart
                key = LIVE_STATES["idle"]
            doing[key] = doing.get(key, 0) + 1
        ranked = sorted(doing.items(), key=lambda item: item[0][2])
        doing_now = [(f"{count} {label}", style) for (label, style, _), count in ranked]
        self.cards["card-open"].show(len(open_now), level="good" if open_now else "", beside=tally(doing_now),
                                     line=inline(doing_now) if doing_now else Text("sessions running", style=MUTED))
        self.cards["card-waiting"].show(len(waiting), level="alert" if waiting else "",
                                        beside=tally(by_project(waiting, TEXT)),
                                        line=inline(by_project(waiting, TEXT)) if waiting else
                                        Text("waiting for an answer", style=MUTED))
        self.cards["card-continue"].show(len(to_continue), level="warn" if to_continue else "",
                                         beside=tally(by_project(to_continue, TEXT)),
                                         line=inline(by_project(to_continue, TEXT)) if to_continue else
                                         Text("cut off or unanswered", style=MUTED))
        signs = {key: ("±" if state.changed else "") + ("↑" if state.ahead else "") for key, state in self.git.items()}
        self.projects.show_projects(sessions, {s.session_id for s in waiting}, signs)
        self.mascot.set_mood(working=sum(s.live_state.lower() in SPINNING for s in open_now),
                             waiting=sum(state_of(s)[2] == 0 for s in open_now))

    @work(thread=True, exclusive=True, group="tokens")
    def refresh_tokens(self) -> None:
        report = tokens.collect(max(self.days, CHART_DAYS))
        self.call_from_thread(self.show_tokens, report)
        others = [name for name in clis.enabled() if name != "claude"]
        if others:  # Codex and agy have no live feed: their sessions are re-read every minute
            fresh = [s for s in other_sessions(self.days, others)
                     if self.include_all or s.entrypoint in ("", "cli")]
            self.call_from_thread(self.apply_other_sessions, fresh)

    def apply_other_sessions(self, fresh: list[Session]) -> None:
        current = {sid: s for sid, s in self.sessions.items() if s.cli != "claude"}
        wanted = {s.session_id: s for s in fresh}
        if current == wanted:
            return
        gone = current.keys() - wanted.keys()
        for sid in gone:
            del self.sessions[sid]
        self.sessions.update(wanted)
        self.sync_rows({sid for sid in wanted if current.get(sid) != wanted[sid]} | gone)
        self.update_dashboard()

    def show_tokens(self, report: tokens.Report) -> None:
        self.token_report = report
        self.session_tokens = report.per_session(whole_life=True)
        today = report.total(last_days=1)
        value, unit, decimals = tokens.scaled(today.total)
        card = self.cards["card-tokens"]
        card.decimals = decimals
        card.show(value, caption=f"{unit} · {human(today.output)} output", short=unit,
                  beside=HourBars(report.hourly()), line=Text(f"{human(today.output)} output", style=MUTED), unit=unit)
        self.update_chart()
        self.update_detail()

    def update_chart(self) -> None:
        """Tokens per day over the last two weeks, for the project picked in the sidebar."""
        report = self.token_report
        if report is None:
            return
        ids = None
        if self.project_filter != ALL_PROJECTS:
            # Where the session list knows a session, its folder decides, as in the list.
            folders = report.folders | {sid: s.cwd for sid, s in self.sessions.items()}
            ids = {sid for sid, folder in folders.items() if folder_key(folder) == self.project_filter}
        target = [use.total for use in report.daily(ids)[-CHART_DAYS:]]
        if not self.activity_target:  # first data: let the bars grow in
            self.grow_until = time.monotonic() + GROW_SECONDS
        else:
            self.activity_chart.data = target
        self.activity_target = target

    def groups(self) -> list[tuple[str, list[Session]]]:
        """The sessions shown, in the groups the list heads: open (the teams with a member open
        first, closed members too), pinned, the rest (viewmodel.group)."""
        grouped = VM.group(self.visible(), self.marks.pinned, self.team_states, teams_too=not self.live_mode)
        self.blocks, self.member_rows = grouped.blocks, grouped.members
        return grouped.groups

    def rows(self) -> list[tuple[str, Session | None, int]]:
        """(key, session, height) of the table's rows: the sessions shown, under a heading for
        each group when there is more than one, and each team in OPEN NOW under a row of its own
        with its members (unless folded). Neither kind of heading has a session."""
        groups = self.groups()
        if self.live_mode:
            return [(s.session_id, s, self.row_height(s)) for _, items in groups for s in items]
        out: list[tuple[str, Session | None, int]] = []
        blocks = {state.team.name: members for state, members in self.blocks}
        placed: set[str] = set()
        for name, items in groups:
            if len(groups) > 1:  # two rows high: a blank line sets each heading apart from what is above it
                out.append((f"{HEADING}{name}", None, 2))
            for session in items:
                team = self.member_rows.get(session.session_id, ("", ""))[0]
                if not team:
                    out.append((session.session_id, session, self.row_height(session)))
                elif team not in placed:  # the team's row where its first member comes, once
                    placed.add(team)
                    out.append((f"{TEAM_ROW}{team}", None, 1))
                    if team in self.unfolded:
                        out += [(s.session_id, s, self.row_height(s)) for _, s in blocks[team]]
        return out

    def heading_cells(self, key: str) -> tuple[Text, ...]:
        group = key.removeprefix(HEADING)
        # OPEN NOW counts the open ones: a team shows its closed members there too.
        count = {name: sum(s.exit is Exit.RUNNING for s in items) if name == "open" else len(items)
                 for name, items in self.groups()}.get(group, 0)
        title, style = {"open": ("OPEN NOW", f"bold {GREEN}"), "pinned": ("★ PINNED", f"bold {self.accent()}"),
                        "rest": ("EARLIER", f"bold {TEXT}")}[group]
        label = Text.assemble("\n", (title, style), (f" · {count}", MUTED))
        return (Text(""), label, *(Text("") for _ in range(5)))

    def refresh_rows(self) -> None:
        table = self.table
        current = self.cursor_key()
        table.clear()
        for key, session, height in self.rows():
            cells = (self.cells(session) if session is not None else self.team_cells(key)
                     if key.startswith(TEAM_ROW) else self.heading_cells(key))
            table.add_row(*cells, key=key, height=height)
        if current is not None and current in table.rows:
            table.move_cursor(row=table.get_row_index(current))
        self.off_heading(1)
        self.update_status()

    def cursor_key(self) -> str | None:
        """The key of the row under the cursor: a session id, or a heading's or team's key."""
        table = self.table
        if not 0 <= table.cursor_row < table.row_count:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def team_at(self, row: int | None = None) -> teamtree.TeamState | None:
        """The team whose heading row this is (the cursor's by default)."""
        if row is not None and not 0 <= row < self.table.row_count:
            return None
        key = self.cursor_key() if row is None else \
            self.table.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        return self.team_states.get(key[1:]) if key and key.startswith(TEAM_ROW) else None

    def team_members(self, name: str) -> list[tuple[str, Session]]:
        """A team's members in the list, the lead first, each with its session as the list has it now."""
        members = next((members for state, members in self.blocks if state.team.name == name), [])
        return [(member, self.sessions.get(s.session_id, s)) for member, s in members]

    def team_open(self, name: str) -> bool:
        return any(s.exit is Exit.RUNNING for _, s in self.team_members(name))

    def team_cells(self, key: str) -> tuple[Text, ...]:
        """A team's row. Folded (as it starts): its members as chips, each with a sign of what it is
        doing. Unfolded, its members are listed under it, and the row says how many are open and how
        many messages went between them. Then what the open members are doing (or that it is closed),
        its tokens, and when it last spoke."""
        name = key.removeprefix(TEAM_ROW)
        state = self.team_states.get(name)
        if state is None:  # gone since the rows were made: the next refresh drops the row
            return tuple(Text("") for _ in range(7))
        accent = self.accent()
        members = self.team_members(name)
        opened = any(s.exit is Exit.RUNNING for _, s in members)
        if opened:
            mark = Text("▾" if name in self.unfolded else "▸", style=f"bold {accent}")
        else:  # closed: it opens as a team, so it is ticked as one
            closed = {s.session_id for _, s in members}
            ticked = closed & self.selected
            mark = (Text("[x]", style=f"bold {accent}") if ticked == closed else
                    Text("[-]", style=f"bold {accent}") if ticked else Text("[ ]", style="#5a5a5a"))
        heading = Text.assemble(("TEAM ", f"bold {MUTED}"), (name, f"bold {accent}"))
        heading.truncate(18, overflow="ellipsis")
        if name in self.unfolded:
            said = len(state.between_members())
            messages = f"{said} message{'' if said == 1 else 's'}"
            opened_now, everyone = sum(s.exit is Exit.RUNNING for _, s in members), len(state.team.members)
            what = next((words for words in (f"{messages} · {opened_now} of {everyone} open",
                                             f"{messages} · {opened_now}/{everyone} open", messages)
                         if len(words) <= self.title_width), messages)
            title = Text(_clip(what, self.title_width), style=MUTED)
        else:
            title = self.team_chips(state)
        if opened:
            ranks: dict[int, int] = {}
            for _, session in members:
                if session.exit is Exit.RUNNING:
                    rank = state_of(session)[2]
                    ranks[rank] = ranks.get(rank, 0) + 1
            status = Text()
            for rank in sorted(ranks):
                glyph, style = TEAM_GLYPHS.get(rank, ("●", MUTED))
                status.append(f"{'  ' if status else ''}{glyph} {ranks[rank]}", style=style)
            last = ago(state.said[-1].at) if state.said else ""
        else:
            stopped = sum(s.status in INTERRUPTED for _, s in members)
            status = (Text(f"▲ {stopped} to continue", style=badge(f"bold {AMBER}")) if stopped
                      else Text("closed", style=MUTED))
            last = ago(max(s.last_active for _, s in members)) if members else ""
        spent = state.tokens(self.session_tokens)
        return (mark, heading, title, status, Text(human(spent) if spent else "", style=MUTED),
                Text(last, style=MUTED), Text(""))

    def chip(self, session: Session | None) -> tuple[str, str, str, int]:
        """A member's sign on its team's row, the sign's style, its name's, and how much it needs you
        (lowest first): red when it waits for you, Claude's turning star while it works; a closed one
        is quiet, with ▲ when it was cut off."""
        if session is None or session.exit is not Exit.RUNNING:
            if session is not None and session.status in INTERRUPTED:
                label, style = STATUS_LABEL[session.status]
                return label.split()[0], style, MUTED, 4
            return "", "", MUTED, 5
        if at_limit(session):
            return "◐", AMBER, AMBER, 3
        raw = session.live_state.lower()
        rank = state_of(session)[2]
        if raw in SPINNING:
            return STAR[int(time.monotonic() * 8) % len(STAR)], f"bold {BLUE}", TEXT, rank
        if rank == 0:
            return "!", f"bold {RED}", f"bold {RED}", rank
        if rank == 1:
            return "●", RED, RED, rank
        if raw == "shell":
            return "◦", CYAN, TEXT, 3
        return "●", state_of(session)[1], TEXT, rank

    def team_chips(self, state: teamtree.TeamState) -> Text:
        """Every member of a team on its row, in the team's order, the lead first."""
        listed = dict(self.team_members(state.team.name))
        items = []
        for member in [state.team.lead, *(m for m in state.team.members if not m.lead)]:
            session = listed.get(member.name) or state.sessions.get(member.name)
            items.append((member.name, member.lead, *self.chip(session)))
        return teamtree.chips(items, self.title_width, f"bold {self.accent()}")

    def update_team_rows(self) -> None:
        for state, _ in self.blocks:
            key = f"{TEAM_ROW}{state.team.name}"
            if key in self.table.rows:
                for column, value in zip(("sel", "project", "title", "status", "exit", "active", "mode"),
                                         self.team_cells(key)):
                    self.table.update_cell(key, column, value)

    def member_cells(self, session: Session, style: str) -> tuple[Text, Text]:
        """A team member's place in the tree, where the project goes, and the latest message to or
        from it, where the title goes (its role, while nothing is said)."""
        name, member = self.member_rows[session.session_id]
        state = self.team_states[name]
        colours = team_colours(state.team, self.accent())
        members = next((members for st, members in self.blocks if st.team.name == name), [])
        lead = member == state.team.lead.name
        branch = "★ " if lead else "└ " if members and members[-1][0] == member else "├ "
        place = Text.assemble((branch, f"bold {colours[member]}" if lead else MUTED),
                              (member, f"bold {colours.get(member, TEXT)} {style}".strip()))
        place.truncate(18, overflow="ellipsis")
        said = teamtree.last_word(state, member)
        if said is not None:
            line = teamtree.word(said, member, colours)
        else:
            focus = next((m.focus for m in state.team.members if m.name == member), "")
            line = Text(focus, style=MUTED)
        line.truncate(self.title_width, overflow="ellipsis")
        if session.session_id in self.expanded:
            line = Text("\n").join([line, *self.expansion(session)])
        return place, line

    def off_heading(self, step: int) -> None:
        """The cursor never rests on a heading: on to the next row (or back, at the end)."""
        table = self.table
        for direction in (step, -step):
            row = table.cursor_row
            while 0 <= row < table.row_count and self.is_heading(row):
                row += direction
            if 0 <= row < table.row_count:
                if row != table.cursor_row:
                    table.move_cursor(row=row)
                return

    def is_heading(self, row: int) -> bool:
        if not 0 <= row < self.table.row_count:  # an event about rows since replaced
            return False
        key = self.table.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        return bool(key) and key.startswith(HEADING)

    def sync_rows(self, changed: set[str]) -> None:
        """Bring the table up to date without rebuilding it when the rows are the same.

        A rebuild every few seconds made the table flicker.
        """
        wanted = [(key, height) for key, _, height in self.rows()]
        if wanted != [(row.key.value, row.height) for row in self.table.ordered_rows]:
            self.refresh_rows()
            return
        for sid in changed:
            if sid in self.sessions:
                self.update_row(self.sessions[sid])
        self.update_team_rows()
        self.update_status()

    def update_row(self, session: Session) -> None:
        table = self.table
        if session.session_id not in table.rows:
            return
        for key, value in zip(("sel", "project", "title", "status", "exit", "active", "mode"),
                              self.cells(session)):
            table.update_cell(session.session_id, key, value)

    def current(self) -> Session | None:
        table = self.table
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return self.sessions.get(row_key.value)

    def update_status(self) -> None:
        if self.live_mode:
            open_now = [s for s in self.sessions.values() if s.exit is Exit.RUNNING]
            waiting = sum(state_of(s)[2] < 2 for s in open_now)
            self.bar_info = ""
            self.sub_title = (f"Live · {len(open_now)} open · {waiting} waiting for you · "
                              f"refreshes every {LIVE_REFRESH_SECONDS}s · l returns to the list")
            self.update_detail()
            return
        hidden = sum(self.marks.is_hidden(s) for s in self.sessions.values())
        info = f"{len(self.sessions) - (0 if self.show_hidden else hidden)} sessions, last {self.days:g} days"
        if hidden:
            info += f" · showing {hidden} hidden (x brings one back)" if self.show_hidden else                 f" · {hidden} hidden (X shows them)"
        self.bar_info = info
        if self.card_filter:
            key, _, title = CARDS[self.card_filter]
            info += f" · only {title} ({key} or Esc shows all)"
        if self.selected:
            info += f" · {len(self.selected)} selected (Enter opens them)"
        if self.preselected:
            info += f" · {len(self.preselected)} looked open at the last shutdown"
        if self.sub_title == info:  # the same words, other ticks: the pills still change
            self.watch_sub_title(info)
        self.sub_title = info
        self.update_detail()
        self.refresh_footer()

    def update_detail(self) -> None:
        detail = self.detail
        session = self.current()
        if session is None:
            state = self.team_at()
            if state is not None:
                self.show_team(state)
            return
        self.inspector.remove_class("team-only")
        head = Text()
        head.append(session.title + "\n", style=f"bold {TEXT}")
        label, style = (STATUS_LABEL[session.status] if session.exit is not Exit.RUNNING
                        else state_of(session)[:2])
        agent = clis.label(session.cli).split()[0]  # Claude, Codex, Antigravity
        if at_limit(session):
            head.append(limit_cell(session))
            head.append("  " + limit_help(session), style=MUTED)
        else:
            head.append(label, style=style)
            head.append("  " + STATUS_HELP[session.status].replace("Claude", agent), style=MUTED)
        head.append("\n")
        head.append_text(Text.assemble(("⏎ ", f"bold {self.accent()}"), (self.enter_words(session), self.accent())))
        # Quiet labels on the left, the words themselves bright and indented beside them,
        # each cut to a few lines of the room there is, so the reply still shows when narrow.
        room = max(24, (detail.content_size.width or 74) - 14)
        said = Table.grid(padding=(0, 2))
        said.add_column(style=MUTED, no_wrap=True)
        said.add_column(style=TEXT, overflow="fold")
        if session.session_id in self.member_rows:
            said.add_row("Messages", self.member_messages(session, 2 * room))
        if session.session_id in self.said_in():  # the filter's words, where they were said
            piece, start, end = self.search.snippet(session.session_id, self.filter_text, width=2 * room)
            if piece:
                found = Text(piece)
                found.stylize(f"bold reverse {self.accent()}", start, end)
                said.add_row("Found", found)
        summary = self.summaries.get(session.session_id)
        if session.session_id in self.summarizing:
            said.add_row("Summary", Text(f"asking Claude ({summarize.MODEL})…", style="dim italic"))
        elif summary:
            words = Text(summary.get("summary", ""))
            if summary.get("size") != session.size:
                words.append("  (written before the latest activity; s refreshes it)", style="dim")
            said.add_row("Summary", words)
        said.add_row("First prompt", _clip(session.first_prompt, min(220, 3 * room)))
        if session.last_prompt and session.last_prompt != session.first_prompt:
            said.add_row("Last prompt", _clip(session.last_prompt, min(220, 2 * room)))
        if session.last_reply:
            said.add_row(agent, _clip(session.last_reply, min(320, 4 * room)))
        if session.files:
            said.add_row("Files edited", _clip(", ".join(session.files), 2 * room))
        detail.update(Group(head, Text(""), said))
        self.meta.update(self.meta_table(session))

    def enter_words(self, session: Session) -> str:
        """What Enter does with the highlighted session, as it stands: ticked ones go first."""
        if self.selected:
            count = len(self.selected)
            ticked = f"opens the {count} ticked session{'' if count == 1 else 's'}, each in a tab"
            return ticked if session.session_id in self.selected else ticked + " (not this one: Space ticks it)"
        if session.status is Status.BACKGROUND:
            return "attaches to it in a new tab"
        if session.exit is Exit.RUNNING:
            return "goes to its tab, to type there"
        launch = self.launch_for(session)
        words = {Mode.RESUME: "resumes it in a new tab",
                 Mode.AUTO: f"resumes it in a new tab and tells {clis.label(session.cli).split()[0]} to carry on",
                 Mode.FORK: "forks it: a new session in a new tab, from a copy of its history",
                 Mode.NEW: "starts a fresh session in its folder"}.get(launch.mode, "opens it in a new tab")
        if launch.options:
            team, _, member = launch.title.partition(": ")
            words += f", as {member} of the team {team}"
        return words + " · m changes how"

    def meta_table(self, session: Session) -> Table:
        """The facts column next to the preview."""
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row("Folder", Text(home_short(session.cwd) or "(none recorded)", style=TEXT))
        if session.session_id in self.member_rows:
            team, member = self.member_rows[session.session_id]
            grid.add_row("Team", Text.assemble((team, f"bold {self.accent()}"), (f" · {member}", TEXT)))
        if session.cli != "claude":
            grid.add_row("CLI", Text(clis.label(session.cli), style=CYAN))
        if session.git_branch:
            grid.add_row("Branch", Text(f"⎇ {session.git_branch}", style=CYAN))
        state = self.git.get(folder_key(session.cwd))
        if state is not None:  # the folder as it is now, whatever the session left
            grid.add_row("Git", Text(state.words(), style=AMBER if state.changed or state.ahead else MUTED))
        if session.started:
            grid.add_row("Started", f"{session.started:%d %b %H:%M}")
        grid.add_row("Last active", f"{session.last_active:%d %b %H:%M}  ({ago(session.last_active)} ago)")
        if session.lines_added is not None:
            grid.add_row("Lines", Text.assemble((f"+{session.lines_added}", GREEN), "  ",
                                                (f"−{session.lines_removed or 0}", RED)))
        use = self.session_tokens.get(session.session_id)
        if use is not None and use.total:
            grid.add_row("Tokens", Text.assemble((human(use.total), TEXT), (f"  ·  {human(use.output)} out", MUTED)))
        if session.context and session.cli == "claude":
            grid.add_row("Context", fill(session))
            if context.crowded(session):  # the two ways on, in a line: the same work, or a new phase
                grid.add_row("", Text.assemble(("/compact", f"bold {AMBER}"), (": go on · ", AMBER),
                                               ("h", f"bold {AMBER}"), (": new phase", AMBER)))
        if session.exit in (Exit.CLEAN, Exit.KILLED):
            grid.add_row("Process", Text.assemble(
                (session.exit.value, AMBER if session.exit is Exit.KILLED else TEXT),
                (f"  {EXIT_HELP[session.exit]}", MUTED)))
        if session.status is not Status.RUNNING:
            launch = self.launch_for(session)
            grid.add_row("Opens as", self.mode_cell(session, plain=True))
            argv = replace(launch, options=()).argv  # a member's flags are long: said in words below
            if launch.mode is Mode.AUTO:  # the prompt is long; its start is enough here
                argv = [*argv[:-1], _clip(argv[-1], 28)]
            grid.add_row("", Text(subprocess.list2cmdline(argv), style=MUTED))
            if launch.options:
                grid.add_row("", Text(f"as {launch.title}, with its role and the team's messages let in",
                                      style=MUTED))
        return grid

    def launch_for(self, session: Session, saved: dict[str, teams.Team] | None = None) -> Launch:
        """How a closed session opens: a team member's as that member (teams.as_member)."""
        mode = self.mode(session)
        return teams.as_member(session, Launch(
            title=_clip(f"{session.project}: {session.title}", 48), cwd=session.cwd, mode=mode,
            session_id=session.session_id, cli=session.cli,
            prompt=continue_prompt(session.status) if mode is Mode.AUTO else None), saved)

    # --- events and actions -------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.is_heading(event.cursor_row):
            self.off_heading(1 if event.cursor_row >= self.last_row else -1)
            return
        self.last_row = event.cursor_row
        self.update_detail()
        self.refresh_footer()
        self.detail.styles.opacity = 0.4
        self.detail.styles.animate("opacity", 1.0, duration=0.25)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list is not self.projects or event.option.id == self.project_filter:
            return
        self.project_filter = event.option.id or ALL_PROJECTS
        self.refresh_rows()
        self.update_chart()


    def outcome(self, session: Session) -> tuple[Text, str]:
        """A heading and a few words on what the session came to (shown when a row is opened)."""
        if session.exit is Exit.RUNNING:
            heading, style = f"Open now · {state_of(session)[0]}", GREEN
        else:
            heading, style = TOOLTIP_STATUS.get(session.status, "Session"), STATUS_LABEL[session.status][1]
        summary = self.summaries.get(session.session_id, {}).get("summary")
        if session.status is Status.DONE or session.exit is Exit.RUNNING:
            body = summary or session.last_reply or session.last_prompt
        elif session.status in (Status.CUT_OFF, Status.UNANSWERED, Status.LIMIT, Status.DROPPED):
            body = f"Last asked: {session.last_prompt or session.first_prompt}"
        else:
            body = summary or session.last_reply or session.first_prompt
        title = Text.assemble((heading, f"bold {style}"), (f"  ·  {ago(session.last_active)} ago", MUTED))
        return title, " ".join((body or "").split())

    def action_prompt(self) -> None:
        session = self.current()
        if session is None:
            return
        if session.status is Status.RUNNING:
            # Nothing can type into it from outside, so take you to its tab instead.
            self.go_to_tab(session)
            return
        if session.status is Status.BACKGROUND:
            self.notify("This session runs in the background: Enter attaches to it.", severity="warning")
            return

        def send(answer: tuple[str, str] | None) -> None:
            if not answer:
                return
            how, prompt = answer
            launch = teams.as_member(session, Launch(
                title=_clip(f"{session.project}: {session.title}", 48), cwd=session.cwd, mode=Mode.RESUME,
                session_id=session.session_id, prompt=prompt, cli=session.cli))
            if how == "background" and not clis.CLIS[session.cli].can_background:
                self.notify(f"{clis.label(session.cli)} has no background sessions: opening a tab instead.",
                            severity="warning")
                how = "tab"
            if how == "tab":
                leftover = open_tabs([launch])
                if leftover:
                    self.notify("No supported terminal here. Run: " + leftover[0], severity="warning")
                else:
                    self.notify(f"Opened {session.project} in a new tab with your prompt.")
            else:
                self.notify(f"Starting {session.project} in the background…")
                self.start_background(launch)

        self.push_screen(PromptDialog(session), send)

    def action_tokens(self) -> None:
        if self.token_report is None:
            self.notify("Still counting tokens…", timeout=3)
            return
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        self.push_screen(StatsScreen(self.token_report, self.sessions, accent))

    # --- handing over -----------------------------------------------------------------

    def warn_crowded(self, sessions) -> None:
        """Say so, once per session, when an open session's context passes the handover point.
        Nothing is handed over unless you ask."""
        crowded = [s for s in sessions if s.exit is Exit.RUNNING and context.crowded(s)
                   and s.session_id not in self.told_crowded]
        if not crowded:
            return
        self.told_crowded |= {s.session_id for s in crowded}
        if len(crowded) == 1:
            session = crowded[0]
            going = f" has been going for {duration((datetime.now() - session.started).total_seconds())} and" \
                if session.started else ""
            self.notify(f"{session.project}{going} its context is {context.share(session):.0%} full. /compact in "
                        "its tab goes on with the same work; h starts a new phase in a fresh session, and shows "
                        "what is handed over.", title="Crowded session", timeout=12)
            self.mascot.say(f"{session.project}: context {context.share(session):.0%} full", AMBER, seconds=6)
        else:
            names = ", ".join(s.project for s in crowded[:4])
            self.notify(f"{len(crowded)} open sessions have crowded contexts ({names}). /compact in a tab goes "
                        "on with the same work; h on one starts a new phase in a fresh session, and shows what is "
                        "handed over.", title="Crowded sessions", timeout=12)

    def action_handover(self) -> None:
        self.open_handover(self.current())

    def open_handover(self, session: Session | None, how: str | None = None) -> None:
        """The h dialog for a session: how its handover should be written. Given how ("self" or
        "transcript", as act() may), it starts straight away, after the same checks."""
        if session is None:
            return
        if session.status is Status.BACKGROUND:
            self.notify("Background sessions cannot be handed over from here: attach to it first.",
                        severity="warning")
            return
        if session.cli == "agy":
            self.notify("Antigravity's conversations cannot be read, so there is nothing to hand over.",
                        severity="warning")
            return
        if session.session_id in self.queued:  # h again: not after all
            del self.queued[session.session_id]
            self.notify(f"{session.project} will not be handed over when it finishes.", timeout=5)
            self.update_row(session)
            return
        if session.exit is Exit.RUNNING:
            # A busy one is handed over once it finishes; only a reason that stays stops it now.
            reason = handover.cannot_close(replace(session, live_state="idle"), self.open_ones())
            if reason:
                self.notify(f"{session.project} cannot be handed over now: {reason}.", severity="warning",
                            timeout=10)
                return

        if how is not None:
            self.start_handover(session, how)
            return
        self.push_screen(HandoverDialog(session), lambda how: how and self.start_handover(session, how))

    def start_handover(self, session: Session, how: str) -> None:
        """Hand it over now, or once it finishes if it is working."""
        writer = "it writes its handover" if how == "self" else f"Claude ({handover.MODEL}) writes the handover"
        now = self.sessions.get(session.session_id, session)
        if now.exit is Exit.RUNNING and now.live_state not in handover.IDLE:
            self.queued[session.session_id] = how
            self.notify(f"{session.project} is working: it is handed over as soon as it finishes ({writer}). "
                        "h again cancels.", title=f"Handing over {session.project}", timeout=10)
            self.update_row(now)
            return
        closing = "Closing it; " if session.exit is Exit.RUNNING else ""
        self.notify(f"{closing}{writer} in the background. The fresh session opens when it is done.",
                    title=f"Handing over {session.project}", timeout=10)
        self.hand_over(session, how)

    def open_ones(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.exit is Exit.RUNNING]

    @work(thread=True, group="handover")
    def hand_over(self, session: Session, how: str) -> None:
        """Write the handover, close the old session if it is open, then open the fresh one.

        The session writing it itself needs it closed first. Written from the transcript, the
        handover is there before the old session closes, so a failed close only leaves it open.
        """
        written, left_open, closed = None, "", False
        try:
            if how == "transcript":
                written = handover.from_transcript(session)
            if session.exit is Exit.RUNNING:
                # As it is now, in case it got busy again while the handover was written.
                now, open_ones = self.call_from_thread(
                    lambda: (self.sessions.get(session.session_id, session), self.open_ones()))
                try:
                    handover.close(now, open_ones)
                    closed = True
                except handover.HandoverError as error:
                    if written is None:
                        raise
                    left_open = str(error)
            if written is None:
                written = handover.write_itself(session)
        except (handover.HandoverError, summarize.SummaryError, OSError) as error:
            after = " It is closed now; Enter on it resumes it." if closed else ""
            self.call_from_thread(self.notify, f"{session.project} was not handed over: {str(error).rstrip('.')}."
                                               f"{after}", severity="error", timeout=15)
            return
        self.call_from_thread(self.handover_ready, session, written, left_open)

    def handover_ready(self, session: Session, written: str, left_open: str = "") -> None:
        self.open_sessions([handover.fresh(session, written)])
        if left_open:
            self.notify(f"Handed {session.project} over to a fresh session, but the old one is still open: "
                        f"{left_open}.", title="Handed over", severity="warning", timeout=15)
        else:
            self.notify(f"Handed {session.project} over to a fresh session.", title="Handed over", timeout=8)
        self.mascot.celebrate(session.project)

    # --- renaming ----------------------------------------------------------------------

    def action_rename(self) -> None:
        session = self.current()
        if session is None:
            return
        if session.cli != "claude":
            self.notify(f"Only Claude Code sessions can be renamed: {clis.label(session.cli)} has no /rename.",
                        severity="warning")
            return
        if teams.member_of(session) is not None:
            self.notify("Team members keep their names: the team finds them by it.", severity="warning")
            return
        if session.status is Status.BACKGROUND:
            self.notify("A background session renames itself: attach to it (B) and type /rename there.",
                        severity="warning")
            return

        def chosen(name: str | None) -> None:
            if not name or name == session.title:
                return
            if session.exit is Exit.RUNNING:  # renamed from outside, its next turn would write the old name back
                copy_text(self, rename.command(name))
                self.go_to_tab(session)
                self.notify(f"{session.project} is open, so it renames itself: /rename is on the clipboard, "
                            "paste it into its tab (Ctrl+V, Enter).", title="Rename", timeout=12)
            else:
                self.rename_closed(session, name)

        if session.exit is Exit.RUNNING:
            hint = Text("It is open, so it renames itself.\n", style=MUTED) + keys(
                ("Enter", "copy /rename and go to its tab, to paste"), ("Esc", "cancel"))
        else:
            hint = keys(("Enter", "rename it, in Claude Code's own list too"), ("Esc", "cancel"))
        self.push_screen(NamePrompt(f"Rename {session.project} · “{_clip(session.title, 60)}”", value=session.title,
                                    placeholder="a new name", hint=hint), chosen)

    @work(thread=True, group="rename")
    def rename_closed(self, session: Session, name: str) -> None:
        try:
            rename.rename(session, name)
        except OSError as error:
            self.call_from_thread(self.notify, f"{session.project} was not renamed: {error}", severity="error",
                                  timeout=10)
            return
        fresh = read_session(session.path) if session.path else None
        self.call_from_thread(self.renamed, fresh or replace(session, title=name))

    def renamed(self, session: Session) -> None:
        if session.session_id in self.sessions:
            self.sessions[session.session_id] = session
            self.update_row(session)
            self.update_detail()
        self.notify(f"Renamed to “{session.title}”.", timeout=5)

    def action_teams(self) -> None:
        """T: on a team's row or one of its members, that team's view; elsewhere the list of teams
        (to pick one, or make a new one)."""
        here = self.team_at()
        session = self.current()
        if here is None and session is not None and session.session_id in self.member_rows:
            here = self.team_states.get(self.member_rows[session.session_id][0])
        if here is not None:
            self.push_screen(TeamScreen(here.team, self.accent()))
            return
        saved = teams.load()
        if not saved:
            self.new_team()
            return

        def chosen(answer: tuple[str, str] | None) -> None:
            if not answer:
                return
            action, name = answer
            team = teams.load().get(name)
            if action == "new":
                self.new_team()
            elif team is not None:
                self.push_screen(TeamScreen(team, self.accent()))

        self.push_screen(TeamsDialog(saved), chosen)

    def new_team(self) -> None:
        here = self.current()
        if self.project_filter != ALL_PROJECTS and self.sessions:
            folder = next((s.cwd for s in self.sessions.values() if folder_key(s.cwd) == self.project_filter), "")
        else:
            folder = here.cwd if here is not None else ""

        def made(team: teams.Team | None) -> None:
            if team is None:
                return
            saved = teams.load()
            saved[team.name] = team
            teams.save(saved)
            self.open_team(team, teams.start(team, {}))  # the members first, the orchestrator last
            self.push_screen(TeamScreen(team, self.accent()))

        newest = sorted(self.sessions.values(), key=lambda s: s.last_active, reverse=True)
        folders = list(dict.fromkeys(s.cwd for s in newest if s.cwd))  # the folder box completes these
        self.push_screen(NewTeamDialog(folder or str(Path.home()), set(teams.load()), folders), made)

    def accent(self) -> str:
        return THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]

    # --- the dashboard as data, and its actions by a session's id --------------------------------

    def view(self) -> VM.View:
        """Everything the dashboard shows, as plain data (viewmodel), for anything built on it.
        Built afresh each time; cheap enough for every poll."""
        sessions = self.sessions
        return VM.build(sessions, hidden={sid for sid, s in sessions.items() if self.marks.is_hidden(s)},
                        pinned=set(self.marks.pinned), git=self.git, team_states=self.team_states,
                        tokens=self.session_tokens, limits=self.limits, armed=self.armed, queued=self.queued)

    ACTIONS = ("open", "focus", "handover", "continue_at_reset")

    def act(self, action: str, session_id: str, **args) -> str:
        """One of the list's actions on one session, by its id, through the same code paths as its
        key: open (Enter), focus (its tab), handover (h; how="self"|"transcript" skips the dialog),
        continue_at_reset (c, for this one session). Returns what happens, in a few words; raises
        KeyError for an unknown session or action. Runs on the app's own thread."""
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"no session {session_id}")
        if action == "open":
            if session.status is Status.RUNNING:
                self.go_to_tab(session)
                return "going to its tab"
            if time.monotonic() - self.opening.get(session_id, -OPENING_SECONDS) < OPENING_SECONDS:
                return "already opening"
            self.open_sessions([self.launch_for(session)])
            return "opening"
        if action == "focus":
            if session.exit is not Exit.RUNNING:
                return "not open"
            self.go_to_tab(session)
            return "going to its tab"
        if action == "handover":
            how = args.get("how")
            self.open_handover(session, how if how in ("self", "transcript") else None)
            return "handing over" if how in ("self", "transcript") else "asking how"
        if action == "continue_at_reset":
            if not at_limit(session) or session.status is Status.BACKGROUND:
                return "not at a limit"
            self.armed[session_id] = session.resets or datetime.now()
            self.save_armed()
            self.update_row(session)
            self.update_detail()
            return f"continues at {lifts(self.armed[session_id])}"
        raise KeyError(f"no action {action}")

    # --- teams in the list ----------------------------------------------------------------

    def poll_teams(self) -> None:
        self.read_teams(list(self.sessions.values()))  # listed here: the poll's thread must not walk the dict

    @work(thread=True, exclusive=True, group="teams")
    def read_teams(self, listed: list[Session]) -> None:
        """The teams with a member in the list, open or not, each member's session and what was said:
        only what the transcripts gained since is read. A team whose sessions are all older than the
        list is not read at all."""
        live = teams.live_names()
        states = {}
        for name, team in teams.load().items():
            found = teams.member_sessions(team, listed, live)
            if any(s is not None for s in found.values()):
                states[name] = teamtree.TeamState(team, found, teams.conversation(team, found, live))
        self.call_from_thread(self.show_teams, states)

    def show_teams(self, states: dict[str, teamtree.TeamState]) -> None:
        now = time.monotonic()
        for name, state in states.items():
            said = {(m.sender, m.receiver, m.text) for m in state.said}
            seen = self.team_seen.get(name)
            new = [(m, now) for m in state.between_members() if (m.sender, m.receiver, m.text) not in (seen or ())]
            if seen is not None and new:  # the first look only takes note: history does not fly
                self.team_flights.setdefault(name, []).extend(new)
            self.team_seen[name] = said
        if states == self.team_states:
            return
        self.team_states = states
        self.sync_rows({s.session_id for state in states.values() for s in state.sessions.values() if s is not None})

    def fly(self, now: float) -> None:
        """Messages in flight along a team's wires, in the preview of its row."""
        shown = self.team_at()
        for name in list(self.team_flights):
            flying = [f for f in self.team_flights[name] if now - f[1] < teamtree.FLIGHT_SECONDS]
            if shown is not None and shown.team.name == name:
                self.show_team(shown)  # once more after the last has landed, to clear it
            if flying:
                self.team_flights[name] = flying
            else:
                del self.team_flights[name]

    def member_look(self, session: Session | None) -> tuple[str, str]:
        """What a member is doing, in the list's words and colours."""
        session = self.sessions.get(session.session_id, session) if session is not None else None
        if session is None:
            return "not started", MUTED
        if session.exit is not Exit.RUNNING:
            return STATUS_LABEL[session.status]
        label, style, rank = state_of(session)
        glyph = TEAM_GLYPHS.get(rank, ("●", ""))[0]
        return f"{glyph} {label}", style

    def show_team(self, state: teamtree.TeamState) -> None:
        """The preview of a team's row: the lead and each member on its wire, with the messages along
        it, and the newest messages beside it."""
        colours = team_colours(state.team, self.accent())
        looks = {name: self.member_look(session) for name, session in state.sessions.items()}
        # Narrower than this, the tree takes the facts column's room too: the latest word on each
        # wire says much of what the list of messages would.
        wide = self.size.width >= TEAM_MESSAGES_FROM
        self.inspector.set_class(not wide, "team-only")
        width = max(30, (self.detail.content_size.width if wide else self.inspector.content_size.width - 1) or 70)
        head = teamtree.head(state, state.tokens(self.session_tokens), self.accent())
        hint = self.team_keys(state)
        goal = Text.assemble(("Goal  ", MUTED), (teamtree.one_line(state.team.goal), TEXT))
        goal.truncate(width, overflow="ellipsis")
        lines = [head, goal, *teamtree.tree(state, width, looks, colours, self.team_flights.get(state.team.name, ()),
                                            time.monotonic())]
        if not self.team_open(state.team.name):
            lines.insert(2, Text(f"Closed. Enter starts it: each member resumed as itself, {state.team.lead.name} "
                                 "last.", style=AMBER))
        if not wide:  # no room beside it: its keys under it
            hint.truncate(width, overflow="ellipsis")
            self.detail.update(Text("\n").join([*lines, hint]))
            return
        self.detail.update(Text("\n").join(lines))
        room = max(2, (self.inspector.content_size.height or 8) - 4)
        self.meta.update(Group(Text("MESSAGES", style=f"bold {MUTED}"),
                               teamtree.latest(state, colours, room, max(20, (self.meta.content_size.width or 44) - 2)),
                               Text(""), hint))

    def team_keys(self, state: teamtree.TeamState) -> Text:
        """The keys of a team's row as it stands: closed, folded or unfolded."""
        name = state.team.name
        if not self.team_open(name):
            return keys(("Enter", "start the team"), ("e", "members"), ("Space", "tick"), ("a", "add"),
                        ("x", "hide"), ("T", "team view"))
        unfolded = name in self.unfolded
        return keys(("Enter", "team view" if unfolded else "members"), ("e", "fold" if unfolded else "unfold"),
                    ("s", "start the rest"), ("a", "add"), ("x", "close"), ("T", "team view"))

    def member_messages(self, session: Session, room: int) -> Text:
        """'3 in · 2 out · ← lead 2m: Heads-up…' for a member's own preview."""
        name, member = self.member_rows[session.session_id]
        state = self.team_states[name]
        mine = [m for m in state.said if member in (m.sender, m.receiver)]
        got = sum(m.receiver == member for m in mine)
        line = Text(f"{got} in · {len(mine) - got} out", style=TEXT)
        if mine:
            line.append(f"  ·  {ago(mine[-1].at)} ago  ", style=MUTED)
            line.append_text(teamtree.word(mine[-1], member, team_colours(state.team, self.accent())))
        line.truncate(room, overflow="ellipsis")
        return line

    def toggle_folded(self, name: str) -> None:
        self.unfolded ^= {name}
        self.refresh_rows()

    def toggle_team_selected(self, name: str) -> None:
        """Tick a closed team's members, or untick them all when they are all ticked."""
        closed = {s.session_id for _, s in self.team_members(name) if s.status is not Status.RUNNING}
        if not closed:
            return
        self.selected = self.selected - closed if closed <= self.selected else self.selected | closed
        self.sync_rows(closed)
        self.update_status()

    # --- a team's row: its keys -----------------------------------------------------------------

    def start_team(self, state: teamtree.TeamState) -> None:
        """Every member not open: a closed one resumed as itself, one never started new; the lead last."""
        sessions = {name: self.sessions.get(s.session_id, s) if s is not None else None
                    for name, s in state.sessions.items()}
        now = time.monotonic()
        launches = [launch for launch in teams.start(state.team, sessions)
                    if not launch.session_id or now - self.opening.get(launch.session_id, -OPENING_SECONDS)
                    >= OPENING_SECONDS]
        if not launches:
            self.notify(f"Every member of {state.team.name} is open already, or opening.", timeout=3)
            return
        self.open_team(state.team, launches)

    def add_to_team(self, state: teamtree.TeamState) -> None:
        """New members for a team: at work, each opens and introduces itself to the lead; closed, they
        join it and start with it."""
        at_work = self.team_open(state.team.name)

        def add(text: str | None) -> None:
            new, note = teams.add_members(state.team, text or "")
            if note:
                self.notify(note[0].upper() + note[1:] + ".", severity="warning", timeout=8)
            if not new:
                return
            names = ", ".join(m.name for m in new)
            if at_work:
                team = teams.load().get(state.team.name, state.team)
                self.open_team(team, teams.joining(team, new))
            else:
                self.notify(f"{names} joined {state.team.name}: they start with the team (Enter on its row).",
                            timeout=6)
            self.poll_teams()

        self.push_screen(LineDialog(ADD_QUESTION, ADD_EXAMPLE, enter="add them"), add)

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """A team's sessions: in tabs, or for a team whose members run in the background, its members
        as background sessions first and then the lead's tab, once they are up."""
        quiet = [launch for launch in launches if teams.in_background(team, launch)]
        tabs = [launch for launch in launches if launch not in quiet]
        if quiet:
            self.start_in_background(team, quiet, tabs)
        elif tabs:
            self.open_sessions(tabs)

    @work(thread=True, group="team-background")
    def start_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        """claude --bg for each (a few seconds each), then the tabs: the lead finds its members up."""
        started, failed = [], []
        for launch in launches:
            try:
                run_in_background(teams.background_start(team, launch))
                started.append(launch.title.partition(": ")[2])
            except OSError as error:
                failed.append(f"{launch.title.partition(': ')[2]}: {error}")
        if started:
            self.call_from_thread(self.notify, f"Started {', '.join(started)} of {team.name} in the background. "
                                               "B lists them; T shows the team.", timeout=6)
        if failed:
            self.call_from_thread(self.notify, "Could not start in the background: " + "; ".join(failed),
                                  severity="warning", timeout=12)
        if then:
            self.call_from_thread(self.open_sessions, then)

    def close_team(self, state: teamtree.TeamState) -> None:
        """x, twice: every open member's tab closes, as on /exit; the sessions stay (s starts them)."""
        sessions = {name: self.sessions.get(s.session_id, s) if s is not None else None
                    for name, s in state.sessions.items()}
        note = closing_note(state.team, sessions)
        if note is None:
            self.notify("No member is open.", timeout=3)
            return
        if self.closing_team != state.team.name:
            self.closing_team = state.team.name
            self.notify(note, timeout=6)
            self.set_timer(6, lambda: setattr(self, "closing_team", None))
            return
        self.closing_team = None
        self.close_team_tabs(open_names(state.team, sessions))

    @work(thread=True, group="team-close")
    def close_team_tabs(self, names: list[str]) -> None:
        said, severity = close_tabs(names)
        self.call_from_thread(self.notify, said, severity=severity, timeout=8 if severity == "warning" else 4)

    def action_new_sessions(self) -> None:
        found = newsessions.projects(list(self.sessions.values()))
        ticked = {self.project_filter} if self.project_filter != ALL_PROJECTS else set()

        def start(request: newsessions.Request | None) -> None:
            if request and request.folders:
                self.open_sessions(newsessions.launches(request.folders, request.cli, request.message))

        self.push_screen(newsessions.NewSessions(found, ticked), start)

    def action_settings(self) -> None:
        before = clis.enabled()

        def save(chosen: list[str] | None) -> None:
            if chosen is None:
                return
            after = clis.set_enabled(chosen)
            if after != before:
                self.notify("Listing sessions from " + ", ".join(clis.label(n) for n in after) + ".", timeout=4)
                self.reload_sessions()

        self.push_screen(CliSettings(), save)

    @work(thread=True, exclusive=True, group="reload")
    def reload_sessions(self) -> None:
        sessions = find_sessions(self.days, self.include_all, quick=True)
        self.call_from_thread(self.apply_reload, sessions)
        self.call_from_thread(self.refresh_tokens)  # the newly listed CLIs count too

    def apply_reload(self, sessions: list[Session]) -> None:
        self.sessions = {s.session_id: s for s in sessions}
        self.selected &= self.sessions.keys()
        self.preselected &= self.sessions.keys()
        self.expanded &= self.sessions.keys()
        self.refresh_rows()
        self.update_dashboard()
        self.poll_live()  # the quick read misses background sessions

    def action_background(self) -> None:
        self.load_background()

    @work(thread=True, exclusive=True, group="background")
    def load_background(self) -> None:
        self.call_from_thread(self.show_background, background.jobs())

    def show_background(self, found: list[background.Job] | None) -> None:
        if found is None:
            self.notify("`claude agents --json` did not answer, so background sessions cannot be listed.",
                        severity="warning")
            return

        def attach(job: background.Job | None) -> None:
            if job is not None:
                self.open_sessions([Launch(title=_clip(f"{job.project}: {job.name}", 48), cwd=job.cwd,
                                           mode=Mode.ATTACH, session_id=job.id)])

        self.push_screen(background.BackgroundDialog(found), attach)

    @work(thread=True, exclusive=True, group="usage")
    def refresh_usage(self) -> None:
        limits = usage.read_all()  # Codex's too, when it is turned on
        usage.record(limits)  # the stats chart these readings and forecast from them
        self.call_from_thread(self.show_usage, limits)

    def show_usage(self, limits: list[usage.Limit]) -> None:
        self.limits = limits
        self.topbar.show_usage(limits)
        full = [lim for lim in limits if lim.percent >= 85]
        if full:  # Bit mentions it once per reading
            worst = max(full, key=lambda lim: lim.percent)
            self.mascot.say(f"Careful: {worst.label} limit at {worst.percent}%", RED, seconds=6)

    @work(thread=True)
    def start_background(self, launch: Launch) -> None:
        try:
            message, ok = run_in_background(launch), True
        except OSError as error:
            message, ok = str(error), False
        self.call_from_thread(self.notify, message, severity="information" if ok else "error", timeout=8)
        if ok:
            time.sleep(2)  # give Claude a moment to register it, then show it as background
            self.poll_live(full=True)

    def place_mascot(self) -> None:
        """Bit walks on the preview's top edge; without Bit, a blank row separates the two."""
        if not hasattr(self, "inspector"):
            return  # a resize before the dashboard is mounted
        shown = not self.mascot.has_class("hidden") and not self.dashboard.has_class("short")
        self.inspector.styles.margin = (0 if shown else 1, 0, 0, 0)

    def action_toggle_mascot(self) -> None:
        hidden = not self.mascot.has_class("hidden")
        self.mascot.set_class(hidden, "hidden")
        self.place_mascot()
        saved = read_json("config.json")
        saved = saved if isinstance(saved, dict) else {}
        saved["mascot"] = not hidden
        try:
            write_json("config.json", saved, indent=2)
        except OSError:
            pass

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Bit's eyes follow the mouse pointer, wherever it is over the dashboard."""
        mascot = getattr(self, "mascot", None)
        if mascot is not None and mascot.display and mascot.region.width:
            mascot.point_at((event.screen_x - mascot.region.x) * 2)

    def action_cycle_theme(self) -> None:
        names = list(THEMES)
        self.theme = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else DEFAULT_THEME
        self.refresh_rows()  # accent-coloured cells follow the theme
        self.mascot.accent = THEMES[self.theme][1]
        saved = read_json("config.json")
        saved = saved if isinstance(saved, dict) else {}
        saved["theme"] = self.theme
        try:
            write_json("config.json", saved, indent=2)
        except OSError:
            pass
        self.notify(f"Theme: {THEMES[self.theme][0]}", timeout=2)

    def action_toggle(self) -> None:
        team = self.team_at()
        if team is not None:  # a team's row: its closed members, all at once
            self.toggle_team_selected(team.team.name)
            self.table.action_cursor_down()
            return
        session = self.current()
        if session is None or session.status is Status.RUNNING:
            return
        self.toggle_selected(session.session_id)
        self.table.action_cursor_down()

    def toggle_selected(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None or session.status is Status.RUNNING:  # open: nothing to tick
            return
        self.selected ^= {session_id}
        self.update_row(session)
        self.update_status()

    def action_cycle_mode(self) -> None:
        session = self.current()
        if session is None or session.status is Status.RUNNING:
            return
        cli = clis.CLIS.get(session.cli)
        modes = [m for m in MODES if m is not Mode.FORK or (cli and cli.can_fork)]
        current = self.mode(session)
        self.modes[session.session_id] = modes[(modes.index(current) + 1) % len(modes)] if current in modes \
            else modes[0]
        self.update_row(session)
        self.update_detail()

    def action_toggle_all(self) -> None:
        team = self.team_at()
        if team is not None:  # a team's row: new members for it
            self.add_to_team(team)
            return
        ids = {s.session_id for s in self.visible() if s.status is not Status.RUNNING}
        if ids <= self.selected:
            self.selected -= ids
        else:
            self.selected |= ids
        for session in self.visible():
            self.update_row(session)
        self.update_status()

    def action_filter(self) -> None:
        box = self.filter_box
        box.add_class("visible")
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is not self.filter_box:
            return
        self.filter_text = event.value
        self.refresh_rows()
        if len(event.value.strip()) >= search.SHORTEST:
            self.index_search()  # what the open sessions said since: only that is read

    @work(thread=True, exclusive=True, group="search")
    def index_search(self) -> None:
        if self.search.update(list(self.sessions.values())):
            self.call_from_thread(self.search_ready)

    def search_ready(self) -> None:
        self.found = ("", -1, set())
        self.search_version += 1
        if len(self.filter_text.strip()) >= search.SHORTEST:
            self.refresh_rows()

    def said_in(self) -> set[str]:
        """The sessions in whose conversation the filter's words were said; worked out once
        per filter and index, since the list asks many times a second."""
        needle = self.filter_text
        if self.found[:2] != (needle, self.search_version):
            self.found = (needle, self.search_version, self.search.find(needle, self.sessions.keys()))
        return self.found[2]

    def action_back(self) -> None:
        """Esc: the text filter cleared, else a card's, else quit."""
        box = self.filter_box
        if box.has_class("visible"):
            box.value = ""
            box.remove_class("visible")
            self.table.focus()
        elif self.card_filter:
            self.action_card(self.card_filter)
        else:
            self.exit(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # With a dialog or the team view on top, the dashboard's keys belong to it. Enter is a
        # priority binding, so it would otherwise reach past them.
        on_top = len(self.screen_stack) > 1
        if action == "open" and (on_top or self.focused is self.projects):
            return False
        # Only the dashboard's own keys: Textual's (Tab between boxes, Ctrl+P) stay on.
        if on_top and action in DASHBOARD_ACTIONS:
            return False
        if action == "continue_at_reset":  # shown only when there is something to continue
            return any(at_limit(s) for s in self.sessions.values())
        if self.live_mode and action in PICKER_ONLY:
            return False
        if not on_top and action in IDLE_ON and self.footer_context()[0] in IDLE_ON[action]:
            return None  # nothing to do on this row: the key stays in the footer, dimmed
        if not on_top and action == "next_waiting" and not self.footer_context()[2]:
            return None  # nobody waits for you
        return True

    # --- the footer follows the row ---------------------------------------------------------------

    def footer_context(self) -> tuple:
        """What the footer's words and dimmed keys depend on: the kind of row highlighted ("team",
        "team-closed", "open", "member-open", "closed", "member", "none", or "ticked" while any are
        ticked), whether a team is unfolded, whether anyone waits for you."""
        if not hasattr(self, "table") or not self.table.row_count:
            return ("none", False, False)
        waiting = any(s.exit is Exit.RUNNING and state_of(s)[2] < 2 for s in self.sessions.values())
        team = self.team_at()
        if team is not None:
            name = team.team.name
            return ("team" if self.team_open(name) else "team-closed", name in self.unfolded, waiting)
        session = self.current()
        if session is None:
            return ("none", False, waiting)
        if self.selected:
            return ("ticked", False, waiting)
        member = "member-" if session.session_id in self.member_rows else ""
        return (member + ("open" if session.status is Status.RUNNING else "closed"), False, waiting)

    def refresh_footer(self) -> None:
        """Make the footer again for the row at hand, only when what it shows would change."""
        context = self.footer_context()
        if context != self.footer_was and self.screen_stack:
            self.footer_was = context
            self.refresh_bindings()

    def footer_words(self, action: str) -> str | None:
        """What a key does on the row at hand, where it is not what its own name says."""
        kind, unfolded, _ = self.footer_context()
        if action == "open":
            if self.selected:
                return f"Open {len(self.selected)}"
            return {"team": "Team view" if unfolded else "Members", "team-closed": "Start team",
                    "open": "Its tab", "member-open": "Its tab"}.get(kind)
        if action == "summarize":
            return {"team": "Start rest", "team-closed": "Start team"}.get(kind)
        if action == "hide":
            return {"team": "Close team", "team-closed": "Hide team"}.get(kind)
        if action == "toggle" and kind == "team-closed":
            return "Tick team"
        return None

    @staticmethod
    def footer_group(action: str) -> str | None:
        """The ? screen's group of a key, which the footer keeps together. ? itself belongs to none:
        it stands beside Enter, which the footer puts first."""
        return None if action == "keys" else KEY_GROUPS.get(action)

    def action_save_workspace(self) -> None:
        chosen = [s for s in self.sessions.values() if s.session_id in self.selected]
        if not chosen:
            self.notify("Tick the sessions for the workspace first (Space).", severity="warning")
            return

        def save(name: str | None) -> None:
            if not name or not workspaces.clean_name(name):
                return
            saved = workspaces.load()
            workspace = workspaces.from_sessions(name, chosen, self.modes)
            saved[workspace.name] = workspace
            workspaces.save(saved)
            self.notify(f"Saved workspace '{workspace.name}': {workspace.summary}")

        what = "this session's folder" if len(chosen) == 1 else f"these {len(chosen)} sessions' folders"
        self.push_screen(NamePrompt(f"Name for a workspace with {what}"), save)

    def action_open_workspace(self) -> None:
        saved = workspaces.load()
        if not saved:
            self.notify("No workspaces yet. Tick sessions and press w to save one.", severity="warning")
            return

        def open_it(name: str | None) -> None:
            workspace = workspaces.load().get(name) if name else None
            if workspace is None:
                return
            newest_first = sorted(self.sessions.values(), key=lambda s: s.last_active, reverse=True)
            launches, notes = workspaces.resolve(workspace, newest_first)
            if launches:
                self.open_sessions(launches)
            else:
                self.notify("\n".join(notes) or "Nothing to open.", severity="warning")

        self.push_screen(WorkspaceList(saved), open_it)

    # --- live view ------------------------------------------------------------------

    def action_toggle_live(self) -> None:
        self.live_mode = not self.live_mode
        self.setup_columns()
        self.fit_sidebar()  # the live view's other columns are wider
        self.refresh_rows()
        self.refresh_bindings()

    def poll_tick(self) -> None:
        if not self.screen_stack:  # the app is closing: the timer fired once more (seen on CI)
            return
        self.polls += 1
        self.poll_live(full=self.polls % FULL_POLL_EVERY == 0)
        self.poll_teams()

    def action_next_waiting(self) -> None:
        """Go to the next open session waiting for you, one after another."""
        waiting = sorted((s for s in self.sessions.values()
                          if s.exit is Exit.RUNNING and state_of(s)[2] < 2),
                         key=lambda s: (s.project.casefold(), s.session_id))
        if not waiting:
            self.notify("No session is waiting for you.", timeout=3)
            return
        session = waiting[self.waiting_turn % len(waiting)]
        self.waiting_turn += 1
        team = self.member_rows.get(session.session_id, ("", ""))[0]
        if team and team not in self.unfolded:  # under a folded team's row: unfold it to show the member
            self.toggle_folded(team)
        if session.session_id in self.table.rows:
            self.table.move_cursor(row=self.table.get_row_index(session.session_id))
        self.go_to_tab(session)

    # --- usage limits ---------------------------------------------------------------

    def action_continue_at_reset(self) -> None:
        """c: the sessions stopped at a usage limit continue now (after you have signed in with another
        account), or by themselves when the limit lifts, or no longer by themselves."""
        stopped = [s for s in self.sessions.values() if at_limit(s) and s.status is not Status.BACKGROUND]
        if not stopped:
            self.notify("No session is stopped at a usage limit.", timeout=4)
            return
        soon = min(s.resets or datetime.now() for s in stopped)
        when = "now" if soon <= datetime.now() else f"at {lifts(soon)}"
        # The plan as last read (/usage, every few minutes): room on it means the limit no longer stands
        # in the way (another account, or it lifted), and continuing now comes first.
        session_limit = next((limit for limit in self.limits if limit.label == "session"), None)
        plan = ("Your plan now: " + " · ".join(f"{limit.label} {limit.percent}%" for limit in self.limits
                                                 if limit.label in ("session", "week"))) if self.limits else ""
        room = session_limit is not None and session_limit.percent < 95

        def chosen(answer: str | None) -> None:
            if answer == "now":
                self.armed = {sid: moment for sid, moment in self.armed.items() if sid not in {s.session_id for s in stopped}}
                self.save_armed()
                self.continue_these(stopped)
                return
            if answer == "lift":
                self.armed = {s.session_id: s.resets or datetime.now() for s in stopped}
                self.notify(f"{len(stopped)} {'session continues' if len(stopped) == 1 else 'sessions continue'} "
                            f"{when}, when the limit lifts. Leave the dashboard open.", timeout=8)
            elif answer == "off":
                self.armed.clear()
                self.notify("Sessions at a limit will not continue by themselves.", timeout=5)
            else:
                return
            self.save_armed()
            for session in stopped:
                self.update_row(session)
            self.update_detail()

        self.push_screen(LimitDialog(len(stopped), when, all(s.session_id in self.armed for s in stopped), plan,
                                     room), chosen)

    def continue_these(self, stopped: list[Session]) -> None:
        """Each goes on where it stopped: a closed one in a new tab, an open one with the prompt
        typed into its own tab."""
        closed = []
        for session in stopped:
            if session.exit is Exit.RUNNING:
                self.continue_open(session)
            else:
                closed.append(replace(self.launch_for(session), mode=Mode.AUTO, prompt=continue_prompt(Status.LIMIT)))
        if closed:
            self.open_sessions(closed)
        self.mascot.say(f"{len(stopped)} back at work", self.accent(), seconds=6)

    def save_armed(self) -> None:
        saved = read_json("config.json")
        saved = saved if isinstance(saved, dict) else {}
        saved["continue_at_reset"] = {sid: moment.isoformat() for sid, moment in self.armed.items()}
        try:
            write_json("config.json", saved, indent=2)
        except OSError:
            pass

    def continue_due(self) -> None:
        """Continue the sessions whose limit has lifted: a closed one in a new tab, an open one by
        closing its tab and resuming it. One gone on by hand meanwhile is left alone."""
        now = datetime.now()
        due = [sid for sid, moment in self.armed.items() if moment + LIMIT_MARGIN <= now]
        if not due:
            return
        lifted = []
        for sid in due:
            del self.armed[sid]
            session = self.sessions.get(sid)
            if session is not None and at_limit(session):
                lifted.append(session)
        self.save_armed()
        if lifted:
            self.continue_these(lifted)

    @work(thread=True, group="limits")
    def continue_open(self, session: Session) -> None:
        """The continue prompt goes into its tab, as if you typed it; where that cannot be done,
        the tab is closed and the session resumed with it."""
        if session.live_state in handover.IDLE and typein.type_into(session.session_id,
                                                                     continue_prompt(Status.LIMIT)):
            return
        open_ones = self.call_from_thread(self.open_ones)
        try:
            handover.close(session, open_ones, again="Type “continue” in it yourself")
        except handover.HandoverError as error:
            self.call_from_thread(self.notify, f"{session.project} did not continue by itself: {error}.",
                                  severity="warning", timeout=15)
            return
        launch = replace(self.launch_for(session), mode=Mode.AUTO, prompt=continue_prompt(Status.LIMIT))
        self.call_from_thread(self.open_sessions, [launch])

    def check_limits(self) -> None:
        """Forget the sessions gone on from a limit; once per session, Bit says which ones a
        usage limit stopped and that c continues them."""
        at = {s.session_id for s in self.sessions.values() if at_limit(s)}
        self.told_limit &= at
        if self.sessions and not self.armed.keys() <= at:
            self.armed = {sid: moment for sid, moment in self.armed.items() if sid in at}
            self.save_armed()
        if at != self.limited and self.screen_stack:  # not while the app shuts down: no screen then
            self.limited = at
            self.refresh_bindings()  # c is in the footer only while there is something to continue
        stopped = [self.sessions[sid] for sid in at - self.told_limit - self.armed.keys()
                   if self.sessions[sid].status is not Status.BACKGROUND]
        if not stopped:
            return
        self.told_limit.update(s.session_id for s in stopped)
        names = ", ".join(sorted({s.project for s in stopped})[:3])
        soon = min((s.resets for s in stopped if s.resets), default=None)
        if soon and soon > datetime.now():
            self.mascot.say(f"{names}: limit until {lifts(soon)}. c continues then", AMBER, seconds=10)
        else:
            self.mascot.say(f"{names}: the limit has lifted. c continues now", AMBER, seconds=10)

    # --- hiding and pinning -----------------------------------------------------------

    def chosen(self) -> list[Session]:
        """The ticked sessions, or else the highlighted one."""
        if self.selected:
            return [self.sessions[sid] for sid in self.selected if sid in self.sessions]
        session = self.current()
        return [session] if session is not None else []

    def action_hide(self) -> None:
        """x: the ticked sessions, or the highlighted one, out of sight; in the sidebar, the whole
        project. On one already hidden (listed with X), back in sight."""
        if self.focused is self.projects:
            self.hide_project()
            return
        team = self.team_at() if not self.selected else None
        if team is not None and self.team_open(team.team.name):  # an open team's row: close it (twice)
            self.close_team(team)
            return
        chosen = ([s for _, s in self.team_members(team.team.name)] if team is not None else self.chosen())
        chosen = [s for s in chosen if s.exit is not Exit.RUNNING]
        if not chosen:
            self.notify("Open sessions always show: close one to hide it.", timeout=4)
            return
        if all(self.marks.is_hidden(s) for s in chosen):
            for session in chosen:
                self.marks.hidden.discard(session.session_id)
                self.marks.projects.discard(folder_key(session.cwd))  # its project comes back with it
            self.notify(f"Back in sight: {self.counted(chosen)}.", timeout=4)
        else:
            self.marks.hidden |= {s.session_id for s in chosen}
            self.selected -= {s.session_id for s in chosen}
            self.notify(f"Hid {self.counted(chosen)}. Nothing is deleted: X shows hidden ones, and x on one "
                        "brings it back.", timeout=6)
        self.marks.save()
        self.marked()

    def hide_project(self) -> None:
        key = self.projects.highlighted_id()
        if not key or key == ALL_PROJECTS:
            self.notify("Pick a project in the sidebar to hide it, or a session in the list.", timeout=4)
            return
        sessions = [s for s in self.sessions.values() if folder_key(s.cwd) == key]
        name = sessions[0].project if sessions else key
        if key in self.marks.projects:
            self.marks.projects.discard(key)
            self.notify(f"The project {name} is back in sight.", timeout=4)
        else:
            self.marks.projects.add(key)
            self.notify(f"Hid the project {name} ({self.counted(sessions)}; open ones still show). X shows it "
                        "again, and x on it brings it back.", timeout=6)
        self.marks.save()
        self.marked()

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.marked()

    def action_pin(self) -> None:
        """f: the ticked sessions, or the highlighted one, pinned above the rest; again, unpinned."""
        chosen = self.chosen()
        if not chosen:
            return
        if all(self.marks.is_pinned(s) for s in chosen):
            self.marks.pinned -= {s.session_id for s in chosen}
            self.notify(f"Unpinned {self.counted(chosen)}.", timeout=3)
        else:
            self.marks.pinned |= {s.session_id for s in chosen}
            self.notify(f"Pinned {self.counted(chosen)} above the rest.", timeout=3)
        self.marks.save()
        self.marked()

    @staticmethod
    def counted(sessions: list[Session]) -> str:
        return f"“{sessions[0].title}”" if len(sessions) == 1 else f"{len(sessions)} sessions"

    def marked(self) -> None:
        """Hidden or pinned ones changed: the list, the counts and the sidebar follow."""
        self.refresh_rows()
        self.update_dashboard()

    def action_keys(self) -> None:
        def run(action: str | None) -> None:
            if action:
                self.call_later(self.run_action, action)
        self.push_screen(KeysScreen(), run)

    def poll_live(self, full: bool = True) -> None:
        """Bring open sessions up to date.

        A full poll asks `claude agents --json`. A quick one reads the pid files, which
        is nearly free but misses background sessions: those are carried over from the
        last full poll. Only full polls decide that a session has closed.

        Polls replace one another, so a quick one is not started while a full one is on its
        way: it would cancel it before it ran (seen on CI), and a session just opened would
        show as open only at the next full poll, 16 s on.
        """
        if full:
            self.full_poll_due = True
        elif self.full_poll_due:
            return
        self.read_live(full)

    @work(thread=True, exclusive=True, group="live")
    def read_live(self, full: bool) -> None:
        known = dict(self.sessions)
        if full:
            live = live_sessions()
            self.background_live = {sid: e for sid, e in live.items() if e.get("kind") == "background"}
            idle = sum(bool(e.get("pid")) and str(e.get("state") or e.get("status")).lower() in background.IDLE
                       for e in self.background_live.values())
            if idle and not self.told_idle:
                self.told_idle = True
                self.call_from_thread(self.mascot.say, f"{idle} background session{'s sit' if idle > 1 else ' sits'}"
                                      " idle. B tidies up.", seconds=6)
            watched = set(live) | {sid for sid, s in known.items() if s.exit is Exit.RUNNING}
        else:
            live = {e["sessionId"]: e for e in _registry_sessions()} | self.background_live
            watched = set(live)
        updates: dict[str, Session] = {}
        for sid in watched:
            old = known.get(sid)
            path = old.path if old else transcript_path(sid)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if old is not None and old.size == size and sid in live:
                fresh = replace(old)  # transcript unchanged: only the live state can differ
            else:
                fresh = read_session(path)  # new activity, a new session, or one that closed
            if fresh is None:
                continue
            mark_live(fresh, live.get(sid))
            updates[sid] = fresh
        self.call_from_thread(self.apply_live_updates, updates, full)

    def apply_live_updates(self, updates: dict[str, Session], full: bool = False) -> None:
        if full:
            self.full_poll_due = False
        self.warn_crowded(updates.values())
        turned: list[Session] = []  # were working and now wait for you, or stopped at a limit
        for sid, fresh in updates.items():
            if sid in self.queued and (fresh.exit is not Exit.RUNNING or fresh.live_state in handover.IDLE):
                how = self.queued.pop(sid)  # it finished: the handover asked for with h
                self.notify(f"{fresh.project} finished: handing it over now.", title=f"Handing over {fresh.project}",
                            timeout=10)
                self.hand_over(fresh, how)
                continue
            old = self.sessions.get(sid)
            was = state_of(old)[2] if old and old.exit is Exit.RUNNING else None
            now = state_of(fresh)[2] if fresh.exit is Exit.RUNNING else None
            if was == 2 and at_limit(fresh):
                turned.append(fresh)
            if was == 2 and now in (0, 1):  # it was working, now it needs you
                turned.append(fresh)
                if self.live_mode:
                    self.bell()
                self.notify(f"{fresh.project}: {state_of(fresh)[0]}", timeout=10)
                if now == 1:
                    self.mascot.celebrate(fresh.project)  # done, waiting for your next message
                else:
                    self.mascot.alert(fresh.project)  # blocked: it needs an answer
        if turned:
            self.tell_desktop(turned)
        changed = {sid for sid, fresh in updates.items() if self.sessions.get(sid) != fresh}
        until = time.monotonic() + FLASH_SECONDS
        for sid, fresh in updates.items():
            old = self.sessions.get(sid)
            if old is not None and (old.live_state, old.exit, old.status) != (
                    fresh.live_state, fresh.exit, fresh.status):
                self.flash[sid] = until
        self.sessions.update(updates)
        # A session that turns out to be open was not left open at shutdown: untick it if
        # it was ticked automatically. Terminal sessions that are open can never be ticked.
        now_open = {sid for sid, s in updates.items() if s.exit is Exit.RUNNING}
        self.selected -= (now_open & self.preselected) | {
            sid for sid, s in updates.items() if s.status is Status.RUNNING}
        self.preselected -= now_open
        if changed:
            self.sync_rows(changed)
            self.update_dashboard()
            self.check_limits()

    def tell_desktop(self, turned: list[Session]) -> None:
        """A desktop notification too, for when the dashboard is not in front: Windows asks what
        is (toast.show); elsewhere the terminal reports focus."""
        if not toast.enabled() or (sys.platform != "win32" and self.app_focus):
            return
        if len(turned) > 1:
            title = f"{len(turned)} sessions want you back"
            body = ", ".join(f"{s.project} ({state_of(s)[0]})" for s in turned)
        elif at_limit(turned[0]):
            session = turned[0]
            title = f"{session.project} stopped at the usage limit"
            when = f"at {lifts(session.resets)}" if session.resets and session.resets > datetime.now() else "now"
            body = f"It lifts {when}. c in the dashboard continues it then."
        else:
            session = turned[0]
            title = f"{session.project}: {state_of(session)[0]}"
            body = session.title
        self.send_toast(title, body)

    @work(thread=True, group="toast")
    def send_toast(self, title: str, body: str) -> None:
        toast.show(title, body, dashboard=self.TITLE)

    @work(thread=True, exclusive=True, group="git")
    def refresh_git(self) -> None:
        states, failed = gitstate.read_all([s.cwd for s in self.sessions.values()])
        self.call_from_thread(self.show_git, states, failed)

    def show_git(self, states: dict[str, gitstate.GitState], failed: set[str] = frozenset()) -> None:
        # A folder git could not answer for keeps what was known, and is asked again soon.
        states = states | {key: state for key, state in self.git.items() if key in failed}
        if failed:
            self.set_timer(10, self.refresh_git)
        if states == self.git:
            return
        self.git = states
        self.update_dashboard()  # the sidebar's marks
        self.update_detail()  # the preview's Git row

    def action_summarize(self) -> None:
        team = self.team_at()
        if team is not None:  # a team's row: start every member not open
            self.start_team(team)
            return
        session = self.current()
        if session is None or session.session_id in self.summarizing:
            return
        self.summarizing.add(session.session_id)
        self.update_detail()
        self.run_summary(session)

    @work(thread=True)
    def run_summary(self, session: Session) -> None:
        try:
            entry, error = summarize.summarize(session), None
        except (summarize.SummaryError, OSError) as failure:
            entry, error = None, str(failure)
        self.call_from_thread(self.summary_done, session, entry, error)

    def summary_done(self, session: Session, entry: dict | None, error: str | None) -> None:
        self.summarizing.discard(session.session_id)
        if entry:
            self.summaries[session.session_id] = entry
        else:
            self.notify(f"Summary failed: {error}", severity="error", timeout=8)
        # By id: a live poll replaces an open session's object every few seconds.
        current = self.current()
        if current is not None and current.session_id == session.session_id:
            self.update_detail()

    def action_open(self) -> None:
        if isinstance(self.focused, Input):  # Enter in the filter box only confirms it
            self.table.focus()
            return
        chosen = [s for s in self.sessions.values() if s.session_id in self.selected]
        if not chosen:
            session = self.current()
            team = self.team_at() if session is None else None
            if team is not None:  # a team's row: closed, it starts; folded, it unfolds; else its view
                name = team.team.name
                if not self.team_open(name):
                    self.start_team(team)
                elif name not in self.unfolded:
                    self.toggle_folded(name)
                else:
                    self.push_screen(TeamScreen(team.team, self.accent()))
                return
            if session is None:
                self.notify("Select sessions with Space first.", severity="warning")
                return
            if session.status is Status.RUNNING:  # already open: go there
                self.go_to_tab(session)
                return
            chosen = [session]
        recent = {sid for sid, when in self.opening.items() if time.monotonic() - when < OPENING_SECONDS}
        chosen = [s for s in chosen if s.session_id not in recent]
        if not chosen:
            self.notify("Already opening: it shows as open in a moment.", severity="warning")
            return
        saved = teams.load()
        self.open_sessions([self.launch_for(s, saved) for s in teams.lead_last(chosen, saved)])

    def open_sessions(self, launches: list[Launch]) -> None:
        """Open sessions in new tabs beside the dashboard, which stays open."""
        leftover = open_tabs(launches)
        if leftover:
            # No terminal that can open tabs: close, and leave the commands to run by hand.
            self.exit(leftover)
            return
        ids = {launch.session_id for launch in launches}
        self.opening.update(dict.fromkeys(ids, time.monotonic()))
        self.selected -= ids
        for sid in ids & self.sessions.keys():
            self.update_row(self.sessions[sid])
        self.update_status()
        what = launches[0].title if len(launches) == 1 else f"{len(launches)} sessions"
        self.notify(f"Opened {what} in {'a new tab' if len(launches) == 1 else 'new tabs'}.", timeout=4)
        self.set_timer(5, lambda: self.poll_live(full=True))  # show them as open once started

    @work(thread=True, exclusive=True, group="focus")
    def go_to_tab(self, session: Session) -> None:
        """Switch to the Windows Terminal tab where this session is open."""
        found = focus.focus_tab(session.title)
        if found:
            self.call_from_thread(self.notify, f"Switched to {session.project}. Type your prompt there.",
                                  timeout=4)
        else:
            self.call_from_thread(
                self.notify, f"Could not find the tab of {session.project}. It may be open outside "
                             "Windows Terminal, or its tab has been renamed.", severity="warning", timeout=8)


# The dashboard's own keys, which a dialog or the team view on top keeps for itself.
DASHBOARD_ACTIONS = {binding.action for binding in SessionPicker.BINDINGS} - {
    "quit", "back", "cycle_theme", "toggle_mascot"}
# Keys that do nothing on a kind of row (footer_context): dimmed in the footer there, never hidden.
TEAM_ROWS = {"team", "team-closed"}
IDLE_ON = {
    "toggle": {"open", "member-open"},  # an open session cannot be ticked
    "cycle_mode": {"open", "member-open", *TEAM_ROWS},
    "hide": {"open", "member-open"},  # open sessions always show
    "pin": TEAM_ROWS,
    "prompt": TEAM_ROWS,
    "handover": TEAM_ROWS,
    "rename": {"member", "member-open", *TEAM_ROWS},  # members keep the names the team finds them by
}
