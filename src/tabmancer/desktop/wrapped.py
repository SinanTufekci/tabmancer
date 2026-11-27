"""W in the window: Claude Code Wrapped (WrappedDialog), as the terminal's W screen has it: the card of the
slide shown, as large as fits in whole units, in the night sky it is drawn in. ← → go from slide to slide, 1 2 3
take the week, the month or all of the time kept, f turns it between the wide shape and the story one, n puts
the top project's name on it, s saves it as a PNG, g as a GIF with its numbers counting up, c copies it, p
shares it (X, Bluesky, Threads, Instagram, or the post's words alone). What it says and does is
wrappedwords.py's, one copy with the terminal's; the card is wrapped.py's (card.Canvas), shown here as an
image. Counting a period and drawing it moving run off the window's thread (stats.Worker)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QImage, QKeyEvent, QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget

from .. import card, wrapped, wrappedwords
from ..actions import Note
from ..bitart import Colour
from ..sessions import Session
from ..tokens import Report
from . import look
from .formparts import keys_html
from .handover import label
from .stats import Worker

NIGHT = wrappedwords.hex_colour(wrapped.GROUND)  # the window is the card's sky, so the card sits in it
FRAME = wrappedwords.hex_colour(wrapped.VIOLET)
PICKED = "#2d333b"  # the ground of the word picked, as the terminal's pills
NOTE_COLOURS = {"information": look.TEXT, "warning": look.AMBER, "error": look.RED}


def rgb(colour: str) -> Colour:
    found = look.qcolor(colour)
    return found.red(), found.green(), found.blue()


def image_of(canvas: card.Canvas) -> QImage:
    """A card (rows of palette indices) as an image: the rows as they are, the palette as its colour table."""
    data = b"".join(bytes(row) for row in canvas.rows)
    image = QImage(data, canvas.width, canvas.height, canvas.width, QImage.Format.Format_Indexed8)
    image.setColorTable([0xFF000000 | (r << 16) | (g << 8) | b for r, g, b in canvas.palette])
    return image.copy()  # its own pixels: data goes when this returns


def pills_html(words: list[str], picked: str, labels: dict[str, str] | None = None) -> str:
    """Words side by side, the one picked marked, as the terminal's pills: ' ● word ' on a ground of its own,
    '○ word' in grey."""
    out = []
    for word in words:
        shown = (labels or {}).get(word, word)
        out.append(look.styled(f" ● {shown} ", f"bold {look.TEXT} on {PICKED}") if word == picked
                   else look.styled(f"○ {shown}", wrappedwords.MUTED))
    return look.styled("  ").join(out)


class CardView(QWidget):
    """The card, centred, as many real pixels a design pixel as fit (up to the saved card's); drawn again only
    when the slide, the shape, the period, the names or the unit change."""

    def __init__(self, dialog: WrappedDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.drawn: tuple | None = None
        self.image: QImage | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(420, 260)

    def unit(self) -> int:
        wide, high = wrapped.SHAPES[self.dialog.shape]
        return max(1, min(self.width() // wide, self.height() // high, wrapped.UNIT))

    def picture(self) -> QImage:
        d = self.dialog
        key = (id(d.week), d.shape, d.slide, d.names, self.unit())
        if key != self.drawn or self.image is None:
            self.drawn = key
            self.image = image_of(wrapped.draw(d.week, d.rgb, self.unit(), d.shape, d.slide, names=d.names))
        return self.image

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        image = self.picture()
        painter = QPainter(self)
        painter.fillRect(self.rect(), look.qcolor(NIGHT))
        scale = min(1.0, self.width() / image.width(), self.height() / image.height())  # a window smaller than one unit
        if scale < 1.0:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            image = image.scaled(int(image.width() * scale), int(image.height() * scale),
                                 Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(QPoint((self.width() - image.width()) // 2, (self.height() - image.height()) // 2), image)
        painter.end()


class WrappedDialog(QDialog):
    """The week's card, to share, save or copy. Esc (or W) closes it. Window-modal, never waited on."""

    def __init__(self, parent: QWidget | None, report: Report, sessions: dict[str, Session], accent: str,
                 worker: Worker | None = None) -> None:
        super().__init__(parent)
        self.week = wrapped.week(report, dict(sessions))
        self.accent, self.rgb = accent, rgb(accent)
        self.shape = "wide"
        self.slide = wrapped.SLIDES[0]
        self.period = 0 if self.week.whole else self.week.days
        self.names = False  # the top project's name on the tools slide
        self.counting = False
        self.worker = worker or Worker(self)
        self.setWindowTitle("tabmancer: Claude Code Wrapped")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(1000, 720)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        top = QHBoxLayout()
        self.heading = label("")
        self.shapes = label("")
        top.addWidget(self.heading, 1)
        top.addWidget(self.shapes)
        box.addLayout(top)
        self.view = CardView(self)
        box.addWidget(self.view, 1)
        self.about = label("", wrap=True)
        box.addWidget(self.about)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        box.addWidget(label(keys_html(wrappedwords.KEY_PAIRS)))
        self.setStyleSheet(f"QDialog {{ background:{NIGHT}; border:1px solid {FRAME}; }} QLabel {{ color:{look.TEXT}; }}")
        self.finished.connect(self.deleteLater)
        self.show_words()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.setFocus()

    def show_words(self) -> None:
        """The heading, the shapes and the line under the card as they are now; the card drawn again."""
        heading = "".join(look.styled(w, s) for w, s in wrappedwords.title_parts(self.week, self.accent))
        heading += pills_html(list(wrappedwords.PERIODS.values()), wrappedwords.PERIODS.get(self.period, "week"))
        if self.counting:
            heading += look.styled(*wrappedwords.COUNTING)
        self.heading.setText(heading)
        self.shapes.setText(pills_html(list(wrappedwords.SHAPES), self.shape, wrappedwords.SHAPES)
                            + look.styled("   f", wrappedwords.MUTED))
        self.about.setText("".join(look.styled(w, s) for w, s in
                                   wrappedwords.about_parts(self.slide, self.names, self.accent)))
        self.view.update()

    def say(self, note: Note, title: str = "") -> None:
        words = f"{title}: {note.words}" if title else note.words
        self.note.setText(look.span(words, NOTE_COLOURS.get(note.severity, look.TEXT)))

    def canvas(self, shape: str | None = None) -> card.Canvas:
        """The card as saved: three real pixels a design pixel."""
        return wrapped.draw(self.week, self.rgb, shape=shape or self.shape, slide=self.slide, names=self.names)

    # --- the keys -----------------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, text = event.key(), event.text()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.slide = wrappedwords.stepped(self.slide, 1 if key == Qt.Key.Key_Right else -1)
            self.show_words()
        elif text and text in "123":
            self.recount(list(wrappedwords.PERIODS)[int(text) - 1])
        elif text == "f":
            self.shape = wrappedwords.turned(self.shape)
            self.show_words()
        elif text == "n":
            self.names = not self.names
            self.show_words()
        elif text == "s":
            self.say(wrappedwords.saved(self.canvas(), self.week, self.shape, self.slide))
        elif text == "g":
            self.save_moving()
        elif text == "c":
            self.copy()
        elif text == "p":
            self.share_menu().popup(self.view.mapToGlobal(self.view.rect().center()))
        elif text in ("W", "q"):
            self.reject()
        else:
            super().keyPressEvent(event)  # Esc: closed

    def recount(self, days: int) -> None:
        if days == self.period or self.counting:
            return
        self.counting = True
        self.show_words()
        self.worker.run(lambda: wrappedwords.recount(days), lambda week: self.recounted(days, week))

    def recounted(self, days: int, week: wrapped.Week) -> None:
        self.period, self.week, self.counting = days, week, False
        self.show_words()

    def save_moving(self) -> None:
        self.say(wrappedwords.DRAWING)
        week, shape, slide, names = self.week, self.shape, self.slide, self.names
        self.worker.run(lambda: wrappedwords.saved_moving(week, self.rgb, shape, slide, names), self.say)

    def copy(self) -> None:
        """The card on the clipboard as the window's own (QClipboard), on every system."""
        image = image_of(self.canvas())
        QGuiApplication.clipboard().setImage(image)
        self.say(wrappedwords.COPIED if not image.isNull() else wrappedwords.NOT_COPIED)

    def share_menu(self) -> QMenu:
        """Where to take the card: a place (its key picks it), or the post's words alone; what the post says
        under them."""
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background:{look.SURFACE}; color:{look.TEXT}; border:1px solid {FRAME}; }}"
                           f"QMenu::item {{ padding:5px 22px 5px 14px; }}"
                           f"QMenu::item:selected {{ background:{look.PANEL}; color:{self.accent}; }}"
                           f"QMenu::item:disabled {{ color:{wrappedwords.MUTED}; }}")
        title = menu.addAction(f"{wrappedwords.SHARE_TITLE[0]}: {wrappedwords.SHARE_TITLE[1]}")
        title.setEnabled(False)
        menu.addSeparator()
        for key, name, how in wrappedwords.share_choices():
            action = menu.addAction(f"&{key}   {name:<11} {how}")
            action.setData(key)
            action.triggered.connect(lambda _=False, key=key: self.share(key))
        menu.addSeparator()
        said = menu.addAction(f"The post says: {wrapped.words(self.week)}")
        said.setEnabled(False)
        menu.aboutToHide.connect(menu.deleteLater)
        return menu

    def share(self, key: str) -> None:
        if key == wrappedwords.WORDS:
            QGuiApplication.clipboard().setText(wrapped.words(self.week))
            self.say(wrappedwords.WORDS_COPIED)
            return
        done = wrappedwords.shared(key, self.week, self.rgb, self.slide, self.names)
        if done is not None:
            self.say(done[1], title=done[0])
