"""Claude Code Wrapped: your week as a card to share, drawn in pixels like Bit and saved as a PNG.

The last seven days of Claude Code: the tokens and how they compare with the week before, the time
at work, the sessions, how many worked at once (side by side, in their own tabs), the sessions
tabmancer brought back (opened again where they left off, or told to go on), and the tool called
most. No project names, no titles, nothing said in a session: only counts.

On Fridays from noon (or the weekend, if the dashboard was not open then) the dashboard says the
week is ready, once a week; W shows the card, `tabmancer wrapped` saves it any day, and
`tabmancer wrapped off` stops the Friday word.
"""

from __future__ import annotations

import time as clock
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import bitart, card, stats
from .bitart import Colour
from .config import read_json, write_json
from .sessions import Session
from .tokens import ALONE_SECONDS, IDLE_SECONDS, Report, duration, human

DAY, HOUR = 4, 12  # Friday, from noon: the week is told of then, or the first time after it
OPENED = "opened.json"  # the sessions tabmancer opened again, for the card
KEEP_DAYS = 60
WIDTH, HEIGHT = 400, 225  # the card in design pixels; a unit makes each so many real ones
UNIT = 3  # saved and copied: 1200x675, the shape shared pictures take
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

GROUND: Colour = (11, 14, 20)
PANEL: Colour = (21, 26, 36)
TEXT: Colour = (230, 227, 221)
MUTED: Colour = (125, 133, 144)
GREEN: Colour = (76, 195, 138)


@dataclass
class Week:
    first: date
    last: date
    tokens: int
    before: int | None          # the seven days before; None when not counted
    seconds: float              # time at work, side by side sessions once
    days_worked: int
    sessions: int
    projects: int
    at_once: int                # the most sessions at work at the same time
    busiest: tuple[int, float] | None  # (weekday, seconds at work)
    tool: tuple[str, int] | None       # the tool called most, and how often
    brought_back: int           # sessions tabmancer opened again or told to go on
    carried_on: int             # of those, the ones that went on by themselves (auto-continue, a limit)
    daily: list[int]            # tokens a day, oldest first


def week(report: Report, sessions: dict[str, Session], today: date | None = None,
         opened: list[dict] | None = None) -> Week:
    """The seven days to today, Claude Code's sessions only (the card is named for it)."""
    today = today or date.today()
    first = today - timedelta(days=6)
    start = datetime.combine(first, time.min).timestamp()
    end = datetime.combine(today + timedelta(days=1), time.min).timestamp()

    def claude(sid: str) -> bool:
        return report.clis.get(sid, "claude") == "claude"

    def tokens_between(a: date, b: date) -> int:
        return sum(use.total for (sid, day), use in report.cells.items()
                   if a.toordinal() <= day <= b.toordinal() and claude(sid))

    events = [e for e in report.events if start <= e.at < end and claude(e.session)]
    earlier = first - timedelta(days=7)
    activity = report.activity
    offset = (first - report.first).days
    day_seconds = [activity.day_seconds[offset + k] if 0 <= offset + k < report.days else 0.0 for k in range(7)]
    worked = [k for k in range(7) if day_seconds[k] > 0]
    busiest = max(worked, key=lambda k: day_seconds[k], default=None)
    ids = {e.session for e in events}
    tools = Counter(stats.tool_group(name) for e in events for name in e.tools)
    back = [e for e in (opened if opened is not None else opened_sessions()) if start <= e.get("at", 0) < end]
    return Week(
        first=first, last=today, tokens=tokens_between(first, today),
        before=tokens_between(earlier, first - timedelta(days=1)) if report.first <= earlier else None,
        seconds=sum(day_seconds), days_worked=len(worked), sessions=len(ids),
        projects=len({report.folders.get(sid) for sid in ids} - {None, ""}),
        at_once=side_by_side(events),
        busiest=None if busiest is None else ((first + timedelta(days=busiest)).weekday(), day_seconds[busiest]),
        tool=tools.most_common(1)[0] if tools else None,
        brought_back=len({e.get("session") for e in back}),
        carried_on=len({e.get("session") for e in back if e.get("how") in ("auto", "limit")}),
        daily=[tokens_between(first + timedelta(days=k), first + timedelta(days=k)) for k in range(7)])


