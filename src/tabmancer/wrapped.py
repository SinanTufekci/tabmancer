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
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TOKENS_A_NOVEL = 120_000  # a novel of about 90,000 words, at about three words for four tokens
TOKENS_A_PAGE = 400       # a page of about 300 words
NIGHT, EARLY = (22, 23, 0, 1, 2, 3, 4), (5, 6, 7, 8)  # hours of the day
FAMILY = re.compile(r"(fable|mythos|opus|sonnet|haiku)")
TAG = "#ClaudeCodeWrapped"
REPO = "github.com/SinanTufekci/tabmancer"

# The card is a night sky: deep blue at the top going to purple, stars where nothing is written, and
# Bit glowing in the theme's colour. Each number has a colour of its own, its icon and its name in it.
GROUND: Colour = (8, 12, 34)  # the sky at the top
DUSK: Colour = (42, 16, 58)  # ...and at the foot
PANEL: Colour = (40, 36, 72)  # an empty bar, a rule between numbers
TEXT: Colour = (236, 233, 227)
MUTED: Colour = (150, 146, 178)  # grey with the sky's violet in it
GREEN: Colour = (76, 195, 138)
STAR: Colour = (255, 248, 236)
HUES: tuple[Colour, ...] = ((57, 197, 207), (167, 139, 250), (76, 195, 138), (227, 179, 65), (240, 120, 160),
                            (108, 182, 255))  # after the accent, the numbers' colours in turn
VIOLET = HUES[1]


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
    pen = _Pen(canvas, unit, width, height)
    pen.sky()
    (_wide if shape == "wide" else _story)(pen, week, accent)
    return canvas


# Seven by seven, a number's sign beside it, by what the number is.
ICONS = {
    "TOKENS": "0001000 0011100 0111110 1111111 0111110 0011100 0001000",
    "AT WORK": "0011100 0100010 1001001 1001101 1000001 0100010 0011100",
    "SESSIONS": "1111111 1000001 1111111 1010001 1001001 1010001 1111111",
    "SIDE BY SIDE": "1111100 1000100 1011111 1110001 0010001 0010001 0011111",
    "CLAUDE WROTE": "1111111 0000000 1111100 0000000 1111110 0000000 1110000",
    "FILES EDITED": "1111100 1000110 1000011 1011101 1000001 1011101 1111111",
    "MODELS": "0101010 1111111 0100010 1101011 0100010 1111111 0101010",
    "BROUGHT BACK": "0011100 0100011 1000011 1000000 1000001 0100010 0011100",
    "STREAK": "0001000 0011000 0111100 0111110 1111111 0111110 0011100",
    "BUSIEST DAY": "1111111 1000001 1111111 1000001 1010101 1000001 1111111",
    "LONGEST SESSION": "1111111 0100010 0010100 0001000 0010100 0100010 1111111",
    "TOP TOOL": "0000110 0001100 0001101 0011111 0111000 1110000 1100000",
}


