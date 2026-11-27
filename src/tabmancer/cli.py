"""Command line entry point."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import timedelta

from . import claudeupdate, clis, shortcuts, workspaces, workspacewords
from .config import Busy, merge_json, read_json, write_json
from .launch import Launch, continue_prompt, open_tabs
from .picks import EFFORTS, NO_CAP, Pick, cap_tokens
from .sessions import MOST_DAYS, find_sessions, nothing_found
from .shutdowns import open_at_shutdown, recent_shutdowns

PROG = shortcuts.PROG


def days_back(value: str) -> float:
    """--days: a number of days above 0. inf, nan and 1e9 ended in a traceback (no date is that far
    back), and -1 printed "No sessions in the last -1 days"."""
    try:
        days = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of days: {value}") from None
    if not math.isfinite(days) or not 0 < days <= MOST_DAYS:
        raise argparse.ArgumentTypeError(f"days must be above 0 and at most {MOST_DAYS}: {value}")
    return days


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        # Piped on Windows, Python writes in the ANSI code page, which jq and the like cannot read
        # and which drops what it cannot show: piped or saved, the output is UTF-8. In a console a
        # title or a bar the console cannot show still comes out as ? rather than stop the command.
        if hasattr(stream, "reconfigure"):
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="A dashboard for Claude Code sessions: see what they are doing, write to them, "
                    "reopen them after a reboot.")
    # The same filters work before or after the subcommand. SUPPRESS keeps the
    # subcommand from overwriting a value given before it with its own default.
    filters = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    for target in (parser, filters):
        target.add_argument("--days", type=days_back, help="how far back to look (default 14; for report, 7)")
        target.add_argument("--all", action="store_true",
                            help="include non-terminal sessions (claude -p, desktop app, IDE)")
    parser.add_argument("--demo", action="store_true",
                        help="the dashboard over made-up sessions, to try it: nothing of yours is read or changed")
    # The Windows Terminal window the dashboard runs in, by the name tabmancer's own launcher gave it
    # (wt -w <name>): what opens by itself goes there. A monitor is told the dashboard's.
    parser.add_argument("--tabs-window", default="", help=argparse.SUPPRESS)
    # The terminal dashboard while the window takes over: one flag, which the archive drops (shortcuts.TERMINAL).
    parser.add_argument(shortcuts.TERMINAL, dest="terminal", action="store_true",
                        help="the terminal dashboard, as before the window (being retired): alone, with --demo, "
                             "live or team watch")
    parser.set_defaults(days=None, all=False)  # 14, or a week for report: see below
    sub = parser.add_subparsers(dest="command", metavar="command")
    listing = sub.add_parser("list", parents=[filters],
                             help="print recent sessions instead of opening the picker")
    listing.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("live", parents=[filters], help="deprecated: the window's Live view (l in the window)")
    stating = sub.add_parser("stats", parents=[filters],
                             help="tokens, when you work, tools and files, plan limits (text, JSON or CSV)")
    stating.add_argument("--all-time", action="store_true",
                         help="from the first day kept (tabmancer's history, Claude Code's own record before it)")
    kind = stating.add_mutually_exclusive_group()
    kind.add_argument("--json", action="store_true", help="everything as JSON")
    kind.add_argument("--csv", action="store_true", help="one row per day as CSV")
    reporting = sub.add_parser("report", parents=[filters],
                               help="a report of the work, written by Claude, to share (default: last 7 days)")
    reporting.add_argument("--save", action="store_true", help="also save it as Markdown in Downloads")
    counting = sub.add_parser("tokens", parents=[filters],
                              help="where the tokens went: by day, project, session and model")
    counting.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("doctor", help="check that everything tabmancer relies on still works")
    fresh = sub.add_parser("new", parents=[filters], help="start fresh sessions in several folders at once")
    fresh.add_argument("folders", nargs="+", metavar="folder",
                       help="a folder path, or the name of a project you worked in lately")
    fresh.add_argument("--prompt", action="append",
                       help="a first message; given more than once, a session for each in every folder")
    fresh.add_argument("--linked", action="store_true",
                       help="with one folder and two prompts or more: open them as a team of peers")
    fresh.add_argument("--cli", choices=list(clis.CLIS), help="which CLI to start (default: Claude Code if on)")
    fresh.add_argument("--model", help="Claude Code's model: opus, sonnet, haiku, fable or a full name "
                                       "(default: your own setting)")
    fresh.add_argument("--effort", choices=list(EFFORTS), help="Claude Code's effort (default: your own setting)")
    fresh.add_argument("--autocompact", type=_cap, metavar="CAP",
                       help="the context Claude Code keeps before it compacts: 100k to 1M, or auto for its own "
                            "window (default: 300k, or context_cap in config.json)")
    fresh.add_argument("--advisor", choices=["opus", "fable"],
                       help="a stronger model the sessions ask at the turns that matter (Claude Code's --advisor), "
                            "for these sessions only")
    using = sub.add_parser("clis", help="which coding CLIs' sessions to list: claude, codex, agy")
    using.add_argument("action", nargs="?", choices=["enable", "disable", "main"],
                       help="main: the CLI tabmancer starts by default and runs its own calls on (claude or agy)")
    using.add_argument("names", nargs="*", metavar="name", help=", ".join(clis.CLIS))
    cleaning = sub.add_parser("cleanup", help="free disk space: move old sessions to the Recycle Bin (a screen; "
                                              "--list, --older and --yes in plain text)")
    cleaning.add_argument("--list", action="store_true", help="list the sessions on disk, biggest first")
    cleaning.add_argument("--older", type=float, metavar="DAYS",
                          help="pick the closed sessions last active more than DAYS ago (says what would move)")
    cleaning.add_argument("--yes", action="store_true", help="move the sessions --older picks to the Recycle Bin")
    cleaning.add_argument("--json", action="store_true", help="machine-readable output")
    prompt = sub.add_parser("continue-prompt", help="show or change what auto-continue tells Claude")
    prompt.add_argument("text", nargs="?", help="the new prompt")
    prompt.add_argument("--reset", action="store_true", help="go back to the default prompt")
    opening = sub.add_parser("open", parents=[filters], help="open a saved workspace without the picker")
    opening.add_argument("name", help="workspace name")
    listed = sub.add_parser("workspaces", help="list saved workspaces")
    listed.add_argument("--delete", metavar="NAME", help="delete a workspace")
    shortcut = sub.add_parser("shortcut", help="create a desktop shortcut (Windows)")
    shortcut.add_argument("--workspace", metavar="NAME",
                          help="open this workspace directly instead of the picker")
    startup = sub.add_parser("startup", help="open the dashboard at login when sessions were left open")
    startup.add_argument("action", choices=["on", "off", "status"])
    teaming = sub.add_parser("team", help="manage a team: add/start members, send/listen/who on the cross-CLI bus, "
                                          "or watch it in a monitor of its own")
    teaming.add_argument("action", choices=["add", "start", "send", "listen", "who", "run", "watch"])
    teaming.add_argument("team", nargs="?", default="",
                         help="the team's name (watch: default the one team with members open)")
    teaming.add_argument("rest", nargs=argparse.REMAINDER,
                         help='add: "name: area @agy @model @effort; ..."; start: [member ...]; '
                              'send: <to> <message> [--from <member>] [--summary <text>]; '
                              'listen: [member] [--timeout N]; who; run: <member> -- <cmd...>')
    serving = sub.add_parser("mcp", help="the team bus as an MCP server on stdin/stdout, for a member's CLI "
                                         "(Claude Code, Antigravity, Codex)")
    serving.add_argument("--team", default="", help="default: TABMANCER_TEAM")
    serving.add_argument("--member", default="", help="default: TABMANCER_MEMBER")
    plugging = sub.add_parser("agy-plugin", help="Antigravity plugin for agy team members: the team bus as tools "
                                                 "and messages handed over when a turn ends (changes agy's plugins)")
    plugging.add_argument("action", nargs="?", choices=["install", "remove", "status"], default="status")
    telling = sub.add_parser("notifications", help="desktop notifications when a session wants you back")
    telling.add_argument("action", nargs="?", choices=["on", "off", "test"])
    checking = sub.add_parser("updates", help="say when a newer tabmancer is out (PyPI, once a day): on or off")
    claude_updating = sub.add_parser("claude-updates", help="run claude update once a day before sessions open: "
                                                            "on, off, or now")
    claude_updating.add_argument("action", nargs="?", choices=["on", "off", "now"])
    checking.add_argument("action", nargs="?", choices=["on", "off"])
    wrapping = sub.add_parser("wrapped", help="Claude Code Wrapped: your week (or month, or all time) as a picture "
                                              "to share; on or off: the dashboard's word on Fridays")
    wrapping.add_argument("action", nargs="?", choices=["on", "off"])
    wrapping.add_argument("--out", help="where to save the picture (default: Downloads)")
    wrapping.add_argument("--story", action="store_true", help="the tall card for stories (1080x1920), not the wide one")
    wrapping.add_argument("--share", choices=["x", "bluesky", "threads", "instagram"],
                          help="save and copy the card, and open a new post there with the words in it")
    wrapping.add_argument("--period", choices=["week", "month", "all"], default="week",
                          help="the last seven days (default), the last thirty, or all the days kept")
    wrapping.add_argument("--slide", choices=["you", "tokens", "time", "sessions", "tools", "summary"],
                          default="summary", help="one slide, not the card that sums it up")
    wrapping.add_argument("--gif", action="store_true", help="a GIF, its numbers counting up, not a still PNG")
    wrapping.add_argument("--names", action="store_true", help="the top project's name on the tools slide")
    windowing = sub.add_parser("desktop", parents=[filters], help="tabmancer in a desktop window (the desktop extra: "
                                               'pip install "tabmancer[desktop]"): the dashboard, which '
                                               "only watches for now, or a team's monitor")
    windowing.add_argument("--monitor", action="store_true",
                           help="a team's monitor, read-only: what tabmancer team watch shows, with room")
    windowing.add_argument("team", nargs="?", default="", help="the team (default: the one team with members open)")
    windowing.add_argument("--demo", dest="made_up", action="store_true",
                           help="over the made-up sessions and team of tabmancer --demo: nothing of yours is read")
    windowing.add_argument("--m-opens", dest="m_opens", choices=("terminal", "desktop"),
                           help="deprecated: what M in the terminal dashboard (--terminal) opens")
    # What the login shortcut runs when there is no windowless gui launcher.
    sub.add_parser("startup-run", help="(used by the login shortcut)")
    args = parser.parse_args(argv)
    if args.days is None:
        args.days = 7 if args.command == "report" else 14
    watching = args.command == "team" and args.action == "watch"
    if args.terminal and args.command not in (None, "live") and not watching:
        parser.error(f"{shortcuts.TERMINAL} is the terminal dashboard: alone, with --demo, live or team watch")

    if args.demo:
        if args.command:
            parser.error("--demo opens the dashboard: leave out the command")
        if args.terminal:
            terminal_dashboard(args.days, args.all, demo=True)
        else:  # `desktop --demo`
            sys.exit(window_or_install(parser, demo=True, days=args.days, include_all=args.all,
                                       tabs_window=args.tabs_window))
    elif args.command == "list":
        print_sessions(args.days, args.all, args.json)
    elif args.command == "tokens":
        print_tokens(args.days, args.json)
    elif args.command == "stats":
        print_stats(None if args.all_time else args.days, args.json, args.csv)
    elif args.command == "report":
        print_report(args.days, args.save)
    elif args.command == "clis":
        manage_clis(args.action, args.names)
    elif args.command == "new":
        claudeupdate.before_opening(print)  # today's claude update, before the sessions open
        start_fresh(args.folders, "\n".join(args.prompt or []), args.cli, args.days,
                    Pick(args.model or "", args.effort or "", args.autocompact or "", args.advisor or ""), args.linked)
    elif args.command == "open":
        claudeupdate.before_opening(print)
        open_workspace(args.name, args.days, args.all)
    elif args.command == "workspaces":
        manage_workspaces(args.delete)
    elif args.command == "doctor":
        from .doctor import run

        sys.exit(run())
    elif args.command == "cleanup":
        sys.exit(clean_up(args.list, args.older, args.yes, args.json))
    elif args.command == "continue-prompt":
        manage_continue_prompt(args.text, args.reset)
    elif args.command == "shortcut":
        create_shortcut(args.workspace)
    elif args.command == "startup":
        manage_startup(args.action)
    elif args.command == "notifications":
        manage_notifications(args.action)
    elif args.command == "claude-updates":
        manage_claude_updates(args.action)
    elif args.command == "updates":
        manage_updates(args.action)
    elif args.command == "wrapped":
        save_wrapped(args.action, args.out, "story" if args.story else "wide", args.share, args.period, args.slide,
                     args.gif, args.names)
    elif args.command == "team":
        if args.action == "watch" and args.terminal:
            sys.exit(team_watch(args.team, args.days, args.tabs_window))
        if args.action == "watch":
            print(f"{PROG} team watch is now {PROG} desktop --monitor; "
                  f"{PROG} {shortcuts.TERMINAL} team watch is the terminal one, for now.", file=sys.stderr)
            sys.exit(monitor_or_install(args.team))
        if not args.team:
            parser.error(f"team {args.action} needs the team's name")
        if args.action in ("add", "start"):
            claudeupdate.before_opening(print)  # today's claude update, before the members open
            rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest  # older Pythons keep the --
            spec = " ".join(rest)
            sys.exit(start_team(args.team, spec) if args.action == "start" else add_to_team(args.team, spec))
        elif args.action == "send":
            sys.exit(team_send(args.team, args.rest))
        elif args.action == "listen":
            sys.exit(team_listen(args.team, args.rest))
        elif args.action == "who":
            sys.exit(team_who(args.team))
        elif args.action == "run":
            sys.exit(team_run(args.team, args.rest))
    elif args.command == "desktop":
        sys.exit(m_opens(args.m_opens) if args.m_opens else desktop_window(
            args.monitor, args.team, args.made_up, args.days, args.all, args.tabs_window))
    elif args.command == "live":
        if args.terminal:
            terminal_dashboard(args.days, args.all, live=True, tabs_window=args.tabs_window)
        else:
            print(f"{PROG} live is now the window's Live view (l); "
                  f"{PROG} {shortcuts.TERMINAL} live is the terminal one, for now.", file=sys.stderr)
            sys.exit(window_or_install(parser, days=args.days, include_all=args.all, tabs_window=args.tabs_window,
                                       view="live"))
    elif args.command == "mcp":
        from . import mcpserver

        sys.exit(mcpserver.main(["--team", args.team, "--member", args.member]))
    elif args.command == "agy-plugin":
        from . import agyplugin

        sys.exit(agyplugin.main([args.action]))
    elif args.command == "startup-run":
        startup_main()
    elif args.terminal:
        terminal_dashboard(args.days, args.all, tabs_window=args.tabs_window)
    else:  # the dashboard: the window, or how to get it
        sys.exit(window_or_install(parser, days=args.days, include_all=args.all, tabs_window=args.tabs_window))


def terminal_dashboard(days: float, include_all: bool, demo: bool = False, live: bool = False,
                       tabs_window: str = "") -> None:
    """`tabmancer --terminal`: the terminal dashboard, as it was before the window took over; with demo
    over the made-up sessions (`--terminal --demo`), with live on its live view (`--terminal live`).
    Being retired: this, team_watch's terminal monitor and shortcuts.TERMINAL go when it is archived."""
    if not has_textual():
        sys.exit(f"The terminal dashboard is not installed (no Textual). The dashboard is the window: {PROG}")
    if demo:
        from .demo import run

        run(days)
        return
    from . import sixel
    from .app import SessionPicker  # Textual is slow to import; only the picker needs it

    sixel.probe()  # whether Bit can be drawn in real pixels: asked before Textual reads the keys
    # The picker opens sessions itself and stays open; it only hands back commands
    # when no supported terminal can open tabs.
    print_leftover(SessionPicker(days, include_all, live=live, tabs_window=tabs_window).run())


