"""Draw docs/demo.svg: the dashboard over the README's made-up sessions, a few keys pressed one
after another, as one SVG that shows each screen in turn (CSS animation, no script, no GIF).

usage: python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
DOCS = HERE.parent / "docs"
SIZE = (150, 44)
ACCENT = "#d97757"
# (seconds on screen, what the caption says)
SCENES = [
    (3.2, "a session cut off by a reboot  ·  Enter calls it back"),
    (2.6, "2  ·  only the sessions waiting for you"),
    (3.0, "a team on one row  ·  its members as chips"),
    (2.6, "e  ·  the team's members, each on its wire"),
    (3.4, "T  ·  who said what to whom"),
    (2.6, "?  ·  every key, and Enter runs it"),
]


async def frames(home: Path) -> list[str]:
    import screenshots

    from tabmancer import app as app_module
    from tabmancer import sessions, summarize, teams
    from tabmancer.app import TEAM_ROW, SessionPicker
    from tabmancer.teamview import TeamScreen

    team, team_live = screenshots.build_team(home)
    sessions._agents_json = lambda include_finished=False: screenshots.LIVE + team_live
    app_module.recent_shutdowns = lambda: [screenshots.SHUTDOWN]
    pcb = next(home.glob("projects/*/pcb-router.jsonl"))
    summarize._store("pcb-router", {"summary": screenshots.SUMMARY, "size": pcb.stat().st_size, "model": "haiku"})
    screenshots.made_up_limits()
    shots = []
    app = SessionPicker(days=14)
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if app.token_report and app.team_states and any(s.live_state for s in app.sessions.values()):
                break
        await pilot.pause(1.4)  # the cards have counted up, the list has faded in
        app.selected.clear()
        app.table.move_cursor(row=app.table.get_row_index("pcb-router"))
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
        await pilot.press("2")
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
        await pilot.press("2")
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        flying = teams.Message(datetime.now(), "lead", "tests", "Mock the webhook meanwhile")
        app.team_flights["shop"] = [(flying, time.monotonic() - 0.5)]
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
        app.team_flights.clear()
        await pilot.press("e")
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
        app.push_screen(TeamScreen(team, ACCENT))
        await pilot.pause(0.8)
        shots.append(app.export_screenshot())
        await pilot.press("escape")
        await pilot.press("question_mark")
        await pilot.pause(0.6)
        shots.append(app.export_screenshot())
    return shots


def stitch(shots: list[str]) -> str:
    """One SVG holding every frame, each shown in turn with its caption."""
    box = re.search(r'viewBox="([^"]+)"', shots[0])[1]
    width, height = (float(n) for n in box.split()[2:])
    strip = 76  # under the window: what the screen shows, in words
    total = sum(seconds for seconds, _ in SCENES)
    css, bodies, start = [], [], 0.0
    for index, (shot, (seconds, caption)) in enumerate(zip(shots, SCENES)):
        inner = shot[shot.index(">", shot.index("<svg")) + 1: shot.rindex("</svg>")]
        # Each export has its own class prefix; make sure no two frames share one.
        inner = re.sub(r"terminal-(\d+)", rf"f{index}-terminal-\1", inner)
        begin, end = start / total * 100, (start + seconds) / total * 100
        on = f"{begin:.3f}%, {end - 0.01:.3f}%" if index else f"0%, {end - 0.01:.3f}%"
        css.append(f".f{index} {{ animation: k{index} {total}s infinite; opacity: {1 if index == 0 else 0}; }}"
                   f"@keyframes k{index} {{ {on} {{ opacity: 1; }} "
                   + (f"0%, {max(0.0, begin - 0.01):.3f}%, " if index else "")
                   + f"{end:.3f}%, 100% {{ opacity: 0; }} }}")
        bodies.append(
            f'<g class="f{index}"><svg x="0" y="0" width="{width}" height="{height}" viewBox="{box}">{inner}</svg>'
            f'<text x="{width / 2}" y="{height + 48}" fill="{ACCENT}" font-size="30" font-weight="bold" '
            f'text-anchor="middle" font-family="ui-monospace, Cascadia Mono, Menlo, Consolas, monospace">{caption}</text>'
            "</g>")
        start += seconds
    whole = f"0 0 {width} {height + strip}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{whole}" width="{width}" height="{height + strip}" '
            'role="img" aria-label="tabmancer in use: a cut-off session, a card filter, a team, the team view and '
            'the keys">'
            f'<style>{"".join(css)}@media (prefers-reduced-motion: reduce) {{ g {{ animation: none !important; }} }}'
            f'</style><rect x="0" y="{height}" width="{width}" height="{strip}" rx="12" fill="#0b0b0c"/>'
            f'{"".join(bodies)}</svg>')


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tabmancer-demo-") as tmp:
        home = Path(tmp)
        os.environ["CLAUDE_CONFIG_DIR"] = str(home / "claude")
        os.environ["TABMANCER_CONFIG_DIR"] = str(home / "config")
        os.environ["TABMANCER_MASCOT_BLOCKS"] = "half"  # SVG viewers' fonts rarely have the octants
        os.environ.pop("NO_COLOR", None)
        import screenshots

        from tabmancer import app as app_module
        from tabmancer import usage

        app_module.open_tabs = lambda launches: []  # whatever is pressed, no real tabs
        usage.read_usage = list
        screenshots.build_home(home / "claude")
        shots = asyncio.run(frames(home / "claude"))
    DOCS.mkdir(exist_ok=True)
    out = DOCS / "demo.svg"
    out.write_text(stitch(shots), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(shots)} frames)")


if __name__ == "__main__":
    main()
