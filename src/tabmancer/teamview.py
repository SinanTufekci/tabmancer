"""The team view: the orchestrator's card on top, the members' cards under it on a tree of wires,
every message flying from one card to another as it is sent, and the conversation underneath as
arrows between the members' lanes. Enter on a team's row opens it from the dashboard (T lists the teams)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
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
    background,
    context,
    focus,
    handover,
    improve,
    newsessions,
    picks,
    summarize,
    teams,
    teamtree,
    viewmodel,
)
from .dialogs import Choice, FolderSuggester, cards, frame, light_rows, pill, pills, tilde, title
from .safework import work
from .sessions import Exit, Session, Status, folder_key
from .statsview import copy_text
from .theme import AMBER, BLUE, CYAN, DEFAULT_THEME, GREEN, MUTED, ORANGE, RED, TEXT, THEMES, keys, mix
from .tokens import human

PALETTE = [CYAN, GREEN, AMBER, ORANGE, "#c678dd", "#61afef", "#e5c07b", "#56b6c2"]
FLIGHT_SECONDS = 1.8  # how long a message takes to fly from card to card
CARD_H = 4  # a card's rows: its border, the name and what it does, its context and tokens, its border
CARD_LEAST, CARD_MOST = 24, 36  # cards grow with the room between these widths
GAP = 3
METER = 5  # cells of a card's context meter
WIRE = "#3a3a3a"
STAR = "·✢✶✻✽✻✶✢"
LOG_LINES = 200


def colours(team: teams.Team, accent: str) -> dict[str, str]:
    """The lead in the theme's accent, the others in the palette's colours; peers, all of them."""
    out = {teams.YOU: TEXT, teams.TABMANCER: MUTED}
    if team.lead is not None:
        out[team.lead.name] = accent
    others = [m for m in team.members if not m.lead]
    out.update({m.name: PALETTE[i % len(PALETTE)] for i, m in enumerate(others)})
    return out


def status_of(session: Session | None) -> tuple[str, str, bool]:
    """(label, style, spinning) for a member's card, in the dashboard's colours: blue works, red
    waits for you, amber is stopped at a usage limit (as in the list); a member done for now is
    idle, quiet."""
    if session is None:
        return "not started", MUTED, False
    if viewmodel.at_limit(session):
        return "at the limit", AMBER, False
    if session.exit is not Exit.RUNNING:
        return "closed", MUTED, False
    state = session.live_state.lower()
    if state in ("busy", "working"):
        return "working", BLUE, True
    if state == "permission":
        return "needs your OK", f"bold {RED}", False
    if state in ("blocked", "waiting"):
        return "needs you", f"bold {RED}", False
    if state == "resting":
        return "idle", MUTED, False
    return "waiting", RED, False


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

    def render(self) -> Text:
        width = max(40, self.size.width or 100)
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
        right = " · ".join(part for part in (member.pick.short(), tokens) if part)
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
            canvas.put(x + 2 + inner - len(right), y + 2, right, MUTED)  # its model and tokens, at the right

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
        places, _ = self.layout_boxes(max(40, self.size.width or 100))
        path = self.path(message.sender, message.receiver, places)
        if path:
            self.flights.append(Flight(message, path, time.monotonic()))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _clip_goal(goal: str, most: int = 220) -> str:
    """A goal on one line, cut short where it is long."""
    line = _one_line(goal)
    return line if len(line) <= most else line[:most - 1].rstrip() + "…"


ADD_QUESTION = "Add members as name: what they work on (several with ;, @sonnet at the end picks a model)"
ADD_EXAMPLE = "docs: the README and the user guide"


def closing_note(team: teams.Team, sessions: dict[str, Session | None]) -> str | None:
    """What x says before it closes a team's open members (None: nobody is open)."""
    open_now = [s for s in sessions.values() if s is not None and s.exit is Exit.RUNNING]
    if not open_now:
        return None
    busy = sum(s.live_state.lower() in ("busy", "working") for s in open_now)
    at_work = f" {busy} of them {'is' if busy == 1 else 'are'} working and would stop." if busy else ""
    return (f"Press x again to close the {len(open_now)} open members' tabs.{at_work} Their sessions stay, and "
            "s starts them again.")


def open_names(team: teams.Team, sessions: dict[str, Session | None]) -> list[str]:
    """The session names of the members open now, the tabs x closes."""
    return [team.session_name(m) for m in team.members
            if (s := sessions.get(m.name)) is not None and s.exit is Exit.RUNNING]


def close_tabs(names: list[str]) -> tuple[str, str]:
    """Close the members' tabs (Claude exits as on /exit), and stop the ones running in the background,
    which have none (claude stop: the session stays): what to say, and how loud. Slow: a worker's."""
    missed = [name for name in names if not focus.close_tab(name)]
    stopped = 0
    if missed:
        running = {job.name: job for job in background.jobs() or [] if job.running}
        for name in list(missed):
            if name in running:
                try:
                    background.stop(running[name].id)
                except OSError:
                    continue
                missed.remove(name)
                stopped += 1
    if missed:
        return f"Could not find the tab of {', '.join(missed)}: close it yourself (/exit).", "warning"
    closed = len(names) - stopped
    said = [f"closed {closed} tab{'s' if closed != 1 else ''}"] if closed else []
    said += [f"stopped {stopped} in the background"] if stopped else []
    return f"The team's members: {' and '.join(said)}.", "information"


