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
from collections.abc import Callable
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
    account: str = ""  # the Claude account's key (accounts.Account.key); "" in a line from before


def record(limits: list[Limit], now: datetime | None = None, account: str = "") -> None:
    """Add a reading to the history. The dashboard reads the limits every five minutes;
    each reading is kept, so the stats can chart them and see how fast they fill. account: the key
    of the Claude account signed in while it was read (the limits are that account's)."""
    if not limits:
        return
    now = now or datetime.now()
    line = {"at": now.isoformat(timespec="seconds"),
            **({"account": account} if account else {}),
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



SHARED_FOR = 15 * 60  # a team's monitor takes the dashboard's reading up to this old


def _limits(data: dict) -> list[Limit] | None:
    """A history line's limits, or None when it cannot be read."""
    try:
        return [Limit(str(item["label"]), int(item["percent"]),
                      datetime.fromisoformat(item["resets"]) if item.get("resets") else None, "")
                for item in data["limits"]]
    except (KeyError, TypeError, ValueError):
        return None


def latest(max_age: float = SHARED_FOR, now: datetime | None = None, account: str | None = None) -> list[Limit] | None:
    """The newest reading in the history (the dashboard's, every five minutes) when it is at most
    max_age seconds old, else None: a team's monitor shows it instead of asking claude again. With an
    account (its key), only a reading of that account's, or one from before readings named theirs."""
    raw = _raw_history()
    if not raw:
        return None
    at, data = raw[-1]
    if not 0 <= ((now or datetime.now()) - at).total_seconds() <= max_age:
        return None
    if account and data.get("account") not in (None, "", account):
        return None  # another account's: signed in with this one since
    return _limits(data)


def history(days: float = 7, now: datetime | None = None, account: str | None = None) -> list[Reading]:
    """Readings from the last `days` days, oldest first: with an account (its key), that account's
    only (and none from before readings named theirs)."""
    since = (now or datetime.now()) - timedelta(days=days)
    readings = []
    for at, data in _raw_history():
        whose = str(data.get("account") or "")
        if at < since or (account is not None and whose != account):
            continue
        for item in data.get("limits") or []:
            try:
                resets = datetime.fromisoformat(item["resets"]) if item.get("resets") else None
                readings.append(Reading(at, str(item["label"]), int(item["percent"]), resets, whose))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(readings, key=lambda r: r.at)


def accounts_read(days: float = 7, now: datetime | None = None) -> set[str]:
    """The accounts with readings in the last `days` days ("" for lines from before they were named)."""
    since = (now or datetime.now()) - timedelta(days=days)
    return {str(data.get("account") or "") for at, data in _raw_history() if at >= since}


def chart_history(days: float = 7, now: datetime | None = None) -> list[Reading]:
    """The readings the stats chart and forecast from: see chart_account."""
    from . import accounts

    signed_in = accounts.claude()
    return history(days, now, chart_account(signed_in.key if signed_in else None, days, now))


def chart_account(current: str | None, days: float = 7, now: datetime | None = None) -> str | None:
    """Whose readings the stats chart: the account signed in now, once another account's readings are
    in the history too (two accounts' limits on one line would mean nothing); else all of them."""
    named = accounts_read(days, now) - {""}
    return current if current and named - {current} else None


def last_by_account() -> dict[str, tuple[datetime, list[Limit]]]:
    """Each account's newest reading in the history (its key -> when, its Claude limits): what the
    other account had left when you last used it. Lines from before readings named an account are
    not in it."""
    found: dict[str, tuple[datetime, list[Limit]]] = {}
    for at, data in _raw_history():
        limits = _limits(data) if data.get("account") else None
        claude = [limit for limit in limits or [] if not limit.label.startswith("codex ")]
        if claude:
            found[str(data["account"])] = (at, claude)
    return found


def last_words(at: datetime, limits: list[Limit], now: datetime | None = None) -> str:
    """An account's last reading in words: 'session 100% · opens 16:00 · week 40% · read 2h ago'.
    A full limit whose reset time has passed is 'probably open': nothing has read it since."""
    now = now or datetime.now()
    parts = []
    for limit in limits:
        if limit.label not in ("session", "week") and limit.percent < 100:
            continue  # a model's own week only when it is the one in the way
        passed = limit.resets is not None and limit.resets <= now
        if limit.percent >= 100 and passed:
            parts.append(f"{limit.label} probably open")
        elif passed:
            parts.append(f"{limit.label} reset since")
        elif limit.percent >= 100 and limit.resets is not None:
            day = "" if limit.resets.date() == now.date() else f"{limit.resets:%a} "
            parts.append(f"{limit.label} 100% · opens {day}{limit.resets:%H:%M}")
        else:
            parts.append(f"{limit.label} {limit.percent}%")
    ago = until(now, at) if at <= now else ""
    return " · ".join([*parts, f"read {ago} ago" if ago and ago != "0m" else "read just now"])


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


def forecast(readings: list[Reading], label: str, now: datetime | None = None,
             account: str | None = None) -> Forecast | None:
    """From the readings of one limit since its last reset, how fast it fills and when it is full:
    with an account (its key), from that account's readings only.

    None with too few readings to tell (less than twenty minutes of them).
    """
    mine = [r for r in readings if r.label == label and (account is None or r.account == account)]
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


def full_until(limits: list[Limit], now: datetime | None = None) -> datetime | None:
    """When an account's last reading has room again: the latest reset of its limits that were full
    and have not reset since; None when none of them stands in the way (or when it does not say)."""
    now = now or datetime.now()
    resets = [limit.resets for limit in limits if limit.percent >= 100 and limit.resets and limit.resets > now]
    return max(resets, default=None)


def other_accounts(this: str, opens: datetime | None, others: list[tuple[str, datetime, list[Limit]]],
                   when: Callable[[datetime], str], now: datetime | None = None) -> tuple[str, str]:
    """What a limit's note says of the other accounts, from their last readings (others: name, when
    read, limits): (the whole line, the part after this account's own). One with room: 'a@x has room
    (session 12%, 45m ago): A to switch', after 'this: limit reached (opens 16:00)'. All full: 'both
    full · earliest 16:00' ('all' past two). None read: ("", "")."""
    now = now or datetime.now()
    if not others:
        return "", ""
    room = [(name, at, limits) for name, at, limits in others if full_until(limits, now) is None]
    if room:
        name, at, limits = min(room, key=lambda o: next((lim.percent for lim in o[2] if lim.label == "session"), 0))
        session = next((lim for lim in limits if lim.label == "session"), None)
        left = ("session probably open" if session.resets and session.resets <= now and session.percent >= 100
                else f"session {session.percent}%") if session else ""
        ago = until(now, at) if at <= now else ""
        said = ", ".join(part for part in (left, f"{ago} ago" if ago and ago != "0m" else "just now") if part)
        part = f"{name} has room ({said}): A to switch"
        own = f"{this}: limit reached{f' (opens {when(opens)})' if opens else ''}"
        return f"{own} · {part}", part
    back = [moment for moment in (opens, *(full_until(limits, now) for _, _, limits in others)) if moment]
    part = f"{'both' if len(others) == 1 else 'all'} full{f' · earliest {when(min(back))}' if back else ''}"
    return part, part


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
