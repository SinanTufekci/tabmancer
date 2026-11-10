"""Claude Code Wrapped: your week as a card to share, drawn in pixels like Bit and saved as a PNG.

The last seven days of Claude Code (or thirty, or all of the time kept: history.py), and who they
make you: the conductor of sessions side by side,
the night owl, the marathoner and a few more (persona), each with the number behind it. Then the
numbers: the tokens and how they compare with the week before, the time at work, the sessions,
how many worked at once, what Claude wrote (in novels' worth), the files it edited, the models it
ran on, the sessions tabmancer brought back (or the days in a row, or the busiest day), the
longest session and the tool called most; and on the story card, the hours of the day the work was
done in. No project names, no titles, nothing said in a session: only counts.

Two shapes: wide (1200x675, for X, Bluesky and Threads) and story (1080x1920, for Instagram and
other stories); share.py takes them there. Besides the card that sums it up, slides of one thing
each (who you were, the tokens, the time, the sessions, the tools: SLIDES), each to share on its
own, and any of them moving: its numbers counting up, as a GIF (moving).

On Fridays from noon (or the weekend, if the dashboard was not open then) the dashboard says the
week is ready, once a week; W shows the card, `tabmancer wrapped` saves it any day, and
`tabmancer wrapped off` stops the Friday word.
"""

from __future__ import annotations

import math
import re
import time as clock
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import bitart, card, stats, wrapped_poses
from .bitart import Colour
from .config import read_json, write_json
from .sessions import Session
from .tokens import ALONE_SECONDS, IDLE_SECONDS, Day, Report, duration, human

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
    days: int = 7               # the period: seven days, thirty, or all of it (whole)
    whole: bool = False         # from the first day kept: all time
    before_seconds: float | None = None  # time at work in the period before; None when not known
    before_sessions: int | None = None
    top_project: tuple[str, float] | None = None  # the project with the most tokens, and its share
    peak: tuple[date, int] | None = None  # the day with the most tokens
    best_streak: int = 0        # the longest run of days at work in the period
    day_sessions: list[int] = field(default_factory=list)  # sessions at work each day, oldest first
    top_tools: list[tuple[str, int]] = field(default_factory=list)  # the tools called most, and how often


