"""Teams in the dashboard's list: a team's heading in OPEN NOW with its members under it as a tree,
and the team's preview, which shows who talks to whom. Each member hangs on a wire from the lead,
with the messages sent along it both ways and the latest one beside it; a message flies along the
wires as it is sent. T opens the full team view."""

from __future__ import annotations

from datetime import date

from rich.text import Text

from . import rowwords, teams, teamwords
from .rowwords import last_word  # noqa: F401  (named here too)
from .teams import TeamState, counts
from .teamwords import gist, one_line
from .theme import MUTED, TEXT, mix
from .viewmodel import FLIGHT_SECONDS, OTHER, lanes_of  # noqa: F401  (named here too)

TRAIL = 6  # cells of fading wire behind a message in flight
TIME = 7  # "15:04  " before the lanes of the sequence diagram
LANES_SHARE = 0.45  # the lanes take at most this much of the width; what was said gets the rest
LANE_MOST, LANE_LEAST = 14, 7  # a lane's width
LANE_LINE = "#333333"


def word(message: teams.Message, member: str, colours: dict[str, str]) -> Text:
    """'→ dash: Heads-up…' when this member said it, '← lead: …' when it was said to it."""
    return Text.assemble(*teamwords.message_parts(message, member, colours))


def wire(down: int, up: int) -> str:
    """The wire from the lead to a member: '──3▶──2◀──', sent down to it and back up."""
    return f"──{down}▶──{up}◀──"


def flight_spot(message: teams.Message, progress: float, lead: str,
                wires: dict[str, str]) -> tuple[str, int, bool] | None:
    """Where on the wires a message is: (member whose wire, index in it, going out to the member).
    From the lead it runs out along the receiver's wire, to the lead back along the sender's, and
    from member to member back along one wire and out along the other."""

    def along(name: str, share: float, outward: bool) -> tuple[str, int, bool] | None:
        line = wires.get(name)
        if line is None:
            return None
        dashes = [i for i, char in enumerate(line) if char == "─"]
        step = min(len(dashes) - 1, int(share * len(dashes)))
        return name, dashes[step if outward else len(dashes) - 1 - step], outward

    if message.sender == lead:
        return along(message.receiver, progress, True)
    if message.receiver == lead:
        return along(message.sender, progress, False)
    if progress < 0.5:
        return along(message.sender, progress * 2, False)
    return along(message.receiver, progress * 2 - 1, True)


def tree(state: TeamState, width: int, looks: dict[str, tuple[str, str]], colours: dict[str, str],
         flights: list[tuple[teams.Message, float]] = (), now: float = 0.0) -> list[Text]:
    """The lead on top and every member on its wire under it, with what it is doing and the latest
    word on that wire. A team of peers has no lead line: each member's wire counts what the others
    sent it and what it sent them. looks: member -> (label, style) as the list shows its state."""
    team = state.team
    lead = team.lead.name if team.lead is not None else ""
    members = [m for m in team.members if not m.lead]
    sent = counts(state.said)
    if lead:
        wires = {m.name: wire(sent.get((lead, m.name), 0), sent.get((m.name, lead), 0)) for m in members}
    else:
        names_in = {m.name for m in members}
        wires = {m.name: wire(sum(n for (a, b), n in sent.items() if b == m.name and a in names_in),
                              sum(n for (a, b), n in sent.items() if a == m.name and b in names_in)) for m in members}
    reach = max((len(w) for w in wires.values()), default=0)
    wires = {name: line.ljust(reach, "─") for name, line in wires.items()}
    names = max(len(m.name) for m in team.members)
    labels = max(len(looks.get(m.name, ("", ""))[0]) for m in team.members)
    dots: dict[str, dict[int, str]] = {}  # member -> wire index -> colour: the messages in flight
    trails: dict[str, dict[int, str]] = {}  # ... and the wire lit behind them, fading
    for message, start in flights:
        progress = (now - start) / FLIGHT_SECONDS
        spot = flight_spot(message, progress, lead, wires) if 0 <= progress < 1 else None
        if spot is None:
            continue
        name, index, outward = spot
        colour = colours.get(message.sender, TEXT)
        dots.setdefault(name, {})[index] = colour
        for back in range(1, TRAIL + 1):
            trails.setdefault(name, {})[index - back if outward else index + back] = \
                mix(MUTED, colour, 0.8 * (1 - back / (TRAIL + 1)))

    def state_part(name: str) -> Text:
        label, style = looks.get(name, ("not started", MUTED))
        return Text(label.ljust(labels), style=style)

    lines = [Text.assemble(("★ ", f"bold {colours.get(lead, TEXT)}"),  # its state lines up with the members'
                           (lead.ljust(reach + names), f"bold {colours.get(lead, TEXT)}"), "  ", state_part(lead))
             ] if lead else []
    for index, member in enumerate(members):
        line = Text("└" if index == len(members) - 1 else "├", style=MUTED)
        dot, trail = dots.get(member.name, {}), trails.get(member.name, {})
        for at, char in enumerate(wires[member.name]):
            if at in dot:
                line.append("●", style=f"bold {dot[at]}")
            elif char.isdigit():
                line.append(char, style=TEXT if char != "0" else MUTED)
            else:
                line.append(char, style=trail.get(at, MUTED))
        line.append(" ")
        line.append(member.name.ljust(names), style=f"bold {colours.get(member.name, TEXT)}")
        line.append("  ")
        line.append_text(state_part(member.name))
        if lead:
            latest = next((m for m in reversed(state.said) if {m.sender, m.receiver} == {lead, member.name}), None)
        else:  # a peer's latest word with any of the others
            latest = next((m for m in reversed(state.between_members()) if member.name in (m.sender, m.receiver)),
                          None)
        if latest is not None:
            line.append("  ")
            line.append_text(word(latest, member.name, colours))
        line.truncate(width, overflow="ellipsis")
        lines.append(line)
    pairs = counts(state.between_members())
    across = [(a, b, n) for (a, b), n in pairs.items() if lead not in (a, b)]
    if across:
        line = Text("  member to member: ", style=MUTED)
        for i, (a, b, n) in enumerate(sorted(across)):
            line.append(" · " if i else "", style=MUTED)
            line.append_text(Text.assemble((a, colours.get(a, TEXT)), (" → ", MUTED), (b, colours.get(b, TEXT)),
                                           (f" {n}", TEXT)))
        line.truncate(width, overflow="ellipsis")
        lines.append(line)
    return lines


