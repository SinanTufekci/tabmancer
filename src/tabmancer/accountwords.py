"""What the dashboard says about the Claude accounts you use, with no UI behind it: the A dialog's
options, note and keys, where each account's sign-in page opens and how ←/→ steps through the places,
the sign-in itself (Claude Code's own `claude auth login`, its page opening in the picked browser
profile; tabmancer never touches tokens), and the words for a session that last ran on another
organisation. Parts are (words, style) as in teamwords. The terminal dashboard (app, accountsview)
puts them into rich Text, the desktop window into its own; both say the same."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from . import accounts, browsers, usage
from .accounts import Account
from .actions import Note
from .browsers import DEFAULT, Place, guess
from .launch import Launch, Mode
from .palette import CYAN, GREEN, MUTED, TEXT
from .sessions import Session, sensitive_calls
from .teamwords import Part
from .viewmodel import lifts

ANOTHER = "+"  # the option for an account not seen yet
TITLE = "Claude accounts"  # A's dialog: its heading, and in the window its title
# ←/→ when accounts.keep_opens_in could not keep the place (accounts.json there but unreadable, or not written).
NOT_KEPT = ("Not saved for next time: accounts.json could not be read or written, or another tabmancer was "
            "changing it, so it was left as it is. This sign-in still opens there.")


def seen_words(when: str) -> str:
    """'last on 2 Nov 14:05' from an ISO time; nothing when it cannot be read."""
    try:
        return f"last on {datetime.fromisoformat(when):%d %b %H:%M}"
    except ValueError:
        return ""


def shared(seen: list[tuple[Account, str]]) -> set[str]:
    """The emails listed more than once (one per organisation): shown with their organisation."""
    emails = [account.email for account, _ in seen]
    return {email for email in emails if emails.count(email) > 1}


def listed(current: Account | None, seen: list[tuple[Account, str]]) -> list[tuple[Account, str]]:
    """The accounts A lists: those seen (accounts.known, the latest first), and the one signed in now first
    when it has not been seen yet (seen when the dashboard's poll next keeps it)."""
    if current is not None and not any(account.key == current.key for account, _ in seen):
        return [(current, ""), *seen]
    return seen


def opens_in(seen: list[tuple[Account, str]], places: list[Place],
             picked: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(option id -> the email it signs in with, option id -> the place its page opens in): as picked
    for that email when the place is still here, else browsers.guess's; another account's, as picked
    for it or the system's own way."""
    known = {place.id for place in places}
    email: dict[str, str] = {ANOTHER: accounts.ANY_OTHER}
    place: dict[str, str] = {}
    for account, _ in seen:
        email[account.key] = account.email
        chosen = picked.get(account.email)
        place[account.key] = chosen if chosen in known else guess(account.email, places)
    chosen = picked.get(accounts.ANY_OTHER)
    place[ANOTHER] = chosen if chosen in known else DEFAULT
    return email, place


def next_place(ids: list[str], now: str, step: int) -> str:
    """←/→: the place step away from now, round the list (from the first when now is gone)."""
    return ids[((ids.index(now) if now in ids else 0) + step) % len(ids)]


class Turned(NamedTuple):
    """A place turned with ←/→ (turn): for which email (accounts.ANY_OTHER for another account), where it
    opens now, and the options it changed (the picked one first, then its email's other organisations)."""

    email: str
    place: str
    keys: list[str]


def turn(place: dict[str, str], email: dict[str, str], key: str, ids: list[str], step: int) -> Turned:
    """←/→ on option key: its page's next place among ids (next_place), set in place (option id -> place id,
    as opens_in made it) for it and for its email's other organisations, as they share the page. The caller
    keeps it for next time: accounts.keep_opens_in(turned.email, turned.place)."""
    now = next_place(ids, place.get(key, DEFAULT), step)
    its = email.get(key, key)
    keys = [key, *(other for other, other_email in email.items() if other_email == its and other != key)]
    for turned in keys:
        place[turned] = now
    return Turned(its, now, keys)


def place_words(places: list[Place], place: str) -> str:
    return next((p.words for p in places if p.id == place), "default browser")


def option_parts(account: Account, when: str, *, current: bool, shared: bool,
                 last: tuple[datetime, list[usage.Limit]] | None, place: str = "") -> list[Part]:
    """An account's option: its email (with its organisation when shared), plan, when last seen or
    signed in now, how its limits stood when last read, and where its page opens (place: its words,
    "" with no choice to show). Signed in now: nothing to sign in to, so neither of the last two."""
    where = account.org_words() if shared else ""
    out: list[Part] = [("● " if current else "  ", GREEN if current else MUTED), (account.email, f"bold {TEXT}"),
                       (f"  {where}" if where else "", CYAN), (f"  {account.plan}" if account.plan else "", MUTED),
                       ("  signed in now" if current else f"  {seen_words(when)}", GREEN if current else MUTED)]
    if current:
        return out
    if last is not None:
        out += [("\n    ", MUTED), (usage.last_words(*last), MUTED)]
    return out + opens_parts(place)


def another_parts(place: str = "") -> list[Part]:
    return [("  Another account", f"bold {TEXT}"), ("  one not listed here", MUTED), *opens_parts(place)]


def opens_parts(place: str) -> list[Part]:
    return [("\n    opens in ", MUTED), (place, CYAN)] if place else []


def title_words(current: Account | None) -> str:
    return f"signed in as {current.email}" if current else "not signed in"


def note(has_places: bool, stopped: int) -> str:
    """The A dialog's note: how the sign-in goes, and what c does for sessions stopped at a limit."""
    if has_places:
        out = ("Claude Code signs you in, in a new tab, its page where you pick: a browser profile signed in "
               "to that account takes one click, a private window asks you to sign in. tabmancer reads "
               "only your browsers' profile names.")
    else:
        out = ("Claude Code signs you in, in a new tab: your browser opens with the account filled in, and "
               "every session goes on with it. tabmancer keeps only the emails it has seen.")
    if stopped:
        out += (f" {stopped} session{' is' if stopped == 1 else 's are'} stopped at a usage limit: "
                "once you are signed in with another account, c continues "
                f"{'it' if stopped == 1 else 'them'} at once.")
    return out


def key_pairs(has_places: bool) -> list[tuple[str, str]]:
    return [("Enter", "sign in with it"), *((("←/→", "where it opens"),) if has_places else ()), ("Esc", "cancel")]


def sign_in_launch(email: str, place: str) -> Launch:
    """Claude Code's own sign-in (`claude auth login`, with --email for a known account), in a new
    tab, its page opening where place says (browsers.login_environment sets BROWSER for it)."""
    return Launch(title=f"Sign in{f': {email}' if email else ''}", cwd=str(Path.home()), mode=Mode.NEW,
                  session_id="", options=("auth", "login", *(("--email", email) if email else ())),
                  env=browsers.login_environment(place))


def where_words(login: Launch, places: list[Place], place: str) -> str:
    """Where its page opens: the picked place's words, or 'your browser' with no BROWSER set."""
    return next((p.words for p in places if p.id == place and login.env), "your browser")


def open_failed_note(error: OSError) -> Note:
    return Note(f"Could not open the tab: {error}", severity="error", seconds=15)


def read_failed_note(error: Exception) -> Note:
    """The accounts could not be read for A (a file it reads, broken): said, and A pressed again reads them again."""
    return Note(f"Could not read the Claude accounts: {str(error) or type(error).__name__}", severity="error",
                seconds=15)


def signing_in_note(email: str, where: str, left: list[str]) -> Note:
    """Said once the sign-in tab opens; left: the command, when there was no terminal to open it in."""
    if left:
        return Note(f"No terminal to open it in. Run it yourself: {left[0]}", seconds=15)
    return Note(f"Signing in{f' as {email}' if email else ''} in a new tab: finish it in {where}. "
                "The top bar says when it is done.", seconds=8)


def sign_in(email: str, place: str, places: list[Place], open_tabs: Callable[[list[Launch]], list[str]],
            demo: bool = False) -> Note | None:
    """Claude Code's own sign-in (sign_in_launch) opened in a new tab by open_tabs (the caller's, looked up
    when called: the demo stands in for it), and what to say of it: the tab opening, where its page opens,
    or why it did not. demo: its stand-in says what would have opened, so only a command left is said."""
    login = sign_in_launch(email, place)
    try:
        left = open_tabs([login])
    except OSError as error:
        return open_failed_note(error)
    if left or not demo:
        return signing_in_note(email, where_words(login, places, place), left)
    return None


def ran_elsewhere(account: Account | None, sessions: list[Session]) -> list[Session]:
    """Those that last ran on another organisation than account (signed in now): going on with
    them (or a handover, or a summary) sends their conversation there. Unknown either way: none."""
    here = account.org_id if account is not None else ""
    return [s for s in sessions if here and s.org and s.org != here]


def goes_to(account: Account | None) -> str:
    """Where a conversation goes now: 'your personal account', 'the Example Labs organisation'."""
    if account is None:
        return "this account"
    if account.personal:
        return "your personal account"
    return f"the {account.org_name} organisation" if account.org_name else "this organisation"


def held(session: Session) -> list[str]:
    """What its tool calls read: those of its tail as read, and any further back (sensitive_calls)."""
    return list(dict.fromkeys([*session.sensitive, *sensitive_calls(session.path)]))


def elsewhere_note(session: Session, what: str, account: Account | None) -> Note | None:
    """One line, never a stop: what of a session that ran on another organisation goes to this one.
    None when it ran here (or either is unknown)."""
    if not ran_elsewhere(account, [session]):
        return None
    read = held(session)
    content = f" ({' / '.join(read)} content in it)" if read else ""
    return Note(f"{session.project} ran on another organisation: {what} goes to {goes_to(account)}{content}.",
                severity="warning", seconds=10)


def _name(account: Account, shared_emails: set[str]) -> str:
    return accounts.words(replace(account, plan=""), 60, org=account.email in shared_emails)  # no plan


def other_accounts_note(account: Account | None, signed_as: str | None, opens: datetime | None) -> tuple[str, str]:
    """For a limit's note: how the other Claude accounts stood when last read (usage.other_accounts),
    named as the top bar names them. ("", "") with none read."""
    last = usage.last_by_account()
    shared_emails = accounts.shared_emails()
    others = [(_name(other, shared_emails), *last[other.key]) for other, _ in accounts.known()
              if other.key != signed_as and other.key in last]
    this = _name(account, shared_emails) if account is not None else "this account"
    return usage.other_accounts(this, opens, others, lifts)


def other_accounts_words(signed_as: str | None) -> list[str]:
    """How the other Claude accounts' limits stood when last read, one line each, for where you
    choose to go on: 'b@example.com: session 12% · week 40% · read 45m ago'. None read: nothing."""
    last = usage.last_by_account()
    shared_emails = accounts.shared_emails()
    return [f"{accounts.words(other, 60, org=other.email in shared_emails)}: {usage.last_words(*last[other.key])}"
            for other, _ in accounts.known() if other.key != signed_as and other.key in last]
