"""The terminal's quit keys (q, Ctrl+Q, Esc) with handovers queued here: the first press says they go with
the dashboard, a second within QUIT_AGAIN_SECONDS quits; nothing queued, it quits at once."""

from __future__ import annotations

from tabmancer import app as app_module
from tabmancer import handoverwords
from tabmancer.app import SessionPicker


def test_the_words_count_what_goes():
    one = handoverwords.queued_on_quit(1, "q")
    assert one.words.startswith("1 handover is queued here: q again drops it. ") and one.severity == "warning"
    assert handoverwords.queued_on_quit(2, "Esc").words.startswith("2 handovers are queued here: Esc again drops them.")
    assert handoverwords.QUEUED_WHY in one.words


async def test_q_with_handovers_queued_asks_once_and_quits_on_the_second(fake_home, monkeypatch):
    now = [100.0]
    said: list = []
    exits: list = []
    leaving = [False]
    real_exit = SessionPicker.exit

    def exit_(self, *args, **kwargs):
        if leaving[0]:
            return real_exit(self, *args, **kwargs)
        exits.append(now[0])

    monkeypatch.setattr(SessionPicker, "exit", exit_)
    monkeypatch.setattr(SessionPicker, "say_note", lambda self, note: said.append(note.words))
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.2)
        app.row_keys.clock = lambda: now[0]
        app.queued.update({"a": "self", "b": "transcript"})
        await pilot.press("q")
        assert exits == [] and said[-1].startswith("2 handovers are queued here: q again drops them.")
        now[0] += app_module.QUIT_AGAIN_SECONDS + 0.5  # too late: a first press again
        await pilot.press("q")
        assert exits == [] and len(said) == 2
        now[0] += 1
        await pilot.press("escape")  # any quit key is the second press
        assert exits == [now[0]]
        app.queued.clear()
        await pilot.press("q")  # nothing queued: at once
        assert exits == [now[0], now[0]] and len(said) == 2
        leaving[0] = True
