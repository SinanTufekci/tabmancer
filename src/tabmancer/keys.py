"""Every key of the dashboard and of the team monitor, and what each does, as plain data.

The terminal dashboard's ? menu (keysview) and the team monitor's (monitorview) list these, and so
do the desktop window's menus (tabmancer.desktop): one list for every front end. It imports
nothing, so a window without Textual, or a check of the keys, reads it as it is.
"""

from __future__ import annotations

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
        ("E", "fold_teams", ("every team's members under its row, or every team back to one row: each press "
                             "goes the other way (not in the live view)")),
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
        ("j", "next_waiting", "go to the next session that needs you, else the next one waiting for you"),
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
        ("M", "monitor", ("on a team's row or a member's (or in a team's view): the team's monitor in a window of "
                          "its own, on the second screen if there is one; open already, it comes to the front. In the "
                          "desktop window instead: tabmancer desktop --m-opens desktop")),
        ("V", "toggle_split", ("the preview beside the list, both at full height (by itself from 180x40, with a "
                               "team's cards in it, or its tree when they leave no room for its keys), or under "
                               "the list at any size (remembered)")),
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

# The monitor's keys, for its ? menu: (group, [(key, its action on the monitor, what it does)]).
MONITOR_KEYS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Members", [
        ("← →", "", "pick a member: its card, its lane and its arrows lit, and its two lines under the cards"),
        ("Enter", "open_member", "go to the picked member's tab, or open it"),
        ("j", "next_needs", "pick the next member that needs you"),
        ("p", "message", ("write to the picked member: typed into its tab once it waits for you, over the team "
                          "bus for a member on it; a closed one opens with it")),
        ("Esc", "clear_pick", "everyone again: no member picked"),
    ]),
    ("The team", [
        ("s", "start_team", "start every member not open, the lead last"),
        ("a", "add_member", "add members"),
        ("d", "remove_member", "take the picked member off the team (twice); its sessions stay"),
        ("g", "edit_goal", "the team's goal; the lead is told"),
        ("h", "handover_team", "hand the whole team over (twice): a fresh lead with the handover, fresh members"),
        ("H", "handover", "hand the picked member alone over to a fresh session"),
        ("x", "close_team", "close every open member's tab (twice); the sessions stay"),
    ]),
    ("Windows", [
        ("T", "other_team", "the monitor of another team, in this window"),
        ("D", "dashboard", "the dashboard: its tab to the front, or a new one"),
        ("?", "", "this list"),
        ("q", "quit_monitor", "close the monitor (twice while a handover is on its way from here)"),
    ]),
]
