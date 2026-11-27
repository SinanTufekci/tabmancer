"""The desktop window says what the terminal dashboard said, on the made-up sessions of `tabmancer --demo`:
the goldens in tests/golden/terminal (written from the terminal by make_terminal_golden.py; golden_terminal.py
says what they hold) against the window's own code over the store's read: the list's rows (desktop/table.py's
Table), each session's preview (drawer.detail_of, the summaries the window's Summarizer reads), each team
member's words (teamwords.member_status_words on the view model, as the team graph and the monitor say them),
and the view model itself (VM.to_json). In a child process, offscreen: the clock stops before tabmancer is
imported. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import difflib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import golden_terminal as G
import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")

WINDOW = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import golden_terminal as G
G.freeze()
home = Path(sys.argv[2])
G.prepare(home)
from PySide6.QtWidgets import QApplication
qapp = QApplication(["tabmancer-golden"])
from tabmancer import picks, rowwords, teamwords
from tabmancer import viewmodel as VM
from tabmancer.desktop import drawer, look, table
from tabmancer.desktop.summaries import Summarizer
from tabmancer.store import DashboardStore
look.fonts_for_offscreen()

said = json.loads(G.load()["rows"])["listing"]  # the terminal's room for the Title and Model columns
snap = DashboardStore().poll()
hidden = lambda s: s.session_id in snap.hidden
ticked = VM.preselect(snap.sessions, snap.open_at_shutdown, hidden)
shown = table.Table(said["accent"])
shown.width_now, shown.model_shown = said["width"], said["model_shown"]
shown.set(snap, VM.visible(snap.sessions.values(), hidden), ticked)
listing = shown.listing
listed = rowwords.keys(listing)
rows = {"listing": {"width": listing.width, "model_shown": listing.model_shown, "accent": listing.accent,
                    "selected": sorted(listing.selected), "unfolded": sorted(listing.unfolded),
                    "modes": G.plain(dict(listing.modes)), "pinned": sorted(listing.pinned), "more": drawer.MORE},
        "rows": [{"key": key, **G.cells(rowwords.row(listing, key, session))} for key, session in listed]}
summaries = Summarizer().summaries
members = drawer.member_ids(snap.team_states)
cap = picks.cap_tokens(picks.default_cap())
detail = {s.session_id: G.plain(drawer.detail_of(snap, s, members, accent=said["accent"], default_cap=cap,
                                                 summaries=summaries, found=None, selected=ticked))
          for _, s in listed if s is not None}
words = {team.name: {member.name: teamwords.member_status_words(member) for member in team.members}
         for team in sorted(snap.view.teams, key=lambda team: team.name)}
parts = G.mask({"view": VM.to_json(snap.view), "rows": rows, "detail": detail, "members": words}, G.masks(home))
print("GOLDEN " + json.dumps(parts, ensure_ascii=False))
'''


@pytest.fixture(scope="module")
def window(tmp_path_factory) -> dict:
    """What the window's code says on the demo, masked as the goldens are."""
    home = tmp_path_factory.mktemp("golden-home")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", WINDOW, str(Path(__file__).parent), str(home)], check=False,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=300,
                          stdin=subprocess.DEVNULL)
    out = [line for line in done.stdout.splitlines() if line.startswith("GOLDEN ")]
    assert done.returncode == 0 and out, done.stderr[-3000:]
    return json.loads(out[-1].removeprefix("GOLDEN "))


def same(name: str, now: object) -> None:
    """The part as the window says it is the golden's; else the first lines that differ."""
    golden = G.load()[name]
    text = G.dump({**dict.fromkeys(G.PARTS), name: now})[name]
    if text != golden:
        diff = difflib.unified_diff(golden.splitlines(), text.splitlines(), f"golden/terminal/{name}.json",
                                    "the window's", lineterm="", n=2)
        pytest.fail("\n".join(list(diff)[:80]))


def strings(value) -> list[str]:
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [key, *strings(item)]]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return [value] if isinstance(value, str) else []


def test_the_goldens_hold_nothing_of_the_machine_that_wrote_them():
    for name, text in G.load().items():
        assert G.leftovers(text) == [], name
        assert [said for said in strings(json.loads(text)) if "\\" in said] == [], name  # every path with '/'


def test_the_view_model_is_the_one_the_goldens_were_written_with(window):
    same("view", window["view"])


def test_the_list_says_what_the_terminal_list_said(window):
    same("rows", window["rows"])


def test_each_preview_says_what_the_terminal_preview_said(window):
    same("detail", window["detail"])


def test_each_member_is_said_as_the_terminal_team_view_says_it(window):
    terminal = json.loads(G.load()["members"])
    assert {team: {member: said["label"] for member, said in members.items()}
            for team, members in terminal.items()} == window["members"]
