"""The team monitor: `tabmancer team watch <team>`, a team at work on a screen of its own.

It is the team view (teamview.TeamScreen) with more on it: the plan limits and agy's quota beside
the team's name, who needs you, and the picked member in two lines; on a wide screen every member
in a table beside the conversation. It shows the team and does what you ask of it there, nothing
by itself: the dashboard under it (SessionPicker with watch) is never shown.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Static

from . import agy, teams, teamtree, teamwatch
from . import viewmodel as VM
from .keys import MONITOR_KEYS  # the list itself; tests import it from here too
from .keysview import KeysScreen
from .safework import work
from .sessions import Session, folder_key
from .teamview import LineDialog, TeamGraph, TeamScreen, TeamsDialog
from .theme import AMBER, CYAN, GREEN, MUTED, RED, TEXT, keys_in
from .tokens import human

WIDE = 180  # columns from which every member shows in a table beside the conversation
SIDE = 72  # that table's width


def tone_style(tone: str) -> str:
    from .app import TONE  # the dashboard's colours for its words; loaded already when a monitor runs

    return TONE.get(tone, "")


def _percent_style(percent: float) -> str:
    return f"bold {RED}" if percent >= 85 else AMBER if percent >= 60 else TEXT


def quota_line(limits: list, agy_view: dict, with_agy: bool, now: datetime | None = None) -> Text:
    """The plan limits as the dashboard read them last ('session 42% · week 18%') and, for a team
    with agy members, where agy's chain stands ('Neon out till Tue 09:02 · Hydrogen 31%'). A quota read longer ago than agy.STALE says so instead: it tells nothing of now."""
    now = now or datetime.now()
    out = Text()
    for i, limit in enumerate(limits):
        out.append(" · " if i else "Claude ", style=MUTED)
        out.append(f"{limit.label} {limit.percent}%", style=_percent_style(limit.percent))
    if not with_agy or not agy_view:
        return out
    if out:
        out.append("   ")
    read_at = agy_view.get("read_at")
    if read_at is None:
        return out.append("agy quota not read", style=MUTED)
    if now - read_at > agy.STALE:
        return out.append(f"agy quota read {read_at:%H:%M}", style=MUTED)
    out.append("agy ", style=MUTED)
    for i, standing in enumerate(agy_view.get("chain") or []):
        if i:
            out.append(" · ", style=MUTED)
        name, left, back = standing.get("name") or standing.get("model", "?"), standing.get("left"), standing.get("back")
        if back is not None:
            when = f"{back:%H:%M}" if back.date() == now.date() else f"{back:%a %H:%M}"
            out.append(f"{name} out till {when}", style=RED)
        elif left is None:
            out.append(f"{name} ?", style=MUTED)
        else:
            out.append(f"{name} {left:.0f}%", style=_percent_style(100 - left))
    return out


def needs_line(view: VM.TeamView, palette: dict[str, str]) -> Text | None:
    """'NEEDS YOU  lead: waiting for an answer · dash: done   j picks them in turn'; None when
    nobody waits for you."""
    waiting = [m for m in view.members if m.needs_you]
    if not waiting:
        return None
    out = Text("NEEDS YOU  ", style=f"bold {RED}")
    for i, member in enumerate(waiting):
        if i:
            out.append(" · ", style=MUTED)
        out.append(member.name, style=f"bold {palette.get(member.name, TEXT)}")
        out.append(f": {member.label}", style=tone_style(member.tone))
    return out.append("   j picks them in turn", style=MUTED)


def _model_of(member: VM.MemberView) -> str:
    """'claude · opus 5.5 · high', 'agy · Neon (High)': as the team view and the desktop monitor say it."""
    return f"{member.cli} · {VM.model_words(member)}"


def _facts(member: VM.MemberView) -> list[tuple[str, str]]:
    """What the picked member's first line and the table say of it, as (words, style)."""
    out: list[tuple[str, str]] = [(_model_of(member), CYAN if member.cli != "claude" else MUTED)]
    if member.fell_back:
        out.append((f"fell back: {member.fell_back}", AMBER))
    if member.context is not None:
        out.append((f"context {member.context:.0%}", _percent_style(member.context * 100)))
    if member.tokens:
        out.append((f"{human(member.tokens)} tokens", MUTED))
    if member.bus is not None and member.state != "not-started":
        since = member.bus.get("since")
        if member.bus.get("listening"):
            out.append((f"bus: listening since {since:%H:%M}" if since else "bus: listening", GREEN))
        elif member.bus.get("runner"):
            out.append(("bus: runner alive", MUTED))
        else:
            out.append(("bus: not listening", AMBER))
    if member.unread:
        out.append((f"{member.unread} unread", AMBER))
    if member.git:
        git = member.git
        words = git.get("branch") or "git"
        words += f" +{git['changed']}" if git.get("changed") else ""
        words += f" ↑{git['ahead']}" if git.get("ahead") else ""
        out.append((words, MUTED))
    if member.quota and member.quota.get("back") is not None:
        out.append((f"out of quota till {member.quota['back']:%H:%M}", RED))
    if member.model_note:
        out.append((member.model_note, MUTED))
    return out


