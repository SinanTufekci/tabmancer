"""Token use, tools and working time per session and day, from the session files.

Every Claude Code reply in a transcript carries the API's usage counts. A reply split
over several records repeats the same counts under the same message id, and a forked
session starts with a copy of its parent's history, so counts are kept per message and
each message is counted once, for the session that was created first. Each reply also
names the tools it called and the files it edited, and its time stamp says when the
work happened.

Codex, when it is on, adds its rollouts: running token totals, tool calls and patches.

Session files only grow. The cache remembers how far each one has been read, so after
the first run only what was appended since is read.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cached_property
from pathlib import Path

from .config import read_json, write_json
from .sessions import EDIT_TOOLS, _glob, claude_dir

CACHE_FILE = "tokens.json"
CACHE_FORMAT = 2  # bump when the row layout changes
FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
IDLE_SECONDS = 5 * 60  # a longer gap between two replies is a break, not work
ALONE_SECONDS = 60     # what a reply after a break counts for
PATCHED = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
_lock = threading.Lock()  # one scan at a time: they share the cache file


@dataclass
class Tokens:
    input: int = 0        # sent fresh, without the cache
    cache_write: int = 0  # written to the cache
    cache_read: int = 0   # read back from the cache: the conversation so far, every turn
    output: int = 0       # what the model wrote, thinking included

    @property
    def total(self) -> int:
        return self.input + self.cache_write + self.cache_read + self.output

    def add(self, other: Tokens) -> None:
        self.input += other.input
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read
        self.output += other.output


def human(count: float) -> str:
    """1234 -> 1.2k, 5_300_000 -> 5.3M, 8_264_000_000 -> 8.3B."""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if count >= size * 0.9995:  # 999 960 is 1M, not 1000k
            value = count / size
            return f"{value:.0f}{unit}" if value >= 100 else f"{value:.1f}{unit}".replace(".0", "")
    return f"{count:.0f}"


def scaled(count: float) -> tuple[float, str, int]:
    """A count as (number, unit word, decimals) for a big-digit display: 2.1 billion, 612 million."""
    for size, word in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if count >= size:
            decimals = 1 if count / size < 10 else 0
            return round(count / size, decimals), word, decimals
    return count, "tokens", 0


def duration(seconds: float) -> str:
    """3h 20m, 45m, 0m."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


@dataclass
class Event:
    """One reply (or, for Codex, one usage update or tool call)."""
    session: str
    at: int  # epoch seconds
    model: str
    use: Tokens
    tools: list[str]
    files: list[str]


@dataclass
class Activity:
    """When the work happened. Wall time merges sessions that ran side by side."""
    day_seconds: list[float]                 # wall time per day of the window, oldest first
    slot_seconds: list[list[float]]          # [weekday 0=Monday][hour], wall time
    session_seconds: dict[str, float]        # per session, inside the window
    sessions_per_day: list[int]              # sessions with any activity that day


