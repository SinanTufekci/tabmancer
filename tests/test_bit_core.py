"""Bit's art and moves on their own (bitart, bitmoves), as the window's pet draws them: no terminal mascot
around them. Each move's frames come from a Context alone; a spin turns Bit round with its depth; every move
draws inside its place at any cell size; within a move nothing jumps from frame to frame; a stride takes ten
frames at least; a walk's poses come round and are drawn once; a call goes three ways."""

import math
from itertools import pairwise

import pytest

from tabmancer import bitart, bitmoves

CELLS = [bitart.WT_CELL, (6, 13), (9, 19), (14, 28)]


def frames(action: str, count: int, **context) -> list[bitart.Frame]:
    """A move's first frames, as a life would draw them: the beat on, and a walk's strides walked at its pace."""
    speed = bitmoves.TRAITS[action].speed
    return [bitmoves.frame(action, bitmoves.Context(
        beat=n, frame=n, gait=n * speed / bitmoves.stride_length(speed) if speed else 0.0, **context))
        for n in range(count)]


def joints(pose: bitart.Pose) -> dict[str, tuple[float, float]]:
    """Where the hands and elbows are, and the head and the face, in design pixels."""
    out = {}
    for side, arm in ((-1, pose.left), (1, pose.right)):
        _, elbow, hand = bitart._arm_points(side, arm)
        out[f"elbow{side}"] = (elbow[0], elbow[1] - pose.lift)
        out[f"hand{side}"] = (hand[0], hand[1] - pose.lift)
    out["head"] = (55 + pose.lean * 0.6, 40 - pose.lift + pose.head_y)
    out["face"] = (29 * math.sin(math.radians(pose.turn)), 29 * math.cos(math.radians(pose.turn)))
    return out


def test_a_spin_turns_bit_round_with_its_depth():
    def across(pixels, colours, rows) -> list[int]:
        return [x for y in rows for x, c in enumerate(pixels[y]) if c in colours]

    head_rows = range(32, 48)  # the helmet's middle rows, the visor in front
    front, side, back = (bitart.figure(bitart.Pose(turn=turn)) for turn in (0, 90, 180))
    helmet = {bitart.HELMET, bitart.HELMET_MID, bitart.HELMET_SHADE, bitart.RIM, bitart.VISOR, bitart.VISOR_EDGE}
    width = {turn: max(xs) - min(xs) for turn, pixels in ((0, front), (90, side))
             for xs in [across(pixels, helmet, head_rows)]}
    assert width[90] > width[0] * 0.6  # side on, as deep as it is: not a sliver
    assert across(front, {bitart.VISOR}, head_rows) and across(front, {bitart.EYE}, head_rows)
    assert not across(back, {bitart.VISOR, bitart.EYE}, head_rows)  # from behind, no face
    visor = across(bitart.figure(bitart.Pose(turn=45)), {bitart.VISOR}, head_rows)
    assert sum(visor) / len(visor) > 55 + 8  # turned a little, the face has gone round to one side
    assert across(back, {bitart.HELMET_SHADE}, range(36, 48))  # the grille on the back of the head
    turns = [f.pose.turn for f in frames("spin", bitmoves.TRAITS["spin"].frames[0] + 1)]
    assert 180 in turns and turns[0] == turns[-1] == 0  # round once, and facing you again


@pytest.mark.parametrize("cell", CELLS)
def test_every_move_draws_inside_its_place_at_any_cell_size(cell):
    width, height = bitart.size(cell)
    for action in bitmoves.TRAITS:
        for n, frame in enumerate(frames(action, 40)):
            if n % 8:
                continue
            pixels = bitart.frame_pixels(frame, cell)
            assert len(pixels) == height and all(len(row) == width for row in pixels), action
            assert any(px is not None for row in pixels for px in row), action


def test_the_feet_reach_the_bottom_of_bits_place():
    for cell in CELLS:
        pixels = bitart.figure(bitart.Pose(), cell)
        assert any(px is not None for px in pixels[-3]), cell  # down to its feet
        assert pixels == bitart.frame_pixels(bitart.Frame(bitart.Pose()), cell)  # a pose is a frame with nothing on


def test_within_a_move_nothing_of_bit_jumps_from_frame_to_frame():
    """A hand, an elbow, the head or the face goes at most this far from one frame to the next."""
    worst = (0.0, "")
    for action, trait in bitmoves.TRAITS.items():
        before = None
        for frame in frames(action, min(trait.frames[0], 150)):
            now = joints(frame.pose)
            if before is not None:
                worst = max(worst, *((math.dist(before[k], now[k]), f"{k} in {action}") for k in now))
            before = now
    assert worst[0] < 22, worst


def test_a_stride_takes_ten_frames_at_least_whatever_the_pace():
    """The legs and arms swing, they do not flicker: hurrying, a stride is longer, not faster."""
    assert bitmoves.stride_length(0.35) == 4  # strolling, as before
    for action, trait in bitmoves.TRAITS.items():
        if trait.speed:
            assert bitmoves.stride_length(trait.speed) / trait.speed >= 10, action
    feet = [f.pose.left_foot > f.pose.right_foot for f in frames("hurry", 40)]
    changes = sum(a != b for a, b in pairwise(feet))
    assert 2 <= changes <= 8  # they swing: four strides at most in forty frames


def test_a_walks_poses_come_round_and_are_drawn_once():
    bitart.drawn.cache_clear()
    for frame in frames("walk", 240):
        bitart.figure(frame.pose)
    info = bitart.drawn.cache_info()
    assert info.hits > info.misses * 3


def test_what_the_behaviour_knows_reaches_the_frame():
    """The context's eyes, lights and height: Bit asleep has its eyes closed; a call's beacon is red; off the
    ground, lifted; the behaviour's effects are drawn on top of the move's own."""
    assert bitmoves.frame("idle", bitmoves.Context(eyes="sleep")).pose.eyes == "closed"
    assert bitmoves.frame("idle", bitmoves.Context(beacon_red=True)).pose.beacon_colour == bitart.RED
    assert bitmoves.frame("idle", bitmoves.Context(height=3)).pose.lift >= 15
    note = bitart.Effect("note", 20, 20)
    assert note in bitmoves.frame("idle", bitmoves.Context(effects=[note])).effects
    assert bitmoves.frame("no such move", bitmoves.Context()) == bitmoves.frame("idle", bitmoves.Context())


def test_a_call_goes_three_ways_each_its_own():
    beat = 12  # mid-call: the arms up
    poses = {way: bitmoves.frame("call", bitmoves.Context(beat=beat, call=way)).pose for way in bitmoves.CALLS}
    assert len(bitmoves.CALLS) == 3 and len({repr(pose) for pose in poses.values()}) == 3
    assert all(pose.eyes == "wide" for pose in poses.values())