def side_by_side(events) -> int:
    """The most sessions at work at the same time: each at work from a reply back to the one
    before it, unless that was a break (as time at work counts it)."""
    stamps: dict[str, list[int]] = {}
    for event in events:
        stamps.setdefault(event.session, []).append(event.at)
    edges: list[tuple[float, int]] = []
    for times in stamps.values():
        times.sort()
        spans: list[list[float]] = []
        previous = None
        for moment in times:
            gap = moment - previous if previous is not None else None
            begin = moment - (gap if gap is not None and gap <= IDLE_SECONDS else ALONE_SECONDS)
            if spans and begin <= spans[-1][1]:
                spans[-1][1] = moment
            else:
                spans.append([begin, moment])
            previous = moment
        edges += [edge for begin, end in spans for edge in ((begin, 1), (end, -1))]
    most = now = 0
    for _, step in sorted(edges):  # at the same moment one ends (-1) before another starts
        now += step
        most = max(most, now)
    return most


# --- the sessions tabmancer brought back ------------------------------------------------------

def note_opened(entries: list[tuple[str, str]], now: float | None = None) -> None:
    """Sessions tabmancer opened again (resume, auto) or told to go on after a limit (limit), as
    (session id, how): kept two months, for the card. Only ids, never what was said."""
    if not entries:
        return
    now = clock.time() if now is None else now
    kept = [e for e in opened_sessions() if now - e.get("at", 0) < KEEP_DAYS * 86400]
    kept += [{"at": now, "session": sid, "how": how} for sid, how in entries]
    try:
        write_json(OPENED, kept, indent=None)
    except OSError:
        pass  # the card counts fewer; nothing else depends on it


def opened_sessions() -> list[dict]:
    saved = read_json(OPENED)
    return [e for e in saved if isinstance(e, dict)] if isinstance(saved, list) else []


# --- on Fridays -------------------------------------------------------------------------------

def enabled() -> bool:
    saved = read_json("config.json")
    return not (isinstance(saved, dict) and saved.get("wrapped") is False)


def set_enabled(on: bool) -> None:
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    saved["wrapped"] = on
    write_json("config.json", saved, indent=2)


def week_key(now: datetime) -> str:
    year, number, _ = now.isocalendar()
    return f"{year}-W{number:02d}"


def due(now: datetime | None = None) -> bool:
    """Whether to say the week is ready: Friday from noon, or the weekend after, once a week."""
    now = now or datetime.now()
    if not enabled() or now.weekday() < DAY or (now.weekday() == DAY and now.hour < HOUR):
        return False
    saved = read_json("config.json")
    return not (isinstance(saved, dict) and saved.get("wrapped_told") == week_key(now))


def told(now: datetime | None = None) -> None:
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    saved["wrapped_told"] = week_key(now or datetime.now())
    write_json("config.json", saved, indent=2)


# --- the card ---------------------------------------------------------------------------------

def tiles(week: Week) -> list[tuple[str, str, str]]:
    """The six numbers on the card: (what, the number, a word under it)."""
    if week.before:
        change = round((week.tokens - week.before) / week.before * 100)
        note = f"{change:+d}% ON LAST WEEK"
    else:
        note = "NEW THIS WEEK" if week.before == 0 and week.tokens else ""
    days = f"OVER {week.days_worked} DAY{'S' * (week.days_worked != 1)}"
    out = [("TOKENS", human(week.tokens).upper(), note),
           ("AT WORK", _time(week.seconds), days),
           ("SESSIONS", str(week.sessions), f"IN {week.projects} PROJECT{'S' * (week.projects != 1)}"),
           ("SIDE BY SIDE", str(week.at_once), "AT WORK AT ONCE")]
    if week.brought_back:
        alone = week.carried_on
        out.append(("BROUGHT BACK", str(week.brought_back),
                    "1 ON ITS OWN" if alone == 1 else f"{alone} ON THEIR OWN" if alone else "PICKED UP AGAIN"))
    elif week.busiest:
        out.append(("BUSIEST DAY", WEEKDAYS[week.busiest[0]], _time(week.busiest[1])))
    else:
        out.append(("BUSIEST DAY", "-", ""))
    if week.tool:
        name, count = week.tool
        short = name.replace(" (MCP)", "").upper()
        out.append(("TOP TOOL", short, f"{count:,} CALLS" + (" · MCP" if name.endswith("(MCP)") else "")))
    else:
        out.append(("TOP TOOL", "-", ""))
    return out