def picked_lines(view: VM.TeamView, picked: str | None, state: teams.TeamState, palette: dict[str, str],
                 width: int) -> Text:
    """Two lines under the cards: the picked member (or everyone) and its newest message, each cut
    to the width with an ellipsis."""
    member = next((m for m in view.members if m.name == picked), None) if picked else None
    if member is None:
        first = Text.assemble(("everyone", f"bold {TEXT}"), (f"  ·  {view.open} of {len(view.members)} open", MUTED))
        if view.tokens:
            first.append(f"  ·  {human(view.tokens)} tokens", style=MUTED)
        said = state.said[-1] if state.said else None
        second = Text.assemble((f"last {said.at:%H:%M}  ", MUTED), teamtree.word(said, said.sender, palette)) \
            if said is not None and said.at else Text("Nothing said yet.", style=MUTED)
    else:
        first = Text.assemble((member.name, f"bold {palette.get(member.name, TEXT)}"),
                              ("  ·  ", MUTED), (member.label, tone_style(member.tone)))
        for words, style in _facts(member):
            first.append("  ·  ", style=MUTED)
            first.append(words, style=style)
        said = teamtree.last_word(state, member.name)
        second = Text(f"last {said.at:%H:%M}  " if said is not None and said.at else "", style=MUTED)
        second.append_text(teamtree.word(said, member.name, palette) if said is not None
                           else Text(f"Nothing said to or by {member.name} yet.", style=MUTED))
    for line in (first, second):
        line.no_wrap = True
        line.truncate(max(1, width), overflow="ellipsis")
    return Text("\n").join([first, second])


