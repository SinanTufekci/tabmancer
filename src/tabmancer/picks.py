"""Model, effort and context: the --model, --effort and --autocompact a Claude Code session starts with.

Claude Code keeps a resumed session on the model it ran on, but not on its effort: a resume starts
at your default effort again (checked with 2.1.317: a haiku session started at low came back at
medium). So when tabmancer opens a fresh session with an effort of its own, it gives the session
its id (--session-id) and keeps the pick here under that id, and whenever tabmancer resumes or
forks that session it passes the effort again.

The context cap is kept the same way, as a resume forgets it too (checked with 2.1.319: a session
started with --autocompact 300k came back at a 1M window). It is the most conversation a session
keeps before Claude Code compacts it into a summary (it compacts some 33k before the cap). Every
reply reads the whole conversation again, so a long one is where most of a plan's limit goes;
tabmancer starts its sessions with a cap (300k unless you change it) and says where one is set.

Nothing is passed for a model or an effort not picked: the session runs on your own settings.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from .config import config_dir, read_json, write_json
from .launch import Launch, Mode

FILE = "picks.json"
KEEP = 2000  # picks remembered; the oldest go first
DEFAULT = "your default"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("opus", "sonnet", "haiku", "fable")  # Claude Code's aliases for the latest of each
CAPS = ("200k", "300k", "400k", "600k")  # context caps to pick from (Claude Code takes 100k to 1M)
NO_CAP = "auto"  # no cap: Claude Code's own window (its word for it, as in --autocompact auto)
CAP_KEY = "context_cap"  # in config.json: the cap a new session starts with, or "auto"
DEFAULT_CAP = "300k"
CAP_ROOM = 50_000  # a session resumed under a cap of its own has this much room below it at least
CAP_WORDS = {
    "200k": "compacts sooner: small, contained tasks",
    "300k": "room for most work, at a fraction of a long session's cost",
    "400k": "more of the work kept word for word",
    "600k": "wide work: much of a large codebase in view at once",
    NO_CAP: "Claude Code's own: the model's whole window (1M for most), compacted near its end",
}
# One line each, said where you pick. Prices are the API's per token; a plan's limits weigh
# the models in much the same order.
MODEL_WORDS = {
    DEFAULT: "whatever your Claude Code settings say",
    "opus": "strong all-round: the usual pick for coding and for a team's lead",
    "sonnet": "about half Opus's price: routine coding, tests, docs",
    "haiku": "a fraction of the price: quick look-ups, small edits, summaries",
    "fable": "the most capable, about 2.5 times Opus's price: the hardest problems",
}
EFFORT_WORDS = {
    DEFAULT: "whatever your Claude Code settings say",
    "low": "short and quick: simple, well-defined tasks",
    "medium": "lighter on your limit: routine work",
    "high": "a good balance for most work",
    "xhigh": "thorough: coding and long, many-step work",
    "max": "as deep as it goes: when being right matters more than the cost",
}
TAG = re.compile(r"(?:^|\s)@([A-Za-z0-9][\w.\-\[\]]*)\s*$")  # "tests: checks it @sonnet @high"


@dataclass(frozen=True)
class Pick:
    model: str = ""   # an alias such as sonnet, or a full name; empty: your default
    effort: str = ""  # one of EFFORTS; empty: your default
    cap: str = ""     # the context cap, such as 300k, or NO_CAP; empty: not decided (default_cap())

    def __bool__(self) -> bool:
        return bool(self.model or self.effort or self.cap)

    def flags(self) -> tuple[str, ...]:
        return (*(("--model", self.model) if self.model else ()), *(("--effort", self.effort) if self.effort else ()),
                *cap_flags(self.cap))

    def words(self) -> str:
        """'sonnet · high', 'sonnet', 'high effort', or '' when nothing was picked."""
        if self.model and self.effort:
            return f"{self.model} · {self.effort}"
        return self.model or (f"{self.effort} effort" if self.effort else "")

    def short(self) -> str:
        """For a narrow place such as a team card: 'sonnet/high'."""
        return f"{self.model}/{self.effort}" if self.model and self.effort else self.words()


def split(text: str) -> tuple[str, Pick]:
    """The @words at the end of a line taken off it: an effort level is the effort, any other word
    the model. "api: endpoints @sonnet @high" -> ("api: endpoints", Pick("sonnet", "high"))."""
    model = effort = ""
    while found := TAG.search(text):
        word = found[1]
        if word.lower() in EFFORTS:
            effort = effort or word.lower()
        else:
            model = model or word
        text = text[:found.start()]
    return text, Pick(model, effort)


def tags(pick: Pick) -> str:
    """The pick as it is written at the end of a line: ' @sonnet @high'."""
    return "".join(f" @{word}" for word in (pick.model, pick.effort) if word)


# --- the context cap -----------------------------------------------------------------------------

CAP_WORD = re.compile(r"^(\d+(?:\.\d+)?)\s*([km]?)$", re.IGNORECASE)


def cap_tokens(cap: str) -> int:
    """'300k' -> 300000, '1m' -> 1000000, '200' -> 200000 (Claude Code's shorthand); 0 for no cap
    or a word that is none, or one outside the 100k to 1M Claude Code takes."""
    found = CAP_WORD.match(cap.strip()) if cap else None
    if not found:
        return 0
    number, unit = float(found[1]), found[2].lower()
    tokens = int(number * (1_000_000 if unit == "m" else 1000 if unit == "k" or number < 1000 else 1))
    return tokens if 100_000 <= tokens <= 1_000_000 else 0


def cap_flags(cap: str) -> tuple[str, ...]:
    """--autocompact for a cap; nothing for NO_CAP or none decided."""
    return ("--autocompact", cap) if cap_tokens(cap) else ()


def default_cap() -> str:
    """The cap a new session starts with: TABMANCER_CONTEXT_CAP, else the one in config.json, else
    300k. "auto" (or "off") there leaves Claude Code's own window."""
    saved = read_json("config.json")
    chosen = os.environ.get("TABMANCER_CONTEXT_CAP") or (saved.get(CAP_KEY) if isinstance(saved, dict) else None)
    word = str(chosen or DEFAULT_CAP).strip().lower()
    if word in (NO_CAP, "off", "none", "0"):
        return NO_CAP
    return word if cap_tokens(word) else DEFAULT_CAP


def decided(pick: Pick) -> Pick:
    """The pick with its cap decided: the default where none was picked."""
    return pick if pick.cap else replace(pick, cap=default_cap())


def model_name(model: str) -> str:
    """A model's full name as people say it: claude-opus-5-5 -> Opus 5.5; an alias stays as it is."""
    if not model or model.startswith("<"):  # <synthetic>: Claude Code's own records, no model
        return ""
    parts = model.removeprefix("claude-").split("[")[0].split("-")
    if len(parts) < 2 or not parts[0].isalpha():
        return model
    numbers = [p for p in parts[1:] if p.isdigit() and len(p) < 4]  # a date suffix is left out
    return f"{parts[0].capitalize()} {'.'.join(numbers)}".strip()


# --- what was picked, by session id ------------------------------------------------------------

_cache: tuple[tuple, dict[str, dict]] | None = None  # (the file, its time and size), what it held


def _stamp() -> tuple | None:
    path = config_dir() / FILE
    try:
        found = path.stat()
    except OSError:
        return None
    return str(path), found.st_mtime_ns, found.st_size


def _load() -> dict[str, dict]:
    """The picks, read again only when the file changed: the dashboard asks on every cursor move.
    Writes in quick succession can leave the time as it was (seen on Windows), so remember() keeps
    the cache itself and the size is in the stamp too."""
    global _cache
    stamp = _stamp()
    if stamp is None:
        return {}
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]
    data = read_json(FILE)
    picks = data.get("picks") if isinstance(data, dict) else None
    found = {str(k): v for k, v in picks.items() if isinstance(v, dict)} if isinstance(picks, dict) else {}
    _cache = (stamp, found)
    return found


