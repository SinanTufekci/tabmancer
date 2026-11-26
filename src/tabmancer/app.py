"""The dashboard: see every recent session, choose which to reopen, and how."""

from __future__ import annotations

import asyncio
import gc
import sys
import textwrap
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import ClassVar, cast

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import DataTable, Input, Label, OptionList, Rule, SelectionList, Sparkline, Static
from textual.widgets.data_table import ColumnKey, RowKey
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from . import (
    accounts,
    accountwords,
    actions,
    actor,
    agy,
    alerts,
    background,
    backgroundwords,
    browsers,
    claudeupdate,
    clis,
    engine,
    focus,
    footer,
    gitstate,
    handoverwords,
    limitwords,
    marks,
    newforms,
    newsessions,
    openings,
    picks,
    prompting,
    rename,
    rowwords,
    search,
    sessionwords,
    sixel,
    store,
    summarize,
    teamkeys,
    teams,
    teamtree,
    teamwatch,
    teamwords,
    toast,
    tokens,
    usage,
    workspaces,
    workspacewords,
    wrapped,
)
from . import viewmodel as VM
from .actions import (
    LIMIT_MARGIN,  # noqa: F401  (app.LIMIT_MARGIN, as before)
    close_tabs,
)
from .actions import clip as _clip
from .clisettings import CliSettings
from .config import (  # NOT_SAVED: merge_json refused, said as is
    NOT_SAVED,
    UI_TRIES,
    Busy,
    merge_json,
    read_json,
)
from .dialogs import frame, title
from .engine import (  # the timers' schedule (engine.SCHEDULE)
    ACCOUNTS_SECONDS,
    ACTOR_SECONDS,
    DUE_SECONDS,
    GIT_SECONDS,
    LIVE_REFRESH_SECONDS,
    TOKENS_SECONDS,
    UPDATES_SECONDS,
    USAGE_SECONDS,
    WRAPPED_AFTER,
    WRAPPED_SECONDS,
)
from .handoverview import HandoverDialog
from .keysview import KEYS, KeysScreen
from .launch import Launch, Mode, default_mode, open_tabs, run_in_background, terminal_kind
from .mascot import Mascot, way_of
from .safework import work
from .sessions import (
    UNREADABLE,
    Exit,
    Session,
    Status,
    _registry_sessions,
    find_sessions,
    live_sessions,
    nothing_found,
    read_session,
)
from .shortcuts import DASHBOARD_WINDOW
from .shutdowns import open_at_shutdown, recent_shutdowns
from .statsview import StatsScreen, copy_text
from .teamforms import ADD_ENTER, ADD_EXAMPLE, ADD_QUESTION
from .teamview import (
    JoinTeam,
    LineDialog,
    NewTeamDialog,
    TeamGraph,
    TeamHandoverDialog,
    TeamScreen,
    TeamsDialog,
    TellTeam,
)
from .teamview import colours as team_colours
from .theme import (
    AMBER,
    CYAN,
    DEFAULT_THEME,
    GREEN,
    MUTED,
    RED,
    SUNKEN,
    TEXT,
    THEMES,
    keys,
    keys_in,
    mix,
    use_saved_theme,
)
from .tokenchart import TokenChart
from .tokens import human
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
TONE = sessionwords.TONE
STATUS_LABEL = rowwords.STATUS_LABEL
BADGE, badge = rowwords.BADGE, rowwords.badge  # a loud state as a badge, its colour tinted in behind it
STATUS_HELP, EXIT_HELP, TOOLTIP_STATUS = VM.STATUS_HELP, VM.EXIT_HELP, VM.OUTCOME
MODE_LABEL = sessionwords.MODE_LABEL
# Raw states from `claude agents --json` -> label, style, sort rank (lowest first).
LIVE_STATES = {raw: (label, TONE[tone], rank) for raw, (label, tone, rank, _) in VM.LIVE.items()}
SPINNING = rowwords.SPINNING
# A team's members, counted on its row by what each does (VM.state_id), in this order (VM.TEAM_GLYPHS).
TEAM_GLYPHS = rowwords.TEAM_GLYPHS
MONITOR_AGAIN = 4.0  # seconds in which a second M for the same team does nothing: its window is on its way
TEAM_MESSAGES_FROM = 150  # columns from which a team's preview lists its messages beside the tree
# Columns and rows from which the preview stands beside the list, both at full height (V keeps it
# under the list): a maximized window, not a 120x30 or half-screen one.
SPLIT_FROM = (180, 40)
# Said instead of "remembered" when config.json is there but could not be read: writing the one key
# alone would have wiped the rest of it (merge_json refuses).
PANE_LEAST, PANE_MOST = 56, 90  # the preview's width there: the window's less 132, within these
PANE_TAKES = 132
# The sidebar's marks, said once under its projects (two lines of the sidebar's width).
LEGEND = Text.assemble(("● ", RED), ("your turn  ", MUTED), ("● ", GREEN), ("open\n", MUTED),
                       ("▲ ", AMBER), ("to continue  ", MUTED), ("± ", AMBER), ("↑ ", CYAN), ("git", MUTED))
FULL_POLL_EVERY = 8        # every 8th poll asks `claude agents --json` (background sessions too)
SPIN_SECONDS = 0.08
TIMER_THREADS = 64  # Windows: a thread for every timer at once, the team view's and dialogs' too
NAMELESS = ("This dashboard could not read its own start time, so it only shows sessions: it does not "
            "continue them at their reset, send desktop notes or run updates. Another tabmancer dashboard can.")
CHART_DAYS = 14
GROW_SECONDS = 0.8  # the token chart rises on load
FOCUS_CLICK = 0.5  # seconds after the window takes the focus in which a click only brought it to the front
KEEP_BACK_DAYS = 45  # counted once at the start, so the history has the days the 30-day cleanup takes next
SETTLE_AFTER = (5, 45)  # seconds after the list is shown: memory set aside from the collector (settle_memory)
STAR = rowwords.STAR  # Claude Code's turning star
EXPAND_WIDTH, TITLE_MIN = rowwords.TITLE_WIDEST, rowwords.TITLE_MIN  # the Title column's room (rowwords.title_room)
OTHER_COLUMNS = rowwords.OTHER_COLUMNS  # every column but the title (tests/test_layout.py holds these to it)
MODEL_COLUMN = rowwords.MODEL_COLUMN  # the Model column with its padding; on a narrow screen it is left empty
MODEL_ROOM = MODEL_COLUMN - 2  # ...its words
COLUMNS = ("sel", "project", "title", "status", "model", "exit", "active", "mode")
EFFORT_SHORT = sessionwords.EFFORT_SHORT  # as the Model column says an effort
HEADING = "\x00"  # the keys of the rows that head the open sessions and the rest
TEAM_ROW = "\x01"  # ...and of the row that heads a team in OPEN NOW, which the cursor does stop on
SIDEBAR = 26 + 3    # the project sidebar with its margins; it steps aside when the table would not fit
MINI_BELOW = 40     # rows of terminal under which the cards take two lines each, not seven
KEY_GROUPS = {action: group for group, rows in KEYS for _, action, _ in rows if action}  # the ? screen's
# 1 2 3 or a click on a card: the list shows only what it counts. Its key, its card, its title.
CARDS = {card: (key, f"card-{card}", title) for card, (key, title) in VM.CARDS.items()}  # key, its card, title
EXPAND_LINES = 4


at_limit, lifts, limit_help, ago = VM.at_limit, VM.lifts, VM.limit_help, VM.ago


def live_label(raw: str) -> tuple[str, str, int]:
    label, tone, rank = VM.live_label(raw)
    return label, TONE[tone], rank


def state_of(session: Session) -> tuple[str, str, int]:
    """What an open session is doing: (label, style, rank)."""
    label, tone, rank = VM.state_of(session)
    return label, TONE[tone], rank


