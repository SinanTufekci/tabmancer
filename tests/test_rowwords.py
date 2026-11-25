"""rowwords: the dashboard's list as words with no UI behind them. On the demo, every row the terminal
list draws (folded and unfolded teams, the live view, a narrow screen, pinned and hidden sessions)
looks the same drawn from rowwords; and fit() cuts words as rich's Text.truncate does."""

import os
from dataclasses import replace

import pytest
from rich.console import Console
from rich.text import Text

from tabmancer import app as terminal
from tabmancer import demo, rowwords, teamtree
from tabmancer import viewmodel as VM
from tabmancer.sessions import Exit

DEMO_ENV = ("CLAUDE_CONFIG_DIR", "TABMANCER_CONFIG_DIR", "CODEX_HOME")  # what demo.prepare points elsewhere
SCREEN = Console(color_system="truecolor", force_terminal=True, width=400, legacy_windows=False)


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    """The demo's dashboard and its made-up world, put back after the test."""
    for name in DEMO_ENV:
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    tui = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, tui.tell):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(terminal, "STAR", "✶" * len(terminal.STAR))  # a star that stands still
    return tui


def drawn(text: Text) -> list[tuple[str, str]]:
    """What a Text puts on the screen: its words, run by run, each run's whole style (its own style
    under its spans', as a table cell draws it: render() alone leaves it out where there are no spans)."""
    whole = Text()
    whole.append_text(text)
    runs: list[tuple[str, str]] = []
    for segment in whole.render(SCREEN):
        if not segment.text:
            continue
        style = str(segment.style) if segment.style else "none"
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + segment.text, style)
        else:
            runs.append((segment.text, style))
    return runs


def as_text(column: str, parts: list) -> Text:
    """A cell as the terminal will make it from rowwords: a BASED column on its first part's style."""
    if not parts:
        return Text("")
    return terminal.based(parts) if column in rowwords.BASED else Text.assemble(*parts)


async def settled(tui, pilot) -> None:
    """The list, its teams and tokens read whole, the same a while."""
    so_far, steady = None, 0
    for _ in range(400):
        await pilot.pause(0.05)
        now = (len(tui.sessions), len(tui.team_states), len(tui.session_tokens))
        steady = steady + 1 if now == so_far else 0
        so_far = now
        if now[0] >= len(demo.SESSIONS) and all(now) and steady >= 10:
            return
    raise AssertionError("the demo never settled")


def listing_of(tui) -> rowwords.Listing:
    """What the terminal dashboard has now, as a Listing: its list grouped as it groups it (groups())."""
    grouped = VM.group(tui.visible(), tui.marks.pinned, tui.team_states, teams_too=not tui.live_mode)
    return rowwords.Listing(
        grouped, tui.sessions, tui.team_states, tui.accent(), tui.title_width, model_shown=tui.model_shown,
        live=tui.live_mode, unfolded=tui.unfolded, selected=tui.selected, pinned=tui.marks.pinned,
        is_hidden=tui.marks.is_hidden, modes=tui.modes, armed=tui.armed, queued=tui.queued, tokens=tui.session_tokens,
        default_cap=tui.default_cap)


def compare(tui) -> dict[str, int]:
    """Every row of the terminal's list against rowwords' words for it; the kinds of rows seen."""
    tui.flash, tui.expanded = {}, set()  # a lit row and an opened one are the front's own
    shown = tui.rows()
    listing = listing_of(tui)
    assert rowwords.keys(listing) == [(key, session) for key, session, _ in shown]
    seen = {"session": 0, "member": 0, "team": 0, "heading": 0, "spin": 0, "pulse": 0}
    for key, session, _ in shown:
        if session is not None:
            cells = tui.cells(session)
            seen["member" if session.session_id in tui.member_rows and not tui.live_mode else "session"] += 1
        elif key.startswith(rowwords.TEAM_ROW):
            cells, seen["team"] = tui.team_cells(key), seen["team"] + 1
        else:
            cells, seen["heading"] = tui.heading_cells(key), seen["heading"] + 1
        said = rowwords.row(listing, key, session)
        assert len(said.cells) == len(cells) == len(rowwords.COLUMNS)
        if said.motion:
            seen[said.motion] += 1
        for column, theirs, ours in zip(rowwords.COLUMNS, cells, said.cells, strict=True):
            if key.startswith(rowwords.HEADING) and theirs.plain:  # two lines high: a blank line over it
                assert theirs.plain.startswith("\n")
                theirs = theirs[1:]
            mine = as_text(column, ours)
            where = f"{key!r} {column}: {theirs.plain!r}"
            if column == "status" and said.motion in ("spin", "pulse"):  # it moves: the words, not the frame
                assert mine.plain == theirs.plain, where
            else:
                assert drawn(mine) == drawn(theirs), where
    return seen


