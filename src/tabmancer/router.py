"""The router: the model, effort and context cap a task needs, suggested before its session opens.

One quick call to a small model (Haiku at low effort, no tools, no transcript left) reads the first
message and answers with a pick and why, in a sentence. It is asked only when you press Ctrl+R in
the new-session dialog, once the first message is written, and its pick goes into the rows there,
yours to change.

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

from . import clis, summarize
from .picks import CAPS, EFFORTS, MODELS, Pick, cap_tokens

CLIS = ("claude", "agy")  # the CLIs it picks for; never Codex
MODEL = os.environ.get("TABMANCER_ROUTER_MODEL", "haiku")
EFFORT = "low"
TIMEOUT = 90
AGY_QUOTA_TIMEOUT = 25  # seconds for agy's /usage, read before an agy pick
MIN_WORDS = 3  # a first message shorter than this says too little to route
MAX_CHARS = 4000  # of the first message sent: its start says what the work is

# Said wherever Claude picks a model and an effort for a session: here, and for a team's members.
GUIDE = (
    "Models: opus for hard design, tricky debugging and code where a mistake costs much; sonnet for routine "
    "code, tests, docs and well-defined changes; haiku for quick look-ups, small edits and summaries; fable only "
    "for the hardest problems (twice Opus's price for what it writes; plans give it at most half the week). Efforts: low for look-ups and simple, well-defined edits, "
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
XHIGH = re.compile(r"\b(?:x|extra)[\s-]?high\b", re.IGNORECASE)  # "x-high", "extra high": xhigh
NOT_THIS = {"not", "no", "than", "over", "avoid", "skip"}  # "not opus", "rather than opus", "cheaper than max"
NOT_THESE = {("instead", "of")}  # two words, as "of" alone is no "not": "an effort of medium"
TEMPLATE = re.compile(r"<[^>]*\|")  # the answer's form said back: "Pick: <opus|sonnet|...> ..."
MAX_WHY = 100  # characters of the why shown under the rows
MAX_FOLDERS = 6  # folder names in the prompt; more say little and cost words
RETRY = "\n\nYour last answer could not be read. Answer with exactly the two lines asked for, nothing else."
# The guess, when the router cannot be asked: words that start a word in the message (English and
# Turkish), "debug" finding "debugging" too.
HARD_WORDS = re.compile(
    r"\b(refactor|architect|redesign|debug|races?\b|race condition|deadlock|concurren|migrat|security|vulnerab|"
    r"performance|memory leak|mimari|tasarla|hata ayıkla|yeniden yapılandır|güvenlik)", re.IGNORECASE)
LIGHT_WORDS = re.compile(
    r"\b(typo|rename|comment|readme|docstring|bump|look up|lookup|what is|what does|where is|explain|"
    r"summari[sz]e|yazım hatası|yeniden adlandır|nedir|nerede|açıkla|özetle)", re.IGNORECASE)
WIDE_WORDS = re.compile(r"\b(across|whole|entire|every file|all files|codebase|tüm|bütün)", re.IGNORECASE)
SHORT_TASK = 25  # words: a light word in a message longer than this is part of more work
LONG_TASK = 120  # words: a message this long spells out a detailed task
PROMPT_START = "You pick the Claude Code model, effort and context size"  # the demo knows the router by it


@dataclass(frozen=True)
class Route:
    pick: Pick
    why: str = ""
    cli: str = "claude"  # the CLI the pick is for: its model names are that CLI's
    guessed: bool = False  # made here from the message's words, the router not reached: worth asking again

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
    text = re.sub(r"</?\s*message\s*>", "", text, flags=re.IGNORECASE)  # a pasted tag cannot end the frame
    many = len([line for line in text.splitlines() if line.strip()]) > 1
    names = list(dict.fromkeys(name for name in folders if name))  # ticked twice, said once
    more = f" and {len(names) - MAX_FOLDERS} more" if len(names) > MAX_FOLDERS else ""
    where = ", ".join(names[:MAX_FOLDERS]) + more if names else "(not chosen yet)"
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


def _named(text: str) -> tuple[str, str, str]:
    """The first model, effort and cap words in text, leaving out any said not to be the pick
    ("not opus", "rather than max", "instead of opus")."""
    words = [w.strip(".").lower() for w in WORD.findall(XHIGH.sub("xhigh", text))]  # "300k." ends a sentence
    kept = [w for i, w in enumerate(words)
            if not (i and words[i - 1] in NOT_THIS) and not (i > 1 and (words[i - 2], words[i - 1]) in NOT_THESE)]
    model = next((w for w in kept if w in MODELS), "")
    effort = next((w for w in kept if w in EFFORTS), "")
    cap = next((w for w in kept if w in CAPS), "") or next(
        (c for c in CAPS if any(cap_tokens(w) == cap_tokens(c) for w in kept)), "")
    return model, effort, cap


def read(answer: str) -> Route | None:
    """The pick in the router's answer: its 'Pick:' line, else the first model, effort and cap
    words in the rest of it (never in its 'Why:' line, whose "low risk" is no effort). A Pick line
    that only says the form back ("<opus|sonnet|...>") is no pick. In the rest, a model must be
    named: "the cost is low" alone is no pick. None when there is none."""
    found = next((line for line in PICK_LINE.finditer(answer) if not TEMPLATE.search(line[1])), None)
    model, effort, cap = _named(found[1]) if found else ("", "", "")
    if not (model or effort):
        rest = "\n".join(line for line in WHY_LINE.sub("", PICK_LINE.sub("", answer)).splitlines()
                         if not TEMPLATE.search(line))
        model, effort, cap = _named(rest)
        if not model:
            return None
    why = WHY_LINE.search(answer)
    said = " ".join(why[1].split()).rstrip(" .*_") if why else ""
    if len(said) > MAX_WHY:
        said = said[:MAX_WHY - 1].rstrip() + "…"
    return Route(Pick(model, effort, cap), said)


def guess(message: str, reason: str = "") -> Route:
    """A pick from the message's own words, for when the router cannot be asked: the same message
    always gets the same pick. A small or look-up task in a short message gets Haiku (first: "rename
    the design doc" is small, whatever it names), work that is hard to get right (a refactor, a
    redesign, a bug hunt) Opus, the rest Sonnet; a long message is a detailed task. Marked as a
    guess, and why the router was not asked, so it is never taken for the router's own."""
    text = message.lower()
    words = len(text.split())
    hard, light = HARD_WORDS.findall(text), LIGHT_WORDS.findall(text)
    if light and words <= SHORT_TASK:
        pick, about = Pick("haiku", "low", "200k"), f"'{light[0]}' is a small task"
    elif hard:
        wide = bool(WIDE_WORDS.search(text))
        pick, about = Pick("opus", "high", "400k" if wide else "300k"), f"'{hard[0]}' is work to get right"
    elif words > LONG_TASK:
        pick, about = Pick("sonnet", "high", "300k"), "a long, detailed task"
    else:
        pick, about = Pick("sonnet", "medium", "300k"), "routine work, by its words"
    said = f"guess ({reason}): {about}" if reason else f"guess: {about}"
    return Route(pick, said if len(said) <= MAX_WHY else said[:MAX_WHY - 1].rstrip() + "…", guessed=True)


