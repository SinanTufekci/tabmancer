"""Write tests/golden/terminal/*.json from the terminal dashboard on the made-up sessions of `tabmancer --demo`
(golden_terminal.py says what they hold and why). Not a test: run it by hand, only while the terminal
dashboard is still there and only when its words change on purpose:

    uv run python tests/make_terminal_golden.py

Run twice, a minute or more apart: the files must come out the same. --print writes nothing and prints them."""

import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import golden_terminal as G  # after the path: the clock stops before tabmancer is imported

G.freeze()

ROOM = 60  # the preview's room in characters, as the terminal's beside the list (and the window's, drawer.ROOM)


async def terminal(home: Path) -> dict[str, object]:
    """The terminal dashboard's words on the demo, once every made-up session and the team are read."""
    demo, _ = G.prepare(home)
    from tabmancer import rowwords, sessionwords, store, teamwords
    from tabmancer.engine import LIVE_REFRESH_SECONDS
    from tabmancer.teamview import TeamScreen

    app = demo.picker()
    async with app.run_test(size=G.WIDTH) as pilot:
        for _ in range(400):
            if len(app.sessions) >= len(demo.SESSIONS) and app.team_states and app.table.row_count:
                break
            await pilot.pause(0.05)
        # Two live reads after the teams are known: the first read marks no member resting (engine.live
        # takes the teams from the read before), and the git states and the tokens come after the sessions.
        await pilot.pause(2 * LIVE_REFRESH_SECONDS + 1)
        # A member done for now rests (waits for its lead): a live read marks it so only when that member is
        # among the sessions it brought up to date, which may first be the full read 16 s on; until then its
        # row says it waits for you. The rule that read applies, applied now (store.rest), as the store's
        # every read does: the words the dashboard settles on.
        store.rest(app.sessions.values(), app.resting_members())
        listing = replace(app.listing(), star=rowwords.STILL_STAR)
        listed = rowwords.keys(listing)
        rows = {"listing": {"width": listing.width, "model_shown": listing.model_shown,
                            "accent": listing.accent, "selected": sorted(listing.selected),
                            "unfolded": sorted(listing.unfolded), "modes": G.plain(dict(listing.modes)),
                            "pinned": sorted(listing.pinned), "more": 3 if app.split else 1},
                "rows": [{"key": key, **G.cells(rowwords.row(listing, key, session))} for key, session in listed]}
        detail = {session.session_id: G.plain(sessionwords.detail(session, app.detail_inputs(session, ROOM)))
                  for _, session in listed if session is not None}
        members = {}
        for name, state in sorted(app.team_states.items()):  # each team's screen, as T or Enter opens it
            screen = TeamScreen(state.team, app.accent())
            await app.push_screen(screen)
            for _ in range(200):
                if screen.sessions:
                    break
                await pilot.pause(0.05)
            members[name] = {member.name: dict(zip(("label", "style", "spinning"),
                                                   teamwords.member_status(screen.sessions.get(member.name))))
                             for member in state.team.members}
            app.pop_screen()
            await pilot.pause(0.1)
    return {"rows": rows, "detail": detail, "members": members}


def store_view() -> dict:
    """The view model the window draws (the terminal dashboard has none): the store's read, as JSON."""
    from tabmancer import viewmodel as VM
    from tabmancer.store import DashboardStore

    return VM.to_json(DashboardStore().poll().view)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tabmancer-golden-") as folder:
        home = Path(folder)
        parts = asyncio.run(terminal(home))
        parts["view"] = store_view()
        masked = G.mask(parts, G.masks(home))
    texts = G.dump(masked)
    left = [found for text in texts.values() for found in G.leftovers(text)]
    if left and "--raw" not in sys.argv:
        sys.exit(f"Not written: something of this machine is left in them: {sorted(set(left))}")
    if "--print" in sys.argv:
        for name, text in texts.items():
            print(f"=== {name}.json\n{text}")
        return
    G.save(masked)
    print(f"Wrote {', '.join(f'{name}.json' for name in G.PARTS)} to {G.GOLDEN.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
