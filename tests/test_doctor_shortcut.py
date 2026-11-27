"""doctor: a desktop shortcut written before the window took over (or before the dashboard's named window)
is named, never rewritten."""

from __future__ import annotations

import pytest

from tabmancer import doctor, shortcuts

WT = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\wt.EXE"
OLDEST = (WT, '-w new new-tab --title tabmancer pwsh -NoLogo -Command "& \'tabmancer.EXE\'"')
NAMED = (WT, ('-w tabmancer new-tab --title tabmancer pwsh -NoLogo -Command "& \'tabmancer.EXE\' --tabs-window '
              'tabmancer"'))
TERMINAL = (WT, ('-w tabmancer new-tab --title tabmancer pwsh -NoLogo -Command "& \'tabmancer.EXE\' --terminal '
                 '--tabs-window tabmancer"'))
TERMINAL_UNNAMED = (WT, "-w new new-tab --title tabmancer tabmancer.EXE --terminal")
WINDOW = (r"C:\Users\me\.local\bin\tabmancer-desktop.exe", "")
PYTHONW = (r"C:\tools\tabmancer\Scripts\pythonw.exe", "-m tabmancer desktop")


@pytest.mark.parametrize(("found", "qt", "level", "words"), [
    (OLDEST, True, "warn", "as before the window took over"),
    (NAMED, True, "warn", "as before the window took over"),  # a terminal tab, though named
    (TERMINAL, True, "warn", "as before the window took over"),  # the terminal one, with the window there
    (OLDEST, False, "warn", "as before the window took over"),  # bare tabmancer: says how to get Qt, and goes
    (NAMED, False, "warn", "as before the window took over"),
    (TERMINAL, False, "ok", "terminal dashboard in a window of its own name"),  # no Qt: as written now
    (TERMINAL_UNNAMED, False, "warn", "predates the dashboard's named window"),
    (WINDOW, True, "ok", "opens the dashboard's window"),
    (PYTHONW, True, "ok", "opens the dashboard's window"),
    (WINDOW, False, "warn", "needs Qt"),  # Qt taken out since
    ((r"C:\Windows\System32\cmd.exe", "/c tabmancer"), True, None, ""),  # neither: not tabmancer's own
    ((r"C:\tools\pythonw.exe", "-m tabmancer startup-run"), True, None, ""),
    (None, True, None, ""),  # no desktop shortcut
])
def test_the_desktop_shortcut_is_checked_for_the_window(monkeypatch, tmp_path, found, qt, level, words):
    from tabmancer import desktop

    monkeypatch.setattr(desktop, "available", lambda: qt)
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setenv("TABMANCER_SHORTCUT_DIR", str(tmp_path))
    asked, written = [], []
    monkeypatch.setattr(shortcuts, "read_shortcut", lambda path: asked.append(path) or found)
    monkeypatch.setattr(shortcuts, "write_shortcut", lambda *args, **kwargs: written.append(args))
    result = doctor.shortcut_check()
    assert (result[0] if result else None) == level
    assert words in (result[1] if result else "")
    if level == "warn" and words != "needs Qt":
        assert "tabmancer shortcut writes it again" in result[1]
    assert asked == [tmp_path / "tabmancer.lnk"] and written == []  # only read


def test_off_windows_nothing_is_read(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.setattr(shortcuts, "read_shortcut", lambda path: pytest.fail("read off Windows"))
    assert doctor.shortcut_check() is None
