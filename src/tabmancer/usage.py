"""Plan usage limits, read from Claude Code's own `/usage` command, and Codex's from its
session files when Codex is turned on.

`claude -p /usage` prints the same panel as typing /usage in a session. It is a
local command (no model call), so it costs nothing beyond a second of CPU. Only
the "Current ...: N% used · resets ..." lines are used.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import config_dir

LINE = re.compile(r"^\s*Current (?P<what>[^:]+):\s*(?P<pct>\d+)% used(?:\s*·\s*resets (?P<reset>.+?))?\s*$")
RESET_FORMATS = ("%b %d, %I:%M%p", "%b %d, %I%p")


@dataclass
class Limit:
    label: str          # "session", "week", "week Fable" ...
    percent: int
    resets: datetime | None
    resets_text: str    # as Claude printed it, for when it cannot be parsed


def _label(what: str) -> str:
    what = what.strip().lower()
    if what == "session":
        return "session"
    if what.startswith("week"):
        inside = re.search(r"\((.+)\)", what)
        model = inside.group(1) if inside else ""
        return "week" if model in ("", "all models") else f"week {model.title()}"
    return what


def _reset_time(text: str, now: datetime) -> datetime | None:
    text = re.sub(r"\s*\(.*\)\s*$", "", text).strip()  # drop "(Europe/Istanbul)"
    for fmt in RESET_FORMATS:
        try:  # the year goes in explicitly: parsing a day without one is deprecated
            moment = datetime.strptime(f"{text} {now.year}", f"{fmt} %Y")
        except ValueError:
            continue
        if moment < now and (now - moment).days > 180:  # a reset early next year
            moment = moment.replace(year=now.year + 1)
        return moment
    return None


def parse(output: str, now: datetime | None = None) -> list[Limit]:
    now = now or datetime.now()
    limits = []
    for line in output.splitlines():
        match = LINE.match(line)
        if match:
            reset = (match.group("reset") or "").strip()
            limits.append(Limit(_label(match.group("what")), int(match.group("pct")),
                                _reset_time(reset, now) if reset else None, reset))
    return limits


def read_usage(timeout: float = 45) -> list[Limit]:
    """Current plan limits, or [] when unavailable (API key use, older CLI, offline)."""
    claude = shutil.which("claude")
    if not claude:
        return []
    try:
        # Cheap safety flags in case a CLI without /usage takes it for a prompt.
        result = subprocess.run(
            [claude, "-p", "/usage", "--model", "haiku", "--tools", "", "--no-session-persistence"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False, cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return parse(result.stdout) if result.returncode == 0 else []


def read_all() -> list[Limit]:
    """Claude's plan limits, and Codex's too when its sessions are listed (turned on with , or
    `tabmancer clis enable codex`): a Claude-only setup reads nothing more."""
    from . import clis

    return read_usage() + (read_codex() if "codex" in clis.enabled() else [])


# --- Codex ---------------------------------------------------------------------------------
# Codex has no command to ask. It writes its plan limits into its own session files instead:
# every token_count event carries rate_limits, a primary window and sometimes a secondary one,
# each as used_percent, window_minutes and resets_at (Unix seconds). So the newest reading is
# the one from your latest Codex turn.

CODEX_FILES = 20  # the newest session files looked through for a reading
WINDOW_WORDS = {300: "5h", 1440: "day", 10080: "week", 43200: "month"}


def codex_window(minutes: int) -> str:
    """'5h', 'week', 'month': a window's length in words."""
    if minutes in WINDOW_WORDS:
        return WINDOW_WORDS[minutes]
    return f"{minutes // 1440}d" if minutes % 1440 == 0 else f"{minutes // 60}h" if minutes % 60 == 0         else f"{minutes}m"


def codex_limits(rate_limits: dict, now: datetime) -> list[Limit]:
    """One reading's windows as limits. A window that has reset since starts again at 0 %: had
    Codex been used after it, a newer reading would say so."""
    limits = []
    for key in ("primary", "secondary"):
        window = rate_limits.get(key)
        if not isinstance(window, dict):
            continue
        try:
            percent = round(float(window["used_percent"]))
            minutes = int(window["window_minutes"])
            resets = datetime.fromtimestamp(int(window["resets_at"])) if window.get("resets_at") else None
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if resets is not None and resets <= now:
            percent, resets = 0, None
        limits.append(Limit(f"codex {codex_window(minutes)}", percent, resets,
                            f"{resets:%b %d, %H:%M}" if resets else ""))
    return limits