def chips(members: list[tuple[str, bool, str, str, str, int]], width: int, lead_style: str) -> Text:
    """A team's members on its one row (rowwords.chips), as one Text."""
    return Text.assemble(*rowwords.chips(members, width, lead_style))


def sequence(team: teams.Team, said: list[teams.Message], colours: dict[str, str], width: int,
             picked: str | None = None) -> tuple[Text, list[Text]]:
    """The conversation as a sequence diagram: a lane for each member, a row for each message with an
    arrow from its sender's lane to its receiver's, and what it said beside the lanes; a day's first
    message under a line naming the day, when not today. A message on its way is a dashed arrow from
    a hollow dot. With a member picked, its lane and its arrows stay lit and the rest go quiet.
    Returns the lane names' line and the rows, oldest first."""
    lanes = lanes_of(team, said)
    step = max(LANE_LEAST, min(LANE_MOST, int(width * LANES_SHARE) // max(1, len(lanes))))
    xs = [TIME + step // 2 + i * step for i in range(len(lanes))]
    end = TIME + step * len(lanes)

    def lane(name: str) -> int:
        if name in lanes:
            return xs[lanes.index(name)]
        return xs[0] if name == teams.TABMANCER else xs[-1]

    head = Text(" " * TIME)
    for name, x in zip(lanes, xs):
        label = name if len(name) < step else name[: step - 2] + "…"
        head.append(" " * max(0, x - len(label) // 2 - head.cell_len))
        head.append(label, style=f"bold {colours.get(name, TEXT)}" if picked in (None, name) else MUTED)
    rows: list[Text] = []
    day = date.today()
    for message in said:
        if message.at.date() != day:
            day = message.at.date()
            rows.append(Text(f"{'':{TIME}}── {message.at:%a %d %b} ──", style=MUTED))
        lit = picked is None or picked in (message.sender, message.receiver)
        colour = colours.get(message.sender, TEXT) if lit else MUTED
        if not message.delivered:
            colour = mix(MUTED, colour, 0.5)
        cells = [(" ", "")] * end
        for name, x in zip(lanes, xs):
            cells[x] = ("│", mix(LANE_LINE, colours.get(name, TEXT), 0.6) if name == picked else LANE_LINE)
        a, b = lane(message.sender), lane(message.receiver)
        if a != b:
            way = 1 if b > a else -1
            for x in range(a + way, b, way):
                cells[x] = ("─" if message.delivered else "╌", colour)
            cells[a] = ("●" if message.delivered else "○", f"bold {colour}")
            cells[b] = ("▶" if b > a else "◀", f"bold {colour}")
        row = Text(f"{message.at:%H:%M}  ", style=MUTED)  # the time fills the cells before the lanes
        for char, style in cells[TIME:]:
            row.append(char, style=style)
        words = Text(" " + gist(message, message.receiver), style=TEXT if lit and message.delivered else MUTED)
        if not message.delivered:
            words.append("  on its way", style=f"italic {MUTED}")
        words.truncate(max(0, width - end), overflow="ellipsis")
        row.append_text(words)
        rows.append(row)
    return head, rows


def head(state: TeamState, tokens: int, accent: str) -> Text:
    """'TEAM tabmancer · 3 of 3 open · 12 messages · 14.9M tokens' (teamwords.head)."""
    return Text.assemble(*teamwords.head(state, tokens, accent))


def latest(state: TeamState, colours: dict[str, str], lines: int, width: int) -> Text:
    """The newest messages first, two lines each: who to whom, then what."""
    out = Text()
    for message in list(reversed(state.said))[: max(1, lines // 2)]:
        if out:
            out.append("\n")
        out.append(f"{message.at:%H:%M} ", style=MUTED)
        out.append(message.sender, style=f"bold {colours.get(message.sender, TEXT)}")
        out.append(" → ", style=MUTED)
        out.append(message.receiver, style=f"bold {colours.get(message.receiver, TEXT)}")
        body = Text("\n  " + one_line(message.summary or message.text), style=TEXT if message.delivered else MUTED)
        body.truncate(width + 1, overflow="ellipsis")
        out.append_text(body)
    if not out:
        out = Text("Nothing said yet: messages between the members show up here.", style=MUTED)
    return out
