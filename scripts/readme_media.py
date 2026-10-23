"""The README's pictures from scripts/window_shots.py's captures: a PNG of each scene the README
shows, and docs/demo.webp, the scenes in turn with what each key did written beneath them.

usage: uv run --with pillow python scripts/readme_media.py <captures dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DOCS = Path(__file__).resolve().parent.parent / "docs"
SHOTS = Path(sys.argv[1])
ACCENT = (217, 119, 87)
STRIP = 56  # under the window: what the scene shows, in words
FONT = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 24)
STILLS = {  # scene -> the README's picture
    0: "picker", 2: "team-rows", 4: "team", 5: "live",
    6: "stats-tokens", 7: "stats-work", 8: "stats-limits", 9: "stats-report",
}
# (scene, what the caption says) for the moving picture, in order
MOVIE = [
    (0, "a session cut off by a reboot  ·  Enter calls it back"),
    (1, "2  ·  only the sessions waiting for you"),
    (2, "a team on one row  ·  its members as chips"),
    (3, "e  ·  the team's members, each on its wire"),
    (4, "T  ·  who said what to whom"),
    (5, "l  ·  the live view: what each one is doing"),
    (10, "?  ·  every key, and Enter runs it"),
]
FRAME_MS, HOLD_MS = 120, 1700  # a scene's quick frames, then its last held


def flat(image: Image.Image) -> Image.Image:
    """256 colours: a terminal has few, and the file gets several times smaller."""
    return image.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def captioned(frame: Image.Image, words: str) -> Image.Image:
    out = Image.new("RGB", (frame.width, frame.height + STRIP), (11, 11, 12))
    out.paste(frame, (0, 0))
    draw = ImageDraw.Draw(out)
    width = draw.textlength(words, font=FONT)
    draw.text(((frame.width - width) / 2, frame.height + (STRIP - 24) / 2 - 2), words, font=FONT, fill=ACCENT)
    return out


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    for scene, name in STILLS.items():
        source = next(SHOTS.glob(f"{scene:02d}-*.png"))
        flat(Image.open(source)).save(DOCS / f"{name}.png", optimize=True)
        print(f"{name}.png  {(DOCS / f'{name}.png').stat().st_size // 1024} KB")
    frames, durations = [], []
    for scene, words in MOVIE:
        quick = sorted((SHOTS / "frames").glob(f"{scene:02d}-*.png"))
        for index, path in enumerate(quick):
            frames.append(captioned(Image.open(path).convert("RGB"), words))
            durations.append(HOLD_MS if index == len(quick) - 1 else FRAME_MS)
    out = DOCS / "demo.webp"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=82, method=6)
    print(f"demo.webp  {out.stat().st_size // 1024} KB, {len(frames)} frames, {sum(durations) / 1000:.1f} s")


if __name__ == "__main__":
    main()
