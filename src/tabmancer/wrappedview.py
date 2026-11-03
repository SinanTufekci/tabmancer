"""W: Claude Code Wrapped on the screen. The card in real pixels where the terminal draws them, as
large as fits in whole units; elsewhere its numbers in words. s saves it as a PNG, c copies it to
paste where you share it (wrapped.py draws it)."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from . import card, sixel, wrapped
from .dialogs import frame, title
from .theme import MUTED, TEXT, keys
from .tokenchart import PixelPicture

_SHIFTED = bytes((n + 1) % 256 for n in range(256))  # the card's palette after a None for "nothing drawn"


def _rgb(colour: str) -> tuple[int, int, int]:
    found = Color.parse(colour)
    return found.r, found.g, found.b


class CardPicture(PixelPicture, Static):
    """The week's card: a picture in whole units, centred, where pixels are drawn and it fits."""

    DEFAULT_CSS = "CardPicture { width: 1fr; height: 1fr; }"

    def __init__(self, week: wrapped.Week, accent: str) -> None:
        super().__init__("")
        self.week, self.accent = week, accent

    def on_mount(self) -> None:
        if self.start_pixels():
            self.refresh()

    def unit(self) -> int:
        """Real pixels a design pixel, the most the card fits in at: 0 when it does not."""
        found, cells = sixel.terminal(), self.cells()
        if found is None or cells is None:
            return 0
        width, height = cells[2] * found.cell[0], cells[3] * found.cell[1] // 6 * 6
        return min(width // wrapped.WIDTH, height // wrapped.HEIGHT, wrapped.UNIT)

    def picture_key(self) -> tuple:
        return repr(self.week), self.accent, self.unit()

    def picture_rows(self, width: int, height: int):
        drawn = wrapped.draw(self.week, _rgb(self.accent), max(1, self.unit()))
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
        return in_words(self.week, self.accent)


def in_words(week: wrapped.Week, accent: str) -> Text:
    """The card's numbers where no picture of it is shown."""
    out = Text.assemble(("CLAUDE CODE ", f"bold {TEXT}"), ("WRAPPED", f"bold {accent}"),
                        (f"   {wrapped.span(week).lower()}\n\n", MUTED))
    for what, number, note in wrapped.tiles(week):
        out.append(f"{what.lower():<14}", style=MUTED)
        out.append(f"{number.lower() if what == 'TOP TOOL' else number:<12}", style=f"bold {TEXT}")
        out.append(f"{note.lower()}\n", style=MUTED)
    out.append("\nWhere the terminal draws pictures this is a card; s saves it as one either way.", style=MUTED)
    return out


class WrappedScreen(ModalScreen[None]):
    """The week's card, to save or copy."""

    DEFAULT_CSS = frame("WrappedScreen", "large", height="92%") + """
    WrappedScreen CardPicture { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q,W", "close", "Close"),
        Binding("s", "save", "Save"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, week: wrapped.Week, accent: str) -> None:
        super().__init__()
        self.week, self.accent = week, accent

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Claude Code Wrapped", "your last seven days, to share: counts only, no project names"))
            yield CardPicture(self.week, self.accent)
            yield Label(keys(("s", "save it as a picture"), ("c", "copy it, to paste where you share it"),
                             ("Esc", "close")), classes="hint")

    def canvas(self) -> card.Canvas:
        return wrapped.draw(self.week, _rgb(self.accent))

    def action_save(self) -> None:
        try:
            path = card.save(self.canvas(), wrapped.filename(self.week))
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
