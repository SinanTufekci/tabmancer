"""The README's Claude Code Wrapped cards, from the demo's made-up week: docs/wrapped.png (wide) and
docs/wrapped-story.png (story). Nothing of yours is read: the demo writes its own home in a temp
folder, and four of its sessions are noted as brought back, as the dashboard notes them.

usage: uv run python scripts/wrapped_cards.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from tabmancer import demo, tokens, wrapped
from tabmancer.sessions import find_sessions

DOCS = Path(__file__).resolve().parent.parent / "docs"
ACCENT = (217, 119, 87)  # the default theme's orange


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="tabmancer-cards-"))
    try:
        demo.prepare(home)
        listed = find_sessions(14, include_all=True, quick=True)
        wrapped.note_opened([(s.session_id, "auto" if n % 2 else "resume") for n, s in enumerate(listed[:4])])
        week = wrapped.week(tokens.collect(14), {s.session_id: s for s in listed})
        for shape, name in (("wide", "wrapped.png"), ("story", "wrapped-story.png")):
            (DOCS / name).write_bytes(wrapped.draw(week, ACCENT, shape=shape).png())
            print(f"docs/{name}")
        print(wrapped.words(week))
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
