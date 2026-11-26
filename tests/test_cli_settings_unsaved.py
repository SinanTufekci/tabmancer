"""The , dialog with a config.json that is there but cannot be read: nothing is saved (merge_json leaves it as
it is), and the terminal says so instead of saying nothing."""

from __future__ import annotations

from tabmancer import clis, config


async def test_an_unreadable_config_json_is_said_and_left_as_it_is(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    said = []
    monkeypatch.setattr(SessionPicker, "notify", lambda self, message, **kw: said.append((str(message), kw)))
    monkeypatch.setattr(SessionPicker, "reload_sessions", lambda self: said.append(("reloaded", {})))
    path = config.config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        said.clear()
        await pilot.press("comma", "down", "space", "enter")
        await pilot.pause(0.2)
    assert said == [(config.NOT_SAVED, {"severity": "warning", "timeout": 8})]
    assert path.read_text(encoding="utf-8") == "{not json" and clis.enabled() == ["claude"]