def remember(session_id: str, pick: Pick) -> None:
    picks = dict(_load())
    picks[session_id] = {"model": pick.model, "effort": pick.effort, "cap": pick.cap,
                         "at": datetime.now().isoformat(timespec="seconds")}
    if len(picks) > KEEP:
        picks = dict(sorted(picks.items(), key=lambda item: str(item[1].get("at", "")))[-KEEP:])
    write_json(FILE, {"picks": picks}, indent=1)
    global _cache
    stamp = _stamp()
    _cache = (stamp, picks) if stamp is not None else None


def recall(session_id: str, picks: dict[str, dict] | None = None) -> Pick:
    """What a session was opened with by tabmancer; an empty Pick for any other session."""
    found = (_load() if picks is None else picks).get(session_id) or {}
    effort, cap = str(found.get("effort") or ""), str(found.get("cap") or "")
    return Pick(str(found.get("model") or ""), effort if effort in EFFORTS else "",
                cap if cap == NO_CAP or cap_tokens(cap) else "")


def cap_of(session_id: str, picks: dict[str, dict] | None = None) -> int:
    """The cap a session was last opened with by tabmancer, in tokens; 0 for none."""
    return cap_tokens(recall(session_id, picks).cap)


# --- launches ------------------------------------------------------------------------------------

