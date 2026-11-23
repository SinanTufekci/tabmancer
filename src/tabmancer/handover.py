"""Handing a crowded session over to a fresh one, when you decide to.

A session whose context has filled up gets slow, costly and forgetful. A handover moves
the work to a fresh session without losing the thread. A full handover text becomes the
fresh session's first message; it is not written to a file. The text comes from one of
two places:

- the old session itself, which knows everything: it is resumed once without a tab
  (`claude -p --resume`), writes the handover and exits;
- or Claude, writing it from the transcript with `claude -p`.

An open session is closed on the way (its tab is closed, so Claude exits cleanly), and
only while it waits for you: one that is working is left alone. It stays resumable.

Nothing happens on its own: the dashboard only says when a session has grown crowded.

A team is handed over as a whole (h in its view): its open members close, its lead writes the
handover and the line-up the rest of the work needs, you see both and change the line-up as you
like, and the team starts again as a new generation of itself, under the same name: a fresh lead
with the handover, fresh members. Their sessions are new ones, so none carries a long context on,
and they start under the context cap (see picks). The old sessions stay, resumable, as sessions of
their own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from . import focus, gitstate, picks, router, summarize, teams
from .launch import Launch, Mode, clean_env
from .sessions import Session, _registry_sessions, _shorten, folder_key, sanitize

MODEL = os.environ.get("TABMANCER_HANDOVER_MODEL", "sonnet")
WRITE_SECONDS = 900  # a crowded session reads its whole context back before it writes
CLOSE_SECONDS = 15
IDLE = {"idle", "done", "resting"}  # live states in which a session waits (resting: a member, for its lead)
AGAIN = "Close it yourself (/exit), then press h again"  # when an open session cannot be closed
# The handover travels on the command line of the new tab, which Windows caps at 32K characters.
MAX_CHARS = 20_000
START, END = "<handover>", "</handover>"
LINEUP_START, LINEUP_END = "<lineup>", "</lineup>"
REQUEST = (
    "We are handing this work over to a fresh session that will not see this conversation. Write the "
    "handover now, complete enough for it to carry on without asking anything: the goal; the decisions "
    "made and why; what is done, with the files; what is open or broken; the gotchas you ran into; and the "
    "exact next steps, with any commands to run. At most 1,200 words. Write it in the language we have been "
    f"using. Put the whole handover between {START} and {END}, and write nothing after it.")
INSTRUCTIONS = """Write a handover from the notes below on a coding-agent session, for a fresh session
that will carry on the work without the old conversation. Markdown with these sections: Goal,
Decisions, Done (with the files), Open or broken, Gotchas, Next steps. Concrete, complete, at
most 500 words, in the same language as the user's messages. Only what the notes support. No
preamble. Do not use any tools.
"""


def opening(session: Session) -> str:
    return (f"You are taking over from an earlier session in this folder ({session.last_active:%d %b %H:%M}, "
            f"“{_shorten(session.title, 60)}”), which had grown too long. Its handover follows. Read it, check "
            "the current state of the files, then carry on.")


def from_transcript(session: Session) -> str:
    """Claude writes the handover from the transcript (one claude -p call)."""
    if session.cli != "claude":
        notes = summarize.session_digest(session)
    else:
        notes = summarize.digest(session, max_prompts=80, keep_replies=12)
        made = commits(session.path)
        if made:  # the surest record of what got done
            notes += "\n\nCommits made, oldest first:\n" + "\n".join(f"- {m}" for m in made[-40:])
    return summarize.ask(INSTRUCTIONS + "\n" + notes, MODEL, timeout=300).strip()


COMMIT = re.compile(r"""git\s+commit\b[^\n]*?-m\s+(?:"([^"]+)"|'([^']+)'|@'\s*\n([^\n]+))""")


