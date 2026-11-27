"""Sessions the window's engine opened (Engine.open: a team started, c, B, h's fresh tab, and what the window
opens when it acts) that never came up are named a while later, as the terminal dashboard names them: the
engine's timer on the window (WindowFront.later, CAME_UP_SECONDS) asks Engine.came_up_check, which reads what
the window's reads saw open (the Openings it shares with Enter's Opener) and says the rest on Bit's line.
Offscreen over the made-up sessions of `tabmancer --demo`; the tabs open through the demo's stand-in.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from dataclasses import replace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtTest import QTest
from test_desktop_front import qapp, read, taking, window, world  # noqa: F401  (fixtures)

from tabmancer import openings
from tabmancer.launch import Mode
from tabmancer.sessions import Exit


def test_the_ones_the_engine_opened_and_never_came_up_are_named(qapp, window, monkeypatch):  # noqa: F811
    monkeypatch.setattr(openings, "CAME_UP_SECONDS", 0.2)  # read when the engine sets its timer
    shown = window()
    read(qapp, shown)
    said: list[tuple[str, str]] = []
    real = shown.say_note
    shown.say_note = lambda words, seconds, severity: said.append((words, severity)) or real(words, seconds, severity)
    closed = sorted((s for s in shown.snap.sessions.values() if s.exit is not Exit.RUNNING and s.cwd
                     and s.session_id not in shown.front.members), key=lambda s: s.project)
    up, gone = next(s for s in closed), next(s for s in closed if s.project != closed[0].project)
    launches = shown.opener.openings.launches([up, gone], lambda s: Mode.RESUME, {}, {})
    shown.engine.open(launches, None, True)  # the update seen to: they open now
    assert {up.session_id, gone.session_id} <= set(shown.opener.openings.opening)
    taking(shown, replace(up, exit=Exit.RUNNING))  # a read sees one of them open
    assert shown.opener.openings.came_up >= {up.session_id}
    QTest.qWait(600)
    qapp.processEvents()
    missing = [words for words, _ in said if "did not come up" in words]
    assert missing == [f"{gone.project} did not come up: its tab says why."]
    assert dict(said)[missing[0]] == "warning"