@dataclass
class Report:
    """What happened in the window that ends today, plus each session's tokens over its whole life."""

    first: date
    days: int
    cells: dict[tuple[str, int], Tokens] = field(default_factory=dict)  # (session, day ordinal)
    models: dict[str, Tokens] = field(default_factory=dict)  # inside the window
    folders: dict[str, str] = field(default_factory=dict)  # session id -> the folder it was last in
    clis: dict[str, str] = field(default_factory=dict)  # session id -> claude or codex
    events: list[Event] = field(default_factory=list)  # every event, oldest first, whole life

    @property
    def start(self) -> float:
        return datetime.combine(self.first, time.min).timestamp()

    @property
    def last(self) -> date:
        """The window's last day: the day the report was collected, which is not today any more once
        midnight has passed and the report is still the one from before it."""
        return self.first + timedelta(days=self.days - 1)

    def in_window(self, day: int) -> bool:
        return day >= self.first.toordinal()

    def total(self, sessions: set[str] | None = None, last_days: int | None = None) -> Tokens:
        """Use inside the window, or only over today and the last_days - 1 before it."""
        start = self.first.toordinal() if last_days is None else self.last.toordinal() - last_days + 1
        out = Tokens()
        for (sid, day), use in self.cells.items():
            if day >= start and (sessions is None or sid in sessions):
                out.add(use)
        return out

    def between(self, first: date, last: date) -> Tokens:
        out = Tokens()
        for (_, day), use in self.cells.items():
            if first.toordinal() <= day <= last.toordinal():
                out.add(use)
        return out

    def daily(self, sessions: set[str] | None = None) -> list[Tokens]:
        """One entry per day of the window, oldest first."""
        out = [Tokens() for _ in range(self.days)]
        start = self.first.toordinal()
        for (sid, day), use in self.cells.items():
            if 0 <= day - start < self.days and (sessions is None or sid in sessions):
                out[day - start].add(use)
        return out

    def hourly(self) -> list[int]:
        """Today's tokens per hour, from midnight up to the hour now."""
        midnight = datetime.combine(date.today(), time.min).timestamp()
        out = [0] * (datetime.now().hour + 1)
        for event in reversed(self.events):  # oldest first: today's are at the end
            if event.at < midnight:
                break
            hour = datetime.fromtimestamp(event.at).hour
            if hour < len(out):
                out[hour] += event.use.total
        return out

    def per_session(self, whole_life: bool = False) -> dict[str, Tokens]:
        out: dict[str, Tokens] = {}
        for (sid, day), use in self.cells.items():
            if whole_life or self.in_window(day):
                out.setdefault(sid, Tokens()).add(use)
        return out

    def session(self, session_id: str) -> Tokens:
        """Everything one session has used, including days before the window."""
        out = Tokens()
        for (sid, _), use in self.cells.items():
            if sid == session_id:
                out.add(use)
        return out

    def window_events(self) -> list[Event]:
        start = self.start
        return [e for e in self.events if e.at >= start]

    def tools(self) -> Counter[str]:
        return Counter(name for e in self.window_events() for name in e.tools)

    def edited(self) -> Counter[tuple[str, str]]:
        """(session, file path) -> edits, inside the window."""
        return Counter((e.session, path) for e in self.window_events() for path in e.files)

    def wall_seconds(self, sessions: set[str]) -> float:
        """Time at work in the window for these sessions together, side by side ones counted once."""
        start = self.start
        stamps: dict[str, list[int]] = {}
        for event in self.events:
            if event.at >= start and event.session in sessions:
                stamps.setdefault(event.session, []).append(event.at)
        total, reach = 0.0, float("-inf")
        for begin, end in sorted(_spans(stamps)):
            begin = max(begin, reach, start)
            if end > begin:
                total += end - begin
            reach = max(reach, end)
        return total

    @cached_property
    def activity(self) -> Activity:
        start = self.start
        day0 = self.first.toordinal()
        stamps: dict[str, list[int]] = {}
        for event in self.events:
            if event.at >= start:
                stamps.setdefault(event.session, []).append(event.at)
        spans: list[tuple[float, float]] = []
        per_session: dict[str, float] = {}
        active_days: list[set[str]] = [set() for _ in range(self.days)]
        for sid, times in stamps.items():
            times.sort()
            previous = None
            for moment in times:
                gap = moment - previous if previous is not None else None
                credit = gap if gap is not None and gap <= IDLE_SECONDS else ALONE_SECONDS
                spans.append((moment - credit, moment))
                per_session[sid] = per_session.get(sid, 0.0) + credit
                index = date.fromtimestamp(moment).toordinal() - day0
                if 0 <= index < self.days:
                    active_days[index].add(sid)
                previous = moment
        day_seconds = [0.0] * self.days
        slots = [[0.0] * 24 for _ in range(7)]
        spans.sort()
        merged: list[list[float]] = []
        for begin, end in spans:  # side by side sessions count once on the clock
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([begin, end])
        for begin, end in merged:
            moment = max(begin, start)
            while moment < end:  # split at hour boundaries
                local = datetime.fromtimestamp(moment)
                hour_end = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
                piece = min(end, hour_end) - moment
                index = local.date().toordinal() - day0
                if 0 <= index < self.days:
                    day_seconds[index] += piece
                slots[local.weekday()][local.hour] += piece
                moment += piece
        return Activity(day_seconds, slots, per_session, [len(s) for s in active_days])

    def week_against_last(self) -> tuple[dict, dict] | None:
        """This week (today and the six days before) against the seven before; None if the
        window is too short to hold both."""
        if self.days < 14:
            return None
        today = self.last  # past midnight, date.today() ran the index out of the window (IndexError)
        weeks = []
        for back in (0, 7):
            last = today - timedelta(days=back)
            first = last - timedelta(days=6)
            use = self.between(first, last)
            index = [(first - self.first).days + i for i in range(7)]
            weeks.append({"tokens": use.total, "output": use.output,
                          "seconds": sum(self.activity.day_seconds[i] for i in index),
                          "sessions": len({e.session for e in self.window_events()
                                           if first <= date.fromtimestamp(e.at) <= last})})
        return weeks[0], weeks[1]


