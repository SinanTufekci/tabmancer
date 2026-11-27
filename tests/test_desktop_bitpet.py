"""Bit in the window (desktop/bitpet.py): its life a frame at a time, its pictures made once, and its floor's
timers, which run only while it moves and are single shots while it stands still. Skipped without the desktop
extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import random

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from tabmancer import bitmoves
from tabmancer.config import merge_json, read_json
from tabmancer.desktop import bitpet, look

look.fonts_for_offscreen()
ACCENT = bitpet.rgb(look.ACCENT)
SPAN = 40.0  # octant pixels of floor


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def life(seed: int = 1) -> bitpet.Life:
    return bitpet.Life(random.Random(seed))


def live_out(bit: bitpet.Life, most: int = 2000) -> int:
    """Frames till Bit stands still again (each drawn, as the floor does)."""
    for n in range(most):
        if not bit.moving():
            return n
        bit.advance(SPAN)
        bit.picture(ACCENT)
    raise AssertionError(f"still moving after {most} frames: {bit.action}")


def test_a_stroll_goes_along_the_floor_and_ends_standing_still():
    bit = life()
    bit.start("walk", 60)
    bit.direction = 1
    seen = set()
    for _ in range(60):
        bit.advance(SPAN)
        seen.add(round(bit.x))
        assert 0 <= bit.x <= SPAN
    assert len(seen) > 10  # it went somewhere
    live_out(bit)
    assert bit.resting() and not bit.moving()
    first = bit.picture(ACCENT)
    assert bit.picture(ACCENT) == first  # still: the same picture, frame after frame


def test_a_call_starts_with_the_first_session_waiting_and_ends_with_the_last():
    bit = life()
    assert bit.set_mood(working=2, waiting=0)  # news
    assert bit.resting()
    assert bit.set_mood(working=2, waiting=1) and bit.action == "call" and bit.eyes == "alarm"
    assert not bit.set_mood(working=2, waiting=1)  # the same: no news
    assert bit.set_mood(working=2, waiting=0) and bit.resting()
    bit.call()
    calls = bit.calls
    live_out(bit)
    assert bit.calls == calls + 1  # the next call goes another way (bitmoves.CALLS)


def test_calm_never_leaves_the_floor_and_moves_as_bitmoves_calm_says():
    bit = life()
    bit.calm = True
    bit.click(0.0)
    assert bit.action == bitmoves.CALM["jump"]
    for _ in range(40):
        bit.advance(SPAN)
        assert bit.height == 0
    bit.start("cheer", 40)
    assert bit.action == bitmoves.CALM["cheer"]


def test_three_quick_clicks_make_bit_dizzy_and_one_hops():
    bit = life()
    bit.click(0.0)
    assert bit.action == "jump"
    heights = []
    for _ in range(20):
        bit.advance(SPAN)
        heights.append(bit.height)
    assert max(heights) > 0  # off the floor, and down again
    bit.click(10.0)
    bit.click(10.5)
    bit.click(11.0)
    assert bit.action == "dizzy"


def test_quiet_bit_yawns_nods_off_and_sleeps_still_till_a_click_wakes_it():
    bit = life()
    bit.doze()
    actions = []
    for _ in range(400):
        bit.advance(SPAN)
        bit.picture(ACCENT)
        actions.append(bit.action)
        if bit.action == "sleep" and not bit.moving():
            break
    assert "yawn" in actions and "nod" in actions and bit.action == "sleep" and not bit.moving()
    bit.click(0.0)
    assert bit.action == "wake" and bit.moving()


def test_a_still_bit_is_one_picture_made_once(qapp):
    sprites = bitpet.Sprites()
    bit = life()
    key, first = sprites.get(bit.picture(ACCENT))
    again_key, again = sprites.get(bit.picture(ACCENT))
    assert again_key == key and again is first and len(sprites.kept) == 1
    assert (first.width(), first.height()) == (bitpet.SPRITE_W, bitpet.SPRITE_H) == (66, 48)
    image = first.toImage()
    assert image.pixelColor(0, 0).alpha() == 0  # clear where nothing is drawn
    assert any(image.pixelColor(x, y).alpha() == 255 for x in range(66) for y in range(48))
    bit.blink()
    blink_key, _ = sprites.get(bit.picture(ACCENT))
    assert blink_key != key


@pytest.fixture
def floor(qapp):
    """The floor in a window of its own, as wide as a Bit panel; closed after the test."""
    made = []

    def make(width: int = 600, **kwargs) -> tuple[QWidget, bitpet.Pet]:
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        pet = bitpet.Pet(look.ACCENT, seed=3, **kwargs)
        box.addWidget(pet)
        host.resize(width, 60)
        host.show()
        qapp.processEvents()
        made.append(host)
        return host, pet

    yield make
    for host in made:
        host.close()
        host.deleteLater()
    qapp.processEvents()


def running(pet: bitpet.Pet) -> set[str]:
    names = {pet.ticker: "frames", pet.blinker: "blink", pet.mover: "move", pet.caller: "call", pet.dozer: "doze",
             pet.breather: "breath"}
    return {name for timer, name in names.items() if timer.isActive()}


def settle(qapp, pet: bitpet.Pet) -> None:
    """The move under way played out (frames stepped by hand, not waited for)."""
    for _ in range(2000):
        if not pet.ticker.isActive():
            return
        pet.step()
    raise AssertionError("still moving")


def test_standing_still_only_single_shot_timers_run(qapp, floor):
    """No frame timer while nothing moves: the next blink, move and doze wait as single shots."""
    _, pet = floor()
    settle(qapp, pet)
    assert running(pet) == {"blink", "move", "doze"}
    assert all(timer.isSingleShot() for timer in (pet.blinker, pet.mover, pet.dozer))
    pet.blink()
    assert "frames" in running(pet)  # a blink: a few frames...
    settle(qapp, pet)
    assert "frames" not in running(pet)  # ...and still again


def test_out_of_sight_nothing_runs(qapp, floor):
    """Its panel closed, or the window minimised: every timer stops; back in sight, they wait again."""
    host, pet = floor()
    settle(qapp, pet)
    pet.hide()
    qapp.processEvents()
    assert running(pet) == set()
    pet.show()
    qapp.processEvents()
    assert running(pet) == {"blink", "move", "doze"}
    host.setWindowState(Qt.WindowState.WindowMinimized)
    qapp.processEvents()
    assert running(pet) == set()
    host.setWindowState(Qt.WindowState.WindowNoState)
    qapp.processEvents()
    assert running(pet) == {"blink", "move", "doze"}


def test_a_session_needing_you_brings_a_call_and_a_call_again_till_none_does(qapp, floor):
    _, pet = floor()
    settle(qapp, pet)
    pet.set_mood(0, 1)
    assert pet.life.action == "call" and "frames" in running(pet)
    settle(qapp, pet)
    assert running(pet) == {"blink", "call"}  # the next call waits; no stroll, no doze while you are needed
    pet.set_mood(0, 0)
    settle(qapp, pet)
    assert running(pet) == {"blink", "move", "doze"}


def test_away_draws_no_bit_and_its_line_takes_the_floor(qapp, floor):
    _, pet = floor()
    settle(qapp, pet)
    assert not pet.at.isNull() and pet.line.x() == bitpet.SPRITE_W + bitpet.GAP
    pet.set_way("away")
    assert pet.at.isNull() and pet.pixmap is None and running(pet) == set()
    assert pet.line.x() == 0 and pet.line.width() == pet.width()
    pet.set_mood(0, 1)  # no call: nothing to show it
    assert running(pet) == set()
    pet.set_way("pixels")
    assert not pet.at.isNull() and pet.line.x() == bitpet.SPRITE_W + bitpet.GAP


def test_no_motion_holds_a_pose_for_the_news_and_runs_no_timer(qapp, floor):
    """View ▸ Reduce motion: off. A session needing you: a still call (arms up, eyes wide); none: standing."""
    _, pet = floor()
    settle(qapp, pet)
    standing = pet.shown
    pet.set_motion("off")
    assert running(pet) == set() and pet.shown == standing
    pet.set_mood(0, 1)
    assert running(pet) == set() and pet.shown != standing and pet.life.beat == bitpet.CALL_HELD
    pet.set_mood(0, 0)
    assert running(pet) == set() and pet.shown == standing
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pet.at.center())
    assert running(pet) == set()  # a click: no hop either


def test_a_click_on_bit_hops_and_one_beside_it_does_nothing(qapp, floor):
    _, pet = floor()
    settle(qapp, pet)
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pet.at.topRight() + QPoint(40, 20))
    assert "frames" not in running(pet)
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pet.at.center())
    assert pet.life.action == "jump" and "frames" in running(pet)


def test_the_line_goes_along_beside_bit_as_wide_wherever_it_strolls(qapp, floor):
    _, pet = floor(600)
    settle(qapp, pet)
    home, wide = pet.line.x(), pet.line.width()
    assert pet.roam() == bitpet.ROAM and wide == 600 - bitpet.SPRITE_W - bitpet.GAP - bitpet.ROAM
    pet.life.x = pet.span()
    pet.draw()
    assert pet.line.x() == home + bitpet.ROAM and pet.line.width() == wide
    assert pet.line.geometry().right() < pet.width()


def test_a_narrow_floor_keeps_the_line_readable_and_bit_looks_about_instead(qapp, floor):
    _, pet = floor(bitpet.SPRITE_W + bitpet.GAP + bitpet.LEAST_LINE)
    assert pet.roam() == 0 and pet.span() == 0
    pet.life.rng = random.Random(0)
    for _ in range(30):
        pet.life.next_move(pet.span())
        assert not pet.life.speed()  # no stroll where there is no room
        pet.life.rest()


def test_b_choice_is_the_terminals_mascot_setting(qapp):
    assert bitpet.saved_way() == "pixels"  # none saved: lively
    for saved, way in (("blocks", "pixels"), ("pixels", "pixels"), ("calm", "calm"), ("away", "away"),
                       (False, "away")):
        merge_json("config.json", mascot=saved)
        assert bitpet.saved_way() == way
    assert [bitpet.next_way(w) for w in bitpet.WAYS] == ["calm", "away", "pixels"]
    assert bitpet.save_way("calm") and read_json("config.json")["mascot"] == "calm"
