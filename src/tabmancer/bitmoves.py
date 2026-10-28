"""What Bit does, move by move: each action's traits for the behaviour (does it walk, sit, for
how long, which octant pose stands in for it) and, for Sixel, its pose and effects frame by frame.

An action's frames come from a Context: how far into the action Bit is (beat), and what the
behaviour knows (walking which way, how far, how high off the ground, what the eyes do, how
the lights glow, the antenna's sway). The octant Bit draws the same behaviour with its own few
poses (Trait.octant), so both drawings follow one life.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from .bitart import RED, Arm, Effect, Frame, Pose, arm_between


@dataclass(frozen=True)
class Trait:
    octant: str                  # the octant pose that stands in for it
    frames: tuple[int, int]      # how long, at random between the two
    speed: float = 0.0           # octant pixels a frame along the strip; 0: stays put
    sits: bool = False


TRAITS: dict[str, Trait] = {
    # Nothing going on.
    "idle": Trait("idle", (30, 70)),
    "look": Trait("look", (40, 40)),
    "tap": Trait("idle", (40, 70)),
    "stretch": Trait("cheer", (40, 40)),
    "coffee": Trait("idle", (70, 110)),
    "whistle": Trait("idle", (60, 100)),
    "sit": Trait("sit", (60, 140), sits=True),
    "wave": Trait("wave", (36, 36)),
    "jump": Trait("jump", (20, 20)),
    "walk": Trait("walk", (50, 130), speed=0.35),
    "skip": Trait("walk", (40, 80), speed=0.5),
    # Sessions at work.
    "hurry": Trait("walk", (40, 90), speed=0.8),
    "carry": Trait("walk", (50, 100), speed=0.3),
    "work": Trait("work", (60, 120)),       # at the laptop
    "think": Trait("work", (50, 80)),
    "idea": Trait("cheer", (30, 30)),
    "read": Trait("work", (60, 100)),
    "fix": Trait("work", (50, 90)),
    # A session needs you.
    "call": Trait("call", (120, 120)),
    # Things that happen.
    "cheer": Trait("cheer", (40, 40)),
    "dance": Trait("cheer", (60, 60)),
    "spin": Trait("dizzy", (30, 30)),
    "alarm": Trait("alarm", (50, 50), speed=0.9),
    "worried": Trait("look", (60, 60)),
    "dizzy": Trait("dizzy", (40, 40)),
    # Dozing off, and back.
    "yawn": Trait("sit", (40, 40), sits=False),
    "nod": Trait("sit", (90, 90), sits=True),
    "sleep": Trait("sleep", (10 ** 9, 10 ** 9), sits=True),
    "wake": Trait("jump", (24, 24)),
}

IDLE_CHOICES = (["walk"] * 5 + ["idle"] * 3 + ["look"] * 2 + ["sit"] * 2 + ["tap", "stretch", "coffee", "whistle",
                                                                          "skip", "wave", "jump"])
WORK_CHOICES = ["walk"] * 2 + ["hurry"] * 2 + ["work"] * 4 + ["think"] * 2 + ["read"] * 2 + ["fix", "carry", "look"]
CHEERS = ("cheer", "dance", "spin")
# Calm (b): more standing still, no hops, spins or confetti. What it does instead, and what it chooses.
CALM = {"cheer": "wave", "dance": "wave", "spin": "wave", "hurry": "walk", "skip": "walk", "jump": "idle",
        "alarm": "look", "dizzy": "look", "whistle": "idle"}
CALM_IDLE_CHOICES = ["idle"] * 4 + ["look"] * 2 + ["sit"] * 3 + ["coffee", "walk"]
CALM_WORK_CHOICES = ["work"] * 4 + ["think"] * 2 + ["read"] * 2 + ["look"]
CALLS = ("wave", "point", "bounce")  # the ways a call goes, one after the other


@dataclass
class Context:
    """What the behaviour knows this frame, for drawing it."""
    beat: int = 0                # frames since the action started
    frame: int = 0               # frames since Bit came to life
    direction: int = 1           # walking right (1) or left (-1)
    distance: float = 0.0        # walked so far, in octant pixels: drives the octant steps
    gait: float = 0.0            # strides walked (stride_length each): drives the steps in pixels
    height: float = 0.0          # off the ground, in octant pixels
    eyes: str = "normal"         # what the behaviour says the eyes do (octant names)
    antenna: float = 0.0         # its sway, degrees
    beacon: float = 0.6
    beacon_red: bool = False
    chest: float = 0.6
    working: bool = False
    call: str = "wave"           # this call's way (CALLS)
    effects: list[Effect] = field(default_factory=list)  # Z's, notes, confetti from the behaviour


OCTANT_EYES = {"normal": "open", "alarm": "wide", "sleep": "closed"}


def frame(action: str, c: Context) -> Frame:
    """Bit this frame: the action's own pose, with what the behaviour adds."""
    make = MOVES.get(action, _idle)
    f = make(c)
    pose = f.pose
    eyes = c.eyes
    if eyes not in ("normal", "") and not _keeps_eyes(action):  # a blink, a look at the pointer
        eyes = OCTANT_EYES.get(eyes, eyes)
        if eyes != "open" or pose.eyes in ("open",):
            pose = _with(pose, eyes=eyes)
    pose = _with(pose, antenna=pose.antenna + c.antenna, beacon=c.beacon if pose.beacon == 0.6 else pose.beacon,
                 chest=c.chest if pose.chest == 0.6 else pose.chest,
                 beacon_colour=RED if c.beacon_red else pose.beacon_colour,
                 lift=max(pose.lift, c.height * 5))
    return Frame(steady(pose), f.effects + c.effects)