def week(report: Report, sessions: dict[str, Session], today: date | None = None,
         opened: list[dict] | None = None, days: int | None = 7) -> Week:
    """The days to today, seven of them, or thirty, or (None) all the report counts; Claude Code's
    sessions only (the card is named for it), and the days the history keeps where their files are
    gone (history.attach). The period before as long is set against it where the report reaches."""
    today = today or date.today()
    whole = days is None
    first = report.first if whole else today - timedelta(days=days - 1)
    days = (today - first).days + 1
    start = datetime.combine(first, time.min).timestamp()
    end = datetime.combine(today + timedelta(days=1), time.min).timestamp()
    earlier = first - timedelta(days=days)
    reaches = not whole and report.first <= earlier

    def claude(sid: str) -> bool:
        return report.clis.get(sid, "claude") == "claude"

    by_day: Counter[int] = Counter()
    for (sid, day), use in report.cells.items():
        if claude(sid):
            by_day[day] += use.total
    for day, kept in report.archive.items():
        by_day[day] += kept.use.total

    def tokens_between(a: date, b: date) -> int:
        return sum(by_day[o] for o in range(a.toordinal(), b.toordinal() + 1))

    def inside(a: date, b: date) -> dict[int, Day]:
        return {o: k for o, k in report.archive.items() if a.toordinal() <= o <= b.toordinal()}

    events = [e for e in report.events if start <= e.at < end and claude(e.session)]
    kept = inside(first, today)
    activity = report.activity

    def seconds_from(a: date, n: int) -> list[float]:
        offset = (a - report.first).days
        return [activity.day_seconds[offset + k] if 0 <= offset + k < report.days else 0.0 for k in range(n)]

    day_seconds = seconds_from(first, days)
    worked = [k for k in range(days) if day_seconds[k] > 0]
    busiest = max(worked, key=lambda k: day_seconds[k], default=None)
    ids = {e.session for e in events}
    tools: Counter[str] = Counter(stats.tool_group(name) for e in events for name in e.tools)
    for day in kept.values():
        for name, count in day.tools.items():
            tools[stats.tool_group(name)] += count
    back = [e for e in (opened if opened is not None else opened_sessions()) if start <= e.get("at", 0) < end]
    spans = work_spans(events)
    hours, weekend, longest = at_work(spans)
    families: Counter[str] = Counter()
    for event in events:
        found = FAMILY.search(event.model or "")
        if found:
            families[found[1]] += event.use.total
    folders: Counter[str] = Counter()
    for (sid, day), use in report.cells.items():
        if first.toordinal() <= day <= today.toordinal() and claude(sid):
            folders[report.folders.get(sid, "")] += use.total
    for ordinal, day in kept.items():
        hours = [h + more for h, more in zip(hours, day.hours)]
        if date.fromordinal(ordinal).weekday() >= 5:
            weekend += day.seconds
        for model, use in day.models.items():
            found = FAMILY.search(model)
            if found:
                families[found[1]] += use.total
        for folder, (use, _, _) in day.projects.items():
            folders[folder] += use.total
    counted = sum(families.values())
    folders.pop("", None)
    daily = [by_day[first.toordinal() + k] for k in range(days)]
    top = max(range(days), key=lambda k: daily[k]) if any(daily) else None
    per_day: dict[int, set[str]] = {}
    for event in events:
        per_day.setdefault(date.fromtimestamp(event.at).toordinal(), set()).add(event.session)
    day_sessions = [len(per_day.get(first.toordinal() + k, ())) + (kept[first.toordinal() + k].sessions
                                                                  if first.toordinal() + k in kept else 0)
                    for k in range(days)]
    before_seconds = before_sessions = None
    if reaches:
        earlier_kept = inside(earlier, first - timedelta(days=1))
        if all(day.source != "claude" for day in earlier_kept.values()):  # Claude Code's record keeps no time
            before_seconds = sum(seconds_from(earlier, days))
        begin = datetime.combine(earlier, time.min).timestamp()
        before_sessions = len({e.session for e in report.events if begin <= e.at < start and claude(e.session)}) \
            + sum(day.sessions for day in earlier_kept.values())
    total = tokens_between(first, today)
    return Week(
        first=first, last=today, tokens=total,
        before=tokens_between(earlier, first - timedelta(days=1)) if reaches else None,
        seconds=sum(day_seconds), days_worked=len(worked),
        sessions=len(ids) + sum(day.sessions for day in kept.values()),
        projects=len(folders),
        at_once=most_at_once(spans),
        busiest=None if busiest is None else ((first + timedelta(days=busiest)).weekday(), day_seconds[busiest]),
        tool=tools.most_common(1)[0] if tools else None,
        brought_back=len({e.get("session") for e in back}),
        carried_on=len({e.get("session") for e in back if e.get("how") in ("auto", "limit")}),
        daily=daily,
        output=sum(e.use.output for e in events) + sum(day.use.output for day in kept.values()),
        files=len({path for e in events for path in e.files}),
        calls=sum(len(e.tools) for e in events) + sum(sum(day.tools.values()) + day.calls for day in kept.values()),
        models=[(name, used / counted) for name, used in families.most_common()] if counted else [],
        hours=hours, weekend=weekend, longest=longest,
        streak=in_a_row(activity.day_seconds, (today - report.first).days),
        days=days, whole=whole, before_seconds=before_seconds, before_sessions=before_sessions,
        top_project=(Path(folders.most_common(1)[0][0]).name or folders.most_common(1)[0][0],
                     folders.most_common(1)[0][1] / total) if folders and total else None,
        peak=None if top is None else (first + timedelta(days=top), daily[top]),
        best_streak=best_run(day_seconds), day_sessions=day_sessions, top_tools=tools.most_common(3))


def best_run(day_seconds: list[float]) -> int:
    """The longest run of days at work."""
    best = run = 0
    for seconds in day_seconds:
        run = run + 1 if seconds > 0 else 0
        best = max(best, run)
    return best


def period(week: Week) -> str:
    """'week', 'month', 'all time', or '12 days'."""
    return "all time" if week.whole else {7: "week", 30: "month"}.get(week.days, f"{week.days} days")


def last_one(week: Week) -> str:
    """What the period is set against: 'LAST WEEK', 'LAST MONTH', 'THE 12 DAYS BEFORE'."""
    return {7: "LAST WEEK", 30: "LAST MONTH"}.get(week.days, f"THE {week.days} DAYS BEFORE")


