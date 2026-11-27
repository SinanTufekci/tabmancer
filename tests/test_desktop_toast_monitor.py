"""A team's monitor M opened from the window counts as looking at the window (WindowFront.looked_at): while it, or a
dialog of its, has the focus, no desktop note goes out, wherever the window itself is (behind it, minimised, in the
tray). Stand-ins for the windows: no window is shown.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tabmancer.desktop import front


def a_window(parent=None, visible: bool = True, minimised: bool = False) -> SimpleNamespace:
    return SimpleNamespace(parent=lambda: parent, isVisible=lambda: visible, isMinimized=lambda: minimised)


def looked_at(window: SimpleNamespace, active, monkeypatch) -> bool:
    monkeypatch.setattr(front.QApplication, "activeWindow", staticmethod(lambda: active))
    return front.WindowFront.looked_at(SimpleNamespace(window=lambda: window))


@pytest.mark.parametrize(("visible", "minimised"), [(True, False), (True, True), (False, False)])
def test_a_monitor_m_opened_in_front_is_looking_at_the_window(monkeypatch, visible, minimised):
    window = a_window(visible=visible, minimised=minimised)
    shop, other = a_window(), a_window()
    window.monitors = {"shop": shop, "other": other}
    assert looked_at(window, shop, monkeypatch)  # behind it, minimised or in the tray: you look at the team
    assert looked_at(window, a_window(parent=other), monkeypatch)  # a dialog of a monitor (g's, a's)


def test_anything_else_in_front_is_not(monkeypatch):
    window = a_window()
    window.monitors = {"shop": a_window()}
    assert not looked_at(window, a_window(), monkeypatch)  # another app's window, a monitor not opened from it
    assert not looked_at(window, None, monkeypatch)  # nothing of tabmancer's in front
    assert looked_at(window, window, monkeypatch)  # the window itself, as before
    assert looked_at(window, a_window(parent=window), monkeypatch)  # a dialog of its
    window.monitors = {}
    window.isMinimized = lambda: True
    assert not looked_at(window, window, monkeypatch)  # minimised, even while Qt still names it the active one