class _Pen:
    """Writes and draws on a canvas in design pixels."""

    def __init__(self, canvas: card.Canvas, unit: int, width: int, height: int = 0) -> None:
        self.canvas, self.u, self.width = canvas, unit, width
        self.height = height or canvas.height // unit
        self.clear: list[tuple[int, int, int, int]] = []  # where the stars keep away from

    def air(self, y: int) -> Colour:
        """The sky's colour at a height."""
        return bitart._mix(GROUND, DUSK, round(y / max(1, self.height - 1) * 64) / 64)

    def sky(self) -> None:
        """Deep blue going to purple, a band a real pixel high at a time (64 shades)."""
        canvas = self.canvas
        for y in range(canvas.height):
            canvas.rect(0, y, canvas.width, 1, self.air(y / self.u))

    def stars(self, count: int, seed: int) -> None:
        """Small stars, and a few that twinkle, where nothing is drawn (clear)."""
        import random

        rng = random.Random(seed)
        free = [(x, y) for _ in range(count * 4) for x, y in [(rng.randrange(2, self.width - 2),
                                                             rng.randrange(2, self.height - 2))]
                if not any(left <= x < right and top <= y < bottom for left, top, right, bottom in self.clear)]
        for k, (x, y) in enumerate(free[:count]):
            shine = (0.25, 0.4, 0.6, 0.9)[rng.randrange(4)]
            self.rect(x, y, 1, 1, bitart._mix(self.air(y), STAR, shine))
            if k % 9 == 0:  # a twinkle: a cross
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    self.rect(x + dx, y + dy, 1, 1, bitart._mix(self.air(y), STAR, 0.45))

    def keep_clear(self, x: int, y: int, w: int, h: int) -> None:
        self.clear.append((x, y, x + w, y + h))

    def rect(self, x: int, y: int, w: int, h: int, colour: Colour) -> None:
        u = self.u
        self.canvas.rect(x * u, y * u, w * u, h * u, colour)

    def text(self, x: int, y: int, words: str, colour: Colour, size: int = 1, bold: bool = False) -> int:
        """Words at (x, y); bold, each stroke a third of a font pixel wider."""
        if bold:
            self.canvas.text(x * self.u + max(1, size * self.u // 3), y * self.u, words, colour, size * self.u)
        return self.canvas.text(x * self.u, y * self.u, words, colour, size * self.u) // self.u

    def centred(self, y: int, words: str, colour: Colour, size: int = 1, room: int | None = None,
                bold: bool = False) -> None:
        words = _fit(words, room or self.width - 20, size)
        self.text((self.width - card.text_width(words, size)) // 2, y, words, colour, size, bold)

    def right(self, x: int, y: int, words: str, colour: Colour, size: int = 1) -> None:
        self.text(x - card.text_width(words, size), y, words, colour, size)

    def box(self, x: int, y: int, w: int, h: int, radius: int, colour: Colour) -> None:
        u = self.u
        self.canvas.box(x * u, y * u, w * u, h * u, radius * u, colour)

    def disc(self, cx: float, cy: float, r: float, colour: Colour) -> None:
        u = self.u
        cx, cy, r = round(cx * u), round(cy * u), round(r * u)
        for dy in range(-r, r + 1):
            half = int((r * r - dy * dy) ** 0.5)
            self.canvas.rect(cx - half, cy + dy, 2 * half + 1, 1, colour)

    def glow(self, cx: float, cy: float, r: float, colour: Colour, strength: float = 0.42, rings: int = 12) -> None:
        """A soft light round a point: rings of the colour, fainter outwards."""
        for k in range(rings, 0, -1):
            self.disc(cx, cy, r * k / rings, bitart._mix(self.air(cy), colour, strength * (1 - k / rings) ** 1.6 + 0.02))

    def icon(self, x: int, y: int, what: str, colour: Colour) -> None:
        for gy, line in enumerate(ICONS.get(what, ICONS["TOKENS"]).split()):
            for gx, bit in enumerate(line):
                if bit == "1":
                    self.rect(x + gx, y + gy, 1, 1, colour)

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

    def bars(self, x: int, y: int, values: list[float], width: int, height: int, accent: Colour,
             labels: list[str] | None = None, peak: int | None = None) -> None:
        """Bars from violet to the accent, left to right; the one at peak (or the last) in the accent
        with its label lit; an empty one a sliver of the sky's own colour."""
        count = len(values)
        gap = 5 if count <= 7 else 3
        bar = (width - gap * (count - 1)) // count
        top = max(values) or 1
        lit = count - 1 if peak is None else peak
        for k, value in enumerate(values):
            tall = max(1, round(value / top * height)) if value else 1
            colour = accent if k == lit and value else bitart._mix(VIOLET, accent, k / max(1, count - 1) * 0.8)
            self.box(x + k * (bar + gap), y + height - tall, bar, tall, 1 if tall > 3 else 0,
                     colour if value else bitart._mix(self.air(y + height), PANEL, 0.8))
            if labels and labels[k]:
                self.text(x + k * (bar + gap) + (bar - card.text_width(labels[k])) // 2, y + height + 4, labels[k],
                          accent if k == lit and value else MUTED)

    def number(self, x: int, y: int, tile: tuple[str, str, str], colour: Colour, room: int, label_at: int) -> None:
        """A line of the list: its sign, the number large, and beside it its name in its colour with
        the word under it (in small letters)."""
        what, figure, note = tile
        self.icon(x, y + 3, what, colour)
        self.text(x + 14, y, _fit(figure, label_at - x - 18, 2), TEXT, 2, bold=True)
        self.text(label_at, y + 1, _fit(what.lower(), room, 1), colour, bold=True)
        self.text(label_at, y + 10, _fit(note.lower(), room, 1), GREEN if note.startswith("+") else MUTED)

    def footer(self, x: int, y: int, accent: Colour) -> None:
        shown = self.text(x, y, "tabmancer", accent, bold=True)
        self.text(x + shown + 5, y, "· " + REPO.lower(), MUTED)


def _wide(pen: _Pen, week: Week, accent: Colour) -> None:
    """1200x675: Bit glowing on the left over the days, and on the right who the week made you and
    six numbers, a line each."""
    pen.keep_clear(140, 0, 260, 207)  # the words
    pen.keep_clear(0, 138, 140, 87)  # the days and the footer
    pen.keep_clear(8, 40, 112, 96)  # Bit
    pen.stars(70, 7)
    pen.glow(64, 92, 54, accent)  # first: nothing is drawn under it
    shown = pen.text(150, 12, "CLAUDE CODE ", TEXT, 2, bold=True)
    pen.text(150 + shown + 2, 12, "WRAPPED", accent, 2, bold=True)
    pen.text(150, 30, span(week), MUTED)
    name, why = persona(week)
    shown = pen.text(150, 42, "I'M ", TEXT, 2, bold=True)
    pen.text(150 + shown + 2, 42, _fit(name, 236 - shown, 2), accent, 2, bold=True)
    pen.text(150, 59, _fit(why.lower(), 236, 1), MUTED)
    every = tiles(week)
    colours = (accent, *HUES)
    for n, index in enumerate(WIDE_TILES):
        y = 72 + n * 22
        pen.number(150, y, every[index], colours[n], 116, 270)
        if n < len(WIDE_TILES) - 1:
            pen.rect(150, y + 19, 236, 1, bitart._mix(pen.air(y + 19), VIOLET, 0.18))

    pen.bit(14, 50, accent, 8)
    pen.text(16, 146, "tokens a day", MUTED)
    labels = ["MTWTFSS"[(week.first + timedelta(days=k)).weekday()] for k in range(7)]
    pen.bars(16, 158, week.daily, 112, 28, accent, labels)
    pen.footer(16, 212, accent)


def _story(pen: _Pen, week: Week, accent: Colour) -> None:
    """1080x1920: the name and Bit glowing large, who the week made you, every number a line, the
    hours of the day."""
    pen.keep_clear(0, 24, 360, 92)  # the name and the dates
    pen.keep_clear(98, 104, 164, 128)  # Bit
    pen.keep_clear(0, 232, 360, 408)  # who, the numbers, the hours, the footer
    pen.stars(110, 11)
    pen.glow(180, 172, 92, accent, 0.38, 14)  # first: the words go over it
    pen.centred(34, "CLAUDE CODE", TEXT, 3, bold=True)
    pen.centred(60, "WRAPPED", accent, 5, bold=True)
    pen.centred(102, span(week), MUTED)
    pen.bit((360 - 154) // 2, 112, accent, 14)
    name, why = persona(week)
    pen.centred(238, "I'M", TEXT, 2, bold=True)
    pen.centred(255, name, accent, 3, room=344, bold=True)
    pen.centred(283, why.lower(), MUTED)

    colours = (accent, *HUES)
    for n, tile in enumerate(tiles(week)):
        y = 304 + n * 25
        pen.number(26, y, tile, colours[n % len(colours)], 150, 196)
        if n < 9:
            pen.rect(26, y + 21, 308, 1, bitart._mix(pen.air(y + 21), VIOLET, 0.18))

    pen.text(26, 560, "when i worked", MUTED)
    top = max(range(24), key=lambda h: week.hours[h])
    pen.bars(26, 572, week.hours, 308, 26, accent, [str(h) if h % 6 == 0 else "" for h in range(24)],
             peak=top if week.hours[top] else None)
    pen.centred(616, TAG, TEXT)
    words = "tabmancer · " + REPO.lower()
    shown = pen.text((360 - card.text_width(words)) // 2, 628, "tabmancer", accent, bold=True)
    pen.text((360 - card.text_width(words)) // 2 + shown, 628, " · " + REPO.lower(), MUTED)


def span(week: Week) -> str:
    """28 Oct – 3 Nov 2026 (not %b: that is in the system's language)."""
    first, last = week.first, week.last
    start = f"{first.day} {MONTHS[first.month - 1]}" + (f" {first.year}" if first.year != last.year else "")
    return f"{start} – {last.day} {MONTHS[last.month - 1]} {last.year}"


def filename(week: Week, shape: str = "wide") -> str:
    return f"claude-code-wrapped-{week.last.isoformat()}{'' if shape == 'wide' else '-' + shape}.png"