def scaled(week: Week, part: float) -> Week:
    """The week's numbers a part of the way up from nothing: a moving card counting up (moving)."""
    if part >= 1:
        return week

    def r(n: float) -> int:
        return round(n * part)

    return replace(week, tokens=r(week.tokens), seconds=week.seconds * part, days_worked=r(week.days_worked),
                   sessions=r(week.sessions), projects=r(week.projects), at_once=r(week.at_once),
                   brought_back=r(week.brought_back), carried_on=r(week.carried_on),
                   daily=[r(n) for n in week.daily], output=r(week.output), files=r(week.files), calls=r(week.calls),
                   hours=[h * part for h in week.hours], longest=week.longest * part, streak=r(week.streak),
                   best_streak=r(week.best_streak), day_sessions=[r(n) for n in week.day_sessions],
                   tool=None if week.tool is None else (week.tool[0], r(week.tool[1])),
                   top_tools=[(name, r(n)) for name, n in week.top_tools],
                   busiest=None if week.busiest is None else (week.busiest[0], week.busiest[1] * part),
                   peak=None if week.peak is None else (week.peak[0], r(week.peak[1])))


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
    for minute in minutes:  # each in its own local hour: grouped by UTC hours, a zone half an hour off
        moment = datetime.fromtimestamp(minute * 60)  # (India, Iran, Adelaide...) had every hour 30 minutes out
        hours[moment.hour] += 60
        if moment.weekday() >= 5:
            weekend += 60
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
    weeks = max(1.0, week.days / 7)  # a month brings back more, and calls more tools, than a week
    night, early = share(NIGHT), share(EARLY)
    weekend = week.weekend / spent if spent else 0.0
    found = [
        (week.at_once / 4, "THE CONDUCTOR", f"{week.at_once} SESSIONS AT WORK AT ONCE"),
        (night / 0.3 if timed else 0.0, "THE NIGHT OWL", f"{_pct(night)}% OF THE WORK AT NIGHT"),
        (early / 0.25 if timed else 0.0, "THE EARLY BIRD", f"{_pct(early)}% OF THE WORK BEFORE 9:00"),
        (weekend / 0.35 if timed else 0.0, "THE WEEKEND WARRIOR", f"{_pct(weekend)}% OF THE WORK ON THE WEEKEND"),
        (week.longest / (4 * 3600), "THE MARATHONER", f"ONE SESSION AT WORK FOR {_time(week.longest)}"),
        (week.brought_back / (3 * weeks), "THE COMEBACK KID", f"{week.brought_back} SESSIONS BROUGHT BACK"),
        (week.streak / 7, "THE STEADY HAND", f"{week.streak} DAYS IN A ROW"),
        # A busy week calls tools by the tens of thousands: a lower bar named nearly everyone so.
        (week.calls / (10_000 * weeks), "THE TOOLSMITH", f"{week.calls:,} TOOL CALLS"),
    ]
    score, name, why = max(found, key=lambda item: item[0])  # the first of equals
    if score >= 1:
        return name, why
    if week.sessions:
        return "THE BUILDER", f"{_count(week.sessions, 'SESSION')} IN {_count(week.projects, 'PROJECT')}"
    return "THE WELL RESTED", "NO SESSIONS IN ALL THIS TIME" if week.whole else f"NO SESSIONS THIS {period(week).upper()}"


def words(week: Week) -> str:
    """What a post says beside the card: the week in a line, who it made you, the tag, and where
    the card comes from. Short enough for X's 280 and Bluesky's 300."""
    name, _ = persona(week)
    parts = [f"{human(week.tokens)} tokens", f"{_time(week.seconds).lower()} at work"]
    if week.at_once > 1:
        parts.append(f"{week.at_once} sessions side by side")
    opening = (f"Me and Claude Code since {MONTHS[week.first.month - 1]} {week.first.year}" if week.whole
               else f"My {period(week)} with Claude Code")
    return f"{opening}: {', '.join(parts)}. I'm {name.title()}. {TAG}\n\nMade with tabmancer: https://{REPO}"


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


def compared(now: float, before: float | None, against: str) -> str:
    """+23% ON LAST WEEK, or nothing when there is nothing to set it against."""
    if not before:
        return ""
    return f"{round((now - before) / before * 100):+d}% ON {against}"


def tiles(week: Week, like: Week | None = None) -> list[tuple[str, str, str]]:
    """The numbers on the card, the most telling first: (what, the number, a word under it). like:
    the whole period, when this one is it counting up (scaled): which numbers there are, and the
    words under them, are its."""
    like = like or week
    if like.before:
        note = compared(like.tokens, like.before, last_one(like))
    elif like.whole:
        note = f"SINCE {like.first.day} {MONTHS[like.first.month - 1].upper()} {like.first.year}"
    else:
        note = f"NEW THIS {period(like).upper()}" if like.before == 0 and like.tokens else ""
    out = [("TOKENS", human(week.tokens).upper(), note),
           ("AT WORK", _time(week.seconds), f"OVER {_count(like.days_worked, 'DAY')}"),
           ("SESSIONS", f"{week.sessions:,}", f"IN {_count(like.projects, 'PROJECT')}"),
           ("SIDE BY SIDE", str(week.at_once), "AT WORK AT ONCE"),
           ("CLAUDE WROTE", human(week.output).upper() if like.output else "-", worth(like.output)),
           ("FILES EDITED", f"{week.files:,}", f"{like.calls:,} TOOL CALLS" if like.calls else "")]
    if like.models:
        (top, share), *rest = like.models
        out.append(("MODELS", f"{top.upper()} {_pct(share)}%",
                    " · ".join(f"{name.upper()} {_pct(part)}%" for name, part in rest if _pct(part) >= 1)))
    else:
        out.append(("MODELS", "-", ""))
    if like.brought_back:
        alone = like.carried_on
        out.append(("BROUGHT BACK", str(week.brought_back),
                    "1 ON ITS OWN" if alone == 1 else f"{alone} ON THEIR OWN" if alone else "PICKED UP AGAIN"))
    elif like.streak >= 3:
        out.append(("STREAK", _count(week.streak, "DAY"), "AT WORK IN A ROW"))
    elif like.busiest:
        out.append(("BUSIEST DAY", WEEKDAYS[like.busiest[0]], _time(week.busiest[1] if week.busiest else 0)))
    else:
        out.append(("BUSIEST DAY", "-", ""))
    out.append(("LONGEST SESSION", _time(week.longest) if like.longest else "-", "AT WORK IN ONE" if like.longest else ""))
    if like.tool:
        name, _ = like.tool
        short = name.replace(" (MCP)", "").upper()
        calls = week.tool[1] if week.tool else 0
        out.append(("TOP TOOL", short, f"{calls:,} CALLS" + (" · MCP" if name.endswith("(MCP)") else "")))
    else:
        out.append(("TOP TOOL", "-", ""))
    return out


