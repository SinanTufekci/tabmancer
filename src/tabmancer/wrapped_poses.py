"""Bit as each persona of Claude Code Wrapped (wrapped.persona): a pose, and what is round it."""

from __future__ import annotations

from . import bitart
from .bitart import Arm, Effect, Pose

C = bitart.CONFETTI


def _confetti() -> list[Effect]:
    return [Effect("confetti", cx, cy, 2.0, 1.0, colour) for cx, cy, colour in
            ((10, 16, C[0]), (98, 8, C[1]), (20, 42, C[2]), (102, 38, C[3]), (4, 62, C[4]), (104, 64, C[0]),
             (30, 4, C[3]), (84, 26, C[2]))]


# persona name -> (pose, effects). The default (the comeback kid's) is Bit glad of the week, in confetti.
POSES: dict[str, tuple[Pose, list[Effect]]] = {
    "THE CONDUCTOR": (Pose(right=Arm("raise", 32.0), left=Arm("raise", 32.0), eyes="focused", beacon=0.95, chest=0.9,
                           antenna=-8.0),
                      [Effect("note", 12, 14, 1.0), Effect("note", 92, 6, 1.0), Effect("note", 100, 40, 1.0),
                       Effect("note", 6, 46, 1.0)]),
    "THE NIGHT OWL": (Pose(right=Arm("reach", to=(66, 56), front=True), left=Arm("hang", 10.0), eyes="half",
                           prop="mug", prop_at=(66.5, 54.5), beacon=0.5, chest=0.5, tilt=-6.0),
                      [Effect("star", 10, 10, 1.0), Effect("star", 96, 20, 1.0), Effect("z", 88, 2, 1.0)]),
    "THE EARLY BIRD": (Pose(right=Arm("reach", to=(71, 66), front=True), left=Arm("raise", 20.0), eyes="happy",
                            prop="mug", prop_at=(71.5, 64.5), beacon=0.9, chest=0.8),
                       [Effect("steam", 72, 56, 0.7, 1.0), Effect("steam", 73, 51, 0.6, 0.7),
                        Effect("note", 14, 12, 1.0)]),
    "THE WEEKEND WARRIOR": (Pose(right=Arm("raise", 34.0), left=Arm("raise", 34.0), eyes="star", lift=4.0,
                                 beacon=1.0, chest=1.0), _confetti()),
    "THE MARATHONER": (Pose(right=Arm("raise", 8.0), left=Arm("hang", 26.0), eyes="focused", lean=10.0,
                            right_foot=5.0, beacon=0.95, chest=0.9),
                       [Effect("sweat", 96, 12, 1.0), Effect("dust", 20, 92, 1.2, 0.9), Effect("dust", 10, 88, 0.9, 0.8)]),
    "THE STEADY HAND": (Pose(left=Arm("reach", to=(48, 68), front=True), right=Arm("reach", to=(62, 68), front=True),
                             eyes="happy", prop="clipboard", prop_at=(55, 64), beacon=0.85, chest=0.8),
                        [Effect("star", 98, 14, 1.0)]),
    "THE TOOLSMITH": (Pose(right=Arm("reach", to=(73, 62), front=True), left=Arm("hang", 14.0), eyes="focused",
                           prop="wrench", prop_at=(73, 62), prop_turn=30.0, beacon=0.9, chest=0.85),
                      [Effect("spark", 86, 46, 1.0), Effect("spark", 92, 42, 1.0), Effect("spark", 88, 38, 1.0)]),
    "THE BUILDER": (Pose(left=Arm("reach", to=(43, 66), front=True), right=Arm("reach", to=(67, 66), front=True),
                         eyes="happy", prop="box", prop_at=(55, 66), beacon=0.85, chest=0.8),
                    [Effect("spark", 96, 20, 1.2)]),
    "THE WELL RESTED": (Pose(sitting=True, eyes="closed", beacon=0.3, chest=0.3, tilt=8.0),
                        [Effect("z", 82, 14, 1.0), Effect("z", 94, 2, 1.4)]),
}
GLAD = (Pose(right=Arm("raise", 20.0), left=Arm("hang", 14.0), eyes="happy", blush=True, beacon=0.95, chest=0.85),
        _confetti())


def pose(persona: str, accent: bitart.Colour) -> bitart.Frame:
    """Bit as the persona, in the theme's colour."""
    found, effects = POSES.get(persona, GLAD)
    return bitart.Frame(bitart.themed(found, accent), effects)