def fresh(launch: Launch, pick: Pick) -> Launch:
    """A new Claude Code session with this pick, its cap decided (default_cap() where none was
    picked): it gets its id from tabmancer, so the pick can be given back when it is resumed.
    Other CLIs go as they are."""
    if launch.cli != "claude" or launch.mode is not Mode.NEW:
        return launch
    pick = decided(pick)
    if not (pick.model or pick.effort) and pick.cap == NO_CAP == default_cap():
        return launch  # nothing of its own: a resume gives it nothing either
    new_id = str(uuid.uuid4())
    remember(new_id, pick)
    return replace(launch, model=pick.model, effort=pick.effort, cap=pick.cap, new_id=new_id)


def resumed(launch: Launch, picks: dict[str, dict] | None = None, context: int = 0) -> Launch:
    """A resume or fork of a session tabmancer opened with an effort or a cap: those again (Claude
    Code keeps the model by itself). A team member's own flags already say theirs. A session never
    opened with a cap (one from before caps, or opened elsewhere) gets the default one only while
    its context (tokens) is well under it, so that nothing is compacted the moment it opens; one
    past it goes on as it was, and the dashboard suggests handing it over instead."""
    if launch.cli != "claude" or launch.mode not in (Mode.RESUME, Mode.AUTO, Mode.FORK):
        return launch
    known = recall(launch.session_id, picks)
    effort = launch.effort or ("" if "--effort" in launch.options else known.effort)
    cap = launch.cap or ("" if "--autocompact" in launch.options else known.cap)
    if not cap and "--autocompact" not in launch.options:
        default = default_cap()
        cap = default if cap_tokens(default) and context < cap_tokens(default) - CAP_ROOM else ""
    return replace(launch, effort=effort, cap=cap) if (effort, cap) != (launch.effort, launch.cap) else launch


def note_opened(launches: list[Launch]) -> None:
    """Called as launches open: a resumed session's cap kept under its id, so it gets the same cap
    when it is resumed again (it may have grown past the point where a new one is given)."""
    found = _load()
    for launch in launches:
        if launch.cli != "claude" or launch.mode not in (Mode.RESUME, Mode.AUTO) or not launch.cap:
            continue
        known = recall(launch.session_id, found)
        if known.cap != launch.cap:
            remember(launch.session_id, replace(known, effort=known.effort or launch.effort, cap=launch.cap))
            found = _load()
