import json
from pathlib import Path

import pytest

from tabmancer import app as app_module
from tabmancer import sessions

CWD = "C:/work/demo"


def team_preview(dialog) -> str:
    """What the new-team form says will open: the title of the card with the drawing, then the line
    under it ("Opens 4 tabs in shop. The lead starts last..."), or what is still needed."""
    from rich.text import Text

    title = Text.from_markup(str(dialog.query_one("#graph-card").border_title)).plain  # kept as markup
    return f"{title}. {dialog.query_one('#team-preview').render()}"


def role_of(argv) -> str:
    """The role a team member starts with: the file its command line names."""
    return Path(argv[list(argv).index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")


async def box_has_focus(pilot, app, tries: int = 150) -> None:
    """Wait until the dialog just opened has given its box the focus, before typing into it.

    pilot.press waits for the screen that was in front when its key went in. A dialog that key
    opened may still be putting itself together, and keys typed before its box has the focus are
    lost: on the slower Windows runner the rename test typed into nothing (seen at least twice).
    """
    for _ in range(tries):
        if app.screen.focused is not None:
            return
        await pilot.pause(0.02)
    raise AssertionError(f"{type(app.screen).__name__} never gave anything the focus")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keep tests off the real CLI (`claude agents --json`, `claude -p /usage`) and config folder."""
    from tabmancer import cli, focus, handover, rename, summarize, toast, typein, usage

    def no_background(launch):
        raise OSError("no background sessions in tests")

    def no_model(prompt, model, timeout=180, effort=""):
        raise summarize.SummaryError("no model calls in tests")

    def no_resume(session):
        raise handover.HandoverError("no model calls in tests")

    def no_rename(session, name):
        raise OSError("no claude calls in tests")

    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [])
    monkeypatch.setattr(summarize, "ask", no_model)  # reports fall back
    monkeypatch.setattr(handover, "write_itself", no_resume)
    monkeypatch.setattr(rename, "rename", no_rename)
    monkeypatch.setattr(usage, "read_usage", list)
    # No context cap unless a test asks for one: the tests of everything else see the command lines
    # as they were before caps (test_router, test_picks: caps).
    monkeypatch.setenv("TABMANCER_CONTEXT_CAP", "auto")
    monkeypatch.setattr(usage, "read_codex", lambda now=None: [])  # never the real ~/.codex
    monkeypatch.setattr(focus, "focus_tab", lambda title: False)  # never switch the real tabs
    monkeypatch.setattr(focus, "close_tab", lambda title: False)  # nor close them
    monkeypatch.setattr(typein, "write_keys", lambda pid, text: False)  # nor type into a real console
    # Never real tabs or windows, whatever a test presses: a test that wants to see what would
    # open puts its own recorder in their place.
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: [])
    monkeypatch.setattr(cli, "open_tabs", lambda launches: [])
    monkeypatch.setattr(app_module, "run_in_background", no_background)
    monkeypatch.setattr(toast, "show", lambda title, body, dashboard: toast.FAILED)  # no real toasts
    from tabmancer import share

    monkeypatch.setattr(share, "open_page", lambda address: None)  # never a real browser
    monkeypatch.setattr(share, "reveal", lambda path: None)  # nor a real folder window
    from tabmancer import browsers

    monkeypatch.setattr(browsers, "places", list)  # nor your browsers' profiles
    # browsers.main sets one and drops the other for what it starts: put back after every test.
    monkeypatch.delenv(browsers.OPENING, raising=False)
    monkeypatch.delenv("BROWSER", raising=False)
    from tabmancer import card

    monkeypatch.setattr(card, "copy_picture", lambda canvas: False)  # nor your clipboard
    from tabmancer import updates

    monkeypatch.setattr(updates, "ask_pypi", lambda timeout=5: None)  # never PyPI
    from tabmancer import claudeupdate

    monkeypatch.setattr(claudeupdate, "_tried", False)  # each test a process of its own (it is off anyway)
    from tabmancer import wrapped

    # The week's word only where a test asks for it: run on a Friday afternoon, every dashboard said it.
    monkeypatch.setattr(wrapped, "due", lambda now=None: False)
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "tabmancer-config"))
    # An empty ~/.claude unless a test builds one: never read the real transcripts.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-home"))
    # Bit in block characters, whatever terminal runs the tests: a test that wants real pixels says so.
    from tabmancer import sixel

    monkeypatch.setenv("TABMANCER_SIXEL", "0")
    monkeypatch.setattr(sixel, "_terminal", None)
    monkeypatch.setattr(sixel, "_asked", False)


def _records(prompt, reply, title):
    return [
        {"type": "user", "cwd": CWD, "entrypoint": "cli", "message": {"content": prompt}},
        {"type": "assistant", "cwd": CWD,
         "message": {"content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"}},
        {"type": "ai-title", "aiTitle": title},
        {"type": "cost-state", "totalLinesAdded": 1, "totalLinesRemoved": 0},
    ]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A ~/.claude with two finished sessions, "one" and "two", in the same folder."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "recent_shutdowns", list)
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: set())
    project = tmp_path / "projects" / "C--work-demo"
    project.mkdir(parents=True)
    for name, title in [("one", "First task"), ("two", "Second task")]:
        lines = [json.dumps(r, separators=(",", ":")) for r in _records(f"do {name}", "done", title)]
        (project / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path
