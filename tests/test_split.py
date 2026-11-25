"""The preview beside the list (split) in a big window: by itself from 180x40, V keeps it under the list."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from test_team_tree import peer, plain, ready, send, shop, transcript

from tabmancer.app import PANE_LEAST, PANE_MOST, SPLIT_FROM, TEAM_ROW, SessionPicker
from tabmancer.config import read_json
from tabmancer.teamview import TeamGraph


async def until(pilot, check, what: str, seconds: float = 20.0) -> None:
    """Until check holds, by the clock, the app idle at each look (pilot.pause() waits for its
    messages, the refreshes a key queued with them). Fails naming what never came."""
    end = time.monotonic() + seconds
    while True:
        await pilot.pause()
        if check():
            return
        if time.monotonic() > end:
            raise AssertionError(f"never: {what}")
        await pilot.pause(0.05)


async def settled(pilot) -> None:
    """What a key set going is done: the app idle twice over, with a refresh between (V's
    call_after_refresh steps, fit_title's columns made again), not a guess at how long that takes."""
    for _ in range(3):
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
    await pilot.pause(0.05)
    await pilot.pause()


def beside(app) -> bool:
    """The preview stands beside the list: same top, right of it."""
    table, inspector = app.table.region, app.inspector.region
    return inspector.x >= table.right and inspector.y <= table.y + 1


@pytest.mark.parametrize(("size", "split"), [((179, 50), False), ((180, 40), True), ((200, 39), False),
                                             ((200, 50), True), ((120, 30), False)])
async def test_the_preview_goes_beside_the_list_from_180_by_40(fake_home, size, split):
    app = SessionPicker(days=14)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.4)
        assert app.dashboard.has_class("split") is split
        assert beside(app) is split
        if split:
            width = max(PANE_LEAST, min(PANE_MOST, size[0] - 132))
            assert app.inspector.region.width == width
            assert app.inspector.region.height >= size[1] - 12  # full height, not 18 rows at most
            assert app.query_one("#floor").display  # Bit's rule at the foot of the list
            assert app.mascot.region.y < app.query_one("#floor").region.y
        else:
            assert not app.query_one("#floor").display
            assert app.inspector.region.y > app.table.region.y  # under the list, as ever
    assert SPLIT_FROM == (180, 40)


async def test_120_by_30_is_as_it_was(fake_home):
    """Below the split, the preview's place, height and margins are the ones it had."""
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        inspector, table = app.inspector.region, app.table.region
        assert inspector.y > table.bottom - 1 and inspector.height <= 18
        assert inspector.x == table.x and inspector.width == table.width  # across the list's width
        assert tuple(app.inspector.styles.margin)[1:] == (0, 0, 0)


async def test_v_turns_it_off_and_on_and_it_is_remembered(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.4)
        mascot = app.mascot
        await pilot.press("V")
        await pilot.pause(0.2)
        assert not app.dashboard.has_class("split") and not beside(app)
        assert app.mascot is mascot and mascot.is_mounted  # moved, never mounted again (its picture stays)
        assert read_json("config.json")["split"] == "off"
        await pilot.press("V")
        await pilot.pause(0.2)
        assert app.dashboard.has_class("split") and beside(app)
        assert read_json("config.json")["split"] == "auto"
        await pilot.press("V")
    again = SessionPicker(days=14)
    async with again.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.4)
        assert not again.dashboard.has_class("split")  # off, as it was left


async def test_a_window_made_smaller_goes_back_under_the_list(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.4)
        assert beside(app)
        await pilot.resize_terminal(150, 44)
        await pilot.pause(0.3)
        assert not app.dashboard.has_class("split") and not beside(app)
        assert app.inspector.region.height <= 18
        await pilot.resize_terminal(200, 50)
        await pilot.pause(0.3)
        assert beside(app)