def launch_all(launches: list[Launch]) -> None:
    try:
        print_leftover(open_tabs(launches))
    except OSError as error:
        sys.exit(f"Could not open the tabs: {error}")


def print_leftover(leftover: list[str] | None) -> None:
    if leftover:
        print("No supported terminal (Windows Terminal or tmux) found. Run these:")
        print("\n".join(leftover))


def _get_workspace(name: str) -> workspaces.Workspace:
    saved = workspaces.load()
    workspace = workspaces.find(saved, name)
    if workspace is None:
        known = ", ".join(saved) or "none yet (save one with w in the picker)"
        sys.exit(f"No workspace named {name!r}. Saved workspaces: {known}")
    return workspace


def open_workspace(name: str, days: float, include_all: bool) -> None:
    workspace = _get_workspace(name)
    launches, notes = workspaces.resolve(workspace, find_sessions(days, include_all))
    for note in notes:
        print(note)
    if launches:
        launch_all(launches)
    elif sys.stdin.isatty():
        # Started from a shortcut, the window would close before this could be read.
        input("Nothing to open. Press Enter to close.")


def add_to_team(name: str, spec: str) -> int:
    """Members join a team and open in their own tabs; said so in words a lead can act on."""
    from . import teams

    saved = teams.load()
    team = saved.get(teams.clean(name))
    if team is None:
        print(f"There is no team called {name}. Teams: {', '.join(saved) or 'none'}.")
        return 1
    try:
        added, note = teams.add_members(team, spec)
    except Busy as error:
        print(teams.not_saved_words("Nobody joined", error))
        return 1
    if not added:
        print(f"Nobody joined{f': {note}' if note else ''}. Write members as \"name: area @model @effort; name: area\".")
        return 1
    team = teams.load()[team.name]  # with them on it: their roles name everyone
    joining = teams.joining(team, added)
    quiet = [launch for launch in joining if teams.in_background(team, launch)]
    tabs = [launch for launch in joining if launch not in quiet]
    leftover: list[str] = []
    if quiet:
        from .launch import run_in_background

        for launch in quiet:
            try:
                run_in_background(launch)
            except OSError as error:
                print(f"{launch.title} did not start in the background: {error}")
    if tabs:
        try:
            leftover = open_tabs(tabs)
        except OSError as error:
            print(f"They joined the team {team.name}, but their tabs could not open: {error}. The dashboard's s "
                  "starts them.")
            return 1
    where = ("They are starting in the background, with no tabs," if quiet and not tabs else
             "Their tabs are opening" if tabs and not quiet else
             "They are starting in tabs and the background")
    names = ", ".join(f"{team.session_name(m)} ({m.cli + ', ' if m.cli != 'claude' else ''}{m.focus or m.name}"
                      f"{f', {m.pick.words()}' if m.pick else ''})" for m in added)
    told = team.session_name(team.lead) if team.lead is not None else "the others"
    how_msg = (f'Message them with `tabmancer team send {team.name} <member> "..."`.'
               if team.uses_bus else "Message them with SendMessage by those names.")
    print(f"Joined the team {team.name}: {names}. {where} and each will introduce itself to "
          f"{told}. {how_msg}")
    if note:
        print(f"Note: {note}.")
    if leftover:  # nothing opened: said in the exit code too, so a lead does not wait for them
        print("No terminal could open their tabs. Run these:\n" + "\n".join(leftover))
        return 1
    return 0


