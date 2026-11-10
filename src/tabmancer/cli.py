"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import timedelta

from . import claudeupdate, clis, shortcuts, workspaces
from .config import read_json, write_json
from .launch import Launch, continue_prompt, open_tabs
from .picks import EFFORTS, NO_CAP, Pick, advisor_ok, cap_tokens
from .sessions import find_sessions, nothing_found
from .shutdowns import open_at_shutdown, recent_shutdowns

PROG = shortcuts.PROG


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
        description="A terminal dashboard for Claude Code sessions: see what they are doing, write to them, "
                    "reopen them after a reboot.")
    # The same filters work before or after the subcommand. SUPPRESS keeps the
    # subcommand from overwriting a value given before it with its own default.
    filters = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    for target in (parser, filters):
        target.add_argument("--days", type=float, help="how far back to look (default 14)")
        target.add_argument("--all", action="store_true",
                            help="include non-terminal sessions (claude -p, desktop app, IDE)")
    parser.add_argument("--demo", action="store_true",
                        help="the dashboard over made-up sessions, to try it: nothing of yours is read or changed")
    parser.set_defaults(days=None, all=False)  # 14, or a week for report: see below
    sub = parser.add_subparsers(dest="command", metavar="command")
    listing = sub.add_parser("list", parents=[filters],
                             help="print recent sessions instead of opening the picker")
    listing.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("live", parents=[filters], help="watch open sessions: which are working, which need you")
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
    using.add_argument("action", nargs="?", choices=["enable", "disable"])
    using.add_argument("names", nargs="*", metavar="name", help=", ".join(clis.CLIS))
    sub.add_parser("cleanup", help="free disk space: move old sessions to the Recycle Bin")
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
    startup = sub.add_parser("startup", help="open the picker at login when sessions were left open")
    startup.add_argument("action", choices=["on", "off", "status"])
    teaming = sub.add_parser("team", help="add members to a team (what a lead that decides runs), or start the "
                                          "ones that are closed (what a lead runs to bring one back)")
    teaming.add_argument("action", choices=["add", "start"])
    teaming.add_argument("team", help="the team's name")
    teaming.add_argument("members", nargs="?", default="",
                         help='add: "name: area @model @effort; name: area" (model and effort are optional); '
                              "start: the members to start, by name (none: every one that is not open)")
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
    # What the login shortcut runs when there is no windowless gui launcher.
    sub.add_parser("startup-run", help="(used by the login shortcut)")
    args = parser.parse_args(argv)
    if args.days is None:
        args.days = 7 if args.command == "report" else 14

    if args.demo:
        if args.command:
            parser.error("--demo opens the dashboard: leave out the command")
        from .demo import run

        run(args.days)
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
        from .cleanup import CleanupApp

        CleanupApp().run()
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
        claudeupdate.before_opening(print)  # today's claude update, before the members open
        sys.exit(start_team(args.team, args.members) if args.action == "start" else add_to_team(args.team, args.members))
    elif args.command == "startup-run":
        startup_main()
    else:
        from . import sixel
        from .app import SessionPicker  # Textual is slow to import; only the picker needs it

        sixel.probe()  # whether Bit can be drawn in real pixels: asked before Textual reads the keys
        # The picker opens sessions itself and stays open; it only hands back commands
        # when no supported terminal can open tabs.
        print_leftover(SessionPicker(args.days, args.all, live=args.command == "live").run())


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
    added, note = teams.add_members(team, spec)
    if not added:
        print(f"Nobody joined{f': {note}' if note else ''}. Write members as \"name: area @model @effort; name: area\".")
        return 1
    team = teams.load()[team.name]  # with them on it: their roles name everyone
    joining = teams.joining(team, added)
    leftover: list[str] = []
    if team.background:  # no tabs: each its own background session
        from .launch import run_in_background

        for launch in joining:
            try:
                run_in_background(launch)
            except OSError as error:
                print(f"{launch.title} did not start in the background: {error}")
        where = "They are starting in the background, with no tabs,"
    else:
        try:
            leftover = open_tabs(joining)
        except OSError as error:
            print(f"They joined the team {team.name}, but their tabs could not open: {error}. The dashboard's s "
                  "starts them.")
            return 1
        where = "Their tabs are opening"
    names = ", ".join(f"{team.session_name(m)} ({m.focus or m.name}{f', {m.pick.words()}' if m.pick else ''})"
                      for m in added)
    told = team.session_name(team.lead) if team.lead is not None else "the others"
    print(f"Joined the team {team.name}: {names}. {where} and each will introduce itself to "
          f"{told}. Message them with SendMessage by those names.")
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
    launches = teams.start(team, found, only={m.name for m in chosen if team.session_name(m) not in left_be})
    if already:
        print(f"Open already: {', '.join(already)}. Message them with SendMessage.")
    if starting:
        print(f"Opening a moment ago, so not opened again: {', '.join(starting)}. Wait for ListAgents to show them.")
    if other:
        print(f"A session of the name {', '.join(other)} is open, not this team's: close it, then start the member.")
    if not launches:
        return 0 if not other else 1
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
            return 1
        if leftover:  # no terminal here could open tabs: say so, so nobody waits for them
            print("No terminal here could open their tabs, so none started. Run these:\n" + "\n".join(leftover))
            return 1
        started += tabs
    teams.note_opening([teams.session_name_of(team, launch) for launch in started])

    def said(launch: Launch) -> str:
        how = "where it left off" if launch.mode is Mode.RESUME else "new, as it never started"
        return f"{teams.session_name_of(team, launch)} ({how})"

    if started:
        where = ("in the background" if all(launch in quiet for launch in started) else
                 "in their tabs" if not any(launch in quiet for launch in started) else "in tabs and the background")
        print(f"Starting {', '.join(said(launch) for launch in started)} {where}. Each comes back with its name "
              "and role; message them with SendMessage by those names once ListAgents shows them.")
    for line in failed:
        print(line)
    return 0 if not failed and not other else 1


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
    saved = workspaces.load()
    if delete:
        workspace = _get_workspace(delete)
        del saved[workspace.name]
        workspaces.save(saved)
        print(f"Deleted workspace {workspace.name!r}.")
        return
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
    from . import newsessions, teams

    cli = cli or newsessions.default_cli()
    if pick and cli != "claude":
        sys.exit("--model, --effort, --autocompact and --advisor are for Claude Code sessions.")
    if pick and not advisor_ok(pick.model, pick.advisor):
        sys.exit(f"{pick.advisor} cannot advise {pick.model}: only a model as strong or stronger can.")
    if linked and (cli != "claude" or len(names) != 1 or len(newsessions.topics(prompt)) < 2):
        sys.exit("--linked takes one folder, two --prompt or more, and Claude Code.")
    listed = find_sessions(days, quick=True)
    known = newsessions.projects(listed)
    folders, missing = [], []
    for name in names:
        project = newsessions.match(name, known)
        if project:
            folders.append(project.cwd)
        else:
            missing.append(name)
    if missing:
        sys.exit(f"Not a folder, and no recent project of that name: {', '.join(missing)}")
    if linked:
        saved = teams.load()
        team = newsessions.linked_team(folders[0], prompt or "", pick or Pick(), set(saved))
        saved[team.name] = team
        teams.save(saved)
        launch_all(teams.start(team, {}))
        print(f"The team of peers {team.name}: {', '.join(team.session_name(m) for m in team.members)}. "
              "T in the dashboard shows it.")
        return
    launch_all(newsessions.launches(folders, cli, prompt or "", pick))