def _agy_route() -> Route:
    """An Antigravity session's pick: its model chain's model while its quota lasts (never a Claude
    model through agy), not one weighed by the message, so no model is asked. agy takes neither
    an effort (it is in the model's name) nor a context cap. Run off the UI thread: the quota is
    read afresh first (agy's free /usage, some 3 s), and a quota that cannot be read leaves the
    one saved before."""
    from . import agy

    agy.refresh_quota(timeout=AGY_QUOTA_TIMEOUT)
    model, why = agy.pick_model()
    if not model:
        raise summarize.SummaryError(why or "No Antigravity model is usable now.")
    if agy.is_claude(model):
        raise summarize.SummaryError(f"{model} is a Claude model: tabmancer never runs Claude through agy.")
    return Route(Pick(model), why, cli="agy")


def suggest(message: str, folders: list[str], cli: str = "claude") -> Route:
    """The router's pick for a first message, for sessions of the given CLI. An answer that cannot
    be read is asked for once more (a small model now and then answers in prose); a call that fails
    or times out is not, as it would fail the same way: the pick is then guessed from the message
    (Route.guessed). Raises summarize.SummaryError for a CLI it does not pick for (Codex), or an
    agy pick with no usable model."""
    if cli == "agy":
        return _agy_route()
    if cli != "claude":
        raise summarize.SummaryError(f"The router picks for Claude Code and Antigravity, not {clis.label(cli)}.")
    asked = prompt(message, folders)
    try:
        for again in (False, True):
            route = read(summarize.ask(asked + (RETRY if again else ""), MODEL, timeout=TIMEOUT, effort=EFFORT))
            if route is not None:
                return route
    except (summarize.SummaryError, OSError) as problem:
        return guess(message, _short(str(problem) or type(problem).__name__))
    return guess(message, "the router's answer named no pick")


def _short(problem: str) -> str:
    """A failure said in a few words: its first line, cut."""
    line = (problem.strip().splitlines() or [""])[0]
    return line if len(line) <= 40 else line[:39].rstrip() + "…"