def commits(path: Path | None) -> list[str]:
    """The subject lines of the commits the session made, from its shell tool calls."""
    if path is None:
        return []
    found: list[str] = []
    try:
        with path.open("rb") as f:
            for raw in f:
                if b"git commit" not in raw or b'"type":"assistant"' not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                content = (sanitize(record).get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    command = (block.get("input") or {}).get("command")
                    if block.get("type") == "tool_use" and isinstance(command, str):
                        found += [next(g for g in match.groups() if g).strip()
                                  for match in COMMIT.finditer(command)]
    except OSError:
        return found
    return list(dict.fromkeys(found))


class HandoverError(Exception):
    pass


def cannot_close(session: Session, open_sessions: list[Session], again: str = AGAIN) -> str | None:
    """Why this open session cannot be closed now; None if it can. `again` says what to do instead."""
    if session.live_state not in IDLE:
        return "it is working. Hand it over once it waits for you"
    if not session.title or sum(s.title == session.title for s in open_sessions if s.cli == session.cli) > 1:
        return f"another open session has the same title, so its tab cannot be told apart. {again}"
    return None


def close(session: Session, open_sessions: list[Session], again: str = AGAIN) -> None:
    """Close an open session by closing its Windows Terminal tab, and wait until it has exited."""
    reason = cannot_close(session, open_sessions, again)
    if reason:
        raise HandoverError(reason)
    if not focus.close_tab(session.title):
        raise HandoverError(f"its tab was not found in Windows Terminal. {again}")
    role = teams.member_of(session) if session.cli == "agy" else None
    deadline = time.monotonic() + CLOSE_SECONDS
    while time.monotonic() < deadline:
        if session.cli == "agy":
            from . import teambus

            if role is None or not teambus.is_alive(role[0].name, role[1].name):
                return
        elif not any(info.get("sessionId") == session.session_id for info in _registry_sessions()):
            return
        time.sleep(0.5)
    who = "Antigravity" if session.cli == "agy" else "Claude"
    raise HandoverError(f"its tab closed, but {who} did not exit")


def write_itself(session: Session, request: str = REQUEST) -> str:
    """The session writes its own handover: resumed once without a tab, it answers and exits. With a
    request of another kind (a lead's, for its team: team_request), the whole answer comes back."""
    if not session.cwd or not Path(session.cwd).is_dir():
        raise HandoverError(f"its folder is gone ({session.cwd})")
    with tempfile.TemporaryDirectory(prefix="tabmancer-handover-") as scratch:
        last = Path(scratch) / "answer.md"  # where Codex writes its last message (-o)
        cmd, model = _resume_command(session, last)
        try:
            # The request goes in on stdin: through a .cmd shim, cmd.exe would take < and > for redirects.
            result = subprocess.run(
                cmd, input=request, cwd=session.cwd,
                env=clean_env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=WRITE_SECONDS, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise HandoverError(f"it was still writing after {WRITE_SECONDS // 60} minutes") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise HandoverError(str(error)) from error
        try:
            written = last.read_text(encoding="utf-8", errors="replace").strip() if last.is_file() else ""
        except OSError:
            written = ""
    answer = written or result.stdout.strip()
    if result.returncode != 0 or not answer:
        if session.cli == "agy":
            from . import agy

            agy.note_quota_error(f"{result.stderr}\n{result.stdout}", model)  # out of quota: the chain moves on
        raise HandoverError(result.stderr.strip()[:300] or f"{session.cli} returned nothing")
    if START in answer and request == REQUEST:
        answer = answer.split(START, 1)[1].split(END, 1)[0].strip()
    return answer


def _resume_command(session: Session, last: Path) -> tuple[list[str], str]:
    """The command that resumes a session once without a tab, the request on stdin and the answer on
    stdout (Codex: in the file `last` too), and the model it runs on ("" when its CLI picks).

    agy: the chain's model (agy.model_flags), never the one its transcript names when that is out of
    the chain (Argon, a Claude model); every model out of quota is a HandoverError, and nothing runs.
    Codex: its own headless resume (codex exec resume), never claude's."""
    if session.cli == "agy":
        from . import agy

        agy_bin = shutil.which("agy")
        if not agy_bin:
            raise HandoverError("the agy CLI is not on PATH")
        try:
            flags, _, _ = agy.model_flags("", session_id=session.session_id)
        except agy.NoModel as error:
            raise HandoverError(str(error)) from error
        return [agy_bin, "-p", "--conversation", session.session_id, *flags], flags[1] if flags else session.model
    if session.cli == "codex":
        codex = shutil.which("codex")
        if not codex:
            raise HandoverError("the codex CLI is not on PATH")
        model = ["-m", session.model] if session.model else []
        return [codex, "exec", "resume", "--skip-git-repo-check", "-o", str(last), *model,
                session.session_id, "-"], session.model
    if session.cli != "claude":
        raise HandoverError(f"a {session.cli} session cannot be resumed to write its handover")
    claude = shutil.which("claude")
    if not claude:
        raise HandoverError("the claude CLI is not on PATH")
    model = ["--model", session.model] if session.model else []
    return [claude, "-p", "--resume", session.session_id, *model], session.model


def fresh(session: Session, handover: str) -> Launch:
    """The fresh session: same folder and CLI, the handover as its first message, the model and
    effort tabmancer opened the old one with (picks), and for a team member the same name and role."""
    if len(handover) > MAX_CHARS:
        handover = (handover[:MAX_CHARS].rsplit("\n", 1)[0]
                    + "\n\n(The handover was cut here: it was too long to send. Ask for the rest if you need it.)")
    prompt = f"{opening(session)}\n\n{handover}"
    role = teams.member_of(session)
    if role is not None:
        team, member = role
        return teams.launch(team, member, prompt=prompt)
    return picks.fresh(Launch(title=f"{session.project}: handed over", cwd=session.cwd, mode=Mode.NEW,
                              session_id="", prompt=prompt, cli=session.cli), picks.recall(session.session_id))


# --- a whole team ----------------------------------------------------------------------------------

def team_request(team: teams.Team, left: str = "") -> str:
    """What a team's lead is asked: the handover for a new lead, and the line-up for the rest of the work.
    With left (work_left), what git shows still lying in the team's folders, for it to say what
    becomes of each."""
    members = "\n".join(f"- {m.line()}" for m in team.members if not m.lead) or "- (none yet)"
    branches = (" Each member worked on a branch of its own: say for each whether it is merged, and what is "
                "left on it. A member whose name you keep goes on in its worktree, on that branch; a new name "
                "starts on a fresh one." if team.worktrees else "")
    lying = (f"\n\nWhat git shows in the team's folders right now, before anyone closes:\n{left}\nSay in the "
             "handover what becomes of each: to commit, to merge or to drop, and who does it."
             if left else "")
    cli_hint = " Keep @agy on members that should run on Antigravity." if team.uses_bus else ""
    return (
        "We are handing this team's work over to a fresh team: a new lead in your place and fresh members, none of "
        "whom will see this conversation or your members' conversations. Your members now:\n"
        f"{members}{lying}\n\n"
        "Write the handover for the new lead, complete enough for it to carry on without asking anything: the "
        "goal; the decisions made and why; what each member did and where its part stands, with the files; what is "
        f"open or broken; the gotchas you ran into; and the exact next steps.{branches} At most 1,200 words, in the "
        f"language we have been using. Put the whole handover between {START} and {END}.\n\n"
        f"Then, between {LINEUP_START} and {LINEUP_END}, the line-up the rest of the work needs, one member a "
        "line:\nname: what it works on [the folders or files that are its own] @model @effort\n"
        "No lead line: the new lead is there. Names: one short lowercase word, as above; keep a member's name "
        "where its part goes on. As few members as the work left needs, each with a part that can go on at the "
        f"same time as the others. Inside a line, use commas, not semicolons.{cli_hint} {router.GUIDE}\n"
        "Write nothing after the line-up.")


def read_team(answer: str, team: teams.Team | None = None) -> tuple[str, str]:
    """A lead's answer as (the handover, the line-up as the members box takes it, no lead line). A
    member named by its session name (shop-api, as the lead knows it) is named as a member (api)."""
    lineup = ""
    if LINEUP_START in answer:
        lineup = answer.split(LINEUP_START, 1)[1].split(LINEUP_END, 1)[0]
        rest = answer.split(LINEUP_START, 1)[0]
    else:
        rest = answer
    text = rest.split(START, 1)[1].split(END, 1)[0] if START in rest else rest
    members = [m for m in teams.parse_members(teams.suggested_lines(lineup), lead_ok=True) if not m.lead]
    if team is not None:
        prefix = f"{team.name}-"
        members = [replace(m, name=m.name[len(prefix):]) if m.name.startswith(prefix) and len(m.name) > len(prefix)
                   else m for m in members]
    return text.strip(), "\n".join(m.line() for m in members)


def write_team(lead: Session, team: teams.Team, left: str = "") -> tuple[str, str]:
    """The lead writes the team's handover and the line-up that goes on: resumed once without a tab.
    What git shows left in the folders (work_left) is asked about, and added to the handover as it is,
    so the new lead has it even where the old one left something out."""
    text, lineup = read_team(write_itself(lead, team_request(team, left)), team)
    if not text:
        raise HandoverError("the lead's answer held no handover")
    if left:
        text += f"\n\n## {LEFT_HEADING}\n\n{left}"
    return text, lineup


LEFT_HEADING = "Left in the team's folders at the handover (from git)"
PROJECT = "the project"


def team_folders(team: teams.Team, members: dict[str, Session | None]) -> list[tuple[str, str]]:
    """(who, folder) for each folder the team works in: the project (the lead's), and each member's
    worktree where it has one. Members working in the project itself share its line."""
    out = [(PROJECT, team.cwd)]
    seen = {folder_key(team.cwd)}
    for member in team.members:
        if member.lead:
            continue
        session = members.get(member.name)
        if session is not None and session.cwd:
            folder = teams.home_of(team, session.cwd)
        elif team.has_worktree(member):
            folder = str(Path(team.cwd, *teams.WORKTREES, team.session_name(member)))
        else:
            continue
        if folder_key(folder) not in seen and Path(folder).is_dir():
            seen.add(folder_key(folder))
            out.append((member.name, folder))
    return out


def unmerged(project: str, branch: str) -> int:
    """Commits on a member's branch that the project's checked-out branch does not have; 0 if none
    or git cannot tell."""
    git = shutil.which("git")
    if not git or not branch:
        return 0
    try:
        result = subprocess.run([git, "-C", project, "rev-list", "--count", f"HEAD..{branch}"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=gitstate.TIMEOUT, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return 0
    count = result.stdout.strip()
    return int(count) if result.returncode == 0 and count.isdigit() else 0


def work_left(team: teams.Team, members: dict[str, Session | None]) -> str:
    """What git shows still lying in the team's folders, a line each: files not committed, commits not
    pushed, a member's branch not merged into the project's. '' when every folder is clean (or none is
    in git). Read before the tabs close, so a handover cannot leave it behind unnoticed."""
    folders = team_folders(team, members)
    # The members' worktrees sit in the project (.claude/worktrees): not the project's own files.
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(lambda folder: gitstate.read(folder, ("/".join(teams.WORKTREES),)),
                               [folder for _, folder in folders]))
    lines = []
    for (who, folder), state in zip(folders, states):
        if not isinstance(state, gitstate.GitState):
            continue
        parts = [f"{state.changed} file{'s' * (state.changed != 1)} not committed"] if state.changed else []
        if state.ahead and state.upstream:
            parts.append(f"{state.ahead} commit{'s' * (state.ahead != 1)} not pushed")
        if who != PROJECT and state.branch and (behind := unmerged(team.cwd, state.branch)):
            parts.append(f"{behind} commit{'s' * (behind != 1)} not merged into the project's branch")
        if parts:
            lines.append(f"- {who} ({folder}, branch {state.branch or '?'}): {', '.join(parts)}")
    return "\n".join(lines)


def current_lineup(team: teams.Team) -> str:
    """The members as they are, as lines: the line-up when the lead suggested none."""
    return "\n".join(m.line() for m in team.members if not m.lead)


def next_team(team: teams.Team, lineup: str, cap: str = "") -> teams.Team:
    """The team's next generation: the same name, folder and goal, its lead as it was (its part, model
    and effort), the members of the line-up, made now, so that the old sessions are no longer its
    members; under the cap given, else the team's, else the one new sessions get."""
    lead = team.lead or teams.Member(teams.LEAD, teams.LEAD_FOCUS, lead=True)
    members = [m for m in teams.parse_members(lineup) if m.name != lead.name]
    return teams.new_team(team.name, team.cwd, team.goal, [lead, *members], team.decide, team.background,
                          worktrees=team.worktrees, cap=cap or team.cap)


def team_opening(team: teams.Team, lead: Session) -> str:
    return (f"You are the new lead of the team {team.name}, taking over from the lead before you "
            f"({lead.last_active:%d %b %H:%M}), whose conversation had grown too long. Your members are fresh "
            "sessions too, and know nothing of the work yet. The handover follows. Read it, check the current state "
            "of the files, then send each member its first task.")


def team_launches(team: teams.Team, lead: Session, handover: str) -> list[Launch]:
    """The new generation's tabs: its members first, fresh, then the lead with the handover."""
    if len(handover) > MAX_CHARS:
        handover = (handover[:MAX_CHARS].rsplit("\n", 1)[0]
                    + "\n\n(The handover was cut here: it was too long to send. Ask for the rest if you need it.)")
    out = teams.start(team, {}, fresh=True)
    if team.lead is not None:
        prompt = f"{team_opening(team, lead)}\n\n{handover}"
        out = [launch for launch in out if launch.title != f"{team.name}: {team.lead.name}"]
        out.append(teams.launch(team, team.lead, prompt=prompt))
    return out
