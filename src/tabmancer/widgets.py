"""Dashboard pieces: the top bar, counting stat cards, the project sidebar and the footer."""

from __future__ import annotations

import math
import time
from datetime import datetime

from rich.cells import cell_len
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import accounts
from .accounts import Account
from .bitart import Colour
from .sessions import INTERRUPTED, Exit, Session, Status, _shorten, folder_key  # noqa: F401  (re-exported)
from .theme import AMBER, CYAN, GREEN, MUTED, RED, TEXT, mix
from .tokenchart import PixelPicture, draw_hours
from .usage import Limit, until

ALL_PROJECTS = "*"
# A 3x5 pixel font drawn with half blocks: three rows high and bolder than Digits' thin lines.
FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    ".": ("0", "0", "0", "0", "1"),
}
HALF = {("0", "0"): " ", ("1", "0"): "▀", ("0", "1"): "▄", ("1", "1"): "█"}
BARS = " ▁▂▃▄▅▆▇█"



def big(number: str) -> str:
    """A number three rows high in the pixel font."""
    glyphs = [FONT[char] for char in number if char in FONT]
    rows = []
    for top in (0, 2, 4):
        cells = []
        for glyph in glyphs:
            lower = glyph[top + 1] if top + 1 < len(glyph) else "0" * len(glyph[top])
            cells.append("".join(HALF[pair] for pair in zip(glyph[top], lower)))
        rows.append(" ".join(cells))
    return "\n".join(rows)


HOUR_NOW = "#e6e6e6"    # the hour now on the TOKENS TODAY card, the others in MUTED
HOUR_FLOOR = "#262626"  # under the hours with none


class HourBars:
    """Today's tokens per hour as bars three rows high; the latest hours when not all fit."""

    def __init__(self, hours: list[int]) -> None:
        self.hours = hours

    def __rich_console__(self, console, options):
        hours = self.hours[-options.max_width:]
        peak = max(hours, default=0) or 1
        # Eighths of a cell over three rows; an hour with any use shows at least a sliver.
        levels = [max(1, round(used / peak * 24)) if used else 0 for used in hours]
        for row in (2, 1, 0):
            line = Text()
            for index, level in enumerate(levels):
                part = min(8, max(0, level - row * 8))
                now = index == len(levels) - 1
                if part:
                    line.append(BARS[part], style=HOUR_NOW if now else MUTED)
                else:
                    line.append("▁" if row == 0 else " ", style=HOUR_FLOOR)  # the hours with none
            yield line


class HourChart(PixelPicture, Static):
    """Beside a card's number: what it is given, and today's tokens by the hour (HourBars) in real
    pixels where the terminal draws them (tokenchart.draw_hours: the whole day, the hour now in
    white), in block characters elsewhere."""

    hours: list[int] | None = None

    def update(self, content="", *, layout: bool = True) -> None:
        self.hours = list(content.hours) if isinstance(content, HourBars) else None
        if self.hours is not None and self.start_pixels():
            super().update("", layout=layout)  # its cells blank: the picture goes over them
            self.draw_picture()
        else:
            self.stop_pixels()
            super().update(content, layout=layout)

    def colours(self) -> tuple[Colour, Colour, Colour]:
        return tuple(self.shown(Color.parse(c)) for c in (MUTED, HOUR_NOW, HOUR_FLOOR))  # type: ignore[return-value]

    def picture_key(self) -> tuple:
        return tuple(self.hours or ()), self.colours()

    def picture_rows(self, width: int, height: int) -> tuple[list[Colour | None], list[bytes]]:
        return draw_hours([float(h) for h in self.hours or ()], width, height, *self.colours())


