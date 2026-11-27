"""The list's words with no front behind them (rowwords, viewmodel): on the demo, rowwords over the store's
read says, row for row, what the terminal list said (the goldens in tests/golden/terminal, as
test_terminal_golden checks the window's table against them); the Title column's room and the Model column's,
a title's marks, a state's words and how they move, the turning star; the list's groups; a member's model in
words; the top bar's limits and agy's chain; a team's tabs by name; the demo's second account."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from tabmancer import accounts, accountwords, demo, launch, picks, rowwords, teams, usage
from tabmancer import viewmodel as VM
from tabmancer.sessions import Exit, Session, Status

TESTS = Path(__file__).parent


def session(sid="s", status=Status.DONE, exit=Exit.CLEAN, live="", title="Fix the login page", **more) -> Session:
    return Session(session_id=sid, path=None, cwd="C:/work/demo", status=status, exit=exit, live_state=live,
                   title=title, **more)


def in_hidden(hidden: set[str], one: Session) -> bool:
    return one.session_id in hidden


def rows_now(said: dict) -> dict:
    """The list's rows as rowwords says them over the store's read, the front's state as the golden's listing
    has it (said): its room, its accent, and the sessions ticked at the start (VM.preselect)."""
    from tabmancer.store import DashboardStore

    snap = DashboardStore().poll()
    hidden = partial(in_hidden, snap.hidden)
    ticked = VM.preselect(snap.sessions, snap.open_at_shutdown, hidden)
    listing = rowwords.Listing(
        VM.group(VM.visible(snap.sessions.values(), hidden), snap.pinned, snap.team_states), snap.sessions,
        snap.team_states, said["accent"], said["width"], model_shown=said["model_shown"], selected=frozenset(ticked),
        pinned=snap.pinned, is_hidden=hidden, armed=snap.armed, queued=snap.queued, tokens=snap.session_tokens,
        default_cap=picks.cap_tokens(picks.default_cap()))
    rows = []
    for key, one in rowwords.keys(listing):
        words = rowwords.row(listing, key, one)
        assert len(words.cells) == len(rowwords.COLUMNS)
        rows.append({"key": key, "cells": dict(zip(rowwords.COLUMNS, [[list(part) for part in cell]
                                                                     for cell in words.cells], strict=True)),
                     "motion": words.motion})
    return {"selected": sorted(listing.selected), "rows": rows}


CHILD = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import golden_terminal as G
G.freeze()
home = Path(sys.argv[2])
G.prepare(home)
import test_rows_core as T
said = json.loads(G.load()["rows"])["listing"]
print("ROWS " + json.dumps(G.mask(getattr(T, sys.argv[3])(said), G.masks(home)), ensure_ascii=False))
'''


def test_on_the_demo_rowwords_says_what_the_terminal_list_said(tmp_path):
    """In a child process: the clock stops (golden_terminal.freeze) before tabmancer is imported, and the
    demo's world is its own (golden_terminal.prepare), as the goldens were written."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", CHILD, str(TESTS), str(tmp_path / "home"), rows_now.__name__], check=False, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300, stdin=subprocess.DEVNULL)
    out = [line for line in done.stdout.splitlines() if line.startswith("ROWS ")]
    assert done.returncode == 0 and out, done.stderr[-3000:]
    now = json.loads(out[-1].removeprefix("ROWS "))
    golden = json.loads((TESTS / "golden" / "terminal" / "rows.json").read_text(encoding="utf-8"))
    assert now["selected"] == golden["listing"]["selected"]
    assert [row["key"] for row in now["rows"]] == [row["key"] for row in golden["rows"]]
    for mine, theirs in zip(now["rows"], golden["rows"], strict=True):
        assert mine == theirs, mine["key"]


def test_the_title_gets_the_room_the_model_goes_first():
    wide, shown = rowwords.title_room(400, live=False)
    assert shown and wide == rowwords.TITLE_WIDEST  # never wider than its widest
    cells = rowwords.OTHER_COLUMNS["picker"] + rowwords.MODEL_COLUMN + rowwords.TITLE_MIN
    assert rowwords.title_room(cells, live=False) == (rowwords.TITLE_MIN, True)  # just room for both
    assert rowwords.title_room(cells - 1, live=False) == (rowwords.TITLE_MIN + rowwords.MODEL_COLUMN - 3, False)
    assert rowwords.title_room(20, live=False) == (rowwords.TITLE_MIN, False)  # never cut below its least
    assert rowwords.title_room(cells, live=True)[0] < rowwords.title_room(cells + 40, live=True)[0]
    assert rowwords.title_room(cells, live=True) != rowwords.title_room(cells, live=False)  # the live view's columns
    assert rowwords.labels(False, False)[rowwords.COLUMNS.index("model")] == ""  # its heading empty with no room


def test_a_title_says_pinned_or_hidden_and_keeps_to_its_room():
    one = session(title="A rather long title that goes on and on past its room")
    pinned = rowwords.title(one, pinned=True, hidden=False, width=20, style="bold", accent="#d97757")
    hidden = rowwords.title(one, pinned=False, hidden=True, width=20, style="bold", accent="#d97757")
    plain = rowwords.title(one, pinned=False, hidden=False, width=20, style="bold", accent="#d97757")
    assert pinned[0] == ("★ ", "#d97757") and hidden[0][0] == "hidden · "
    assert plain[0][1] == "bold" and plain[0][0].startswith("A rather")
    for cell in (pinned, hidden, plain):
        assert len("".join(words for words, _ in cell)) <= 20


