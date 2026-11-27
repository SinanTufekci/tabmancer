"""The Wrapped screen's words and doings, with no UI: one copy for the terminal's W screen (wrappedview.py) and
the desktop window's Wrapped dialog. The periods, the keys, the line under the card, the share menu's
choices; a period counted again (history.collect); the card saved, saved moving, copied or taken where it is
shared, each said as a Note. Drawing it moving and saving take a moment: the host runs those off its own
thread and shows what comes back."""

from __future__ import annotations

from . import card, history, share, wrapped
from .actions import Note
from .bitart import Colour
from .palette import TEXT

PERIODS = {7: "week", 30: "month", 0: "all time"}  # the keys 1 2 3, in order
SHAPES = {"wide": "wide, for posts", "story": "story, for Instagram"}  # f turns it, from one to the other
KEY_PAIRS = (("← →", "slides"), ("1 2 3", "week, month, all time"), ("p", "share"), ("f", "wide or story"),
             ("s", "save"), ("g", "save it moving"), ("c", "copy"), ("n", "names"), ("Esc", "close"))
WORDS = "w"  # the share menu's key for copying the post's words alone
SHARE_TITLE = ("Share your week", "nothing is posted until you post it")
SHARE_KEY_PAIRS = (("Enter", "take it there"), ("x b t i w", "the same, at once"), ("Esc", "cancel"))
DRAWING = Note("Drawing the moving card…", seconds=3)
COPIED = Note("Copied: paste it where you share it.", seconds=6)
NOT_COPIED = Note("This system's clipboard took no picture: s saves it as a file.", "warning", 8)
WORDS_COPIED = Note("The post's words are on the clipboard.", seconds=6)


def hex_colour(colour: Colour) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


MUTED = hex_colour(wrapped.MUTED)


def title_parts(week: wrapped.Week, accent: str) -> list[tuple[str, str]]:
    """The heading's words before the periods' pills, as (words, style)."""
    return [("Claude Code ", f"bold {TEXT}"), ("Wrapped", f"bold {accent}"), (f"   {wrapped.span(week)}   ", MUTED)]


COUNTING = ("   counting…", f"italic {MUTED}")  # after the pills, while a period counts


def about_parts(slide: str, names: bool, accent: str) -> list[tuple[str, str]]:
    """The line under the card: where the slide is among them, its name, and what the card says (or leaves off)."""
    at = wrapped.SLIDES.index(slide)
    return [("".join("●" if k == at else "·" for k in range(len(wrapped.SLIDES))) + "  ", accent),
            (f"{wrapped.SLIDE_NAMES[slide]}   ", f"bold {TEXT}"),
            ("Counts only; " + ("the top project's name is on the tools slide (n leaves it off). " if names else
                                "no project names (n puts the top one on). ") + "Nothing is posted until you post it.",
             MUTED)]


def stepped(slide: str, step: int) -> str:
    """← →: the slide before or after, round the ends."""
    at = wrapped.SLIDES.index(slide)
    return wrapped.SLIDES[(at + step) % len(wrapped.SLIDES)]


def turned(shape: str) -> str:
    return "story" if shape == "wide" else "wide"


def recount(days: int) -> wrapped.Week:
    """The period counted with the kept days (history.py), and the one before it as long; 0 is all time."""
    report = history.collect(None if days == 0 else max(14, days * 2))
    return wrapped.week(report, {}, days=None if days == 0 else days)


def share_choices() -> list[tuple[str, str, str]]:
    """The share menu's rows: (key, label, what happens), the places then the words alone."""
    return [(place.key, place.label, place.how) for place in share.PLACES] + [
        (WORDS, "The words", "copy what the post says, for anywhere else")]


def saved(canvas: card.Canvas, week: wrapped.Week, shape: str, slide: str) -> Note:
    """s: the card as a PNG in the pictures folder (card.save)."""
    try:
        path = card.save(canvas, wrapped.filename(week, shape, slide))
    except OSError as error:
        return Note(f"The card was not saved: {error}", "error", 10)
    return Note(f"Saved as {path}", seconds=10)


def saved_moving(week: wrapped.Week, accent: Colour, shape: str, slide: str, names: bool) -> Note:
    """g: the slide as a GIF, its numbers counting up (a second or two to draw)."""
    try:
        data = wrapped.moving(week, accent, shape, slide, names)
        path = card.save_data(data, wrapped.filename(week, shape, slide, "gif"))
    except OSError as error:
        return Note(f"The moving card was not saved: {error}", "error", 10)
    return Note(f"Saved as {path}: a GIF, for a post that takes one (X does).", seconds=10)


def shared(key: str, week: wrapped.Week, accent: Colour, slide: str, names: bool) -> tuple[str, Note] | None:
    """A place from the share menu: the slide saved and copied in its shape and its new post opened
    (share.share); the note's title and the note. None for a key that is no place (WORDS is the host's: it
    copies wrapped.words)."""
    place = next((p for p in share.PLACES if p.key == key), None)
    if place is None:
        return None
    try:
        done = share.share(place, week, accent, slide=slide, names=names)
    except OSError as error:
        return "", Note(f"The card was not saved: {error}", "error", 10)
    return f"Wrapped for {place.label}", Note(done.note, seconds=14)
