"""The `n` form's rules without a screen (newforms.py): which rows go with which CLI, agy's model row,
the line under the rows, the router's line, what opens and what `tabmancer new` checks; that none of it
needs Textual; and that a linked team is saved as it was before the rules moved out of the dialog."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tabmancer import agy, newforms, picks, router, teams
from tabmancer.picks import Pick

SOURCE = Path(newforms.__file__).parent

CLAUDE_ROWS = {"model": picks.DEFAULT, "agy-model": newforms.AGY_CHAIN, "effort": picks.DEFAULT,
               "cap": picks.NO_CAP, "advisor": picks.NO_ADVISOR}


def test_rows_by_cli():
    assert newforms.rows("claude") == {"model": (True, True), "agy-model": (False, True), "effort": (True, True),
                                       "cap": (True, True), "advisor": (True, True)}
    agy_rows = newforms.rows("agy")
    assert agy_rows["model"] == (False, False) and agy_rows["agy-model"] == (True, True)
    assert all(not on for name, (_, on) in agy_rows.items() if name != "agy-model")
    codex = newforms.rows("codex")
    assert codex["agy-model"][0] is False and not any(on for name, (_, on) in codex.items() if name != "agy-model")
    assert [row.name for row in newforms.choices()] == list(newforms.ROWS)


def test_agy_row_never_offers_claude_and_follows_the_chain(monkeypatch):
    agy_row = next(row for row in newforms.choices() if row.name == "agy-model")
    assert agy_row.value == newforms.AGY_CHAIN and not any(agy.is_claude(v) for v in agy_row.values)
    assert agy_row.values[1:] == list(agy.chain())
    monkeypatch.setattr(agy, "resolve", lambda wanted: ("gemini-4-hydrogen-high", "Neon is out of quota")
                        if not wanted else (wanted, ""))
    assert newforms.agy_model(newforms.AGY_CHAIN) == ("gemini-4-hydrogen-high", "Neon is out of quota")
    pick = newforms.pick_of("agy", {**CLAUDE_ROWS, "model": "opus", "effort": "high"})
    assert pick == Pick("gemini-4-hydrogen-high")  # Claude Code's rows do not reach agy
    assert newforms.pick_of("codex", {**CLAUDE_ROWS, "model": "opus"}) == Pick()


def test_the_pick_and_its_words():
    rows = {**CLAUDE_ROWS, "model": "sonnet", "effort": "high", "cap": "300k", "advisor": "opus"}
    pick = newforms.pick_of("claude", rows)
    assert pick == Pick("sonnet", "high", "300k", "opus")
    assert newforms.pick_parts(pick) == [pick.words(), "300k context", "opus advisor"]
    weak = newforms.pick_of("claude", {**rows, "model": "opus", "advisor": "haiku"})
    assert weak.advisor == ""  # a weaker model does not advise a stronger one
    assert newforms.pick_words("claude", {**rows, "model": "opus", "advisor": "haiku"}).startswith(
        "haiku cannot advise opus")
    assert newforms.pick_words("claude", rows, "cap") == f"300k: {picks.CAP_WORDS['300k']}"
    assert newforms.pick_words("claude", rows, "effort") == f"high: {picks.EFFORT_WORDS['high']}"
    assert newforms.pick_words("claude", rows) == f"sonnet: {picks.MODEL_WORDS['sonnet']}"
    assert newforms.pick_words("claude", CLAUDE_ROWS) == "default: your Claude Code settings pick them."
    assert newforms.pick_words("codex", CLAUDE_ROWS) == (
        "Model and effort are for Claude Code sessions; Codex uses its own settings.")


def test_linking_and_what_opens():
    assert newforms.can_link("agy", "a\nb", 1) == "linked sessions are Claude Code's"
    assert newforms.can_link("claude", "a", 1) == "write two lines or more"
    assert newforms.can_link("claude", "a\nb", 2) == "tick one folder"
    assert newforms.can_link("claude", "a\n\n b ", 1) == ""
    assert newforms.opens([], "a", False) == newforms.Opens("Opens nothing yet", [])
    shown = newforms.opens(["C:/work/shop", "C:/work/web"], "a\nb", False)
    assert shown.title == "Opens 4 tabs" and shown.rows[1] == ("shop", "b")
    assert newforms.opens(["C:/work/shop"], "", True).title == "Opens 1 tab, linked"
    asked = newforms.request(["C:/work/shop", "C:/work/web"], "claude", " a \nb", Pick(), linked=True)
    assert asked.message == "a\nb" and asked.linked is False  # two folders: not linked


def test_what_tabmancer_new_checks():
    assert newforms.fresh_problem(["shop"], "x", "agy", Pick("opus"), False).startswith("--model, --effort")
    assert newforms.fresh_problem(["shop"], "x", "claude", Pick("haiku", advisor="opus"), False) == ""
    assert newforms.fresh_problem(["shop"], "x", "claude", Pick("opus", advisor="haiku"), False) == (
        "haiku cannot advise opus: only a model as strong or stronger can.")
    assert newforms.fresh_problem(["shop"], "one line", "claude", None, True) == (
        "--linked takes one folder, two --prompt or more, and Claude Code.")
    assert newforms.fresh_problem(["shop"], "a\nb", "claude", None, True) == ""


def test_folders_named(tmp_path):
    (tmp_path / "shop").mkdir()
    known = [newforms.Project(str(tmp_path / "shop"))]
    folders, missing = newforms.folders_for([str(tmp_path / "shop"), "SHOP", "nowhere"], known)
    assert len(folders) == 2 and missing == ["nowhere"]


def test_the_router_line_and_its_pick(monkeypatch):
    claude = router.Route(cli="claude", pick=Pick("sonnet", "high", "300k"), why="a small fix")
    assert newforms.route_take(claude) == {"model": "sonnet", "effort": "high", "cap": "300k"}
    assert newforms.route_line("codex", "write the API docs", False, None, False, Pick()) is None
    short = newforms.route_line("claude", "hi", False, None, False, Pick())
    assert "".join(text for text, _ in short) == "Router: Ctrl+R suggests a pick once there is a first message (3 words or more)."
    message = "write the API reference pages"
    asking = newforms.route_line("agy", message, True, None, True, Pick())
    assert asking[1] == ("reading agy's quota…", "muted") and asking[2][0] == "  it goes in when it comes"
    suggests = newforms.route_line("claude", message, False, claude, False, Pick())
    assert suggests[0] == ("Router suggests ", "muted") and suggests[-1] == (" takes it", "muted")
    took = newforms.route_line("claude", message, False, claude, False, Pick("sonnet", "high", "300k"))
    assert took == [("✓ ", "ok-bold"), ("the router's pick", "ok"), (" — a small fix", "muted")]
    failed = newforms.route_line("claude", message, False, "no network", False, Pick())
    assert failed[1] == ("no network", "warn")
    assert {tone for line in (short, asking, suggests, took, failed) for _, tone in line} <= {
        "muted", "muted-bold", "warn", "warn-bold", "pick", "ok", "ok-bold"}

    monkeypatch.setattr(agy, "resolve", lambda wanted: ("gemini-4-neon-high", "") if not wanted else (wanted, ""))
    chain = agy.chain()
    assert newforms.route_take(router.Route(cli="agy", pick=Pick(chain[0]))) == {"agy-model": newforms.AGY_CHAIN}
    assert newforms.route_take(router.Route(cli="agy", pick=Pick(chain[-1]))) == {"agy-model": chain[-1]}
    assert newforms.route_take(router.Route(cli="agy", pick=Pick("claude-opus-4"))) == {}  # never Claude in agy's row


def test_what_ctrl_r_and_ctrl_e_refuse():
    assert newforms.route_refusal("codex", "write the API reference pages")[0].startswith(
        "The router picks for Claude Code and Antigravity sessions, not Codex")
    assert newforms.route_refusal("claude", "hi") == (
        ("Ctrl+R needs the first message: write what the session is to do (3 words or more), and the router picks "
         "for it."), 6)
    assert newforms.route_refusal("claude", "write the API reference pages") is None
    assert newforms.improve_refusal("") == "Ctrl+E needs a first message to improve: write what the session is to do first."


# --- a linked team, saved as before ------------------------------------------------------------------

class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 23, 12, 0, 0, tzinfo=tz)


def member(name, focus, model):
    return {"name": name, "focus": focus, "lead": False, "model": model, "effort": "high", "owns": "",
            "advisor": "opus", "cli": "claude"}


def test_a_linked_team_is_saved_as_before(tmp_path, monkeypatch):
    """Golden: the team file the dialog and `tabmancer new --linked` wrote before newforms.build (taken
    from that code, on the same made-up lines and the same clock)."""
    monkeypatch.setattr(teams, "datetime", Clock)
    folder = tmp_path / "my-shop-frontends-app"
    folder.mkdir()
    built = newforms.build(newforms.Request([str(folder)], "claude", "api: endpoints @sonnet\nthe pages\nthe pages\nfixes",
                                            Pick("opus", "high", "300k", "opus"), linked=True))
    saved = json.loads(Path(os.environ["TABMANCER_CONFIG_DIR"], "teams.json").read_text(encoding="utf-8"))
    created = Clock.now().astimezone().isoformat(timespec="seconds")
    assert saved == {"teams": [{
        "name": "my-shop-frontends", "cwd": str(folder), "goal": "endpoints; the pages; the pages; fixes",
        "members": [member("api", "endpoints", "sonnet"), member("second", "the pages", "opus"),
                    member("third", "the pages", "opus"), member("fourth", "fixes", "opus")],
        "created": created, "decide": 0, "background": False, "worktrees": False, "cap": "300k"}]}
    assert built.team is not None and [launch.argv[:3] for launch in built.launches] == [
        ["claude", "--name", f"my-shop-frontends-{name}"] for name in ("api", "second", "third", "fourth")]
    again = newforms.build(newforms.Request([str(folder)], "claude", "a\nb", Pick(), linked=True))
    assert again.team.name == "my-shop-frontends-2"  # a second linked team is not saved over the first


def test_fresh_sessions_built():
    built = newforms.build(newforms.Request(["C:/work/shop"], "claude", "a\nb", Pick("sonnet")))
    assert built.team is None and [launch.title for launch in built.launches] == ["shop: a", "shop: b"]


# --- without Textual ---------------------------------------------------------------------------------

NO_UI = """
import sys

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("textual", "PySide6", "shiboken6"):
            raise ImportError("blocked: " + name)