def read_codex(now: datetime | None = None) -> list[Limit]:
    """Codex's plan limits as its newest session file last read them; [] without one."""
    from . import codex

    now = now or datetime.now()
    try:
        files = sorted((codex.home() / "sessions").rglob("rollout-*.jsonl"), key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    for path in files[:CODEX_FILES]:
        try:
            lines = path.read_bytes().splitlines()
        except OSError:
            continue
        for raw in reversed(lines):  # the newest reading in the file is the one that counts
            if b'"rate_limits"' not in raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            found = payload.get("rate_limits") if isinstance(payload, dict) else None
            if isinstance(found, dict) and (limits := codex_limits(found, now)):
                return limits
    return []


# --- history and forecast -------------------------------------------------------------

HISTORY_FILE = "usage-history.jsonl"
KEEP_DAYS = 90
TRIM_BYTES = 1024 * 1024  # past this size the history is cut back to KEEP_DAYS
MIN_SPAN_SECONDS = 20 * 60  # a forecast needs readings at least this far apart


@dataclass
class Reading:
    at: datetime
    label: str
    percent: int
    resets: datetime | None


def record(limits: list[Limit], now: datetime | None = None) -> None:
    """Add a reading to the history. The dashboard reads the limits every five minutes;
    each reading is kept, so the stats can chart them and see how fast they fill."""
    if not limits:
        return
    now = now or datetime.now()
    line = {"at": now.isoformat(timespec="seconds"),
            "limits": [{"label": lim.label, "percent": lim.percent,
                        "resets": lim.resets.isoformat(timespec="minutes") if lim.resets else None}
                       for lim in limits]}
    path = config_dir() / HISTORY_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        if path.stat().st_size > TRIM_BYTES:
            keep = [r for r in _raw_history() if now - r[0] < timedelta(days=KEEP_DAYS)]
            path.write_text("".join(json.dumps(r[1]) + "\n" for r in keep), encoding="utf-8")
    except OSError:
        pass  # without a history the stats only show the current reading


def _raw_history() -> list[tuple[datetime, dict]]:
    try:
        lines = (config_dir() / HISTORY_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            data = json.loads(line)
            out.append((datetime.fromisoformat(data["at"]), data))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def history(days: float = 7, now: datetime | None = None) -> list[Reading]:
    """Readings from the last `days` days, oldest first."""
    since = (now or datetime.now()) - timedelta(days=days)
    readings = []
    for at, data in _raw_history():
        if at < since:
            continue
        for item in data.get("limits") or []:
            try:
                resets = datetime.fromisoformat(item["resets"]) if item.get("resets") else None
                readings.append(Reading(at, str(item["label"]), int(item["percent"]), resets))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(readings, key=lambda r: r.at)


@dataclass
class Forecast:
    label: str
    percent: int
    per_hour: float           # how fast it has been filling since the window began
    full_at: datetime | None  # when it reaches 100 % at that pace (None: not filling)
    resets: datetime | None

    @property
    def fills_first(self) -> bool:
        """It reaches 100 % before it resets."""
        return self.full_at is not None and (self.resets is None or self.full_at < self.resets)

    @property
    def at_reset(self) -> int | None:
        """Where it ends up by the reset at this pace."""
        if self.resets is None:
            return None
        hours = max(0.0, (self.resets - datetime.now()).total_seconds() / 3600)
        return min(100, round(self.percent + self.per_hour * hours))


def forecast(readings: list[Reading], label: str, now: datetime | None = None) -> Forecast | None:
    """From the readings of one limit since its last reset, how fast it fills and when it is full.

    None with too few readings to tell (less than twenty minutes of them).
    """
    mine = [r for r in readings if r.label == label]
    if not mine:
        return None
    latest = mine[-1]
    # The current window: readings with the same reset time, or after the last drop.
    window = [r for r in mine if r.resets == latest.resets] if latest.resets else mine
    for i in range(len(window) - 1, 0, -1):
        if window[i].percent < window[i - 1].percent:  # it reset in between
            window = window[i:]
            break
    if (window[-1].at - window[0].at).total_seconds() < MIN_SPAN_SECONDS:
        return None
    xs = [(r.at - window[0].at).total_seconds() / 3600 for r in window]
    ys = [r.percent for r in window]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread if spread else 0.0
    now = now or datetime.now()
    full_at = None
    if slope > 0.01 and latest.percent < 100:
        full_at = now + timedelta(hours=(100 - latest.percent) / slope)
    return Forecast(label, latest.percent, max(0.0, slope), full_at, latest.resets)


def short_window(label: str) -> bool:
    """A limit that resets within hours (Claude's session, Codex's 5h): charted over a day, not a week."""
    return label == "session" or (label.startswith("codex ") and label.endswith("h"))


def until(moment: datetime | None, now: datetime | None = None) -> str:
    """"1h12m", "3d", or "" when unknown."""
    if moment is None:
        return ""
    seconds = max(0, int((moment - (now or datetime.now())).total_seconds()))
    if seconds >= 2 * 86400:
        return f"{seconds // 86400}d"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
