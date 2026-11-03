"""Claude Code Wrapped: your week as a card to share, drawn in pixels like Bit and saved as a PNG.

The last seven days of Claude Code, and who they make you: the conductor of sessions side by side,
the night owl, the marathoner and a few more (persona), each with the number behind it. Then the
numbers: the tokens and how they compare with the week before, the time at work, the sessions,
how many worked at once, what Claude wrote (in novels' worth), the files it edited, the models it
ran on, the sessions tabmancer brought back (or the days in a row, or the busiest day), the
longest session and the tool called most; and on the story card, the hours of the day the work was
done in. No project names, no titles, nothing said in a session: only counts.

Two shapes: wide (1200x675, for X, Bluesky and Threads) and story (1080x1920, for Instagram and
other stories); share.py takes them there.

On Fridays from noon (or the weekend, if the dashboard was not open then) the dashboard says the
week is ready, once a week; W shows the card, `tabmancer wrapped` saves it any day, and
`tabmancer wrapped off` stops the Friday word.
"""

from __future__ import annotations

import math
import re
import time as clock
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import bitart, card, stats
from .bitart import Colour
from .config import read_json, write_json
from .sessions import Session
from .tokens import ALONE_SECONDS, IDLE_SECONDS, Report, duration, human

DAY, HOUR = 4, 12  # Friday, from noon: the week is told of then, or the first time after it
OPENED = "opened.json"  # the sessions tabmancer opened again, for the card
KEEP_DAYS = 60
UNIT = 3  # saved and copied: three real pixels a design pixel
SHAPES = {"wide": (400, 225), "story": (360, 640)}  # design pixels: 1200x675 and 1080x1920 saved
WIDTH, HEIGHT = SHAPES["wide"]
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
TOKENS_A_NOVEL = 120_000  # a novel of about 90,000 words, at about three words for four tokens
TOKENS_A_PAGE = 400       # a page of about 300 words
NIGHT, EARLY = (22, 23, 0, 1, 2, 3, 4), (5, 6, 7, 8)  # hours of the day
FAMILY = re.compile(r"(fable|mythos|opus|sonnet|haiku)")
TAG = "#ClaudeCodeWrapped"
REPO = "github.com/SinanTufekci/tabmancer"

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
    output: int = 0             # tokens Claude wrote, its thinking among them
    files: int = 0              # files edited
    calls: int = 0              # tool calls
    models: list[tuple[str, float]] = field(default_factory=list)  # (family, share of the tokens), most first
    hours: list[float] = field(default_factory=lambda: [0.0] * 24)  # seconds at work by hour of the day
    weekend: float = 0.0        # seconds at work on Saturday and Sunday
    longest: float = 0.0        # the most time one session was at work
    streak: int = 0             # days at work in a row, to today (or yesterday, if today has not begun)


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
    spans = work_spans(events)
    hours, weekend, longest = at_work(spans)
    families: Counter[str] = Counter()
    for event in events:
        found = FAMILY.search(event.model or "")
        if found:
            families[found[1]] += event.use.total
    counted = sum(families.values())
    return Week(
        first=first, last=today, tokens=tokens_between(first, today),
        before=tokens_between(earlier, first - timedelta(days=1)) if report.first <= earlier else None,
        seconds=sum(day_seconds), days_worked=len(worked), sessions=len(ids),
        projects=len({report.folders.get(sid) for sid in ids} - {None, ""}),
        at_once=most_at_once(spans),
        busiest=None if busiest is None else ((first + timedelta(days=busiest)).weekday(), day_seconds[busiest]),
        tool=tools.most_common(1)[0] if tools else None,
        brought_back=len({e.get("session") for e in back}),
        carried_on=len({e.get("session") for e in back if e.get("how") in ("auto", "limit")}),
        daily=[tokens_between(first + timedelta(days=k), first + timedelta(days=k)) for k in range(7)],
        output=sum(e.use.output for e in events),
        files=len({path for e in events for path in e.files}),
        calls=sum(len(e.tools) for e in events),
        models=[(name, used / counted) for name, used in families.most_common()] if counted else [],
        hours=hours, weekend=weekend, longest=longest,
        streak=in_a_row(activity.day_seconds, (today - report.first).days))


