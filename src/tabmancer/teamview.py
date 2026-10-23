"""The team view: the orchestrator on top, the members below it, every message flying from one
box to another as it is sent, and the conversation underneath. T opens it from the dashboard."""

from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
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
from .sessions import Exit, Session, Status
from .statsview import copy_text
from .theme import AMBER, BLUE, CYAN, GREEN, MUTED, ORANGE, RED, TEXT, keys, mix
from .tokens import human

PALETTE = [CYAN, GREEN, AMBER, ORANGE, "#c678dd", "#61afef", "#e5c07b", "#56b6c2"]
FLIGHT_SECONDS = 1.8  # how long a message takes to fly from box to box
BOX_H = 6  # a box's rows: its focus gets one line...
TALL_FROM = 36  # ...and two, with one row more, from this many rows of terminal
BOX_MOST = 52  # boxes grow with the room up to this width
GAP = 3
WIRE = "#3a3a3a"
STAR = "·✢✶✻✽✻✶✢"
LOG_LINES = 200


def colours(team: teams.Team, accent: str) -> dict[str, str]:
    out = {team.lead.name: accent, teams.YOU: TEXT, teams.TABMANCER: MUTED}
    others = [m for m in team.members if not m.lead]
    out.update({m.name: PALETTE[i % len(PALETTE)] for i, m in enumerate(others)})
    return out