def _time(seconds: float) -> str:
    """14H 05M, or 45 MIN under an hour: 45M beside the tokens' 55.8M would read as millions."""
    return duration(seconds).upper() if seconds >= 3600 else f"{int(seconds // 60)} MIN"


def _fit(text: str, room: int, size: int) -> str:
    """Cut to the room, an ellipsis's place taken by a dot."""
    while text and card.text_width(text, size) > room:
        text = text[:-2] + "." if len(text) > 2 else text[:-1]
    return text


def draw(week: Week, accent: Colour, unit: int = UNIT) -> card.Canvas:
    """The card, WIDTH x HEIGHT design pixels of unit x unit real ones each."""
    u = unit
    canvas = card.Canvas(WIDTH * u, HEIGHT * u, GROUND)

    def text(x: int, y: int, words: str, colour: Colour, size: int = 1) -> int:
        return canvas.text(x * u, y * u, words, colour, size * u) // u

    shown = text(14, 12, "CLAUDE CODE ", TEXT, 2)
    text(14 + shown + 2, 12, "WRAPPED", accent, 2)
    text(14, 31, f"MY WEEK · {span(week)}", MUTED)

    # Bit, glad of it, in confetti.
    pose = bitart.themed(bitart.Pose(right=bitart.Arm("raise", 20.0), left=bitart.Arm("hang", 14.0), eyes="happy",
                                     blush=True, beacon=0.95, chest=0.85), accent)
    bits = [bitart.Effect("confetti", x, y, 2.0, 1.0, colour) for x, y, colour in
            ((10, 16, bitart.CONFETTI[0]), (98, 8, bitart.CONFETTI[1]), (20, 42, bitart.CONFETTI[2]),
             (102, 38, bitart.CONFETTI[3]), (4, 62, bitart.CONFETTI[4]), (104, 64, bitart.CONFETTI[0]),
             (30, 4, bitart.CONFETTI[3]), (84, 26, bitart.CONFETTI[2]))]
    palette, rows = bitart.indexed(bitart.Frame(pose, bits), (10 * u, 20 * u))
    canvas.blit(12 * u, 48 * u, palette, rows)

    # The days of the week, their tokens.
    text(16, 140, "TOKENS A DAY", MUTED)
    peak = max(week.daily) or 1
    for k, used in enumerate(week.daily):
        x = 16 + k * 15
        tall = max(1, round(used / peak * 30)) if used else 1
        colour = accent if k == 6 else bitart._mix(GROUND, accent, 0.55)
        canvas.box(x * u, (182 - tall) * u, 10 * u, tall * u, u if tall > 2 else 0, colour if used else PANEL)
        day = "MTWTFSS"[(week.first + timedelta(days=k)).weekday()]
        text(x + 3, 186, day, TEXT if k == 6 else MUTED)

    # The numbers.
    canvas.box(136 * u, 48 * u, 250 * u, 150 * u, 6 * u, PANEL)
    for n, (what, number, note) in enumerate(tiles(week)):
        x, y = 150 + (n % 2) * 120, 60 + (n // 2) * 46
        text(x, y, what, MUTED)
        text(x, y + 11, _fit(number, 110, 2), accent if n == 0 else TEXT, 2)
        colour = GREEN if note.startswith("+") else MUTED
        text(x, y + 29, _fit(note, 110, 1), colour)

    shown = text(14, 208, "TABMANCER", accent)
    text(14 + shown + 4, 208, "· GITHUB.COM/SINANTUFEKCI/TABMANCER", MUTED)
    return canvas


def span(week: Week) -> str:
    """28 OCT – 3 NOV 2026 (not %b: that is in the system's language)."""
    first, last = week.first, week.last
    start = f"{first.day} {MONTHS[first.month - 1]}" + (f" {first.year}" if first.year != last.year else "")
    return f"{start} – {last.day} {MONTHS[last.month - 1]} {last.year}"


def filename(week: Week) -> str:
    return f"claude-code-wrapped-{week.last.isoformat()}.png"