MEMBER_DAYS = 90  # how far back a member's last session is looked for


def start_team(name: str, spec: str = "") -> int:
    """A team's members that are not open, started, as the dashboard's s starts them: a closed one
    resumed as itself (its name, its role, where it left off), one that never started new, the lead
    last; in the background where the team runs its members there. spec names the members (by name
    or session name, apart by spaces, commas or semicolons); none, every one that is not open. What a
    lead runs when a member it needs has closed: `tabmancer team add` takes only members new to it.

    A member is left be when a session of its name is open (in a tab, in the background, or just up
    and not yet written to: a member waits for the lead's first message with nothing in its
    transcript) or when this command opened it a moment ago; two sessions of one name would both
    answer to it, and two processes on one transcript would both write to it."""
    from . import teams
    from .launch import Mode, run_in_background
    from .sessions import Exit, live_sessions, mark_live

    saved = teams.load()
    team = saved.get(teams.clean(name))
    if team is None:
        print(f"There is no team called {name}. Teams: {', '.join(saved) or 'none'}.")
        return 1
    names = [m.name for m in team.members]
    prefix = f"{team.name}-"
    wanted = set()
    for part in re.split(r"[\s,;]+", spec):
        word = part.strip().casefold()
        if word.startswith(prefix) and teams.clean(word[len(prefix):]) in names:
            word = word[len(prefix):]  # a session name, as ListAgents shows it: not cut to a name's length first
        if teams.clean(word):
            wanted.add(teams.clean(word))
    if unknown := sorted(wanted - set(names)):
        print(f"No member {', '.join(unknown)} on the team {team.name}. Its members: {', '.join(names)}.")
        return 1
    # Which are open is asked afresh, background sessions too, and once.
    registry = teams.live_names()
    live = live_sessions()
    listed = find_sessions(MEMBER_DAYS, include_all=True, quick=True)
    for session in listed:
        mark_live(session, live.get(session.session_id))
    found = teams.member_sessions(team, listed, live=registry)
    # The session open under each name now: its id, from the pid files or the background sessions.
    open_ids = {called: str(info.get("sessionId")) for called, info in registry.items()}
    open_ids |= {str(entry.get("name")): sid for sid, entry in live.items() if entry.get("name")}
    by_id = {session.session_id: session for session in listed}
    just_opened = teams.opening()
    chosen = [m for m in team.members if not wanted or m.name in wanted]
    already, starting, other = [], [], []
    for member in chosen:
        session, called = found.get(member.name), team.session_name(member)
        if session is not None and session.exit is Exit.RUNNING:
            already.append(called)
        elif called in just_opened:
            starting.append(called)
        elif called in open_ids:
            # Open under its name: this member, up and not yet written to (it waits for the lead's
            # first message), or a session of an earlier team of the name, open still.
            seen = by_id.get(open_ids[called])
            (other if seen is not None and not teams.since_made(team, seen) else already).append(called)
    left_be = set(already) | set(starting) | set(other)
    wanted_now = [team.session_name(m) for m in chosen if team.session_name(m) not in left_be]
    try:  # checked and noted under one lock: a dashboard's s at the same moment opens each member once
        coming = teams.claim_opening(wanted_now)
    except Busy:
        print(f"Another tabmancer is starting members of {team.name} right now, so none started: run this again in a "
              "moment.")
        return 1
    starting += [called for called in wanted_now if called in coming]
    left_be |= coming
    claimed = [called for called in wanted_now if called not in coming]  # given back unless they start
    launches = teams.start(team, found, only={m.name for m in chosen if team.session_name(m) not in left_be})
    if already:
        how = f"`tabmancer team send {team.name} <member> \"...\"`" if team.uses_bus else "SendMessage"
        print(f"Open already: {', '.join(already)}. Message them with {how}.")
    if starting:
        check = f"`tabmancer team who {team.name}`" if team.uses_bus else "ListAgents"
        print(f"Opening a moment ago, so not opened again: {', '.join(starting)}. Wait for {check} to show them.")
    if other:
        print(f"A session of the name {', '.join(other)} is open, not this team's: close it, then start the member.")
    if not launches:
        teams.forget_opening(claimed)
        return 0 if not other else 1
    opening = {teams.session_name_of(team, launch) for launch in launches}
    for note in teams.agy_notes(team, {m.name for m in chosen if team.session_name(m) in opening}):
        print(note)  # an agy member asked for a Claude model: what it runs on instead
    quiet = [launch for launch in launches if teams.in_background(team, launch)]
    tabs = [launch for launch in launches if launch not in quiet]
    started, failed = [], []
    for launch in quiet:
        try:
            run_in_background(teams.background_start(team, launch))
            started.append(launch)
        except OSError as error:
            failed.append(f"{teams.session_name_of(team, launch)} did not start in the background: {error}")
    if tabs:
        try:
            leftover = open_tabs(tabs)
        except OSError as error:
            print(f"Their tabs could not open: {error}. The dashboard's s starts them.")
            teams.forget_opening(claimed)
            return 1
        if leftover:  # no terminal here could open tabs: say so, so nobody waits for them
            print("No terminal here could open their tabs, so none started. Run these:\n" + "\n".join(leftover))
            teams.forget_opening(claimed)
            return 1
        started += tabs
    begun = {teams.session_name_of(team, launch) for launch in started}
    teams.forget_opening([called for called in claimed if called not in begun])  # failed in the background

    def said(launch: Launch) -> str:
        how = "where it left off" if launch.mode is Mode.RESUME else "new, as it never started"
        return f"{teams.session_name_of(team, launch)} ({how})"

    if started:
        where = ("in the background" if all(launch in quiet for launch in started) else
                 "in their tabs" if not any(launch in quiet for launch in started) else "in tabs and the background")
        if team.uses_bus:
            print(f"Starting {', '.join(said(launch) for launch in started)} {where}. Each comes back with its name "
                  f'and role; message them with `tabmancer team send {team.name} <member> "..."`.')
        else:
            print(f"Starting {', '.join(said(launch) for launch in started)} {where}. Each comes back with its name "
                  "and role; message them with SendMessage by those names once ListAgents shows them.")
    for line in failed:
        print(line)
    return 0 if not failed and not other else 1


