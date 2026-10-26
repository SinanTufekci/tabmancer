"""Guess which sessions were still open when the computer last shut down.

When did it shut down: Windows' System event log says so. On Linux the journal knows each boot's
last entry (journalctl --list-boots), and wtmp the shutdowns themselves (last -x); on macOS, last.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

from .sessions import Exit, Session

# User32 1074: shutdown/restart requested. EventLog 6006: log service stopped.
# Kernel-General 13: OS shutting down. All three are written during a normal
# shutdown, including one with Fast Startup (which leaves the boot time stale).
# A crash or a power cut writes none of them; the boot after it writes EventLog 6008 (the last
# shutdown was unexpected) and Kernel-Power 41 (rebooted without shutting down cleanly), whose
# times stand for it: later than anything the sessions wrote before it.
_QUERY = ("*[System[(Provider[@Name='User32'] and EventID=1074)"
          " or (Provider[@Name='EventLog'] and EventID=6006)"
          " or (Provider[@Name='Microsoft-Windows-Kernel-General'] and EventID=13)"
          " or (Provider[@Name='EventLog'] and EventID=6008)"
          " or (Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41)]]")
BEFORE = timedelta(minutes=3)
AFTER = timedelta(minutes=1)
LOOKBACK = timedelta(hours=24)


def recent_shutdowns(limit: int = 30) -> list[datetime]:
    """Local times of recent shutdowns, newest first. Empty when none can be told."""
    if sys.platform == "darwin":
        return _mac_last(_output(["last", "shutdown"]))[:limit]
    if sys.platform != "win32":
        found = _journal_boots(_output(["journalctl", "--list-boots", "--no-pager", "-o", "json"]))
        if not found:  # an older systemd lists boots as text only
            found = _journal_boots_text(_output(["journalctl", "--list-boots", "--no-pager"]))
        return (found or _linux_last(_output(["last", "-x", "-F", "shutdown"])))[:limit]
    try:
        out = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{_QUERY}", f"/c:{limit}", "/rd:true", "/f:xml"],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    times = []
    for stamp in re.findall(r"SystemTime='(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", out):
        utc = datetime.fromisoformat(stamp + "+00:00")
        times.append(utc.astimezone().replace(tzinfo=None))
    return sorted(times, reverse=True)


def _output(argv: list[str]) -> str:
    """What a command prints, or "" when it is missing or fails."""
    if not shutil.which(argv[0]):
        return ""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _journal_boots(output: str) -> list[datetime]:
    """Each earlier boot's last journal entry, newest first: that is when it went down."""
    try:
        boots = json.loads(output) if output.strip() else []
    except ValueError:
        return []
    ends = [datetime.fromtimestamp(boot["last_entry"] / 1e6) for boot in boots if isinstance(boot, dict)
            and boot.get("index", 0) < 0 and isinstance(boot.get("last_entry"), (int, float))]
    return sorted(ends, reverse=True)


STAMP = re.compile(r"\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def _journal_boots_text(output: str) -> list[datetime]:
    """The same from the text listing: ' -1 <id> Wed 2026-10-22 08:00:01 +03 Wed 2026-10-22 17:30:12 +03'."""
    ends = []
    for line in output.splitlines():
        parts = line.split()
        stamps = STAMP.findall(line)
        if parts and parts[0].lstrip("-").isdigit() and int(parts[0]) < 0 and len(stamps) == 2:
            ends.append(datetime.strptime(stamps[1], "%Y-%m-%d %H:%M:%S"))
    return sorted(ends, reverse=True)


LAST_FULL = re.compile(r"(\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4})")
LAST_SHORT = re.compile(r"(\w{3} \w{3} [ \d]\d \d\d:\d\d)")


def _linux_last(output: str) -> list[datetime]:
    """wtmp's shutdowns: 'shutdown system down  6.8.0 Wed Oct 22 17:30:12 2026 - Thu Oct 23 ...'."""
    times = []
    for line in output.splitlines():
        found = LAST_FULL.search(line) if line.startswith("shutdown") else None
        if found:
            times.append(datetime.strptime(" ".join(found.group(1).split()), "%a %b %d %H:%M:%S %Y"))
    return sorted(times, reverse=True)


def _mac_last(output: str, now: datetime | None = None) -> list[datetime]:
    """macOS' last, which leaves the year out: 'shutdown  ~   Wed Oct 22 17:30'."""
    now = now or datetime.now()
    times = []
    for line in output.splitlines():
        found = LAST_SHORT.search(line) if line.startswith("shutdown") else None
        if not found:
            continue
        moment = datetime.strptime(f"{' '.join(found.group(1).split())} {now.year}", "%a %b %d %H:%M %Y")
        times.append(moment.replace(year=now.year - 1) if moment > now + timedelta(days=1) else moment)
    return sorted(times, reverse=True)


def open_at_shutdown(sessions: list[Session], shutdowns: list[datetime]) -> set[str]:
    """Session ids that look like they were open at the most recent shutdown.

    A session closed by a shutdown writes its final record moments before the
    shutdown event, so match last activity against that window. Older shutdowns
    are only considered within a day of the newest, so a restart that nobody
    restored from does not bring back sessions from last week.
    """
    ended = [s for s in sessions if s.exit in (Exit.CLEAN, Exit.UNKNOWN)]  # UNKNOWN: other CLIs
    picked: set[str] = set()
    if shutdowns:
        newest = shutdowns[0]
        for moment in shutdowns:
            if newest - moment > LOOKBACK:
                break
            picked = {s.session_id for s in ended if moment - BEFORE <= s.last_active <= moment + AFTER}
            if picked:
                break
    else:
        picked = _newest_cluster(ended)
    picked |= {s.session_id for s in sessions if went_down_with_the_machine(s, shutdowns)}
    return picked


def went_down_with_the_machine(session: Session, shutdowns: list[datetime]) -> bool:
    """A session whose process died without cleaning up, lately, and with the machine: a shutdown
    gave it no time, or a crash or power cut took it. One killed while the machine ran on (from
    Task Manager, or with a terminal that crashed) was ended on purpose, or can be ticked by hand,
    so it is not, and not told to carry on by itself. Where no shutdown can be read, every
    session killed lately counts."""
    if session.exit is not Exit.KILLED or datetime.now() - session.last_active >= LOOKBACK:
        return False
    return not shutdowns or shutdowns[0] + AFTER >= session.last_active


def _newest_cluster(ended: list[Session], gap: timedelta = timedelta(seconds=90)) -> set[str]:
    """Without shutdown records: two or more sessions that ended together."""
    ordered = sorted(ended, key=lambda s: s.last_active, reverse=True)
    cluster = ordered[:1]
    for session in ordered[1:]:
        if cluster[-1].last_active - session.last_active > gap:
            break
        cluster.append(session)
    return {s.session_id for s in cluster} if len(cluster) >= 2 else set()