def _spans(stamps: dict[str, list[int]]) -> list[tuple[float, float]]:
    """The stretch of work each reply stands for: back to the previous reply of its session
    if that was within IDLE_SECONDS, else ALONE_SECONDS."""
    spans = []
    for times in stamps.values():
        previous = None
        for moment in sorted(times):
            gap = moment - previous if previous is not None else None
            spans.append((moment - (gap if gap is not None and gap <= IDLE_SECONDS else ALONE_SECONDS), moment))
            previous = moment
    return spans


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _moment(stamp) -> int | None:
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        return int(moment.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _scan(path: Path, entry: dict) -> None:
    """Add the rows written to a Claude Code transcript since entry["offset"].

    A row is [message id, epoch seconds, model, input, cache write, cache read, output,
    tools called, files edited]. A line without its newline is still being written;
    it is read next time.
    """
    rows: list[list] = entry["rows"]
    index = {row[0]: row for row in rows}
    offset = entry["offset"]
    with path.open("rb") as f:
        f.seek(offset)
        for line in f:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            if b'"usage"' not in line or b'"type":"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            counts = [_count(usage.get(name)) for name in FIELDS]
            at = _moment(record.get("timestamp"))
            if not any(counts) or at is None:
                continue  # an error placeholder, or a record with no date to put it on
            if isinstance(record.get("cwd"), str):
                entry["cwd"] = record["cwd"]  # the latest, as in the session list
            tools, files = _tool_calls(message.get("content"))
            key = str(message.get("id") or record.get("requestId") or f"{path.name}:{offset}")
            row = index.get(key)
            if row is None:
                row = index[key] = [key, at, str(message.get("model") or "?"), *counts, tools, files]
                rows.append(row)
            else:  # the same reply again, with its next content block
                row[3:7] = [max(a, b) for a, b in zip(row[3:7], counts)]
                row[7] += tools
                row[8] += files
    entry["offset"] = offset


def _tool_calls(content) -> tuple[list[str], list[str]]:
    tools, files = [], []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "?")
        tools.append(name)
        if name in EDIT_TOOLS:
            given = block.get("input") if isinstance(block.get("input"), dict) else {}
            target = given.get("file_path") or given.get("notebook_path")
            if isinstance(target, str):
                files.append(target)
    return tools, files


def _scan_codex(path: Path, entry: dict) -> None:
    """The same for a Codex rollout: a row per usage update (from the running totals) and
    per tool call."""
    rows: list[list] = entry["rows"]
    state = entry.setdefault("codex", {"total": [0, 0, 0], "model": "?"})
    offset = entry["offset"]
    with path.open("rb") as f:
        f.seek(offset)
        for line in f:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            if not any(word in line for word in (b'"token_count"', b'"turn_context"', b'"session_meta"',
                                                  b'_call"')):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict):
                continue
            kind = record.get("type")
            if kind == "session_meta":
                entry["session"] = str(payload.get("id") or entry["session"])
                entry["cwd"] = str(payload.get("cwd") or "").removeprefix("\\\\?\\")
                continue
            if kind == "turn_context":
                state["model"] = str(payload.get("model") or state["model"])
                continue
            at = _moment(record.get("timestamp"))
            if at is None:
                continue
            key = f"codex:{entry['session']}:{offset}"
            if kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
                now = [_count(total.get("input_tokens")), _count(total.get("cached_input_tokens")),
                       _count(total.get("output_tokens"))]
                before = state["total"]
                # A total that went down started over (a resumed session): count it whole.
                delta = now if any(a < b for a, b in zip(now, before)) else [a - b for a, b in zip(now, before)]
                state["total"] = now
                if any(delta):
                    fresh, cached, out = delta
                    rows.append([key, at, state["model"], max(0, fresh - cached), 0, cached, out, [], []])
            elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                name = str(payload.get("name") or "?")
                text = payload.get("input") or payload.get("arguments") or ""
                files = PATCHED.findall(text) if name == "apply_patch" and isinstance(text, str) else []
                rows.append([key, at, state["model"], 0, 0, 0, 0, [name], files])
    entry["offset"] = offset


