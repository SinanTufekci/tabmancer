"""The team view: the orchestrator's card on top, the members' cards under it on a tree of wires,
every message flying from one card to another as it is sent, and the conversation underneath as
arrows between the members' lanes. Enter on a team's row opens it from the dashboard (T lists the teams)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import (
    actions,
    agy,
    context,
    handoverwords,
    improve,
    newsessions,
    picks,
    summarize,
    teamforms,
    teams,
    teamtree,
    viewmodel,
)
from .actions import (  # noqa: F401  (re-exported)
    close_tabs,
    closing_note,
    cold_note,
    handing_note,
    open_names,
    team_handover_problem,
)
from .dialogs import Choice, FolderSuggester, cards, frame, light_rows, pill, pills, tilde, title
from .safework import work
from .sessions import Exit, Session, Status, folder_key
from .statsview import copy_text
from .teamforms import (  # noqa: F401  (re-exported)
    LEAD_HELP,
    LEAD_LINE,
    MEMBERS_HELP,
    SAID_HERE,
    TOWARD_HELP,
    JoinTeam,
    TellTeam,
)
from .teamwords import PALETTE, colours  # noqa: F401  (PALETTE re-exported)
from .theme import AMBER, BLUE, CYAN, DEFAULT_THEME, GREEN, MUTED, RED, TEXT, THEMES, keys, mix
from .tokens import human
from .viewmodel import FLIGHT_SECONDS

CARD_H = 4  # a card's rows: its border, the name and what it does, its context and tokens, its border
CARD_LEAST, CARD_MOST = 24, 36  # cards grow with the room between these widths
GAP = 3
METER = 5  # cells of a card's context meter
WIRE = "#3a3a3a"
STAR = "·✢✶✻✽✻✶✢"
LOG_LINES = 200


def card_model(session: Session | None, pick, cli: str = "claude") -> tuple[str, bool]:
    """The model on a member's card: the one that answers, with its version, where its session says
    ("opus 5.5/high"), with a ↓ when another took over by itself (and True, for amber); else the one
    picked for it ("sonnet/high"). Non-Claude members carry their CLI tag ("agy")."""
    prefix = f"{cli} " if cli != "claude" else ""
    if session is None or not session.model or session.model.startswith("<"):
        short = pick.short()
        return (f"{prefix}{short}".strip() if prefix else short), False
    fell = bool(session.fell_back) and session.exit is Exit.RUNNING
    text = context.short_model(session.model) + ("↓" if fell else "") + (f"/{pick.effort}" if pick.effort else "")
    return (f"{prefix}{text}".strip() if prefix else text), fell


def status_of(session: Session | None) -> tuple[str, str, bool]:
    """(label, style, spinning) for a member's card, in the dashboard's colours: blue works, red
    waits for you, amber is stopped at a usage limit (as in the list); a member done for now is
    idle, quiet; cyan waits for a command it left running in the background."""
    if session is None:
        return "not started", MUTED, False
    if viewmodel.at_limit(session):
        return "at the limit", AMBER, False
    if session.exit is not Exit.RUNNING:
        return "closed", MUTED, False
    state = session.live_state.lower()
    if state in ("busy", "working"):
        return "working", BLUE, True
    if state == "aside":
        return "background command", CYAN, False
    if state == "permission":
        return "needs your OK", f"bold {RED}", False
    if state in ("blocked", "waiting"):
        return "needs you", f"bold {RED}", False
    # A member waiting with a keep-warm sleep running: until when an answer finds its cache warm.
    warm = f" · warm to {session.warm_until:%H:%M}" if session.warm_until is not None and session.turn_over else ""
    if state == "resting":
        return f"idle{warm}", MUTED, False
    return f"waiting{warm}", RED, False


class Canvas:
    """A grid of styled characters, turned into one Text."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells = [[(" ", "")] * width for _ in range(height)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.height:
            return
        for i, char in enumerate(text):
            if 0 <= x + i < self.width:
                self.cells[y][x + i] = (char, style)

    def style_at(self, x: int, y: int, style: str) -> None:
        if 0 <= y < self.height and 0 <= x < self.width:
            self.cells[y][x] = (self.cells[y][x][0], style)

    def char_at(self, x: int, y: int) -> str:
        return self.cells[y][x][0] if 0 <= y < self.height and 0 <= x < self.width else " "

    def text(self) -> Text:
        out = Text()
        for row, line in enumerate(self.cells):
            run, style = "", None
            for char, cell_style in line:
                if cell_style != style and run:
                    out.append(run, style=style or "")
                    run = ""
                run, style = run + char, cell_style
            out.append(run.rstrip() if not style else run, style=style or "")
            if row < self.height - 1:
                out.append("\n")
        return out


@dataclass
class Flight:
    message: teams.Message
    path: list[tuple[int, int]]
    start: float


