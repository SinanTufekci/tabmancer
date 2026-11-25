"""p's words and steps (prompting.py), one copy for the terminal dashboard and the desktop window: refused
for a background session, the dialog's heading and keys, the launch, and what sending came to."""

from dataclasses import replace

import pytest

from tabmancer import prompting
from tabmancer.actions import Note
from tabmancer.launch import Mode
from tabmancer.sessions import Session, Status


@pytest.fixture
def closed(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting.teams, "member_of", lambda session, saved=None: None)
    return Session(session_id="s1", path=None, cwd=str(tmp_path / "shop"), title="x" * 70, status=Status.DONE)


def test_what_p_says_is_one_copy_for_both_front_ends(closed, monkeypatch):
    assert prompting.refusal(closed) is None
    assert prompting.refusal(replace(closed, status=Status.BACKGROUND)) == prompting.ATTACH
    assert prompting.heading(closed) == f"Prompt shop · {'x' * 49}…"
    assert prompting.keys(closed) == [("Enter", "open in a new tab"), ("Ctrl+B", "run in the background"),
                                      ("Esc", "cancel")]
    assert prompting.keys(replace(closed, cli="codex")) == [("Enter", "open in a new tab"), ("Esc", "cancel")]
    assert prompting.placeholder(closed) == "What should Claude do next?"
    assert prompting.placeholder(replace(closed, cli="agy")) == "What should Antigravity do next?"
    launch = prompting.launch_for(closed, "Add the tests")
    assert (launch.mode, launch.session_id, launch.prompt, launch.cli) == (Mode.RESUME, "s1", "Add the tests", "claude")
    assert launch.cwd == closed.cwd and launch.title.startswith("shop: xxx") and len(launch.title) == 48
    monkeypatch.setattr(prompting.teams, "as_member", lambda session, plain: replace(plain, options=("--as", "lead")))
    assert prompting.launch_for(closed, "Go on").options == ("--as", "lead")  # a team's member, as that member


class Calls:
    """What send asked for, and what each step answers."""

    def __init__(self, live=(), leftover=(), tab_error=None, started="Started bg-1.", start_error=None):
        self.live, self.leftover, self.tab_error = set(live), list(leftover), tab_error
        self.started, self.start_error = started, start_error
        self.tabs: list = []
        self.starts: list = []
        self.said: list[Note] = []

    def live_ids(self):
        return self.live

    def open_tabs(self, launches):
        if self.tab_error:
            raise self.tab_error
        self.tabs.append(launches)
        return self.leftover

    def start(self, launch):
        self.starts.append((launch, len(self.said)))  # what was said before it started
        if self.start_error:
            raise self.start_error
        return self.started

    def send(self, session, how, prompt="Go on"):
        return prompting.send(session, how, prompt, self.live_ids, self.open_tabs, self.start, self.said.append)


def test_a_prompt_opens_its_session_in_a_new_tab(closed):
    calls = Calls()
    sent = calls.send(closed, prompting.TAB)
    assert sent.outcome == "opened" and sent.note.words == "Opened shop in a new tab with your prompt."
    assert sent.note.severity == "information" and sent.leftover == []
    ((launch,),) = calls.tabs
    assert launch.prompt == "Go on" and launch.session_id == "s1" and calls.starts == [] and calls.said == []


def test_a_prompt_to_a_session_open_now_opens_nothing(closed):
    calls = Calls(live={"s1"})
    sent = calls.send(closed, prompting.TAB)
    assert sent.outcome == "elsewhere" and calls.tabs == [] and calls.starts == []
    assert sent.note.words == "shop is open in another tab now: write to it there." and sent.note.severity == "warning"
    assert Calls(live={"s1"}).send(closed, prompting.BACKGROUND).outcome == "elsewhere"


def test_a_tab_that_would_not_open_is_said(closed):
    sent = Calls(tab_error=OSError("wt.exe is gone")).send(closed, prompting.TAB)
    assert sent.outcome == "failed" and sent.note.words == "Could not open the tab: wt.exe is gone"
    assert sent.note.severity == "error"
    sent = Calls(leftover=["claude --resume s1 'Go on'"]).send(closed, prompting.TAB)
    assert sent.outcome == "leftover" and sent.leftover == ["claude --resume s1 'Go on'"]
    assert sent.note.words == "No supported terminal here. Run: claude --resume s1 'Go on'"


def test_ctrl_b_starts_it_in_the_background_saying_so_first(closed):
    calls = Calls()
    sent = calls.send(closed, prompting.BACKGROUND)
    assert calls.said == [Note("Starting shop in the background…")]
    ((launch, said_before),) = calls.starts
    assert said_before == 1 and launch.prompt == "Go on" and calls.tabs == []
    assert sent.outcome == "started" and sent.note.words == "Started bg-1." and sent.note.severity == "information"
    sent = Calls(start_error=OSError("The claude CLI is not on PATH.")).send(closed, prompting.BACKGROUND)
    assert sent.outcome == "failed" and sent.note.words == "The claude CLI is not on PATH."
    assert sent.note.severity == "error"


def test_a_cli_with_no_background_sessions_opens_a_tab_saying_why(closed):
    calls = Calls()
    sent = calls.send(replace(closed, cli="codex"), prompting.BACKGROUND)
    assert sent.outcome == "opened" and calls.starts == [] and len(calls.tabs) == 1 and calls.tabs[0][0].cli == "codex"
    assert sent.note.words == "Codex has no background sessions: opening a tab instead. Opened shop in a new tab with your prompt."
    assert sent.note.severity == "warning"