def status_of(session: Session | None) -> tuple[str, str, bool]:
    """(label, style, spinning) for a member's box, in the dashboard's colours: blue works, red
    waits for you."""
    if session is None:
        return "not started", MUTED, False
    if session.exit is not Exit.RUNNING:
        return "closed", MUTED, False
    state = session.live_state.lower()
    if state in ("busy", "working"):
        return "working", BLUE, True
    if state in ("blocked", "waiting"):
        return "needs you", f"bold {RED}", False
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
    """The boxes, the wires between them and the messages in flight."""

    DEFAULT_CSS = "TeamGraph { height: auto; }"

    def __init__(self, team: teams.Team, accent: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.team, self.accent = team, accent
        self.palette = colours(team, accent)
        self.sessions: dict[str, Session | None] = {}
        self.said: list[teams.Message] = []
        self.unread: dict[str, int] = {}
        self.flights: list[Flight] = []
        self.selected = 0  # an index into order; one past its end is everyone (no box picked)
        self.ticker: teams.Message | None = None

    @property
    def order(self) -> list[teams.Member]:
        return [self.team.lead, *[m for m in self.team.members if not m.lead]]

    def selected_member(self) -> teams.Member | None:
        """The member picked with ← →, or None when everyone is."""
        index = self.selected % (len(self.order) + 1)
        return self.order[index] if index < len(self.order) else None

    @property
    def box_h(self) -> int:
        # Not mounted: a test drawing the boxes alone, with no app to ask.
        tall = self.is_mounted and self.app.size.height >= TALL_FROM
        return BOX_H + 1 if tall else BOX_H

    # --- geometry ----------------------------------------------------------------------

    def layout_boxes(self, width: int) -> tuple[dict[str, tuple[int, int, int]], list[list[str]]]:
        """name -> (x, y, box width), and the members row by row. Boxes grow with the room."""
        members = [m.name for m in self.order[1:]]
        count = max(1, len(members))
        box = max(16, min(BOX_MOST, (width - (count - 1) * GAP) // count))
        per_row = max(1, (width + GAP) // (box + GAP))
        rows = [members[i:i + per_row] for i in range(0, len(members), per_row)] or [[]]
        places = {self.team.lead.name: ((width - box) // 2, 0, box)}
        top = self.box_h + 3
        for row in rows:
            span = len(row) * box + (len(row) - 1) * GAP
            left = max(0, (width - span) // 2)
            for i, name in enumerate(row):
                places[name] = (left + i * (box + GAP), top, box)
            top += self.box_h + 3
        return places, rows

    def height_for(self, rows: list[list[str]]) -> int:
        return self.box_h + len(rows) * (self.box_h + 3) + 2  # boxes, wires, and the ticker line

    def path(self, sender: str, receiver: str, places: dict[str, tuple[int, int, int]]) -> list[tuple[int, int]]:
        """The cells a message passes on its way: down the trunk from the orchestrator, along a bus
        to a member, or from one member's bus to another's."""
        lead = self.team.lead.name
        lead_x = places[lead][0] + places[lead][2] // 2

        def to_trunk(name: str) -> list[tuple[int, int]]:
            """From a member's box up to the trunk under the orchestrator."""
            x, top, box = places[name]
            middle, bus = x + box // 2, top - 2
            cells = [(middle, top - 1), (middle, bus)]
            step = 1 if lead_x > middle else -1
            cells += [(c, bus) for c in range(middle + step, lead_x + step, step)] if middle != lead_x else []
            return cells

        def trunk(from_y: int, to_y: int) -> list[tuple[int, int]]:
            step = 1 if to_y > from_y else -1
            return [(lead_x, y) for y in range(from_y, to_y + step, step)]

        if sender == lead and receiver in places:
            back = to_trunk(receiver)[::-1]
            return trunk(self.box_h, back[0][1] - 1) + back
        if receiver == lead and sender in places:
            up = to_trunk(sender)
            return up + trunk(up[-1][1] - 1, self.box_h)
        if sender in places and receiver in places:
            one, two = to_trunk(sender), to_trunk(receiver)[::-1]
            if one[-1][1] == two[0][1]:  # same row: straight along the bus
                bus = one[-1][1]
                a, b = places[sender][0] + places[sender][2] // 2, places[receiver][0] + places[receiver][2] // 2
                step = 1 if b > a else -1
                return ([(a, places[sender][1] - 1)] + [(c, bus) for c in range(a, b + step, step)]
                        + [(b, places[receiver][1] - 1)])
            return one + trunk(one[-1][1], two[0][1]) + two
        return []

    # --- drawing ---------------------------------------------------------------------------

    def render(self) -> Text:
        width = max(40, self.size.width or 100)
        places, rows = self.layout_boxes(width)
        canvas = Canvas(width, self.height_for(rows))
        self.draw_wires(canvas, places, rows)
        for member in self.order:
            self.draw_box(canvas, member, places[member.name])
        self.draw_flights(canvas, places)
        return canvas.text()

    def draw_wires(self, canvas: Canvas, places: dict, rows: list[list[str]]) -> None:
        lead = self.team.lead.name
        lead_x = places[lead][0] + places[lead][2] // 2
        buses = []
        for row in rows:
            if not row:
                continue
            top = places[row[0]][1]
            bus = top - 2
            middles = [places[n][0] + places[n][2] // 2 for n in row]
            left, right = min([*middles, lead_x]), max([*middles, lead_x])
            canvas.put(left, bus, "─" * (right - left + 1), WIRE)
            for middle in middles:
                canvas.put(middle, bus, "┬" if middle != lead_x else "┼", WIRE)
                canvas.put(middle, top - 1, "│", WIRE)
            canvas.put(left, bus, "╭" if left in middles and left != lead_x else canvas.char_at(left, bus), WIRE)
            canvas.put(right, bus, "╮" if right in middles and right != lead_x else canvas.char_at(right, bus), WIRE)
            buses.append(bus)
        if buses:
            for y in range(self.box_h, max(buses)):
                if canvas.char_at(lead_x, y) == " ":
                    canvas.put(lead_x, y, "│", WIRE)
            for bus in buses:
                if canvas.char_at(lead_x, bus) == "─":
                    canvas.put(lead_x, bus, "┴" if bus == max(buses) else "┼", WIRE)
        self.draw_traffic(canvas, places, rows, lead_x)

    def draw_traffic(self, canvas: Canvas, places: dict, rows: list[list[str]], lead_x: int) -> None:
        """Beside the wire down to each member: the messages sent along it, down to it and back up
        ('3▼ 2▲'), on its left, and the latest of them on its right."""
        lead = self.team.lead.name
        sent = teamtree.counts(self.said)
        for row in rows:
            for name in row:
                x, top, box = places[name]
                middle, y = x + box // 2, top - 1
                # The trunk runs on down past a row that is not the last: the words stop short of it.
                trunk = lead_x if row is not rows[-1] else -1
                tally = f"{sent.get((lead, name), 0)}▼ {sent.get((name, lead), 0)}▲"
                start = max(x, middle - 1 - len(tally))
                if not start <= trunk < middle:
                    canvas.put(start, y, tally[: middle - 1 - start], MUTED)
                latest = next((m for m in reversed(self.said) if {m.sender, m.receiver} == {lead, name}), None)
                end = trunk if middle + 2 <= trunk < x + box else x + box
                if latest is not None and end - (middle + 2) >= 4:
                    words = teamtree.gist(latest, name)
                    room = end - (middle + 2)
                    canvas.put(middle + 2, y, words if len(words) <= room else words[: room - 1] + "…", TEXT)

    def draw_box(self, canvas: Canvas, member: teams.Member, place: tuple[int, int, int]) -> None:
        x, y, box = place
        height = self.box_h
        colour = self.palette.get(member.name, TEXT)
        chosen = member is self.selected_member()
        edge = f"bold {colour}" if chosen else mix("#000000", colour, 0.55)
        corner = ("┏", "┓", "┗", "┛", "━", "┃") if chosen else ("╭", "╮", "╰", "╯", "─", "│")
        canvas.put(x, y, corner[0] + corner[4] * (box - 2) + corner[1], edge)
        for row in range(1, height - 1):
            canvas.put(x, y + row, corner[5] + " " * (box - 2) + corner[5], edge)
        canvas.put(x, y + height - 1, corner[2] + corner[4] * (box - 2) + corner[3], edge)
        inner = box - 4
        session = self.sessions.get(member.name)
        badge = "★ " if member.lead else "● "
        canvas.put(x + 2, y + 1, (badge + member.name)[:inner], f"bold {colour}")
        if member.model and len(badge + member.name) + 2 + len(member.model) <= inner:
            canvas.put(x + box - 2 - len(member.model), y + 1, member.model, MUTED)  # its own model, top right
        # Its focus, on as many lines as there is room for (the last one cut short if need be).
        room = height - 5  # the rows between the name and the status
        lines = textwrap.wrap(member.focus or "", inner) or [""]
        if len(lines) > room:
            rest = " ".join(lines[room - 1:])
            lines = [*lines[:room - 1], rest if len(rest) <= inner else rest[: inner - 1] + "…"]
        for row, line in enumerate(lines[:room]):
            canvas.put(x + 2, y + 2 + row, line, MUTED)
        status_y, ctx_y = y + height - 3, y + height - 2
        label, style, spinning = status_of(session)
        if spinning:
            label = f"{STAR[int(time.monotonic() * 8) % len(STAR)]} {label}"
        unread = self.unread.get(member.name, 0)
        if unread:
            label += f" · {unread} new"
        canvas.put(x + 2, status_y, label[:inner], style)
        use = getattr(self.app, "session_tokens", {}).get(session.session_id) if session is not None else None
        if use is not None and use.total and len(label) + 2 + len(spent := human(use.total)) <= inner:
            canvas.put(x + box - 2 - len(spent), status_y, spent, MUTED)  # its tokens, at the right
        if session is not None and session.context:
            share = context.share(session)
            cells = max(1, inner - 10)
            filled = round(share * cells)
            bar_style = GREEN if share < 0.6 else AMBER if share < 0.85 else RED
            canvas.put(x + 2, ctx_y, "ctx ", MUTED)
            canvas.put(x + 6, ctx_y, "▰" * filled, bar_style)
            canvas.put(x + 6 + filled, ctx_y, "▱" * (cells - filled), "#2a2a2a")
            canvas.put(x + 7 + cells, ctx_y, f"{share:.0%}", bar_style)

    def draw_flights(self, canvas: Canvas, places: dict) -> None:
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
        if self.ticker is not None:  # the latest message, spelled out under the boxes
            message, x, line = self.ticker, 2, canvas.height - 1
            for part, style in ((message.sender, f"bold {self.palette.get(message.sender, TEXT)}"), (" → ", MUTED),
                                (message.receiver, f"bold {self.palette.get(message.receiver, TEXT)}"),
                                ("  " + _one_line(message.summary or message.text), TEXT)):
                canvas.put(x, line, part[: max(0, canvas.width - x - 1)], style)
                x += len(part)

    def launch_flight(self, message: teams.Message) -> None:
        places, _ = self.layout_boxes(max(40, self.size.width or 100))
        path = self.path(message.sender, message.receiver, places)
        if path:
            self.flights.append(Flight(message, path, time.monotonic()))
        self.ticker = message


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
    TeamScreen VerticalScroll { height: 1fr; margin: 0 2; }
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
        yield Label(keys(("← →", "pick a member"), ("Enter", "go to its tab or open it"),
                         ("s", "start every member not open"), ("a", "add a member"), ("d", "take one off"),
                         ("g", "the goal"), ("h", "hand over"), ("x", "close the team"), ("Esc", "back")),
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
        self.query_one("#team-head", Static).update(Text.assemble(
            ("TEAM ", f"bold {MUTED}"), (self.team.name, f"bold {self.accent}"),
            (f"  ·  {self.team.cwd}  ·  {open_now} of {len(self.team.members)} open", MUTED),
            (f"  ·  the lead picks up to {self.team.decide} members" if self.team.decide else "", MUTED),
            ("  ·  members in the background" if self.team.background else "", MUTED),
            ("\nGoal: ", MUTED), (self.team.goal, TEXT), self.spent()))

    def spent(self) -> Text:
        """'Tokens 12.3M · lead 3.1M · api 5.2M …': what each member's session has used, as the
        dashboard counts it (nothing until it has)."""
        counted = getattr(self.app, "session_tokens", {})
        used = []
        for member in self.team.members:
            session = self.sessions.get(member.name)
            use = counted.get(session.session_id) if session is not None else None
            if use is not None and use.total:
                used.append((member.name, use.total))
        if not used:
            return Text("")
        each = " · ".join(f"{name} {human(total)}" for name, total in used)
        return Text.assemble(("\nTokens ", MUTED), (human(sum(total for _, total in used)), TEXT), (f"  ·  {each}", MUTED))

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
            if said:
                self.graph.ticker = said[-1]
        else:
            members = {m.name for m in self.team.members}
            for message in said:
                key = (message.sender, message.receiver, message.text)
                if key in self.seen:
                    continue
                self.seen.add(key)
                if message.sender in members and message.receiver in members:
                    self.graph.launch_flight(message)
                else:
                    self.graph.ticker = message
        self.show_head()
        self.show_log()

    def show_log(self) -> None:
        """The conversation as a sequence diagram (teamtree.sequence): a lane a member, an arrow a
        message. ← → light one member's lane and arrows."""
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
                       "arrow from one lane to another, and flies across the boxes above.", style=MUTED)
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

    def __init__(self, saved: dict[str, teams.Team]) -> None:
        super().__init__()
        self.saved = saved
        self.armed: str | None = None

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