def watched_team(name: str) -> tuple[str, str]:
    """The team `team watch` shows, and a word for when there is none: the one named; else the one
    team with members open; else '' (the monitor asks with the list of teams)."""
    from . import teams

    saved = teams.load()
    if not saved:
        return "", "There is no team yet: T in the dashboard makes one."
    if name:
        found = next((t for t in saved if t.casefold() == name.casefold()), None)
        return (found, "") if found else ("", f"There is no team {name}. Teams: {', '.join(sorted(saved))}.")
    live = teams.live_names()
    working = [t.name for t in saved.values()
               if any(t.session_name(m) in live for m in t.members)]
    return (working[0] if len(working) == 1 else ""), ""


def monitor_or_install(name: str) -> int:
    """`tabmancer team watch [team]`: the desktop monitor (`tabmancer desktop --monitor`); without Qt,
    who is in the team (as `team who` says it) and how to get the monitor."""
    from . import desktop

    if desktop.available():
        return window_or_install(team=name or "")
    team, problem = watched_team(name)
    if problem:
        print(problem, file=sys.stderr)
    elif team:
        team_who(team)
    else:  # none named and not one at work: the window would ask which
        from . import teams

        print(f"Teams: {', '.join(sorted(teams.load()))}. `tabmancer team who TEAM` lists one's members.")
    window_or_install(team=name or "")
    return 1 if problem else 0


