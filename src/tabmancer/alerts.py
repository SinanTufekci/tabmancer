"""What the dashboard says when sessions change, without a screen (G3 slice 8).

Two looks at the sessions, a poll apart, come to events: one turned to need you or finished its
reply, one stopped at a usage limit, one whose model fell back by itself, one whose context grew
crowded (once a session), and a team's new messages. Beside them: the ones a limit stopped (once a
session), Claude Code signed in with another account, sessions opened a while ago that never came
up, the messages waiting to be typed into a member's tab, the daily `claude update`; idle background
sessions, the week's Wrapped, a newer tabmancer, a plan limit nearly full and agy's models out. Each event
carries what to say (a note), what Bit does, whether the bell rings and whether it goes into the
desktop toast; the terminal dashboard (app.py) shows them as it always did, and a desktop window can
show the same. The words are returned as data, with a tone where Bit colours them ('amber', 'red',
'green', 'accent', or 'plain': Bit's own)."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from . import __version__, claudeupdate, context, handover, typein, updates, viewmodel, wrapped
from .actions import Note, Told, came_up_missing, limit_say
from .sessions import Exit, Session, Status
from .tokens import duration, human

NEEDS_YOU = "needs-you"      # was working, now asks you something
DONE = "done"                # was working, now waits for your next message
LIMIT = "limit"              # was working, now stopped at a usage limit (the toast says so)
FELL_BACK = "fell-back"      # another model took over by itself
CROWDED = "crowded"          # its context passed the handover point (once a session)
STOPPED = "stopped"          # Bit names the ones a usage limit stopped (once a session)
SIGNED_IN = "signed-in"      # Claude Code is signed in with another account now
NOT_UP = "not-up"            # opened a while ago from here, never seen open
TEAM_MESSAGE = "team-message"  # one member told another something
IDLE = "idle"                # background sessions sit idle (once a dashboard)
WRAPPED = "wrapped"          # the week is ready as a card (Friday from noon, once a week)
NEWER = "newer"              # a newer tabmancer is out (once a version)
LIMIT_NEAR = "limit-near"    # a plan limit at 85% or more (each reading)
AGY_OUT = "agy-out"          # every agy model in the chain is out (once until it is back)


@dataclass
class Bit:
    """What Bit does: celebrate or alert (words: the project), react with a mood, worry (a limit nearly
    full), or say the words."""

    how: str
    words: str
    mood: str = ""
    tone: str = "amber"
    seconds: float | None = None  # None: Bit's own default


@dataclass
class Event:
    """One thing to tell. note: the notification (None: none); bit: Bit's part; bell: rings the
    terminal's bell (where the dashboard shows the live list); toast: goes into the desktop note of the
    sessions that turned; desktop: a desktop note of its own, (title, body); once_keys: the sessions it
    is said for, never again (crowded, stopped).
    A team's message carries its team and the message itself (it flies along the team's wires)."""

    kind: str
    session: str = ""
    note: Told | None = None
    bit: Bit | None = None
    bell: bool = False
    toast: bool = False
    desktop: tuple[str, str] | None = None
    once_keys: tuple[str, ...] = ()
    team: str = ""
    message: object = None


# --- a session that changed ---------------------------------------------------------------------


def fallback_event(session: Session, who: str) -> Event:
    """Another model took over in an open session by itself (its safeguards stopped a reply, say): it
    goes on, weaker, unless you pick the model again, so it is said once, loud. who: the project, or a
    team member as 'team: member'."""
    return Event(FELL_BACK, session.session_id,
                 Told(f"{who}: {session.fell_back}. It goes on with that model: /model in its tab picks yours again.",
                      "warning", 15, title="Model fell back"),
                 Bit("alert", session.project), bell=True)


def session_events(old: Session | None, fresh: Session, who: str = "") -> list[Event]:
    """What one session's change says: its model fell back; it was working and a usage limit stopped
    it (the toast's); it was working and now needs you, or is done (a note, the bell, Bit, the toast)."""
    out: list[Event] = []
    sid = fresh.session_id
    if fresh.exit is Exit.RUNNING and fresh.fell_back and (old is None or old.fell_back != fresh.fell_back):
        out.append(fallback_event(fresh, who or fresh.project))
    was = viewmodel.state_of(old)[2] if old and old.exit is Exit.RUNNING else None
    now = viewmodel.state_of(fresh)[2] if fresh.exit is Exit.RUNNING else None
    if was == 2 and viewmodel.at_limit(fresh):
        out.append(Event(LIMIT, sid, toast=True))
    if was == 2 and now in (0, 1):  # it was working, now it needs you
        words = f"{fresh.project}: {viewmodel.state_of(fresh)[0]}"
        if now == 0 and fresh.status is Status.BACKGROUND:  # nowhere to answer but in it
            words += f" ({fresh.title}). It runs in the background: Enter on its row attaches to it."
        out.append(Event(NEEDS_YOU if now == 0 else DONE, sid, Told(words, seconds=10),
                         # done: waiting for your next message; blocked: it needs an answer
                         Bit("celebrate" if now == 1 else "alert", fresh.project), bell=True, toast=True))
    return out


def crowded_event(sessions: Iterable[Session], cap_of: Callable[[Session], int], default_cap: int,
                  told: set[str], now: datetime | None = None) -> Event | None:
    """Once per session, an open session whose context passed the handover point (told: the ones said
    already). Nothing is handed over unless you ask."""
    crowded = [s for s in sessions if s.exit is Exit.RUNNING and context.crowded(s, cap_of(s), default_cap)
               and s.session_id not in told]
    if not crowded:
        return None
    keys = tuple(s.session_id for s in crowded)
    if len(crowded) > 1:
        names = ", ".join(s.project for s in crowded[:4])
        return Event(CROWDED, "", Told(
            f"{len(crowded)} open sessions have crowded contexts ({names}). /compact in a tab goes on with the same "
            "work; h on one starts a new phase in a fresh session, and shows what is handed over.",
            seconds=12, title="Crowded sessions"), once_keys=keys)
    session = crowded[0]
    going = (f" has been going for {duration(((now or datetime.now()) - session.started).total_seconds())} and"
             if session.started else "")
    cap = cap_of(session)
    size = human(session.context) + (f", {context.share(session, cap):.0%} of its cap" if cap else "")
    return Event(CROWDED, session.session_id, Told(
        f"{session.project}{going} its context is {size}, and every reply reads all of it again. /compact in its "
        "tab goes on with the same work; h starts a new phase in a fresh session, and shows what is handed over.",
        seconds=12, title="Crowded session"),
        Bit("react", f"{session.project}: context {human(session.context)}", "crowded"), once_keys=keys)


def events(before: dict[str, Session], updates: dict[str, Session], who_of: Callable[[Session], str] | None = None,
           skip: Iterable[str] = ()) -> list[Event]:
    """Two looks a poll apart, as events: each changed session's in the poll's order (the crowded ones
    are crowded_event's, asked first). skip: sessions handled otherwise (a queued handover that just
    finished)."""
    out: list[Event] = []
    skipped = set(skip)
    for sid, fresh in updates.items():
        if sid not in skipped:
            out.extend(session_events(before.get(sid), fresh, who_of(fresh) if who_of is not None else ""))
    return out


def toast_words(turned: list[Session], others_note: Callable[[datetime | None], str]) -> tuple[str, str]:
    """The desktop note for the sessions that turned: (title, body). One stopped at a usage limit says
    when it lifts, and how the other accounts stood (others_note(when it lifts, or None))."""
    if len(turned) > 1:
        return (f"{len(turned)} sessions want you back",
                ", ".join(f"{s.project} ({viewmodel.state_of(s)[0]})" for s in turned))
    session = turned[0]
    if viewmodel.at_limit(session):
        ahead = session.resets and session.resets > datetime.now()
        when = f"at {viewmodel.lifts(session.resets)}" if ahead else "now"
        body = f"It lifts {when}. c in the dashboard continues it then."
        note = others_note(session.resets if ahead else None)
        if note:
            body += f"\n{note}"
        return f"{session.project} stopped at the usage limit", body
    return f"{session.project}: {viewmodel.state_of(session)[0]}", session.title


# --- limits and accounts ------------------------------------------------------------------------


def stopped_event(stopped: list[Session], others_for: Callable[[datetime], str],
                  now: datetime | None = None) -> Event:
    """Once per session, what Bit says of the ones a usage limit stopped (actions.limit_say)."""
    said = limit_say(stopped, others_for, now)
    return Event(STOPPED, bit=Bit("react" if said.mood else "say", said.words, said.mood, seconds=said.seconds),
                 once_keys=tuple(s.session_id for s in stopped))


def signed_in_event(free: int, who: str) -> Event:
    """Claude Code is signed in with another account now: how many a limit stopped can go on."""
    if free:
        return Event(SIGNED_IN, bit=Bit("react", f"Signed in as {who}: {free} stopped at a limit can go on. "
                                        "c continues them", "lifted", seconds=10))
    return Event(SIGNED_IN, bit=Bit("say", f"Signed in as {who}", tone="accent", seconds=6))


def not_up_event(ids: set[str], came_up: set[str], sessions: dict[str, Session]) -> Event | None:
    """The sessions opened a while ago that never showed up open (actions.came_up_missing)."""
    words = came_up_missing(ids, came_up, sessions)
    return Event(NOT_UP, note=Told(words, "warning", 15)) if words else None


# --- a team's messages --------------------------------------------------------------------------


def team_events(states: dict, seen: dict[str, set]) -> tuple[list[Event], dict[str, set]]:
    """The messages between a team's members since the last look (seen: each team's, as
    (sender, receiver, text)), and what is seen now. The first look at a team only takes note: its
    history does not fly."""
    out: list[Event] = []
    now_seen: dict[str, set] = {}
    for name, state in states.items():
        said = {(m.sender, m.receiver, m.text) for m in state.said}
        before = seen.get(name)
        if before is not None:
            out.extend(Event(TEAM_MESSAGE, team=name, message=m) for m in state.between_members()
                       if (m.sender, m.receiver, m.text) not in before)
        now_seen[name] = said
    return out, now_seen


# --- messages typed into a member's tab ---------------------------------------------------------


@dataclass
class Delivery:
    """A message waiting for a team member: closed (its note: the message is on the clipboard), or
    ready to type in now (closed None) since it waits for you."""

    sid: str
    who: str
    message: str
    session: Session | None
    closed: Told | None = None


def deliveries(notes: dict[str, tuple[str, str]], sessions: dict[str, Session]) -> list[Delivery]:
    """The messages to deal with now, in the order they were left: typed in once one waits for you,
    never while it works or asks you something (Enter would answer a question on its screen)."""
    out = []
    for sid, (who, message) in notes.items():
        session = sessions.get(sid)
        if session is None or session.exit is not Exit.RUNNING:
            out.append(Delivery(sid, who, message, session, Told(
                f"{who} closed before its message went in: the message is on the clipboard.", "warning", 12,
                clipboard=message)))
        elif session.live_state in handover.IDLE:
            out.append(Delivery(sid, who, message, session))
    return out


def type_note(session: Session, who: str, message: str) -> Told:
    """Type the message into the member's tab (blocking: run it off the screen's thread). Nothing could
    (not Windows, or its console would not take the keys): the message on the clipboard, and its tab
    to go to."""
    if typein.type_into(session.session_id, message):
        return Told(f"Sent to {who}.", seconds=5)
    return Told(f"Could not type into {who}'s tab: the message is on the clipboard (Ctrl+V, Enter).", "warning", 14,
                clipboard=message, go_to=session)


# --- claude update, once a day before sessions open ---------------------------------------------


def update_due(updating: bool, clis: Iterable[str]) -> bool:
    """Whether claude update is to run now, before these sessions open (their CLIs): not running
    already, one of them is Claude Code, and today's has not run (claudeupdate.due)."""
    return not updating and any(cli == "claude" for cli in clis) and claudeupdate.due()


def run_claude_update() -> tuple[claudeupdate.Outcome, list[str] | None]:
    """claude update (blocking), and with a new version, whether it still takes every flag tabmancer
    runs it with (doctor's check): what it no longer lists, or None."""
    outcome = claudeupdate.run()
    missing = None
    if outcome.changed and (claude := shutil.which("claude")):
        from .doctor import missing_from_cli

        missing = missing_from_cli(claude)
    return outcome, missing


def update_notes(outcome: claudeupdate.Outcome, missing: list[str] | None, waited: bool) -> list[Note]:
    """What the update says: how it went, when it changed anything, failed, or something waited for it;
    and the flags the new version no longer lists."""
    out = []
    if outcome.changed or not outcome.ok or waited:
        out.append(Note(outcome.words(), "information" if outcome.ok else "warning", 10 if outcome.ok else 20))
    if missing:
        out.append(Note(f"Claude Code {outcome.after} no longer lists what tabmancer runs it with: "
                        f"{'; '.join(missing)}. tabmancer doctor says more.", "error", 30))
    return out


def updating_note(what: str) -> Note:
    """While claude update runs: what waits for it."""
    return Note(f"Claude Code is updating: {what} once it is done.", seconds=8)


# --- Bit's other words: idle background sessions, the week, a newer tabmancer, the limits ---------


def idle_event(idle: int) -> Event | None:
    """Background sessions sitting idle (a count from a full poll): B tidies them up."""
    if not idle:
        return None
    return Event(IDLE, bit=Bit("react", f"{idle} background session{'s sit' if idle > 1 else ' sits'} idle. B tidies up.",
                               "idle", tone="plain", seconds=6))


def wrapped_event(now: datetime | None = None) -> Event | None:
    """On Fridays from noon, or the weekend after, once a week (wrapped.due): the week is ready (W).
    The caller marks it told (wrapped.told)."""
    if not wrapped.due(now):
        return None
    return Event(WRAPPED, note=Told("Claude Code Wrapped: your week as a card to share. W shows it.", seconds=15,
                                    title="Your week"),
                 bit=Bit("react", "Your week, wrapped! W shows it.", "lifted", tone="accent", seconds=10),
                 desktop=("Your week, wrapped", "Claude Code Wrapped is ready: W in the dashboard shows it."))


def newer_tabmancer(told: str | None) -> tuple[str, str] | None:
    """A newer tabmancer on PyPI (updates.news: asked once a day at most) not told of yet: (version,
    the command that upgrades to it). Blocking: run it off the screen's thread."""
    found = updates.news()
    return found if found and found[0] != told else None


def newer_event(version: str, command: str) -> Event:
    """A newer tabmancer, once in a note with the command that upgrades it, which Bit announces too."""
    return Event(NEWER, note=Told(f"tabmancer {version} is out (you have {__version__}). To upgrade: {command}",
                                  seconds=30, title="A newer tabmancer"),
                 bit=Bit("say", f"tabmancer {version} is out!", tone="green", seconds=8))


def usage_events(limits: list, bar: dict, told_agy_out: datetime | None) -> tuple[list[Event], datetime | None]:
    """A plan reading's words (limits: usage.Limit; bar: agy's chain, agy.bar_view, {} without agy): a
    limit at 85% or more, each reading; every agy model out, said once until it is back. And the
    time it is back, as told now."""
    out: list[Event] = []
    full = [lim for lim in limits if lim.percent >= 85]
    if full:
        worst = max(full, key=lambda lim: lim.percent)
        out.append(Event(LIMIT_NEAR, bit=Bit("worry", f"Careful: {worst.label} limit at {worst.percent}%", tone="red")))
    back = bar.get("all_out")
    if back is not None and back != told_agy_out:
        out.append(Event(AGY_OUT, bit=Bit("say", bar["said"], seconds=12)))
    return out, back