def _step(value: float, step: float) -> float:
    return round(value / step) * step


def _steady_arm(arm: Arm) -> Arm:
    return Arm(arm.kind, _step(arm.angle, 4), (_step(arm.to[0], 1), _step(arm.to[1], 1)), arm.front,
               (_step(arm.elbow[0], 1), _step(arm.elbow[1], 1)))


def steady(pose: Pose) -> Pose:
    """The pose in steps about a pixel apart, so poses repeat and are drawn once (bitart.drawn keeps
    them): a swinging arm, a tilted head or a swaying antenna come round to the same few. A gear
    comes round to itself every tooth (45 degrees)."""
    return _with(pose, lift=_step(pose.lift, 1), squash=_step(pose.squash, 0.02), turn=_step(pose.turn, 6) % 360,
                 lean=_step(pose.lean, 2), tilt=_step(pose.tilt, 2), head_y=_step(pose.head_y, 0.5),
                 antenna=_step(pose.antenna, 4), left_foot=_step(pose.left_foot, 1),
                 right_foot=_step(pose.right_foot, 1), look=(_step(pose.look[0], 1), _step(pose.look[1], 1)),
                 beacon=_step(pose.beacon, 0.125), chest=_step(pose.chest, 0.125), left=_steady_arm(pose.left),
                 right=_steady_arm(pose.right), prop_at=(_step(pose.prop_at[0], 0.5), _step(pose.prop_at[1], 0.5)),
                 prop_turn=_step(pose.prop_turn % 45, 7.5) % 45 if pose.prop == "gear" else _step(pose.prop_turn, 1))


def _keeps_eyes(action: str) -> bool:
    """Actions whose eyes are part of the act (asleep, dizzy, at an idea) and stay so."""
    return action in ("sleep", "dizzy", "idea", "cheer", "dance", "worried", "nod", "yawn", "spin")


def _with(pose: Pose, **changes) -> Pose:
    from dataclasses import replace
    return replace(pose, **changes)


def _hang(angle: float, front: bool = False) -> Arm:
    return Arm("hang", angle, front=front)


def _raise(swing: float) -> Arm:
    return Arm("raise", swing)


def _reach(x: float, y: float, front: bool = True) -> Arm:
    return Arm("reach", to=(x, y), front=front)


def _breath(c: Context) -> float:
    return math.sin(c.frame / 14) * 0.5


