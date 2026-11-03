"""W: Claude Code Wrapped on the screen. The card in real pixels where the terminal draws them, as
large as fits in whole units; elsewhere its numbers in words. f turns it between the wide card and
the story one, p shares it (X, Bluesky, Threads, Instagram: share.py), s saves it as a PNG, c
copies it to paste where you share it (wrapped.py draws it)."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from . import card, share, sixel, wrapped
from .dialogs import frame, title
from .theme import MUTED, TEXT, keys
from .tokenchart import PixelPicture

_SHIFTED = bytes((n + 1) % 256 for n in range(256))  # the card's palette after a None for "nothing drawn"
WORDS = "w"  # the share menu's key for copying the post's words alone


def _rgb(colour: str) -> tuple[int, int, int]:
    found = Color.parse(colour)
    return found.r, found.g, found.b


class CardPicture(PixelPicture, Static):
    """The week's card: a picture in whole units, centred, where pixels are drawn and it fits."""

    DEFAULT_CSS = "CardPicture { width: 1fr; height: 1fr; }"

    def __init__(self, week: wrapped.Week, accent: str, shape: str = "wide") -> None:
        super().__init__("")
        self.week, self.accent, self.shape = week, accent, shape

    def on_mount(self) -> None:
        if self.start_pixels():
            self.refresh()

    def turn(self, shape: str) -> None:
        self.shape = shape
        self.refresh()

    def unit(self) -> int:
        """Real pixels a design pixel, the most the card fits in at: 0 when it does not."""
        found, cells = sixel.terminal(), self.cells()
        if found is None or cells is None:
            return 0
        wide, high = wrapped.SHAPES[self.shape]
        width, height = cells[2] * found.cell[0], cells[3] * found.cell[1] // 6 * 6
        return min(width // wide, height // high, wrapped.UNIT)

    def picture_key(self) -> tuple:
        return repr(self.week), self.accent, self.shape, self.unit()

    def picture_rows(self, width: int, height: int):
        drawn = wrapped.draw(self.week, _rgb(self.accent), max(1, self.unit()), self.shape)
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


def in_words(week: wrapped.Week, accent: str, cramped: bool = False) -> Text:
    """The card's numbers where no picture of it is shown."""
    name, why = wrapped.persona(week)
    out = Text.assemble(("CLAUDE CODE ", f"bold {TEXT}"), ("WRAPPED", f"bold {accent}"),
                        (f"   {wrapped.span(week).lower()}\n\n", MUTED),
                        ("I'm ", TEXT), (name.title(), f"bold {accent}"), (f"  {why.lower()}\n\n", MUTED))
    for what, number, note in wrapped.tiles(week):
        out.append(f"{what.lower():<17}", style=MUTED)
        out.append(f"{number.lower() if what in ('TOP TOOL', 'MODELS') else number:<14}", style=f"bold {TEXT}")
        out.append(f"{note.lower()}\n", style=MUTED)
    out.append("\nThe card is larger than this window: a larger one shows it; s saves it either way." if cramped else
               "\nWhere the terminal draws pictures this is a card; s saves it as one either way.", style=MUTED)
    return out


class WrappedScreen(ModalScreen[None]):
    """The week's card, to share, save or copy."""

    DEFAULT_CSS = frame("WrappedScreen", "large", height="92%") + """
    WrappedScreen CardPicture { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q,W", "close", "Close"),
        Binding("p", "share", "Share"),
        Binding("f", "turn", "Wide or story"),
        Binding("s", "save", "Save"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, week: wrapped.Week, accent: str) -> None:
        super().__init__()
        self.week, self.accent = week, accent
        self.shape = "wide"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.heading(), id="wrapped-title")
            yield CardPicture(self.week, self.accent, self.shape)
            yield Label(keys(("p", "share: X, Bluesky, Threads, Instagram"), ("f", "wide or story"),
                             ("s", "save"), ("c", "copy"), ("Esc", "close")), classes="hint")

    def heading(self) -> Text:
        shaped = "the wide card, for posts" if self.shape == "wide" else "the story card, for Instagram and stories"
        return title("Claude Code Wrapped", "your last seven days: counts only, no project names", shaped)

    def canvas(self) -> card.Canvas:
        return wrapped.draw(self.week, _rgb(self.accent), shape=self.shape)

    def action_turn(self) -> None:
        self.shape = "story" if self.shape == "wide" else "wide"
        self.query_one(CardPicture).turn(self.shape)
        self.query_one("#wrapped-title", Label).update(self.heading())

    def action_share(self) -> None:
        def chosen(key: str | None) -> None:
            if key == WORDS:
                from .statsview import copy_text

                copy_text(self.app, wrapped.words(self.week))
                self.notify("The post's words are on the clipboard.", timeout=6)
                return
            place = next((p for p in share.PLACES if p.key == key), None)
            if place is None:
                return
            try:
                done = share.share(place, self.week, _rgb(self.accent))
            except OSError as error:
                self.notify(f"The card was not saved: {error}", severity="error", timeout=10)
                return
            self.notify(done.note, title=f"Wrapped for {place.label}", timeout=14)

        self.app.push_screen(ShareMenu(self.week), chosen)

    def action_save(self) -> None:
        try:
            path = card.save(self.canvas(), wrapped.filename(self.week, self.shape))
        except OSError as error:
            self.notify(f"The card was not saved: {error}", severity="error", timeout=10)
            return
        self.notify(f"Saved as {path}", timeout=10)

    def action_copy(self) -> None:
        if card.copy_picture(self.canvas()):
            self.notify("Copied: paste it where you share it.", timeout=6)
        else:
            self.notify("This system's clipboard took no picture: s saves it as a file.", severity="warning",
                        timeout=8)

    def action_close(self) -> None:
        self.dismiss(None)


class ShareMenu(ModalScreen[str | None]):
    """Where to take the card: a place's key, or WORDS for the post's words alone."""

    DEFAULT_CSS = frame("ShareMenu", "medium") + """
    ShareMenu OptionList { height: auto; background: $surface; border: none; margin-top: 1; }
    ShareMenu #share-words { color: $text-muted; margin-top: 1; }
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
        options = [Option(Text.assemble((f"{place.key}  ", f"bold {TEXT}"), (f"{place.label:<11}", f"bold {TEXT}"),
                                        (place.how, MUTED)), id=place.key) for place in share.PLACES]
        options.append(Option(Text.assemble((f"{WORDS}  ", f"bold {TEXT}"), (f"{'The words':<11}", f"bold {TEXT}"),
                                            ("copy what the post says, for anywhere else", MUTED)), id=WORDS))
        with Vertical():
            yield Label(title("Share your week", "nothing is posted until you post it"))
            yield OptionList(*options)
            yield Label(Text.assemble(("The post says: ", MUTED), (wrapped.words(self.week), TEXT)), id="share-words")
            yield Label(keys(("Enter", "take it there"), ("x b t i w", "the same, at once"), ("Esc", "cancel")),
                        classes="hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_pick(self, key: str) -> None:
        self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