def team_watch(name: str, days: float, tabs_window: str = "") -> int:
    """`tabmancer --terminal team watch [team]`: the team's terminal monitor, in this window. It acts
    only when asked (no limits continued, no toasts, no updates): the dashboard stays the one that
    does. Being retired with the terminal dashboard (terminal_dashboard)."""
    if not has_textual():
        print(f"The terminal monitor is not installed (no Textual). The monitor is a window: "
              f"{PROG} desktop --monitor", file=sys.stderr)
        return 1
    team, problem = watched_team(name)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    from . import claudeupdate, sixel
    from .app import SessionPicker

    claudeupdate.leave_to_dashboard()  # tabs opened from the monitor do not run claude update first
    sixel.probe()
    print_leftover(SessionPicker(days, watch=team, tabs_window=tabs_window).run())  # "": it asks which, with the list of teams
    return 0


def m_opens(choice: str) -> int:
    """`tabmancer desktop --m-opens terminal|desktop`, deprecated: which monitor M in the terminal
    dashboard opens (M in the window opens its own). Kept, and said so, till the terminal one goes."""
    from . import desktop

    print(f"--m-opens is deprecated: it sets only what M in the terminal dashboard ({PROG} {shortcuts.TERMINAL}) "
          "opens; M in the window opens its own monitor.", file=sys.stderr)
    merge_json("config.json", monitor=choice)
    if choice == "terminal":
        print("M in the dashboard opens the team's monitor in a terminal window.")
    elif desktop.available():
        print("M in the dashboard opens the team's monitor in the desktop window.")
    else:
        print("M in the dashboard opens the team's monitor in the desktop window once Qt is installed "
              f"({desktop.INSTALL}); till then, in a terminal window.")
    return 0


CORE_INSTALL = 'uv tool install "tabmancer[desktop]"'
# `tabmancer cleanup` with no screen to pick in (a core install): where to pick them by hand
CLEAN_UP_BY_HAND = "To pick them by hand: {how}, then Settings ▸ Free disk space… in the window."


def has_textual() -> bool:
    """Whether the terminal dashboard can run (`tabmancer --terminal`): Textual installed."""
    try:
        return importlib.util.find_spec("textual") is not None
    except ImportError:
        return False


def window_or_install(parser: argparse.ArgumentParser | None = None, demo: bool = False,
                      team: str | None = None, days: float = 14, include_all: bool = False,
                      tabs_window: str = "", view: str = "") -> int:
    """`tabmancer`, `--demo`, `live` and `team watch`: the desktop window if Qt is there (the dashboard,
    over the made-up sessions with demo, or a team's monitor with team: "" asks which), the way
    `tabmancer desktop` opens it; else how to add it, the terminal dashboard while it is kept, and with
    parser what the commands are (exit 0: they work as they are). days and include_all: --days and
    --all, which the window lists by as the terminal's did; tabs_window: --tabs-window, the Windows
    Terminal window its tabs go to; view: the view it opens on (`live`). `live` without Qt exits 1."""
    from . import desktop

    if desktop.available():
        return desktop_window(team is not None, team or "", demo, days, include_all, tabs_window, view)
    what = "A team's monitor" if team is not None else "The demo" if demo else "The dashboard"
    print(f"{what} is a desktop window, which needs Qt: {CORE_INSTALL}")
    if has_textual():
        print(f"The terminal one runs as before, for now: {PROG} {shortcuts.TERMINAL}")
    if parser is not None:
        print(f"Without it the commands work as they are:\n\n{parser.format_help()}")
    return 1 if view else 0


def desktop_window(monitor: bool, name: str, demo: bool = False, days: float = 14,
                   include_all: bool = False, tabs_window: str = "", view: str = "") -> int:
    """`tabmancer desktop`: the dashboard in a Qt window, from the desktop extra, listing the sessions
    of the last days (--days) and with include_all those tools started too (--all); with --monitor
    [team], a team's monitor. Without Qt it says how to add it; the terminal dashboard and monitor
    (team watch) are there either way. The dashboard's tabs go into one named Windows Terminal window:
    tabs_window (--tabs-window), else the terminal dashboard's own (shortcuts.DASHBOARD_WINDOW), as
    launch.gui_tabs says. view: the view the dashboard opens on ("live" for `tabmancer live`), else its own."""
    from . import desktop

    if not desktop.available():
        print(f"The desktop window needs Qt, from the desktop extra: {desktop.INSTALL}", file=sys.stderr)
        if has_textual():
            print(f"The terminal ones need nothing more, for now: {PROG} {shortcuts.TERMINAL}, and "
                  f"{PROG} {shortcuts.TERMINAL} team watch [team]", file=sys.stderr)
        return 1
    if not monitor and not name:
        from . import launch
        from .desktop.main import run as run_dashboard

        with launch.gui_tabs(tabs_window or shortcuts.DASHBOARD_WINDOW):
            return run_dashboard(demo=demo, days=days, include_all=include_all, **({"view": view} if view else {}))
    if not monitor:  # a team named, not --monitor
        print(f"A team's monitor: {PROG} desktop --monitor {name}", file=sys.stderr)
        return 2
    team = name
    if not demo:  # the demo's teams are made up when it starts
        team, problem = watched_team(name)
        if problem:
            print(problem, file=sys.stderr)
            return 1
    from .desktop.monitor import run

    return run(team, demo=demo)  # "": it asks which, with the list of teams


def team_send(name: str, rest: list[str]) -> int:
    """Send a message to a team member over tabmancer's cross-CLI bus."""
    from . import teambus, teams

    p = argparse.ArgumentParser(prog=f"{PROG} team send {name}", add_help=True)
    p.add_argument("--from", dest="sender", default="", help="sender member name (defaults to TABMANCER_MEMBER)")
    p.add_argument("--summary", default="", help="optional short one-line summary")
    p.add_argument("to", help="recipient member name (or session name, or 'all')")
    p.add_argument("message", nargs="+", help="message text")
    try:
        opts = p.parse_args(rest)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    team_name = teams.clean(name)
    team = teams.load().get(team_name)
    return teambus.send_command(team_name, opts.to, " ".join(opts.message), sender=opts.sender,
                                summary=opts.summary, members={m.name for m in team.members} if team else None)


def team_listen(name: str, rest: list[str]) -> int:
    """Wait until unread message(s) arrive for this member on the team bus, print them and exit."""

    from . import teambus, teams

    p = argparse.ArgumentParser(prog=f"{PROG} team listen {name}", add_help=True)
    p.add_argument("member", nargs="?", default="", help="member name (defaults to TABMANCER_MEMBER)")
    p.add_argument("--timeout", type=float, default=teambus.DEFAULT_LISTEN_TIMEOUT,
                   help="seconds to wait before exiting cleanly (default 3300)")
    try:
        opts = p.parse_args(rest)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    team_name = teams.clean(name)
    member = opts.member or os.environ.get("TABMANCER_MEMBER") or ""
    if not member:
        print("Name the member to listen for (or set TABMANCER_MEMBER).", file=sys.stderr)
        return 1
    team = teams.load().get(team_name)
    since = teams._made(team.created) if team is not None and team.created else None
    return teambus.listen_command(team_name, member, timeout=opts.timeout, since=since)