async def test_a_teams_row_beside_the_list_shows_its_cards(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.3)
        graph = app.team_graph
        assert isinstance(graph, TeamGraph) and graph.team.name == "shop" and graph.is_mounted
        assert graph.selected_member() is None  # no card picked
        cards = plain(graph.render())
        assert "★ lead" in cards and "api" in cards and "ui" in cards
        assert "Goal  Add a checkout" in plain(app.detail.content)
        meta = plain(app.meta.content)
        assert "MESSAGES" in meta and "endpoint done" in meta and "T  teams" in meta
        assert graph.region.y > app.detail.region.y and app.meta.region.y > graph.region.y  # stacked
        assert len(graph.layout_boxes(graph.size.width)[1][0]) == 2  # two cards a row at 68 columns
        # a member's row: its own preview, the cards gone
        app.table.move_cursor(row=app.table.get_row_index("api-1"))
        await pilot.pause(0.2)
        assert app.team_graph is None and not app.query(TeamGraph)
        assert "shop · api" in plain(app.meta.content)
        # back on the team, then V: the compact tree under the list, the cards gone
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.2)
        assert app.team_graph is not None
        await pilot.press("V")
        await pilot.pause(0.3)
        assert app.team_graph is None and "├──1▶──1◀── api" in plain(app.detail.content)


async def test_a_new_message_flies_in_the_cards(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.3)
        graph = app.team_graph
        state = app.team_states["shop"]
        message = next(m for m in state.said if m.sender == "lead" and m.receiver == "api")
        moved = replace(state, said=[*state.said, replace(message, text="one more")])
        app.show_teams({**app.team_states, "shop": moved})
        assert len(graph.flights) == 1


async def test_a_sessions_row_beside_the_list_says_more(fake_home):
    app = SessionPicker(days=14)
    long = "word " * 400
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.4)
        session = app.current()
        app.sessions[session.session_id] = replace(session, last_reply=long)
        app.update_detail()
        await pilot.pause(0.1)
        split_words = len(plain(app.detail.content).split())
        await pilot.press("V")
        await pilot.pause(0.3)
        app.update_detail()
        await pilot.pause(0.1)
        assert split_words > len(plain(app.detail.content).split())


async def test_a_busy_teams_keys_all_show_beside_the_list(fake_home, tmp_path, monkeypatch):
    """The messages take the room the words and the cards leave, so the team's keys show whole at
    the foot of the preview, the last line of them too."""
    from rich.text import Text

    from tabmancer import teamtree

    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(teamtree, "latest", lambda state, colours, room, width: Text(
        "\n".join(f"message {n}" for n in range(room))))
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))

        def shown() -> str:
            visible = app.inspector.content_region.bottom - app.meta.region.y
            lines = app.meta.render_lines(app.meta.region.reset_offset)[:visible]
            return "\n".join("".join(segment.text for segment in line) for line in lines)

        def last() -> str:
            return plain(app.meta.content).rstrip().split("\n")[-1].strip()

        # By the clock, not a fixed wait: once the cards are in, the footer takes a second row on the
        # team's row, after the fit that followed the first layout. The spin tick's fit (every 0.08 s)
        # then measures the messages again; on a loaded machine that tick came after a 0.2 s wait,
        # and the keys' last line was found a row below the screen (seen once in a full run).
        await until(pilot, lambda: "message 0" in shown() and app.team_graph is not None
                    and app.team_graph.region.height and last() in shown(), "the keys' last line on the screen")
        text = shown()
        assert "message 5" in text and "Enter  team view" in text
        assert last() and last() in text  # the keys' last line is on the screen


def team_pane_ok(app) -> tuple[bool, str]:
    """The cards at the height measured for them, and the keys' last line on the screen."""
    visible = app.inspector.content_region.bottom - app.meta.region.y
    lines = app.meta.render_lines(app.meta.region.reset_offset)[:max(0, visible)]
    text = "\n".join("".join(segment.text for segment in line) for line in lines)
    last = plain(app.meta.content).rstrip().split("\n")[-1].strip()
    graph = app.team_graph
    height = graph.height_for(graph.layout_boxes(graph.size.width)[1]) if graph is not None else -1
    return graph is not None and graph.region.height == height and bool(last) and last in text, text