def by_project(sessions: list[Session], style: str) -> list[tuple[str, str]]:
    """'web-app ×2', 'api-server': the projects of these sessions, the busiest first."""
    return [(name, style) for name in VM.by_project(sessions)]


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
    """Write to a session: Enter opens it in a tab with the prompt, Ctrl+B runs it in the background (where
    its CLI can't, prompting.send opens a tab instead, saying so). Its words are prompting.py's."""

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
            yield Label(title("Prompt", (self.session.project, f"bold {CYAN}"),
                              _clip(self.session.title, prompting.TITLE_CHARS)))
            yield Input(placeholder=prompting.placeholder(self.session))
            yield Label(keys(*prompting.keys(self.session)), classes="hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.dismiss((prompting.TAB, event.value.strip()))

    def action_background(self) -> None:
        text = self.query_one(Input).value.strip()
        if text:
            self.dismiss((prompting.BACKGROUND, text))

    def action_cancel(self) -> None:
        self.dismiss(None)


BIG_CONTEXT = limitwords.BIG_CONTEXT  # tokens: going on with this much on another account costs much of its limit


class LimitDialog(ModalScreen[tuple[str, list[str]] | None]):
    """c: what becomes of the sessions stopped at a usage limit, the ticked ones: on now (signed in
    with another account, or the limit has lifted), when the limit lifts, handed over to fresh
    sessions, or no longer by themselves. Each says how full its context is: on another account a
    session goes on by writing all of it into the cache again, which costs much of the limit (seen:
    716k tokens written by one session's first reply, a third of a 5-hour limit for a few at once)."""

    DEFAULT_CSS = frame("LimitDialog") + """
    LimitDialog OptionList { height: auto; margin-top: 1; background: $surface; border: none; }
    LimitDialog SelectionList { height: auto; max-height: 10; margin-top: 1; }
    LimitDialog .plan, LimitDialog .cost { color: $text-muted; margin-top: 1; }
    LimitDialog .away { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, stopped: list[Session], when: str, armed: bool, plan: str, room: bool,
                 away: dict[str, list[str]] | None = None, where: str = "") -> None:
        super().__init__()
        self.stopped, self.when, self.armed, self.plan, self.room = stopped, when, armed, plan, room
        # The sessions that ran on another organisation than the account signed in now (id -> what
        # their tool calls read, SENSITIVE), and where going on would send them: said, never stopped.
        self.away, self.where = away or {}, where

    def compose(self) -> ComposeResult:
        options = [Option(Text.assemble(*parts), id=way)
                   for way, parts in limitwords.ways(self.when, self.room, self.armed, bool(self.away))]
        with Vertical():
            yield Label(title(*limitwords.title_words(len(self.stopped), self.when)))
            if self.plan:
                yield Label(Text(self.plan), classes="plan")  # other accounts' names: never markup
            yield SelectionList(*[Selection(self.line(s), s.session_id, True)
                                  for s in limitwords.biggest(self.stopped)], id="which")
            yield Label(self.cost(), classes="cost")
            yield Label(self.away_words(), classes="away")
            yield OptionList(*options, id="ways")
            yield Label(keys(*limitwords.KEY_PAIRS), classes="hint")

    def on_mount(self) -> None:
        ways = self.query_one("#ways", OptionList)
        ways.focus()
        if self.away:  # going on would send a conversation to another organisation: the handover first
            ways.highlighted = limitwords.first_way([ways.get_option_at_index(i).id or ""
                                                     for i in range(ways.option_count)], True)

    def away_words(self) -> Text:
        """For the ticked ones that ran on another organisation: where going on sends them."""
        return Text.assemble(*limitwords.away_parts(self.ticked(), self.away, self.where))

    def line(self, session: Session) -> Text:
        """'web-app  Fix the login loop  612k context', a big context in amber."""
        return Text.assemble(*limitwords.line_parts(session))

    def ticked(self) -> list[str]:
        return list(self.query_one("#which", SelectionList).selected) if self.is_mounted else \
            [s.session_id for s in self.stopped]

    def cost(self) -> Text:
        """What going on costs on another account, for the ticked ones."""
        parts = limitwords.cost_parts(limitwords.ticked_total(self.stopped, self.ticked()))
        return Text.assemble(*parts, style=MUTED) if parts else Text("")

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        self.query_one(".cost", Label).update(self.cost())
        self.query_one(".away", Label).update(self.away_words())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "ways":
            return
        ticked = self.ticked()
        refused = limitwords.pick_refusal(ticked, event.option.id or "")
        if refused is not None:
            self.notify(refused.words, timeout=refused.seconds)
            return
        self.dismiss((event.option.id, ticked))

    def action_cancel(self) -> None:
        self.dismiss(None)


home_short = sessionwords.home_short


def cell(value: sessionwords.Cell) -> Text:
    """A preview's cell (sessionwords) in rich Text: a str as plain words (never markup: prompts and
    replies are anyone's words, and a stray '[/b]' in one would not render), one part as a Text in
    its style, more assembled."""
    if isinstance(value, str):
        return Text(value)
    if len(value) == 1:
        return Text(value[0][0], style=value[0][1])
    return Text.assemble(*value)


def facts_table(rows: list[sessionwords.Row]) -> Table:
    """The facts column next to the preview (sessionwords.fact_rows): quiet labels, the words beside them."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=MUTED, no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(label, cell(value))
    return grid


def clipped_line(parts: list[tuple[str, str]], room: int) -> Text:
    """Parts on one line (based), cut to the room with an ellipsis."""
    line = based(parts)
    line.truncate(room, overflow="ellipsis")
    return line


def based(parts: list[tuple[str, str]]) -> Text:
    """Parts as one Text in the first part's style, the rest appended in theirs."""
    if not parts:
        return Text("")
    out = Text(parts[0][0], style=parts[0][1])
    for words, style in parts[1:]:
        out.append(words, style=style)
    return out


def drawn_cells(words: rowwords.RowWords) -> list[Text]:
    """A list row's words (rowwords) as the table's cells: the Model column on its first part's style."""
    return [based(parts) if column in rowwords.BASED else cell(parts)
            for column, parts in zip(COLUMNS, words.cells, strict=True)]


class SessionTable(DataTable):
    """A DataTable where clicking a row opens it up to show how that session ended,
    and clicking its [ ] box ticks it."""

    def on_resize(self, event: events.Resize) -> None:
        # Its own width is known here only: the window's resize comes before the layout gives it one,
        # and the columns were fitted to the width before (a window made full screen from a small
        # one kept the small one's columns, the Model column left out).
        if isinstance(self.app, SessionPicker):
            self.app.call_after_refresh(self.app.fit_title)

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

    def update_cells(self, changes: list[tuple[str, str, object]]) -> None:
        """Cells changed, (row, column, value), and only their rows drawn again. update_cell throws
        away the drawing of every row (DataTable's caches go by a count of changes) and draws the
        whole table again: for the turning stars, a dozen times a second, that was most of the
        dashboard's drawing. Here the changed rows are forgotten from the caches and redrawn; should
        a Textual to come keep them otherwise, update_cell does it."""
        rows: dict[RowKey, int] = {}
        try:
            for row, column, value in changes:
                key = RowKey(row)
                self._data[key][ColumnKey(column)] = value
                rows[key] = self.get_row_index(key)
            count = self._update_count
            for index in rows.values():
                self._row_renderable_cache.discard((count, index))
            # Textual's LRUCache, not a dict: it has keys() but no __iter__ (iterated, it raises KeyError).
            for cache in (self._cell_render_cache, self._row_render_cache):  # keyed by the row first
                for stale in [k for k in cache.keys() if k[0] in rows]:  # noqa: SIM118
                    cache.discard(stale)
            stale = []
            for k in self._line_cache.keys():  # noqa: SIM118 - keyed by the line first
                try:
                    if self._get_offsets(k[0])[0] in rows:
                        stale.append(k)
                except LookupError:
                    pass
            for k in stale:
                self._line_cache.discard(k)
        except (AttributeError, KeyError, LookupError, TypeError):
            for row, column, value in changes:
                if row in self.rows:
                    self.update_cell(row, column, value)
            return
        for index in rows.values():
            self.refresh_row(index)


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
            yield Label(title(workspacewords.TITLE))
            yield OptionList(*[Option(Text.assemble((w.name, "bold"), "   ", (w.summary, "dim")), id=w.name)
                               for w in self.saved.values()])
            yield Label(keys(*workspacewords.LIST_KEYS), classes="hint")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_delete(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return
        name = options.get_option_at_index(options.highlighted).id
        if name is None:
            return
        said = cast("SessionPicker", self.app).say_note
        if not workspaces.drop(name, UI_TRIES):  # the file left as it is: the row stays
            said(workspacewords.not_kept_note(name, deleting=True))
            return
        options.remove_option(name)
        said(workspacewords.deleted_note(name))
        if options.option_count == 0:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def settle_memory() -> None:
    """What the dashboard has loaded is set aside from Python's garbage collector (gc.freeze): with
    some 460k objects of sessions, token counts and the search index in memory, every full collection
    walked them all, 150 to 260 ms in which nothing was drawn and no key was taken, every few seconds
    (typing in the first message froze). Collected first, so no garbage is kept for good; what comes
    after is collected as before, in 35 to 50 ms."""
    gc.collect()
    gc.freeze()


def wrap_at(text: Text, width: int, separator: str = "  ·  ") -> list[Text]:
    """A line of parts on as many lines as it takes at this width, broken only between its parts
    ("TEAM shop · 4 of 4 open · 7 messages · 671k tokens": never "671k" on one line, "tokens" on the
    next); a part wider than the width by itself is cut."""
    out: list[Text] = []
    for part in text.split(separator, allow_blank=True):
        part.end = ""
        if out and out[-1].cell_len + len(separator) + part.cell_len <= width:
            out[-1].append(separator, style=MUTED)
            out[-1].append_text(part)
        else:
            out.append(part)
    for line in out:
        line.truncate(width, overflow="ellipsis")
    return out or [Text("")]


class PaneGraph(TeamGraph):
    """A team's cards in the preview beside the list: no card is picked there (← → are the list's), so
    no line for what a picked one works on under them; drawn at the pane's width (pane_width), known
    before the first layout gives the widget a size, so its height (set from height_for) is never
    measured at a width it will not have."""

    pane_width = 0

    def height_for(self, rows: list[list[str]]) -> int:
        return (self.card_h if self.lead else 0) + len([row for row in rows if row]) * (self.card_h + 1)

    def draw_focus(self, canvas) -> None:
        pass

    def canvas_width(self) -> int:
        return max(40, self.size.width or self.pane_width or 100)


class SessionPicker(sixel.SixelHost, App[list[str]]):
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
    #sidebar > .legend { height: 2; margin-top: 1; padding: 0 2; }
    #sidebar > #gap { height: 1fr; }  /* what the list leaves: the legend follows the list, the chart keeps the bottom */
    #projects { width: 100%; background: $sunken; }
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
    /* Beside the list (split): the preview a column of its own at full height, its facts under its
       words; Bit at the foot of the list, on a rule of its own. Its width is set with the window's. */
    #list { height: 1fr; }
    #floor { display: none; margin: 0; color: #262626; }
    .split #main { layout: horizontal; }
    .split #list { width: 1fr; height: 100%; }
    .split #floor { display: block; }
    .split #inspector { layout: vertical; height: 100%; max-height: 100%; }
    .split #detail { width: 100%; padding: 0; }
    .split #meta { width: 100%; padding-left: 0; border-left: none; margin-top: 1; }
    .split .pane-graph { margin-top: 1; }
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
        Binding("E", "fold_teams", "Fold teams", show=False),
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
        Binding("M", "monitor", "Monitor"),  # on a team's row or a member's: dimmed on the rest
        Binding("A", "accounts", "Accounts", show=False),
        Binding("W", "wrapped", "Wrapped", show=False),
        Binding("w", "save_workspace", "Save workspace", show=False),
        Binding("o", "open_workspace", "Workspaces", show=False),
        Binding("X", "toggle_hidden", "Show hidden", show=False),  # the status line says it when any are
        Binding("comma", "settings", "CLIs", show=False),
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("b", "toggle_mascot", "Bit", show=False),
        Binding("V", "toggle_split", "Split"),
        Binding("escape", "back", "Quit", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, days: float = 14, include_all: bool = False, live: bool = False,
                 watch: str | None = None, tabs_window: str = "") -> None:
        super().__init__()
        # What the dashboard does by itself, with no UI: the live poll's words, queued handovers, team
        # notes. Its state (engine.FORWARDED) reads and sets here by the same names.
        self.engine = engine.Engine(self, clock=datetime.now)
        # `tabmancer team watch`: a team's monitor, in a window of its own beside the dashboard. It
        # shows the team and does what you ask of it there, and nothing by itself: no limits
        # continued, no toasts, no updates, nothing saved for the stats. "" asks which team, with the
        # list of them.
        self.watch = watch
        # Whether this one does what happens by itself (those things): the dashboard holding the
        # actor lock (actor.py), once however many dashboards and monitors are open. Taken in on_mount;
        # a dashboard without it watches like a monitor, and takes it over when its holder is gone.
        self.actor_timers: list[Timer] = []  # what acting runs on a timer: stopped if the lock is lost
        # The Windows Terminal window sessions open in, by name (--tabs-window), when tabmancer's own
        # launcher named it: the dashboard's own, which a monitor is told too. "" when not known.
        self.tabs_window = tabs_window
        self.start_live = live
        self.live_mode = False
        self.days = days
        self.include_all = include_all
        self.selected: set[str] = set()
        self.modes: dict[str, Mode] = {}
        self.preselected: set[str] = set()  # ticked automatically: open at the last shutdown
        self.filter_text = ""
        self.summaries: dict[str, dict] = {}
        self.summarizing: set[str] = set()
        self.project_filter = ALL_PROJECTS
        self.expanded: set[str] = set()  # rows opened to show how they ended
        self.title_width = EXPAND_WIDTH
        self.model_shown = True  # the Model column has room; on a narrow screen the title has it
        self.frame = 0  # spinner frame
        self.polls = 0
        self.token_report: tokens.Report | None = None
        self.kept_back = False  # the days a 45-day count keeps in the history: counted once (refresh_tokens)
        self.last_row = 0  # where the cursor was, so it steps over a heading the same way
        self.waiting_turn = 0  # j goes round the sessions waiting for you
        self.waiting_group = ""  # j: "needs" while some session needs you, else "waiting"
        self.limited: set[str] = set()  # the sessions at a limit when the footer last heard of it
        self.finder = search.Finder(search.Index.load())  # what was said in each session, for the filter
        self.search = self.finder.index
        self.marks = marks.Marks.load()  # hidden and pinned sessions
        self.git: dict[str, gitstate.GitState] = {}  # folder key -> where it stands in git
        self.git_failed: set[str] = set()  # folders git could not answer for, the last time
        self.git_retry: Timer | None = None  # the one retry pending for them
        self.show_hidden = False  # X: the hidden ones are listed too
        self.session_tokens: dict[str, tokens.Tokens] = {}  # each session over its whole life
        self.team_states: dict[str, teamtree.TeamState] = {}  # the teams with a member open, as last read
        self.team_flights: dict[str, list[tuple[teams.Message, float]]] = {}  # messages in flight, since when
        self.unfolded: set[str] = set()  # teams shown with their members under their row (e); else one row
        # a team row's keys; their presses are the engine's, shared with the team's view (TeamScreen)
        self.row_keys = teamkeys.TeamKeys(RowSurface(self), clock=time.monotonic, presses=self.engine.state.team_presses)
        self.handing: dict[int, str] = {}  # the handovers being written (hand_over): a token -> what is handed over
        self.quit_after = False  # a quit key pressed twice while one was written: quits once none is (finish_quitting)
        self.blocks: list[tuple[teamtree.TeamState, list[tuple[str, Session]]]] = []  # the teams in the list
        self.member_rows: dict[str, tuple[str, str]] = {}  # their members' session ids -> (team, member)
        self.grouped = VM.Groups([], [], {})  # the list as groups() last grouped it (rowwords.Listing)
        self.default_cap = picks.cap_tokens(picks.default_cap())  # the cap new sessions get, in tokens
        self.limits: list[usage.Limit] = []  # the plan limits as last read
        self.agy_bar: dict = {}  # agy's chain as last read from its saved quota (agy.bar_view); {} with no agy
        self.card_filter: str | None = None  # 1 2 3: the list shows only what that card counts
        self.bar_info = ""  # the top bar's counts, apart from its pills (watch_sub_title)
        self.focused_at = float("-inf")  # when the terminal last said the window took the focus
        self.swallowing = False  # the press that brought the window to the front: its release goes too
        self.footer_was: tuple | None = None  # the row the footer's words and dimmed keys were last made for
        self.monitors_opened: dict[str, float] = {}  # M: team -> when its monitor last opened or came up
        self.monitors_opening: set[str] = set()  # M: teams whose monitor is being opened right now
        self.split_on = True  # V: the preview beside the list from SPLIT_FROM; off, under it at any size
        self.team_graph: TeamGraph | None = None  # a team's cards in the preview beside the list
        self.pane_fit: tuple[int, int, int] = (0, 0, 0)  # the pane's room a team's preview was fitted to

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
                yield Static(id="gap")
                yield Label(f"TOKENS · {CHART_DAYS} DAYS", classes="heading")
                yield TokenChart([0] * CHART_DAYS, id="activity")  # in real pixels where it can be
            with Vertical(id="main"):
                with Vertical(id="list"):
                    yield Input(placeholder="Find by project, title or anything said in a session", id="filter")
                    # The cursor lights the row but keeps each cell's own colours (status, mode).
                    yield SessionTable(id="table", cursor_type="row", cursor_foreground_priority="renderable")
                    yield Mascot(id="mascot")
                    yield Rule(id="floor")  # what Bit stands on with the preview beside the list
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

    def on_load(self) -> None:
        # On Windows Textual sleeps out every timer in a thread of asyncio's default pool, which
        # has as many threads as cores + 4. The dashboard runs a dozen timers, some sleeping for
        # minutes: on a 4-core machine they took every thread, the rest (the screen's own updates
        # among them) waited their turn, and the dashboard froze for seconds at a time. Set on every
        # system: the threads start only when needed, and Textual picks its sleep when imported, so a
        # test that says it runs elsewhere still sleeps in threads (8 on a 4-core CI runner were full).
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=TIMER_THREADS, thread_name_prefix="timers"))

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
        self.faded = False  # the dashboard has faded in (faded_in)
        self.activity_target: list[int] = []
        self.grow_until = 0.0
        self.filter_box = self.query_one("#filter", Input)
        self.projects = self.query_one(ProjectList)
        self.cards = {card.id: card for card in self.query(StatCard)}
        self.setup_columns()
        use_saved_theme(self)
        saved = read_json("config.json")
        self.mascot.accent = THEMES[self.theme][1]
        self.show_mascot(way_of(saved.get("mascot") if isinstance(saved, dict) else None))
        self.split_on = not (isinstance(saved, dict) and saved.get("split") == "off")
        self.fit_split()
        self.engine.mount()  # a monitor never acts, nor asks for the lock
        self.set_interval(ACTOR_SECONDS, self.tick_actor)
        self.set_interval(SPIN_SECONDS, self.spin)
        self.refresh_usage()
        self.set_interval(USAGE_SECONDS, self.refresh_usage)
        self.refresh_tokens()
        self.set_interval(TOKENS_SECONDS, self.refresh_tokens)
        self.show_accounts()
        self.set_interval(ACCOUNTS_SECONDS, self.show_accounts)
        if self.acts:
            self.start_acting()
        elif self.watch is None:
            self.load_armed()  # shown: the one that acts continues them
            if actor.nameless():
                self.notify(NAMELESS, title="Watching", severity="warning", timeout=12)
            else:
                self.say_who_acts()
        # The tab is named after the dashboard, whatever started it, so that a desktop
        # notification can tell whether you are looking at it (toast.show). A monitor's is named
        # after its team: never the dashboard's, which M and a monitor's D look for.
        self.name_tab(self.TITLE if self.watch is None else teamwatch.title(self.watch or "teams"))
        table.focus()
        self.load()
        if self.watch is not None:  # on top from the first frame: the list is never seen
            self.show_monitor(self.watch)

    def start_acting(self) -> None:
        """This dashboard holds the actor lock: what happens by itself is its to do. At its start, or
        later when it takes the lock over from a dashboard that closed (then Wrapped's word too, which
        the first load starts otherwise)."""
        self.engine.started()
        self.check_updates()
        self.actor_timers.append(self.set_interval(UPDATES_SECONDS, self.check_updates))
        self.engine.update_due()  # today's claude update, before any session opens
        self.actor_timers.append(self.set_interval(DUE_SECONDS, self.continue_due))

    def start_wrapped(self) -> None:
        self.actor_timers.append(self.set_timer(WRAPPED_AFTER, self.tell_wrapped))
        self.actor_timers.append(self.set_interval(WRAPPED_SECONDS, self.tell_wrapped))

    def stop_acting(self) -> None:
        for timer in self.actor_timers:
            timer.stop()
        self.actor_timers.clear()
        self.engine.stopped()

    def say_who_acts(self, held: dict | None = None) -> None:
        held = held if held is not None else (actor.holder() or {})
        pid = f" (pid {held['pid']})" if isinstance(held.get("pid"), int) else ""
        self.notify(f"Another tabmancer dashboard{pid} continues sessions at their reset, sends the desktop "
                    "notes and runs the updates; this one shows the same, and takes over when that one closes.",
                    title="Watching", timeout=10)

    @work(thread=True, exclusive=True, group="actor")
    def tick_actor(self) -> None:
        """Every ACTOR_SECONDS, off the screen's thread (reading another process can take a moment:
        `ps` on macOS). A dashboard that watches: whether the one that acts is gone (it takes over
        then), and the sessions to continue as that one saved them. The one that acts: whether it
        still holds the lock; it steps back when a readable lock names another process, or after two
        looks in a row that found none it could read (a file held open a moment is no reason). A
        monitor: whether any dashboard acts at all (engine.look, then Engine.seen)."""
        self.call_from_thread(self.engine.seen, engine.look(self.watch is not None, self.acts))

    def show_armed(self, armed: dict[str, datetime]) -> None:
        """A dashboard that watches: the sessions to continue at their reset, as the one that acts saved them."""
        self.engine.show_armed(armed)

    def armed_changed(self) -> None:
        if self.screen_stack:
            for session in self.sessions.values():
                if at_limit(session):
                    self.update_row(session)

    def on_unmount(self) -> None:
        self.engine.unmount()

    def name_tab(self, name: str) -> None:
        """The terminal tab's title."""
        driver = self._driver
        if driver is not None and not driver.is_headless:
            driver.write(f"\x1b]0;{name}\x07")

    def show_monitor(self, name: str) -> None:
        """The monitor of this team, in place of the one on top (T or a handover picked another);
        with no such team, the list of teams to pick one from, or quit."""
        from .monitorview import TeamMonitor

        team = teams.load().get(name) if name else None
        if team is None:
            saved = teams.load()
            if not saved:
                self.exit(None)
                return

            def chosen(answer: tuple[str, str] | None) -> None:
                picked = teams.load().get(answer[1]) if answer and answer[0] == "open" else None
                if picked is not None:
                    self.show_monitor(picked.name)
                elif answer and answer[0] != "open":  # a new team: the dashboard makes it, the list again
                    self.notify("New teams are made in the dashboard: T there.", timeout=6)
                    self.show_monitor("")
                elif not isinstance(self.screen, TeamMonitor):
                    self.exit(None)  # nothing picked, and no monitor to go back to: not the list

            self.push_screen(TeamsDialog(saved, None), chosen)
            return
        self.watch = team.name
        self.name_tab(teamwatch.title(team.name))
        monitor = TeamMonitor(team, self.accent())
        if isinstance(self.screen, TeamMonitor):
            self.switch_screen(monitor)
        else:
            self.push_screen(monitor)

    @work(thread=True, group="tabs-open")
    def open_in_dashboard_window(self, launches: list[Launch], unasked: bool = False) -> None:
        """Tabs that belong in the dashboard's window, not the one used last: a monitor's (its own
        window would be "0"), and the dashboard's unasked ones (open_unasked). By the window's name
        when it is known. Else, asked (a key in a monitor), the dashboard's tab is brought to the
        front first, which makes its window the one used last ("0"); unasked, never: keys typed
        meanwhile elsewhere would land in the dashboard as its keys (x, h, q, Enter). With no name
        and no tab found: the dashboard's go to "0", a monitor's to a new window; but the
        dashboard's unasked ones go to a window named for them when a team's monitor is open (it
        may be the one used last): only looked for, nothing comes to the front. Unasked, the
        window that had the keys gets them back. Off the screen's thread: finding a tab takes a
        second or two. Today's claude update, when it is due, runs here first, never on the screen's
        thread: a limit lifting at 04:00 with the day's update due would have frozen the dashboard
        while it ran."""
        if any(launch.cli == "claude" for launch in launches):
            claudeupdate.before_opening(lambda words: self.call_from_thread(self.notify, words, timeout=8))
        before = focus.foreground() if unasked else 0  # after the update: you may have moved on meanwhile
        window = actions.window_for(self.tabs_window, self.watch is not None, unasked, teamwatch.monitor_open,
                                    lambda: focus.focus_tab(self.TITLE), DASHBOARD_WINDOW)
        self.call_from_thread(self.open_sessions, launches, window, True)
        if before:
            focus.give_back(before)

    def open_unasked(self, launches: list[Launch]) -> None:
        """Tabs no key asked for just now (a session whose limit lifted, a queued handover's fresh
        session): in the dashboard's own window when its name is known, never a team's monitor; with
        no name and no monitor open, the one used last (open_in_dashboard_window). The keys stay where
        they were. Elsewhere than in Windows Terminal, as any other."""
        if terminal_kind() != "windows-terminal":
            self.open_sessions(launches)
            return
        self.open_in_dashboard_window(launches, unasked=True)

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
            out.append(" " if out else "")
            out.append(f" {VM.ticked_words(self.selected, self.preselected)} · ⏎ opens ", style=pill)
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
        self.fit_split(event.size.width, event.size.height)
        self.place_mascot()

    def fit_sidebar(self, width: int | None = None) -> None:
        """The project sidebar steps aside when the session table would not fit beside it."""
        least = OTHER_COLUMNS["live" if self.live_mode else "picker"] + TITLE_MIN
        width = self.size.width if width is None else width
        self.dashboard.set_class(width - SIDEBAR < least, "no-sidebar")
        if hasattr(self, "projects"):  # the first resize comes before on_mount
            self.call_after_refresh(self.projects.fit)  # the list keeps clear of the legend and chart at any height
        self.call_after_refresh(self.fit_title)

    @property
    def split(self) -> bool:
        """The preview stands beside the list now (fit_split). False while shutting down."""
        return bool(self.screen_stack) and self.dashboard.has_class("split")

    def fit_split(self, width: int | None = None, height: int | None = None) -> None:
        """From SPLIT_FROM, unless V turned it off: the preview beside the list, both at full height,
        the window's width less PANE_TAKES wide, within PANE_LEAST and PANE_MOST. The widgets stay
        as they are (Bit's picture with them); only the layout changes."""
        if not hasattr(self, "inspector") or not self.screen_stack:
            return  # the first resize comes before on_mount, which fits it then; or shutting down
        width = self.size.width if width is None else width
        height = self.size.height if height is None else height
        split = self.split_on and width >= SPLIT_FROM[0] and height >= SPLIT_FROM[1]
        was = self.split
        self.dashboard.set_class(split, "split")
        self.inspector.styles.width = max(PANE_LEAST, min(PANE_MOST, width - PANE_TAKES)) if split else None
        if split != was:
            if not split:
                self.drop_team_graph()  # now: a frame of the cards under the list would show otherwise
            self.call_after_refresh(self.update_detail)  # the preview's words for its new room
            self.call_after_refresh(self.fit_title)  # the list's room changed too

    def action_toggle_split(self) -> None:
        """V: the preview beside the list (by itself from SPLIT_FROM), or under it at any size.
        Remembered."""
        self.split_on = not self.split_on
        self.fit_split()
        self.place_mascot()
        columns, rows = SPLIT_FROM
        if not self.split_on:
            said = "The preview stays under the list: V puts it beside the list again."
        elif self.split:
            said = "The preview beside the list: V puts it under the list."
        else:
            said = (f"The preview goes beside the list from {columns}x{rows}; this window is "
                    f"{self.size.width}x{self.size.height}.")
        if not merge_json("config.json", split="auto" if self.split_on else "off"):
            said += " " + NOT_SAVED
        self.notify(said, timeout=4)

    @work(thread=True)
    def load(self) -> None:
        # Quick: open sessions from the pid files now, `claude agents` refines them below.
        sessions = find_sessions(self.days, self.include_all, quick=True)
        preselected = open_at_shutdown(sessions, recent_shutdowns())
        self.summaries = summarize.load_cache()
        self.call_from_thread(self.show_sessions, sessions, preselected)

    def show_sessions(self, sessions: list[Session], preselected: set[str]) -> None:
        self.sessions = {s.session_id: s for s in sessions}
        if UNREADABLE:
            self.notify(f"{sum(UNREADABLE.values())} transcripts could not be read and are left out of the list: "
                        "tabmancer doctor says more.", severity="warning", timeout=15)
        preselected = VM.preselect(self.sessions, preselected, self.marks.is_hidden)
        self.selected = set(preselected)
        self.preselected = set(preselected)
        if self.start_live:
            self.action_toggle_live()
        self.poll_live()  # refine the quick view with `claude agents --json`
        self.poll_teams()
        if self.watch is None:  # a monitor has no filter to search with
            self.index_search()  # what was said in each, for the filter
        self.refresh_git()
        self.set_interval(GIT_SECONDS, self.refresh_git)
        self.set_interval(LIVE_REFRESH_SECONDS, self.poll_tick)
        self.refresh_rows()
        self.update_dashboard()
        # Faded in, every widget takes its colours afresh: those drawn during the fade kept them
        # blended towards black (the preview's facts column stayed black for good).
        self.body.styles.animate("opacity", 1.0, duration=0.5, easing="out_cubic", on_complete=self.faded_in)
        if not sessions:
            self.detail.update(nothing_found(self.days, self.include_all, dashboard=True))
        if self.watch is not None:
            pass  # a monitor: Bit stays quiet on the list nobody sees there
        elif self.preselected:
            self.mascot.say(VM.shutdown_hello(len(self.preselected)), seconds=6)
        elif not sessions:
            self.mascot.say("Hi! No sessions here yet: n starts some.", seconds=6)
        else:
            self.mascot.say("Hi! Press Enter to open a session.", seconds=5)
        self.check_limits()
        if self.acts:
            self.start_wrapped()  # after Bit's hello: Friday's word on the week
        for after in SETTLE_AFTER:  # once what loads at the start (tokens, the search index) is in
            self.set_timer(after, settle_memory)

    def faded_in(self) -> None:
        """The fade-in over: every widget takes its colours afresh (faded says so to the tests)."""
        self.refresh_css(animate=False)
        # And is drawn again: refresh_css redraws only a widget whose own styles changed, and a part
        # drawn during the fade, its styles the same all along, kept its lines blended towards black
        # (the preview's facts column, seen black for good on the Linux and macOS runners).
        for widget in (self.body, *self.body.query("*")):
            widget.refresh()
        self.faded = True

    # --- rows ---------------------------------------------------------------------

    def setup_columns(self, keep: bool = False) -> None:
        """The columns, made again. keep: the rows built again too, the cursor on the row it was on
        (clearing the columns clears the rows, which put the cursor on the first row: V, l or a
        resize sent it there)."""
        table = self.table
        was = (self.cursor_key(), table.cursor_row, table.scroll_y)
        table.clear(columns=True)
        for key, label in zip(COLUMNS, rowwords.labels(self.live_mode, self.model_shown)):
            table.add_column(label, key=key)
        if keep:
            self.refresh_rows(was=was)

    def visible(self) -> list[Session]:
        """The list's sessions, filtered and in order (VM.visible): groups() puts the pinned ones apart."""
        return VM.visible(self.sessions.values(), self.marks.is_hidden, show_hidden=self.show_hidden,
                          text=self.filter_text, said_in=self.said_in, project=self.project_filter,
                          card=self.card_filter, live=self.live_mode)

    counted_by = staticmethod(VM.counted_by)  # whether a card counts this session, as update_dashboard counts them

    def action_card(self, card: str) -> None:
        """1 2 3, or a click on a card: only what that card counts; the same again shows everything."""
        self.card_filter = VM.toggle_card(self.card_filter, card)
        for name, (_, card_id, _) in CARDS.items():
            self.cards[card_id].pick(name == self.card_filter)
        self.refresh_rows(keep_place=False)
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
        return VM.in_sight(self.sessions.values(), self.marks.is_hidden, self.show_hidden)

    def mode(self, session: Session) -> Mode:
        return self.modes.get(session.session_id) or default_mode(session.status)

    def cells(self, session: Session) -> tuple[Text, ...]:
        """A session's row (rowwords.row), in the picker's columns or the live view's. Its status as
        it moves now (status_cell); opened, how it ended under its title (expansion)."""
        out = drawn_cells(rowwords.row(self.listing(), session.session_id, session))
        out[3] = self.status_cell(session)
        if session.session_id in self.expanded and not self.live_mode:
            out[2] = Text("\n").join([out[2], *self.expansion(session)])
        return tuple(out)

    def model_cell(self, session: Session) -> Text:
        """'opus 5.5·xhigh': the model of its last reply, with its version, and the effort it ran at (as
        Claude Code wrote it, else as tabmancer opened it); quiet, and left empty where the screen is too
        narrow for it. An amber ↓ after the model: another took over by itself (Session.fell_back), and
        the effort makes room for it where both do not fit."""
        if not self.model_shown:
            return Text("")
        return based(sessionwords.model_parts(session, self.effort_of(session), MODEL_ROOM))

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
        width, shown = rowwords.title_room(self.table.size.width, self.live_mode)
        if width != self.title_width or shown != self.model_shown:
            self.title_width, self.model_shown = width, shown
            self.setup_columns(keep=True)  # a column keeps the width of its widest cell so far

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
        return cell(sessionwords.open_as(session, self.mode(session), armed=self.armed.get(session.session_id),
                                          queued=session.session_id in self.shown_queued, accent=self.accent(),
                                          plain=plain))

    def flash_background(self, session: Session) -> str:
        """While a row is lit after a change, a background that fades back to black."""
        left = self.flash.get(session.session_id, 0) - time.monotonic()
        return rowwords.flash_background(THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1], left)

    def status_cell(self, session: Session) -> Text:
        """Its Status (State) cell, rowwords.status as it moves now: the turning star and its band of
        light, the red pulse; a row lit after a change, on a background that fades."""
        said, motion = rowwords.status(session, self.accent())
        lit = self.flash_background(session)
        if motion == "spin":
            return self.working(said[-1][0], lit)
        if motion == "pulse":  # a smooth pulse rather than a blink: the red breathes in and out
            return cell(rowwords.moving(said, motion, self.accent(), self.frame * SPIN_SECONDS))
        return cell(rowwords.lit(said, lit))

    def working(self, label: str, background: str) -> Text:
        """Claude's own look (rowwords.moving): a turning star in the accent colour that glows softly,
        and a slow band of light sweeping across the word; on a lit row's background."""
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        now = time.monotonic()
        parts = rowwords.moving([("", ""), (label, "")], "spin", accent, now)
        parts[0] = (f"{STAR[int(now * 8) % len(STAR)]} ", parts[0][1])  # this front's frames (a test stills them)
        return Text.assemble(*rowwords.lit(parts, background))

    def spin(self) -> None:
        """Advance the spinners and pulses of open sessions; touch only their cells. Not behind a
        dialog or another screen: with a dialog in front, Textual sends the whole screen to the
        terminal again for any change behind it, a dozen times a second for a turning star."""
        self.frame += 1
        if not self.screen_stack or self.screen is not self.dashboard:
            return
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
        graph = self.team_graph
        if graph is not None and (graph.flights or any(
                s is not None and s.live_state.lower() in SPINNING for s in graph.sessions.values())):
            graph.refresh()  # its stars turn and its messages fly, as in the team view
        if graph is not None:
            self.fit_team_pane()  # a layout after the last look moved its messages' room
        changes: list[tuple[str, str, object]] = []  # only these rows are drawn again (update_cells)
        for state, _ in self.blocks:  # a folded team's members at work: their stars turn on its row
            key = f"{TEAM_ROW}{state.team.name}"
            if (state.team.name not in self.unfolded and key in self.table.rows
                    and any(s.live_state.lower() in SPINNING for _, s in self.team_members(state.team.name))):
                changes.append((key, "title", self.team_cells(key)[2]))
        for session in self.visible():
            sid = session.session_id
            if self.animates(session) and sid in self.table.rows:
                changes.append((sid, "status", self.status_cell(session)))
            if self.flash.get(sid, now) < now:
                del self.flash[sid]  # drawn one last time without the highlight
        if changes:
            self.table.update_cells(changes)

    def animates(self, session: Session) -> bool:
        """Whether its status cell changes from one frame to the next: a turning star, the red
        pulse, a fading flash. Only those are touched: the table draws itself anew for every cell
        that changes, so an open session waiting quietly would redraw it twelve times a second."""
        if session.session_id in self.flash:
            return True
        if at_limit(session) or session.exit is not Exit.RUNNING:
            return False
        return session.live_state.lower() in SPINNING or state_of(session)[2] == 0

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
        if not self.kept_back:  # once: the days whose files Claude Code deletes next go into the history
            self.kept_back = True
            tokens.collect(KEEP_BACK_DAYS)
        fresh = store.listed_others(self.days, self.include_all)
        if fresh is not None:  # Codex and agy have no live feed: their sessions are re-read every minute
            self.call_from_thread(self.apply_other_sessions, fresh)

    def apply_other_sessions(self, fresh: list[Session]) -> None:
        changed = store.take_others(self.sessions, fresh)
        if not changed:
            return
        self.sync_rows(changed)
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
        ids = VM.token_ids(report, self.sessions, self.project_filter)  # the desktop rail's rule too
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
        self.grouped, self.blocks, self.member_rows = grouped, grouped.blocks, grouped.members
        return grouped.groups

    def listing(self) -> rowwords.Listing:
        """What the list is drawn from now (rowwords.Listing): the sessions as groups() last grouped
        them, and the star's frame this moment for a folded team's member at work."""
        return rowwords.Listing(
            self.grouped, self.sessions, self.team_states, self.accent(), self.title_width,
            model_shown=self.model_shown, live=self.live_mode, unfolded=self.unfolded, selected=self.selected,
            pinned=self.marks.pinned, is_hidden=self.marks.is_hidden, modes=self.modes, armed=self.armed,
            queued=self.shown_queued, tokens=self.session_tokens, default_cap=self.default_cap,
            star=STAR[int(time.monotonic() * 8) % len(STAR)])

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
        """A group's heading (rowwords.heading), two rows high: a blank line sets it apart from what is
        above it."""
        self.groups()  # as the list heads them now
        words = rowwords.row(self.listing(), key, None)
        return tuple(Text("") if not parts else Text("\n" + parts[0][0], style=parts[0][1]) if len(parts) == 1
                     else Text.assemble("\n", *parts) for parts in words.cells)

    def refresh_rows(self, keep_place: bool = True, was: tuple[str | None, int, float] | None = None) -> None:
        """Build the table again. A filter that changed (keep_place False) starts at its newest match.
        was: where the cursor and the scroll were, when the rows went before this (setup_columns)."""
        table = self.table
        current, row, scrolled = was or (self.cursor_key(), table.cursor_row, table.scroll_y)
        table.clear()  # the cursor goes to the top with it, and the scroll
        for key, session, height in self.rows():
            cells = (self.cells(session) if session is not None else self.team_cells(key)
                     if key.startswith(TEAM_ROW) else self.heading_cells(key))
            table.add_row(*cells, key=key, height=height)
        table.scroll_to(y=scrolled, animate=False, immediate=True)
        # On its row; gone (hidden, closed, folded into its team), on the row that took its place (VM.kept_row).
        kept = VM.kept_row([key.value for key in table.rows], current, row, keep_place)
        if kept is not None:
            table.move_cursor(row=kept)
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

    def members_open(self, name: str) -> int:
        """How many of a team's members are open, as the list has them (its row and its preview)."""
        return sum(s.exit is Exit.RUNNING for _, s in self.team_members(name))

    def team_cells(self, key: str) -> tuple[Text, ...]:
        """A team's row (rowwords.team_row). Folded (as it starts): its members as chips, a member at
        work with the star's frame now. Unfolded, its members are listed under it."""
        return tuple(drawn_cells(rowwords.row(self.listing(), key, None)))

    def update_team_rows(self) -> None:
        for state, _ in self.blocks:
            key = f"{TEAM_ROW}{state.team.name}"
            if key in self.table.rows:
                for column, value in zip(COLUMNS, self.team_cells(key)):
                    self.table.update_cell(key, column, value)

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
        for key, value in zip(COLUMNS, self.cells(session)):
            table.update_cell(session.session_id, key, value)

    def current(self) -> Session | None:
        table = self.table
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return self.sessions.get(row_key.value)

    def update_status(self) -> None:
        if self.live_mode:
            self.bar_info = ""
            self.sub_title = VM.live_words(list(self.sessions.values()), LIVE_REFRESH_SECONDS)
            self.update_detail()
            return
        hidden = sum(self.marks.is_hidden(s) for s in self.sessions.values())
        card = (CARDS[self.card_filter][0], CARDS[self.card_filter][2]) if self.card_filter else None
        self.bar_info, info = VM.info_words(len(self.sessions), hidden, self.days, show_hidden=self.show_hidden,
                                            card=card, selected=len(self.selected),
                                            preselected=len(self.preselected))
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
        self.drop_team_graph()
        # Quiet labels on the left, the words themselves bright and indented beside them,
        # each cut to a few lines of the room there is, so the reply still shows when narrow.
        room = max(24, (detail.content_size.width or 74) - 14)
        view = sessionwords.detail(session, self.detail_inputs(session, room))
        head = Text(view.head, style=f"bold {TEXT}")
        status = Table.grid(padding=(0, 2))  # what it means beside the status, wrapped under itself
        status.add_column(no_wrap=True)
        status.add_column(style=MUTED, overflow="fold")
        status.add_row(cell(view.status), view.means)
        enter = Text.assemble(*view.enter)
        said = Table.grid(padding=(0, 2))
        said.add_column(style=MUTED, no_wrap=True)
        said.add_column(style=TEXT, overflow="fold")
        for label, value in view.said:
            # more: beside the list, at full height, more of each; a member's messages cut to the room here
            said.add_row(label, clipped_line(value, 2 * room) if label == "Messages" else cell(value))
        detail.update(Group(head, status, enter, Text(""), said))
        self.meta.update(facts_table(view.facts))

    def detail_inputs(self, session: Session, room: int = 60, said: bool = True) -> sessionwords.DetailInputs:
        """What the preview says of a session beyond the session itself, as this dashboard has it
        (sessionwords.detail). said=False: only what its facts need."""
        sid = session.session_id
        found = None
        if said and sid in self.said_in():  # the filter's words, where they were said
            found = self.search.snippet(sid, self.filter_text, width=2 * room)
        return sessionwords.read_inputs(
            session, members=self.member_rows, team_states=self.team_states, git=self.git,
            session_tokens=self.session_tokens, background=self.background_live, armed=self.armed,
            queued=self.shown_queued, modes=self.modes, default_cap=self.default_cap, accent=self.accent(),
            selected=self.selected, summaries=self.summaries, summarizing=self.summarizing, found=found,
            room=room, more=3 if self.split else 1, said=said)

    def enter_words(self, session: Session) -> str:
        """What Enter does with the highlighted session, as it stands: ticked ones go first."""
        return sessionwords.enter_words(session, lambda: self.launch_for(session), self.selected)

    def meta_table(self, session: Session) -> Table:
        """The facts column next to the preview."""
        return facts_table(sessionwords.facts(session, self.detail_inputs(session, said=False)))

    def cap_of(self, session: Session) -> int:
        """The context cap a session runs under, as far as tabmancer knows (teams.cap_of); 0 for none."""
        found = self.member_rows.get(session.session_id)
        state = self.team_states.get(found[0]) if found else None
        return teams.cap_of(session.session_id, state.team if state is not None else None)

    def effort_of(self, session: Session) -> str:
        """The effort a session runs at: as its last reply says (Claude Code writes it there since
        2.1.319), else as tabmancer started it: a team member's own, or what it was opened with from
        here (picks). Others ran on Claude Code's own setting."""
        return VM.effort_of(session, session.session_id in self.member_rows)

    def launch_for(self, session: Session, saved: dict[str, teams.Team] | None = None) -> Launch:
        """How a closed session opens: a team member's as that member (teams.as_member), one tabmancer
        opened with an effort at that effort again (picks.resumed). One running in the background is
        attached to, as its preview says: resumed, it was dropped as open already, and nothing opened."""
        job = self.background_live.get(session.session_id) if session.status is Status.BACKGROUND else None
        return actions.launch_for(session, self.mode(session), job, saved)

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
        self.refresh_rows(keep_place=False)
        self.update_chart()


    def outcome(self, session: Session) -> tuple[Text, str]:
        """A heading and a few words on what the session came to (shown when a row is opened)."""
        heading, body = sessionwords.outcome(session, self.summaries.get(session.session_id, {}).get("summary"))
        return Text.assemble(*heading), body

    def action_prompt(self) -> None:
        """p: a closed session opened with a prompt (PromptDialog, then sending_prompt); an open one has its
        tab brought forward (nothing types into it from outside); prompting.py's words."""
        session = self.current()
        if session is None:
            return
        if session.status is Status.RUNNING:
            self.go_to_tab(session)
            return
        refused = prompting.refusal(session)
        if refused:
            self.say_note(actions.Note(refused, "warning"))
            return

        def send(answer: tuple[str, str] | None) -> None:
            if not answer:
                return
            if self.after_update("it opens with your prompt", lambda: send(answer)):
                return
            self.sending_prompt(session, *answer)

        self.push_screen(PromptDialog(session), send)

    @work(thread=True, group="prompt")
    def sending_prompt(self, session: Session, how: str, prompt: str) -> None:
        """The prompt sent (prompting.send: a terminal takes seconds to open a tab, a background start
        longer), then what it came to said; the list looks again where a session opened elsewhere or
        started in the background (after a moment: Claude registers it first)."""
        sent = prompting.send(session, how, prompt, lambda: self.call_from_thread(self.live_ids), open_tabs,
                              run_in_background, lambda note: self.call_from_thread(self.say_note, note))
        self.call_from_thread(self.say_note, sent.note)
        if sent.outcome == "elsewhere":
            self.call_from_thread(self.poll_live)
        elif sent.outcome == "started":
            time.sleep(2)
            self.call_from_thread(self.poll_live, True)

    def action_tokens(self) -> None:
        if self.token_report is None:
            self.notify("Still counting tokens…", timeout=3)
            return
        accent = THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]
        self.push_screen(StatsScreen(self.token_report, self.sessions, accent))

    def action_wrapped(self) -> None:
        """W: Claude Code Wrapped, the last seven days as a card to share."""
        if self.token_report is None:
            self.notify("Still counting tokens…", timeout=3)
            return
        from .wrappedview import WrappedScreen

        self.push_screen(WrappedScreen(wrapped.week(self.token_report, self.sessions), self.accent()))

    def tell_wrapped(self) -> None:
        """On Fridays from noon, or the weekend after, once a week: the week is ready (W)."""
        self.engine.wrapped()

    # --- handing over -----------------------------------------------------------------

    def warn_crowded(self, sessions) -> None:
        """Say so, once per session, when an open session's context passes the handover point.
        Nothing is handed over unless you ask."""
        self.engine.crowded(sessions)

    def action_handover(self) -> None:
        team = self.team_at() if not self.selected else None
        if team is not None:  # a team's row: the whole team, as h in its view
            self.press_team_handover(team.team, {name: self.sessions.get(s.session_id, s) if s is not None else None
                                                 for name, s in team.sessions.items()})
            return
        self.open_handover(self.current())

    def press_team_handover(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """h on a team's row or in its view. Twice: the whole team handed over (hand_over_team). A
        handover its lead wrote already, the team still closed, is shown again, not written again
        (Engine.team_handover)."""
        self.engine.team_handover(team, members)

    def open_handover(self, session: Session | None, how: str | None = None) -> None:
        """The h dialog for a session: how its handover should be written. Given how ("self" or
        "transcript", as act() may), it starts straight away, after the same checks (Engine.handover)."""
        self.engine.handover(session, how)

    def ask_handover(self, session: Session, then: Callable[[str], None]) -> None:
        self.push_screen(HandoverDialog(session), lambda how: how and then(how))

    def start_handover(self, session: Session, how: str) -> None:
        """Hand it over now, or once it finishes if it is working (Engine.start_handover)."""
        self.engine.start_handover(session, how)

    def open_ones(self) -> list[Session]:
        return self.engine.open_ones()

    def hand_over(self, session: Session, how: str) -> None:
        """Write the handover on a thread (writing_handover); quitting meanwhile waits for it (quit_ok)."""
        self.writing_handover(session, how, self.handing_starts(session.project))

    @work(thread=True, group="handover")
    def writing_handover(self, session: Session, how: str, token: int) -> None:
        """Write the handover, close the old session if it is open (actions.write_handover), then open
        the fresh one."""
        try:
            try:
                handed = actions.write_handover(session, how, lambda: self.call_from_thread(
                    lambda: (self.sessions.get(session.session_id, session), self.open_ones())), teams.member_of)
            except actions.ActionError as error:
                actor.let_go(f"handover-{session.session_id}")  # a queued one: h again may queue it afresh
                self.call_from_thread(self.notify, error.words, severity="error", timeout=15)
                return
            self.call_from_thread(self.handover_ready, session, handed.written, handed.left_open)
        finally:
            self.handing_ended(token)

    def handing_starts(self, what: str) -> int:
        """A handover being written (on the UI thread, as it starts): its token for handing_ended."""
        token = max(self.handing, default=0) + 1
        self.handing[token] = what
        return token

    def handing_ended(self, token: int) -> None:
        """From its thread, after what came of it (the fresh session opened, or the dialog shown): no longer
        written; a quit asked for meanwhile goes on (finish_quitting)."""
        def ended() -> None:
            self.handing.pop(token, None)
            self.finish_quitting()

        try:
            self.call_from_thread(ended)
        except RuntimeError:  # the app has closed meanwhile (no active app then)
            pass

    def handover_ready(self, session: Session, written: str, left_open: str = "") -> None:
        """Its handover written: the fresh session opens (Engine.handover_ready)."""
        self.engine.handover_ready(session, written, left_open)

    def hand_over_team(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """A whole team handed over on a thread (writing_team_handover); quitting meanwhile waits for it."""
        self.writing_team_handover(team, members, self.handing_starts(f"team {team.name}"))

    @work(thread=True, group="team-handover")
    def writing_team_handover(self, team: teams.Team, members: dict[str, Session | None], token: int) -> None:
        """A whole team handed over (actions.write_team_handover); then the dialog."""
        try:
            try:
                handed = actions.write_team_handover(team, members, lambda lead: self.call_from_thread(
                    lambda: (self.sessions.get(lead.session_id, lead), self.open_ones())), close_tabs)
            except actions.ActionError as error:
                self.call_from_thread(self.notify, error.words, severity="error", timeout=15)
                return
            if handed is not None:
                self.call_from_thread(self.engine.team_handover_ready, team, handed.lead, handed.written,
                                      handed.lineup, handed.suggested)
        finally:
            self.handing_ended(token)

    def ask_team_start(self, team: teams.Team, written: str, lineup: str, suggested: bool,
                       then: Callable[[tuple[str, str] | None], None]) -> None:
        """The lead's handover and line-up shown; Ctrl+S starts the team's new generation."""
        self.push_screen(TeamHandoverDialog(team, written, lineup, suggested), then)

    def adopt_team(self, team: teams.Team) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, TeamScreen) and screen.team.name == team.name:
                screen.adopt(team)

    # --- renaming ----------------------------------------------------------------------

    def action_rename(self) -> None:
        session = self.current()
        if session is None:
            return
        refused = rename.refusal(session)  # its words in one copy: the window says them too
        if refused is not None:
            self.say_note(actions.Note(refused, "warning"))
            return

        def chosen(name: str | None) -> None:
            if not name or name == session.title:
                return
            if session.exit is Exit.RUNNING:  # renamed from outside, its next turn would write the old name back
                told = rename.open_told(session, name)
                copy_text(self, told.clipboard)
                self.go_to_tab(session)
                self.say_note(told)
            else:
                self.rename_closed(session, name)

        enter = keys(("Enter", rename.enter_words(session)), ("Esc", "cancel"))
        hint = Text(rename.OPEN_WORDS + "\n", style=MUTED) + enter if session.exit is Exit.RUNNING else enter
        self.push_screen(NamePrompt(rename.heading(session), value=session.title, placeholder=rename.PLACEHOLDER,
                                    hint=hint), chosen)

    @work(thread=True, group="rename")
    def rename_closed(self, session: Session, name: str) -> None:
        try:
            rename.rename(session, name)
        except OSError as error:
            self.call_from_thread(self.say_note, rename.failed_note(session, error))
            return
        fresh = read_session(session.path) if session.path else None
        self.call_from_thread(self.renamed, fresh or replace(session, title=name))

    def renamed(self, session: Session) -> None:
        if session.session_id in self.sessions:
            self.sessions[session.session_id] = session
            self.update_row(session)
            self.update_detail()
        self.say_note(rename.renamed_note(session.title))

    def action_monitor(self, name: str = "") -> None:
        """M: the team's monitor (`tabmancer team watch`) in a window of its own, on the second
        screen when there is one; open already, it comes to the front. On a team's row or a member's;
        a team's view passes its team."""
        if not name:
            here = self.team_at()
            session = self.current()
            if here is not None:
                name = here.team.name
            elif session is not None and session.session_id in self.member_rows:
                name = self.member_rows[session.session_id][0]
        if not name:
            self.notify("M opens a team's monitor: on a team's row, or one of its members'.", timeout=4)
            return
        if name in self.monitors_opening:
            return  # being opened (Windows Terminal can take a while): a second M would open another
        if time.monotonic() - self.monitors_opened.get(name, float("-inf")) < MONITOR_AGAIN:
            return  # opened a moment ago, its window still on its way
        self.monitors_opening.add(name)
        team = teams.load().get(name)
        self.open_monitor(name, team.cwd if team is not None else "")

    @work(thread=True, group="monitor")
    def open_monitor(self, name: str, folder: str) -> None:
        try:
            said, severity = teamwatch.open_monitor(name, folder, tabs_window=self.tabs_window), "information"
        except OSError as error:
            said, severity = f"Could not open the monitor of {name}: {error}", "error"
        finally:  # from when it is done: the window shows a moment after wt returns
            self.monitors_opened[name] = time.monotonic()
            self.monitors_opening.discard(name)
        self.call_from_thread(self.notify, said, severity=severity, timeout=6)

    def action_teams(self) -> None:
        """T: the list of teams, to open one's view or make a new one; on a team's row or one of its
        members, with that team picked. A team's own view opens with Enter on its row."""
        here = self.team_at()
        session = self.current()
        if here is None and session is not None and session.session_id in self.member_rows:
            here = self.team_states.get(self.member_rows[session.session_id][0])
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

        self.push_screen(TeamsDialog(saved, here.team.name if here is not None else None), chosen)

    def new_team(self) -> None:
        here = self.current()
        if self.project_filter != ALL_PROJECTS and self.sessions:
            folder = next((s.cwd for s in self.sessions.values() if folder_key(s.cwd) == self.project_filter), "")
        else:
            folder = here.cwd if here is not None else ""

        def made(team: teams.Team | JoinTeam | TellTeam | None) -> None:
            if isinstance(team, JoinTeam):
                self.join_team(team)
                return
            if isinstance(team, TellTeam):
                self.tell_team(team.team, team.to, team.message)
                return
            if team is None:
                return
            try:
                teams.make(team, UI_TRIES)  # a team of that name made meanwhile: Taken
            except Busy as error:
                self.notify(teams.not_saved_words(f"{team.name} was not made", error), severity="warning", timeout=8)
                return
            self.open_team(team, teams.start(team, {}))  # the members first, the orchestrator last
            self.push_screen(TeamScreen(team, self.accent()))

        newest = sorted(self.sessions.values(), key=lambda s: s.last_active, reverse=True)
        folders = list(dict.fromkeys(s.cwd for s in newest if s.cwd))  # the folder box completes these
        found = newsessions.projects(list(self.sessions.values()))  # and lists these, as n does
        working = [(state.team, self.open_members(state)) for state in self.team_states.values()]
        self.push_screen(NewTeamDialog(folder or str(Path.home()), set(teams.load(UI_TRIES)), folders, found,
                                       [(team, names) for team, names in working if names]), made)

    def open_members(self, state: teamtree.TeamState) -> set[str]:
        """The names of a team's members whose sessions are open, as the list knows them now."""
        return {name for name, s in state.sessions.items()
                if s is not None and self.sessions.get(s.session_id, s).exit is Exit.RUNNING}

    def join_team(self, join: JoinTeam) -> None:
        """T's Ctrl+R found the goal adds to a team at work: the goal grows by it, the members join and
        open, each introducing itself, and the lead (or the peer it concerns) is told
        (teamkeys.TeamKeys.join_team)."""
        self.row_keys.join_team(join)

    def tell_team(self, name: str, to: str, message: str) -> None:
        """A message for a team's member (its lead, or the peer the goal concerns): typed into its tab
        once it waits for you, as c types into one; a closed one opens with it
        (teamkeys.TeamKeys.tell_team)."""
        self.row_keys.tell_team(name, to, message)

    def deliver_notes(self) -> None:
        """The messages waiting for team members: typed in once one waits for you, never while it works
        or asks you something (Enter would answer a question on its screen)."""
        self.engine.deliver()

    @work(thread=True, group="notes")
    def type_note(self, session: Session, who: str, message: str) -> None:
        self.call_from_thread(self.engine.typed, alerts.type_note(session, who, message))

    def put_on_clipboard(self, text: str) -> None:
        copy_text(self, text)

    def notify(self, message: str, *, title: str = "", severity: str = "information",
               timeout: float | None = None, markup: bool = True) -> None:
        """Every note as plain words, never markup: notes carry titles, projects, member names and
        errors, anyone's words, and a stray '[/b]' in one would not show (Textual's MarkupError)."""
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=False)

    def accent(self) -> str:
        return THEMES.get(self.theme, THEMES[DEFAULT_THEME])[1]

    # --- the dashboard as data, and its actions by a session's id --------------------------------

    def view(self) -> VM.View:
        """Everything the dashboard shows, as plain data (viewmodel), for anything built on it.
        Built afresh each time; cheap enough for every poll."""
        sessions = self.sessions
        return VM.build(sessions, hidden={sid for sid, s in sessions.items() if self.marks.is_hidden(s)},
                        pinned=set(self.marks.pinned), git=self.git, team_states=self.team_states,
                        tokens=self.session_tokens, limits=self.limits, armed=self.armed,
                        queued=self.shown_queued)

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
            if session_id in self.openings.recent():
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
            refused = self.engine.arm(session)
            if refused is not None:
                return refused
            self.update_row(session)
            self.update_detail()
            return actions.armed_words(self.armed[session_id])
        raise KeyError(f"no action {action}")

    # --- teams in the list ----------------------------------------------------------------

    def poll_teams(self) -> None:
        self.read_teams(list(self.sessions.values()))  # listed here: the poll's thread must not walk the dict

    @work(thread=True, exclusive=True, group="teams")
    def read_teams(self, listed: list[Session]) -> None:
        """The teams with a member in the list, open or not, each member's session and what was said:
        only what the transcripts gained since is read. A team whose sessions are all older than the
        list is not read at all."""
        states, agy_updates = store.read_team_states(listed, self.days)
        self.call_from_thread(self.show_teams, states, agy_updates)

    def show_teams(self, states: dict[str, teamtree.TeamState], agy_updates: dict[str, Session] | None = None) -> None:
        if agy_updates:
            changed_agy = self.engine.team_sessions(agy_updates)
            if changed_agy:
                self.sync_rows(changed_agy)
                self.update_dashboard()
        now = time.monotonic()
        for event in self.engine.heard(states):  # the first look at a team only takes note
            self.team_flights.setdefault(event.team, []).append((event.message, now))
            if self.team_graph is not None and self.team_graph.team.name == event.team:
                self.team_graph.launch_flight(event.message)
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
        words, tone = VM.member_words(session)
        return words, TONE[tone]

    def show_team(self, state: teamtree.TeamState) -> None:
        """The preview of a team's row: the lead and each member on its wire, with the messages along
        it, and the newest messages beside it."""
        colours = team_colours(state.team, self.accent())
        if self.split:
            self.show_team_pane(state, colours)
            return
        self.drop_team_graph()
        looks = {name: self.member_look(session) for name, session in state.sessions.items()}
        # Narrower than this, the tree takes the facts column's room too: the latest word on each
        # wire says much of what the list of messages would.
        wide = self.size.width >= TEAM_MESSAGES_FROM
        self.inspector.set_class(not wide, "team-only")
        width = max(30, (self.detail.content_size.width if wide else self.inspector.content_size.width - 1) or 70)
        words = teamwords.team_detail(state, state.tokens(self.session_tokens), self.accent(),
                                      self.members_open(state.team.name))
        hint = self.team_keys(state)
        goal = cell(words.goal)
        goal.truncate(width, overflow="ellipsis")
        lines = [cell(words.head), goal, *teamtree.tree(state, width, looks, colours,
                                                        self.team_flights.get(state.team.name, ()), time.monotonic())]
        if words.closed:
            lines.insert(2, cell(words.closed))
        if not wide:  # no room beside it: its keys under it
            hint.truncate(width, overflow="ellipsis")
            self.detail.update(Text("\n").join([*lines, hint]))
            return
        self.detail.update(Text("\n").join(lines))
        beside = max(20, (self.meta.content_size.width or 44) - 2)
        hint = keys_in(beside, *self.team_key_pairs(state))
        # The messages take what the heading, a blank line and the keys leave, so the keys always show.
        room = max(2, (self.inspector.content_size.height or 8) - 2 - len(hint.split()))
        self.meta.update(Group(Text("MESSAGES", style=f"bold {MUTED}"),
                               teamtree.latest(state, colours, room, beside), Text(""), hint))

    def show_team_pane(self, state: teamtree.TeamState, colours: dict[str, str]) -> None:
        """A team's row with the preview beside the list: its head and goal, the team view's cards
        (a PaneGraph, kept while the cursor stays on the team), then its newest messages and its keys,
        the messages taking the height the rest leaves. The cards only when they leave room for two
        messages and every key: else the compact tree, as under the list (a big team, a low window)."""
        self.inspector.remove_class("team-only")
        area = self.inspector.content_region
        width = area.width or max(PANE_LEAST, min(PANE_MOST, self.size.width - PANE_TAKES)) - 4  # padding 2+2
        top = area.y or 2
        bottom = area.bottom if area.height else self.size.height - 3  # before the first layout, a guess
        words = teamwords.team_detail(state, state.tokens(self.session_tokens), self.accent(),
                                      self.members_open(state.team.name))
        goal = cell(words.goal)
        goal.truncate(width, overflow="ellipsis")
        lines = [*wrap_at(cell(words.head), width), goal]
        if words.closed:
            lines.append(cell(words.closed))
        beside = max(20, width - 2)  # a body's indent: no line wraps
        hint = keys_in(beside, *self.team_key_pairs(state))
        keys_h = len(hint.split())
        graph = self.team_graph
        if graph is None or graph.team.name != state.team.name or graph.accent != self.accent():
            graph = PaneGraph(state.team, self.accent(), classes="pane-graph")  # no id: the one going may still be there
        graph.team, graph.palette, graph.pane_width = state.team, colours, width
        cards_h = graph.height_for(graph.layout_boxes(width)[1])
        # Under the words: a blank line, the cards, a blank line, MESSAGES and two of them at least, a
        # blank line and the keys.
        words_h = sum(len(line.wrap(self.console, width)) for line in lines)  # rows as shown: the note can wrap
        if words_h + 1 + cards_h + 1 + 1 + 2 + 1 + keys_h <= bottom - top:
            if graph is not self.team_graph:
                self.drop_team_graph()
                self.team_graph = graph
                graph.selected = len(graph.order)  # no card picked: ← → are the list's here
                self.inspector.mount(graph, after=self.detail)
            graph.styles.height = cards_h  # measured: laid out at its own width, never a guessed one
            graph.sessions, graph.said = dict(state.sessions), list(state.said)
            graph.unread = {}
            for message in state.said:
                if not message.delivered:
                    graph.unread[message.receiver] = graph.unread.get(message.receiver, 0) + 1
            graph.refresh(layout=True)
            used = words_h + 1 + cards_h
        else:
            self.drop_team_graph()
            looks = {name: self.member_look(session) for name, session in state.sessions.items()}
            tree = teamtree.tree(state, width, looks, colours, self.team_flights.get(state.team.name, ()),
                                 time.monotonic())
            lines += tree
            used = words_h + sum(len(line.wrap(self.console, width)) for line in tree)
        self.detail.update(Text("\n").join(lines))
        room = max(2, bottom - top - used - 1 - 1 - 1 - keys_h)  # less the blank line, MESSAGES, a blank line
        self.meta.update(Group(Text("MESSAGES", style=f"bold {MUTED}"),
                               teamtree.latest(state, colours, room, beside), Text(""), hint))
        self.pane_fit = (top, bottom, width)
        self.call_after_refresh(self.fit_team_pane)

    def fit_team_pane(self) -> None:
        """Once laid out: the team's preview shown again if the room its messages were measured for
        changed meanwhile (the cards just came in, the footer took a second row on the team's row).
        Shown with what it measures now, it stops there."""
        if not self.split or self.team_at() is None:
            return
        area = self.inspector.content_region
        if area.height and (area.y, area.bottom, area.width) != self.pane_fit:
            self.update_detail()

    def drop_team_graph(self) -> None:
        """The team's cards out of the preview: a session's row, or the preview under the list."""
        if self.team_graph is not None:
            self.team_graph.remove()
            self.team_graph = None

    def team_keys(self, state: teamtree.TeamState) -> Text:
        """The keys of a team's row as it stands: closed, folded or unfolded."""
        return keys(*self.team_key_pairs(state))

    def team_key_pairs(self, state: teamtree.TeamState) -> tuple[tuple[str, str], ...]:
        name = state.team.name
        return VM.team_key_pairs(self.team_open(name), name in self.unfolded)

    def member_messages(self, session: Session, room: int) -> Text:
        """'3 in · 2 out · ← lead 2m: Heads-up…' for a member's own preview."""
        return clipped_line(self.member_parts(session), room)

    def member_parts(self, session: Session) -> list[sessionwords.Part]:
        """A member's messages on one line, as parts (sessionwords.messages)."""
        name, member = self.member_rows[session.session_id]
        state = self.team_states[name]
        return sessionwords.messages(state.said, member, team_colours(state.team, self.accent()))

    def toggle_folded(self, name: str) -> None:
        self.unfolded ^= {name}
        self.refresh_rows()

    def action_fold_teams(self) -> None:
        """E: every team in the list unfolded, its members under its row; pressed again, every team back
        to one row. Any team still folded (one that came in since) makes the next press unfold."""
        names = {state.team.name for state, _ in self.blocks}
        if not names:
            self.notify("No team in the list to unfold.", timeout=4)
            return
        key = self.cursor_key()
        self.unfolded = self.unfolded - names if names <= self.unfolded else self.unfolded | names
        self.refresh_rows()
        if key is not None and key not in self.table.rows:  # a member folded away: its team's row, not a stray one
            team = self.member_rows.get(key, ("", ""))[0]
            if f"{TEAM_ROW}{team}" in self.table.rows:
                self.table.move_cursor(row=self.table.get_row_index(f"{TEAM_ROW}{team}"))

    def toggle_team_selected(self, name: str) -> None:
        """Tick a closed team's members, or untick them all when they are all ticked."""
        closed = {s.session_id for _, s in self.team_members(name) if s.status is not Status.RUNNING}
        if not closed:
            return
        self.selected = self.selected - closed if closed <= self.selected else self.selected | closed
        self.sync_rows(closed)
        self.update_status()

    # --- a team's row: its keys -----------------------------------------------------------------

    def team_members_now(self, state: teamtree.TeamState) -> dict[str, Session | None]:
        """A team's members, each with its session as the list has it now (one never started: None)."""
        return {name: self.sessions.get(s.session_id, s) if s is not None else None
                for name, s in state.sessions.items()}

    def start_team(self, state: teamtree.TeamState) -> None:
        """Every member not open: a closed one resumed as itself, one never started new; the lead last
        (teamkeys.TeamKeys.start)."""
        self.row_keys.start(state.team, self.team_members_now(state))

    def cold_start_ok(self, team: teams.Team, members: dict[str, Session | None]) -> bool:
        """Whether a closed team starts now (teamkeys.TeamKeys.cold_start_ok): at once, unless its
        members' cache is gone; then a second press within COLD_AGAIN_SECONDS starts it."""
        return self.row_keys.cold_start_ok(team, members)

    def add_to_team(self, state: teamtree.TeamState) -> None:
        """New members for a team: at work, each opens and introduces itself to the lead; closed, they
        join it and start with it (teamkeys.TeamKeys.add)."""
        at_work = self.team_open(state.team.name)
        self.push_screen(LineDialog(ADD_QUESTION, ADD_EXAMPLE, enter=ADD_ENTER),
                         lambda text: self.row_keys.add(state.team, text, at_work))

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """A team's sessions: in tabs, or for a team whose members run in the background, its members
        as background sessions first and then the lead's tab (teamkeys.TeamKeys.open_team). A member
        opened a moment ago, here or by `tabmancer team start`, is not opened again: s twice, while the
        first claude --bg calls were still running, started every member twice."""
        self.row_keys.open_team(team, launches)

    @work(thread=True, group="team-background")
    def start_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        """claude --bg for each (actions.start_in_background), then the tabs: the lead finds its members up."""
        for note in actions.start_in_background(team, launches, run_in_background):
            self.call_from_thread(self.notify, note.words, severity=note.severity, timeout=note.seconds)
        if then:
            self.call_from_thread(self.open_sessions, then)

    def close_team(self, state: teamtree.TeamState) -> None:
        """x, twice: every open member's tab closes, as on /exit; the sessions stay (s starts them)."""
        self.row_keys.close(state.team, self.team_members_now(state))

    @property
    def closing_team(self) -> str | None:
        """The team x was pressed on once (its row): x again closes its members' tabs."""
        return self.row_keys.closing

    @property
    def cold_team(self) -> str | None:
        """The team started once while its cache was gone: s again starts it."""
        return self.row_keys.cold

    @work(thread=True, group="team-close")
    def close_team_tabs(self, names: list[str]) -> None:
        note = actions.closed_note(names, close_tabs)
        self.call_from_thread(self.notify, note.words, severity=note.severity, timeout=note.seconds)

    def action_new_sessions(self) -> None:
        found = newsessions.projects(list(self.sessions.values()))
        ticked = {self.project_filter} if self.project_filter != ALL_PROJECTS else set()

        def start(request: newsessions.Request | None) -> None:
            if not request or not request.folders:
                return
            try:
                built = newforms.build(request, UI_TRIES)
            except Busy as error:
                self.notify(teams.not_saved_words("Nothing was started", error), severity="warning", timeout=8)
                return
            if built.team is not None:  # the lines as a team of peers: saved, opened, and shown
                self.open_team(built.team, built.launches)
                self.push_screen(TeamScreen(built.team, self.accent()))
                return
            self.open_sessions(built.launches)

        self.push_screen(newsessions.NewSessions(found, ticked), start)

    def action_settings(self) -> None:
        """,: which CLIs are listed and the main one (CliSettings saves them); then the list read again when
        other CLIs are listed now. config.json there but unreadable: nothing changed, and that is said."""
        def saved(done: clis.Saved | None) -> None:
            if done is None:
                return
            if done.words:
                self.notify(done.words, **({"timeout": 4} if done.kept else {"severity": "warning", "timeout": 8}))
            if done.changed:
                self.reload_sessions()

        self.push_screen(CliSettings(), saved)

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
            self.notify(backgroundwords.NO_ANSWER, severity="warning")
            return

        def attach(job: background.Job | None) -> None:
            if job is not None:
                self.open_sessions([backgroundwords.attach_launch(job)])

        self.push_screen(background.BackgroundDialog(found), attach)

    def uses_agy(self) -> bool:
        """An agy session in the list, or an agy member in a team: then agy's quota is shown."""
        return store.uses_agy(self.sessions.values(), self.team_states)

    @work(thread=True, exclusive=True, group="usage")
    def refresh_usage(self) -> None:
        before = accounts.claude()
        # agy's quota as saved (no agy is run for it), only while agy is in use: read with the plan limits.
        self.agy_bar = agy.bar_view() if self.call_from_thread(self.uses_agy) else {}
        limits = engine.read_usage(self.acts, before)
        if limits is not None:
            self.call_from_thread(self.show_usage, limits)

    def show_accounts(self) -> None:
        """The accounts in the top bar, beside the plan limits: read from the CLIs' own files, again
        only when they changed. Claude Code's is remembered for A, and a switch to another one frees
        the sessions a usage limit stopped on the other."""
        found = accounts.signed_in()
        self.topbar.show_accounts(found, accounts.shared_emails())
        self.engine.signed_in(found)

    def show_rows(self, sessions: list[Session]) -> None:
        for session in sessions:
            self.update_row(session)
        self.update_detail()

    def action_accounts(self) -> None:
        """A: the Claude accounts you use, to sign in with another (Claude Code's own sign-in, in a tab),
        its page opening where you picked for that account (browsers)."""
        from .accountsview import AccountsDialog

        current = accounts.claude()
        seen = accountwords.listed(current, accounts.known())
        stopped = sum(1 for s in self.sessions.values() if at_limit(s))
        places = browsers.places()

        def chosen(picked: tuple[str, str] | None) -> None:
            if picked is None:
                return
            if self.after_update("the sign-in opens", lambda: chosen(picked)):
                return
            email, place = picked
            said = accountwords.sign_in(email, place, places, open_tabs)  # open_tabs: the demo stands in for it
            if said is not None:
                self.say_note(said)

        self.push_screen(AccountsDialog(current, seen, stopped, places, accounts.opens_in(), usage.last_by_account()),
                         chosen)

    def ran_elsewhere(self, sessions: list[Session]) -> list[Session]:
        """Those that last ran on another organisation than the account signed in now (accountwords)."""
        return accountwords.ran_elsewhere(self.account, sessions)

    def org_words(self) -> str:
        """Where a conversation goes now: 'your personal account', 'the Example Labs organisation'."""
        return accountwords.goes_to(self.account)

    @staticmethod
    def held(session: Session) -> list[str]:
        """What its tool calls read: those of its tail as read, and any further back (sensitive_calls)."""
        return accountwords.held(session)

    def tell_elsewhere(self, session: Session, what: str) -> None:
        """One line, never a stop: what of a session that ran on another organisation goes to this one."""
        note = accountwords.elsewhere_note(session, what, self.account)
        if note is not None:
            self.say_note(note)

    def other_accounts_note(self, opens: datetime | None) -> tuple[str, str]:
        """For a limit's note: how the other Claude accounts stood when last read (accountwords)."""
        return accountwords.other_accounts_note(self.account, self.signed_as, opens)

    def other_accounts_words(self) -> list[str]:
        """How the other Claude accounts' limits stood when last read, one line each (accountwords)."""
        return accountwords.other_accounts_words(self.signed_as)

    @work(thread=True, exclusive=True, group="updates")
    def check_updates(self) -> None:
        """A newer tabmancer on PyPI (updates.news: asked once a day at most), told once."""
        found = alerts.newer_tabmancer(self.told_update)
        if found:
            self.call_from_thread(self.engine.newer, *found)

    @work(thread=True, exclusive=True, group="claude-update")
    def update_claude(self) -> None:
        """claude update (claudeupdate.py), and with a new version, whether it still takes every flag
        tabmancer runs it with (doctor's check)."""
        self.call_from_thread(self.claude_updated, *alerts.run_claude_update())

    def claude_updated(self, outcome: claudeupdate.Outcome, missing: list[str] | None) -> None:
        """Done: what waited for it opens (Engine.claude_updated), and a quit asked for meanwhile goes on."""
        self.engine.claude_updated(outcome, missing)
        self.finish_quitting()

    def after_update(self, what: str, then: Callable[[], None]) -> bool:
        """While claude update runs: then, once it is done (Engine.after_update)."""
        return self.engine.after_update(what, then)

    def show_newer(self, version: str) -> None:
        """Beside the name in the top bar while the dashboard is open."""
        self.topbar.put("brand", Text.assemble("◆ tabmancer", (f"  ↑ {version}", f"bold {GREEN}")))

    def show_usage(self, limits: list[usage.Limit]) -> None:
        self.limits = limits
        bar = self.agy_bar
        self.topbar.show_usage(limits, bar.get("parts", []), agy_first=bar != {} and clis.main_cli() == "agy")
        self.engine.usage(limits, bar)  # Bit: a limit at 85% or more; every agy model out, once

    def place_mascot(self) -> None:
        """Bit walks on the preview's top edge; without Bit, a blank row separates the two."""
        if not hasattr(self, "inspector"):
            return  # a resize before the dashboard is mounted
        if self.split:  # beside the list: Bit stands on the list's own floor
            self.inspector.styles.margin = (0, 0, 0, 1)
            return
        shown = not self.mascot.has_class("hidden") and not self.dashboard.has_class("short")
        self.inspector.styles.margin = (0 if shown else 1, 0, 0, 0)

    def action_toggle_mascot(self) -> None:
        """b: Bit in real pixels, then calm (fewer moves), then in block characters, then away,
        then back; pixels only where the terminal draws them. Remembered."""
        ways = (["pixels"] if sixel.terminal() else []) + ["calm", "blocks", "away"]
        bit = self.mascot
        now = "away" if bit.has_class("hidden") else "calm" if bit.calm else "pixels" if bit.picture else "blocks"
        way = ways[(ways.index(now) + 1) % len(ways)]
        self.show_mascot(way)
        said = {"pixels": "Bit in real pixels.", "calm": "Bit, calm: fewer moves, no confetti.",
                "blocks": "Bit in block characters.", "away": "Bit is away: b brings it back."}[way]
        saved = merge_json("config.json", mascot=way)
        self.notify(said if saved else f"{said} {NOT_SAVED}", timeout=3 if saved else 8)

    def show_mascot(self, way: str) -> None:
        self.mascot.set_class(way == "away", "hidden")
        self.mascot.calm = way == "calm"
        if way != "away":
            self.mascot.use_pixels(way in ("pixels", "calm"))
        self.place_mascot()

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
        saved = merge_json("config.json", theme=self.theme)
        said = f"Theme: {THEMES[self.theme][0]}"
        self.notify(said if saved else f"{said}. {NOT_SAVED}", timeout=2 if saved else 8)

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
        self.modes[session.session_id] = actions.next_mode(session, self.mode(session))
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
        self.refresh_rows(keep_place=False)
        if len(event.value.strip()) >= search.SHORTEST:
            self.index_search()  # what the open sessions said since: only that is read

    @work(thread=True, exclusive=True, group="search")
    def index_search(self) -> None:
        if self.search.update(list(self.sessions.values())):
            self.call_from_thread(self.search_ready)

    def search_ready(self) -> None:
        self.finder.renew()
        if len(self.filter_text.strip()) >= search.SHORTEST:
            self.refresh_rows(keep_place=False)

    def said_in(self) -> set[str]:
        """The sessions in whose conversation the filter's words were said; worked out once
        per filter and index, since the list asks many times a second."""
        return self.finder.said_in(self.filter_text, self.sessions.keys())

    def action_back(self) -> None:
        """Esc: the text filter cleared, else a card's, else quit."""
        box = self.filter_box
        step = VM.back_step(box.has_class("visible"), self.card_filter)
        if step == "search":
            box.value = ""
            box.remove_class("visible")
            self.table.focus()
        elif step == "card":
            self.action_card(self.card_filter)
        elif self.quit_ok("Esc"):
            self.exit(None)

    async def action_quit(self) -> None:
        """q (and Ctrl+Q): quit, unless handovers are queued here (quit_ok)."""
        if self.quit_ok("q"):
            await super().action_quit()

    def quit_ok(self, key: str) -> bool:
        """Whether a quit key quits now. A handover being written (minutes) would be lost, its old session
        closed already when it writes its own: the first press says so, and a second within
        QUIT_AGAIN_SECONDS quits once it is written and its fresh session opened (finish_quitting). With
        handovers queued here (h on sessions at work), which go with the dashboard, the first press says so
        and a second quits. The presses are on row_keys' clock: no timer to forget them."""
        writing = list(self.handing.values())
        if writing:
            if self.quit_after or self.row_keys.again("quit", "dashboard", QUIT_AGAIN_SECONDS):
                self.quit_after = True
                self.say_note(handoverwords.finishing_note(writing))
            else:
                self.say_note(handoverwords.writing_on_quit(writing, key))
            return False
        count = len(self.queued)
        if not count or self.row_keys.again("quit", "dashboard", QUIT_AGAIN_SECONDS):
            return True
        self.say_note(handoverwords.queued_on_quit(count, key))
        return False

    def finish_quitting(self) -> None:
        """A quit asked for while handovers were written: once none is, and the fresh sessions waiting for a
        claude update have opened, it quits (still asked first about handovers queued here). A team's
        handover dialog shown meanwhile is left to be answered: quitting would lose what it holds."""
        if not self.quit_after or self.handing or self.engine.state.updating_claude:
            return
        self.quit_after = False
        if isinstance(self.screen, TeamHandoverDialog):
            self.say_note(handoverwords.team_written_on_quit(self.screen.team.name))
            return
        if self.quit_ok("q"):
            self.exit(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # With a dialog or the team view on top, the dashboard's keys belong to it (footer.allowed).
        return footer.allowed(action, on_top=len(self.screen_stack) > 1, monitor=self.watch is not None,
                              projects_focused=self.focused is self.projects, live_mode=self.live_mode,
                              own=DASHBOARD_ACTIONS, sessions=self.sessions.values(), at=self.footer_context)

    # --- the footer follows the row ---------------------------------------------------------------

    def footer_context(self) -> footer.Row:
        """What the footer's words and dimmed keys depend on: the row highlighted (footer.row)."""
        if not hasattr(self, "table") or not self.table.row_count:
            return footer.NONE
        team = self.team_at()
        if team is not None:
            name = team.team.name
            return footer.row(self.sessions.values(), (name, self.team_open(name), name in self.unfolded), None,
                              len(self.selected), self.member_rows)
        return footer.row(self.sessions.values(), None, self.current(), len(self.selected), self.member_rows)

    def refresh_footer(self) -> None:
        """Make the footer again for the row at hand, only when what it shows would change."""
        context = self.footer_context()
        if context != self.footer_was and self.screen_stack:
            self.footer_was = context
            self.refresh_bindings()

    def footer_words(self, action: str) -> str | None:
        """What a key does on the row at hand, where it is not what its own name says (footer.words)."""
        return footer.words(action, self.footer_context(), len(self.selected))

    @staticmethod
    def footer_group(action: str) -> str | None:
        """The ? screen's group of a key, which the footer keeps together. ? itself belongs to none:
        it stands beside Enter, which the footer puts first."""
        return None if action == "keys" else KEY_GROUPS.get(action)

    def action_save_workspace(self) -> None:
        chosen = [s for s in self.sessions.values() if s.session_id in self.selected]
        if not chosen:
            self.say_note(workspacewords.tick_first_note())
            return

        def save(name: str | None) -> None:
            if not name or not workspaces.clean_name(name):
                return
            workspace = workspaces.from_sessions(name, chosen, self.modes)
            if workspaces.keep(workspace, UI_TRIES):  # the others kept as the file has them now
                self.say_note(workspacewords.saved_note(workspace))
            else:
                self.say_note(workspacewords.not_kept_note(workspace.name))

        self.push_screen(NamePrompt(workspacewords.name_heading(len(chosen)), placeholder=workspacewords.PLACEHOLDER),
                         save)

    def action_open_workspace(self) -> None:
        saved = workspaces.load()
        if not saved:
            self.say_note(workspacewords.none_yet_note())
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
                self.say_note(workspacewords.nothing_note(notes))

        self.push_screen(WorkspaceList(saved), open_it)

    # --- live view ------------------------------------------------------------------

    def action_toggle_live(self) -> None:
        self.live_mode = not self.live_mode
        self.fit_sidebar()  # the live view's other columns are wider
        self.setup_columns(keep=True)
        self.refresh_bindings()

    def poll_tick(self) -> None:
        if not self.screen_stack:  # the app is closing: the timer fired once more (seen on CI)
            return
        self.polls += 1
        self.poll_live(full=self.polls % FULL_POLL_EVERY == 0)
        self.poll_teams()

    def action_next_waiting(self) -> None:
        """Go to the next open session that needs you (a question, a permission), one after another;
        when none does, the next one waiting for you (its turn over)."""
        turn = actions.next_waiting(self.sessions.values(), self.waiting_group, self.waiting_turn)
        if turn.session is None:
            self.say_note(actions.NONE_WAITING)
            return
        session, self.waiting_group, self.waiting_turn = turn.session, turn.group, turn.turn
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
        asked = limitwords.ask(self.sessions, self.limits, self.account, self.other_accounts_words(), self.armed)
        if isinstance(asked, actions.Note):
            self.notify(asked.words, timeout=asked.seconds)
            return
        stopped, when = asked.stopped, asked.when

        def chosen(picked: tuple[str, list[str]] | None) -> None:
            if picked is None:
                return
            answer, ids = picked
            if not self.engine.choose(answer, ids, stopped, when):
                return
            for session in stopped:
                self.update_row(session)
            self.update_detail()

        self.push_screen(LimitDialog(stopped, when, asked.armed, asked.plan, asked.room, asked.away, asked.where),
                         chosen)

    def continue_these(self, stopped: list[Session], unasked: bool = False) -> None:
        """Each goes on where it stopped: a closed one in a new tab, an open one with the prompt
        typed into its own tab. unasked: by itself, the limit lifted (open_unasked). Engine.continue_these."""
        self.engine.continue_these(stopped, unasked)

    def load_armed(self) -> None:
        """The sessions to continue at their reset, as saved: the dashboard that acts saves them as it
        continues them, and one that watches shows them."""
        self.engine.load_armed()

    def save_armed(self) -> None:
        """The sessions to continue at their reset, saved: only by the dashboard that acts. A monitor
        and a dashboard that watches continue nothing, and their copy is the saved one, read again."""
        self.engine.save_armed()

    def continue_due(self) -> None:
        """Continue the sessions whose limit has lifted: a closed one in a new tab, an open one with
        the prompt typed into its own tab, which is never closed. One gone on by hand meanwhile is
        left alone."""
        self.engine.due()

    @work(thread=True, group="limits")
    def continue_open(self, still_open: list[Session], opened: int) -> None:
        """The continue prompt goes into each one's tab, as if you typed it. Its tab is never closed:
        one that will not take the prompt is left as it is, and Bit says which (Engine.continued)."""
        self.call_from_thread(self.engine.continued, actions.type_continue(still_open, opened))

    def check_limits(self) -> None:
        """Forget the sessions gone on from a limit; once per session, Bit says which ones a
        usage limit stopped and that c continues them (Engine.limits)."""
        self.engine.limits()

    def limits_at(self, at: set[str]) -> None:
        if at != self.limited and self.screen_stack:  # not while the app shuts down: no screen then
            self.limited = at
            self.refresh_bindings()  # c is in the footer only while there is something to continue

    def save_queued(self) -> None:
        """This window's queued handovers, saved for the other windows to show (actions.read_queued)."""
        actions.save_queued(self.queued)

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
        self.change_marks(lambda now: marks.hide(now, chosen))

    def hide_project(self) -> None:
        key = self.projects.highlighted_id()
        if not key or key == ALL_PROJECTS:
            self.notify("Pick a project in the sidebar to hide it, or a session in the list.", timeout=4)
            return
        sessions = [s for s in self.sessions.values() if folder_key(s.cwd) == key]
        self.change_marks(lambda now: marks.hide_project(now, key, sessions))

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.marked()

    def action_pin(self) -> None:
        """f: the ticked sessions, or the highlighted one, pinned above the rest; again, unpinned."""
        chosen = self.chosen()
        if not chosen:
            return
        self.change_marks(lambda now: marks.pin(now, chosen))

    def change_marks(self, how: Callable[[marks.Marks], marks.Changed]) -> None:
        """f, x: marks.json changed as it is now (marks.change: what another dashboard or a window changed
        since this one read it stays), said, and the list following. Ticks on rows it took out of sight
        go: Enter would open a session the list no longer shows. When marks.json could not be read (saving
        over it would wipe what it keeps) or the change could not be saved, nothing changes, and it says so."""
        changed = marks.change(how)
        if isinstance(changed, marks.Refused):
            self.notify(changed.words, severity="warning", timeout=6)
            return
        self.marks, done = changed
        self.selected -= done.hid
        self.notify(done.words, timeout=done.seconds)
        self.marked()

    def reread_marks(self) -> None:
        """At each live poll: what another dashboard or a window pinned or hid since this one read
        marks.json shows here (ticks on rows it took out of sight go, as with x). Read once, with no
        wait on the UI thread: held by another writer or unreadable, the list keeps its marks and the
        next poll reads again."""
        now = marks.Marks.read(tries=1)
        if now is None or now == self.marks:
            return
        self.marks = now
        self.selected -= {sid for sid in self.selected if sid in self.sessions and now.is_hidden(self.sessions[sid])}
        self.marked()

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
        live, kept = store.live_entries(full, self.background_live)
        if full:
            idle = sum(bool(e.get("pid")) and background.state_of(e).lower() in background.IDLE
                       for e in kept.values())
            if not self.told_idle:
                self.call_from_thread(self.engine.idle, idle)
        updates = store.fresh_sessions(known, live, full)
        elsewhere = actions.read_queued(others=True)  # the other windows' queued handovers, for the rows
        self.call_from_thread(self.apply_live_updates, updates, full, kept, elsewhere)  # kept: on the UI thread

    def apply_live_updates(self, updates: dict[str, Session], full: bool = False,
                           kept: dict[str, dict] | None = None, elsewhere: dict[str, str] | None = None) -> None:
        self.reread_marks()
        self.engine.live(updates, full, self.resting_members(), kept, elsewhere)

    @property
    def shown_queued(self) -> dict[str, str]:
        """The handovers the list shows as queued: this window's (self.queued, which q warns about and
        saves) and the other open windows' (queued_elsewhere), as the desktop window shows them all."""
        return {**self.queued_elsewhere, **self.queued}

    def monitoring(self) -> bool:
        return self.watch is not None

    def flash_changed(self, updates: dict[str, Session]) -> None:
        until = time.monotonic() + rowwords.FLASH_SECONDS
        for sid in rowwords.changed(self.sessions, updates.values()):
            self.flash[sid] = until

    def untick_open(self, updates: dict[str, Session]) -> None:
        """A session that turns out to be open was not left open at shutdown: untick it if it was
        ticked automatically. Terminal sessions that are open can never be ticked (VM.untick_open)."""
        self.selected, self.preselected = VM.untick_open(self.selected, self.preselected, updates.values())

    def rows_changed(self, ids: set[str]) -> None:
        self.sync_rows(ids)
        self.update_dashboard()
        self.check_limits()

    def who(self, session: Session) -> str:
        """A session as a note names it: its project, or a team member as 'team: member' (VM.who)."""
        return VM.who(session, self.member_rows)

    def tell_fallback(self, session: Session) -> None:
        """Another model took over in an open session by itself (alerts.fallback_event)."""
        self.tell(alerts.fallback_event(session, self.who(session)))

    def tell(self, event: alerts.Event) -> None:
        """An event as the dashboard says it: its note, the bell where the live list shows, Bit."""
        ring = event.bell and self.live_mode
        turned = event.kind in (alerts.NEEDS_YOU, alerts.DONE)
        if ring and turned:  # a turn rings before its note, a fallback after (as always)
            self.bell()
        if event.note is not None:
            self.say_note(event.note)
        if ring and not turned:
            self.bell()
        if event.bit is not None:
            self.show_bit(event.bit)

    def say_note(self, note: actions.Note) -> None:
        """A note, with only what it sets beyond notify's defaults (its title, severity, timeout)."""
        extra = {}
        if getattr(note, "title", ""):
            extra["title"] = note.title
        if note.severity != "information":
            extra["severity"] = note.severity
        if note.seconds is not None:
            extra["timeout"] = note.seconds
        self.notify(note.words, **extra)

    def show_bit(self, bit: alerts.Bit) -> None:
        """What Bit does for an event: the front end's part (alerts.Bit says what happened, how loud)."""
        tone = {} if bit.tone == "plain" else {
            "colour": {"amber": AMBER, "green": GREEN, "red": RED}.get(bit.tone) or self.accent()}
        timing = {} if bit.seconds is None else {"seconds": bit.seconds}
        if bit.how == "celebrate":
            self.mascot.celebrate(bit.words)
        elif bit.how == "alert":
            self.mascot.alert(bit.words)
        elif bit.how == "worry":
            self.mascot.worry(bit.words, **tone)
        elif bit.how == "react":
            self.mascot.react(bit.mood, bit.words, **tone, **timing)
        else:
            self.mascot.say(bit.words, **tone, **timing)

    def resting_members(self) -> set[str]:
        """The sessions of the teams' members other than their leads: idle, they wait for the lead's
        next message, not for you. A team of peers has none: each one, idle, waits for you."""
        return store.resting_ids(self.team_states)

    def tell_desktop(self, turned: list[Session]) -> None:
        """A desktop notification too, for when the dashboard is not in front: Windows asks what
        is (toast.show); elsewhere the terminal reports focus."""
        if not toast.enabled() or (sys.platform != "win32" and self.app_focus):
            return
        self.send_toast(*alerts.toast_words(turned, lambda opens: self.other_accounts_note(opens)[0]))

    @work(thread=True, group="toast")
    def send_toast(self, title: str, body: str) -> None:
        """A desktop note: only from the dashboard that acts, or each one open would send it."""
        if self.acts:
            toast.show(title, body, dashboard=self.TITLE)

    def retry_git(self) -> None:
        self.git_retry = None
        self.refresh_git()

    @work(thread=True, exclusive=True, group="git")
    def refresh_git(self) -> None:
        # A monitor shows one team: its folder and worktrees, not every session's.
        states, failed = gitstate.read_all(store.git_folders(self.sessions.values(), self.watch))
        self.call_from_thread(self.show_git, states, failed)

    def show_git(self, states: dict[str, gitstate.GitState], failed: set[str] = frozenset()) -> None:
        # A folder git could not answer for keeps what was known, and is asked again soon: once, when it
        # has just begun failing. Each retry that failed set another, and the minute's refresh started
        # one more chain each time: a folder git always refuses (dubious ownership) ran git without end.
        states = store.kept_git(self.git, states, failed)
        newly, self.git_failed = set(failed) - self.git_failed, set(failed)
        if newly and self.git_retry is None:
            self.git_retry = self.set_timer(10, self.retry_git)
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
        self.tell_elsewhere(session, sessionwords.SUMMARY_GOES)
        self.summarizing.add(session.session_id)
        self.update_detail()
        self.run_summary(session)

    @work(thread=True)
    def run_summary(self, session: Session) -> None:
        try:
            summary, error = summarize.summarize(session), None
        except (summarize.SummaryError, OSError) as failure:
            summary, error = None, str(failure)
        self.call_from_thread(self.summary_done, session, summary, error)

    def summary_done(self, session: Session, summary: summarize.Summary | None, error: str | None) -> None:
        self.summarizing.discard(session.session_id)
        if summary is not None:
            self.summaries[session.session_id] = summary.entry  # shown, saved or not (the note says)
        note = sessionwords.summary_note(summary, error)
        if note is not None:
            self.notify(note.words, severity=note.severity, timeout=note.seconds or 4)
        # By id: a live poll replaces an open session's object every few seconds.
        current = self.current()
        if current is not None and current.session_id == session.session_id:
            self.update_detail()

    def action_open(self) -> None:
        if isinstance(self.focused, Input):  # Enter in the filter box only confirms it
            self.table.focus()
            return
        ticked = [s for s in self.sessions.values() if s.session_id in self.selected]
        session = self.current() if not ticked else None
        enter = actions.enter_choice(ticked, session, self.team_at() if not ticked and session is None else None)
        if enter.team is not None:  # a team's row: its view, open or closed (e unfolds it, s starts it)
            self.push_screen(TeamScreen(enter.team.team, self.accent()))
            return
        if enter.note is not None:
            self.say_note(enter.note)
            return
        if enter.tab is not None:
            self.go_to_tab(enter.tab)
            return
        chosen = self.openings.choose(enter.open or [])  # not the ones opened a moment ago, still starting
        if not chosen:
            self.say_note(openings.ALREADY)
            return
        self.open_sessions(self.openings.launches(chosen, self.mode, self.background_live))

    def open_sessions(self, launches: list[Launch], window: str | None = None, updated: bool = False) -> None:
        """Open sessions in new tabs beside the dashboard, which stays open (Engine.open)."""
        self.engine.open(launches, window, updated)

    def open_tabs(self, launches: list[Launch], window: str | None) -> list[str]:
        """New tabs (launch.open_tabs), in that window when given."""
        return open_tabs(launches) if window is None else open_tabs(launches, window)

    def untick(self, ids: set[str]) -> None:
        self.selected -= ids

    def exit_with(self, leftover: list[str]) -> None:
        """No terminal that can open tabs: close, and leave the commands to run by hand."""
        self.exit(leftover)

    def later(self, seconds: float, then: Callable[[], None]) -> None:
        self.set_timer(seconds, then)

    def opened_rows(self, ids: set[str]) -> None:
        for sid in ids & self.sessions.keys():
            self.update_row(self.sessions[sid])
        self.update_status()

    def check_came_up(self, ids: set[str]) -> None:
        """Say which of the sessions opened a while ago never showed up open: Claude Code would not
        resume one (its folder gone, say), and its tab says why. After a reboot, with a dozen tabs
        opening at once, one of them is easy to miss."""
        self.engine.came_up_check(ids)

    def open_elsewhere(self, launches: list[Launch]) -> set[str]:
        """Which of these sessions to resume a Claude process has open right now (actions.open_elsewhere)."""
        return actions.open_elsewhere(launches, self.live_ids)

    def live_ids(self) -> set[str]:
        """The sessions a Claude process has open right now, asked afresh, not taken from the list: the
        pid files, and the background sessions of the last full poll (they have no pid file), or before
        the first full poll has come back, `claude agents --json` itself (half a second)."""
        if self.full_polls:
            return {entry["sessionId"] for entry in _registry_sessions()} | set(self.background_live)
        return set(live_sessions())

    @work(thread=True, exclusive=True, group="focus")
    def go_to_tab(self, session: Session) -> None:
        """Switch to the Windows Terminal tab where this session is open (actions.go_to_tab)."""
        note = actions.go_to_tab(session)
        self.call_from_thread(self.notify, note.words, severity=note.severity, timeout=note.seconds)


def forward(name: str) -> property:
    """SessionPicker.<name> is its engine's state.<name>: read and set there, one copy."""
    return property(lambda self: getattr(self.engine.state, name),
                    lambda self, value: setattr(self.engine.state, name, value))


class RowSurface:
    """What a team's keys (teamkeys.TeamKeys) ask of the dashboard, for its team rows."""

    def __init__(self, app: SessionPicker) -> None:
        self.app = app

    def say(self, note: actions.Note) -> None:
        self.app.say_note(note)

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        self.app.open_team(team, launches)

    def open_sessions(self, launches: list[Launch]) -> None:
        self.app.open_sessions(launches)

    def cold_start_ok(self, team: teams.Team, members: dict[str, Session | None]) -> bool:
        return self.app.cold_start_ok(team, members)

    def recent(self) -> set[str] | None:
        return self.app.openings.recent()

    def kept_handovers(self) -> dict:
        return self.app.engine.state.kept_handovers

    def close_tabs(self, names: list[str]) -> None:
        self.app.close_team_tabs(names)

    def adopt(self, team: teams.Team) -> None:
        self.app.poll_teams()  # the list reads its teams again

    def go_to_tab(self, session: Session) -> None:
        self.app.go_to_tab(session)

    def put_on_clipboard(self, text: str) -> None:
        copy_text(self.app, text)

    def hand_over(self, session: Session) -> None:
        self.app.open_handover(session)

    def after_update(self, what: str, then: Callable[[], None]) -> bool:
        return self.app.after_update(what, then)

    def start_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        self.app.start_in_background(team, launches, then)

    def member_session(self, team: str, member: str) -> Session | None:
        state = self.app.team_states.get(team)
        found = state.sessions.get(member) if state is not None else None
        return self.app.sessions.get(found.session_id, found) if found is not None else None

    def notes(self) -> dict[str, tuple[str, str]]:
        return self.app.notes

    def deliver_notes(self) -> None:
        self.app.deliver_notes()

    def show_team(self, team: teams.Team) -> None:
        self.app.push_screen(TeamScreen(team, self.app.accent()))


for _name in engine.FORWARDED:
    setattr(SessionPicker, _name, forward(_name))
del _name

# The dashboard's own keys, which a dialog or the team view on top keeps for itself.
QUIT_AGAIN_SECONDS = 6  # q again, with handovers queued here: quits (SessionPicker.quit_ok)
DASHBOARD_ACTIONS = {binding.action for binding in SessionPicker.BINDINGS} - {
    "quit", "back", "cycle_theme", "toggle_mascot"}
