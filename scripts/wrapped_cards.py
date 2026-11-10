"""The README's Claude Code Wrapped pictures, from the demo's made-up week: docs/wrapped.png (the
wide card that sums it up), docs/wrapped-story.png (the same as a story), docs/wrapped-slides.png
(every slide, wide, on one sheet) and docs/wrapped-moving.gif (the tokens slide counting up, as g
saves it). Nothing of yours is read: the demo writes its own home in a temp folder, and four of its
sessions are noted as brought back, as the dashboard notes them.

usage: uv run --with pillow python scripts/wrapped_cards.py
"""

from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from tabmancer import demo, history, wrapped
from tabmancer.sessions import find_sessions

DOCS = Path(__file__).resolve().parent.parent / "docs"
ACCENT = (217, 119, 87)  # the default theme's orange
GAP = 16  # between the slides on the sheet, in the card's night colour


def sheet(week: wrapped.Week) -> Image.Image:
    """The six slides, wide, three a row, at two real pixels a design pixel."""
    slides = [Image.open(io.BytesIO(wrapped.draw(week, ACCENT, 2, "wide", slide).png())).convert("RGB")
              for slide in wrapped.SLIDES]
    w, h = slides[0].size
    out = Image.new("RGB", (3 * w + 2 * GAP, 2 * h + GAP), wrapped.GROUND)
    for k, slide in enumerate(slides):
        out.paste(slide, ((k % 3) * (w + GAP), (k // 3) * (h + GAP)))
    return out.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="tabmancer-cards-"))
    try:
        demo.prepare(home)
        listed = find_sessions(14, include_all=True, quick=True)
        wrapped.note_opened([(s.session_id, "auto" if n % 2 else "resume") for n, s in enumerate(listed[:4])])
        week = wrapped.week(history.collect(14), {s.session_id: s for s in listed})
        for shape, name in (("wide", "wrapped.png"), ("story", "wrapped-story.png")):
            (DOCS / name).write_bytes(wrapped.draw(week, ACCENT, shape=shape).png())
            print(f"docs/{name}")
        sheet(week).save(DOCS / "wrapped-slides.png", optimize=True)
        print("docs/wrapped-slides.png")
        (DOCS / "wrapped-moving.gif").write_bytes(wrapped.moving(week, ACCENT, "wide", "tokens"))
        print("docs/wrapped-moving.gif")
        print(wrapped.words(week))
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
