"""A first message or a team's goal made clearer before it goes: Ctrl+E in the new-session dialog
and the new-team form.

One call on your plan (Sonnet at a low effort, no tools, no transcript left, see summarize.ask)
rewrites what you wrote so a session can act on it: what is to be done, where, what must hold and
when it is done, as far as your words say so. Nothing is made up, and it stays in your language.
The rewrite replaces the text in its box as an edit of its own, so Ctrl+Z there puts yours back.

In the new-session dialog every line is a session of its own, so a rewrite keeps one line for each
of yours; one that does not is not used.
"""

from __future__ import annotations

import os
import re

from . import summarize

MODEL = os.environ.get("TABMANCER_IMPROVE_MODEL", "sonnet")
EFFORT = "low"
TIMEOUT = 120
MIN_WORDS = 3  # shorter than this says too little to improve on
MAX_CHARS = 12000  # a plan pasted in longer than this is sent cut

RULES = (
    "Rewrite it so that a coding agent can act on it at once: say plainly what is to be done, where (files, "
    "parts, commands named in it), what must hold or stay as it is, and how to tell it is done, but only as far "
    "as the text says or plainly means. Never add requirements, features, tools or steps of your own, and never "
    "guess names that are not there. Keep every detail, path, name, number and quoted text as written. Keep the "
    "language it is written in. Keep it about as short as the work allows: no headings, no preamble, no sign-off. "
    "If it is already clear, change little.")
FENCE = re.compile(r"^```[a-z]*\n(.*)\n```$", re.DOTALL)


class NotImproved(Exception):
    """Why there is no rewrite to use."""


def worth_improving(text: str) -> bool:
    return len(text.split()) >= MIN_WORDS


def _cut(text: str) -> str:
    text = text.strip()
    return text[:MAX_CHARS] + " (…)" if len(text) > MAX_CHARS else text


def session_prompt(message: str, folders: list[str]) -> str:
    """What is asked for the new-session dialog's first message: each line a session's."""
    lines = [line for line in message.splitlines() if line.strip()]
    where = ", ".join(folders) if folders else "(not chosen yet)"
    if len(lines) > 1:
        about = (f"Below are the first messages of {len(lines)} new Claude Code sessions, one line each: each line "
                 "is a session of its own. Rewrite each line on its own. Answer with exactly "
                 f"{len(lines)} lines, in the same order, each one a whole message on a single line (no line "
                 "breaks inside it, no numbering, no blank lines), and nothing else.")
    else:
        about = ("Below is the first message of a new Claude Code session. Answer with the rewritten message on a "
                 "single line (no line breaks), and nothing else.")
    return (f"You improve prompts written for Claude Code, a coding agent, before they are sent. {about}\n\n"
            f"{RULES}\n\nProject folder: {where}\n<message>\n{_cut(message)}\n</message>")


def goal_prompt(goal: str, folder: str) -> str:
    """What is asked for a team's goal: every member gets all of it."""
    return ("You improve prompts written for Claude Code, a coding agent, before they are sent. Below is the goal "
            "of a team of Claude Code sessions working together in one project: each member is given all of it, "
            "with its own part. Answer with the rewritten goal and nothing else; it may take several lines, as "
            "plain text (a short list is fine where the goal has several parts).\n\n"
            f"{RULES}\n\nProject folder: {folder or '(not chosen yet)'}\n<goal>\n{_cut(goal)}\n</goal>")


def tidy(answer: str) -> str:
    """The answer without a code fence or quotes around it, or a 'Rewritten:' before it."""
    text = answer.strip()
    fenced = FENCE.match(text)
    if fenced:
        text = fenced[1].strip()
    text = re.sub(r"^(?:\*\*)?(?:rewritten|improved)(?: (?:prompt|message|goal))?(?:\*\*)?\s*:(?:\*\*)?\s*", "",
                  text, flags=re.IGNORECASE)
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'“”" and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    return text


def improve_message(message: str, folders: list[str]) -> str:
    """The first message rewritten, line for line. Raises NotImproved (or summarize.SummaryError)."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    answer = tidy(summarize.ask(session_prompt(message, folders), MODEL, timeout=TIMEOUT, effort=EFFORT))
    got = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(lines) == 1 and got:
        got = [" ".join(got)]  # one session: a rewrite on several lines is still its one message
    if len(got) != len(lines):
        raise NotImproved(f"the rewrite had {len(got)} line{'s' * (len(got) != 1)} for your {len(lines)}: "
                          "each line is a session, so it is not used")
    return "\n".join(got)


def improve_goal(goal: str, folder: str) -> str:
    """The team's goal rewritten. Raises NotImproved (or summarize.SummaryError)."""
    answer = tidy(summarize.ask(goal_prompt(goal, folder), MODEL, timeout=TIMEOUT, effort=EFFORT))
    if not answer:
        raise NotImproved("the answer was empty")
    return answer