def _files(cutoff: float) -> list[tuple[Path, str, str]]:
    """Session files written since cutoff: (path, session id, cli)."""
    from . import clis

    on = clis.enabled()
    found: list[tuple[Path, str, str]] = []
    if "claude" in on:
        projects = claude_dir() / "projects"
        found += [(p, p.stem, "claude") for p in _glob(projects, "*/*.jsonl")]
        found += [(p, p.parent.parent.name, "claude") for p in _glob(projects, "*/*/subagents/*.jsonl")]
    if "codex" in on:
        from . import codex

        found += [(p, p.stem[-36:], "codex") for p in _glob(codex.home() / "sessions", "*/*/*/rollout-*.jsonl")]
    out = []
    for path, sid, cli in found:
        try:
            if path.stat().st_mtime >= cutoff:
                out.append((path, sid, cli))
        except OSError:
            continue
    return out


def collect(days: float = 14) -> Report:
    """What happened over today and the days before it, `days` days in all."""
    with _lock:
        return _collect(days)


def _born(path: Path, stat) -> float:
    """When a transcript was begun: the file's birth time where the system keeps one (Windows, macOS),
    else the time of its first record. Linux has no birth time, and the change time it fell back to
    moves with every write: a parent that went on after its fork looked born after it, and the
    fork was given the parent's replies."""
    born = getattr(stat, "st_birthtime", None)
    if born:
        return born
    try:
        with path.open("rb") as f:
            for raw in f.read(64 * 1024).split(b"\n")[:50]:
                try:
                    stamp = json.loads(raw).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if isinstance(stamp, str):
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        pass
    return stat.st_mtime


def _collect(days: float) -> Report:
    span = max(1, math.ceil(days))
    first = date.today() - timedelta(days=span - 1)
    cutoff = datetime.combine(first, time.min).timestamp()
    cached = read_json(CACHE_FILE)
    entries = cached.get("entries", {}) if isinstance(cached, dict) and cached.get("format") == CACHE_FORMAT else {}
    fresh: dict[str, dict] = {}
    changed = not entries
    for path, sid, cli in _files(cutoff):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        entry = entries.get(key)
        if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime_ns:
            fresh[key] = entry
            continue
        if not entry or entry.get("offset", 0) > stat.st_size:  # new, or rewritten shorter
            entry = {"session": sid, "cli": cli, "main": cli == "codex" or path.stem == sid,
                     "rows": [], "offset": 0, "cwd": "",
                     # Created first wins a reply that a fork copied.
                     "born": _born(path, stat)}
        try:
            (_scan_codex if cli == "codex" else _scan)(path, entry)
        except OSError:
            continue
        entry.update(size=stat.st_size, mtime=stat.st_mtime_ns)
        fresh[key] = entry
        changed = True
    # Keep what a wider --days run read, as long as the file is still there.
    kept = {key: entry for key, entry in entries.items() if key not in fresh and Path(key).exists()}
    changed = changed or len(kept) + len(fresh) != len(entries)
    if changed:
        try:
            write_json(CACHE_FILE, {"format": CACHE_FORMAT, "entries": fresh | kept}, indent=None)
        except OSError:
            pass  # a read-only config folder only costs speed

    report = Report(first=first, days=span)
    seen: set[str] = set()
    for _, entry in sorted(fresh.items(), key=lambda item: (item[1].get("born", 0), item[0])):
        sid = entry["session"]
        report.clis[sid] = entry.get("cli", "claude")
        if entry.get("cwd") and (entry.get("main") or sid not in report.folders):
            report.folders[sid] = entry["cwd"]  # a subagent's folder only stands in for a missing one
        for message_id, at, model, *rest in entry["rows"]:
            if message_id in seen:
                continue
            seen.add(message_id)
            use = Tokens(*rest[:4])
            day = date.fromtimestamp(at).toordinal()
            if use.total:
                report.cells.setdefault((sid, day), Tokens()).add(use)
                if report.in_window(day):
                    report.models.setdefault(model, Tokens()).add(use)
            report.events.append(Event(sid, at, model, use, rest[4], rest[5]))
    report.events.sort(key=lambda e: e.at)
    return report
