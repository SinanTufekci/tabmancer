from datetime import datetime

from tabmancer import usage

SAMPLE = """You are currently using your subscription to power your Claude Code usage

Current session: 33% used · resets Oct 16, 4:10pm (Europe/Istanbul)
Current week (all models): 74% used · resets Oct 20, 6am (Europe/Istanbul)
Current week (Fable): 0% used · resets Oct 20, 6am (Europe/Istanbul)

What's contributing to your limits usage?
Last 24h · 4199 requests · 16 sessions
  96% of your usage was at >150k context
"""
NOW = datetime(2026, 10, 16, 14, 58)
REAL_READ_USAGE = usage.read_usage  # captured before the autouse fixture swaps it out
REAL_READ_CODEX = usage.read_codex  # ...and so is this one


def test_parse_reads_the_limit_lines_only():
    limits = usage.parse(SAMPLE, NOW)
    assert [(lim.label, lim.percent) for lim in limits] == [("session", 33), ("week", 74), ("week Fable", 0)]
    assert limits[0].resets == datetime(2026, 10, 16, 16, 10)
    assert limits[1].resets == datetime(2026, 10, 20, 6, 0)


def test_until_formats_the_time_left():
    assert usage.until(datetime(2026, 10, 16, 16, 10), NOW) == "1h12m"
    assert usage.until(datetime(2026, 10, 16, 15, 20), NOW) == "22m"
    assert usage.until(datetime(2026, 10, 20, 6, 0), NOW) == "3d"
    assert usage.until(None, NOW) == ""


def test_unreadable_output_gives_nothing():
    assert usage.parse("Usage is not available for API key users.", NOW) == []


def test_read_usage_without_the_cli(monkeypatch):
    monkeypatch.setattr(usage.shutil, "which", lambda name: None)
    assert REAL_READ_USAGE() == []  # conftest replaces read_usage itself for every test


async def test_top_bar_shows_session_and_week_limits(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    monkeypatch.setattr(usage, "read_usage", lambda: usage.parse(SAMPLE, NOW))
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            shown = str(app.topbar.query_one("#usage").render())
            if "33%" in shown:
                break
    assert "session" in shown and "33%" in shown and "week" in shown and "74%" in shown
    assert "Fable" not in shown  # a per-model week at 0 % is left out



# --- Codex ------------------------------------------------------------------------------------

def token_count(primary, secondary=None):
    """A token_count event as Codex writes it, with its rate limits."""
    return {"timestamp": "2026-10-15T09:43:18.174Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": None,
        "rate_limits": {"limit_id": "codex", "primary": primary, "secondary": secondary, "plan_type": "plus",
                        "credits": None, "rate_limit_reached_type": None}}}


def window(percent, minutes, resets):
    return {"used_percent": percent, "window_minutes": minutes, "resets_at": int(resets.timestamp())}


def test_codex_windows_are_named_by_their_length_and_a_past_reset_starts_again():
    later = datetime(2026, 10, 16, 18, 0)
    limits = usage.codex_limits(token_count(window(12.6, 300, later), window(40.0, 10080, later))["payload"]
                                ["rate_limits"], NOW)
    assert [(lim.label, lim.percent, lim.resets) for lim in limits] == [
        ("codex 5h", 13, later), ("codex week", 40, later)]
    gone = usage.codex_limits({"primary": window(90.0, 43200, datetime(2026, 10, 1))}, NOW)
    assert [(lim.label, lim.percent, lim.resets) for lim in gone] == [("codex month", 0, None)]
    assert usage.codex_limits({"primary": {"used_percent": "x"}, "secondary": None}, NOW) == []
    assert usage.codex_window(120) == "2h" and usage.codex_window(2880) == "2d"


def test_codex_limits_come_from_the_newest_reading(tmp_path, monkeypatch):
    import json
    import os

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    folder = tmp_path / "sessions" / "2026" / "10" / "15"
    folder.mkdir(parents=True)
    later = datetime(2026, 10, 20, 6, 0)

    def rollout(name, records, mtime):
        path = folder / f"rollout-{name}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        os.utime(path, (mtime, mtime))

    rollout("old", [token_count(window(5.0, 300, later))], 1000)
    rollout("mid", [token_count(window(20.0, 300, later)), token_count(window(25.0, 300, later))], 2000)
    rollout("new", [{"type": "session_meta", "payload": {}}], 3000)  # no reading in it: the next one counts
    assert [(lim.label, lim.percent) for lim in REAL_READ_CODEX(NOW)] == [("codex 5h", 25)]
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    assert REAL_READ_CODEX(NOW) == []


def test_codex_limits_are_read_only_with_codex_turned_on(monkeypatch):
    from tabmancer import clis

    codex = [usage.Limit("codex week", 40, None, "")]
    monkeypatch.setattr(usage, "read_usage", lambda: usage.parse(SAMPLE, NOW))
    monkeypatch.setattr(usage, "read_codex", lambda now=None: codex)
    assert [lim.label for lim in usage.read_all()] == ["session", "week", "week Fable"]  # Claude only: the default
    clis.set_enabled(["claude", "codex"])
    assert [lim.label for lim in usage.read_all()] == ["session", "week", "week Fable", "codex week"]
    assert usage.short_window("codex 5h") and usage.short_window("session") and not usage.short_window("codex week")


def test_the_top_bar_always_shows_codex_limits():
    from tabmancer.widgets import TopBar

    shown = []
    bar = TopBar()
    bar.put = lambda part, content: shown.append(content.plain)
    bar.show_usage([usage.Limit("week Fable", 10, None, ""), usage.Limit("codex month", 10, None, "")])
    assert "codex month" in shown[0] and "Fable" not in shown[0]  # a per-model week shows only once it fills
