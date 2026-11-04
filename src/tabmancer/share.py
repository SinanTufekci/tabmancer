"""Taking the Wrapped card to where people share it: X, Bluesky, Threads and Instagram.

No platform takes a picture through a link, so each place gets what can be done from here: the
card in the shape it wants is saved (in Downloads) and put on the clipboard, to paste into the
post, and the platform's own page for a new post opens with the words already in it
(wrapped.words). Nothing is posted until you post it.

Instagram has no such page, and stories are mostly posted from the phone: there the story card is
saved and copied, its folder opened (to send it to the phone, or to drag it in) and instagram.com
opened. LinkedIn is left out: it has no documented way to start a post with words in it.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from . import card, wrapped
from .bitart import Colour


@dataclass(frozen=True)
class Place:
    key: str    # the key that picks it in the share menu
    label: str
    shape: str  # wrapped.SHAPES
    page: str   # its page for a new post; {text} takes the words, encoded
    how: str    # what happens, said in the menu


PLACES = (
    Place("x", "X", "wide", "https://x.com/intent/post?{text}",
          "a new post with the words in it; paste the card in"),
    Place("b", "Bluesky", "wide", "https://bsky.app/intent/compose?{text}",
          "a new post with the words in it; paste the card in"),
    Place("t", "Threads", "wide", "https://www.threads.com/intent/post?{text}",
          "a new post with the words in it; paste the card in"),
    Place("i", "Instagram", "story", "https://www.instagram.com/",
          "the story card, saved and copied, for your phone or instagram.com"),
)
PASTE = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"


def page(place: Place, words: str) -> str:
    return place.page.format(text=urlencode({"text": words})) if "{text}" in place.page else place.page


@dataclass
class Shared:
    path: Path
    copied: bool
    page: str
    note: str  # what was done and what is left to you, in a line or two


def share(place: Place, week: wrapped.Week, accent: Colour, folder: Path | None = None) -> Shared:
    """The card saved and copied in the place's shape, and the place's new post opened. Raises
    OSError when the card cannot be saved."""
    canvas = wrapped.draw(week, accent, shape=place.shape)
    path = card.save(canvas, wrapped.filename(week, place.shape), folder)
    copied = card.copy_picture(canvas)
    address = page(place, wrapped.words(week))
    open_page(address)
    if place.shape == "story":
        reveal(path)
        note = (f"Instagram has no link for a new post: the story card is saved as {path.name} and its folder is "
                "open. Send it to your phone and add it to your story, or post it on instagram.com.")
    elif copied:
        note = f"{place.label} is open with the words. The card is on the clipboard: {PASTE} pastes it into the post."
    else:
        note = f"{place.label} is open with the words. Add the card to the post: it is saved as {path}."
    return Shared(path, copied, address, note)


def open_page(address: str) -> None:
    webbrowser.open(address)


def reveal(path: Path) -> None:
    """The file's folder, with the file picked where the system can."""
    try:
        if sys.platform == "win32":
            # As one line: given as a list, a path with a space had the switch quoted with it
            # ("/select,C:\Users\John Smith\..."), which Explorer does not take for a switch.
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        pass  # the note names the file all the same