class StatCard(Vertical):
    """A big number that counts up to its new value, and beside it what it counts. On a short
    screen (.mini on the dashboard) it is two lines: its title and number, then what it counts."""

    DEFAULT_CSS = """
    StatCard {
        width: 1fr; height: 5; margin: 0 1 0 0; padding: 0 2;
        background: $surface; border-left: outer $panel-lighten-2;
    }
    StatCard > .title { color: $text-muted; text-style: bold; }
    StatCard > Horizontal { height: 3; }
    StatCard .number { width: auto; color: $foreground; }
    StatCard .beside { width: 1fr; height: 3; padding-left: 3; }
    StatCard > .caption { color: $text-muted; }
    StatCard > .head, StatCard > .line { display: none; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    StatCard.good { border-left: outer $success; }
    StatCard.good .number { color: $success; }
    StatCard.warn { border-left: outer $warning; }
    StatCard.warn .number { color: $warning; }
    StatCard.alert { border-left: outer $error; }
    StatCard.alert .number { color: $error; }
    StatCard:hover { background: $panel; }
    StatCard.picked { background: $panel-lighten-1; }
    StatCard.picked > .title { color: $accent; }
    """

    class Picked(Message):
        """A card was clicked: the dashboard shows only what it counts (or, for tokens, the stats)."""

        def __init__(self, card: StatCard) -> None:
            super().__init__()
            self.card = card

    value: reactive[float] = reactive(0.0)

    def __init__(self, title: str, caption: str = "", short: str = "", decimals: int = 0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.title_text = title
        self.caption_text = caption
        self.short_text = short or caption  # shown instead when the card is too narrow
        self.decimals = decimals
        self.unit = ""  # after the number on the short card's head: "billion"
        self.level = ""

    def compose(self) -> ComposeResult:
        self.number = Static(big(f"{0:.{self.decimals}f}"), classes="number")
        self.beside = HourChart("", classes="beside")  # the tokens card's hours, in pixels where it can be
        yield Label(self.title_text, classes="title")
        with Horizontal():
            yield self.number
            yield self.beside
        yield Label(self.caption_text, classes="caption")
        self.head = Static(self.head_text(0), classes="head")
        self.line = Static("", classes="line")
        yield self.head
        yield self.line

    def head_text(self, value: float) -> Text:
        """The short card's first line: 'NEED YOU  7', the number in the card's colour."""
        colour = {"good": GREEN, "warn": AMBER, "alert": RED}.get(self.level, TEXT)
        title = f"bold {self.app.current_theme.accent}" if self.has_class("picked") and self.is_mounted else f"bold {MUTED}"
        return Text.assemble((self.title_text, title), "  ", (f"{value:.{self.decimals}f}", f"bold {colour}"),
                             (f" {self.unit}" if self.unit else "", MUTED))

    def watch_value(self, value: float) -> None:
        # The count-up animation can still tick while the app is shutting down.
        number = getattr(self, "number", None)
        if number is not None and number.is_attached:
            number.update(big(f"{value:.{self.decimals}f}"))
        head = getattr(self, "head", None)
        if head is not None and head.is_attached:
            head.update(self.head_text(value))

    def on_resize(self) -> None:
        self.fit_caption()

    def on_click(self) -> None:
        self.post_message(self.Picked(self))

    def pick(self, picked: bool) -> None:
        """Lit as the card whose sessions the list shows (1 2 3, or a click)."""
        self.set_class(picked, "picked")
        self.watch_value(self.value)

    def fit_caption(self) -> None:
        # A live update can still arrive while the app is shutting down and the card is emptied.
        captions = self.query(".caption")
        if not captions:
            return
        room = self.content_size.width
        fits = not room or len(self.caption_text) <= room
        captions.first(Label).update(self.caption_text if fits else self.short_text)

    def show(self, target: float, caption: str | None = None, short: str | None = None,
             level: str = "", beside=None, line: Text | None = None, unit: str | None = None) -> None:
        """line: what the card counts on one line, for the short card; unit: after its number there."""
        self.level = level
        if unit is not None:
            self.unit = unit
        if line is not None and getattr(self, "line", None) is not None and self.line.is_attached:
            self.line.update(line)
        self.watch_value(self.value)  # redraw now in case the number of decimals changed
        self.animate("value", value=float(target), duration=0.7, easing="out_cubic")
        if caption is not None:
            self.caption_text, self.short_text = caption, short or caption
            self.fit_caption()
        if beside is not None and getattr(self, "beside", None) is not None and self.beside.is_attached:
            self.beside.update(beside)
        for name in ("good", "warn", "alert"):
            self.set_class(name == level, name)


def inline(lines: list[tuple[str, str]]) -> Text:
    """The same (words, style) on one line, for a short card: '7 waiting for you · 6 working'."""
    return Text(" · ", style=MUTED).join(Text(words, style=style) for words, style in lines)


def tally(lines: list[tuple[str, str]], room: int = 3) -> Text:
    """Up to `room` lines of (words, style) for beside a card's number; '+N more' when there are more."""
    if len(lines) > room:
        lines = [*lines[:room - 1], (f"+{len(lines) - room + 1} more", MUTED)]
    return Text("\n").join(Text(words, style=style, no_wrap=True, overflow="ellipsis") for words, style in lines)


BAR_PADDING = {"pills": 2, "usage": 2}  # the top bar's parts' own padding, as in its CSS


class TopBar(Horizontal):
    """One slim line: the name, what is on screen, a breathing live dot and a clock."""

    DEFAULT_CSS = """
    TopBar { height: 1; background: $surface; padding: 0 1; }
    TopBar > #brand { width: auto; color: $accent; text-style: bold; }
    TopBar > #info { width: 1fr; color: $text-muted; padding: 0 3; text-wrap: nowrap; text-overflow: ellipsis; }
    TopBar > #pills { width: auto; padding: 0 1; }
    TopBar > #account { width: auto; padding: 0 1; }
    TopBar > #usage { width: auto; padding: 0 1; }
    TopBar > #live { width: auto; padding: 0 2; }
    TopBar > #clock { width: auto; color: $text-muted; }
    """

    INFO_LEAST = 20  # cells the info line keeps before the accounts give way

    def __init__(self, live: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.live = live  # the breathing dot: only where the view refreshes itself
        self.widths: dict[str, int] = {"brand": cell_len("◆ tabmancer")}  # the parts' contents, in cells
        self.accounts: list[Account] = []

    def compose(self) -> ComposeResult:
        yield Static("◆ tabmancer", id="brand")
        yield Static("", id="info")
        yield Static("", id="pills")  # what the list is narrowed to and what is ticked: never cut off
        yield Static("", id="account")  # who the CLIs are signed in as: gives way first
        yield Static("", id="usage")
        yield Static("", id="live")
        yield Static("", id="clock")

    def show_usage(self, limits: list[Limit]) -> None:
        """Plan limits as small bars: green, then amber from 60 %, red from 85 %."""
        text = Text()
        for limit in limits:
            if limit.label not in ("session", "week") and not limit.label.startswith("codex ")                     and limit.percent < 50:
                continue  # per-model weekly limits only matter once they fill up
            colour = GREEN if limit.percent < 60 else AMBER if limit.percent < 85 else RED
            filled = round(limit.percent / 100 * 8)
            if text:
                text.append("   ")
            text.append(f"{limit.label} ", style=MUTED)
            text.append("▰" * filled, style=colour)
            text.append("▱" * (8 - filled), style="#2a2a2a")
            text.append(f" {limit.percent}%", style=f"bold {colour}")
            left = until(limit.resets)
            if left:
                # No ↻: some fonts draw it two cells wide, right over the time.
                text.append(f" · {left}", style=MUTED)
        self.put("usage", text)

    def put(self, part: str, content) -> None:
        """Update one part of the bar. A poll can still land while the app shuts down and the bar
        has been emptied (seen on CI as NoMatches): then there is nothing to update."""
        found = self.query(f"#{part}")
        if found:
            found.first(Static).update(content)
        if part not in ("info", "account", "clock", "live"):  # the clock and the dot keep their widths
            width = content.cell_len if isinstance(content, Text) else cell_len(str(content))
            if width != self.widths.get(part):
                self.widths[part] = width
                self.fit_accounts()

    def on_click(self, event: events.Click) -> None:
        """The account in the top bar: the accounts you use, to switch (A)."""
        if getattr(event.widget, "id", None) == "account" and hasattr(self.app, "action_accounts"):
            self.app.action_accounts()

    def show_accounts(self, found: list[Account]) -> None:
        if found != self.accounts:
            self.accounts = found
            self.fit_accounts()

    def on_resize(self, event: events.Resize) -> None:
        self.fit_accounts()

    def fit_accounts(self) -> None:
        """The accounts in the room the rest of the bar leaves: Claude Code's beside the plan limits
        it is about, Codex's before it. Short of room, Codex's goes first, then Claude Code's plan,
        then its email is cut in the middle. The info line keeps a little room, unless Claude
        Code's account would not fit at all: then it gives way."""
        # The parts, the clock, the live dot with its padding, the info's padding (the bar's own
        # padding is outside self.size).
        taken = (sum(width + BAR_PADDING.get(part, 0) for part, width in self.widths.items() if width)
                 + 8 + (10 if self.live else 0) + 6)
        for least in (self.INFO_LEAST, 0):
            text, claude_shown = self._accounts_in(self.size.width - taken - least - 2)  # less their padding
            if claude_shown or not any(account.cli == "claude" for account in self.accounts):
                break
        self.put("account", text)

    def _accounts_in(self, room: int) -> tuple[Text, bool]:
        text, claude_shown = Text(), False
        for account in sorted(self.accounts, key=lambda a: a.cli != "codex"):  # Codex's first, Claude's last
            label = "codex " if account.cli == "codex" else ""
            gap = 3 if text else 0
            shown = accounts.words(account, room - gap - len(label) - text.cell_len)
            if not shown:
                continue  # Codex's gone: Claude Code's, which matters more, may still fit
            email, _, plan = shown.partition(" · ")
            text.append(" " * gap)
            text.append(label, style=MUTED)
            text.append(email, style=TEXT)
            if plan:
                text.append(f" · {plan}", style=MUTED)
            claude_shown = claude_shown or account.cli == "claude"
        return text, claude_shown

    def on_mount(self) -> None:
        self.started = time.monotonic()
        self.tick()
        self.set_interval(0.1, self.tick)

    def tick(self) -> None:
        # The live dot breathes: a slow sine between dim and bright green.
        phase = (math.sin((time.monotonic() - self.started) * 2.2) + 1) / 2
        if self.live:
            dot = Text("● ", style=mix("#163d1f", GREEN, phase))
            self.put("live", dot + Text("live", style=MUTED))
        self.put("clock", datetime.now().strftime("%H:%M:%S"))

    def show_info(self, text: str) -> None:
        self.put("info", text)


def project_line(mark: tuple[str, str], name: Text, count: int, git: str = "") -> Table:
    """A sidebar line: the mark and name on the left, then where its folder stands in git (± changes
    not committed, ↑ commits not pushed), the number of sessions lined up on the right."""
    marks = Text.assemble(*((("±", AMBER),) if "±" in git else ()), *((("↑", CYAN),) if "↑" in git else ()))
    right = Text.assemble(marks, " " if marks else "", (str(count), MUTED))
    line = Table.grid(expand=True)
    # A long name gives way, not the marks or the count: only a ratio column is shrunk to fit.
    line.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    line.add_column(justify="right", no_wrap=True, width=right.cell_len + 1)
    line.add_row(Text.assemble(mark, name), right)
    return line


class ProjectList(OptionList):
    """All projects first, then one line per project, in a stable alphabetical order."""

    DEFAULT_CSS = """
    ProjectList { background: $surface; border: none; padding: 0 1; }
    """

    def show_projects(self, sessions: list[Session], waiting: set[str] = frozenset(),
                      git: dict[str, str] | None = None) -> None:
        """Update the list without moving it; `waiting` are the sessions waiting for you, `git`
        folder key -> its marks (± changed, ↑ to push).

        Live updates arrive every few seconds. Rebuilding the options each time reset
        the scroll position and highlight, and sorting by activity reshuffled the rows,
        so the list jumped under the mouse. Now the order is alphabetical and, while
        the set of projects stays the same, only the labels are replaced in place.
        """
        groups: dict[str, list[Session]] = {}
        for session in sessions:
            groups.setdefault(folder_key(session.cwd) or "?", []).append(session)
        entries = [(ALL_PROJECTS, project_line(("", ""), Text("All projects", style="bold"), len(sessions)))]
        for key, items in sorted(groups.items(), key=lambda item: item[1][0].project.casefold()):
            if any(s.session_id in waiting for s in items):
                mark = ("● ", RED)
            elif any(s.exit is Exit.RUNNING for s in items):
                mark = ("● ", GREEN)
            elif any(s.status in INTERRUPTED for s in items):
                mark = ("▲ ", AMBER)
            else:
                mark = ("  ", "")
            entries.append((key, project_line(mark, Text(_shorten(items[0].project, 18)), len(items),
                                              (git or {}).get(key, ""))))
        ids = [key for key, _ in entries]
        if ids == [self.get_option_at_index(i).id for i in range(self.option_count)]:
            for key, prompt in entries:
                self.replace_option_prompt(key, prompt)
            return
        keep = self.highlighted_id()
        self.clear_options()
        self.add_options([Option(prompt, id=key) for key, prompt in entries])
        self.highlighted = ids.index(keep) if keep in ids else 0

    def highlighted_id(self) -> str | None:
        if self.highlighted is None or self.option_count == 0:
            return None
        return self.get_option_at_index(self.highlighted).id


class KeysFooter(Footer):
    """The footer, on as many rows as its keys need (two at 120 columns): none is ever cut off
    the end. One row while they fit; the keys line up in columns when there are more.

    The app may say what a key does on the row at hand (footer_words(action): words, or None to
    keep the key's own) and which group a key is in (footer_group(action)): a group's first key
    keeps a little apart from the one before (padding, not a margin, which a grid column would
    take off its words)."""

    # In columns, a gutter keeps the keys apart: a key's own margin would be taken off its text.
    DEFAULT_CSS = """
    KeysFooter.-rows { layout: grid; grid-columns: auto; grid-gutter: 0 1; }
    KeysFooter.-rows.-compact FooterKey { margin: 0; }
    KeysFooter FooterKey.-group-start { padding-left: 2; }
    KeysFooter.-roomy-row.-compact FooterKey { margin-right: 2; }
    KeysFooter.-rows.-roomy-grid { grid-gutter: 0 2; }
    """
    ROOMY = 2  # cells between the keys where they fit (one where they do not): "Select m Mode" read as one
    GROUP_GAP = 2
    MOST_ROWS = 4

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.widths: list[int] = []  # each key's cells, as last composed

    def compose(self) -> ComposeResult:
        self.widths = []
        words_for = getattr(self.app, "footer_words", None)
        group_of = getattr(self.app, "footer_group", None)
        last_group = None
        for widget in super().compose():
            key = getattr(widget, "key_display", None)  # a FooterKey: its width as the compact footer draws it
            if key is not None:
                action = getattr(widget, "action", "")
                said = words_for(action) if words_for is not None else None
                if said:
                    widget.description = said
                group = group_of(action) if group_of is not None else None
                gap = 0
                if group is not None and last_group is not None and group != last_group:
                    widget.add_class("-group-start")
                    gap = self.GROUP_GAP
                last_group = group if group is not None else last_group
                words = getattr(widget, "description", "")
                self.widths.append(gap + cell_len(key) + (1 + cell_len(words) if words else 0))
            yield widget
        self.fit()

    def on_resize(self, event: events.Resize) -> None:
        self.fit(event.size.width)

    def rows_for(self, width: int) -> int:
        """The fewest rows whose columns (each as wide as its widest key, a cell between them)
        fit in this width."""
        for rows in range(1, self.MOST_ROWS + 1):
            columns = math.ceil(len(self.widths) / rows) if self.widths else 1
            if sum(max(self.widths[c::columns]) for c in range(columns)) + columns - 1 <= width:
                return rows
        return self.MOST_ROWS

    def fit(self, width: int | None = None) -> None:
        width = width or self.size.width or (self.app.size.width if self.is_attached else 0)
        rows = self.rows_for(width) if width and self.widths else 1
        self.set_class(rows > 1, "-rows")
        columns = math.ceil(len(self.widths) / rows) if self.widths else 1
        need = sum(max(self.widths[c::columns]) for c in range(columns)) if self.widths else 0
        roomy = bool(width) and need + self.ROOMY * (columns - 1) <= width
        self.set_class(roomy and rows == 1, "-roomy-row")
        self.set_class(roomy and rows > 1, "-roomy-grid")
        self.styles.height = rows
        if rows > 1:
            self.styles.grid_size_columns = math.ceil(len(self.widths) / rows)
