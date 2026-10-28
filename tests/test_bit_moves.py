"""Bit's moves in real pixels: from one frame to the next its parts move a few pixels, never
jump, in a move and from one move into the next; a spin turns it round with its depth, not
flat as a sheet of paper; and a hurry's legs swing, they do not flicker."""

import math
from itertools import pairwise

from tabmancer import bitart, bitmoves
from tabmancer import mascot as M


def joints(pose: bitart.Pose) -> dict[str, tuple[float, float]]:
    """Where the hands and elbows are, and the head, in design pixels."""
    out = {}
    for side, arm in ((-1, pose.left), (1, pose.right)):
        _, elbow, hand = bitart._arm_points(side, arm)
        out[f"elbow{side}"] = (elbow[0], elbow[1] - pose.lift)
        out[f"hand{side}"] = (hand[0], hand[1] - pose.lift)
    out["head"] = (55 + pose.lean * 0.6, 40 - pose.lift + pose.head_y)
    out["face"] = (29 * math.sin(math.radians(pose.turn)), 29 * math.cos(math.radians(pose.turn)))
    return out


def test_from_frame_to_frame_nothing_of_bit_jumps():
    """A long life, idle, at work, called and cheering: a hand, an elbow, the head or the face goes
    at most this far from one frame to the next, in a move or into the next one (a move used to
    start from its own pose, a hand fifty pixels from where it was)."""
    robot = M.Mascot(seed=7, fine=True)
    moods = [(0, 0)] * 3 + [(2, 0)] * 2 + [(0, 1)]
    before, worst = None, (0.0, "")
    for n in range(12000):
        if n % 1500 == 0:
            robot.set_mood(*moods[(n // 1500) % len(moods)])
            if n % 3000 == 0:
                robot.celebrate("x")
        robot.advance(80)
        now = joints(robot.hires().pose)
        if before is not None:
            worst = max(worst, *((math.dist(before[k], now[k]), f"{k} in {robot.action}") for k in now))
        before = now
    assert worst[0] < 22, worst


def test_a_spin_turns_bit_round_with_its_depth():
    def drawn(turn: float) -> list[list]:
        return bitart.figure(bitart.Pose(turn=turn))

    def across(pixels, colours, rows) -> list[int]:
        return [x for y in rows for x, c in enumerate(pixels[y]) if c in colours]

    head_rows = range(32, 48)  # the helmet's middle rows, the visor in front
    front, side, back = drawn(0), drawn(90), drawn(180)
    helmet = {bitart.HELMET, bitart.HELMET_MID, bitart.HELMET_SHADE, bitart.RIM}
    width = {turn: max(xs) - min(xs) for turn, pixels in ((0, front), (90, side))
             for xs in [across(pixels, helmet | {bitart.VISOR, bitart.VISOR_EDGE}, head_rows)]}
    assert width[90] > width[0] * 0.6  # side on, as deep as it is (the ears aside): not a sliver of six pixels
    assert across(front, {bitart.VISOR}, head_rows) and not across(back, {bitart.VISOR, bitart.EYE}, head_rows)
    visor = across(drawn(45), {bitart.VISOR}, head_rows)
    assert sum(visor) / len(visor) > 55 + 8  # turned a little, the face has gone round to one side
    assert across(back, {bitart.HELMET_SHADE}, range(36, 48))  # the grille on the back of the head
    robot = M.Mascot(seed=1, fine=True)
    robot.start("spin", M.TRAITS["spin"].frames[0])
    turns = []
    for _ in range(M.TRAITS["spin"].frames[0] + 1):
        robot.advance(80)
        turns.append(robot.hires().pose.turn)
    assert 180 in turns and turns[-1] == 0  # round once, and facing you again


def test_hurrying_bit_swings_its_legs_a_stride_in_ten_frames_at_least():
    robot = M.Mascot(seed=3, fine=True)
    robot.start("hurry", 10 ** 6)
    feet = []
    for _ in range(40):
        robot.advance(80)
        pose = robot.hires().pose
        feet.append(pose.left_foot > pose.right_foot)
    changes = sum(a != b for a, b in pairwise(feet))
    assert changes <= 8  # four strides at most in forty frames (it was eight: a blur of legs)
    assert bitmoves.stride_length(0.35) == 4  # strolling, as before


def test_an_arm_on_its_way_keeps_its_length_and_ends_where_it_goes():
    up, down, mug = bitart.Arm("raise", 20), bitart.Arm("hang", 8), bitart.Arm("reach", to=(66.0, 53.0))
    for side in (-1, 1):
        for a, b in ((up, down), (down, up), (up, mug), (mug, down)):
            (shoulder, elbow_a, _), (_, elbow_b, _) = bitart._arm_points(side, a), bitart._arm_points(side, b)
            assert bitart.arm_between(side, a, b, 0) == a and bitart.arm_between(side, a, b, 1) == b
            last = None
            for k in range(1, 20):
                _, elbow, hand = bitart._arm_points(side, bitart.arm_between(side, a, b, k / 20))
                upper = math.dist(shoulder, elbow)
                assert min(math.dist(shoulder, elbow_a), math.dist(shoulder, elbow_b)) - 0.01 <= upper
                assert upper <= max(math.dist(shoulder, elbow_a), math.dist(shoulder, elbow_b)) + 0.01
                if last is not None:
                    assert math.dist(last, hand) < 15, (side, a, b, k)  # on its way, step by step
                last = hand