@pytest.mark.parametrize("attempt", range(4))
async def test_the_cards_have_their_measured_height_at_180_by_40(fake_home, tmp_path, monkeypatch, attempt):
    """Laid out at the pane's width from the first frame: never a 100-column guess wrapped to the
    pane, which pushed the messages and the keys off its foot. No line under the cards (none picked)."""
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(180, 40 + attempt % 2 * 2)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        for _ in range(40):
            await pilot.pause(0.05)
            if app.team_graph is not None and app.team_graph.region.height:
                break
        await pilot.pause(0.3)
        ok, text = team_pane_ok(app)
        assert ok, text
        graph = app.team_graph
        assert graph.region.height == graph.card_h * 2 + 1  # the lead's card and one row: no focus line


async def test_a_big_team_shows_the_compact_tree_beside_the_list(fake_home, tmp_path, monkeypatch):
    """Cards that would leave no room for two messages and every key give way to the tree."""
    from tabmancer import teams

    made = shop(fake_home, tmp_path, monkeypatch)
    made.members += teams.parse_members("; ".join(f"m{n}: part {n}" for n in range(7)))  # lead + 9
    teams.save({"shop": made})
    app = SessionPicker(days=14)
    async with app.run_test(size=(190, 48)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.5)
        assert app.team_graph is None and not app.query(TeamGraph)
        detail = plain(app.detail.content)
        assert "m6" in detail and "api" in detail  # the tree, every member on it
        visible = app.inspector.content_region.bottom - app.meta.region.y
        lines = app.meta.render_lines(app.meta.region.reset_offset)[:visible]
        text = "\n".join("".join(segment.text for segment in line) for line in lines)
        last = plain(app.meta.content).rstrip().split("\n")[-1].strip()
        assert "MESSAGES" in text and last in text  # and every key


async def test_v_is_in_the_footer(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.3)
        assert next(b for b in app.BINDINGS if b.action == "toggle_split").show
        assert any(key == "V" and active.binding.show for key, active in app.active_bindings.items())


async def test_v_leaves_a_config_it_cannot_read_as_it_is(fake_home):
    """config.json there but unreadable: V would have written {"split": ...} alone over the theme,
    Bit and the sessions to continue. It is left as it is, and the note says it was not saved."""
    from tabmancer.app import NOT_SAVED
    from tabmancer.config import config_dir

    path = config_dir() / "config.json"
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause(0.3)
        path.write_text('{"theme": "dusk", "mascot": "calm", "continue_at_reset": {', encoding="utf-8")
        await pilot.press("V")
        await pilot.pause(0.2)
        assert not app.split  # it still turns off for now
        assert any(NOT_SAVED in n.message for n in app._notifications)
    assert path.read_text(encoding="utf-8").startswith('{"theme": "dusk"')


def two_teams(fake_home, tmp_path, monkeypatch) -> None:
    """Teams shop and docs, both at work."""
    from tabmancer import sessions, teams

    shop(fake_home, tmp_path, monkeypatch)
    folder = tmp_path / "docs"
    folder.mkdir()
    made = teams.new_team("docs", str(folder), "Write the guide", teams.parse_members("writer: the guide"))
    made.created = shop_created = teams.load()["shop"].created
    teams.save({**teams.load(), "docs": made})
    transcript(fake_home, folder, "dlead-1", "docs-lead", [send("docs-writer", "start the guide", 9)])
    transcript(fake_home, folder, "writer-1", "docs-writer", [peer("docs-lead", "start the guide", 8)])
    live = [{"sessionId": sid, "kind": "interactive", "status": "idle"}
            for sid in ("lead-1", "api-1", "one", "dlead-1", "writer-1")]
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    assert shop_created


