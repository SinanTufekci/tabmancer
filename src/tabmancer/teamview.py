"""The team view: the orchestrator's card on top, the members' cards under it on a tree of wires,
every message flying from one card to another as it is sent, and the conversation underneath as
arrows between the members' lanes. Enter on a team's row opens it from the dashboard (T lists the teams)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import background, context, focus, teams, teamtree
from .dialogs import frame, title
from .safework import work
from .sessions import Exit, Session, Status
from .statsview import copy_text
from .theme import AMBER, BLUE, CYAN, GREEN, MUTED, ORANGE, RED, TEXT, keys, mix
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
    out = {team.lead.name: accent, teams.YOU: TEXT, teams.TABMANCER: MUTED}
    others = [m for m in team.members if not m.lead]
    out.update({m.name: PALETTE[i % len(PALETTE)] for i, m in enumerate(others)})
    return out


def status_of(session: Session | None) -> tuple[str, str, bool]:
    """(label, style, spinning) for a member's card, in the dashboard's colours: blue works, red
    waits for you; a member done for now is idle, quiet."""
    if session is None:
        return "not started", MUTED, False
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
    along them; under the cards, what the picked member works on."""

    DEFAULT_CSS = "TeamGraph { height: auto; }"

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
        return [self.team.lead, *[m for m in self.team.members if not m.lead]]

    def selected_member(self) -> teams.Member | None:
        """The member picked with ← →, or None when everyone is."""
        index = self.selected % (len(self.order) + 1)
        return self.order[index] if index < len(self.order) else None

    # --- geometry ----------------------------------------------------------------------

    def layout_boxes(self, width: int) -> tuple[dict[str, tuple[int, int, int]], list[list[str]]]:
        """name -> (x, y, card width), and the members row by row: the lead's card on top in the
        middle, each row of members under a wire of its own."""
        members = [m.name for m in self.order[1:]]
        count = max(1, len(members))
        card = max(CARD_LEAST, min(CARD_MOST, (width - (count - 1) * GAP) // count))
        per_row = max(1, (width + GAP) // (card + GAP))
        rows = [members[i:i + per_row] for i in range(0, len(members), per_row)] or [[]]
        places = {self.team.lead.name: ((width - card) // 2, 0, card)}
        top = CARD_H + 1  # under a row's wire
        for row in rows:
            span = len(row) * card + (len(row) - 1) * GAP
            left = max(0, (width - span) // 2)
            for i, name in enumerate(row):
                places[name] = (left + i * (card + GAP), top, card)
            top += CARD_H + 1
        return places, rows

    def height_for(self, rows: list[list[str]]) -> int:
        return CARD_H + len(rows) * (CARD_H + 1) + 2  # the cards, their wires, and the focus line

    def path(self, sender: str, receiver: str, places: dict[str, tuple[int, int, int]]) -> list[tuple[int, int]]:
        """The cells a message passes on its way: out of the lead's card, down the trunk to a
        member's wire and into its card; or along a wire from one member to another."""
        lead = self.team.lead.name
        lead_x = places[lead][0] + places[lead][2] // 2

        def down_to(name: str) -> list[tuple[int, int]]:
            x, top, card = places[name]
            middle, wire = x + card // 2, top - 1
            cells = [(lead_x, y) for y in range(CARD_H - 1, wire + 1)]
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
        lead = self.team.lead.name
        lx, _, lcard = places[lead]
        lead_x = lx + lcard // 2
        chosen = self.selected_member()
        lead_picked = chosen is self.team.lead
        canvas.put(lead_x, CARD_H - 1, "┳" if lead_picked else "┬", f"bold {self.accent}" if lead_picked else WIRE)
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
        last = max(wires, default=CARD_H - 1)
        cards = [(cx, cy, cw) for name, (cx, cy, cw) in places.items() if name != lead]
        for y in range(CARD_H, last + 1):  # the trunk, down to the last row's wire, behind the cards
            if any(cx <= lead_x < cx + cw and cy <= y < cy + CARD_H for cx, cy, cw in cards):
                continue
            here = canvas.char_at(lead_x, y)
            alone = canvas.char_at(lead_x - 1, y) == " " and canvas.char_at(lead_x + 1, y) == " "
            if here == " " or alone:  # nothing across here, or one card right under the trunk
                canvas.put(lead_x, y, "│", WIRE)
            elif here in ("─", "┬"):
                canvas.put(lead_x, y, "┼" if y != last or here == "┬" else "┴", WIRE)
        self.draw_traffic(canvas, places, rows)

    def draw_traffic(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        """' 2▼ 1▲ ' on a member's wire beside its drop: the messages the lead sent down to it and
        the ones it sent back up. Left of the drop, or right of it where the wire ends there; only
        where the wire has room, a cell of it kept on either side."""
        lead = self.team.lead.name
        sent = teamtree.counts(self.said)
        for row in rows:
            for name in row:
                x, top, card = places[name]
                middle, wire = x + card // 2, top - 1
                tally = f" {sent.get((lead, name), 0)}▼ {sent.get((name, lead), 0)}▲ "
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
        for row in range(1, CARD_H - 1):
            canvas.put(x, y + row, corner[5] + " " * (card - 2) + corner[5], edge)
        canvas.put(x, y + CARD_H - 1, corner[2] + corner[4] * (card - 2) + corner[3], edge)
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
        right = " · ".join(part for part in (member.model, tokens) if part)
        left = 0
        if session is not None and session.context:
            share = context.share(session)
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
        Binding("h", "handover", "Hand over"),
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
                         ("d", "take off"), ("g", "goal"), ("h", "hand over"), ("x", "close"), ("Esc", "back")),
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
            ("\nGoal  ", MUTED), (self.team.goal, TEXT)))

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

    def action_handover(self) -> None:
        """Hand the picked member over to a fresh session with the same name and role."""
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
        from then on, and the lead is told (it started with the old line-up)."""
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
            if not goal or goal == self.team.goal:
                return
            team = teams.set_goal(self.team.name, goal)
            if team is None:
                return
            self.adopt(team)
            self.tell_lead(teams.goal_note(team), "The goal is changed.")

        self.app.push_screen(LineDialog("The team's goal", "e.g. Add Stripe checkout with tests",
                                        value=self.team.goal), chosen)

    def tell_lead(self, note: str, done: str) -> None:
        """A change made here, for the lead to hear: nothing can type into its tab, so the note goes
        to the clipboard and you to its tab, to paste it. A closed lead gets it in its role."""
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


LEAD_LINE = f"{teams.LEAD}: {teams.LEAD_FOCUS}"
MEMBERS_HELP = "one a line · name: what they work on · @sonnet at the end picks its model"
LEAD_HELP = "@opus (or any model) at the end picks the lead's model; it picks the members' models itself"


class NewTeamDialog(ModalScreen[teams.Team | None]):
    """A name, a folder, a goal and the line-up, one member a line. Below it, what will open,
    or what is still missing, as you type. Enter goes to the next box; Ctrl+S creates."""

    DEFAULT_CSS = frame("NewTeamDialog", "large") + """
    NewTeamDialog .row { height: auto; margin-top: 1; }
    NewTeamDialog .row > Label { width: 10; color: $text-muted; }
    NewTeamDialog .row > Vertical { width: 1fr; height: auto; }
    NewTeamDialog Input, NewTeamDialog TextArea { margin: 0; background: $panel; }
    NewTeamDialog Input:focus, NewTeamDialog TextArea:focus { background: $panel-lighten-2; }
    NewTeamDialog #team-goal { height: 2; }
    NewTeamDialog #team-members { height: 5; }
    NewTeamDialog #team-members.alone { height: 1; }
    NewTeamDialog #team-most { width: 3; }
    NewTeamDialog .aside { width: 1fr; color: $text-muted; }
    NewTeamDialog #team-preview { margin-top: 1; height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+t", "template", "Next line-up", priority=True),
        Binding("ctrl+l", "keep_lineup", "Keep the line-up", priority=True),
        Binding("ctrl+b", "toggle_background", "Tabs or background", priority=True),
        Binding("ctrl+s", "create", "Create", priority=True),
    ]

    def __init__(self, folder: str, taken: set[str], folders: list[str] | None = None) -> None:
        super().__init__()
        self.folder, self.taken = folder, taken
        self.folders = folders or []  # known project folders, newest first: the folder box completes them
        self.template = 0
        self.tried = False  # once Ctrl+S has been pressed, what is missing is said louder
        self.saved = teams.saved_lineups()  # line-ups of your own, kept with Ctrl+L
        self.lineups = [*teams.TEMPLATES, *self.saved, teams.DECIDE]
        self.forgetting: str | None = None  # Ctrl+L once on a kept line-up: again forgets it
        self.background = False  # Ctrl+B: the members run as background sessions, the lead in a tab

    def compose(self) -> ComposeResult:
        first = next(iter(teams.TEMPLATES))
        with Vertical():
            yield Label(title("New team", "every member is a session of its own; the lead hands out the work"))
            with Horizontal(classes="row"):
                yield Label("Name")
                yield Input(placeholder="e.g. checkout", id="team-name", compact=True)
            with Horizontal(classes="row"):
                yield Label("Project")
                with Vertical():
                    yield Input(value=self.folder, id="team-folder", compact=True,
                                suggester=SuggestFromList(self.folders, case_sensitive=False))
                    yield Label(Text("→ completes a folder you have worked in", style=MUTED))
            with Horizontal(classes="row"):
                yield Label("Goal")
                yield TextArea(id="team-goal", compact=True, placeholder="e.g. Add Stripe checkout with tests")
            with Horizontal(classes="row"):
                yield Label("Line-up")
                with Vertical():
                    yield Static(self.lineup(), id="team-lineup")
                    yield Static(self.where(), id="team-where")  # tabs or the background: Ctrl+B
            with Horizontal(classes="row"):
                yield Label("Members")
                with Vertical():
                    yield TextArea(f"{LEAD_LINE}\n{teams.TEMPLATES[first]}", id="team-members", compact=True)
                    yield Static(MEMBERS_HELP, id="team-members-help", classes="aside")
            with Horizontal(classes="row", id="team-most-row"):  # only when the lead decides
                yield Label("At most")
                yield Input(str(teams.DECIDE_MOST), id="team-most", compact=True, restrict=r"[0-9]*", max_length=1)
                yield Static("  members: the lead reads the goal and picks them, fewer when the work is small",
                             classes="aside")
            yield Static("", id="team-preview")
            # One line, even in a 120x30 window: a second one would be cut off there.
            yield Label(keys(("Ctrl+S", "create"), ("Enter/Tab", "next box"), ("Ctrl+T", "next line-up"),
                             ("Ctrl+L", "keep it"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        self.show_preview()

    def lineup(self) -> Text:
        """The ready-made line-ups, the one in the box marked; "your own" once it has been edited."""
        own = self.is_mounted and self.members_text() != self.template_text(self.lineups[self.template])
        out = Text()
        for index, name in enumerate([*self.lineups, "your own"]):
            on = (name == "your own") if own else (index == self.template)
            out.append(("● " if on else "○ ") + name, style=f"bold {TEXT}" if on else MUTED)
            out.append("   ")
        return out

    def where(self) -> Text:
        """Where the members run: each in a tab, or in the background with only the lead in a tab."""
        out = Text()
        for on, words in ((not self.background, "each in a tab"),
                          (self.background, "the members in the background, the lead in a tab")):
            out.append(("● " if on else "○ ") + words, style=f"bold {TEXT}" if on else MUTED)
            out.append("   ")
        out.append("Ctrl+B switches", style=MUTED)
        return out

    def action_toggle_background(self) -> None:
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
        return f"{lead}\n{teams.TEMPLATES.get(name) or self.saved.get(name, '')}"

    def deciding(self) -> bool:
        """The lead picks the members: that line-up chosen, and no member written in."""
        return (self.lineups[self.template] == teams.DECIDE
                and not any(not m.lead for m in teams.parse_members(self.members_text(), lead_ok=True)))

    def action_template(self) -> None:
        self.template = (self.template + 1) % len(self.lineups)
        self.query_one("#team-members", TextArea).text = self.template_text(self.lineups[self.template])
        self.show_preview()

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

        def keep(name: str | None) -> None:
            name = " ".join((name or "").split())[:30]
            if not name:
                return
            if name in teams.TEMPLATES or name in (teams.DECIDE, "your own"):
                self.notify(f"{name} is a ready-made line-up: give yours another name.", severity="warning")
                return
            teams.keep_lineup(name, "\n".join(members))
            self.saved[name] = "\n".join(members)
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
        self.focus_next()  # Enter goes on to the next box; Ctrl+S creates

    def on_input_changed(self, event: Input.Changed) -> None:
        self.show_preview()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.show_preview()

    def read(self) -> tuple[str, str, str, list[teams.Member]]:
        return (teams.clean(self.query_one("#team-name", Input).value),
                self.query_one("#team-folder", Input).value.strip(),
                " ".join(self.query_one("#team-goal", TextArea).text.split()),
                teams.parse_members(self.members_text(), lead_ok=True))

    def missing(self) -> list[str]:
        name, folder, goal, members = self.read()
        deciding = self.deciding()
        return [problem for problem, wrong in (
            ("a name", not name),
            (f"another name: {name} exists already", name in self.taken),
            ("a project folder that exists", not folder or not Path(folder).is_dir()),
            ("the goal", not goal),
            ("a member besides the lead", not deciding and not any(not m.lead for m in members)),
            ("how many members at most, 1 to 9", deciding and not 1 <= self.most() <= 9)) if wrong]

    def show_preview(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#team-lineup", Static).update(self.lineup())
        self.query_one("#team-where", Static).update(self.where())
        deciding = self.deciding()
        self.query_one("#team-most-row").display = deciding
        # Deciding, the box holds the lead's line alone: one row, and what is left to say about it.
        self.query_one("#team-members", TextArea).set_class(deciding, "alone")
        self.query_one("#team-members-help", Static).update(LEAD_HELP if deciding else MEMBERS_HELP)
        preview = self.query_one("#team-preview", Static)
        missing = self.missing()
        if missing:
            style = f"bold {AMBER}" if self.tried else MUTED
            preview.update(Text.assemble(("Still needed: ", style), (", ".join(missing), style)))
            return
        name, folder, _, members = self.read()
        team = teams.new_team(name, folder, "", members)
        tabs = ", ".join(team.session_name(m) + (f" ({m.model})" if m.model else "") for m in team.members)
        each = "each in the background" if self.background else "each in its own tab"
        if deciding:
            preview.update(Text.assemble((f"Opens 1 tab in {team.project}: ", MUTED), (tabs, TEXT),
                                         ((f". It reads the goal and adds up to {self.most()} members itself, {each}; "
                                           "you see them join in the team view."), MUTED)))
            return
        if self.background:
            members = ", ".join(team.session_name(m) for m in team.members if not m.lead)
            preview.update(Text.assemble((f"Starts {len(team.members) - 1} in the background in {team.project}: ",
                                          MUTED), (members, TEXT), (", then opens a tab for ", MUTED),
                                         (team.session_name(team.lead), TEXT),
                                         (", which hands out the work. B lists the background ones.", MUTED)))
            return
        preview.update(Text.assemble((f"Opens {len(team.members)} tabs in {team.project}: ", MUTED), (tabs, TEXT),
                                     (". The lead starts last and hands out the work.", MUTED)))

    def action_create(self) -> None:
        self.tried = True
        if self.missing():
            self.show_preview()
            return
        name, folder, goal, members = self.read()
        self.dismiss(teams.new_team(name, str(Path(folder).resolve()), goal, members,
                                    decide=self.most() if self.deciding() else 0, background=self.background))

    def action_cancel(self) -> None:
        self.dismiss(None)