def trend(week: Week, like: Week | None = None) -> tuple[str, list[float], list[str], float]:
    """The tokens over the period for a chart: (what it shows, the values, a label under each, the
    highest of like's): a day a bar up to a month, a month a bar beyond."""
    like = like or week

    def grouped(one: Week) -> tuple[list[float], list[str]]:
        if one.days <= 7:
            return list(one.daily), ["MTWTFSS"[(one.first + timedelta(days=k)).weekday()] for k in range(one.days)]
        if one.days <= 31:
            return list(one.daily), [str((one.first + timedelta(days=k)).day) if k % 7 == 0 else ""
                                     for k in range(one.days)]
        months: dict[tuple[int, int], float] = {}
        for k, value in enumerate(one.daily):
            day = one.first + timedelta(days=k)
            months[(day.year, day.month)] = months.get((day.year, day.month), 0) + value
        return list(months.values()), ["JFMAMJJASOND"[month - 1] for _, month in months]

    values, labels = grouped(week)
    return ("tokens a day" if week.days <= 31 else "tokens a month"), values, labels, max(grouped(like)[0], default=0)


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

SLIDES = ("you", "tokens", "time", "sessions", "tools", "summary")
SLIDE_NAMES = {"you": "who you were", "tokens": "the tokens", "time": "the time", "sessions": "the sessions",
               "tools": "tools and models", "summary": "all of it"}
def slide_colour(slide: str, accent: Colour) -> Colour:
    """The slide's own colour: its sky is tinted with it (the card that sums it up keeps the night)."""
    return {"time": HUES[0], "sessions": HUES[1], "tools": HUES[2]}.get(slide, accent)


def ease(part: float) -> float:
    """Fast, then settling: a number counting up."""
    part = min(1.0, max(0.0, part))
    return 1 - (1 - part) ** 3


def draw(week: Week, accent: Colour, unit: int = UNIT, shape: str = "wide", slide: str = "summary",
         progress: float = 1.0, names: bool = False) -> card.Canvas:
    """A slide (SLIDES; the card that sums it up by default) in a shape of SHAPES, its design pixels
    unit x unit real ones each. progress below 1: its numbers that part of the way up (moving);
    names: the top project's name on it, which is otherwise left off."""
    width, height = SHAPES[shape]
    canvas = card.Canvas(width * unit, height * unit, GROUND)
    tint = None if slide == "summary" else slide_colour(slide, accent)
    pen = _Pen(canvas, unit, width, height, tint)
    pen.sky()
    shown = scaled(week, ease(progress))
    if slide == "summary":
        (_wide if shape == "wide" else _story)(pen, shown, accent, week)
    else:
        _slide(pen, shown, week, accent, slide, shape, names)
    return canvas