async def test_moving_between_two_teams_rows_keeps_one_set_of_cards(fake_home, tmp_path, monkeypatch):
    """From one team's row to another's and back: the cards going were still there (a removal waits
    for the next frame) when the next team's came in, and the same id crashed the dashboard
    (DuplicateIds). Any test of a move from row to row needs two rows of the same kind."""
    two_teams(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app)
        for _ in range(100):
            await pilot.pause(0.05)
            if f"{TEAM_ROW}docs" in app.table.rows:
                break
        for name in ("shop", "docs", "shop", "docs", "shop"):
            app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}{name}"))
            await pilot.pause(0.05)  # quicker than a frame or two: the old cards may still be going
        await pilot.pause(0.4)
        assert app.team_graph is not None and app.team_graph.team.name == "shop"
        assert len(app.query(".pane-graph")) == 1 and len(app.query(TeamGraph)) == 1


async def test_a_key_that_makes_the_columns_again_keeps_the_cursor_on_its_row(fake_home, tmp_path, monkeypatch):
    """V (the title's room changes) and l make the columns again, which empties the table: the cursor
    went to the first row with it, whichever row it was on (a real window showed it, 2026-11-23: on
    the second team's row, V took it to the first team's)."""
    two_teams(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app)
        docs = f"{TEAM_ROW}docs"
        # Both teams in, and done coming in: a teams poll landing after the cursor moved was a
        # second rebuild the old 5 s loop let through (it went on even when docs never came).
        await until(pilot, lambda: docs in app.table.rows and app.team_open("docs"), "the docs team's row")
        await settled(pilot)
        app.table.move_cursor(row=app.table.get_row_index(docs))
        await until(pilot, lambda: app.cursor_key() == docs, "the cursor on docs")
        assert app.table.cursor_row > app.table.get_row_index(f"{TEAM_ROW}shop")  # not the first row
        for split in (False, True):  # under the list, and beside it again
            await pilot.press("V")
            await until(pilot, lambda split=split: app.split_on is split, "V taken")
            await settled(pilot)
            assert app.cursor_key() == docs
        await until(pilot, lambda: app.team_graph is not None and app.team_graph.team.name == "docs",
                    "docs's graph in the preview")
        await pilot.press("l")  # the live view: its rows are others (the members, unfolded)
        await until(pilot, lambda: app.live_mode, "the live view")
        await settled(pilot)
        rows = [str(r.value) for r in app.table.rows]
        app.table.move_cursor(row=len(rows) - 1)
        await settled(pilot)
        here = app.cursor_key()
        assert app.table.cursor_row > 1
        await pilot.press("l")
        await until(pilot, lambda: not app.live_mode, "the list again")
        await settled(pilot)
        assert here not in app.table.rows or app.cursor_key() == here
        await pilot.press("l")
        await until(pilot, lambda: app.live_mode, "the live view again")
        await settled(pilot)
        assert app.cursor_key() == here  # the live view again: back on its row


async def test_a_head_wider_than_the_pane_leaves_every_key_on_the_screen(fake_home, tmp_path, monkeypatch):
    """At 190x48 the pane is 54 columns: a head of many messages and many tokens takes two rows.
    Counted as one, it pushed the keys' last line (M monitor) off the pane's foot (seen in a real
    window, 2026-11-23). It breaks between its parts only: never "671k" on one row, "tokens" on the next."""
    from rich.text import Text

    from tabmancer import teamtree, teamwords

    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(teamwords, "head", lambda state, tokens, accent, **_: [  # the preview's head (team_detail)
        ("TEAM ", "bold"), ("shop", f"bold {accent}"), ("  ·  4 of 4 open  ·  1234 messages", "dim"),
        ("  ·  999.9M tokens", "dim")])
    monkeypatch.setattr(teamtree, "latest", lambda state, colours, room, width: Text(
        "\n".join(f"message {n}" for n in range(room))))
    app = SessionPicker(days=14)
    async with app.run_test(size=(190, 48)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        for _ in range(60):
            await pilot.pause(0.05)
            if app.team_graph is not None and app.team_graph.region.height:
                break
        await pilot.pause(0.3)
        ok, text = team_pane_ok(app)
        assert ok, text
        assert "monitor" in text
        head = plain(app.detail.content).split("\n")
        assert head[0].endswith("1234 messages") and head[1] == "999.9M tokens"