async def test_every_row_says_what_the_terminal_list_says(in_demo):
    tui = in_demo
    async with tui.run_test(size=(240, 50)) as pilot:
        await settled(tui, pilot)
        folded = compare(tui)
        assert folded["team"] and folded["heading"] and folded["session"] and not folded["member"]
        assert folded["spin"] and folded["pulse"]  # the demo has sessions at work and one that needs you
        tui.action_fold_teams()
        await pilot.pause(0.1)
        unfolded = compare(tui)
        assert unfolded["member"] and unfolded["team"]
        tui.title_width, tui.model_shown = 20, False  # a narrow screen: titles, chips and lines cut
        compare(tui)
        tui.action_fold_teams()
        compare(tui)
        tui.title_width, tui.model_shown = terminal.EXPAND_WIDTH, True
        tui.action_toggle_live()
        await pilot.pause(0.1)
        live = compare(tui)
        assert live["session"] and not live["team"] and not live["heading"]


async def test_pinned_hidden_and_ticked_rows_say_so_as_the_terminal_does(in_demo):
    tui = in_demo
    async with tui.run_test(size=(240, 50)) as pilot:
        await settled(tui, pilot)
        closed = sorted(s.session_id for s in tui.sessions.values()
                        if s.exit is not Exit.RUNNING and s.session_id not in tui.member_rows)
        assert len(closed) >= 3
        tui.marks.pinned, tui.marks.hidden = {closed[0]}, {closed[1]}  # in memory only: nothing saved
        tui.show_hidden = True
        tui.selected = {closed[0], closed[2]}
        long = tui.sessions[closed[2]]  # cut where the terminal cuts: its project, a title of wide characters
        tui.sessions[closed[2]] = replace(long, cwd=f"{long.cwd}/a-project-with-a-long-name", title="日本語のタイトル " * 9)
        seen = compare(tui)
        assert seen["heading"] >= 3  # OPEN NOW, ★ PINNED, EARLIER
        keys = [key for key, _, _ in tui.rows()]
        assert f"{rowwords.HEADING}pinned" in keys and closed[1] in keys
        hidden = tui.sessions[closed[1]]
        assert rowwords.title(hidden, pinned=False, hidden=True, width=30, style="", accent="#ffffff")[0][0] == \
            "hidden · "


def test_fit_cuts_as_a_text_is_truncated():
    cases = [
        [("TEAM ", "bold #888888"), ("a-very-long-team-name", "bold #ff8800")],
        [("├ ", "#888888"), ("worker", "")],
        [("plain words that go on", "")],
        [("★ ", "#ff8800"), ("日本語のタイトルはとても長い", "dim")],  # wide characters: two cells each
        [("→ ", "bold"), ("lead", "#00ff00"), (": ", ""), ("Heads-up, the build is red again", "#cccccc")],
        [("", "bold"), ("ab", "red"), ("", ""), ("cdefgh", "blue")],
        [("short", "red")],
    ]
    for parts in cases:
        for width in range(2, 30):
            cut = Text.assemble(*parts)
            cut.truncate(width, overflow="ellipsis")
            mine = Text.assemble(*rowwords.fit(parts, width))
            assert drawn(mine) == drawn(cut), (parts, width)
            assert mine.cell_len <= width


def test_the_chips_on_a_teams_row_are_one_copy():
    members = [("lead", True, "●", "#ff0000", "#ffffff", 1), ("common-gcode-common-geom", False, "✶", "bold blue",
               "#ffffff", 2), ("perf", False, "✶", "bold blue", "#ffffff", 2), ("docs", False, "●", "red", "red", 1)]
    for width in (80, 40, 20, 8):
        parts = rowwords.chips(members, width, "bold #ff8800")
        assert drawn(teamtree.chips(members, width, "bold #ff8800")) == drawn(Text.assemble(*parts))
