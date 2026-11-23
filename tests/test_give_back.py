"""focus.give_back: the keys go back only from a Windows Terminal window, and only to a window still there."""

from __future__ import annotations

import subprocess

import pytest

from tabmancer import focus

GIVE_BACK = focus.give_back  # conftest swaps it for a stand-in in every test: this is the real one


@pytest.mark.parametrize(("took", "terminal", "still_there", "given"), [
    (777, True, True, True),  # the terminal brought its window up: the keys go back
    (777, False, True, False),  # you went to the browser meanwhile: you stay there
    (777, True, False, False),  # the window that had them closed meanwhile
])
def test_the_keys_go_back_only_from_the_terminal(monkeypatch, took, terminal, still_there, given):
    monkeypatch.setattr(focus.sys, "platform", "win32")
    monkeypatch.setattr(focus, "foreground", lambda: took)
    monkeypatch.setattr(focus, "is_terminal", lambda hwnd: hwnd == took and terminal)
    monkeypatch.setattr(focus, "is_window", lambda hwnd: still_there)
    ran = []

    def run(argv, **kwargs):
        ran.append(kwargs["env"]["CCR_HWND"])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(focus.subprocess, "run", run)
    assert GIVE_BACK(4242, wait=0.3) is given
    assert ran == (["4242"] if given else [])


def test_nothing_took_the_keys(monkeypatch):
    monkeypatch.setattr(focus.sys, "platform", "win32")
    monkeypatch.setattr(focus, "foreground", lambda: 4242)  # still the window that had them
    monkeypatch.setattr(focus.subprocess, "run", lambda *a, **k: pytest.fail("given back with nothing taken"))
    assert GIVE_BACK(4242, wait=0.2) is False