def ease(t: float) -> float:
    """0 to 1, slow to start and slow to stop."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _hop(t: float, height: float) -> float:
    """Off the ground and back, t going 0 to 1: up and down in an arc, never a jump of a few pixels."""
    return height * math.sin(math.pi * t) if 0 < t < 1 else 0.0


def _arms(left: tuple[Arm, Arm, float], right: tuple[Arm, Arm, float]) -> dict[str, Arm]:
    """Both arms, each on its way from one place to another (bitart.arm_between): (from, to, how far)."""
    return {"left": arm_between(-1, *left), "right": arm_between(1, *right)}


# --- nothing going on ---------------------------------------------------------------------------

def _idle(c: Context) -> Frame:
    sway = math.sin(c.frame / 18) * 4
    return Frame(Pose(head_y=_breath(c), left=_hang(8 + sway), right=_hang(8 - sway)))


def _look(c: Context) -> Frame:
    side = -1 if c.eyes == "left" else 1
    return Frame(Pose(tilt=side * 5, head_y=_breath(c), eyes="left" if side < 0 else "right"))


def _tap(c: Context) -> Frame:
    up = (c.beat // 5) % 2 == 0
    return Frame(Pose(right_foot=2.0 if up else 0.0, left=_reach(36.5, 69.5), right=_reach(73.5, 69.5),
                      eyes="up" if (c.beat // 30) % 2 else "open", head_y=_breath(c)))


def _stretch(c: Context) -> Frame:
    k = ease(c.beat / 14) if c.beat < 26 else ease((40 - c.beat) / 14)
    swing = math.sin(c.beat / 3) * 8 * k
    up = ease((k - 0.1) / 0.8)
    return Frame(Pose(squash=1 + 0.05 * k, eyes="closed" if 6 < c.beat < 34 else "happy", tilt=math.sin(c.beat / 6) * 3 * k,
                      **_arms((_hang(40 * k + 8), _raise(-10 + swing), up), (_hang(40 * k + 8), _raise(-10 - swing), up))))


def _coffee(c: Context) -> Frame:
    beat = c.beat % 50
    sip = beat < 20  # up in six frames, a sip, down in six
    k = ease(beat / 6) if beat < 14 else ease((20 - beat) / 6)
    hand = (71 - 5 * k, 66 - 13 * k)
    effects = [Effect("steam", hand[0] + 1 + math.sin((c.frame + n * 7) / 5) * 1.5, hand[1] - 7 - n * 4,
                      size=0.6, alpha=1 - n * 0.3) for n in range(3)] if not sip and c.frame % 3 else []
    return Frame(Pose(right=_reach(*hand), prop="mug", prop_at=(hand[0] + 0.5, hand[1] - 1.5),
                      eyes="happy" if sip and k > 0.8 else "open", head_y=_breath(c), tilt=-3 * k), effects)


def _whistle(c: Context) -> Frame:
    bob = math.sin(c.beat / 5)
    notes = [Effect("note", 88 + n * 5, 30 - ((c.beat + n * 12) % 36) * 0.7, alpha=1 - ((c.beat + n * 12) % 36) / 36)
             for n in range(2)]
    return Frame(Pose(tilt=bob * 5, head_y=_breath(c), eyes="half", left=_hang(10 + bob * 6), right=_hang(10 - bob * 6)),
                 notes)


def _sit(c: Context) -> Frame:
    swing = math.sin(c.beat / 6)
    return Frame(Pose(sitting=True, left_foot=max(0.0, swing) * 2, right_foot=max(0.0, -swing) * 2,
                      left=_hang(4), right=_hang(4), head_y=_breath(c), look=(math.sin(c.beat / 40) * 2, 0)))


def _wave(c: Context) -> Frame:
    return Frame(Pose(right=_raise(math.sin(c.beat / 2.2) * 24), left=_hang(8), eyes="happy",
                      blush=(c.beat // 18) % 2 == 0, tilt=math.sin(c.beat / 4.4) * 4))


def _jump(c: Context) -> Frame:
    up = c.height > 0.3
    dust = [Effect("dust", 55 + side * (12 + c.beat % 6), 76, size=0.8, alpha=0.8) for side in (-1, 1)] \
        if not up and 2 < c.beat < 8 else []
    k = min(ease((c.beat + 1) / 6), ease((15 - c.beat) / 8))  # the arms up as it takes off, down after it lands
    return Frame(Pose(squash=1.05 if up else (0.92 if c.beat < 3 else 1.0), tucked=up, eyes="happy",
                      **_arms((_hang(24), _raise(10), k), (_hang(24), _raise(10), k))), dust)


STRIDE_STEPS = 24  # a stride's poses: a walk comes round to the same ones


def stride_length(speed: float) -> float:
    """Octant pixels walked a stride (two steps): four strolling; hurrying, longer, so that a stride
    takes ten frames at least and the legs and arms swing, not flicker."""
    return max(4.0, speed * 10)


def stride(c: Context) -> float:
    """Where in its stride Bit is, 0 to 1, in STRIDE_STEPS steps."""
    return round(c.gait * STRIDE_STEPS) % STRIDE_STEPS / STRIDE_STEPS


def _walk(c: Context, speed: float = 1.0) -> Frame:
    t = stride(c)
    s = math.sin(2 * math.pi * t)
    look = (2.0 * c.direction, 0.0)
    return Frame(Pose(left_foot=max(0.0, s) * 3.2, right_foot=max(0.0, -s) * 3.2, head_y=-abs(s) * 1.0,
                      left=_hang(10 + s * 20 * speed), right=_hang(10 - s * 20 * speed), look=look,
                      antenna=-c.direction * 6 * speed))


def _skip(c: Context) -> Frame:
    t = stride(c)
    hop = abs(math.sin(math.pi * t))
    f = _walk(c, 1.3)
    return Frame(_with(f.pose, lift=hop * 4, eyes="happy", left=_raise(math.sin(2 * math.pi * t) * 15),
                       right=_hang(30)))


def _hurry(c: Context) -> Frame:
    f = _walk(c, 1.5)
    dust = [Effect("dust", 55 - c.direction * 16, 76, size=0.7, alpha=0.7)] if int(c.distance * 2) % 6 == 0 else []
    return Frame(_with(f.pose, lean=c.direction * 7), dust)


def _carry(c: Context) -> Frame:
    f = _walk(c, 0.4)
    bob = abs(math.sin(math.pi * stride(c))) * 1.0
    box = (55.0, 66.0 - bob)
    return Frame(_with(f.pose, prop="box", prop_at=box, left=_reach(43, 66 - bob), right=_reach(67, 66 - bob)))


# --- at work ------------------------------------------------------------------------------------

def _work(c: Context) -> Frame:
    t = c.beat % 4
    glance = (c.beat // 40) % 3 == 2 and (c.beat % 40) < 8
    return Frame(Pose(prop="laptop", eyes="open" if glance else "down",
                      left=_reach(47, 74 - (1.5 if t < 2 else 0)), right=_reach(63, 74 - (0 if t < 2 else 1.5)),
                      head_y=0.6 if t in (1, 2) else 0.0, chest=0.4 + 0.6 * abs(math.sin(c.beat / 1.5))))


def _think(c: Context) -> Frame:
    prop = "gear" if (c.beat // 25) % 2 == 0 else "dots"
    return Frame(Pose(right=_reach(63, 57), left=_reach(48, 67, front=True), eyes="up", tilt=-4 + math.sin(c.beat / 12) * 2,
                      prop=prop, prop_at=(80, 14), prop_turn=c.beat * 7.5 if prop == "gear" else c.beat / 6,
                      antenna=math.sin(c.beat / 10) * 6))


def _idea(c: Context) -> Frame:
    pop = min(1.0, c.beat / 4)
    return Frame(Pose(lift=_hop((c.beat - 2) / 7, 4), left=_raise(-5), right=_raise(15), eyes="star", blush=True,
                      prop="bulb" if pop > 0.5 else None, prop_at=(80, 12), antenna=8 if c.beat < 6 else 0),
                 [Effect("spark", 80 + dx, 12 + dy, size=1) for dx, dy in ((-9, -4), (9, -4), (0, -11), (-7, 6), (7, 6))
                  if c.beat % 6 < 3])


def _read(c: Context) -> Frame:
    line = (c.beat % 30) / 30
    return Frame(Pose(prop="clipboard", prop_at=(55, 64), left=_reach(48, 68), right=_reach(62, 68),
                      look=(-2 + line * 4, 1.5 + (c.beat // 30 % 3) * 0.8), tilt=math.sin(c.beat / 15) * 2,
                      eyes="half" if (c.beat // 30) % 4 == 3 else "open"))


def _fix(c: Context) -> Frame:
    t = c.beat % 16
    swing = -40 + t * 12 if t < 8 else 56 - (t - 8) * 12
    strike = t == 8
    sparks = [Effect("spark", 80 + dx, 70 + dy, size=1) for dx, dy in ((-3, -3), (3, -4), (0, -6), (4, 0))] if t in (8, 9) else []
    return Frame(Pose(right=_reach(73, 62), prop="wrench", prop_at=(73, 62), prop_turn=swing, eyes="focused",
                      left=_hang(14), squash=0.97 if strike else 1.0), sparks)


# --- a session needs you ------------------------------------------------------------------------

def _call(c: Context) -> Frame:
    beat = c.beat % 120
    if beat >= 48:  # between calls: stand and look this way
        return Frame(Pose(eyes="wide" if beat % 30 < 4 else "open", head_y=_breath(c)))
    # The arms go up as a call starts and come down before it ends, not in a frame.
    up = min(ease(beat / 8), ease((47 - beat) / 10))
    rest = Pose()
    if c.call == "point":  # points up at the list
        return Frame(Pose(eyes="wide", tilt=-6 * up, lift=_hop((beat % 12) / 5, 2) * up,
                          **_arms((rest.left, _hang(10), up), (rest.right, _raise(-35 + math.sin(beat / 3) * 6), up))),
                     [Effect("bang", 82, 6, size=1)] if beat % 10 < 6 else [])
    if c.call == "bounce":  # both arms, jumping
        hop = _hop((beat % 10) / 7, 4) * up
        return Frame(Pose(lift=hop, tucked=hop > 2, eyes="wide",
                          **_arms((rest.left, _raise(math.sin(beat / 2) * 20), up),
                                  (rest.right, _raise(-math.sin(beat / 2) * 20), up))))
    return Frame(Pose(eyes="wide", lift=_hop((beat % 10) / 5, 2.5) * up,
                      **_arms((rest.left, _hang(8), up), (rest.right, _raise(math.sin(beat / 1.8) * 28), up))))


# --- things that happen -------------------------------------------------------------------------

def _cheer(c: Context) -> Frame:
    up = c.height > 0.3
    return Frame(Pose(left=_raise(math.sin(c.beat / 2) * 18), right=_raise(-math.sin(c.beat / 2) * 18),
                      eyes="star" if (c.beat // 8) % 2 == 0 else "happy", blush=True, tucked=up,
                      squash=1.05 if up else 1.0))


def _dance(c: Context) -> Frame:
    """Swaying side to side, a beat of 24 frames: leaning and tilting over, the arm on that side up
    and a foot off the ground, swung there and back, not snapped."""
    sway = -math.cos(c.beat / 24 * 2 * math.pi)  # -1 over to the left, 1 to the right
    side = 1 if sway > 0 else -1
    left, right = ease(0.5 - sway * 0.5), ease(0.5 + sway * 0.5)  # how far up each arm is
    return Frame(Pose(lean=sway * 8, tilt=sway * 6, left_foot=3 * max(0.0, -sway) ** 2, right_foot=3 * max(0.0, sway) ** 2,
                      eyes="heart", blush=True, **_arms((_hang(40), _raise(20), left), (_hang(40), _raise(-20), right))),
                 [Effect("note", 88 if side > 0 else 18, 26 - (c.beat % 12), alpha=1 - (c.beat % 12) / 12)])


def _spin(c: Context) -> Frame:
    """Round once on the spot and facing you again: slow to start and to stop, off the ground a
    little on the way, the arms out. Its head and body go round, its back shows, then its face."""
    t = c.beat / TRAITS["spin"].frames[0]
    return Frame(Pose(turn=360 * ease(t), lift=_hop(t, 3), eyes="happy", left=_hang(70), right=_hang(70)))


def _alarm(c: Context) -> Frame:
    f = _walk(c, 1.6)
    return Frame(_with(f.pose, eyes="wide", left=_raise(math.sin(c.beat) * 20), right=_raise(-math.sin(c.beat) * 20),
                       lean=c.direction * 6), [Effect("bang", 80, 4, size=1)] if c.beat % 8 < 5 else [])


def _worried(c: Context) -> Frame:
    drip = (c.beat % 30) / 30
    return Frame(Pose(eyes="worried", tilt=math.sin(c.beat / 2) * 1.2, left=_reach(43, 66), right=_reach(67, 66),
                      head_y=_breath(c)),
                 [Effect("sweat", 86, 24 + drip * 16, alpha=1 - drip * 0.5)])


def _dizzy(c: Context) -> Frame:
    a = c.beat / 3
    stars = [Effect("star", 55 + math.cos(a + k * 2.1) * 20 - 2, 9 + math.sin(a + k * 2.1) * 4, size=1) for k in range(3)]
    return Frame(Pose(eyes="dizzy", tilt=math.sin(c.beat / 2.5) * 8, lean=math.sin(c.beat / 3) * 5,
                      left=_hang(30 + math.sin(c.beat) * 15), right=_hang(30 - math.sin(c.beat) * 15),
                      antenna=math.sin(c.beat / 1.5) * 20), stars)


# --- dozing off, and back -------------------------------------------------------------------------

def _yawn(c: Context) -> Frame:
    k = math.sin(min(1.0, c.beat / 40) * math.pi)
    up = ease((k - 0.15) / 0.7)
    return Frame(Pose(eyes="closed" if k > 0.3 else "half", tilt=-4 * k, squash=1 + 0.04 * k, head_y=-1 * k,
                      **_arms((_hang(20 + 30 * k), _raise(-20), up), (_hang(20 + 30 * k), _raise(20), up))))


def _nod(c: Context) -> Frame:
    beat = c.beat % 45
    drop = min(1.0, beat / 30)
    startled = 30 <= beat < 36
    return Frame(Pose(sitting=True, head_y=0 if startled else drop * 3, tilt=0 if startled else -drop * 6,
                      eyes="wide" if startled else "half" if drop < 0.6 else "closed",
                      left=_hang(4), right=_hang(4), antenna=8 if startled else 0, beacon=0.3))


def _sleep(c: Context) -> Frame:
    breath = math.sin(c.frame / 8)
    zs = []
    for k in range(3):
        age = ((c.frame + k * 14) % 42) / 42
        zs.append(Effect("z", 78 + age * 16, 22 - age * 20, size=1 + (age > 0.5), alpha=1 - age))
    return Frame(Pose(sitting=True, head_y=1 + breath * 0.7, tilt=-5, eyes="closed", left=_hang(4), right=_hang(4),
                      beacon=0.12 + 0.08 * (breath + 1), chest=0.2 + 0.15 * (breath + 1)), zs)


def _wake(c: Context) -> Frame:
    first = c.beat < 10
    down = ease((c.beat - 8) / 10)  # the arms, up with the start, come down again
    return Frame(Pose(eyes="wide" if first else "blink" if c.beat in (14, 15) else "open",
                      lift=_hop((c.beat - 1) / 6, 5), antenna=math.sin(c.beat / 1.4) * max(0, 20 - c.beat),
                      **_arms((_raise(20), _hang(10), down), (_raise(-20), _hang(10), down))),
                 [Effect("bang", 80, 4, size=1)] if first else [])


MOVES: dict[str, Callable[[Context], Frame]] = {
    "idle": _idle, "look": _look, "tap": _tap, "stretch": _stretch, "coffee": _coffee, "whistle": _whistle,
    "sit": _sit, "wave": _wave, "jump": _jump, "walk": _walk, "skip": _skip, "hurry": _hurry, "carry": _carry,
    "work": _work, "think": _think, "idea": _idea, "read": _read, "fix": _fix, "call": _call, "cheer": _cheer,
    "dance": _dance, "spin": _spin, "alarm": _alarm, "worried": _worried, "dizzy": _dizzy, "yawn": _yawn,
    "nod": _nod, "sleep": _sleep, "wake": _wake,
}
