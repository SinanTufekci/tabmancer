"""The ? menu: every key of the dashboard and what it does, in groups, and a way to run it.

The footer shows the keys used most. Everything else is here: ↑↓ picks a line, Enter runs
it as if its key had been pressed, so a feature nobody remembers the key of is still a few
keystrokes away. A test checks that every key the dashboard binds is listed.
"""

from __future__ import annotations

from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from .dialogs import frame
from .theme import MUTED, TEXT, keys

# (group, [(key, the dashboard action it runs, what it does)])
KEYS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Pick and open", [
        ("Enter", "open", ("open the ticked sessions, or else the highlighted one, each in a tab of its own. "
                           "An open session: go to its tab, ready to type. A team's row: the team view (e shows its "
                           "members under it, s starts a closed team, each member as itself and the lead last)")),
        ("Space", "toggle", ("tick or untick the highlighted session (or click its [ ]); on a closed team's row, "
                             "all its members")),
        ("a", "toggle_all", "tick or untick every session shown. On a team's row: add members to the team"),
        ("m", "cycle_mode", ("how the highlighted session opens: resume · auto-continue (resume and tell Claude "
                             "to carry on) · fork (a new session from a copy of its history) · new (a fresh "
                             "session in its folder)")),
        ("e", "expand", ("show how the session ended, under its title (or click the row). On a team's row: its "
                         "members under it, or the team back to one row")),
        ("/", "filter", "find by project, title or anything said in a session; Esc clears it"),
        ("1", "card('open')", ("only the open sessions, as the OPEN NOW card counts them (or click the card); "
                               "again, or Esc, shows all")),
        ("2", "card('waiting')", "only the sessions waiting for you, as NEED YOU counts them; again shows all"),
        ("3", "card('continue')", ("only the sessions to continue (cut off, unanswered, at a limit and the rest), "
                                   "as TO CONTINUE counts them; again shows all")),
        ("Tab", "", "move between the project list and the sessions"),
    ]),
    ("Keep the list tidy", [
        ("x", "hide", ("hide the ticked sessions, or the highlighted one (in the sidebar: the project); nothing is "
                       "deleted. On a hidden one: back in sight. On an open team's row, twice: close its members' "
                       "tabs (the sessions stay)")),
        ("X", "toggle_hidden", "show the hidden ones too, or not"),
        ("f", "pin", "pin the ticked sessions, or the highlighted one, above the rest; again unpins"),
    ]),
    ("Work with a session", [
        ("p", "prompt", ("write it a prompt: Enter opens it in a tab with the prompt, Ctrl+B runs it in the "
                         "background. An open session: go to its tab, to type there")),
        ("j", "next_waiting", "go to the next open session waiting for you, round and round"),
        ("h", "handover", ("hand it over to a fresh session that starts from a full handover. An open session "
                           "is closed on the way; a working one as soon as it finishes (h again cancels)")),
        ("c", "continue_at_reset", ("the sessions stopped at a usage limit: continue them now (after you sign "
                                    "in with another account) or when the limit lifts; an open one gets the "
                                    "prompt in its own tab")),
        ("r", "rename", "rename it, in Claude Code's own /resume list too"),
        ("s", "summarize", ("a short AI summary of it (one claude -p call, on your plan). On a team's row: start "
                            "every member not open")),
        ("n", "new_sessions", "start fresh sessions in several projects at once"),
        ("B", "background", "background sessions: attach, stop, remove, clean up"),
    ]),
    ("Views", [
        ("l", "toggle_live", "the live view: only the open sessions and what each is doing; l again goes back"),
        ("u", "tokens", "stats: tokens, when you work, tools and files, plan limits, a written report"),
        ("W", "wrapped", ("Claude Code Wrapped: your last seven days as a card to share (s saves it, c copies "
                          "it); on Fridays Bit says it is ready")),
        ("T", "teams", ("teams: several sessions on one project with an orchestrator, talking to each other: the "
                        "list of them, and a new one. A team's own view: Enter on its row")),
    ]),
    ("Workspaces", [
        ("w", "save_workspace", "save the ticked sessions' folders as a workspace"),
        ("o", "open_workspace", "open a workspace: carry on in each of its folders"),
    ]),
    ("Settings and the rest", [
        (",", "settings", "which CLIs to list: Claude Code, Codex, Antigravity"),
        ("t", "cycle_theme", "the next colour theme (remembered)"),
        ("A", "accounts", ("the Claude accounts you use: sign in with another one, in Claude Code's own sign-in, "
                           "its page in the browser profile you pick for it (a click on the account in the top "
                           "bar too)")),
        ("b", "toggle_mascot", "Bit in pixels, calm, in blocks, or away"),
        ("?", "keys", "this list"),
        ("Esc", "back", "clear the filter (a card's too); with no filter, quit"),
        ("q", "quit", "quit"),
    ]),
]


def line(key: str, what: str) -> Table:
    """A line of the menu: the key, and what it does wrapping under itself."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, width=6)
    grid.add_column()
    grid.add_row(Text(key, style=f"bold {TEXT}"), Text(what, style=TEXT))
    return grid


def options() -> list[Option | None]:
    out: list[Option | None] = []
    for i, (group, rows) in enumerate(KEYS):
        if i:
            out.append(None)
        out.append(Option(Text(group.upper(), style=f"bold {MUTED}"), disabled=True))
        for key, action, what in rows:
            out.append(Option(line(key, what), id=action or None, disabled=not action or action == "keys"))
    return out


class KeysScreen(ModalScreen[str | None]):
    """The ? menu. Dismissed with the action picked, or None."""

    DEFAULT_CSS = frame("KeysScreen", "large", height="90%") + """
    KeysScreen > Vertical { max-height: 48; }
    KeysScreen OptionList { height: 1fr; margin-top: 1; border: none; background: $surface; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape,q,question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text.assemble(("Every key", "bold"),
                                      ("   the footer shows the common ones; Enter runs any of these", MUTED)))
            yield OptionList(*options())
            yield Label(keys(("↑↓", "pick"), ("Enter", "run it"), ("Esc", "close")), classes="hint")

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.focus()
        menu.action_first()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_close(self) -> None:
        self.dismiss(None)
