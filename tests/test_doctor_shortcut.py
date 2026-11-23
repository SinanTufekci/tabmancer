"""doctor: a desktop shortcut written before the dashboard's named window is named, never rewritten."""

from __future__ import annotations

import pytest

from tabmancer import doctor, shortcuts

WT = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\wt.EXE"


@pytest.mark.parametrize(("found", "level"), [
    ((WT, '-w new new-tab --title tabmancer pwsh -NoLogo -Command "& \'tabmancer.EXE\'"'), "warn"),
    ((WT, ('-w tabmancer new-tab --title tabmancer pwsh -NoLogo -Command "& \'tabmancer.EXE\' --tabs-window '
           'tabmancer"')), "ok"),
    ((r"C:\Windows\System32\cmd.exe", "/c tabmancer"), None),  # not through Windows Terminal: no window names
    (None, None),  # no desktop shortcut
])
def test_the_desktop_shortcut_is_checked_for_the_named_window(monkeypatch, tmp_path, found, level):
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setenv("TABMANCER_SHORTCUT_DIR", str(tmp_path))
    asked, written = [], []
    monkeypatch.setattr(shortcuts, "read_shortcut", lambda path: asked.append(path) or found)
    monkeypatch.setattr(shortcuts, "write_shortcut", lambda *args, **kwargs: written.append(args))
    result = doctor.shortcut_check()
    assert (result[0] if result else None) == level
    if level == "warn":
        assert "tabmancer shortcut" in result[1]
    assert asked == [tmp_path / "tabmancer.lnk"] and written == []  # only read


def test_off_windows_nothing_is_read(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.setattr(shortcuts, "read_shortcut", lambda path: pytest.fail("read off Windows"))
    assert doctor.shortcut_check() is None