def team_who(name: str) -> int:
    """List a team's members, their CLIs and whether each is online / listening."""
    from . import teambus, teams
    from .sessions import Exit

    team_name = teams.clean(name)
    saved = teams.load()
    team = saved.get(team_name)
    if team is None:
        print(f"There is no team called {name}. Teams: {', '.join(saved) or 'none'}.")
        return 1
    live = teams.live_names()
    presence = teambus.read_presence(team.name)
    listed = find_sessions(MEMBER_DAYS, include_all=True, quick=True)
    found = teams.member_sessions(team, listed, live=live)
    for member in team.ordered:
        session = found.get(member.name)
        pinfo = presence.get(member.name)
        if session is not None and session.exit is Exit.RUNNING:
            state = "listening" if (pinfo and pinfo.get("listening")) else (session.live_state or "open")
        elif pinfo and pinfo.get("runner_alive"):
            state = "listening" if pinfo.get("listening") else "open"
        else:
            state = "closed"
        role_word = "lead" if member.lead else (member.focus or member.name)
        owns = f" [{member.owns}]" if member.owns else ""
        delivery = teambus.delivery_words(pinfo)  # a Codex member's last `codex queue`
        print(f"{member.name} ({team.session_name(member)}) · {member.cli} · {state} · {role_word}{owns}"
              + (f" · {delivery}" if delivery else ""))
    return 0


