"""Model and effort: the --model and --effort a Claude Code session starts with.

Claude Code keeps a resumed session on the model it ran on, but not on its effort: a resume starts
at your default effort again (checked with 2.1.317: a haiku session started at low came back at
medium). So when tabmancer opens a fresh session with an effort of its own, it gives the session
its id (--session-id) and keeps the pick here under that id, and whenever tabmancer resumes or
forks that session it passes the effort again.

Nothing is passed when nothing was picked: the session runs on your own Claude Code settings.
"""

from __future__ import annotations

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

    def __bool__(self) -> bool:
        return bool(self.model or self.effort)

    def flags(self) -> tuple[str, ...]:
        return (*(("--model", self.model) if self.model else ()), *(("--effort", self.effort) if self.effort else ()))

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
    picks[session_id] ={"model": pick.model, "effort": pick.effort, "at": datetime.now().isoformat(timespec="seconds")}
    if len(picks) > KEEP:
        picks = dict(sorted(picks.items(), key=lambda item: str(item[1].get("at", "")))[-KEEP:])
    write_json(FILE, {"picks": picks}, indent=1)
    global _cache
    stamp = _stamp()
    _cache = (stamp, picks) if stamp is not None else None


def recall(session_id: str, picks: dict[str, dict] | None = None) -> Pick:
    """What a session was opened with by tabmancer; an empty Pick for any other session."""
    found = (_load() if picks is None else picks).get(session_id) or {}
    effort = str(found.get("effort") or "")
    return Pick(str(found.get("model") or ""), effort if effort in EFFORTS else "")


# --- launches ------------------------------------------------------------------------------------

def fresh(launch: Launch, pick: Pick) -> Launch:
    """A new Claude Code session with this pick: it gets its id from tabmancer, so the pick can be
    given back when it is resumed. Other CLIs and an empty pick go as they are."""
    if not pick or launch.cli != "claude" or launch.mode is not Mode.NEW:
        return launch
    new_id = str(uuid.uuid4())
    remember(new_id, pick)
    return replace(launch, model=pick.model, effort=pick.effort, new_id=new_id)


def resumed(launch: Launch, picks: dict[str, dict] | None = None) -> Launch:
    """A resume or fork of a session tabmancer opened with an effort: that effort again (Claude Code
    keeps the model by itself). A team member's own flags already say it."""
    if launch.cli != "claude" or launch.mode not in (Mode.RESUME, Mode.AUTO, Mode.FORK) or launch.effort:
        return launch
    if "--effort" in launch.options:
        return launch
    effort = recall(launch.session_id, picks).effort
    return replace(launch, effort=effort) if effort else launch