def test_a_state_says_what_it_does_and_how_it_moves():
    words, motion = rowwords.status(session(status=Status.RUNNING, exit=Exit.RUNNING, live="busy"), "#d97757")
    assert motion == "spin" and words[0][0] == f"{rowwords.STILL_STAR} " and rowwords.STILL_STAR in rowwords.STAR
    words, motion = rowwords.status(session(status=Status.RUNNING, exit=Exit.RUNNING, live="blocked"), "#d97757")
    assert motion == "pulse" and words[0][0].startswith("! ")
    words, motion = rowwords.status(session(status=Status.BACKGROUND, exit=Exit.RUNNING, live="idle"), "#d97757")
    assert motion == "" and words[0][0].endswith(" · bg")
    label, _ = VM.STATUS[Status.DONE]
    words, motion = rowwords.status(session(), "#d97757")
    assert motion == "" and words[0][0] == label  # closed: how it ended, as a badge


def test_the_star_turns_through_its_frames_eight_a_second():
    assert "".join(rowwords.star_at(n / 8) for n in range(len(rowwords.STAR))) == rowwords.STAR
    assert rowwords.star_at(len(rowwords.STAR) / 8) == rowwords.STAR[0]  # and round again
    assert not any("\U0001f300" <= ch or ch == "\ufe0f" for ch in rowwords.STAR)  # none drawn as an emoji


def test_the_model_cell_is_drawn_on_its_first_parts_style():
    assert rowwords.BASED <= set(rowwords.COLUMNS)
    cell = rowwords.model(session(model="claude-opus-5-5"), "high", shown=True)
    assert cell and cell[0][0].startswith("opus 5.5")
    assert rowwords.model(session(model="claude-opus-5-5"), "high", shown=False) == []


def test_the_list_groups_open_then_pinned_then_the_rest():
    shown = [session("live", Status.RUNNING, Exit.RUNNING, "idle"), session("kept"), session("old")]
    groups = VM.group(shown, {"kept"}, {}, teams_too=False)
    assert [(name, [s.session_id for s in items]) for name, items in groups.groups] == [
        ("open", ["live"]), ("pinned", ["kept"]), ("rest", ["old"])]
    assert [name for name, _ in VM.group(shown[2:], set(), {}).groups] == ["rest"]  # a group with none is left out


def test_a_members_model_in_words():
    member = partial(VM.MemberView, "api", False, "", None, "", "", "")
    assert VM.model_words(member()) == "default model"
    assert VM.model_words(member(cli="agy")) == "no model now"
    assert VM.model_words(member(model="claude-opus-5-5", effort="high")) == "opus 5.5 · high"
    neon = VM.model_words(member(cli="agy", model="gemini-4-neon-high", effort="high"))
    assert neon.startswith("Neon") and "·" not in neon  # its level is in its name


def test_the_limits_in_the_top_bar_and_agys_chain():
    now = datetime(2026, 11, 27, 12, 0)
    limits = [usage.Limit("session", 30, now + timedelta(hours=2), ""), usage.Limit("week", 70, None, ""),
              usage.Limit("week Fable", 40, None, ""), usage.Limit("week Opus", 90, None, "")]
    parts = VM.usage_parts(limits, now=now)
    text = "".join(words for words, _ in parts)
    assert "session " in text and "week " in text and "week Opus" in text and "Fable" not in text  # half full first
    tones = {words.strip(): tone for words, tone in parts if words.strip().endswith("%")}
    assert tones == {"30%": "bold ok", "70%": "bold low", "90%": "bold out"}
    assert "▰" * 2 + "▱" * 6 in text  # 30% of eight
    chain = [("Neon 54% left", "ok"), ("Hydrogen 0% left", "out")]
    after, first = VM.usage_parts(limits, chain, now=now), VM.usage_parts(limits, chain, agy_first=True, now=now)
    assert after[0][0] == "session " and first[0] == ("agy ", "muted") and ("Hydrogen 0% left", "bold out") in first
    assert VM.usage_parts([], chain, now=now)[0] == ("agy ", "muted")  # agy alone


def test_a_teams_tabs_are_named_team_colon_member(tmp_path):
    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x"))
    opened = teams.start(team, {})
    assert [one.title for one in opened] == ["shop: api", "shop: lead"]  # the lead last
    assert all(isinstance(one, launch.Launch) for one in opened)


def test_the_demo_has_a_second_account_and_a_session_from_its_organisation(tmp_path, monkeypatch):
    for name in ("CLAUDE_CONFIG_DIR", "TABMANCER_CONFIG_DIR", "CODEX_HOME", "TABMANCER_BUS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "before" / name))  # put back after (prepare sets them)
    demo.prepare(tmp_path / "home")
    work = demo._account(demo.WORK)
    assert work.email == demo.WORK["emailAddress"] and work.org_id == demo.WORK["organizationUuid"]
    assert work.key in [account.key for account, _ in accounts.known()]
    assert usage.history(days=1, account=work.key)  # its readings of this morning
    signed = accounts.signed_in()
    assert signed and signed[0].key != work.key  # signed in now with the other one
    from tabmancer.store import DashboardStore

    listed = DashboardStore().poll().sessions
    assert [s.session_id for s in accountwords.ran_elsewhere(signed[0], [listed["wiki"], listed["ml"]])] == ["wiki"]