def team_run(name: str, rest: list[str]) -> int:
    """Run a team member CLI inside its terminal tab while tracking live process presence."""
    from . import teambus, teams

    if not rest:
        print(f"Usage: {PROG} team run <team> <member> -- <command...>", file=sys.stderr)
        return 2
    member = rest[0]
    cmd = rest[1:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print(f"Usage: {PROG} team run <team> <member> -- <command...>", file=sys.stderr)
        return 2
    team_name = teams.clean(name)
    saved = teams.load()
    team = saved.get(team_name)
    created = team.created if team is not None else ""
    # Either separator: a team file (and so its command) may have been written on Windows.
    program = cmd[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    cli_name = "agy" if program.startswith("agy") else "codex" if program.startswith("codex") else "claude"
    beside = None
    if cli_name == "codex":  # no background command wakes Codex: its messages are queued into its thread
        from functools import partial

        from . import codexbus

        beside = partial(codexbus.watch, team_name, teambus.normalize_member(team_name, member))
    return teambus.run_member(team_name, member, cmd, cli=cli_name, created=created, beside=beside)


def manage_notifications(action: str | None) -> None:
    from . import toast

    if action in ("on", "off"):
        saved = read_json("config.json")
        saved = saved if isinstance(saved, dict) else {}
        saved["desktop_notifications"] = action == "on"
        write_json("config.json", saved, indent=2)
    elif action == "test":
        shown = toast.show("tabmancer", "Desktop notifications work.", dashboard="")
        print("Shown." if shown == toast.SHOWN else "This system could not show it.")
        return
    print(f"Desktop notifications are {'on' if toast.enabled() else 'off'}: a session that stops working "
          "and waits for you, or stops at a usage limit, is told outside the dashboard too.")


def manage_claude_updates(action: str | None) -> None:
    """claudeupdate.py: on, off, now (whether due or not), or how it stands."""
    if action in ("on", "off"):
        claudeupdate.set_enabled(action == "on")
    if action == "now":
        print("Updating Claude Code (claude update)…")
        print(claudeupdate.run().words())
        return
    done = claudeupdate.last()
    when = f" Last run {done['at'].replace('T', ' ')}: {done.get('after', '?')}." if done.get("at") else ""
    if claudeupdate.enabled():
        print("On: once a day, claude update runs before tabmancer opens a session (the dashboard, tabmancer new, "
              f"tabmancer team), and the sessions open when it is done.{when}")
    else:
        print(f"Off: tabmancer never runs claude update (tabmancer claude-updates on turns it on).{when}")


def manage_updates(action: str | None) -> None:
    from . import __version__, updates

    if action in ("on", "off"):
        updates.set_enabled(action == "on")
    if not updates.enabled():
        print(f"tabmancer {__version__}. The update check is off: the dashboard never asks PyPI for a "
              "newer version (tabmancer updates on turns it on).")
        return
    command = updates.upgrade_command()
    if command is None:
        print(f"tabmancer {__version__}, run from its source: git pull brings it up to date.")
        return
    found = updates.latest()
    if found and updates.newer(found):
        print(f"tabmancer {__version__}; {found} is out. To upgrade: {command}")
    else:
        print(f"tabmancer {__version__}" + (", the latest." if found else ": PyPI did not answer.")
              + " The dashboard asks PyPI once a day and says when a newer one is out "
              "(tabmancer updates off turns that off).")


def manage_continue_prompt(text: str | None, reset: bool) -> None:
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    if reset or text:
        saved.pop("continue_prompt", None)
        if text and not reset:
            saved["continue_prompt"] = text
        write_json("config.json", saved, indent=2)
    print(f"Auto-continue sends: {continue_prompt()}")


def manage_workspaces(delete: str | None) -> None:
    if delete:
        workspace = _get_workspace(delete)
        if not workspaces.drop(workspace.name):  # unreadable, held past the full wait, or not written
            print(workspacewords.not_kept_note(workspace.name, deleting=True).words)
            sys.exit(1)
        print(workspacewords.deleted_note(workspace.name).words)
        return
    saved = workspaces.load()
    if not saved:
        print("No workspaces yet. In the picker, tick sessions and press w to save one.")
    for workspace in saved.values():
        print(workspace.name)
        for entry in workspace.entries:
            print(f"    {entry.mode:<8} {entry.cwd}")


def print_sessions(days: float, include_all: bool, as_json: bool) -> None:
    sessions = find_sessions(days, include_all)
    preselected = open_at_shutdown(sessions, recent_shutdowns())
    if as_json:
        rows = [dict(asdict(s), path=str(s.path), status=s.status.value, exit=s.exit.value,
                     started=s.started.isoformat() if s.started else None,
                     last_active=s.last_active.isoformat(),
                     warm_until=s.warm_until.isoformat() if s.warm_until else None,
                     resets=s.resets.isoformat() if s.resets else None,
                     open_at_shutdown=s.session_id in preselected) for s in sessions]
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    if not sessions:
        print(nothing_found(days, include_all), file=sys.stderr)
        return
    for s in sessions:
        mark = "*" if s.session_id in preselected else " "
        print(f"{mark} {s.last_active:%Y-%m-%d %H:%M}  {s.status.value:<10} {s.exit.value:<7} "
              f"{s.project[:20]:<20} {s.title[:60]}  [{s.session_id}]")
    if preselected:
        print("\n* looked open at the last shutdown")


def _cap(word: str) -> str:
    """--autocompact as Claude Code takes it: 100k to 1M (300k, 300000, or 300 for short), or auto."""
    word = word.strip().lower()
    if word == NO_CAP or cap_tokens(word):
        return word
    raise argparse.ArgumentTypeError("100k to 1M (e.g. 300k), or auto")


def start_fresh(names: list[str], prompt: str | None, cli: str | None, days: float, pick: Pick | None = None,
                linked: bool = False) -> None:
    from . import newforms, teams

    cli = cli or newforms.default_cli()
    problem = newforms.fresh_problem(names, prompt, cli, pick, linked)
    if problem:
        sys.exit(problem)
    folders, missing = newforms.folders_for(names, newforms.projects(find_sessions(days, quick=True)))
    if missing:
        sys.exit(f"Not a folder, and no recent project of that name: {', '.join(missing)}")
    try:
        built = newforms.build(newforms.Request(folders, cli, prompt or "", pick or Pick(), linked))
    except Busy as error:
        sys.exit(teams.not_saved_words("Nothing was started", error))
    launch_all(built.launches)
    if built.team is not None:
        print(f"The team of peers {built.team.name}: "
              f"{', '.join(built.team.session_name(m) for m in built.team.members)}. T in the dashboard shows it.")


def manage_clis(action: str | None, names: list[str]) -> None:
    unknown = [name for name in names if name not in clis.CLIS]
    if unknown or (action and not names):
        sys.exit(f"Name one or more of: {', '.join(clis.CLIS)}" + (f" (not {', '.join(unknown)})" if unknown else ""))
    if action == "main":
        if len(names) != 1 or names[0] not in clis.MAINS:
            sys.exit(f"Name one of: {', '.join(clis.MAINS)} (Codex has no background run tabmancer can use)")
        clis.set_main(names[0])
    elif action:
        on = set(clis.enabled())
        on = on | set(names) if action == "enable" else on - set(names)
        clis.set_enabled(sorted(on))
    on = clis.enabled()
    for cli in clis.CLIS.values():
        where = clis.installed(cli.name) or "not found on PATH"
        print(f"{'on ' if cli.name in on else 'off'}  {cli.label:<12} {cli.name:<7} {where}")
    print(f"\nMain CLI: {clis.label(clis.main_cli())}. {clis.MAIN_COST[clis.main_cli()]}")
    if not action:
        print(f"\nTurn one on with: {PROG} clis enable codex   (or press , in the dashboard)")
        print(f"Run tabmancer on Antigravity with: {PROG} clis main agy")


def print_report(days: float, save: bool) -> None:
    from . import report, summarize, tokens

    stats_report = tokens.collect(days)
    sessions = {s.session_id: s for s in find_sessions(days, quick=True)}
    print(f"Claude ({report.MODEL}) is writing the report for the last {stats_report.days} days…", file=sys.stderr)
    try:
        entry = report.write(sessions, stats_report)
    except summarize.SummaryError as error:
        sys.exit(f"The report could not be written: {error}")
    print(entry["text"])
    if save:
        print(f"Saved {report.save(entry)}", file=sys.stderr)


def save_wrapped(action: str | None, out: str | None, shape: str = "wide", where: str | None = None,
                 period: str = "week", slide: str = "summary", moving: bool = False, names: bool = False) -> None:
    """Claude Code Wrapped: the week, the month or all of it saved as a picture (a GIF with moving),
    or taken where it is shared; on/off: the Friday word."""
    from pathlib import Path

    from . import card, history, share, wrapped
    from .palette import DEFAULT_THEME, THEMES

    if action:
        wrapped.set_enabled(action == "on")
        print("On Fridays from noon the dashboard says your week is ready (W shows it)." if action == "on" else
              "The dashboard no longer says so on Fridays; W and tabmancer wrapped still show your week.")
        return
    days = {"week": 7, "month": 30, "all": None}[period]
    report = history.collect(None if days is None else days * 2)
    week = wrapped.week(report, {s.session_id: s for s in find_sessions(report.days, include_all=True, quick=True)},
                        days=days)
    accent = THEMES[DEFAULT_THEME][1].lstrip("#")
    colour = (int(accent[0:2], 16), int(accent[2:4], 16), int(accent[4:6], 16))
    target = Path(out).expanduser() if out else None
    name, why = wrapped.persona(week)
    if where:
        place = next(p for p in share.PLACES if p.label.lower() == where)
        folder = target if target is not None and target.suffix.lower() not in (".png", ".gif") else None
        done = share.share(place, week, colour, folder, slide=slide, names=names)
        print(f"Claude Code Wrapped, {wrapped.span(week).lower()}: saved as {done.path}\n{done.note}")
        return
    kind = "gif" if moving else "png"
    data = wrapped.moving(week, colour, shape, slide, names) if moving else \
        wrapped.draw(week, colour, shape=shape, slide=slide, names=names).png()
    if target is not None and target.suffix.lower() not in (".png", ".gif"):  # a folder
        path = card.save_data(data, wrapped.filename(week, shape, slide, kind), target)
    elif target is not None:
        target.write_bytes(data)
        path = target
    else:
        path = card.save_data(data, wrapped.filename(week, shape, slide, kind))
    print(f"Claude Code Wrapped, {wrapped.span(week).lower()}: saved as {path}")
    print(f"  I'm {name.title()}: {why.lower()}")
    for what, number, note in wrapped.tiles(week):
        print(f"  {what.lower():<17}{number:<14}{note.lower()}")
    print(f"\nTo post it: tabmancer wrapped --share x (or bluesky, threads, instagram). The words:\n{wrapped.words(week)}")


def print_stats(days: float | None, as_json: bool, as_csv: bool) -> None:
    """days None: all of it. Counted with the days tabmancer has kept (history.py)."""
    from . import history, stats

    report = history.collect(None if days is None else max(1, round(days)))
    sessions = {s.session_id: s for s in find_sessions(report.days, include_all=True, quick=True)}
    if as_json:
        json.dump(stats.export(report, sessions), sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    if as_csv:
        sys.stdout.write(stats.to_csv(report))
        return
    from rich.console import Console

    from . import usage
    from .palette import DEFAULT_THEME, THEMES
    from .statstext import plain_text

    usage.record(usage.read_all())  # a fresh reading for the limits part
    before = None if days is None else stats.before(history.collect(report.days * 2), report.days, sessions)
    console = Console()
    for part in plain_text(report, sessions, THEMES[DEFAULT_THEME][1], before):
        console.print(part)


def clean_up(listing: bool, older: float | None, yes: bool, as_json: bool) -> int:
    """`tabmancer cleanup`: the screen, where Textual is installed and no flag is given; else plain
    text. --list lists, --older DAYS says what it would move, and only --yes with --older moves:
    never everything by accident. Which are open is asked again as they move (diskclean.trash). With
    --older, the line on top is the screen's (cleanupwords.status: what is picked, and its size). A core
    install (no screen) lists, and says where to pick them by hand: the desktop window."""
    from . import cleanupwords, diskclean

    if yes and older is None:
        print("--yes moves what --older DAYS picks: give --older too (nothing was moved).", file=sys.stderr)
        return 2
    no_screen = False
    if not (listing or older is not None or as_json):
        try:
            from .cleanup import CleanupApp
        except ImportError as error:
            if not (error.name or "").startswith("textual"):
                raise
            listing = no_screen = True  # a core install: no screen, so the list
        else:
            CleanupApp().run()
            return 0
    items = diskclean.collect()
    picked = diskclean.older_than(items, older) if older is not None else []
    if yes:
        moved, problems = diskclean.trash(picked)
        freed = sum(item.size for item in moved)
        if as_json:
            json.dump({"moved": [item.session.session_id for item in moved], "freed": freed, "problems": problems},
                      sys.stdout, indent=2)
            print()
        else:
            print(f"Moved {len(moved)} sessions ({diskclean.human(freed)}) to the {diskclean.BIN}.")
            for problem in problems:
                print(f"  not moved: {problem}")
        return 1 if problems else 0
    if as_json:
        chosen = {id(item) for item in picked}
        json.dump([dict(diskclean.as_json(item), **({"picked": id(item) in chosen} if older is not None else {}))
                   for item in items], sys.stdout, indent=2)
        print()
        return 0
    shown = picked if older is not None else items
    if older is not None:
        print(cleanupwords.status(items, {item.session.session_id for item in picked}))
    else:
        total = sum(item.size for item in items)
        print(f"{len(items)} sessions use {diskclean.human(total)} · Claude Code deletes sessions older than "
              f"{diskclean.claude_cleanup_days()} days on its own")
    for item in shown:
        s = item.session
        print(f"{diskclean.human(item.size):>9}  {s.last_active:%Y-%m-%d}  {s.project[:22]:<22}  {s.title[:52]}"
              + ("  (open, kept)" if item.is_open else ""))
    if older is not None:
        size = diskclean.human(sum(item.size for item in picked))
        days = f"{older:g}"
        print(f"Would move {len(picked)} sessions ({size}) last active more than {days} days ago to the "
              f"{diskclean.BIN}; add --yes to move them.")
    else:
        print(f"Pick with --older DAYS (what would move), then add --yes to move them to the {diskclean.BIN}.")
    if no_screen:
        from . import desktop

        print(CLEAN_UP_BY_HAND.format(how=f"{PROG} desktop" if desktop.available() else CORE_INSTALL))
    return 0


def print_tokens(days: float, as_json: bool) -> None:
    from . import tokens

    report = tokens.collect(days)
    if as_json:
        def counts(use: tokens.Tokens) -> dict:
            return dict(asdict(use), total=use.total)

        ranked = sorted(report.per_session().items(), key=lambda item: item[1].total, reverse=True)
        json.dump({
            "first_day": report.first.isoformat(),
            "days": [dict(counts(use), date=(report.first + timedelta(days=i)).isoformat())
                     for i, use in enumerate(report.daily())],
            "sessions": [dict(counts(use), session_id=sid, folder=report.folders.get(sid, ""))
                         for sid, use in ranked],
            "models": {model: counts(use) for model, use in report.models.items()},
        }, sys.stdout, indent=2)
        print()
        return
    from rich.console import Console

    from .palette import DEFAULT_THEME, THEMES
    from .statstext import render

    sessions = {s.session_id: s for s in find_sessions(days, include_all=True, quick=True)}
    Console().print(render(report, sessions, THEMES[DEFAULT_THEME][1]))


def _windows_only(what: str) -> None:
    if sys.platform != "win32":
        sys.exit(f"{what} is only available on Windows for now.")


def create_shortcut(workspace_name: str | None = None) -> None:
    """The desktop shortcut: the dashboard's window, with no console (shortcuts.window_argv); without Qt
    the terminal dashboard while it is kept. A workspace's opens its sessions in a terminal tab."""
    _windows_only("Creating shortcuts")
    from . import desktop

    name = shortcuts.PICKER_TITLE
    if workspace_name:
        workspace = _get_workspace(workspace_name)  # names are kept plain, see clean_name
        name = f"{workspace.name} (Claude)"
        argv = shortcuts.picker_argv(["open", workspace.name], title=name)
    elif desktop.available():
        argv = shortcuts.window_argv()
    elif has_textual():
        argv = shortcuts.picker_argv([shortcuts.TERMINAL], title=name)
    else:
        sys.exit(f"The dashboard is a desktop window, which needs Qt: {CORE_INSTALL}")
    try:
        path = shortcuts.write_shortcut(shortcuts.special_folder("Desktop"), name, argv,
                                        "Reopen recent Claude Code sessions")
    except OSError as error:
        sys.exit(f"Could not create the shortcut: {error}")
    print(f"Created {path}")


def manage_startup(action: str) -> None:
    """At login: a Startup shortcut on Windows, a LaunchAgent on macOS, an autostart entry on Linux."""
    try:
        path = shortcuts.startup_file()
        if action == "on":
            argv = shortcuts.login_argv()
            if argv is None:
                sys.exit("No windowless launcher found (tabmancer-startup or pythonw); reinstall tabmancer.")
            path = shortcuts.enable_startup(argv)
            print(f"On: {path}\nAt login the dashboard opens only if sessions were open at shutdown.")
        elif action == "off":
            path.unlink(missing_ok=True)
            print("Off: the dashboard no longer opens at login.")
        else:
            print(f"On ({path})" if path.exists() else "Off")
    except OSError as error:
        sys.exit(f"Could not change start at login: {error}")


def startup_main() -> None:
    """Runs at login without a console window: opens the dashboard only when sessions
    were open at the last shutdown, otherwise exits without showing anything. The window
    runs here, windowless already; without Qt, the terminal dashboard in a terminal window
    while it is kept (--terminal)."""
    from . import desktop

    try:
        sessions = find_sessions(quick=True)
        if not open_at_shutdown(sessions, recent_shutdowns()):
            return
        if desktop.available():
            desktop_window(False, "")
        elif has_textual():
            subprocess.Popen(shortcuts.picker_argv([shortcuts.TERMINAL]),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError:
        pass  # nothing could show the error anyway; the desktop shortcut still works


def desktop_main() -> None:
    """The tabmancer-desktop launcher (a gui-script: no console behind it), which the desktop shortcut
    starts: `tabmancer desktop` with what it was given."""
    main(["desktop", *sys.argv[1:]])
