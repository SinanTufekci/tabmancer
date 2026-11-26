"""What the c dialog says about the sessions stopped at a usage limit, with no UI behind it: the ways
on (now, when the limit lifts, handed over, no longer by themselves) and their order, each session's
line, what going on costs on another account and where it sends a conversation that ran on another
organisation. Parts are (words, style) as in teamwords. The terminal dashboard (app's LimitDialog)
puts them into rich Text, the desktop window into its own (desktop/limit.py); both say the same. What c
asks about (ask: the stopped, when they can go on, the plan, room, where a conversation goes) and the
dialog's rules (first_way, pick_refusal) are here too, one copy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import accountwords, actions
from .accounts import Account
from .actions import Note, clip
from .palette import AMBER, MUTED, TEXT
from .sessions import Session
from .teamwords import Part
from .tokens import human

BIG_CONTEXT = 300_000  # tokens: going on with this much on another account costs much of its limit

TICK_FIRST = Note("Tick a session first (Space, after Tab).", seconds=4)
KEY_PAIRS = [("Space", "tick"), ("Tab", "the sessions or the ways"), ("Enter", "choose"), ("Esc", "cancel")]
# Where c's dialog does not act (the desktop window for now): why it offers no "when the limit lifts"
NOT_HERE = ("Continuing when the limit lifts is set in the terminal dashboard (tabmancer): it continues "
            "them by itself then. This window does not act yet.")
# What "now" does with an open one: typed into its tab (the terminal dashboard), or, where the window does not
# type into tabs (the desktop window for now), its prompt put on the clipboard to paste there
NOW_OPEN_TYPED = "  an open one gets the prompt in its tab, a closed one opens in a new tab"
NOW_OPEN_CLIPBOARD = "  an open one's prompt goes on the clipboard to paste into its tab, a closed one opens in a new tab"


@dataclass(frozen=True)
class Ask:
    """What c asks about: the sessions stopped at a usage limit, when the first can go on ('now', 'at
    16:00'), whether all of them are set to continue at their reset (armed), the plan as last read and
    the other accounts (plan), whether the limit no longer stands in the way (room), those that ran on
    another organisation (away: id -> what their tool calls read) and where going on sends them (where)."""

    stopped: list[Session]
    when: str
    armed: bool
    plan: str
    room: bool
    away: dict[str, list[str]]
    where: str


def ask(sessions: dict[str, Session], limits: list, account: Account | None, others: list[str],
        armed: dict[str, datetime]) -> Ask | Note:
    """c: what its dialog asks about, or the note that nothing is stopped at a limit (actions.NONE_STOPPED).
    account: the Claude account signed in now; others: the other accounts' lines
    (accountwords.other_accounts_words of its key); armed: the sessions to continue at their reset."""
    stopped = actions.stopped_at_limit(sessions)
    if not stopped:
        return actions.NONE_STOPPED
    plan, room = actions.plan_words(limits, others)
    return Ask(stopped, actions.continue_when(stopped), all(s.session_id in armed for s in stopped), plan, room,
               away(account, stopped), accountwords.goes_to(account))


def first_way(ids: list[str], away: bool) -> int:
    """The way picked when the dialog opens (ids: the ways' ids, in order): the handover when going on
    would send a conversation to another organisation, else the first."""
    return ids.index("handover") if away and "handover" in ids else 0


def pick_refusal(ticked: list[str], way: str) -> Note | None:
    """A way chosen with no session ticked: tick one first (but 'off' needs none)."""
    return TICK_FIRST if not ticked and way != "off" else None


def ways(when: str, room: bool, armed: bool, away: bool, acts: bool = True,
         types: bool = True) -> list[tuple[str, list[Part]]]:
    """(id, its words) for each way, continuing now first with room (another account, or the limit
    lifted), else when the limit lifts; 'off' only for the armed. away: some ran on another
    organisation, so the handover says that only a digest goes. acts: False where the dialog's window
    never acts (the desktop window for now): no 'lift' or 'off', which only the dashboard that acts
    carries out (NOT_HERE says so). types: False where the window does not type into tabs (the desktop
    window for now): an open one's prompt goes on the clipboard (NOW_OPEN_CLIPBOARD)."""
    now = ("now", [("Continue the ticked now", f"bold {TEXT}"),
                   ("  you signed in with another account, or the limit has lifted\n", MUTED),
                   (NOW_OPEN_TYPED if types else NOW_OPEN_CLIPBOARD, MUTED)])
    later = ("lift", [("Continue the ticked when the limit lifts", f"bold {TEXT}"), (f"  {when}\n", MUTED),
                      ("  leave the dashboard open; each goes on by itself then", MUTED)])
    over = ("handover", [("Hand the ticked over", f"bold {TEXT}"),
                         ("  each a fresh session, its handover written from the transcript\n", MUTED),
                         ("  cheap on another account: the old context is not sent again", MUTED),
                         ("\n  only a digest goes: your prompts, its last replies and commits, not what its tools read"
                          if away else "", AMBER)])
    out = [now, later, over] if room else [later, now, over]
    if armed:
        out.append(("off", [("Do not continue them by themselves", f"bold {TEXT}"), ("  they wait for you", MUTED)]))
    return out if acts else [(way, parts) for way, parts in out if way not in ("lift", "off")]


def title_words(count: int, when: str) -> tuple[str, str, str]:
    return "Stopped at a usage limit", f"{count} session{'' if count == 1 else 's'}", f"it lifts {when}"


def biggest(stopped: list[Session]) -> list[Session]:
    """The biggest context first: what costs most to go on with."""
    return sorted(stopped, key=lambda s: -s.context)


def line_parts(session: Session) -> list[Part]:
    """'web-app  Fix the login loop  612k context', a big context in amber."""
    size = session.context
    return [(f"{clip(session.project, 18):<18}  ", TEXT), (f"{clip(session.title, 36):<36}  ", MUTED),
            (f"{human(size)} context" if size else "", AMBER if size >= BIG_CONTEXT else MUTED)]


def ticked_total(stopped: list[Session], ticked: list[str]) -> int:
    """The ticked ones' contexts, together, in tokens."""
    these = set(ticked)
    return sum(s.context for s in stopped if s.session_id in these)


def cost_parts(total: int) -> list[Part]:
    """What going on costs on another account, for the ticked ones (total: their contexts). The
    words are muted, the tokens bold (amber when big); nothing for none."""
    if not total:
        return []
    return [("On another account each one goes on by writing its whole context into the cache again: ", ""),
            (f"{human(total)} tokens", f"bold {AMBER}" if total >= BIG_CONTEXT else f"bold {TEXT}"),
            ((" for the ticked ones, much of a 5-hour limit at once. Handed over, a session starts afresh "
              "with a short handover."), "")]


def away(account: Account | None, stopped: list[Session]) -> dict[str, list[str]]:
    """Those that ran on another organisation than account (signed in now), each with what its tool
    calls read (accountwords.held): going on sends their conversation there."""
    return {s.session_id: accountwords.held(s) for s in accountwords.ran_elsewhere(account, stopped)}


def away_parts(ticked: list[str], away: dict[str, list[str]], where: str) -> list[Part]:
    """For the ticked ones that ran on another organisation (away: id -> what their tool calls read):
    where going on sends them. Nothing for none."""
    these = [sid for sid in ticked if sid in away]
    if not these:
        return []
    held = sorted({kind for sid in these for kind in away[sid]})
    one = len(these) == 1
    said = (f"{'One' if one else len(these)} of the ticked ran on another organisation: "
            f"{'its conversation goes' if one else 'their conversations go'} to {where} if "
            f"{'it goes' if one else 'they go'} on")
    return [(said, AMBER), (f" ({' / '.join(held)} content in {'it' if one else 'them'})" if held else "",
                            f"bold {AMBER}"), (".", AMBER)]