def moving(week: Week, accent: Colour, shape: str = "wide", slide: str = "summary", names: bool = False,
           unit: int = 2, frames: int = 18) -> bytes:
    """The slide as a GIF: its numbers counting up in about a second, then held for five, again
    and again. Two real pixels a design pixel: a third of the still card's size to post."""
    canvases = [draw(week, accent, unit, shape, slide, k / (frames - 1), names) for k in range(frames)]
    return card.gif(canvases, [6] * (frames - 1) + [500])


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

    def __init__(self, canvas: card.Canvas, unit: int, width: int, height: int = 0, tint: Colour | None = None) -> None:
        self.canvas, self.u, self.width = canvas, unit, width
        self.height = height or canvas.height // unit
        self.clear: list[tuple[int, int, int, int]] = []  # where the stars keep away from
        # The night sky, or one tinted with the slide's own colour (slide_colour).
        self.top = bitart._mix(GROUND, tint, 0.16) if tint else GROUND
        self.foot = bitart._mix(DUSK, tint, 0.32) if tint else DUSK

    def air(self, y: int) -> Colour:
        """The sky's colour at a height."""
        return bitart._mix(self.top, self.foot, round(y / max(1, self.height - 1) * 64) / 64)

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

    def bit(self, x: int, y: int, accent: Colour, cell: int, persona: str = "") -> None:
        """Bit as the week's persona (wrapped_poses; glad of the week, in confetti, for one it has no
        pose for); cell is a terminal cell's width in design pixels."""
        u = self.u
        palette, rows = bitart.indexed(wrapped_poses.pose(persona, accent), (cell * u, 2 * cell * u))
        self.canvas.blit(x * u, y * u, palette, rows)

    def bars(self, x: int, y: int, values: list[float], width: int, height: int, accent: Colour,
             labels: list[str] | None = None, peak: int | None = None, top: float | None = None) -> None:
        """Bars from violet to the accent, left to right; the one at peak (or the last) in the accent
        with its label lit; an empty one a sliver of the sky's own colour. top: the value a bar the
        whole height stands for (the whole period's, while it counts up)."""
        count = len(values)
        if not count:
            return
        gap = 5 if count <= 7 else 3 if count <= 16 else 1
        bar = max(1, (width - gap * (count - 1)) // count)
        top = top or max(values) or 1
        lit = count - 1 if peak is None else peak
        for k, value in enumerate(values):
            tall = max(1, min(height, round(value / top * height))) if value else 1
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


def _wide(pen: _Pen, week: Week, accent: Colour, full: Week | None = None) -> None:
    """1200x675: Bit glowing on the left over the days, and on the right who the week made you and
    six numbers, a line each. full: the whole week, when this one is it counting up."""
    full = full or week
    pen.keep_clear(140, 0, 260, 207)  # the words
    pen.keep_clear(0, 138, 140, 87)  # the days and the footer
    pen.keep_clear(8, 40, 112, 96)  # Bit
    pen.stars(70, 7)
    pen.glow(64, 92, 54, accent)  # first: nothing is drawn under it
    shown = pen.text(150, 12, "CLAUDE CODE ", TEXT, 2, bold=True)
    pen.text(150 + shown + 2, 12, "WRAPPED", accent, 2, bold=True)
    pen.text(150, 30, span(week), MUTED)
    name, why = persona(full)
    shown = pen.text(150, 42, "I'M ", TEXT, 2, bold=True)
    pen.text(150 + shown + 2, 42, _fit(name, 236 - shown, 2), accent, 2, bold=True)
    pen.text(150, 59, _fit(why.lower(), 236, 1), MUTED)
    every = tiles(week, full)
    colours = (accent, *HUES)
    for n, index in enumerate(WIDE_TILES):
        y = 72 + n * 22
        pen.number(150, y, every[index], colours[n], 116, 270)
        if n < len(WIDE_TILES) - 1:
            pen.rect(150, y + 19, 236, 1, bitart._mix(pen.air(y + 19), VIOLET, 0.18))

    pen.bit(14, 50, accent, 8, name)
    what, values, labels, top = trend(week, full)
    pen.text(16, 146, what, MUTED)
    pen.bars(16, 158, values, 112, 28, accent, labels, top=top)
    pen.footer(16, 212, accent)


def _story(pen: _Pen, week: Week, accent: Colour, full: Week | None = None) -> None:
    """1080x1920: the name and Bit glowing large, who the week made you, every number a line, the
    hours of the day. full: the whole week, when this one is it counting up."""
    full = full or week
    pen.keep_clear(0, 24, 360, 92)  # the name and the dates
    pen.keep_clear(98, 104, 164, 128)  # Bit
    pen.keep_clear(0, 232, 360, 408)  # who, the numbers, the hours, the footer
    pen.stars(110, 11)
    pen.glow(180, 172, 92, accent, 0.38, 14)  # first: the words go over it
    pen.centred(34, "CLAUDE CODE", TEXT, 3, bold=True)
    pen.centred(60, "WRAPPED", accent, 5, bold=True)
    pen.centred(102, span(week), MUTED)
    name, why = persona(full)
    pen.bit((360 - 154) // 2, 112, accent, 14, name)
    pen.centred(238, "I'M", TEXT, 2, bold=True)
    pen.centred(255, name, accent, 3, room=344, bold=True)
    pen.centred(283, why.lower(), MUTED)

    colours = (accent, *HUES)
    for n, tile in enumerate(tiles(week, full)):
        y = 304 + n * 25
        pen.number(26, y, tile, colours[n % len(colours)], 150, 196)
        if n < 9:
            pen.rect(26, y + 21, 308, 1, bitart._mix(pen.air(y + 21), VIOLET, 0.18))

    pen.text(26, 560, "when i worked", MUTED)
    top = max(range(24), key=lambda h: full.hours[h])
    pen.bars(26, 572, week.hours, 308, 26, accent, [str(h) if h % 6 == 0 else "" for h in range(24)],
             peak=top if full.hours[top] else None, top=full.hours[top])
    pen.centred(616, TAG, TEXT)
    words = "tabmancer · " + REPO.lower()
    shown = pen.text((360 - card.text_width(words)) // 2, 628, "tabmancer", accent, bold=True)
    pen.text((360 - card.text_width(words)) // 2 + shown, 628, " · " + REPO.lower(), MUTED)


def _wrap(words: str, room: int, size: int) -> list[str]:
    """Words in lines that fit the room at a size, a word too long cut to it."""
    lines: list[str] = []
    for word in words.split():
        if lines and card.text_width(f"{lines[-1]} {word}", size) <= room:
            lines[-1] += f" {word}"
        else:
            lines.append(_fit(word, room, size))
    return lines


def _wrap_parts(line: str, room: int, size: int) -> list[str]:
    """A line of parts set apart by dots in lines that fit, broken between the parts where it can."""
    lines: list[str] = []
    for part in line.split(" · "):
        if lines and card.text_width(f"{lines[-1]} · {part}", size) <= room:
            lines[-1] += f" · {part}"
        else:
            lines += _wrap(part, room, size)
    return lines


def _dots(pen: _Pen, x: int, y: int, slide: str, accent: Colour, centred: bool = False) -> None:
    """The slides as dots, this one lit and longer."""
    widths = [9 if name == slide else 3 for name in SLIDES]
    full = sum(widths) + 3 * (len(SLIDES) - 1)
    left = x - full // 2 if centred else x - full
    for name, wide in zip(SLIDES, widths):
        pen.box(left, y, wide, 3, 1, accent if name == slide else bitart._mix(pen.air(y), MUTED, 0.45))
        left += wide + 3


def _frame(pen: _Pen, week: Week, accent: Colour, slide: str, shape: str) -> None:
    """Round each slide: the name at the top and the dates, the slides as dots, where it comes from."""
    if shape == "wide":
        pen.keep_clear(0, 0, 400, 24)
        pen.keep_clear(0, 200, 400, 25)
        shown = pen.text(16, 12, "CLAUDE CODE ", TEXT, bold=True)
        pen.text(16 + shown, 12, "WRAPPED", accent, bold=True)
        pen.right(384, 12, span(week), MUTED)
        pen.footer(16, 208, accent)
        _dots(pen, 384, 210, slide, accent)
        return
    pen.keep_clear(0, 0, 360, 52)
    pen.keep_clear(0, 590, 360, 50)
    words = "CLAUDE CODE WRAPPED"
    left = (360 - card.text_width(words, 2)) // 2
    shown = pen.text(left, 18, "CLAUDE CODE ", TEXT, 2, bold=True)
    pen.text(left + shown, 18, "WRAPPED", accent, 2, bold=True)
    pen.centred(36, span(week), MUTED)
    _dots(pen, 180, 596, slide, accent, centred=True)
    pen.centred(612, TAG, TEXT)
    line = "tabmancer · " + REPO.lower()
    shown = pen.text((360 - card.text_width(line)) // 2, 626, "tabmancer", accent, bold=True)
    pen.text((360 - card.text_width(line)) // 2 + shown, 626, " · " + REPO.lower(), MUTED)


def _slide_words(week: Week, full: Week, slide: str, names: bool) -> tuple[str, str, list[str]]:
    """A slide's name, its number (counting up with week) and the lines under it (full's)."""
    against = last_one(full)
    if slide == "tokens":
        lines = [compared(full.tokens, full.before, against)]
        if full.output:
            lines.append(f"CLAUDE WROTE {human(full.output).upper()} · {worth(full.output)}")
        if full.peak and full.peak[1]:
            day, count = full.peak
            lines.append(f"BUSIEST DAY {day.day} {MONTHS[day.month - 1].upper()} · {human(count).upper()}")
        return "TOKENS", human(week.tokens).upper(), lines
    if slide == "time":
        lines = [compared(full.seconds, full.before_seconds, against)]
        if full.days_worked:
            lines.append(f"OVER {_count(full.days_worked, 'DAY')} · {_time(full.seconds / full.days_worked)} A DAY")
        if full.best_streak > 1:
            lines.append(f"{full.best_streak} DAYS IN A ROW AT MOST")
        if any(full.hours):
            hour = max(range(24), key=lambda h: full.hours[h])
            lines.append(f"MOST OFTEN AT {hour:02d}:00")
        return "AT WORK", _time(week.seconds), lines
    if slide == "sessions":
        lines = [compared(full.sessions, full.before_sessions, against),
                 f"IN {_count(full.projects, 'PROJECT')} · UP TO {full.at_once} AT ONCE"]
        if full.longest:
            lines.append(f"THE LONGEST AT WORK FOR {_time(full.longest)}")
        if full.brought_back:
            lines.append(f"{full.brought_back} BROUGHT BACK BY TABMANCER")
        return "SESSIONS", f"{week.sessions:,}", lines
    # tools and models
    lines = [f"{full.calls:,} TOOL CALLS · {full.files:,} FILES EDITED"]
    if full.models:
        lines.append(" · ".join(f"{name.upper()} {_pct(part)}%" for name, part in full.models if _pct(part) >= 1))
    if full.top_project:
        name, share = full.top_project
        lines.append(f"MOST IN {name.upper()} · {_pct(share)}%" if names else
                     f"ONE PROJECT TOOK {_pct(share)}% OF THE TOKENS")
    tool = full.tool[0].replace(" (MCP)", "").upper() if full.tool else "-"
    return "TOP TOOL", tool, lines


def _slide_chart(pen: _Pen, week: Week, full: Week, slide: str, colour: Colour, x: int, y: int, width: int,
                 height: int) -> None:
    """The slide's chart, its name over it."""
    if slide == "tokens":
        what, values, labels, top = trend(week, full)
        pen.text(x, y - 12, what, MUTED)
        pen.bars(x, y, values, width, height, colour, labels, top=top)
    elif slide == "time":
        peak = max(range(24), key=lambda h: full.hours[h])
        pen.text(x, y - 12, "when i worked", MUTED)
        pen.bars(x, y, week.hours, width, height, colour, [str(h) if h % 6 == 0 else "" for h in range(24)],
                 peak=peak if full.hours[peak] else None, top=full.hours[peak])
    elif slide == "sessions":
        values, top = list(week.day_sessions), max(full.day_sessions, default=0)
        if week.days <= 31:
            labels = ["MTWTFSS"[(week.first + timedelta(days=k)).weekday()] if week.days <= 7 else
                      (str((week.first + timedelta(days=k)).day) if k % 7 == 0 else "") for k in range(week.days)]
            what = "sessions a day"
        else:  # by month, the most in a day
            months: dict[tuple[int, int], int] = {}
            for k, (shown, whole) in enumerate(zip(week.day_sessions, full.day_sessions)):
                day = week.first + timedelta(days=k)
                months[(day.year, day.month)] = max(months.get((day.year, day.month), 0), shown)
            values, labels = list(months.values()), ["JFMAMJJASOND"[m - 1] for _, m in months]
            what = "the most sessions in a day, by month"
        pen.text(x, y - 12, what, MUTED)
        pen.bars(x, y, values, width, height, colour, labels, top=top)
    else:  # the models' shares, a bar split between them, and the tools called most under it
        pen.text(x, y - 12, "models", MUTED)
        left = x
        for k, (name, part) in enumerate(full.models):
            wide = round(width * part) if k < len(full.models) - 1 else x + width - left
            if wide <= 0:
                continue
            hue = (colour, *HUES)[k % (len(HUES) + 1)]
            pen.box(left, y, max(1, wide - 2), 10, 2 if wide > 6 else 0, hue)
            if wide > card.text_width(name) + 4:
                pen.text(left, y + 14, name, hue)
            left += wide
        if height < 50:  # the wide slide: the tools called most in a line
            pen.text(x, y + 26, _fit(" · ".join(f"{name.replace(' (MCP)', '').lower()} {shown:,}"
                                                for name, shown in week.top_tools), width, 1), MUTED)
            return
        top = max((n for _, n in full.top_tools), default=0) or 1
        for k, ((name, count), (_, shown)) in enumerate(zip(full.top_tools, week.top_tools)):
            row = y + 30 + k * 12
            if row + 8 > y + height + 40:
                break
            label = _fit(name.replace(" (MCP)", "").lower(), 90, 1)
            pen.text(x, row, label, TEXT)
            pen.box(x + 96, row + 1, max(1, round((width - 150) * shown / top)), 5, 1,
                    bitart._mix(colour, TEXT, 0.15 * k))
            pen.right(x + width, row, f"{shown:,}", MUTED)


def _slide(pen: _Pen, week: Week, full: Week, accent: Colour, slide: str, shape: str, names: bool) -> None:
    """One of the slides that is not the summing-up card: who you were, or one number large with
    its lines under it and a chart, Bit as the persona beside it."""
    colour = slide_colour(slide, accent)
    name, why = persona(full)
    wide = shape == "wide"
    if slide == "you":
        if wide:
            pen.keep_clear(184, 30, 216, 170)
            pen.keep_clear(20, 40, 150, 120)
        else:
            pen.keep_clear(0, 230, 360, 340)
            pen.keep_clear(90, 70, 180, 150)
        pen.stars(70 if wide else 110, 3)
        _frame(pen, full, accent, slide, shape)
        opening = (f"SINCE {MONTHS[full.first.month - 1].upper()} {full.first.year} I'VE BEEN" if full.whole
                   else f"THIS {period(full).upper()} I WAS")
        facts = [(f"{week.days_worked}", "days"), (f"{week.sessions:,}", "sessions"),
                 (f"{week.projects}", "projects")]
        if wide:
            pen.glow(96, 112, 62, accent)
            pen.bit(34, 52, accent, 9, name)
            pen.text(196, 44, opening, MUTED)
            lines = _wrap(name, 188, 3)
            for k, line in enumerate(lines[:3]):
                pen.text(196, 58 + k * 24, line, accent, 3, bold=True)
            below = 58 + min(3, len(lines)) * 24
            for k, line in enumerate(_wrap(why.lower(), 188, 1)[:2]):
                pen.text(196, below + 2 + k * 11, line, MUTED)
            for k, (figure, what) in enumerate(facts):
                pen.text(196 + k * 66, 166, figure, TEXT, 2, bold=True)
                pen.text(196 + k * 66, 184, what, MUTED)
        else:
            pen.glow(180, 150, 96, accent, 0.38, 14)
            pen.bit((360 - 154) // 2, 92, accent, 14, name)
            pen.centred(250, opening, MUTED, 2)
            lines = _wrap(name, 330, 4)
            for k, line in enumerate(lines[:3]):
                pen.centred(272 + k * 34, line, accent, 4, bold=True)
            below = 272 + min(3, len(lines)) * 34
            for k, line in enumerate(_wrap(why.lower(), 320, 2)[:2]):
                pen.centred(below + 6 + k * 18, line, MUTED, 2)
            for k, (figure, what) in enumerate(facts):
                centre = 60 + k * 120
                pen.text(centre - card.text_width(figure, 4) // 2, 470, figure, TEXT, 4, bold=True)
                pen.text(centre - card.text_width(what) // 2, 506, what, MUTED)
        return

    what, figure, lines = _slide_words(week, full, slide, names)
    lines = [line for line in lines if line]
    if wide:
        pen.keep_clear(0, 24, 310, 112)
        pen.keep_clear(300, 24, 100, 76)
        pen.keep_clear(0, 132, 400, 70)
        pen.stars(50, 5)
        _frame(pen, full, accent, slide, shape)
        pen.glow(352, 58, 34, colour, 0.34, 8)
        pen.bit(318, 26, accent, 5, name)
        pen.icon(16, 34, what, colour)
        pen.text(28, 34, what.lower(), colour, 1, bold=True)
        pen.text(16, 48, _fit(figure, 290, 6), TEXT, 6, bold=True)
        for k, line in enumerate(lines[:4]):
            pen.text(16, 96 + k * 10, _fit(line.lower(), 368, 1),
                     GREEN if line.startswith("+") else (240, 120, 120) if line.startswith("-") else MUTED)
        _slide_chart(pen, week, full, slide, colour, 16, 152, 368, 34)
        return
    pen.keep_clear(0, 50, 360, 470)
    pen.stars(90, 9)
    _frame(pen, full, accent, slide, shape)
    pen.glow(180, 118, 66, colour, 0.36, 10)
    pen.bit((360 - 110) // 2, 72, accent, 10, name)
    pen.centred(196, what, colour, 3, bold=True)
    pen.centred(226, figure, TEXT, 7 if card.text_width(figure, 8) > 340 else 8, room=344, bold=True)
    written = 0
    for line in lines:
        tone = GREEN if line.startswith("+") else (240, 120, 120) if line.startswith("-") else MUTED
        for part in _wrap_parts(line.lower(), 330, 2):
            if written < 6:
                pen.centred(300 + written * 18, part, tone, 2, room=340)
            written += 1
    _slide_chart(pen, week, full, slide, colour, 26, 446, 308, 56)


def span(week: Week) -> str:
    """28 Oct – 3 Nov 2026 (not %b: that is in the system's language)."""
    first, last = week.first, week.last
    start = f"{first.day} {MONTHS[first.month - 1]}" + (f" {first.year}" if first.year != last.year else "")
    return f"{start} – {last.day} {MONTHS[last.month - 1]} {last.year}"


def filename(week: Week, shape: str = "wide", slide: str = "summary", kind: str = "png") -> str:
    """claude-code-wrapped-2026-11-09[-month][-tokens][-story].png"""
    span_word = "" if week.days == 7 and not week.whole else "-all-time" if week.whole else f"-{period(week).replace(' ', '-')}"
    return (f"claude-code-wrapped-{week.last.isoformat()}{span_word}{'' if slide == 'summary' else '-' + slide}"
            f"{'' if shape == 'wide' else '-' + shape}.{kind}")