class TeamGraph(Widget):
    """The cards: the lead on top, the members under it on a tree of wires, the messages flying
    along them; under the cards, what the picked member works on. Peers, with no lead, stand side
    by side on one wire, and their messages run along it."""

    DEFAULT_CSS = "TeamGraph { height: auto; }"
    card_h, least, gap = CARD_H, CARD_LEAST, GAP  # smaller in the new-team form's preview

    def __init__(self, team: teams.Team, accent: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.team, self.accent = team, accent
        self.palette = colours(team, accent)
        self.sessions: dict[str, Session | None] = {}
        self.said: list[teams.Message] = []
        self.unread: dict[str, int] = {}
        self.flights: list[Flight] = []
        self.selected = 0  # an index into order; one past its end is everyone (no card picked)

    @property
    def order(self) -> list[teams.Member]:
        return self.team.ordered

    @property
    def lead(self) -> str:
        """The lead's name; empty for peers."""
        return self.team.lead.name if self.team.lead is not None else ""

    def selected_member(self) -> teams.Member | None:
        """The member picked with ← →, or None when everyone is."""
        index = self.selected % (len(self.order) + 1)
        return self.order[index] if index < len(self.order) else None

    # --- geometry ----------------------------------------------------------------------

    def layout_boxes(self, width: int) -> tuple[dict[str, tuple[int, int, int]], list[list[str]]]:
        """name -> (x, y, card width), and the members row by row: the lead's card on top in the
        middle, each row of members under a wire of its own. Peers start at the top, under theirs."""
        members = [m.name for m in self.order if not m.lead]
        count = max(1, len(members))
        card = max(self.least, min(CARD_MOST, (width - (count - 1) * self.gap) // count))
        per_row = max(1, (width + self.gap) // (card + self.gap))
        rows = [members[i:i + per_row] for i in range(0, len(members), per_row)] or [[]]
        places = {self.lead: ((width - card) // 2, 0, card)} if self.lead else {}
        top = self.card_h + 1 if self.lead else 1  # under a row's wire
        for row in rows:
            span = len(row) * card + (len(row) - 1) * self.gap
            left = max(0, (width - span) // 2)
            for i, name in enumerate(row):
                places[name] = (left + i * (card + self.gap), top, card)
            top += self.card_h + 1
        return places, rows

    def height_for(self, rows: list[list[str]]) -> int:
        # The lead's card, the rows of cards each under its wire, and the focus line.
        return (self.card_h if self.lead else 0) + len(rows) * (self.card_h + 1) + 2

    def path(self, sender: str, receiver: str, places: dict[str, tuple[int, int, int]]) -> list[tuple[int, int]]:
        """The cells a message passes on its way: out of the lead's card, down the trunk to a
        member's wire and into its card; or along a wire from one member to another."""
        lead = self.lead
        if not lead:  # peers: along their row's wire; between rows nothing flies
            if sender in places and receiver in places and places[sender][1] == places[receiver][1]:
                (a, top, wa), (b, _, wb) = places[sender], places[receiver]
                a, b, wire = a + wa // 2, b + wb // 2, top - 1
                step = 1 if b > a else -1
                return [(a, top), *[(c, wire) for c in range(a, b + step, step)], (b, top)]
            return []
        lead_x = places[lead][0] + places[lead][2] // 2

        def down_to(name: str) -> list[tuple[int, int]]:
            x, top, card = places[name]
            middle, wire = x + card // 2, top - 1
            cells = [(lead_x, y) for y in range(self.card_h - 1, wire + 1)]
            step = 1 if middle > lead_x else -1
            cells += [(c, wire) for c in range(lead_x + step, middle + step, step)] if middle != lead_x else []
            return [*cells, (middle, top)]

        if sender == lead and receiver in places and receiver != lead:
            return down_to(receiver)
        if receiver == lead and sender in places and sender != lead:
            return down_to(sender)[::-1]
        if sender in places and receiver in places and lead not in (sender, receiver):
            if places[sender][1] == places[receiver][1]:  # the same row: along its wire
                (a, top, wa), (b, _, wb) = places[sender], places[receiver]
                a, b, wire = a + wa // 2, b + wb // 2, top - 1
                step = 1 if b > a else -1
                return [(a, top), *[(c, wire) for c in range(a, b + step, step)], (b, top)]
            return down_to(sender)[::-1] + down_to(receiver)
        return []

    # --- drawing ---------------------------------------------------------------------------

    def canvas_width(self) -> int:
        """The width the cards are laid out at: the widget's own, or a guess before its first layout."""
        return max(40, self.size.width or 100)

    def render(self) -> Text:
        width = self.canvas_width()
        places, rows = self.layout_boxes(width)
        canvas = Canvas(width, self.height_for(rows))
        for member in self.order:
            self.draw_box(canvas, member, places[member.name])
        self.draw_wires(canvas, places, rows)
        self.draw_flights(canvas)
        self.draw_focus(canvas)
        return canvas.text()

    def draw_wires(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        """The trunk out of the lead's card, a wire across each row of members, a drop into each
        card; on each wire, beside its drop, the messages sent down to that member and back up."""
        if not self.lead:
            self.draw_peer_wires(canvas, places, rows)
            return
        lead = self.lead
        lx, _, lcard = places[lead]
        lead_x = lx + lcard // 2
        chosen = self.selected_member()
        lead_picked = chosen is self.team.lead
        canvas.put(lead_x, self.card_h - 1, "┳" if lead_picked else "┬", f"bold {self.accent}" if lead_picked else WIRE)
        wires = []
        for row in rows:
            if not row:
                continue
            top = places[row[0]][1]
            wire = top - 1
            middles = [places[n][0] + places[n][2] // 2 for n in row]
            left, right = min([*middles, lead_x]), max([*middles, lead_x])
            canvas.put(left, wire, "─" * (right - left + 1), WIRE)
            for name, middle in zip(row, middles):
                canvas.put(middle, wire, "┬", WIRE)
                picked = chosen is not None and chosen.name == name
                canvas.put(middle, top, "┻" if picked else "┴",
                           f"bold {self.palette.get(name, TEXT)}" if picked else WIRE)
            if left in middles and left != lead_x:
                canvas.put(left, wire, "╭", WIRE)
            if right in middles and right != lead_x:
                canvas.put(right, wire, "╮", WIRE)
            wires.append(wire)
        last = max(wires, default=self.card_h - 1)
        cards = [(cx, cy, cw) for name, (cx, cy, cw) in places.items() if name != lead]
        for y in range(self.card_h, last + 1):  # the trunk, down to the last row's wire, behind the cards
            if any(cx <= lead_x < cx + cw and cy <= y < cy + self.card_h for cx, cy, cw in cards):
                continue
            here = canvas.char_at(lead_x, y)
            alone = canvas.char_at(lead_x - 1, y) == " " and canvas.char_at(lead_x + 1, y) == " "
            if here == " " or alone:  # nothing across here, or one card right under the trunk
                canvas.put(lead_x, y, "│", WIRE)
            elif here in ("─", "┬"):
                canvas.put(lead_x, y, "┼" if y != last or here == "┬" else "┴", WIRE)
        self.draw_traffic(canvas, places, rows)

    def draw_peer_wires(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        """Peers: a wire over each row, from the first card's drop to the last's, a drop into each."""
        chosen = self.selected_member()
        for row in rows:
            if not row:
                continue
            top = places[row[0]][1]
            wire = top - 1
            middles = [places[n][0] + places[n][2] // 2 for n in row]
            if len(middles) > 1:
                canvas.put(middles[0], wire, "─" * (middles[-1] - middles[0] + 1), WIRE)
            for index, (name, middle) in enumerate(zip(row, middles)):
                ends = len(middles) > 1 and index in (0, len(middles) - 1)
                canvas.put(middle, wire, ("╭" if index == 0 else "╮") if ends else "┬" if len(middles) > 1 else "│",
                           WIRE)
                picked = chosen is not None and chosen.name == name
                canvas.put(middle, top, "┻" if picked else "┴",
                           f"bold {self.palette.get(name, TEXT)}" if picked else WIRE)
        self.draw_traffic(canvas, places, rows)

    def draw_traffic(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        """' 2▼ 1▲ ' on a member's wire beside its drop: the messages the lead sent down to it and
        the ones it sent back up (for peers: the others sent it, and it sent them). Left of the drop,
        or right of it where the wire ends there; only where the wire has room, a cell of it kept on
        either side."""
        lead = self.lead
        sent = teamtree.counts(self.said)
        names = {m.name for m in self.team.members}
        for row in rows:
            for name in row:
                x, top, card = places[name]
                middle, wire = x + card // 2, top - 1
                if lead:
                    down, up = sent.get((lead, name), 0), sent.get((name, lead), 0)
                else:
                    down = sum(n for (a, b), n in sent.items() if b == name and a in names)
                    up = sum(n for (a, b), n in sent.items() if a == name and b in names)
                tally = f" {down}▼ {up}▲ "
                for start in (middle - 1 - len(tally), middle + 2):
                    if all(canvas.char_at(c, wire) == "─" for c in range(start - 1, start + len(tally) + 1)):
                        canvas.put(start, wire, tally, MUTED)
                        break

    def draw_box(self, canvas: Canvas, member: teams.Member, place: tuple[int, int, int]) -> None:
        """A card: its top in the member's colour (heavy when picked), its name and what it does on
        the first line, its context as a small meter and its model and tokens on the second."""
        x, y, card = place
        colour = self.palette.get(member.name, TEXT)
        chosen = member is self.selected_member()
        edge = f"bold {colour}" if chosen else mix("#000000", colour, 0.45)
        corner = ("┏", "┓", "┗", "┛", "━", "┃") if chosen else ("╭", "╮", "╰", "╯", "─", "│")
        canvas.put(x, y, corner[0] + corner[4] * (card - 2) + corner[1], f"bold {colour}" if chosen else colour)
        for row in range(1, self.card_h - 1):
            canvas.put(x, y + row, corner[5] + " " * (card - 2) + corner[5], edge)
        canvas.put(x, y + self.card_h - 1, corner[2] + corner[4] * (card - 2) + corner[3], edge)
        inner = card - 4
        session = self.sessions.get(member.name)
        name = ("★ " if member.lead else "") + member.name
        label, style, spinning = status_of(session)
        if spinning:
            label = f"{STAR[int(time.monotonic() * 8) % len(STAR)]} {label}"
        unread = self.unread.get(member.name, 0)
        if unread:
            label += f" · {unread} new"
        label = label[: max(0, inner - min(len(name), 8) - 1)]  # the name keeps a few letters at least
        canvas.put(x + 2, y + 1, name[: max(0, inner - len(label) - 1)], f"bold {colour}")
        canvas.put(x + 2 + inner - len(label), y + 1, label, style)
        used = getattr(self.app, "session_tokens", {}).get(session.session_id) if session is not None else None
        tokens = human(used.total) if used is not None and used.total else ""
        picked, fell = card_model(session, member.pick, member.cli)
        right = " · ".join(part for part in (picked, tokens) if part)
        left = 0
        if session is not None and session.context:
            share = context.share(session, teams.cap_of(session.session_id, self.team))
            filled = round(share * METER)
            bar = GREEN if share < 0.6 else AMBER if share < 0.85 else RED
            canvas.put(x + 2, y + 2, "▰" * filled, bar)
            canvas.put(x + 2 + filled, y + 2, "▱" * (METER - filled), "#2a2a2a")
            canvas.put(x + 3 + METER, y + 2, f"{share:.0%}", bar)
            left = METER + 1 + len(f"{share:.0%}")
        if right and left + 1 + len(right) <= inner:
            canvas.put(x + 2 + inner - len(right), y + 2, right, AMBER if fell else MUTED)  # its model and tokens

    def draw_focus(self, canvas: Canvas) -> None:
        """What the picked member works on, under the cards."""
        member = self.selected_member()
        if member is None or not member.focus:
            return
        line, x = canvas.height - 1, 2
        name = ("★ " if member.lead else "") + member.name
        canvas.put(x, line, name, f"bold {self.palette.get(member.name, TEXT)}")
        words = "  " + _one_line(member.focus)
        room = canvas.width - x - len(name) - 1
        canvas.put(x + len(name), line, words if len(words) <= room else words[: room - 1] + "…", MUTED)

    def draw_flights(self, canvas: Canvas) -> None:
        now = time.monotonic()
        self.flights = [f for f in self.flights if now - f.start < FLIGHT_SECONDS + 0.6]
        for flight in self.flights:
            if not flight.path:
                continue
            colour = self.palette.get(flight.message.sender, TEXT)
            progress = min(1.0, (now - flight.start) / FLIGHT_SECONDS)
            head = int(progress * (len(flight.path) - 1))
            for index, (cx, cy) in enumerate(flight.path[:head + 1]):
                glow = max(0.15, 1 - (head - index) / 10)  # a fading trail behind the message
                canvas.style_at(cx, cy, mix(WIRE, colour, glow))
            if progress < 1:
                cx, cy = flight.path[head]
                canvas.put(cx, cy, "●", f"bold {colour}")

    def launch_flight(self, message: teams.Message) -> None:
        places, _ = self.layout_boxes(self.canvas_width())
        path = self.path(message.sender, message.receiver, places)
        if path:
            self.flights.append(Flight(message, path, time.monotonic()))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _clip_goal(goal: str, most: int = 220) -> str:
    """A goal on one line, cut short where it is long."""
    line = _one_line(goal)
    return line if len(line) <= most else line[:most - 1].rstrip() + "…"


ADD_QUESTION = "Add members as name: area (several with ;, @agy picks Antigravity, @sonnet picks a model)"
ADD_EXAMPLE = "docs: the README and the user guide @agy"


class TeamHandoverDialog(ModalScreen[tuple[str, str] | None]):
    """The new generation of a team handed over: the lead's handover (to read), the line-up it
    suggests and the context cap its members start under (yours to change). Ctrl+S starts it, and
    dismisses with (the line-up, the cap); Esc leaves the team closed."""

    DEFAULT_CSS = frame("TeamHandoverDialog", "large", height="90%") + cards("TeamHandoverDialog") + """
    TeamHandoverDialog > Vertical { width: 95%; max-width: 150; max-height: 40; }
    TeamHandoverDialog #handover-body { height: 1fr; margin-top: 1; }
    TeamHandoverDialog #handover-text-card { width: 55%; height: 1fr; margin-right: 1; }
    TeamHandoverDialog #handover-side { width: 1fr; height: 1fr; }
    TeamHandoverDialog #handover-lineup-card { height: 1fr; }
    TeamHandoverDialog #handover-members { height: 1fr; min-height: 4; }
    TeamHandoverDialog #handover-cap-card { height: auto; }
    TeamHandoverDialog #handover-cap-words { height: auto; color: $text-muted; }
    TeamHandoverDialog #handover-preview { height: auto; color: $text-muted; }
    TeamHandoverDialog #handover-about { color: $text-muted; height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "start", "Start the new team", priority=True),
    ]

    def __init__(self, team: teams.Team, written: str, lineup: str, suggested: bool) -> None:
        super().__init__()
        self.team, self.written, self.lineup, self.suggested = team, written, lineup, suggested

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Hand the team over", self.team.name, "its new generation"))
            yield Label(handoverwords.TEAM_ABOUT, id="handover-about")
            with Horizontal(id="handover-body"):
                with VerticalScroll(id="handover-text-card", classes="card"):
                    yield Static(Text(self.written), id="handover-text")  # the lead's words: never markup
                with Vertical(id="handover-side"):
                    with Vertical(id="handover-lineup-card", classes="card"):
                        yield TextArea(self.lineup, id="handover-members", compact=True)
                    with Vertical(id="handover-cap-card", classes="card"):
                        yield Choice([*picks.CAPS, picks.NO_CAP], value=self.team.cap or picks.default_cap(),
                                     id="handover-cap", labels=newsessions.CAP_SHORT)
                        yield Label("", id="handover-cap-words")
                    yield Static("", id="handover-preview")
            yield Label(keys(("Ctrl+S", "start the new team"), ("Tab", "next box"), ("←/→", "the cap"),
                             ("Esc", "not now: the team stays closed, h shows this again")), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#handover-text-card").border_title = "Handover"
        card = self.query_one("#handover-lineup-card")
        card.border_title = "Line-up"
        card.border_subtitle = "the lead's suggestion" if self.suggested else "as it was: the lead suggested none"
        self.query_one("#handover-cap-card").border_title = "Context"
        self.show_preview()
        self.query_one("#handover-members", TextArea).focus()

    def members(self) -> list[teams.Member]:
        lead = self.team.lead.name if self.team.lead is not None else teams.LEAD
        return [m for m in teams.parse_members(self.query_one("#handover-members", TextArea).text) if m.name != lead]

    def problems(self) -> list[str]:
        return handoverwords.lineup_problems(self.team, self.members())

    def cap(self) -> str:
        return self.query_one("#handover-cap", Choice).value

    def show_preview(self) -> None:
        members = self.members()
        chosen = self.cap()
        self.query_one("#handover-cap-words", Label).update(handoverwords.cap_words(chosen))
        out = Text.assemble(*handoverwords.lineup_parts(members, picks.cap_tokens(chosen), self.problems()))
        self.query_one("#handover-preview", Static).update(out)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.show_preview()

    def on_choice_changed(self, event: Choice.Changed) -> None:
        self.show_preview()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        light_rows(self)

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        light_rows(self)

    def action_start(self) -> None:
        problems = self.problems()
        if problems:
            self.notify("The new team needs " + "; ".join(problems) + ".", severity="warning", timeout=8)
            return
        self.dismiss(("\n".join(m.line() for m in self.members()), self.cap()))

    def action_cancel(self) -> None:
        self.dismiss(None)


class TeamScreen(Screen):
    """The team at work. Arrows pick a member, Enter goes to its tab (or opens it)."""

    DEFAULT_CSS = """
    TeamScreen { background: $background; }
    TeamScreen #team-head { padding: 1 2 0 2; }
    TeamScreen TeamGraph { margin: 1 2 0 2; }
    TeamScreen #team-log-head { padding: 1 2 0 2; color: $text-muted; text-style: bold; }
    TeamScreen #team-lanes { padding: 0 2; height: 1; }
    TeamScreen VerticalScroll { height: 1fr; margin: 0 2 1 2; }
    TeamScreen .hint { color: $text-muted; padding: 0 2; width: 100%; height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q", "close", "Back"),
        Binding("left", "pick(-1)", "Previous", priority=True),
        Binding("right", "pick(1)", "Next", priority=True),
        Binding("enter", "open_member", "Open", priority=True),
        Binding("s", "start_team", "Start the team"),
        Binding("a", "add_member", "Add a member"),
        Binding("d", "remove_member", "Take a member off"),
        Binding("g", "edit_goal", "The goal"),
        Binding("h", "handover_team", "Hand the team over"),
        Binding("H", "handover", "Hand a member over"),
        Binding("x", "close_team", "Close the team"),
        Binding("M", "monitor", "Its monitor"),
    ]

    def __init__(self, team: teams.Team, accent: str) -> None:
        super().__init__()
        self.team, self.accent = team, accent
        self.seen: set[tuple[str, str, str]] | None = None
        self.closing = False  # x pressed once: a second press closes every open member
        self.removing: str | None = None  # d pressed once on this member: a second press takes it off
        self.messages: list[teams.Message] = []
        self.sessions: dict[str, Session | None] = {}
        self.picked = False  # ← → pressed: the conversation shows the picked member's messages only

    def compose(self) -> ComposeResult:
        yield Static("", id="team-head")
        yield TeamGraph(self.team, self.accent, id="team-graph")
        yield Label("CONVERSATION", id="team-log-head")
        yield Static("", id="team-lanes")
        with VerticalScroll():
            yield Static("", id="team-log")
        yield Label(keys(("← →", "member"), ("Enter", "its tab"), ("s", "start the rest"), ("a", "add"),
                         ("d", "take off"), ("g", "goal"), ("h", "hand the team over"), ("H", "a member"),
                         ("x", "close"), ("M", "monitor"), ("Esc", "back")),
                    classes="hint")

    def on_mount(self) -> None:
        self.graph = self.query_one(TeamGraph)
        self.show_head()
        self.set_interval(0.1, self.graph.refresh)
        self.set_interval(2, self.poll)
        self.poll()

    def adopt(self, team: teams.Team) -> None:
        """The team as saved now, with members added since; the boxes are drawn again."""
        self.team = self.graph.team = team
        self.graph.palette = colours(team, self.accent)
        self.show_head()
        self.poll()

    def show_head(self) -> None:
        open_now = sum(1 for s in self.sessions.values() if s is not None and s.exit is Exit.RUNNING)
        spent = self.spent()
        cli_badge = (" + ".join(sorted(self.team.clis)) if self.team.uses_bus else "")
        self.query_one("#team-head", Static).update(Text.assemble(
            ("TEAM ", f"bold {MUTED}"), (self.team.name, f"bold {self.accent}"),
            (f"  ·  {cli_badge}" if cli_badge else "", f"bold {CYAN}"),
            (f"  ·  {self.team.cwd}  ·  {open_now} of {len(self.team.members)} open", MUTED),
            (f"  ·  {human(spent)} tokens" if spent else "", MUTED),
            (f"  ·  the lead picks up to {self.team.decide} members" if self.team.decide else "", MUTED),
            ("  ·  members in the background" if self.team.background else "", MUTED),
            ("  ·  peers, no lead" if self.team.peers else "", MUTED),
            ("  ·  each member on a worktree-<name> branch" if self.team.worktrees else "", MUTED),
            # On one line and cut short: a goal of a few pages pushed the team itself out of sight.
            ("\nGoal  ", MUTED), (_clip_goal(self.team.goal), TEXT)))

    def spent(self) -> int:
        """What the members' sessions have used, as the dashboard counts it (each card has its own)."""
        counted = getattr(self.app, "session_tokens", {})
        total = 0
        for member in self.team.members:
            session = self.sessions.get(member.name)
            use = counted.get(session.session_id) if session is not None else None
            total += use.total if use is not None else 0
        return total

    @work(thread=True, exclusive=True, group="team")
    def poll(self) -> None:
        saved = teams.load().get(self.team.name)  # a lead that decides adds members from its own tab
        if saved is not None and [m.name for m in saved.members] != [m.name for m in self.team.members]:
            self.app.call_from_thread(self.adopt, saved)
            return
        listed = list(getattr(self.app, "sessions", {}).values())
        if self.team.uses_bus:
            by_id = {s.session_id: s for s in listed}
            for found_agy in agy.find(days=2):
                by_id[found_agy.session_id] = found_agy
            listed = list(by_id.values())
        live = teams.live_names()
        sessions = teams.member_sessions(self.team, listed, live)
        for member in self.team.members:  # open, but nothing said yet: no transcript to list it by
            if member.cli != "claude":
                continue
            info = live.get(self.team.session_name(member))
            if sessions.get(member.name) is None and info:
                sessions[member.name] = Session(session_id=str(info.get("sessionId")), path=None, cwd=self.team.cwd,
                                                title=self.team.session_name(member), exit=Exit.RUNNING,
                                                live_state=str(info.get("status") or "idle"))
        said = teams.conversation(self.team, sessions, live)
        self.app.call_from_thread(self.show, sessions, said)

    def show(self, sessions: dict[str, Session | None], said: list[teams.Message]) -> None:
        self.sessions, self.messages = sessions, said
        self.graph.sessions, self.graph.said = sessions, said
        self.graph.unread = {}
        for message in said:
            if not message.delivered:
                self.graph.unread[message.receiver] = self.graph.unread.get(message.receiver, 0) + 1
        # The first look only takes note; then the new messages between members fly (VM.new_flights).
        self.seen, flights = viewmodel.new_flights(self.seen, said, {m.name for m in self.team.members})
        for message in flights:
            self.graph.launch_flight(message)
        self.show_head()
        self.show_log()

    def show_log(self) -> None:
        """The conversation as a sequence diagram (teamtree.sequence): a lane a member, an arrow a
        message from its sender to its receiver. ← → light one member's lane and arrows."""
        palette = self.graph.palette
        only = self.graph.selected_member() if self.picked else None
        head = Text("CONVERSATION", style=f"bold {MUTED}")
        if self.picked:
            head.append(" · ", style=MUTED)
            head.append(only.name if only else "everyone", style=f"bold {palette.get(only.name, TEXT)}" if only else MUTED)
        self.query_one("#team-log-head", Label).update(head)
        scroll = self.query_one(VerticalScroll)
        width = max(40, (scroll.content_size.width or self.size.width - 4) - 1)
        lanes, rows = teamtree.sequence(self.team, self.messages[-LOG_LINES:], palette, width,
                                        picked=only.name if only else None)
        self.query_one("#team-lanes", Static).update(lanes if rows else "")
        log = Text("\n").join(rows)
        if not self.messages:
            log = Text("Nothing said yet. Once the members start, every message between them shows up here as an "
                       "arrow from one lane to another, and flies along the wires between the cards above.",
                       style=MUTED)
        elif only is not None and not any(only.name in (m.sender, m.receiver) for m in self.messages):
            log.append(f"\n\nNothing said to or by {only.name} yet.", style=MUTED)
        self.query_one("#team-log", Static).update(log)
        scroll.scroll_end(animate=False)

    def on_resize(self) -> None:
        self.call_after_refresh(self.show_log)

    def action_pick(self, step: int) -> None:
        """← →: the next member, its box, its lane and its arrows lit; past the last, everyone's."""
        self.graph.selected = (self.graph.selected + step) % (len(self.graph.order) + 1)
        self.picked = True
        self.graph.refresh()
        self.show_log()

    def action_open_member(self) -> None:
        member = self.graph.selected_member()
        if member is None:
            self.notify("Pick a member with ← → first.", timeout=3)
            return
        session = self.sessions.get(member.name)
        if session is not None and session.status is Status.BACKGROUND:
            self.notify(f"{member.name} runs in the background, with no tab: B attaches to it.", timeout=5)
            return
        if session is not None and session.exit is Exit.RUNNING:
            go = getattr(self.app, "go_to_tab", None)
            if go is not None:
                go(session)
            return
        self.app.open_sessions([teams.launch(self.team, member, resume=session if session and session.path else None)])

    def open_team(self, launches: list) -> None:
        """Through the dashboard's open_team (members in the background when the team says so)."""
        opener = getattr(self.app, "open_team", None)
        if opener is not None:
            opener(self.team, launches)
        else:
            self.app.open_sessions(launches)

    def action_handover_team(self) -> None:
        """h, twice: the whole team handed over (the dashboard's press_team_handover): its open members
        close, its lead writes the handover and the line-up for the rest of the work, you see both, and
        the team starts again as a new generation of itself: a fresh lead with the handover, fresh members."""
        pressed = getattr(self.app, "press_team_handover", None)
        if pressed is not None:
            pressed(self.team, dict(self.sessions))

    def action_handover(self) -> None:
        """H: hand the picked member alone over to a fresh session with the same name and role."""
        member = self.graph.selected_member()
        session = self.sessions.get(member.name) if member is not None else None
        if session is None or session.path is None:
            self.notify("This member has no session to hand over yet.", severity="warning")
            return
        opener = getattr(self.app, "open_handover", None)
        if opener is not None:
            opener(session)

    def action_close_team(self) -> None:
        """x, twice: every open member's tab closes. Claude exits as on /exit, so each session can
        be resumed later (s starts them again)."""
        note = closing_note(self.team, self.sessions)
        if note is None:
            self.notify("No member is open.", timeout=3)
            return
        if not self.closing:
            self.closing = True
            self.notify(note, timeout=6)
            self.set_timer(6, lambda: setattr(self, "closing", False))
            return
        self.closing = False
        self.close_members(open_names(self.team, self.sessions))

    @work(thread=True, group="team-close")
    def close_members(self, names: list[str]) -> None:
        note = actions.closed_note(names, close_tabs)
        self.app.call_from_thread(self.notify, note.words, severity=note.severity, timeout=note.seconds)

    def action_start_team(self) -> None:
        getattr(self.app, "kept_handovers", {}).pop(self.team.name, None)  # going on as it was, not handed over
        openings = getattr(self.app, "openings", None)  # the members opened a moment ago are left out
        start = actions.start_team(self.team, self.sessions, openings.recent() if openings is not None else None)
        if not start.launches:
            self.notify(start.words, timeout=3)
            return
        ok = getattr(self.app, "cold_start_ok", None)
        if ok is not None and not ok(self.team, dict(self.sessions)):
            return  # its cache is gone: said what going on costs, s again starts it
        self.open_team(start.launches)

    def action_add_member(self) -> None:
        def add(text: str | None) -> None:
            added = actions.add_members(self.team, text or "")
            if added.warning:
                self.notify(added.warning, severity="warning", timeout=8)
            if not added.new:
                return
            self.adopt(added.team)
            self.open_team(added.launches)

        self.app.push_screen(LineDialog(ADD_QUESTION, ADD_EXAMPLE, enter="add them"), add)

    def action_remove_member(self) -> None:
        """d, twice: the picked member is taken off the team. Its session stays, an ordinary one
        from then on, and the lead (or, among peers, the others) is told: it started with the old
        line-up."""
        member = self.graph.selected_member()
        refusal = actions.removal_refusal(member)
        if refusal is not None or member is None:  # removal_refusal says why when none is picked
            if refusal is not None:
                self.notify(refusal.words, severity=refusal.severity, timeout=refusal.seconds)
            return
        if self.removing != member.name:
            self.removing = member.name
            ask = actions.removal_ask(member, self.sessions.get(member.name))
            self.notify(ask.words, timeout=ask.seconds)
            self.set_timer(6, lambda: setattr(self, "removing", None))
            return
        self.removing = None
        changed = actions.remove_member(self.team, member.name)
        if changed is None:
            return
        self.graph.selected = 0
        self.adopt(changed.team)
        self.tell_lead(changed.note, changed.done)

    def action_edit_goal(self) -> None:
        """g: the team's goal, changed. Members started from now on get the new one; the lead is told."""

        def chosen(goal: str | None) -> None:
            changed = actions.change_goal(self.team, goal or "")
            if changed is None:
                return
            self.adopt(changed.team)
            self.tell_lead(changed.note, changed.done)

        # In a box of several lines, as the new-team form writes it: one line flattened a plan pasted
        # there for good, the moment it was changed at all.
        self.app.push_screen(GoalDialog(self.team.goal), chosen)

    def tell_lead(self, note: str, done: str) -> None:
        """A change made here, for the lead to hear (actions.tell_lead): over the team bus when this team
        uses one, else to the clipboard and you to its tab, to paste it."""
        told = actions.tell_lead(self.team, self.sessions, note, done)
        if told.clipboard:
            copy_text(self.app, told.clipboard)
        go = getattr(self.app, "go_to_tab", None)
        if told.go_to is not None and go is not None:
            go(told.go_to)
        self.notify(told.words, title=told.title, timeout=told.seconds)

    def action_monitor(self) -> None:
        """M: this team's monitor in a window of its own (the dashboard's action_monitor)."""
        opener = getattr(self.app, "action_monitor", None)
        if opener is not None:
            opener(self.team.name)

    def action_close(self) -> None:
        self.app.pop_screen()


class LineDialog(ModalScreen[str | None]):
    DEFAULT_CSS = frame("LineDialog") + "LineDialog Input { margin-top: 1; }"
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, placeholder: str = "", value: str = "", enter: str = "save") -> None:
        super().__init__()
        self.question, self.placeholder, self.value, self.enter = question, placeholder, value, enter

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title(self.question))
            yield Input(value=self.value, placeholder=self.placeholder)
            yield Label(keys(("Enter", self.enter), ("Esc", "cancel")), classes="hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(teamforms.answer(event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class GoalDialog(ModalScreen[str | None]):
    """g in a team's view: its goal, its lines kept. Ctrl+S saves; Enter is a new line."""

    DEFAULT_CSS = frame("GoalDialog") + "GoalDialog TextArea { margin-top: 1; height: 10; }"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, goal: str) -> None:
        super().__init__()
        self.goal = goal

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("The team's goal"))
            yield TextArea(self.goal, id="goal", compact=True)
            yield Label(keys(("Ctrl+S", "save"), ("Enter", "a new line"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        box = self.query_one("#goal", TextArea)
        box.focus()
        box.move_cursor(box.document.end)

    def action_save(self) -> None:
        self.dismiss(teamforms.answer(self.query_one("#goal", TextArea).text))

    def action_cancel(self) -> None:
        self.dismiss(None)


class TeamsDialog(ModalScreen[tuple[str, str] | None]):
    """The saved teams. Enter opens one, n makes a new one, d deletes one (twice)."""

    DEFAULT_CSS = frame("TeamsDialog") + "TeamsDialog OptionList { margin-top: 1; }"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Close"),
        Binding("n", "new", "New team"),
        Binding("d,delete", "delete", "Delete"),
    ]

    def __init__(self, saved: dict[str, teams.Team], picked: str | None = None) -> None:
        super().__init__()
        self.saved = saved
        self.picked = picked  # the team the list starts on (the one the cursor was on)
        self.armed: str | None = None

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        names = list(self.saved)
        if self.picked in names:
            options.highlighted = names.index(self.picked)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Teams"))
            yield OptionList(*self.options())
            yield Label(keys(("Enter", "open"), ("n", "new team"), ("d", "delete (twice; the sessions stay)"),
                             ("Esc", "close")), classes="hint")

    def options(self) -> list[Option]:
        out = []
        for t in self.saved.values():
            members = f"{len(t.members)} members" + (" in the background" if t.background else "")
            out.append(Option(Text.assemble((t.name, "bold"), (f"   {t.project} · {members} · ", MUTED),
                                            (_one_line(t.goal)[:50], MUTED)), id=t.name))
        return [*out, Option(Text("+ new team", style=f"bold {CYAN}"), id="")]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(("open", event.option.id) if event.option.id else ("new", ""))

    def action_new(self) -> None:
        self.dismiss(("new", ""))

    def action_delete(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return
        name = options.get_option_at_index(options.highlighted).id
        if not name:
            return
        if self.armed != name:
            self.armed = name
            self.notify(f"Press d again to delete the team {name}. Its sessions stay.", timeout=4)
            return
        saved = teams.load()
        saved.pop(name, None)
        teams.save(saved)
        self.saved.pop(name, None)
        options.clear_options()
        options.add_options(self.options())
        self.armed = None

    def action_cancel(self) -> None:
        self.dismiss(None)


class RoleDialog(ModalScreen[None]):
    """What a member is told when it starts: its role word for word, and its first message."""

    DEFAULT_CSS = frame("RoleDialog", "large") + """
    RoleDialog #role-text { margin-top: 1; height: auto; max-height: 18; }
    RoleDialog .first { margin-top: 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape,enter,ctrl+g", "close", "Close", priority=True)]

    def __init__(self, team: teams.Team, member: teams.Member) -> None:
        super().__init__()
        self.team, self.member = team, member

    def compose(self) -> ComposeResult:
        said = teamforms.role_words(self.team, self.member)
        with Vertical():
            yield Label(title(said.head, *said.about))
            with VerticalScroll(id="role-text"):
                yield Static(Text(said.role, style=TEXT))
            yield Label(Text.assemble(("Its first message: ", MUTED), (said.first, TEXT)) if said.first else
                        Text(said.instead, style=MUTED), classes="first")
            yield Label(keys(("Esc", "close")), classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class LineupGraph(TeamGraph):
    """The team the new-team form will open, drawn as the team view draws a team, in its colours:
    the lead's card on top, the members' under it on their wires (peers side by side on one wire).
    Smaller cards, of a line each: the name, and its model and effort when picked. Nothing has
    started yet, so there are no states, tokens or messages to show."""

    card_h, least, gap = 3, 12, 2

    def layout_boxes(self, width: int) -> tuple[dict[str, tuple[int, int, int]], list[list[str]]]:
        """As the team view lays them out, but the lead's card as wide as its name and model need,
        however narrow the members' are (three of them side by side in a 120-column window)."""
        places, rows = super().layout_boxes(width)
        if self.lead in places:
            lead = next(m for m in self.order if m.lead)
            wide = min(width, max(places[self.lead][2], len(lead.name) + len(lead.pick.short()) + 9, 24))
            places[self.lead] = ((width - wide) // 2, 0, wide)
        return places, rows

    def show(self, team: teams.Team) -> None:
        self.team, self.palette = team, colours(team, self.accent)
        self.selected = len(team.members)  # nobody picked: no card drawn heavier than the others
        self.refresh(layout=True)

    def height_for(self, rows: list[list[str]]) -> int:  # the cards only: no line under them
        rows = [row for row in rows if row]
        return (self.card_h if self.lead else 0) + len(rows) * (self.card_h + 1)

    def draw_wires(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        if any(rows):  # a lead that picks its members has nobody under it yet
            super().draw_wires(canvas, places, rows)

    def draw_traffic(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        pass  # nobody has said anything yet

    def draw_focus(self, canvas: Canvas) -> None:
        pass

    def draw_box(self, canvas: Canvas, member: teams.Member, place: tuple[int, int, int]) -> None:
        x, y, card = place
        colour = self.palette.get(member.name, TEXT)
        edge = mix("#000000", colour, 0.55)
        canvas.put(x, y, "╭" + "─" * (card - 2) + "╮", colour)
        canvas.put(x, y + 1, "│" + " " * (card - 2) + "│", edge)
        canvas.put(x, y + 2, "╰" + "─" * (card - 2) + "╯", edge)
        inner = card - 4
        quiet = self.team.background and not member.lead and self.team.lead is not None and member.cli == "claude"
        tag = f"{member.cli} {member.pick.short()}".strip() if member.cli != "claude" else member.pick.short()
        right = ("◌ " if quiet else "") + tag
        name = ("★ " if member.lead else "") + member.name
        right = right if len(name) + 1 + len(right) <= inner else ""
        canvas.put(x + 2, y + 1, name[:inner], f"bold {colour}")
        if right:
            canvas.put(x + 2 + inner - len(right), y + 1, right, MUTED)


TONES = {"strong": f"bold {TEXT}", "muted": MUTED, "amber": f"bold {AMBER}", "green": f"bold {GREEN}"}


def toned(parts: list[tuple[str, str]]) -> Text:
    return Text.assemble(*((words, TONES[tone]) for words, tone in parts))


def still_needed(missing: list[str], loud: bool) -> Text:
    style = f"bold {AMBER}" if loud else MUTED
    return Text.assemble(("Still needed: ", style), (", ".join(missing), style))


class ProjectList(OptionList, can_focus=False):
    """The projects to pick the team's from, moved through from the Project box (↑/↓, Enter) or
    clicked: it never takes the focus itself, so Tab goes from box to box as before."""


class NewTeamDialog(ModalScreen[teams.Team | JoinTeam | TellTeam | None]):
    """A name, a folder, a goal and the line-up, one member a line. Below it, what will open,
    or what is still missing, as you type. Enter goes to the next box; Ctrl+S creates. The rules are
    teamforms' (a TeamForm, self.form): this draws them."""

    # The form on the left in two cards (the team; its line-up), and on the right the team it will
    # open, drawn as the team view draws one, over where the members run. A card is lit (its frame
    # and title in the theme's colour) while the cursor is in it. The help for the box in focus is
    # one line under the cards; the keys are docked at the foot, so nothing can push them off.
    DEFAULT_CSS = frame("NewTeamDialog", "large", height="90%") + cards("NewTeamDialog") + """
    NewTeamDialog > Vertical { width: 95%; max-width: 150; max-height: 36; }
    NewTeamDialog #team-top { height: 1; margin-bottom: 1; }
    NewTeamDialog #team-title { width: 1fr; }
    NewTeamDialog #team-ready { width: auto; }
    NewTeamDialog #team-body { height: 1fr; }
    NewTeamDialog #team-form { width: 1fr; height: 1fr; margin-right: 1; }
    NewTeamDialog #team-side { width: 48%; height: 1fr; }
    NewTeamDialog #team-card { height: auto; }
    NewTeamDialog #lineup-card { height: 1fr; }
    NewTeamDialog .row { height: auto; }
    NewTeamDialog .row > .name { width: 9; color: $text-muted; }
    NewTeamDialog Input { padding: 0 1; }
    NewTeamDialog TextArea { padding: 0 0; }
    NewTeamDialog .row.-lit > .name { color: $text; text-style: bold; }
    NewTeamDialog #team-goal { height: 3; }
    NewTeamDialog #team-lineup { height: auto; margin-bottom: 1; }
    NewTeamDialog #team-members { height: 1fr; min-height: 3; }
    NewTeamDialog #team-members.alone { height: 1; min-height: 1; }
    NewTeamDialog #team-most-row { margin-top: 1; }
    NewTeamDialog #team-most { width: 3; }
    NewTeamDialog .aside { width: 1fr; color: $text-muted; padding-left: 2; }
    NewTeamDialog #graph-card { height: 1fr; }
    NewTeamDialog #team-atwork { height: auto; display: none; }
    NewTeamDialog #projects-card { height: 1fr; display: none; }
    NewTeamDialog #team-side.-picking #projects-card { display: block; }
    NewTeamDialog #team-side.-picking #graph-card { display: none; }
    NewTeamDialog #team-project-head { height: 1; }
    NewTeamDialog ProjectList { height: 1fr; min-height: 3; border: none; padding: 0; background: $surface; }
    NewTeamDialog #team-project-where { height: 1; color: $text-muted; }
    NewTeamDialog #team-preview { height: auto; margin-bottom: 1; }
    NewTeamDialog #team-graph-box { height: 1fr; scrollbar-size-vertical: 1; }
    NewTeamDialog #runs-card { height: auto; }
    NewTeamDialog #team-cap-row, NewTeamDialog #team-advisor-row { height: auto; }
    NewTeamDialog #team-help { height: 1; margin-top: 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+t", "template", "Next line-up", priority=True),
        Binding("ctrl+l", "keep_lineup", "Keep the line-up", priority=True),
        Binding("ctrl+b", "toggle_background", "Tabs or background", priority=True),
        Binding("ctrl+o", "toggle_worktrees", "Own worktrees", priority=True),
        Binding("ctrl+r", "suggest", "Suggest a line-up", priority=True),
        Binding("ctrl+g", "show_role", "What a member is told", priority=True),
        Binding("ctrl+e", "improve", "Improve the goal", priority=True),
        Binding("ctrl+s", "create", "Create", priority=True),
        Binding("ctrl+n", "new_instead", "A new team after all", priority=True),
    ]

    def __init__(self, folder: str, taken: set[str], folders: list[str] | None = None,
                 projects: list[newsessions.Project] | None = None,
                 working: list[tuple[teams.Team, set[str]]] | None = None) -> None:
        super().__init__()
        self.folder = folder
        # The teams at work now (each with its open members' names): one in the project is weighed
        # by Ctrl+R, which may add the goal's members to it, or leave the goal to it (form.toward).
        self.form = teamforms.TeamForm(taken, working or [])
        # The projects you worked in, newest first, listed to pick from while the Project box has the
        # focus (as n lists them); their folders are what the box completes with →.
        self.projects = projects if projects is not None else [newsessions.Project(f) for f in folders or []]
        self.folders = folders if folders is not None else [p.cwd for p in self.projects]
        self.picking_from = ""  # the Project box's words when it took the focus: typing other ones filters
        self.listed: dict[str, newsessions.Project] = {}  # folder key -> the project on that row
        self.asking = False  # Ctrl+R: Claude is being asked for a line-up
        self.improving: str | None = None  # Ctrl+E: the goal being rewritten, as it was sent
        self.improve_note = newsessions.Improving(self.GOAL_NOTE, f"improving the goal with {improve.MODEL}")

    # The form's state, by the names it had here (tests and the dashboard read them).
    taken = property(lambda self: self.form.taken)
    working = property(lambda self: self.form.working)
    saved = property(lambda self: self.form.saved)
    lineups = property(lambda self: self.form.lineups)
    toward = property(lambda self: self.form.toward)
    background = property(lambda self: self.form.background)
    worktrees = property(lambda self: self.form.worktrees)
    tried = property(lambda self: self.form.tried)

    @property
    def template(self) -> int:
        return self.form.template

    @template.setter
    def template(self, value: int) -> None:
        self.form.template = value

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="team-top"):
                yield Label(title("New team"), id="team-title")
                yield Label("", id="team-ready")
            with Horizontal(id="team-body"):
                with Vertical(id="team-form"):
                    with Vertical(id="team-card", classes="card"):
                        with Horizontal(classes="row"):
                            yield Label("Name", classes="name")
                            yield Input(placeholder="e.g. checkout", id="team-name", compact=True)
                        with Horizontal(classes="row"):
                            yield Label("Project", classes="name")
                            yield Input(value=tilde(self.folder), id="team-folder", compact=True,
                                        suggester=FolderSuggester(self.folders))
                        with Horizontal(classes="row"):
                            yield Label("Goal", classes="name")
                            yield TextArea(id="team-goal", compact=True,
                                           placeholder="e.g. Add Stripe checkout with tests")
                    with Vertical(id="lineup-card", classes="card"):
                        yield Static("", id="team-lineup")
                        yield TextArea(teamforms.first_lineup(), id="team-members", compact=True)
                        with Horizontal(classes="row", id="team-most-row"):  # only when the lead decides
                            yield Label("At most", classes="name")
                            yield Input(str(teams.DECIDE_MOST), id="team-most", compact=True, restrict=r"[0-9]*",
                                        max_length=1)
                            yield Static("members, picked by the lead", classes="aside")
                with Vertical(id="team-side"):
                    # In the graph's place while the Project box has the focus.
                    with Vertical(id="projects-card", classes="card"):
                        yield Static("", id="team-project-head")
                        yield ProjectList(id="team-projects")
                        yield Static("", id="team-project-where")
                    with Vertical(id="graph-card", classes="card"):
                        yield Static("", id="team-atwork")  # a team at work in the project, said
                        yield Static("", id="team-preview")
                        with VerticalScroll(id="team-graph-box"):
                            yield LineupGraph(teams.Team("", "", ""), self.accent(), id="team-graph")
                    with Vertical(id="runs-card", classes="card"):
                        yield Static("", id="team-where")  # tabs or the background: Ctrl+B; worktrees: Ctrl+O
                        with Horizontal(classes="row", id="team-cap-row"):  # the members' context cap: ←/→
                            yield Label("Context", classes="name")
                            yield Choice(teamforms.CAPS, value=picks.default_cap(), id="team-cap",
                                         labels=newsessions.CAP_SHORT)
                        with Horizontal(classes="row", id="team-advisor-row"):  # the lead's advisor: ←/→
                            yield Label("Advisor", classes="name")
                            yield Choice(teamforms.ADVISOR_CHOICES, id="team-advisor",
                                         labels=newsessions.ADVISOR_SHORT)
            yield Label("", id="team-help")
            yield Label(self.key_line(0), id="team-keys", classes="hint")

    GOAL_NOTE = teamforms.GOAL_NOTE

    def key_line(self, width: int) -> Text:
        """The keys under the dialog, on one line, even in a 120x30 window: a second one would be cut off
        there. Ctrl+E (also on the Team card's border, always) joins them where the line has room."""
        pairs = [("Ctrl+S", "create"), ("Tab", "next"), ("Ctrl+T", "line-up"), ("Ctrl+L", "keep"),
                 ("Ctrl+R", "suggest"), ("Ctrl+G", "role"), ("Esc", "cancel")]
        with_improve = [*pairs[:5], ("Ctrl+E", "improve"), *pairs[5:]]
        wide = keys(*with_improve)
        return wide if width and wide.cell_len <= width else keys(*pairs)

    def show_keys(self) -> None:
        line = self.query_one("#team-keys", Label)
        line.update(self.key_line(line.parent.content_size.width if line.parent is not None else 0))

    def show_improving(self) -> None:
        """The Team card's border: what Ctrl+E does, that it is at it (the goal is rewritten in a
        thread), or how it went (newsessions.Improving)."""
        self.query_one("#team-card").border_subtitle = self.improve_note.note(
            self.query_one("#team-goal", TextArea).text)

    def on_mount(self) -> None:
        self.query_one("#team-card").border_title = "Team"
        self.show_improving()
        self.query_one("#lineup-card").border_title = "Line-up"
        self.query_one("#runs-card").border_title = "Runs"
        self.show_preview()
        self.show_help()
        self.show_keys()
        self.call_after_refresh(self.show_preview)  # laid out: the line-ups wrap to the card's width
        self.call_after_refresh(self.show_keys)

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self.show_preview()
            self.show_keys()

    def accent(self) -> str:
        theme = getattr(self.app, "theme", "") if self.is_attached else ""
        return THEMES.get(theme, THEMES[DEFAULT_THEME])[1]

    def say(self, note: actions.Note) -> None:
        """A note from the form's rules, said as the form always said it (no timeout: the default one)."""
        said = {} if note.severity == "information" else {"severity": note.severity}
        self.notify(note.words, **said, **({} if note.seconds is None else {"timeout": note.seconds}))

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if self.picking() and not self.query_one("#team-side").has_class("-picking"):
            self.picking_from = self.folder_box.value.strip()  # every project, until other words are typed
        self.show_projects()
        self.light()
        self.show_help()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.light()

    def light(self) -> None:
        """The cards that hold the focus lit, and the projects' while it is the Project box's."""
        light_rows(self)
        self.query_one("#projects-card").set_class(self.picking(), "-lit")

    # --- the project, picked from the ones you worked in ---------------------------------------

    @property
    def folder_box(self) -> Input:
        return self.query_one("#team-folder", Input)

    def picking(self) -> bool:
        """The Project box has the focus: the projects are listed on the right to pick from."""
        return self.is_mounted and self.focused is self.folder_box

    def project_candidates(self) -> list[newsessions.Project]:
        """Every project until other words than the box's are typed (its own folder among them), then
        the ones they find, as n finds them (newsessions.find)."""
        typed = self.folder_box.value.strip()
        if typed != self.picking_from:
            return newsessions.find(typed, self.projects)
        hits = list(self.projects)
        path = Path(os.path.expanduser(typed)) if typed else None
        if path is not None and path.is_dir() and folder_key(str(path)) not in {folder_key(p.cwd) for p in hits}:
            hits.insert(0, newsessions.Project(str(path.resolve())))
        return hits

    def project_row(self, project: newsessions.Project, width: int, terms: list[str]) -> Text:
        """A project as n lists it: its name, its sessions, when it was last worked in."""
        row = newsessions.project_columns(project, width, terms)
        row.no_wrap, row.overflow = True, "ellipsis"
        return row

    def show_projects(self, again: bool = True) -> None:
        """While the Project box has the focus, the projects in the graph's place, the box's folder
        (or the first one found) highlighted. Shown a moment ago, the list has no width yet: again
        once it is laid out, and only once, so it can never go on asking."""
        if not self.is_mounted:
            return
        picking = self.picking()
        self.query_one("#team-side").set_class(picking, "-picking")
        if not picking:
            return
        listing = self.query_one(ProjectList)
        hits = self.project_candidates()
        self.listed = {folder_key(p.cwd): p for p in hits}
        typed = self.folder_box.value.strip()
        terms = typed.split() if typed != self.picking_from else []
        width = max(8, (listing.content_size.width or 50) - listing.styles.scrollbar_size_vertical - 1
                    - newsessions.TAIL)
        listing.clear_options()
        listing.add_options([Option(self.project_row(p, width, terms), id=key) for key, p in self.listed.items()])
        if self.listed:
            here = folder_key(os.path.expanduser(typed)) if typed else ""
            keys_ = list(self.listed)
            listing.highlighted = keys_.index(here) if here in self.listed and not terms else 0
        else:
            listing.add_option(Option(Text(f"Nothing matches “{typed}”. A folder path works too.", style=MUTED),
                                      disabled=True))
        self.query_one("#team-project-head", Static).update(Text(
            "PROJECT".ljust(width) + "SESSIONS".rjust(10) + "LAST".rjust(10), style=f"bold {MUTED}"))
        card = self.query_one("#projects-card")
        card.border_title = "Projects"
        card.border_subtitle = (f"{len(self.listed)} of {len(self.projects)}" if terms
                                else f"{len(self.projects)} project{'s' * (len(self.projects) != 1)}")
        self.show_project_where()
        if again and not listing.content_size.width:
            self.call_after_refresh(self.show_projects, False)

    def highlighted_project(self) -> newsessions.Project | None:
        listing = self.query_one(ProjectList)
        if listing.highlighted is None or not listing.option_count:
            return None
        return self.listed.get(listing.get_option_at_index(listing.highlighted).id or "")

    def show_project_where(self) -> None:
        """Under the list: where the highlighted project is, your home as ~."""
        project = self.highlighted_project()
        where = self.query_one("#team-project-where", Static)
        room = max(8, (where.content_size.width or 50) - 2)
        where.update(Text("▸ " + newsessions.clip(tilde(project.cwd), room), style=MUTED) if project is not None else "")

    def pick_project(self, key: str | None = None) -> None:
        """The project highlighted (or the one clicked) into the box."""
        project = self.listed.get(key) if key else self.highlighted_project()
        if project is not None and os.path.expanduser(self.folder_box.value.strip()) != project.cwd:
            self.folder_box.value = tilde(project.cwd)

    def on_key(self, event: events.Key) -> None:
        # In the Project box ↑/↓ move through the projects (the box itself has no use for them).
        moves = {"up": "cursor_up", "down": "cursor_down", "pageup": "page_up", "pagedown": "page_down"}
        if self.picking() and event.key in moves:
            getattr(self.query_one(ProjectList), f"action_{moves[event.key]}")()
            event.stop()
            event.prevent_default()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if isinstance(event.option_list, ProjectList):
            self.show_project_where()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if isinstance(event.option_list, ProjectList):  # a click on one
            event.stop()
            self.pick_project(event.option.id)
            self.query_one("#team-goal", TextArea).focus()

    # --- the boxes, as the form's rules read them ------------------------------------------------

    def fields(self) -> teamforms.Fields:
        """The boxes as they read now."""
        return teamforms.Fields(self.query_one("#team-name", Input).value, self.query_one("#team-folder", Input).value,
                                self.query_one("#team-goal", TextArea).text, self.members_text(),
                                self.query_one("#team-most", Input).value, self.query_one("#team-cap", Choice).value,
                                self.query_one("#team-advisor", Choice).value)

    def read(self) -> tuple[str, str, str, list[teams.Member]]:
        return self.fields().read()

    def most(self) -> int:
        return self.fields().most_n()

    def missing(self) -> list[str]:
        return self.form.missing(self.fields())

    def members_text(self) -> str:
        return self.query_one("#team-members", TextArea).text

    def set_members(self, text: str) -> None:
        self.query_one("#team-members", TextArea).text = text

    def template_text(self, name: str) -> str:
        return self.form.template_text(name, self.members_text())

    def peers(self) -> bool:
        return self.form.peers(self.members_text())

    def deciding(self) -> bool:
        return self.form.deciding(self.members_text())

    def is_repository(self, folder: str) -> bool:
        return self.form.is_repository(folder)

    def teams_here(self, folder: str) -> list[tuple[teams.Team, set[str]]]:
        return self.form.teams_here(folder)

    def working_team(self, name: str) -> teams.Team | None:
        return self.form.working_team(name)

    def show_help(self) -> None:
        """One line under the cards: how to fill the box in focus."""
        focused = self.focused
        self.query_one("#team-help", Label).update(
            self.form.help(focused.id or "" if focused is not None else "", self.fields()))

    def cap_help(self) -> str:
        """The Context row's help: ←/→, and what the cap picked means."""
        return teamforms.cap_help(self.query_one("#team-cap", Choice).value) if self.is_mounted else ""

    def advisor(self) -> str:
        """The lead's advisor picked; empty for none."""
        return self.fields().chosen_advisor() if self.is_mounted else ""

    def advisor_help(self) -> str:
        """The Advisor row's help: whose advisor it is, and what it costs."""
        return self.form.advisor_help(self.fields()) if self.is_mounted else ""

    def on_choice_changed(self, event: Choice.Changed) -> None:
        self.show_help()

    def lineup(self) -> Text:
        """The ready-made line-ups, the one in the box marked; "your own" once it has been edited."""
        picked = self.form.shown_lineup(self.members_text()) if self.is_mounted else self.form.picked()
        width = self.query_one("#team-lineup").content_size.width if self.is_mounted else 0
        return pills([*self.lineups, teamforms.OWN], picked, width=width or 60)  # Ctrl+T: the keys say so

    def show_lineups(self) -> None:
        """The line-ups wrapped to the card's width, now that it has one (shown, they had none)."""
        if self.is_mounted and self.query_one("#team-lineup").display:
            self.query_one("#team-lineup", Static).update(self.lineup())

    def where(self) -> Text:
        """Where the members run: each in a tab, or in the background with only the lead in a tab.
        Peers each in a tab: you talk to every one of them. And where they work: the project, or
        worktrees of their own."""
        where = self.form.where(self.is_mounted and self.peers())
        (first, on), *others = where.runs
        out = Text.assemble(pill(first, on), (where.note, MUTED))
        if where.key:
            out.append("  ")
            out.append(where.key, style=MUTED)
        for words, on in others:
            out.append("\n")
            out.append_text(pill(words, on))
        out.append("\n")
        out.append("[x] " if where.worktrees else "[ ] ", style=f"bold {CYAN}" if where.worktrees else MUTED)
        out.append(where.worktrees_words, style=f"bold {TEXT}" if where.worktrees else MUTED)
        out.append(f"  {where.worktrees_key}", style=MUTED)
        return out

    def action_toggle_worktrees(self) -> None:
        self.form.toggle_worktrees()
        self.show_preview()

    def action_toggle_background(self) -> None:
        note = self.form.toggle_background(self.members_text())
        if note is not None:
            self.say(note)
            return
        self.show_preview()

    def action_template(self) -> None:
        self.set_members(self.form.next_lineup(self.members_text()))
        self.show_preview()

    # --- a team at work in the project -----------------------------------------------------------

    def leave_toward(self) -> None:
        """A team of its own after all: the line-up that was in the box before, as you left it."""
        self.set_members(self.form.leave_toward(self.members_text()))
        self.show_preview()

    def action_new_instead(self) -> None:
        """Ctrl+N: after Ctrl+R said to add to a team at work, or leave the goal to it: a new team."""
        if self.toward is not None:
            self.leave_toward()

    def show_at_work(self, folder: str) -> None:
        """A line at the top of the drawing's card: the teams at work in this project."""
        names = self.form.at_work(folder)
        line = self.query_one("#team-atwork", Static)
        line.display = bool(names) and self.toward is None
        if names:
            line.update(Text.assemble(("▲ ", AMBER), (names, f"bold {AMBER}"),
                                      (" at work in this project: Ctrl+R weighs the goal against it.", MUTED)))

    def show_toward(self) -> None:
        """The cards when Ctrl+R said to add members to a team at work, or that it has the goal: what
        joins it (or what its lead is told), the team drawn, and what Ctrl+S does."""
        view = self.form.toward_view(self.fields())
        if view is None:
            self.leave_toward()
            return
        box = self.query_one("#team-members", TextArea)
        self.query_one("#team-lineup").display = False
        self.query_one("#team-most-row").display = False
        box.set_class(False, "alone")
        self.show_help()
        card, ready = self.query_one("#graph-card"), self.query_one("#team-ready", Label)
        preview = self.query_one("#team-preview", Static)
        self.query_one("#lineup-card").border_title = view.lineup_title
        self.query_one(LineupGraph).show(view.team)
        self.query_one("#team-title", Label).update(title(*view.title))
        card.border_title = Text(view.heading[0], style=TONES[view.heading[1]])
        if view.missing:
            ready.update("")
            preview.update(still_needed(view.missing, view.loud))
        else:
            ready.update(Text.assemble(("● ", GREEN), ("ready", f"bold {GREEN}"), (f"  {view.does}", MUTED)))
            preview.update(toned(view.how))

    def action_keep_lineup(self) -> None:
        """Ctrl+L: keep the members in the box as a line-up of your own, under a name. On a kept
        one as it is, twice: forget it."""
        step = self.form.keep_step(self.members_text())
        if step.note is not None:
            self.say(step.note)
        if step.changed:
            self.show_preview()
        if not step.ask:
            return

        def keep(name: str | None) -> None:
            kept = self.form.keep(name, step.members, step.peers)
            if kept.note is not None:
                self.say(kept.note)
            if kept.changed:
                self.show_preview()

        self.app.push_screen(LineDialog("Keep this line-up as", "e.g. api and screens"), keep)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "team-folder":
            self.pick_project()  # the project highlighted: the box's own, or the one found
        self.focus_next()  # Enter goes on to the next box; Ctrl+S creates

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "team-folder":
            self.show_projects()
        self.show_preview()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "team-goal":
            self.show_improving()  # how Ctrl+E went stays only while the goal is what it went for
        self.show_preview()

    def show_preview(self) -> None:
        """The cards as the boxes now say: the line-ups, where the members run, and on the right the
        team that will open, drawn, under a line on what opens and how (or what is still needed)."""
        if not self.is_mounted:
            return
        fields = self.fields()
        folder = fields.read()[1]
        if self.form.toward_gone(folder):
            self.leave_toward()  # another project now: that team is not at work in it
            return
        self.show_at_work(folder)
        if self.toward is not None and not self.asking:
            self.show_toward()
            return
        self.query_one("#team-title", Label).update(title("New team"))
        self.query_one("#lineup-card").border_title = "Line-up"
        lineups = self.query_one("#team-lineup", Static)
        lineups.display = True
        lineups.update(self.lineup())
        if not lineups.content_size.width:  # shown again a moment ago (a team at work left): once laid out, again
            self.call_after_refresh(self.show_lineups)
        self.query_one("#team-where", Static).update(self.where())
        deciding = self.deciding()
        self.query_one("#team-most-row").display = deciding
        # Deciding, the box holds the lead's line alone: one row, and what is left to say about it.
        self.query_one("#team-members", TextArea).set_class(deciding, "alone")
        self.show_help()
        self.query_one(LineupGraph).show(self.form.drawn(fields))  # drawn as it stands, whatever is still missing
        card, ready = self.query_one("#graph-card"), self.query_one("#team-ready", Label)
        preview = self.query_one("#team-preview", Static)
        shown = self.form.preview(fields, self.asking)
        if shown.asking:
            card.border_title = shown.title
            ready.update("")
            preview.update(Text(shown.head[0][0], style=f"italic {MUTED}"))
            return
        if shown.missing:
            card.border_title = shown.title
            ready.update("")
            preview.update(still_needed(shown.missing, shown.loud))
            return
        card.border_title = Text(shown.tabs, style=f"bold {GREEN}")
        ready.update(Text.assemble(("● ", GREEN), ("ready", f"bold {GREEN}"), ("  Ctrl+S creates it", MUTED)))
        preview.update(toned(shown.head)[len(shown.tabs) + 2:])  # the card's title says what opens; this line, how

    # --- help with the roles ---------------------------------------------------------------

    def action_improve(self) -> None:
        """Ctrl+E: Claude rewrites the goal clearer; it goes into the box as one edit, so Ctrl+Z there
        puts yours back."""
        if self.improving is not None:
            return
        box = self.query_one("#team-goal", TextArea)
        refusal = teamforms.improve_refusal(box.text)
        if refusal is not None:
            self.say(refusal)
            return
        self.improving = box.text
        self.improve_note.start(self, box)
        folder = self.read()[1]
        text = box.text
        self.run_worker(lambda: self.ask_improve(text, folder), thread=True, group="improve", exit_on_error=False)

    def ask_improve(self, text: str, folder: str) -> None:
        """In a thread: the rewrite, or why there is none."""
        try:
            better, why = improve.improve_goal(text, folder), ""
        except Exception as problem:  # noqa: BLE001 - no rewrite leaves the goal as it was, and says why
            better, why = "", str(problem) or type(problem).__name__
        self.app.call_from_thread(self.improved, text, better, why)

    def improved(self, text: str, better: str, why: str) -> None:
        self.improving = None
        if self.is_mounted and self.improve_note.finish(self, self.query_one("#team-goal", TextArea), "#team-card",
                                                        text, better, why, self.notify):
            self.say(teamforms.IMPROVED)

    def action_suggest(self) -> None:
        """Ctrl+R: Claude reads the goal and the project's top level and suggests the team's size, and
        why, with a line-up: each member with its part, its own files, a model and an effort, a lead
        only when the parts need one. The line-up goes into the box; where the lead decides, the size
        goes into At most instead. One call on your plan; it is yours to change after."""
        if self.asking:
            return
        ask = self.form.suggest_ask(self.fields())
        if ask.refusal is not None:
            self.say(ask.refusal)
            return
        if ask.box is not None:  # weighed afresh, from the line-up you had
            self.set_members(ask.box)
            self.show_preview()
        self.asking = True
        self.show_preview()
        self.run_worker(lambda: self.ask_for_lineup(ask.prompt, ask.here, ask.goal), thread=True,
                        exclusive=True, group="suggest", exit_on_error=False)

    def ask_for_lineup(self, prompt: str, here: list[teams.Team] | None = None, goal: str = "") -> None:
        """In a thread: the answer, or why there is none."""
        try:
            answer = summarize.ask(prompt, "", timeout=180, effort=teams.SUGGEST_EFFORT)
            suggestion = teams.read_suggestion(answer, here, goal)
            error = "" if suggestion.lines or suggestion.kind != teams.OWN_TEAM else "Claude's answer named no members."
        except Exception as problem:  # noqa: BLE001 - a failed suggestion leaves the box as it was, and says why
            suggestion, error = None, str(problem) or type(problem).__name__
        self.app.call_from_thread(self.suggested, suggestion, error)

    def suggested(self, suggestion: teams.Suggestion | None, error: str) -> None:
        self.asking = False
        if not self.is_mounted:
            return
        done = self.form.suggested(suggestion, error, self.fields())
        if done.box is not None:
            self.set_members(done.box)
        if done.most is not None:
            self.query_one("#team-most", Input).value = done.most
        self.say(done.note)
        self.show_preview()

    def action_show_role(self) -> None:
        """Ctrl+G: what the member on the cursor's line is told when it starts: its role, word for
        word, and its first message."""
        box = self.query_one("#team-members", TextArea)
        lines = box.text.split("\n")
        row = box.cursor_location[0]
        found = self.form.role_of(self.fields(), lines[row] if row < len(lines) else "")
        if isinstance(found, actions.Note):
            self.say(found)
        elif found is not None:
            self.app.push_screen(RoleDialog(*found))

    def action_create(self) -> None:
        made = self.form.create(self.fields())
        if made is None:
            self.show_preview()
            return
        self.dismiss(made)

    def action_cancel(self) -> None:
        self.dismiss(None)