def members_table(view: VM.TeamView, palette: dict[str, str], picked: str | None = None) -> Table:
    """Every member on a row of its own, for a wide screen: who, what it is doing, on what, and the
    rest of what the picked line says. The picked member's row is marked (▶): the two lines that
    say who is picked are not shown beside the table."""
    table = Table(box=None, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("MEMBER", style=TEXT, no_wrap=True, header_style=f"bold {MUTED}")
    table.add_column("NOW", no_wrap=True, header_style=f"bold {MUTED}")
    table.add_column("ON", no_wrap=False, header_style=f"bold {MUTED}", ratio=1)
    for member in view.members:
        name = Text(("▶ " if member.name == picked else "  ") + ("★ " if member.lead else "") + member.name,
                    style=f"bold {palette.get(member.name, TEXT)}")
        if member.name == picked:
            name.stylize("reverse")
        facts = Text()
        for i, (words, style) in enumerate(_facts(member)):
            if i:
                facts.append(" · ", style=MUTED)
            facts.append(words, style=style)
        table.add_row(name, Text(member.label, style=tone_style(member.tone)), facts)
    return table


def keys_line(width: int) -> Text:
    """The keys under the monitor: on two rows at 120 columns, one key never parted from its words."""
    return keys_in(width, ("← →", "member"), ("Enter", "its tab"), ("j", "needs you"), ("p", "write"),
                   ("s", "start the rest"), ("a", "add"), ("d", "take off"), ("g", "goal"),
                   ("h", "hand the team over"), ("H", "a member"), ("x", "close"), ("T", "teams"),
                   ("D", "dashboard"), ("?", "keys"), ("q", "quit"))


# Work in this window's threads that a q cuts short: (worker group, what it is called).
UNDER_WAY = {"handover": "a handover being written", "team-handover": "the team's handover being written",
             "team-background": "members starting in the background", "tabs-open": "tabs being opened"}


def quit_note(lost: list[str], running: list[str]) -> str:
    """The warning a first q gives when something is unfinished. Measured: a thread under way keeps the
    process (and so this tab) until it ends, and its last step, on the app, finds the app gone."""
    note = "q again quits"
    if lost:
        note += ", and loses " + " and ".join(lost)
    if running:
        under_way = " and ".join(running)
        one = len(running) == 1
        note += (f". {under_way[0].upper()}{under_way[1:]} still {'ends' if one else 'end'} first (this tab waits "
                 f"for {'it' if one else 'them'}), but what {'it' if one else 'they'} would open next does not open")
    return note + "."


class TeamMonitor(TeamScreen):
    """The team view as a monitor: more on it, and it is all there is (q quits, Esc unpicks)."""

    DEFAULT_CSS = """
    TeamMonitor #monitor-top { height: auto; padding: 1 2 0 2; }
    TeamMonitor #monitor-top #team-head { padding: 0; width: 1fr; }
    TeamMonitor #monitor-quota { width: auto; max-width: 70%; text-align: right; }
    TeamMonitor #monitor-needs { padding: 0 2; height: auto; }
    TeamMonitor #monitor-needs.none { display: none; }
    TeamMonitor #monitor-picked { padding: 0 2; height: 2; color: $text; }
    TeamMonitor #monitor-body { height: 1fr; }
    TeamMonitor #monitor-talk { width: 1fr; }
    TeamMonitor #monitor-side { width: 72; padding: 1 2 0 0; display: none; }
    TeamMonitor.wide #monitor-side { display: block; }
    TeamMonitor.wide #monitor-picked { display: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "clear_pick", "Everyone"),
        Binding("q", "quit_monitor", "Quit"),
        Binding("j", "next_needs", "Needs you"),
        Binding("p", "message", "Write to it"),
        Binding("T", "other_team", "Teams"),
        Binding("D", "dashboard", "Dashboard"),
        Binding("question_mark", "keys", "Keys"),
    ]

    def __init__(self, team: teams.Team, accent: str) -> None:
        super().__init__(team, accent)
        self.view: VM.TeamView | None = None
        self.quitting = False  # q pressed once while a handover is on its way: again quits all the same
        self.needs_turn = -1  # j goes round the members who need you, in the team's order
        self.needs_seen: list[str] = []  # who needed you at the last j: another set starts again at the first

    def compose(self) -> ComposeResult:
        with Horizontal(id="monitor-top"):
            yield Static("", id="team-head")
            yield Static("", id="monitor-quota")
        yield Static("", id="monitor-needs", classes="none")
        yield TeamGraph(self.team, self.accent, id="team-graph")
        yield Static("", id="monitor-picked")
        with Horizontal(id="monitor-body"):
            with Vertical(id="monitor-talk"):
                yield Label("CONVERSATION", id="team-log-head")
                yield Static("", id="team-lanes")
                with VerticalScroll():
                    yield Static("", id="team-log")
            yield Static("", id="monitor-side")
        yield Label(keys_line(120), classes="hint", id="monitor-keys")

    def on_mount(self) -> None:
        super().on_mount()
        self.set_class(self.size.width >= WIDE, "wide")

    def on_resize(self) -> None:
        self.set_class(self.size.width >= WIDE, "wide")
        self.query_one("#monitor-keys", Label).update(keys_line(max(40, self.size.width - 4)))
        self.show_more()
        super().on_resize()

    # --- what it shows ---------------------------------------------------------------------------

    def show(self, sessions: dict[str, Session | None], said: list[teams.Message]) -> None:
        super().show(sessions, said)
        self.build_view(dict(sessions), list(said))

    @work(thread=True, exclusive=True, group="monitor-view")
    def build_view(self, sessions: dict[str, Session | None], said: list[teams.Message]) -> None:
        """The view model's team (VM.team_view), off the screen's thread: it reads the bus, agy's
        saved quota and git as the dashboard last read it."""
        app = self.app
        git = dict(getattr(app, "git", {}))

        def git_of(folder: str) -> dict | None:
            state = git.get(folder_key(folder))
            return {"branch": state.branch, "changed": state.changed, "ahead": state.ahead} if state else None

        usage = agy.load_quota()
        view = VM.team_view(teams.TeamState(self.team, sessions, said), dict(getattr(app, "session_tokens", {})),
                            dict(getattr(app, "sessions", {})), git_of=git_of, agy_usage=usage)
        quota = agy.quota_view(usage) if "agy" in view.clis else {}
        app.call_from_thread(self.take_view, view, quota)

    def take_view(self, view: VM.TeamView, quota: dict) -> None:
        self.view, self.agy_view = view, quota
        self.show_more()

    def show_more(self) -> None:
        """The quota, who needs you, the picked member's lines and the wide table, from the view."""
        view = self.view
        if view is None or not self.is_mounted:
            return
        palette = self.graph.palette
        limits = getattr(self.app, "limits", [])
        self.query_one("#monitor-quota", Static).update(
            quota_line(limits, getattr(self, "agy_view", {}), "agy" in view.clis))
        needs = needs_line(view, palette)
        strip = self.query_one("#monitor-needs", Static)
        strip.update(needs or "")
        strip.set_class(needs is None, "none")
        member = self.graph.selected_member() if self.picked else None
        state = teams.TeamState(self.team, self.sessions, self.messages)
        self.query_one("#monitor-picked", Static).update(
            picked_lines(view, member.name if member else None, state, palette, max(20, self.size.width - 4)))
        self.query_one("#monitor-side", Static).update(members_table(view, palette, member.name if member else None))

    def show_log(self) -> None:
        super().show_log()
        self.show_more()

    # --- keys ----------------------------------------------------------------------------------

    def action_clear_pick(self) -> None:
        """Esc: everyone again. The monitor never goes back to a list: q quits it."""
        self.graph.selected = len(self.graph.order)
        self.picked = False
        self.graph.refresh()
        self.show_log()

    def action_close(self) -> None:
        self.action_clear_pick()  # the team view's Esc goes back; here there is nothing behind it

    def action_next_needs(self) -> None:
        names = [m.name for m in self.view.members if m.needs_you] if self.view else []
        if not names:
            self.notify("Nobody in the team needs you.", timeout=3)
            return
        if names != self.needs_seen:
            self.needs_seen, self.needs_turn = names, -1
        self.needs_turn = (self.needs_turn + 1) % len(names)
        order = [m.name for m in self.graph.order]
        self.graph.selected = order.index(names[self.needs_turn]) if names[self.needs_turn] in order else 0
        self.picked = True
        self.graph.refresh()
        self.show_log()

    def action_message(self) -> None:
        member = self.graph.selected_member() if self.picked else None
        member = member or self.team.lead
        if member is None:
            self.notify("Pick a member with ← → first.", timeout=3)
            return
        tell = getattr(self.app, "tell_team", None)

        def send(text: str | None) -> None:
            if text and tell is not None:
                tell(self.team.name, self.team.session_name(member), text)

        self.app.push_screen(LineDialog(f"A message for {member.name}", "what it should hear", enter="send"), send)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "monitor":
            return False  # the team view's M: here it would only bring this window up again
        return super().check_action(action, parameters)

    def action_other_team(self) -> None:
        saved = teams.load()

        def chosen(answer: tuple[str, str] | None) -> None:
            if answer and answer[0] != "open":
                self.notify("New teams are made in the dashboard (D).", timeout=5)
                return
            if answer and answer[1] != self.team.name:
                shower = getattr(self.app, "show_monitor", None)
                if shower is not None:
                    shower(answer[1])

        self.app.push_screen(TeamsDialog(saved, self.team.name), chosen)

    def action_dashboard(self) -> None:
        self.open_dashboard()

    @work(thread=True, group="monitor-dashboard")
    def open_dashboard(self) -> None:
        """D: the dashboard's tab to the front; with none open, the dashboard in a new tab."""
        try:
            said = teamwatch.open_dashboard(getattr(self.app, "TITLE", "tabmancer"), self.team.cwd)
        except OSError as error:
            said = f"Could not open the dashboard: {error}"
        if said:
            self.app.call_from_thread(self.notify, said, timeout=6)

    def action_keys(self) -> None:
        def run(action: str | None) -> None:
            if action:
                self.call_later(self.run_action, action)

        self.app.push_screen(KeysScreen(MONITOR_KEYS), run)

    def handing_over(self) -> tuple[list[str], list[str]]:
        """What a q now would leave unfinished: handovers queued here for when a member finishes (lost
        with this window), and work under way in it (it still ends, the window waiting for it, but what
        it would open next does not open: the app is gone by then)."""
        queued = getattr(self.app, "queued", {})
        mine = {s.session_id: name for name, s in self.sessions.items() if s is not None}
        lost = [f"{mine[sid]}'s handover, queued for when it finishes" for sid in queued if sid in mine]
        running = list(dict.fromkeys(UNDER_WAY[w.group] for w in self.app.workers
                                     if w.group in UNDER_WAY and not w.is_finished))
        return lost, running

    def action_quit_monitor(self) -> None:
        lost, running = self.handing_over()
        if (lost or running) and not self.quitting:
            self.quitting = True
            self.notify(quit_note(lost, running), severity="warning", timeout=8)
            self.set_timer(8, lambda: setattr(self, "quitting", False))
            return
        self.app.exit(None)