def work_spans(events) -> dict[str, list[list[float]]]:
    """Each session's time at work: from a reply back to the one before it, unless that was a
    break (as time at work counts it), joined where they meet."""
    stamps: dict[str, list[int]] = {}
    for event in events:
        stamps.setdefault(event.session, []).append(event.at)
    out: dict[str, list[list[float]]] = {}
    for session, times in stamps.items():
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
        out[session] = spans
    return out


def most_at_once(spans: dict[str, list[list[float]]]) -> int:
    """The most sessions at work at the same time."""
    edges = [edge for own in spans.values() for begin, end in own for edge in ((begin, 1), (end, -1))]
    most = now = 0
    for _, step in sorted(edges):  # at the same moment one ends (-1) before another starts
        now += step
        most = max(most, now)
    return most


def side_by_side(events) -> int:
    return most_at_once(work_spans(events))


def at_work(spans: dict[str, list[list[float]]]) -> tuple[list[float], float, float]:
    """Seconds at work by hour of the day and on the weekend (sessions side by side counted once,
    by the minute), and the most one session was at work."""
    minutes: set[int] = set()
    longest = 0.0
    for own in spans.values():
        longest = max(longest, sum(end - begin for begin, end in own))
        for begin, end in own:
            minutes.update(range(int(begin // 60), max(int(begin // 60) + 1, math.ceil(end / 60))))
    hours, weekend = [0.0] * 24, 0.0
    for hour, count in Counter(minute // 60 for minute in minutes).items():
        moment = datetime.fromtimestamp(hour * 3600)  # local time: an hour of the day, a day of the week
        hours[moment.hour] += count * 60
        if moment.weekday() >= 5:
            weekend += count * 60
    return hours, weekend, longest


def in_a_row(day_seconds: list[float], today: int) -> int:
    """Days at work in a row to today; a today not begun yet does not break it."""
    k = min(today, len(day_seconds) - 1)
    if k >= 0 and k == today and day_seconds[k] <= 0:
        k -= 1
    run = 0
    while k >= 0 and day_seconds[k] > 0:
        run, k = run + 1, k - 1
    return run


# --- who the week makes you ---------------------------------------------------------------------

def persona(week: Week) -> tuple[str, str]:
    """A name for the week and the number behind it: the strongest of a few, each measured against
    the bar it has to clear; the builder when none does."""
    spent = sum(week.hours)

    def share(hours: tuple[int, ...]) -> float:
        return sum(week.hours[h] for h in hours) / spent if spent else 0.0

    timed = spent >= 2 * 3600  # shares of less than two hours say little
    night, early = share(NIGHT), share(EARLY)
    weekend = week.weekend / spent if spent else 0.0
    found = [
        (week.at_once / 4, "THE CONDUCTOR", f"{week.at_once} SESSIONS AT WORK AT ONCE"),
        (night / 0.3 if timed else 0.0, "THE NIGHT OWL", f"{_pct(night)}% OF THE WORK AT NIGHT"),
        (early / 0.25 if timed else 0.0, "THE EARLY BIRD", f"{_pct(early)}% OF THE WORK BEFORE 9:00"),
        (weekend / 0.35 if timed else 0.0, "THE WEEKEND WARRIOR", f"{_pct(weekend)}% OF THE WORK ON THE WEEKEND"),
        (week.longest / (4 * 3600), "THE MARATHONER", f"ONE SESSION AT WORK FOR {_time(week.longest)}"),
        (week.brought_back / 3, "THE COMEBACK KID", f"{week.brought_back} SESSIONS BROUGHT BACK"),
        (week.streak / 7, "THE STEADY HAND", f"{week.streak} DAYS IN A ROW"),
        # A busy week calls tools by the tens of thousands: a lower bar named nearly everyone so.
        (week.calls / 10_000, "THE TOOLSMITH", f"{week.calls:,} TOOL CALLS"),
    ]
    score, name, why = max(found, key=lambda item: item[0])  # the first of equals
    if score >= 1:
        return name, why
    if week.sessions:
        return "THE BUILDER", f"{_count(week.sessions, 'SESSION')} IN {_count(week.projects, 'PROJECT')}"
    return "THE WELL RESTED", "NO SESSIONS THIS WEEK"


def words(week: Week) -> str:
    """What a post says beside the card: the week in a line, who it made you, the tag, and where
    the card comes from. Short enough for X's 280 and Bluesky's 300."""
    name, _ = persona(week)
    parts = [f"{human(week.tokens)} tokens", f"{_time(week.seconds).lower()} at work"]
    if week.at_once > 1:
        parts.append(f"{week.at_once} sessions side by side")
    return (f"My week with Claude Code: {', '.join(parts)}. I'm {name.title()}. {TAG}\n\n"
            f"Made with tabmancer: https://{REPO}")


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


# --- the numbers ------------------------------------------------------------------------------

# The wide card is seen at half its size in a feed, so it keeps to six, roomy: the tokens, the time,
# the sessions, side by side, what Claude wrote, and the eighth (brought back, a streak or the
# busiest day). The story shows them all.
WIDE_TILES = (0, 1, 2, 3, 4, 7)


def tiles(week: Week) -> list[tuple[str, str, str]]:
    """The numbers on the card, the most telling first: (what, the number, a word under it)."""
    if week.before:
        change = round((week.tokens - week.before) / week.before * 100)
        note = f"{change:+d}% ON LAST WEEK"
    else:
        note = "NEW THIS WEEK" if week.before == 0 and week.tokens else ""
    out = [("TOKENS", human(week.tokens).upper(), note),
           ("AT WORK", _time(week.seconds), f"OVER {_count(week.days_worked, 'DAY')}"),
           ("SESSIONS", str(week.sessions), f"IN {_count(week.projects, 'PROJECT')}"),
           ("SIDE BY SIDE", str(week.at_once), "AT WORK AT ONCE"),
           ("CLAUDE WROTE", human(week.output).upper() if week.output else "-", worth(week.output)),
           ("FILES EDITED", f"{week.files:,}", f"{week.calls:,} TOOL CALLS" if week.calls else "")]
    if week.models:
        (top, share), *rest = week.models
        out.append(("MODELS", f"{top.upper()} {_pct(share)}%",
                    " · ".join(f"{name.upper()} {_pct(part)}%" for name, part in rest if _pct(part) >= 1)))
    else:
        out.append(("MODELS", "-", ""))
    if week.brought_back:
        alone = week.carried_on
        out.append(("BROUGHT BACK", str(week.brought_back),
                    "1 ON ITS OWN" if alone == 1 else f"{alone} ON THEIR OWN" if alone else "PICKED UP AGAIN"))
    elif week.streak >= 3:
        out.append(("STREAK", _count(week.streak, "DAY"), "AT WORK IN A ROW"))
    elif week.busiest:
        out.append(("BUSIEST DAY", WEEKDAYS[week.busiest[0]], _time(week.busiest[1])))
    else:
        out.append(("BUSIEST DAY", "-", ""))
    out.append(("LONGEST SESSION", _time(week.longest) if week.longest else "-", "AT WORK IN ONE" if week.longest else ""))
    if week.tool:
        name, count = week.tool
        short = name.replace(" (MCP)", "").upper()
        out.append(("TOP TOOL", short, f"{count:,} CALLS" + (" · MCP" if name.endswith("(MCP)") else "")))
    else:
        out.append(("TOP TOOL", "-", ""))
    return out


def worth(tokens: int) -> str:
    """Claude's writing in books: ~9 NOVELS, or ~40 PAGES for a light week."""
    if tokens >= TOKENS_A_NOVEL * 0.75:
        novels = max(1, round(tokens / TOKENS_A_NOVEL))
        return f"~{_count(novels, 'NOVEL')}"
    pages = round(tokens / TOKENS_A_PAGE)
    return f"~{_count(pages, 'PAGE')}" if pages else ""


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}{'S' * (n != 1)}"


def _pct(share: float) -> int:
    return round(share * 100)


def _time(seconds: float) -> str:
    """14H 05M, or 45 MIN under an hour: 45M beside the tokens' 55.8M would read as millions."""
    return duration(seconds).upper() if seconds >= 3600 else f"{int(seconds // 60)} MIN"


def _fit(text: str, room: int, size: int) -> str:
    """Cut to the room, an ellipsis's place taken by a dot."""
    while text and card.text_width(text, size) > room:
        text = text[:-2] + "." if len(text) > 2 else text[:-1]
    return text


# --- the card ---------------------------------------------------------------------------------

def draw(week: Week, accent: Colour, unit: int = UNIT, shape: str = "wide") -> card.Canvas:
    """The card in a shape of SHAPES, its design pixels unit x unit real ones each."""
    width, height = SHAPES[shape]
    canvas = card.Canvas(width * unit, height * unit, GROUND)
    pen = _Pen(canvas, unit, width)
    (_wide if shape == "wide" else _story)(pen, week, accent)
    return canvas


class _Pen:
    """Writes and draws on a canvas in design pixels."""

    def __init__(self, canvas: card.Canvas, unit: int, width: int) -> None:
        self.canvas, self.u, self.width = canvas, unit, width

    def text(self, x: int, y: int, words: str, colour: Colour, size: int = 1) -> int:
        return self.canvas.text(x * self.u, y * self.u, words, colour, size * self.u) // self.u

    def centred(self, y: int, words: str, colour: Colour, size: int = 1, room: int | None = None) -> None:
        words = _fit(words, room or self.width - 20, size)
        self.text((self.width - card.text_width(words, size)) // 2, y, words, colour, size)

    def right(self, x: int, y: int, words: str, colour: Colour, size: int = 1) -> None:
        self.text(x - card.text_width(words, size), y, words, colour, size)

    def box(self, x: int, y: int, w: int, h: int, radius: int, colour: Colour) -> None:
        u = self.u
        self.canvas.box(x * u, y * u, w * u, h * u, radius * u, colour)

    def bit(self, x: int, y: int, accent: Colour, cell: int) -> None:
        """Bit, glad of the week, in confetti; cell is a terminal cell's width in design pixels."""
        u = self.u
        pose = bitart.themed(bitart.Pose(right=bitart.Arm("raise", 20.0), left=bitart.Arm("hang", 14.0),
                                         eyes="happy", blush=True, beacon=0.95, chest=0.85), accent)
        bits = [bitart.Effect("confetti", cx, cy, 2.0, 1.0, colour) for cx, cy, colour in
                ((10, 16, bitart.CONFETTI[0]), (98, 8, bitart.CONFETTI[1]), (20, 42, bitart.CONFETTI[2]),
                 (102, 38, bitart.CONFETTI[3]), (4, 62, bitart.CONFETTI[4]), (104, 64, bitart.CONFETTI[0]),
                 (30, 4, bitart.CONFETTI[3]), (84, 26, bitart.CONFETTI[2]))]
        palette, rows = bitart.indexed(bitart.Frame(pose, bits), (cell * u, 2 * cell * u))
        self.canvas.blit(x * u, y * u, palette, rows)

    def tile(self, x: int, y: int, room: int, tile: tuple[str, str, str], accent: Colour, first: bool) -> None:
        what, number, note = tile
        self.text(x, y, _fit(what, room, 1), MUTED)
        self.text(x, y + 9, _fit(number, room, 2), accent if first else TEXT, 2)
        self.text(x, y + 26, _fit(note, room, 1), GREEN if note.startswith("+") else MUTED)


def _wide(pen: _Pen, week: Week, accent: Colour) -> None:
    """1200x675: who the week made you along the top, Bit and the days on the left, eight numbers."""
    shown = pen.text(14, 10, "CLAUDE CODE ", TEXT, 2)
    pen.text(14 + shown + 2, 10, "WRAPPED", accent, 2)
    pen.right(386, 14, span(week), MUTED)
    name, why = persona(week)
    shown = pen.text(14, 30, "I'M ", TEXT, 2)
    pen.text(14 + shown + 2, 30, _fit(name, 372 - shown, 2), accent, 2)
    pen.text(14, 48, _fit(why, 372, 1), MUTED)

    pen.bit(20, 60, accent, 8)
    pen.text(16, 134, "TOKENS A DAY", MUTED)
    peak = max(week.daily) or 1
    for k, used in enumerate(week.daily):
        x = 16 + k * 15
        tall = max(1, round(used / peak * 34)) if used else 1
        colour = accent if k == 6 else bitart._mix(GROUND, accent, 0.55)
        pen.box(x, 180 - tall, 10, tall, 1 if tall > 2 else 0, colour if used else PANEL)
        pen.text(x + 3, 184, "MTWTFSS"[(week.first + timedelta(days=k)).weekday()], TEXT if k == 6 else MUTED)

    pen.box(136, 58, 250, 152, 6, PANEL)
    every = tiles(week)
    for n, index in enumerate(WIDE_TILES):
        pen.tile(150 + (n % 2) * 120, 68 + (n // 2) * 48, 110, every[index], accent, n == 0)

    shown = pen.text(14, 214, "TABMANCER", accent)
    pen.text(14 + shown + 4, 214, "· " + REPO.upper(), MUTED)


def _story(pen: _Pen, week: Week, accent: Colour) -> None:
    """1080x1920: the name and Bit large, who the week made you, every number, the hours of the day."""
    pen.centred(34, "CLAUDE CODE", TEXT, 3)
    pen.centred(60, "WRAPPED", accent, 5)
    pen.centred(102, span(week), MUTED)
    pen.bit((360 - 154) // 2, 116, accent, 14)
    name, why = persona(week)
    pen.centred(238, "I'M", MUTED, 2)
    pen.centred(256, name, accent, 3, room=344)
    pen.centred(284, why, TEXT)

    pen.box(14, 300, 332, 238, 8, PANEL)
    for n, tile in enumerate(tiles(week)):
        pen.tile(30 + (n % 2) * 162, 310 + (n // 2) * 45, 146, tile, accent, n == 0)

    pen.text(30, 550, "WHEN I WORKED", MUTED)
    peak = max(week.hours) or 1
    top = max(range(24), key=lambda h: week.hours[h])
    for hour, spent in enumerate(week.hours):
        x = 30 + hour * 13
        tall = max(1, round(spent / peak * 34)) if spent else 1
        colour = accent if hour == top and spent else bitart._mix(GROUND, accent, 0.55)
        pen.box(x, 594 - tall, 10, tall, 1 if tall > 2 else 0, colour if spent else PANEL)
        if hour % 6 == 0:
            pen.text(x + (5 - card.text_width(str(hour)) // 2), 598, str(hour), MUTED)

    pen.centred(614, TAG.upper(), TEXT)
    pen.centred(626, f"TABMANCER · {REPO.upper()}", MUTED)


def span(week: Week) -> str:
    """28 OCT – 3 NOV 2026 (not %b: that is in the system's language)."""
    first, last = week.first, week.last
    start = f"{first.day} {MONTHS[first.month - 1]}" + (f" {first.year}" if first.year != last.year else "")
    return f"{start} – {last.day} {MONTHS[last.month - 1]} {last.year}"


def filename(week: Week, shape: str = "wide") -> str:
    return f"claude-code-wrapped-{week.last.isoformat()}{'' if shape == 'wide' else '-' + shape}.png"