def manage_clis(action: str | None, names: list[str]) -> None:
    unknown = [name for name in names if name not in clis.CLIS]
    if unknown or (action and not names):
        sys.exit(f"Name one or more of: {', '.join(clis.CLIS)}" + (f" (not {', '.join(unknown)})" if unknown else ""))
    if action:
        on = set(clis.enabled())
        on = on | set(names) if action == "enable" else on - set(names)
        clis.set_enabled(sorted(on))
    on = clis.enabled()
    for cli in clis.CLIS.values():
        where = clis.installed(cli.name) or "not found on PATH"
        print(f"{'on ' if cli.name in on else 'off'}  {cli.label:<12} {cli.name:<7} {where}")
    if not action:
        print(f"\nTurn one on with: {PROG} clis enable codex   (or press , in the dashboard)")


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
    from .theme import DEFAULT_THEME, THEMES

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
    from .statsview import plain_text
    from .theme import DEFAULT_THEME, THEMES

    usage.record(usage.read_all())  # a fresh reading for the limits part
    before = None if days is None else stats.before(history.collect(report.days * 2), report.days, sessions)
    console = Console()
    for part in plain_text(report, sessions, THEMES[DEFAULT_THEME][1], before):
        console.print(part)


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

    from .statsview import render
    from .theme import DEFAULT_THEME, THEMES

    sessions = {s.session_id: s for s in find_sessions(days, include_all=True, quick=True)}
    Console().print(render(report, sessions, THEMES[DEFAULT_THEME][1]))


def _windows_only(what: str) -> None:
    if sys.platform != "win32":
        sys.exit(f"{what} is only available on Windows for now.")


def create_shortcut(workspace_name: str | None = None) -> None:
    _windows_only("Creating shortcuts")
    name, extra = shortcuts.PICKER_TITLE, []
    if workspace_name:
        workspace = _get_workspace(workspace_name)  # names are kept plain, see clean_name
        name, extra = f"{workspace.name} (Claude)", ["open", workspace.name]
    try:
        path = shortcuts.write_shortcut(shortcuts.special_folder("Desktop"), name,
                                        shortcuts.picker_argv(extra, title=name),
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
            print(f"On: {path}\nAt login the picker opens only if sessions were open at shutdown.")
        elif action == "off":
            path.unlink(missing_ok=True)
            print("Off: the picker no longer opens at login.")
        else:
            print(f"On ({path})" if path.exists() else "Off")
    except OSError as error:
        sys.exit(f"Could not change start at login: {error}")


def startup_main() -> None:
    """Runs at login without a console window: opens the picker only when sessions
    were open at the last shutdown, otherwise exits without showing anything."""
    try:
        sessions = find_sessions(quick=True)
        if not open_at_shutdown(sessions, recent_shutdowns()):
            return
        subprocess.Popen(shortcuts.picker_argv([]),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError:
        pass  # nothing could show the error anyway; the desktop shortcut still works
