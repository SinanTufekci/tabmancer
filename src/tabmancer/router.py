"""The router: the model, effort and context cap a task needs, suggested before its session opens.

One quick call to a small model (Haiku at low effort, no tools, no transcript left) reads the first
message and answers with a pick and why, in a sentence. Nothing is applied by it: the new-session
dialog shows the suggestion and Ctrl+R takes it, so the pick stays yours.

What a plan's usage goes on decides what the router weighs. Over two weeks of one user's many
parallel sessions, two thirds of it was the conversation read back from the cache with every reply,
at an average of 474k tokens a reply; a fifth was writing it there (a session left for more than an
hour writes all of it again), and only a seventh was what the model wrote. Opus and Sonnet read the
cache at the same price, so a cheaper model saves on the writing and the output only; a session
that stays short, or does less on the way (a lower effort makes fewer, shorter replies), saves on
everything. Hence a model, an effort and a cap together.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import summarize
from .config import read_json
from .picks import CAPS, EFFORTS, MODELS, Pick, cap_tokens

MODEL = os.environ.get("TABMANCER_ROUTER_MODEL", "haiku")
EFFORT = "low"
TIMEOUT = 90
MIN_WORDS = 3  # a first message shorter than this says too little to route
MAX_CHARS = 4000  # of the first message sent: its start says what the work is

# Said wherever Claude picks a model and an effort for a session: here, and for a team's members.
GUIDE = (
    "Models: opus for hard design, tricky debugging and code where a mistake costs much; sonnet for routine "
    "code, tests, docs and well-defined changes; haiku for quick look-ups, small edits and summaries; fable only "
    "for the hardest problems (2.5 times Opus's price). Efforts: low for look-ups and simple, well-defined edits, "
    "medium for routine work, high for most coding, xhigh for long, many-step or hard work, max only when being "
    "right matters more than the cost. Every reply reads the whole conversation again, and a higher effort makes "
    "more and longer replies, so pick the lowest that will do the work well.")
CAP_GUIDE = (
    "Context: how much of the conversation the session keeps word for word before Claude Code compacts it into "
    "a summary. 300k for most work; 400k when the work builds on a lot of earlier detail; 600k only when it needs "
    "much of a large codebase in view at once (a refactor across many files, a review of a whole project); 200k "
    "for a small, contained task.")
# "Pick: ...", and as markdown: "**Pick:** ...", "- Pick: ..."
PICK_LINE = re.compile(r"^[\s*_#>-]*pick[\s*_]*:[\s*_]*(.+)$", re.IGNORECASE | re.MULTILINE)
WHY_LINE = re.compile(r"^[\s*_#>-]*why[\s*_]*:[\s*_]*(.+)$", re.IGNORECASE | re.MULTILINE)
WORD = re.compile(r"[A-Za-z0-9.]+")
PROMPT_START = "You pick the Claude Code model, effort and context size"  # the demo knows the router by it


@dataclass(frozen=True)
class Route:
    pick: Pick
    why: str = ""

    def words(self) -> str:
        """'sonnet · high · 300k'."""
        return " · ".join(part for part in (self.pick.model, self.pick.effort, self.pick.cap) if part)


def worth_routing(message: str) -> bool:
    return len(message.split()) >= MIN_WORDS


def prompt(message: str, folders: list[str]) -> str:
    """What the router is asked: the first message, the folders it goes to, and how to pick."""
    text = message.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " (…)"
    many = len([line for line in text.splitlines() if line.strip()]) > 1
    where = ", ".join(folders) if folders else "(not chosen yet)"
    return (
        f"{PROMPT_START} for a new coding session, before it starts. It runs on the user's plan, whose usage "
        "limit fills with every reply, so the right pick is the cheapest one that will still do the work well.\n\n"
        + ("The sessions' first messages, one line each (each line is a session of its own, and one pick serves "
           "them all, so pick for the hardest):\n" if many else "The session's first message:\n")
        + f"<message>\n{text}\n</message>\nProject folder: {where}\n\n"
        f"{GUIDE}\n{CAP_GUIDE}\n\n"
        "Answer with exactly two lines, nothing else:\n"
        f"Pick: <{'|'.join(MODELS)}> <{'|'.join(EFFORTS)}> <{'|'.join(CAPS)}>\n"
        "Why: <at most 12 words, in the language of the message>")


def read(answer: str) -> Route | None:
    """The pick in the router's answer: its 'Pick:' line, else the first model, effort and cap
    words anywhere in it. None when it names neither a model nor an effort."""
    found = PICK_LINE.search(answer)
    words = [w.strip(".").lower() for w in WORD.findall(found[1] if found else answer)]  # "300k." ends a sentence
    model = next((w for w in words if w in MODELS), "")
    effort = next((w for w in words if w in EFFORTS), "")
    cap = next((w for w in words if w in CAPS), "") or next(
        (c for c in CAPS if any(cap_tokens(w) == cap_tokens(c) for w in words)), "")
    if not (model or effort):
        return None
    why = WHY_LINE.search(answer)
    return Route(Pick(model, effort, cap), " ".join(why[1].split()).rstrip(" .*_") if why else "")


def suggest(message: str, folders: list[str]) -> Route:
    """The router's pick for a first message. Raises summarize.SummaryError when there is none."""
    answer = summarize.ask(prompt(message, folders), MODEL, timeout=TIMEOUT, effort=EFFORT)
    route = read(answer)
    if route is None:
        raise summarize.SummaryError("The router's answer named no model or effort.")
    return route


def enabled() -> bool:
    """Whether the new-session dialog asks the router by itself: on unless TABMANCER_ROUTER=0 or
    "router": false in config.json. Ctrl+R asks it either way."""
    if os.environ.get("TABMANCER_ROUTER", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    saved = read_json("config.json")
    return not (isinstance(saved, dict) and saved.get("router") is False)