def team_handover_problem(team: teams.Team, sessions: dict[str, Session | None]) -> str | None:
    """Why the team cannot be handed over now; None when it can."""
    if team.lead is None:
        return "A team of peers has no lead to write the handover: H hands the picked member over by itself."
    lead = sessions.get(team.lead.name)
    if lead is None or lead.path is None:
        return "The lead has no session yet: there is nothing to hand over."
    busy = [m.name for m in team.members if (s := sessions.get(m.name)) is not None and s.exit is Exit.RUNNING
            and s.live_state not in handover.IDLE]
    if busy:
        return (f"{', '.join(busy)} {'is' if len(busy) == 1 else 'are'} working: hand the team over once "
                f"{'it waits' if len(busy) == 1 else 'they wait'} for you.")
    return None


def handing_note(team: teams.Team, sessions: dict[str, Session | None]) -> str:
    """What h once says: what a second h does."""
    names = open_names(team, sessions)
    closing = f"closes {len(names)} open member{'s' if len(names) != 1 else ''}, " if names else ""
    return (f"h again hands {team.name} over: it {closing}its lead writes the handover and the line-up the rest of "
            "the work needs, and you see both before the new team opens.")


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
            yield Label("The lead wrote this for its successor. The team starts again under its name: a fresh lead "
                        "with this handover, and the members on the right, fresh. The old sessions stay, resumable.",
                        id="handover-about")
            with Horizontal(id="handover-body"):
                with VerticalScroll(id="handover-text-card", classes="card"):
                    yield Static(self.written, id="handover-text")
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
        members = self.members()
        return [problem for problem, wrong in (
            ("a member at least, one a line", not members and not self.team.decide),
            # A fresh member would start in the old one's worktree, on its branch.
            *((f"another name for {m.name}: its worktree from before is still in the project",
               self.team.has_worktree(m) and Path(self.team.cwd, *teams.WORKTREES,
                                                  self.team.session_name(m)).exists()) for m in members)) if wrong]

    def cap(self) -> str:
        return self.query_one("#handover-cap", Choice).value

    def show_preview(self) -> None:
        members = self.members()
        chosen = self.cap()
        self.query_one("#handover-cap-words", Label).update(
            f"{newsessions.CAP_SHORT.get(chosen, chosen)}: {picks.CAP_WORDS[chosen]}")
        cap = picks.cap_tokens(chosen)
        out = Text.assemble(("Opens ", MUTED), (f"{len(members) + 1} tabs", f"bold {GREEN}"),
                            (": the lead, with the handover", MUTED))
        for member in members:
            out.append("\n● ", style=GREEN)
            out.append(member.name, style=f"bold {CYAN}")
            out.append(f"  {member.pick.short() or 'your default model and effort'}", style=MUTED)
        out.append(f"\nEach under a {human(cap)} context cap." if cap else
                   "\nEach on Claude Code's own window, with no cap.", style=MUTED)
        for problem in self.problems():
            out.append(f"\n▲ needs {problem}", style=AMBER)
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
                         ("x", "close"), ("Esc", "back")),
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
        self.query_one("#team-head", Static).update(Text.assemble(
            ("TEAM ", f"bold {MUTED}"), (self.team.name, f"bold {self.accent}"),
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
        live = teams.live_names()
        sessions = teams.member_sessions(self.team, listed, live)
        for member in self.team.members:  # open, but nothing said yet: no transcript to list it by
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
        keys = {(m.sender, m.receiver, m.text) for m in said}
        if self.seen is None:  # the first look: history stays put, nothing flies
            self.seen = keys
        else:
            members = {m.name for m in self.team.members}
            for message in said:
                key = (message.sender, message.receiver, message.text)
                if key in self.seen:
                    continue
                self.seen.add(key)
                if message.sender in members and message.receiver in members:
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
        said, severity = close_tabs(names)
        self.app.call_from_thread(self.notify, said, severity=severity, timeout=8 if severity == "warning" else 4)

    def action_start_team(self) -> None:
        getattr(self.app, "kept_handovers", {}).pop(self.team.name, None)  # going on as it was, not handed over
        launches = teams.start(self.team, self.sessions)
        if not launches:
            self.notify("Every member is open already.", timeout=3)
            return
        self.open_team(launches)

    def action_add_member(self) -> None:
        def add(text: str | None) -> None:
            new, note = teams.add_members(self.team, text or "")
            if note:
                self.notify(note[0].upper() + note[1:] + ".", severity="warning", timeout=8)
            if not new:
                return
            self.adopt(teams.load().get(self.team.name, self.team))
            self.open_team(teams.joining(self.team, new))

        self.app.push_screen(LineDialog(ADD_QUESTION, ADD_EXAMPLE, enter="add them"), add)

    def action_remove_member(self) -> None:
        """d, twice: the picked member is taken off the team. Its session stays, an ordinary one
        from then on, and the lead (or, among peers, the others) is told: it started with the old
        line-up."""
        member = self.graph.selected_member()
        if member is None:
            self.notify("Pick a member with ← → first.", timeout=3)
            return
        if member.lead:
            self.notify("The lead stays: a team has one. h hands it over to a fresh session instead.",
                        severity="warning", timeout=6)
            return
        session = self.sessions.get(member.name)
        if self.removing != member.name:
            self.removing = member.name
            still = " Its tab stays open, as an ordinary session." if session and session.exit is Exit.RUNNING else ""
            self.notify(f"Press d again to take {member.name} off the team. Its sessions stay.{still}", timeout=6)
            self.set_timer(6, lambda: setattr(self, "removing", None))
            return
        self.removing = None
        team = teams.remove_member(self.team.name, member.name)
        if team is None:
            return
        self.graph.selected = 0
        self.adopt(team)
        self.tell_lead(teams.left_note(team, member.name), f"{member.name} is off the team.")

    def action_edit_goal(self) -> None:
        """g: the team's goal, changed. Members started from now on get the new one; the lead is told."""

        def chosen(goal: str | None) -> None:
            goal = " ".join((goal or "").split())
            if not goal or goal == _one_line(self.team.goal):  # a goal of several lines comes back on one
                return
            team = teams.set_goal(self.team.name, goal)
            if team is None:
                return
            self.adopt(team)
            self.tell_lead(teams.goal_note(team), "The goal is changed.")

        self.app.push_screen(LineDialog("The team's goal", "e.g. Add Stripe checkout with tests",
                                        value=_one_line(self.team.goal)), chosen)

    def tell_lead(self, note: str, done: str) -> None:
        """A change made here, for the lead to hear: nothing can type into its tab, so the note goes
        to the clipboard and you to its tab, to paste it. A closed lead gets it in its role. Peers
        each started with the old team in their role: the note is for every open one."""
        if self.team.lead is None:
            open_ones = [name for name, s in self.sessions.items()
                         if s is not None and s.exit is Exit.RUNNING and any(m.name == name for m in self.team.members)]
            if not open_ones:
                self.notify(f"{done} Nobody is open: each starts with the change in its role.", timeout=6)
                return
            copy_text(self.app, note)
            go = getattr(self.app, "go_to_tab", None)
            if go is not None:
                go(self.sessions[open_ones[0]])
            self.notify(f"{done} A note for the team is on the clipboard: paste it into each open member's tab "
                        f"({', '.join(open_ones)}; Ctrl+V, Enter).", title="Tell the team", timeout=14)
            return
        lead = self.sessions.get(self.team.lead.name)
        if lead is None or lead.exit is not Exit.RUNNING:
            self.notify(f"{done} The lead is not open: it starts with the change in its role.", timeout=6)
            return
        copy_text(self.app, note)
        go = getattr(self.app, "go_to_tab", None)
        if go is not None:
            go(lead)
        self.notify(f"{done} A note for the lead is on the clipboard: paste it into its tab (Ctrl+V, Enter).",
                    title="Tell the lead", timeout=12)

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
        self.dismiss(event.value.strip() or None)

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
        team, member = self.team, self.member
        kind = teams.kind_of(member)
        about = [f"a {kind}er" if kind in ("review", "research") else f"the {kind}s" if kind else "",
                 member.pick.words(), f"owns {member.owns}" if member.owns else ""]
        first = teams.launch(team, member).prompt
        with Vertical():
            yield Label(title(f"What {team.session_name(member)} is told", *[part for part in about if part]))
            with VerticalScroll(id="role-text"):
                yield Static(Text(teams.role(team, member), style=TEXT))
            yield Label(Text.assemble(("Its first message: ", MUTED), (first, TEXT)) if first else
                        Text(f"Its first message comes from {team.session_name(team.lead)}." if team.lead and
                             not member.lead else "It starts with no message.", style=MUTED), classes="first")
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
        quiet = self.team.background and not member.lead and self.team.lead is not None
        right = ("◌ " if quiet else "") + member.pick.short()
        name = ("★ " if member.lead else "") + member.name
        right = right if len(name) + 1 + len(right) <= inner else ""
        canvas.put(x + 2, y + 1, name[:inner], f"bold {colour}")
        if right:
            canvas.put(x + 2 + inner - len(right), y + 1, right, MUTED)


LEAD_LINE = f"{teams.LEAD}: {teams.LEAD_FOCUS}"
# One line, so the keys stay on screen in a 120x30 window; the examples show how a line is written.
MEMBERS_HELP = "name: area @model @effort · lead @opus @xhigh · code @opus @high · tests, docs @sonnet @medium"
LEAD_HELP = ("@opus @xhigh (any model, any effort) at the end: the lead's own; "
             "it picks the members' itself")
SAID_HERE = 160  # characters of the message to a team at work's lead the form shows: all of it is sent
TOWARD_HELP = {  # the box's help when Ctrl+R said to add to a team at work, or that it has the goal
    teams.ADD_TO: "The members to add, one a line as in a line-up: each opens in its tab and joins the team at work.",
    teams.HAS_IT: "The message for the lead, yours to change: Ctrl+S types it into its tab once it waits for you.",
}


class ProjectList(OptionList, can_focus=False):
    """The projects to pick the team's from, moved through from the Project box (↑/↓, Enter) or
    clicked: it never takes the focus itself, so Tab goes from box to box as before."""


@dataclass
class JoinTeam:
    """The new-team form's answer when the goal adds to a team at work: these members join it."""
    team: str
    members: str  # one a line, as in a line-up
    to: str       # the session told: its lead, or among peers the member the goal concerns most
    message: str
    goal: str     # what the team's goal grows by


@dataclass
class TellTeam:
    """The new-team form's answer when a team at work has the goal already: its lead is told."""
    team: str
    to: str
    message: str


class NewTeamDialog(ModalScreen[teams.Team | JoinTeam | TellTeam | None]):
    """A name, a folder, a goal and the line-up, one member a line. Below it, what will open,
    or what is still missing, as you type. Enter goes to the next box; Ctrl+S creates."""

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
        self.folder, self.taken = folder, taken
        # The teams at work now (each with its open members' names): one in the project is weighed
        # by Ctrl+R, which may add the goal's members to it, or leave the goal to it (self.toward).
        self.working = working or []
        self.toward: teams.Suggestion | None = None
        self.before_toward: str | None = None  # the line-up in the box before Ctrl+R put another team's in it
        # The projects you worked in, newest first, listed to pick from while the Project box has the
        # focus (as n lists them); their folders are what the box completes with →.
        self.projects = projects if projects is not None else [newsessions.Project(f) for f in folders or []]
        self.folders = folders if folders is not None else [p.cwd for p in self.projects]
        self.picking_from = ""  # the Project box's words when it took the focus: typing other ones filters
        self.listed: dict[str, newsessions.Project] = {}  # folder key -> the project on that row
        self.template = 0
        self.tried = False  # once Ctrl+S has been pressed, what is missing is said louder
        self.saved = teams.saved_lineups()  # line-ups of your own, kept with Ctrl+L
        self.lineups = [*teams.TEMPLATES, *teams.PEER_TEMPLATES, *self.saved, teams.DECIDE]
        self.forgetting: str | None = None  # Ctrl+L once on a kept line-up: again forgets it
        self.background = False  # Ctrl+B: the members run as background sessions, the lead in a tab
        self.asking = False  # Ctrl+R: Claude is being asked for a line-up
        self.improving: str | None = None  # Ctrl+E: the goal being rewritten, as it was sent
        self.worktrees = False  # Ctrl+O: each member in a git worktree of its own
        self.repositories: dict[str, bool] = {}  # folder -> is a repository's top (is_repository)

    def compose(self) -> ComposeResult:
        first = next(iter(teams.TEMPLATES))
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
                        yield TextArea(f"{LEAD_LINE}\n{teams.TEMPLATES[first]}", id="team-members", compact=True)
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
                            yield Choice([*picks.CAPS, picks.NO_CAP], value=picks.default_cap(), id="team-cap",
                                         labels=newsessions.CAP_SHORT)
                        with Horizontal(classes="row", id="team-advisor-row"):  # the lead's advisor: ←/→
                            yield Label("Advisor", classes="name")
                            yield Choice([picks.NO_ADVISOR, *picks.ADVISORS], id="team-advisor",
                                         labels=newsessions.ADVISOR_SHORT)
            yield Label("", id="team-help")
            # One line, even in a 120x30 window: a second one would be cut off there.
            yield Label(keys(("Ctrl+S", "create"), ("Tab", "next"), ("Ctrl+T", "line-up"), ("Ctrl+L", "keep"),
                             ("Ctrl+R", "suggest"), ("Ctrl+G", "role"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#team-card").border_title = "Team"
        self.query_one("#lineup-card").border_title = "Line-up"
        self.query_one("#runs-card").border_title = "Runs"
        self.show_preview()
        self.show_help()
        self.call_after_refresh(self.show_preview)  # laid out: the line-ups wrap to the card's width

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self.show_preview()

    def accent(self) -> str:
        theme = getattr(self.app, "theme", "") if self.is_attached else ""
        return THEMES.get(theme, THEMES[DEFAULT_THEME])[1]

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

    def show_help(self) -> None:
        """One line under the cards: how to fill the box in focus."""
        focused = self.focused
        about = {
            "team-name": "One word: each member's tab is named after it, checkout-api.",
            "team-folder": "Type to find a project; ↑/↓ and Enter pick it. A folder path works too, → completes it.",
            "team-goal": "What the team is for: a plan pasted in keeps its lines, every member gets it. Ctrl+E improves it.",
            "team-members": (TOWARD_HELP[self.toward.kind] if self.toward is not None
                             else LEAD_HELP if self.deciding() else MEMBERS_HELP),
            "team-most": "At most this many: the lead picks them from the goal, fewer for small work. Ctrl+R suggests one.",
            "team-cap": self.cap_help(),
            "team-advisor": self.advisor_help(),
        }.get(focused.id if focused is not None else "",
              "Every member is a session of its own; with no lead line, they work as peers.")
        self.query_one("#team-help", Label).update(about)

    def cap_help(self) -> str:
        """The Context row's help: ←/→, and what the cap picked means."""
        if not self.is_mounted:
            return ""
        cap = self.query_one("#team-cap", Choice).value
        return (f"←/→ the context cap every member starts under. "
                f"{newsessions.CAP_SHORT.get(cap, cap)}: {picks.CAP_WORDS[cap]}.")

    def advisor(self) -> str:
        """The lead's advisor picked; empty for none."""
        value = self.query_one("#team-advisor", Choice).value if self.is_mounted else picks.NO_ADVISOR
        return "" if value == picks.NO_ADVISOR else value

    def advisor_help(self) -> str:
        """The Advisor row's help: whose advisor it is, and what it costs."""
        if not self.is_mounted:
            return ""
        advisor = self.advisor()
        if self.peers():
            return "←/→ an advisor for the lead; a team of peers has none, so it is not given."
        if not advisor:
            return "←/→ an advisor for the lead (only the lead): a stronger model it asks at the turns that matter."
        lead = next((m for m in self.read()[3] if m.lead), None)
        if lead is not None and not picks.advisor_ok(lead.model, advisor):
            return f"{advisor} cannot advise a {picks.family(lead.model)} lead: only a model as strong or stronger can."
        return f"←/→ the lead's advisor, for its session only. {advisor}: {picks.ADVISOR_WORDS[advisor]}."

    def on_choice_changed(self, event: Choice.Changed) -> None:
        self.show_help()

    def lineup(self) -> Text:
        """The ready-made line-ups, the one in the box marked; "your own" once it has been edited."""
        own = self.is_mounted and self.members_text() != self.template_text(self.lineups[self.template])
        picked = "your own" if own else self.lineups[self.template]
        width = self.query_one("#team-lineup").content_size.width if self.is_mounted else 0
        return pills([*self.lineups, "your own"], picked, width=width or 60)  # Ctrl+T: the keys say so

    def where(self) -> Text:
        """Where the members run: each in a tab, or in the background with only the lead in a tab.
        Peers each in a tab: you talk to every one of them. And where they work: the project, or
        worktrees of their own."""
        if self.is_mounted and self.peers():
            out = Text.assemble(pill("each in a tab", True), ("(peers: you talk to each)", MUTED))
        else:
            out = Text.assemble(pill("each in a tab", not self.background), "  ", ("Ctrl+B", MUTED), "\n",
                                pill("the members in the background", self.background))
        out.append("\n")
        out.append("[x] " if self.worktrees else "[ ] ", style=f"bold {CYAN}" if self.worktrees else MUTED)
        out.append("own git worktrees", style=f"bold {TEXT}" if self.worktrees else MUTED)
        out.append("  Ctrl+O", style=MUTED)
        return out

    def is_repository(self, folder: str) -> bool:
        """Whether the folder is a git repository's top, asked of git once a folder."""
        if folder not in self.repositories:
            self.repositories[folder] = bool(folder) and Path(folder).is_dir() and teams.repository_root(folder)
        return self.repositories[folder]

    def action_toggle_worktrees(self) -> None:
        """Ctrl+O: each member (not the lead) in a git worktree of its own, on a branch of its own, so
        two members' half-done changes never mix in one checkout."""
        self.worktrees = not self.worktrees
        self.show_preview()

    def action_toggle_background(self) -> None:
        if self.peers():
            self.notify("Peers each run in a tab: you talk to every one of them.", timeout=4)
            return
        self.background = not self.background
        self.show_preview()

    def members_text(self) -> str:
        return self.query_one("#team-members", TextArea).text

    def template_text(self, name: str) -> str:
        """A line-up with the lead line kept as it is in the box (its model, say). When the lead
        decides, the lead line is all there is."""
        lead = next((line for line in self.members_text().split("\n")
                     if teams.clean(line.partition(":")[0].partition("@")[0]) == teams.LEAD), LEAD_LINE)
        if name == teams.DECIDE:
            return lead
        if name in teams.PEER_TEMPLATES:  # peers: no lead line
            return teams.PEER_TEMPLATES[name]
        kept = self.saved.get(name, "")
        if kept.startswith(teams.PEERS_MARK):
            return kept.removeprefix(teams.PEERS_MARK)
        return f"{lead}\n{teams.TEMPLATES.get(name) or kept}"

    def peers(self) -> bool:
        """No lead line in the box (and not the line-up where the lead decides): a team of peers."""
        return (self.lineups[self.template] != teams.DECIDE
                and not any(m.lead for m in teams.parse_members(self.members_text(), lead_ok=True)))

    def deciding(self) -> bool:
        """The lead picks the members: that line-up chosen, and no member written in."""
        return (self.lineups[self.template] == teams.DECIDE
                and not any(not m.lead for m in teams.parse_members(self.members_text(), lead_ok=True)))

    def action_template(self) -> None:
        if self.toward is not None:  # from members for a team at work back to a team of its own
            self.leave_toward()
            return
        self.template = (self.template + 1) % len(self.lineups)
        self.query_one("#team-members", TextArea).text = self.template_text(self.lineups[self.template])
        self.show_preview()

    # --- a team at work in the project -----------------------------------------------------------

    def teams_here(self, folder: str) -> list[tuple[teams.Team, set[str]]]:
        """The teams at work in the project (or a folder of it), each with its open members."""
        return [(team, names) for team, names in self.working if folder and teams.in_team_folder(team, folder)]

    def working_team(self, name: str) -> teams.Team | None:
        return next((team for team, _ in self.working if team.name == name), None)

    def leave_toward(self) -> None:
        """A team of its own after all: the line-up that was in the box before, as you left it."""
        self.toward = None
        before, self.before_toward = self.before_toward, None
        self.query_one("#team-members", TextArea).text = (
            before if before is not None else self.template_text(self.lineups[self.template]))
        self.show_preview()

    def action_new_instead(self) -> None:
        """Ctrl+N: after Ctrl+R said to add to a team at work, or leave the goal to it: a new team."""
        if self.toward is not None:
            self.leave_toward()

    def show_at_work(self, folder: str) -> None:
        """A line at the top of the drawing's card: the teams at work in this project."""
        here = self.teams_here(folder)
        line = self.query_one("#team-atwork", Static)
        line.display = bool(here) and self.toward is None
        if here:
            names = ", ".join(f"{team.name} ({len(open_names)} open)" for team, open_names in here)
            line.update(Text.assemble(("▲ ", AMBER), (names, f"bold {AMBER}"),
                                      (" at work in this project: Ctrl+R weighs the goal against it.", MUTED)))

    def show_toward(self) -> None:
        """The cards when Ctrl+R said to add members to a team at work, or that it has the goal: what
        joins it (or what its lead is told), the team drawn, and what Ctrl+S does."""
        toward = self.toward
        team = self.working_team(toward.team)
        if team is None:
            self.leave_toward()
            return
        box = self.query_one("#team-members", TextArea)
        self.query_one("#team-lineup").display = False
        self.query_one("#team-most-row").display = False
        box.set_class(False, "alone")
        self.show_help()
        card, ready = self.query_one("#graph-card"), self.query_one("#team-ready", Label)
        preview = self.query_one("#team-preview", Static)
        if toward.kind == teams.ADD_TO:
            self.query_one("#lineup-card").border_title = f"Members to add to {toward.team}"
            new = [m for m in teams.parse_members(box.text) if m.name not in {m.name for m in team.members}]
            self.query_one(LineupGraph).show(replace(team, members=[*team.members, *new]))
            told = team.session_name(team.lead) if team.lead is not None else "the others"
            heading = Text(f"Adds {len(new)} to {toward.team}", style=f"bold {GREEN}")
            # Its start only: a whole message (a long one, often) pushed the team out of the card. All of it is sent.
            said = teams.cut(toward.message, SAID_HERE)
            how = Text.assemble((f"{toward.team} is at work here, and this goal adds to its work. ", f"bold {AMBER}"),
                                ((f"Each one opens in its tab and introduces itself to {told}; {toward.to} is told: "
                                  f"“{said}” Ctrl+N: a new team instead."), MUTED))
            does = "Ctrl+S adds them"
            self.query_one("#team-title", Label).update(title("Add to a team at work", toward.team))
        else:
            self.query_one("#team-title", Label).update(title("Tell a team at work", toward.team))
            self.query_one("#lineup-card").border_title = f"To {toward.to}"
            self.query_one(LineupGraph).show(team)
            heading = Text(f"{toward.team} has this goal", style=f"bold {AMBER}")
            how = Text.assemble((f"{toward.team} is at work on this goal already: no new team needed. ",
                                 f"bold {AMBER}"),
                                ((f"Ctrl+S sends the message on the left to {toward.to}: typed into its tab once it "
                                  "waits for you. Ctrl+N: a new team anyway."), MUTED))
            does = "Ctrl+S sends it"
        card.border_title = heading
        missing = self.missing()
        if missing:
            ready.update("")
            style = f"bold {AMBER}" if self.tried else MUTED
            preview.update(Text.assemble(("Still needed: ", style), (", ".join(missing), style)))
        else:
            ready.update(Text.assemble(("● ", GREEN), ("ready", f"bold {GREEN}"), (f"  {does}", MUTED)))
            preview.update(how)

    def action_keep_lineup(self) -> None:
        """Ctrl+L: keep the members in the box as a line-up of your own, under a name. On a kept
        one as it is, twice: forget it."""
        current = self.lineups[self.template]
        if current in self.saved and self.members_text() == self.template_text(current):
            if self.forgetting != current:
                self.forgetting = current
                self.notify(f"Press Ctrl+L again to forget the line-up {current}.", timeout=4)
                return
            self.forgetting = None
            teams.keep_lineup(current, None)
            del self.saved[current]
            self.lineups.remove(current)
            self.template = 0
            self.notify(f"Forgot the line-up {current}.", timeout=4)
            self.show_preview()
            return
        members = [line.strip() for line in self.members_text().split("\n") if line.strip()
                   and teams.clean(line.partition(":")[0].partition("@")[0]) != teams.LEAD]
        if not members:
            self.notify("Write its members first; with the lead alone it is the line-up lead decides.", timeout=5)
            return
        peers = self.peers()

        def keep(name: str | None) -> None:
            name = " ".join((name or "").split())[:30]
            if not name:
                return
            if name in teams.TEMPLATES or name in teams.PEER_TEMPLATES or name in (teams.DECIDE, "your own"):
                self.notify(f"{name} is a ready-made line-up: give yours another name.", severity="warning")
                return
            kept = (teams.PEERS_MARK if peers else "") + "\n".join(members)  # a line-up of peers stays one
            teams.keep_lineup(name, kept)
            self.saved[name] = kept
            if name not in self.lineups:
                self.lineups.insert(len(self.lineups) - 1, name)  # before lead decides
            self.template = self.lineups.index(name)
            self.notify(f"Kept the line-up {name}: Ctrl+T comes to it next time.", timeout=4)
            self.show_preview()

        self.app.push_screen(LineDialog("Keep this line-up as", "e.g. api and screens"), keep)

    def most(self) -> int:
        try:
            return int(self.query_one("#team-most", Input).value)
        except ValueError:
            return 0

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
        self.show_preview()

    def read(self) -> tuple[str, str, str, list[teams.Member]]:
        return (teams.clean(self.query_one("#team-name", Input).value),
                os.path.expanduser(self.query_one("#team-folder", Input).value.strip()),  # shown with ~
                teams.tidy(self.query_one("#team-goal", TextArea).text),  # its lines kept
                teams.parse_members(self.members_text(), lead_ok=True))

    def missing(self) -> list[str]:
        if self.toward is not None:  # joining a team at work, or telling it: no name, no line-up of its own
            text = self.members_text().strip()
            if self.toward.kind == teams.ADD_TO:
                return [] if teams.parse_members(text) else ["a member to add"]
            return [] if text else ["a message for the lead"]
        name, folder, goal, members = self.read()
        deciding, peers = self.deciding(), self.peers()
        return [problem for problem, wrong in (
            ("a name", not name),
            (f"another name: {name} exists already", name in self.taken),
            ("a project folder that exists", not folder or not Path(folder).is_dir()),
            ("the goal", not goal),
            ("a member besides the lead", not deciding and not peers and not any(not m.lead for m in members)),
            ("two members at least, as peers (or a lead line)", peers and len(members) < 2),
            ("for worktrees, the top folder of a git repository", self.worktrees and folder
             and Path(folder).is_dir() and not self.is_repository(folder)),
            # A fresh member would start in the old one's worktree, on its branch, with its work in it.
            (f"another name: an earlier team {name} left its worktrees in this project", self.worktrees and name
             and folder and any(Path(folder, *teams.WORKTREES, f"{name}-{m.name}").exists() for m in members)),
            ("how many members at most, 1 to 9", deciding and not 1 <= self.most() <= 9)) if wrong]

    def show_preview(self) -> None:
        """The cards as the boxes now say: the line-ups, where the members run, and on the right the
        team that will open, drawn, under a line on what opens and how (or what is still needed)."""
        if not self.is_mounted:
            return
        _, folder, _, _ = self.read()
        if self.toward is not None and not any(t.name == self.toward.team for t, _ in self.teams_here(folder)):
            self.leave_toward()  # another project now: that team is not at work in it
            return
        self.show_at_work(folder)
        if self.toward is not None and not self.asking:
            self.show_toward()
            return
        self.query_one("#team-title", Label).update(title("New team"))
        self.query_one("#lineup-card").border_title = "Line-up"
        self.query_one("#team-lineup").display = True
        self.query_one("#team-lineup", Static).update(self.lineup())
        self.query_one("#team-where", Static).update(self.where())
        deciding = self.deciding()
        self.query_one("#team-most-row").display = deciding
        # Deciding, the box holds the lead's line alone: one row, and what is left to say about it.
        self.query_one("#team-members", TextArea).set_class(deciding, "alone")
        self.show_help()
        name, folder, _, members = self.read()
        team = teams.new_team(name or "team", folder or ".", "", members, peers=self.peers(),
                              background=self.background)
        self.query_one(LineupGraph).show(team)  # drawn as it stands, whatever is still missing
        card, ready = self.query_one("#graph-card"), self.query_one("#team-ready", Label)
        preview = self.query_one("#team-preview", Static)
        if self.asking:
            card.border_title = "Asking Claude…"
            ready.update("")
            preview.update(Text("Asking Claude for a line-up for the goal: your plan, your default model, "
                                "about half a minute…", style=f"italic {MUTED}"))
            return
        missing = self.missing()
        if missing:
            card.border_title = "Will open"
            ready.update("")
            style = f"bold {AMBER}" if self.tried else MUTED
            preview.update(Text.assemble(("Still needed: ", style), (", ".join(missing), style)))
            return
        if team.peers:
            tabs = f"Opens {len(team.members)} tabs in {team.project}"
            peers = ". Nobody leads: each starts on its part, you talk to each, and they tell each other what touches theirs."
            head = Text.assemble((tabs, f"bold {TEXT}"), (peers, MUTED))
        elif deciding:
            tabs = f"Opens 1 tab in {team.project}"
            each = "each in the background" if self.background else "each in its own tab"
            head = Text.assemble((tabs, f"bold {TEXT}"),
                                 (f". It reads the goal and adds up to {self.most()} members itself, {each}.", MUTED))
        elif self.background:
            tabs = f"Starts {len(team.members) - 1} in the background in {team.project}"
            head = Text.assemble(((f"{tabs}, then opens a tab for {team.session_name(team.lead)}"), f"bold {TEXT}"),
                                 (", which hands out the work. B lists the background ones.", MUTED))
        else:
            tabs = f"Opens {len(team.members)} tabs in {team.project}"
            head = Text.assemble((tabs, f"bold {TEXT}"), (". The lead starts last and hands out the work.", MUTED))
        card.border_title = Text(tabs, style=f"bold {GREEN}")
        ready.update(Text.assemble(("● ", GREEN), ("ready", f"bold {GREEN}"), ("  Ctrl+S creates it", MUTED)))
        preview.update(head[len(tabs) + 2:])  # the card's title says what opens; this line, how

    # --- help with the roles ---------------------------------------------------------------

    def action_improve(self) -> None:
        """Ctrl+E: Claude rewrites the goal clearer; it goes into the box as one edit, so Ctrl+Z there
        puts yours back."""
        if self.improving is not None:
            return
        box = self.query_one("#team-goal", TextArea)
        if not improve.worth_improving(box.text):
            self.notify("Ctrl+E needs a goal to improve: write what the team is for first.", severity="warning",
                        timeout=5)
            return
        self.improving = box.text
        self.notify(f"Improving the goal with {improve.MODEL}…", timeout=4)
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
        if self.is_mounted and newsessions.put_improved(self.query_one("#team-goal", TextArea), text, better, why,
                                                        self.notify):
            self.notify("Goal improved: Ctrl+Z in the box puts yours back.", timeout=6)

    def action_suggest(self) -> None:
        """Ctrl+R: Claude reads the goal and the project's top level and suggests the team's size, and
        why, with a line-up: each member with its part, its own files, a model and an effort, a lead
        only when the parts need one. The line-up goes into the box; where the lead decides, the size
        goes into At most instead. One call on your plan; it is yours to change after."""
        if self.asking:
            return
        _, folder, goal, _ = self.read()
        if not goal or not folder or not Path(folder).is_dir():
            self.notify("Write the goal and the project folder first: the line-up is suggested for them.",
                        severity="warning", timeout=5)
            return
        here = self.teams_here(folder)
        prompt = teams.suggestion_prompt(goal, str(Path(folder).resolve()), working=here)
        if self.toward is not None:  # weighed afresh, from the line-up you had
            self.leave_toward()
        self.asking = True
        self.show_preview()
        self.run_worker(lambda: self.ask_for_lineup(prompt, [team for team, _ in here], goal), thread=True,
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
        if error or suggestion is None:
            self.notify(f"No line-up suggested: {error}", severity="warning", timeout=8)
            self.show_preview()
            return
        lines, count, lead = suggestion.lines, suggestion.count, suggestion.lead
        why = suggestion.why.rstrip(" .")
        why = f" — {why}" if why else ""  # "because ...", or in the language Claude answers in ("çünkü ...")
        box = self.query_one("#team-members", TextArea)
        if suggestion.kind == teams.ADD_TO:  # the goal adds to a team at work: members for it, its lead told
            self.toward, self.before_toward = suggestion, box.text
            box.text = lines
            self.notify(f"{suggestion.team} is at work here, and this goal adds to it: Claude suggests adding "
                        f"{count} member{'s' * (count != 1)}{why}. Ctrl+S adds them and tells {suggestion.to}; "
                        "Ctrl+N makes a new team instead.", timeout=15)
        elif suggestion.kind == teams.HAS_IT:  # a team at work has it: the goal, as a prompt for its lead
            self.toward, self.before_toward = suggestion, box.text
            box.text = suggestion.message
            self.notify(f"{suggestion.team} is at work on this goal already. Ctrl+S sends it to {suggestion.to}: "
                        "the message is on the left, yours to change. Ctrl+N makes a new team anyway.",
                        severity="warning", timeout=15)
        elif self.deciding():  # the lead picks the members: the size is what is asked for, its limit
            count = min(max(count, 1), teams.SUGGEST_MOST)  # the box takes one digit
            self.query_one("#team-most", Input).value = str(count)
            self.notify(f"Claude suggests at most {count} member{'s' if count > 1 else ''}{why}. The lead picks "
                        "them; for the members written out, Ctrl+T to another line-up, then Ctrl+R.", timeout=12)
        else:
            box.text = lines
            whom = f"{count} members and a lead" if lead else f"{count} members, as peers"
            self.notify(f"Claude suggests {whom}{why}. Change what you like; Ctrl+G on a line shows what that "
                        "member is told.", timeout=12)
        self.show_preview()

    def action_show_role(self) -> None:
        """Ctrl+G: what the member on the cursor's line is told when it starts: its role, word for
        word, and its first message."""
        box = self.query_one("#team-members", TextArea)
        lines = box.text.split("\n")
        row = box.cursor_location[0]
        found = teams.parse_members(lines[row] if row < len(lines) else "", lead_ok=True)
        if not found:
            self.notify("Put the cursor on a member's line in the box first.", timeout=4)
            return
        name, folder, goal, members = self.read()
        team = teams.new_team(name or "team", folder or ".", goal or "(the goal)", members,
                              decide=self.most() if self.deciding() else 0, peers=self.peers())
        member = next((m for m in team.members if m.name == found[0].name), None)
        if member is not None:
            self.app.push_screen(RoleDialog(team, member))

    def action_create(self) -> None:
        self.tried = True
        if self.missing():
            self.show_preview()
            return
        toward = self.toward
        if toward is not None and toward.kind == teams.ADD_TO:
            self.dismiss(JoinTeam(toward.team, self.members_text(), toward.to, toward.message, self.read()[2]))
            return
        if toward is not None:
            self.dismiss(TellTeam(toward.team, toward.to, " ".join(self.members_text().split())))
            return
        name, folder, goal, members = self.read()
        peers = self.peers()
        team = teams.new_team(name, str(Path(folder).resolve()), goal, members,
                              decide=self.most() if self.deciding() else 0,
                              background=self.background and not peers, peers=peers,
                              worktrees=self.worktrees, cap=self.query_one("#team-cap", Choice).value)
        advisor = self.advisor()
        if advisor:  # the lead's alone, where it can advise the lead's model
            team.members = [replace(m, advisor=advisor) if m.lead and picks.advisor_ok(m.model, advisor) else m
                            for m in team.members]
        self.dismiss(team)

    def action_cancel(self) -> None:
        self.dismiss(None)
