"""Teams in the dashboard's list: a team's heading in OPEN NOW with its members under it as a tree,
and the team's preview, which shows who talks to whom. Each member hangs on a wire from the lead,
with the messages sent along it both ways and the latest one beside it; a message flies along the
wires as it is sent. T opens the full team view."""

from __future__ import annotations

from datetime import date

from rich.text import Text

from . import teams
from .teams import TeamState, counts
from .theme import MUTED, RED, TEXT, mix
from .tokens import human

FLIGHT_SECONDS = 1.8  # as in the team view
TRAIL = 6  # cells of fading wire behind a message in flight
CHIP_GAP = "  "
TIME = 7  # "15:04  " before the lanes of the sequence diagram
LANES_SHARE = 0.45  # the lanes take at most this much of the width; what was said gets the rest
LANE_MOST, LANE_LEAST = 14, 7  # a lane's width
LANE_LINE = "#333333"
OTHER = "other"  # the lane of sessions outside the team that wrote to it
SHORTEST = 4  # a name on a team's row is never cut shorter; past that, "+2" stands for the last ones


def one_line(text: str) -> str:
    return " ".join(text.split())


def last_word(state: TeamState, member: str) -> teams.Message | None:
    """The latest message to or from this member."""
    return next((m for m in reversed(state.said) if member in (m.sender, m.receiver)), None)


def gist(message: teams.Message, other: str = "") -> str:
    """What a message says, on one line: its summary when it has one. A summary that starts with
    the other side's name ("cad: start once the memory is free") loses it where that name shows."""
    text = one_line(message.summary or message.text)
    if other and text.casefold().startswith(f"{other.casefold()}:"):
        text = text[len(other) + 1:].lstrip()
    return text


def word(message: teams.Message, member: str, colours: dict[str, str]) -> Text:
    """'→ dash: Heads-up…' when this member said it, '← lead: …' when it was said to it."""
    out = message.sender == member
    other = message.receiver if out else message.sender
    return Text.assemble(("→ " if out else "← ", MUTED), (other, f"bold {colours.get(other, TEXT)}"), (": ", MUTED),
                         (gist(message, other), TEXT))


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
    word on that wire. looks: member -> (label, style) as the list shows its state."""
    team = state.team
    lead = team.lead.name
    members = [m for m in team.members if not m.lead]
    sent = counts(state.said)
    wires = {m.name: wire(sent.get((lead, m.name), 0), sent.get((m.name, lead), 0)) for m in members}
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
                           (lead.ljust(reach + names), f"bold {colours.get(lead, TEXT)}"), "  ", state_part(lead))]
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
        latest = next((m for m in reversed(state.said) if {m.sender, m.receiver} == {lead, member.name}), None)
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
    """A team's members on its one row, each a sign of what it is doing and its name, as the status
    column puts them: '● ★lead  ✶ api  ◦ ui' (stuck to the name after it, 'api✶  ui◦' read as
    'api', then '✶ ui'). members: (name, lead, sign, sign's style, name's style, rank: 0 needs you,
    1 waits for you, then the quieter ones). Long names are cut until the line fits; when even that
    is not enough, the quietest are counted instead ('+2'), so the ones waiting for you stay named."""

    def drawn(most: int, keep: int) -> Text:
        kept = sorted(range(len(members)), key=lambda i: (not members[i][1], members[i][5], i))[:keep]
        line = Text()
        for index, (name, lead, sign, sign_style, name_style, _) in enumerate(members):
            if index not in kept:
                continue
            if line:
                line.append(CHIP_GAP)
            if sign:
                line.append(sign, style=sign_style)
                line.append(" ")
            if lead:
                line.append("★", style=lead_style)
            line.append(name if len(name) <= most else name[: most - 1] + "…", style=name_style)
        if keep < len(members):  # red when one of those counted waits for you too
            waiting = any(m[5] <= 1 for i, m in enumerate(members) if i not in kept)
            line.append(f"{CHIP_GAP}+{len(members) - keep}", style=f"bold {RED}" if waiting else MUTED)
        return line

    longest = max((len(m[0]) for m in members), default=0)
    for most in range(max(longest, SHORTEST), SHORTEST - 1, -1):
        line = drawn(most, len(members))
        if line.cell_len <= width:
            return line
    for keep in range(len(members) - 1, 0, -1):
        line = drawn(SHORTEST, keep)
        if line.cell_len <= width:
            return line
    return drawn(SHORTEST, 1)


def lanes_of(team: teams.Team, said: list[teams.Message]) -> list[str]:
    """The diagram's lanes: you on the left when you (or tabmancer) said anything, then the lead and the
    members, then one for anyone else who wrote to them."""
    names = [team.lead.name, *(m.name for m in team.members if not m.lead)]
    people = {m.sender for m in said} | {m.receiver for m in said}
    left = [teams.YOU] if people & {teams.YOU, teams.TABMANCER} else []
    right = [OTHER] if people - set(names) - {teams.YOU, teams.TABMANCER} else []
    return [*left, *names, *right]


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
    """'TEAM tabmancer · 3 of 3 open · 12 messages · 14.9M tokens'."""
    team = state.team
    said = len(state.between_members())
    what = f"  ·  {state.open_count()} of {len(team.members)} open  ·  {said} message{'s' if said != 1 else ''}"
    return Text.assemble(("TEAM ", f"bold {MUTED}"), (team.name, f"bold {accent}"), (what, MUTED),
                         (f"  ·  {human(tokens)} tokens" if tokens else "", MUTED))


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
