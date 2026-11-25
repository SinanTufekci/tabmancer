"""p on the terminal dashboard takes its words and its send from prompting.py, the copy the window uses: the
dialog by the session's CLI, the send on a thread (a terminal takes seconds to open a tab), what it came to said
once."""

from __future__ import annotations

import threading
from pathlib import Path

from tabmancer import app as app_module
from tabmancer import prompting
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit, Session, Status


def closed(sid: str, **kw) -> Session:
    return Session(**{"session_id": sid, "path": Path(f"{sid}.jsonl"), "cwd": "C:/work/shop", "title": "the work",
                      "status": Status.DONE, "exit": Exit.CLEAN, **kw})


async def prompted(monkeypatch, session: Session, answer: tuple[str, str] | None):
    """p on this session, the dialog answered: what it showed, and what was said, opened, started, polled."""
    done: list = []
    monkeypatch.setattr(SessionPicker, "notify", lambda self, message, **kw: done.append(("said", str(message), kw)))
    monkeypatch.setattr(SessionPicker, "go_to_tab", lambda self, session: done.append(("go to", session.session_id)))
    monkeypatch.setattr(SessionPicker, "poll_live", lambda self, full=True: done.append(("poll", full)))
    monkeypatch.setattr(SessionPicker, "live_ids", lambda self: set())
    monkeypatch.setattr(SessionPicker, "poll_tick", lambda self: None)
    monkeypatch.setattr(SessionPicker, "current", lambda self: session)
    monkeypatch.setattr(app_module.time, "sleep", lambda seconds: None)

    def open_tabs(launches, *a):
        done.append(("opened", launches[0].prompt, threading.current_thread() is threading.main_thread()))
        return []

    monkeypatch.setattr(app_module, "open_tabs", open_tabs)
    monkeypatch.setattr(app_module, "run_in_background", lambda launch: done.append(("started", launch.prompt))
                        or f"Started {launch.session_id} in the background.")
    app = SessionPicker(days=14)
    shown = None
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.3)
        done.clear()  # the dashboard's own first poll
        app.action_prompt()
        await pilot.pause(0.2)
        if isinstance(app.screen, app_module.PromptDialog):
            dialog = app.screen
            shown = (dialog.query_one("Input").placeholder, str(dialog.query(".hint").last().render()))
            dialog.dismiss(answer)
        await pilot.pause(0.5)
    return shown, done


async def test_a_codex_session_is_asked_by_its_name_and_its_background_opens_a_tab_said_once(fake_home, monkeypatch):
    shown, done = await prompted(monkeypatch, closed("c", cli="codex"), (prompting.BACKGROUND, "go on"))
    assert shown[0] == "What should Codex do next?" and "Ctrl+B" not in shown[1]
    assert done == [("opened", "go on", False),  # on a thread, not the UI's
                    ("said", ("Codex has no background sessions: opening a tab instead. Opened shop in a new tab "
                              "with your prompt."), {"severity": "warning", "timeout": prompting.ELSEWHERE_SECONDS})]


async def test_the_background_says_it_starts_then_what_started_then_looks_again(fake_home, monkeypatch):
    shown, done = await prompted(monkeypatch, closed("b"), (prompting.BACKGROUND, "go on"))
    assert shown[0] == prompting.PLACEHOLDER and "Ctrl+B" in shown[1]
    assert done == [("said", "Starting shop in the background…", {}), ("started", "go on"),
                    ("said", "Started b in the background.", {"timeout": prompting.STARTED_SECONDS}), ("poll", True)]


async def test_one_in_the_background_is_attached_to_not_prompted(fake_home, monkeypatch):
    shown, done = await prompted(monkeypatch, closed("g", status=Status.BACKGROUND, exit=Exit.RUNNING), None)
    assert shown is None and done == [("said", prompting.ATTACH, {"severity": "warning"})]
