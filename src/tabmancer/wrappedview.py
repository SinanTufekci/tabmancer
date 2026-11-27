"""W: Claude Code Wrapped on the screen. The slides in real pixels where the terminal draws them,
as large as fit in whole units; elsewhere the numbers in words. ← → go from slide to slide (who
you were, the tokens, the time, the sessions, the tools, then the card that sums it up), 1 2 3 take
the week, the month or all of the time kept, f turns it between the wide shape and the story one,
p shares the slide shown (X, Bluesky, Threads, Instagram: share.py), s saves it as a PNG, g as a
GIF with its numbers counting up, c copies it, n puts the top project's name on it (left off
otherwise: wrapped.py draws them all). The words and the doings are wrappedwords.py's, one copy with the
desktop window's Wrapped dialog."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from . import card, share, sixel, wrapped, wrappedwords
from .actions import Note
from .dialogs import frame, pills, title
from .safework import work
from .theme import MUTED, TEXT, keys
from .tokenchart import PixelPicture
from .wrappedwords import WORDS
from .wrappedwords import hex_colour as _hex

_SHIFTED = bytes((n + 1) % 256 for n in range(256))  # the card's palette after a None for "nothing drawn"


def _rgb(colour: str) -> tuple[int, int, int]:
    found = Color.parse(colour)
    return found.r, found.g, found.b


class CardPicture(PixelPicture, Static):
    """The week's card: a picture in whole units, centred, where pixels are drawn and it fits."""

    DEFAULT_CSS = "CardPicture { width: 1fr; height: 1fr; }"

    def __init__(self, week: wrapped.Week, accent: str, shape: str = "wide", slide: str = "summary",
                 names: bool = False) -> None:
        super().__init__("")
        self.week, self.accent, self.shape, self.slide, self.names = week, accent, shape, slide, names

    def on_mount(self) -> None:
        if self.start_pixels():
            self.refresh()

    def turn(self, shape: str) -> None:
        self.shape = shape
        self.refresh()
        self.draw_picture()

    def show(self, week: wrapped.Week, shape: str, slide: str, names: bool) -> None:
        self.week, self.shape, self.slide, self.names = week, shape, slide, names
        self.refresh()
        self.draw_picture()  # the picture is only drawn again when asked: a new slide, shape, period or names

    def draw_picture(self) -> None:
        """The picture, unless the card is larger than the room: then the card in words (render) is
        what shows, and no picture, clipped, goes over it."""
        if self.picture is not None and self.unit() == 0:
            if self.picture.data:
                self.picture.data, self.drawn = "", None
                self.refresh()  # its words written over a Sixel image; a kitty one is taken off by the host
            return
        super().draw_picture()

    def unit(self) -> int:
        """Real pixels a design pixel, the most the card fits in at: 0 when it does not."""
        found, cells = sixel.terminal(), self.cells()
        if found is None or cells is None:
            return 0
        wide, high = wrapped.SHAPES[self.shape]
        width, height = cells[2] * found.cell[0], cells[3] * found.cell[1] // 6 * 6
        return min(width // wide, height // high, wrapped.UNIT)

    def picture_key(self) -> tuple:
        return repr(self.week), self.accent, self.shape, self.slide, self.names, self.unit()

    def picture_rows(self, width: int, height: int):
        drawn = wrapped.draw(self.week, _rgb(self.accent), max(1, self.unit()), self.shape, self.slide,
                             names=self.names)
        left, top = max(0, (width - drawn.width) // 2), max(0, (height - drawn.height) // 2)
        right = max(0, width - left - drawn.width)
        blank = bytes(width)
        rows = [blank] * top + [bytes(left) + bytes(row).translate(_SHIFTED)[:width - left] + bytes(right)
                                for row in drawn.rows[:height - top]]
        rows += [blank] * (height - len(rows))
        return [None, *drawn.palette], rows

    def render(self):
        if self.picture is not None and self.unit() > 0:
            return Text("")  # the picture goes over these cells
        cramped = self.picture is not None  # pictures are drawn here, but this one is larger than the room
        return in_words(self.week, self.accent, cramped)


SIGNS = {"TOKENS": "◆", "AT WORK": "◷", "SESSIONS": "▣", "SIDE BY SIDE": "⧉", "CLAUDE WROTE": "≡",
         "FILES EDITED": "▤", "MODELS": "#", "BROUGHT BACK": "↺", "STREAK": "▲", "BUSIEST DAY": "▦",
         "LONGEST SESSION": "⧗", "TOP TOOL": "⚒"}  # the card's icons, in characters


def in_words(week: wrapped.Week, accent: str, cramped: bool = False) -> Text:
    """The card's numbers where no picture of it is shown, in the card's colours: a line a number,
    its sign and its name in a colour of its own."""
    name, why = wrapped.persona(week)
    muted = _hex(wrapped.MUTED)
    out = Text.assemble(("CLAUDE CODE ", f"bold {TEXT}"), ("WRAPPED", f"bold {accent}"),
                        (f"   {wrapped.span(week)}\n\n", muted),
                        ("I'm ", f"bold {TEXT}"), (name.title(), f"bold {accent}"), (f"   {why.lower()}\n\n", muted))
    colours = [accent, *(_hex(hue) for hue in wrapped.HUES)]
    for n, (what, number, note) in enumerate(wrapped.tiles(week)):
        colour = colours[n % len(colours)]
        out.append(f" {SIGNS.get(what, '·')}  ", style=colour)
        out.append(f"{number.lower() if what in ('TOP TOOL', 'MODELS') else number:<14}", style=f"bold {TEXT}")
        out.append(f"{what.lower():<17}", style=f"bold {colour}")
        out.append(f"{note.lower()}\n", style=_hex(wrapped.GREEN) if note.startswith("+") else muted)
    out.append("\nThe card is larger than this window: a larger one shows it; s saves it either way." if cramped else
               "\nWhere the terminal draws pictures this is a card; s saves it as one either way.", style=muted)
    return out


NIGHT = _hex(wrapped.GROUND)  # the window is the card's sky, so the card sits in it without an edge
FRAME = _hex(wrapped.VIOLET)


class WrappedScreen(ModalScreen[None]):
    """The week's card, to share, save or copy."""

    DEFAULT_CSS = frame("WrappedScreen", "large", height="92%", tone=FRAME) + f"""
    WrappedScreen > Vertical {{ background: {NIGHT}; width: 95%; max-width: 150; }}
    WrappedScreen #wrapped-top {{ height: 1; }}
    WrappedScreen #wrapped-title {{ width: 1fr; }}
    WrappedScreen #wrapped-shape {{ width: auto; }}
    WrappedScreen CardPicture {{ margin-top: 1; }}
    WrappedScreen #wrapped-about {{ height: 1; margin-top: 1; color: {_hex(wrapped.MUTED)}; }}
    WrappedScreen .hint {{ color: {_hex(wrapped.MUTED)}; }}
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q,W", "close", "Close"),
        Binding("left", "slide(-1)", "Previous slide", priority=True),
        Binding("right", "slide(1)", "Next slide", priority=True),
        Binding("1", "period(7)", "The week"),
        Binding("2", "period(30)", "The month"),
        Binding("3", "period(0)", "All time"),
        Binding("p", "share", "Share"),
        Binding("f", "turn", "Wide or story"),
        Binding("s", "save", "Save"),
        Binding("g", "save_moving", "Save it moving"),
        Binding("c", "copy", "Copy"),
        Binding("n", "names", "Project names"),
    ]
    PERIODS: ClassVar[dict[int, str]] = wrappedwords.PERIODS

    def __init__(self, week: wrapped.Week, accent: str) -> None:
        super().__init__()
        self.week, self.accent = week, accent
        self.shape = "wide"
        self.slide = wrapped.SLIDES[0]
        self.period = 0 if week.whole else week.days
        self.names = False  # the top project's name on the tools slide
        self.counting = False

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="wrapped-top"):
                yield Label(self.heading(), id="wrapped-title")
                yield Static(self.shapes(), id="wrapped-shape")
            yield CardPicture(self.week, self.accent, self.shape, self.slide, self.names)
            yield Label(self.about(), id="wrapped-about")
            yield Label(keys(*wrappedwords.KEY_PAIRS), classes="hint")

    def heading(self) -> Text:
        out = Text.assemble(*wrappedwords.title_parts(self.week, self.accent))
        out.append_text(pills(list(self.PERIODS.values()), self.PERIODS.get(self.period, "week")))
        if self.counting:
            out.append(*wrappedwords.COUNTING)
        return out

    def show(self) -> None:
        """The slide, the period, the shape and the names as they are now."""
        self.query_one(CardPicture).show(self.week, self.shape, self.slide, self.names)
        self.query_one("#wrapped-title", Label).update(self.heading())
        self.query_one("#wrapped-shape", Static).update(self.shapes())
        self.query_one("#wrapped-about", Label).update(self.about())

    def action_slide(self, step: int) -> None:
        self.slide = wrappedwords.stepped(self.slide, step)
        self.show()

    def action_names(self) -> None:
        self.names = not self.names
        self.show()

    def action_period(self, days: int) -> None:
        if days == self.period or self.counting:
            return
        self.counting = True
        self.query_one("#wrapped-title", Label).update(self.heading())
        self.recount(days)

    @work(thread=True, exclusive=True)
    def recount(self, days: int) -> None:
        """The period counted with the kept days (history.py), and the one before it as long."""
        self.app.call_from_thread(self.recounted, days, wrappedwords.recount(days))

    def recounted(self, days: int, week: wrapped.Week) -> None:
        self.period, self.week, self.counting = days, week, False
        self.show()

    def shapes(self) -> Text:
        """The two cards, the one shown marked, and the key that turns it."""
        return Text.assemble(pills(list(wrappedwords.SHAPES), self.shape, labels=wrappedwords.SHAPES),
                             ("   f", wrappedwords.MUTED))

    def about(self) -> Text:
        out = Text(style=wrappedwords.MUTED)
        for words, style in wrappedwords.about_parts(self.slide, self.names, self.accent):
            out.append(words, style=style)
        return out

    def canvas(self) -> card.Canvas:
        return wrapped.draw(self.week, _rgb(self.accent), shape=self.shape, slide=self.slide, names=self.names)

    def action_turn(self) -> None:
        self.shape = wrappedwords.turned(self.shape)
        self.show()

    @work(thread=True, exclusive=True, group="moving")
    def action_save_moving(self) -> None:
        """g: the slide as a GIF, its numbers counting up (a second or two to draw)."""
        week, shape, slide, names = self.week, self.shape, self.slide, self.names
        self.app.call_from_thread(self.say, wrappedwords.DRAWING)
        self.app.call_from_thread(self.say, wrappedwords.saved_moving(week, _rgb(self.accent), shape, slide, names))

    def say(self, note: Note, title: str = "") -> None:
        self.notify(note.words, title=title, severity=note.severity, timeout=note.seconds)

    def action_share(self) -> None:
        def chosen(key: str | None) -> None:
            if key == WORDS:
                from .statsview import copy_text

                copy_text(self.app, wrapped.words(self.week))
                self.say(wrappedwords.WORDS_COPIED)
                return
            if key is None:
                return
            done = wrappedwords.shared(key, self.week, _rgb(self.accent), self.slide, self.names)
            if done is not None:
                self.say(done[1], title=done[0])

        self.app.push_screen(ShareMenu(self.week), chosen)

    def action_save(self) -> None:
        self.say(wrappedwords.saved(self.canvas(), self.week, self.shape, self.slide))

    def action_copy(self) -> None:
        self.say(wrappedwords.COPIED if card.copy_picture(self.canvas()) else wrappedwords.NOT_COPIED)

    def action_close(self) -> None:
        self.dismiss(None)


class ShareMenu(ModalScreen[str | None]):
    """Where to take the card: a place's key, or WORDS for the post's words alone."""

    DEFAULT_CSS = frame("ShareMenu", "medium", tone=FRAME) + f"""
    ShareMenu > Vertical {{ background: {NIGHT}; }}
    ShareMenu OptionList {{ height: auto; background: {NIGHT}; border: none; margin-top: 1; }}
    ShareMenu #share-words {{ color: {_hex(wrapped.MUTED)}; margin-top: 1; }}
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        *(Binding(place.key, f"pick('{place.key}')", place.label, show=False) for place in share.PLACES),
        Binding(WORDS, f"pick('{WORDS}')", "The words", show=False),
    ]

    def __init__(self, week: wrapped.Week) -> None:
        super().__init__()
        self.week = week

    def compose(self) -> ComposeResult:
        options = [Option(Text.assemble((f"{key}  ", f"bold {TEXT}"), (f"{label:<11}", f"bold {TEXT}"), (how, MUTED)),
                          id=key) for key, label, how in wrappedwords.share_choices()]
        with Vertical():
            yield Label(title(*wrappedwords.SHARE_TITLE))
            yield OptionList(*options)
            yield Label(Text.assemble(("The post says: ", MUTED), (wrapped.words(self.week), TEXT)), id="share-words")
            yield Label(keys(*wrappedwords.SHARE_KEY_PAIRS), classes="hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_pick(self, key: str) -> None:
        self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