sys.meta_path.insert(0, Blocked())
from tabmancer import cli, newforms
opened = []
cli.open_tabs = lambda launches: opened.extend(launches) or []
try:
    cli.main(["new", "--help"])
except SystemExit as done:
    assert done.code == 0
cli.main(["new", FOLDER, "--prompt", "the API reference", "--prompt", "the pages", "--model", "sonnet"])
assert [launch.title for launch in opened] == ["shop: the API reference", "shop: the pages"], opened
cli.main(["new", FOLDER, "--prompt", "a", "--prompt", "b", "--linked"])
assert len(opened) == 4
print(sorted(m for m in sys.modules if m.split(".")[0] in ("textual", "PySide6", "shiboken6")))
"""


def test_tabmancer_new_needs_no_textual(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    env = {**os.environ, "PYTHONPATH": str(SOURCE.parent)}
    for name in ("TABMANCER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        env[name] = str(tmp_path / name.lower())
    out = subprocess.run([sys.executable, "-c", f"FOLDER = {str(folder)!r}\n" + NO_UI], capture_output=True,
                         text=True, timeout=60, env=env, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"
    assert "usage:" in out.stdout and "--linked" in out.stdout  # new --help was printed


def test_newforms_loads_no_ui():
    code = ("import sys, tabmancer.newforms; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


@pytest.mark.parametrize("name", ["Project", "projects", "topics", "launches", "linked_team", "Request",
                                  "default_cli", "find", "CAP_SHORT", "AGY_CHAIN"])
def test_newsessions_still_offers_them(name):
    from tabmancer import newsessions
    assert getattr(newsessions, name) is getattr(newforms, name)
