"""Real pixels in the terminal (Sixel), where the terminal draws them, for Bit.

Whether it does is asked once, before Textual starts reading the keyboard (probe): its primary
device attributes (ESC [ c) list 4 when it draws Sixel, and ESC [ 16 t asks how many pixels a
cell is (elsewhere than Windows, the system knows the window's pixels too). Windows Terminal
(1.22 on) draws Sixel and counts 10x20 pixels to a cell whatever the font; a terminal whose cell
size is not known gets no Sixel, so an image never comes out the wrong size. A font made bigger or
smaller resizes the terminal, and the cell is measured again (resized). TABMANCER_SIXEL=0 turns it
off, =1 on (with Windows Terminal's cell size unless the terminal says otherwise).

An image is text: its colours, then six rows of pixels at a time, a character a column (the
six pixels as bits), colour by colour, runs of a character as !count. It is drawn at the cursor
and stays there until text is written over its cells.

Textual knows nothing of images. SixelHost, mixed into the app, sends each image right after an
update Textual writes to the terminal, inside the same synchronized frame when the terminal has
that (Textual's own _begin_update/_end_update), so Textual's blank cells and the picture over
them show at once: when the update wrote over the image's cells, or the picture changed. An
image is not sent while a dialog is in front, or while something else covers its cells.
"""

from __future__ import annotations

import base64
import itertools
import os
import re
import sys
import time
import weakref
import zlib
from dataclasses import dataclass, replace

from .bitart import WT_CELL, Colour

WAIT = 0.35  # seconds to wait for the terminal's answers
SYNC = 2026  # synchronized output: what comes between its two marks is shown at once
BEGIN, END = f"\x1b[?{SYNC}h", f"\x1b[?{SYNC}l"
MOST = 256   # colours an image can have


KITTY_ASK = "\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\"  # a one-pixel image, only asked about


@dataclass(frozen=True)
class Terminal:
    cell: tuple[int, int]  # pixels a cell, across and down
    sync: bool = False     # shows what comes between two marks at once (synchronized output, mode 2026)
    kitty: bool = False    # draws pictures the kitty way (kitty, Ghostty), not as Sixel


_terminal: Terminal | None = None
_asked = False


def terminal() -> Terminal | None:
    """The terminal draws Sixel at this cell size, as found by probe() (None: it does not)."""
    return _terminal


def probe() -> Terminal | None:
    """Ask the terminal, once, before Textual reads the keyboard. Anything amiss is a no."""
    global _terminal, _asked
    if _asked:
        return _terminal
    _asked = True
    setting = os.environ.get("TABMANCER_SIXEL")
    if setting == "0":
        return None
    try:
        # The attributes last: every terminal answers those. One that does not know the kitty
        # question ignores it.
        answer = _ask(f"\x1b[?{SYNC}$p\x1b[16t{KITTY_ASK}\x1b[c")
    except (OSError, ValueError, AttributeError, ImportError):
        answer = ""
    cell = cell_in(answer) or window_cell() or (WT_CELL if os.environ.get("WT_SESSION") or setting == "1" else None)
    sixel = sixel_in(answer) or setting == "1"
    kitty = not sixel and kitty_in(answer)  # Sixel where both are drawn: the way tried the most
    if cell and (sixel or kitty):
        _terminal = Terminal(cell, sync=sync_in(answer), kitty=kitty)
    return _terminal


def kitty_in(answer: str) -> bool:
    """Whether the terminal answered the kitty graphics question (KITTY_ASK) with OK."""
    return "\x1b_Gi=31;OK\x1b\\" in answer


def sync_in(answer: str) -> bool:
    """Whether the terminal said (DECRQM: ESC [ ? 2026 ; state $ y) it has synchronized output."""
    found = re.search(rf"\x1b\[\?{SYNC};(\d)\$y", answer)
    return bool(found) and found.group(1) in "12"


def sixel_in(answer: str) -> bool:
    """Whether the primary device attributes (ESC [ ? ... c) list Sixel (4)."""
    found = re.search(r"\x1b\[\?([\d;]*)c", answer)
    return bool(found) and "4" in found.group(1).split(";")


def cell_in(answer: str) -> tuple[int, int] | None:
    """The cell size from an answer to ESC [ 16 t: ESC [ 6 ; height ; width t."""
    found = re.search(r"\x1b\[6;(\d+);(\d+)t", answer)
    if not found:
        return None
    return _cell(int(found.group(2)), int(found.group(1)))


def _cell(width: int, height: int, cols: int = 1, rows: int = 1) -> tuple[int, int] | None:
    """A cell's pixels from a width and height in pixels over so many cells; None when unlikely."""
    if not (cols and rows):
        return None
    width, height = width // cols, height // rows
    return (width, height) if 4 <= width <= 64 and 8 <= height <= 128 else None


def window_cell() -> tuple[int, int] | None:
    """The cell size from the window's size in pixels as the system keeps it with the terminal's
    (TIOCGWINSZ): most terminals fill it in, some leave it at zero. Windows keeps no such thing."""
    if sys.platform == "win32":
        return None
    try:
        import fcntl
        import struct
        import termios
    except ImportError:
        return None
    for stream in (sys.__stdin__, sys.__stdout__, sys.__stderr__):
        try:
            rows, cols, width, height = struct.unpack("HHHH", fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ,
                                                                           bytes(8)))
        except (AttributeError, OSError, ValueError):
            continue
        return _cell(width, height, cols, rows)
    return None


def resized(cols: int, rows: int, pixels: tuple[int, int] | None = None) -> bool:
    """After the terminal changed size: its cell's pixels again, as it said with the resize
    (pixels: the window's) or as the system has them. A font made bigger or smaller changes
    them, and Bit is drawn to them. Windows Terminal counts 10x20 whatever the font, and nothing
    says otherwise there: it stays. Whether the cell size changed."""
    global _terminal
    if _terminal is None:
        return False
    found = (_cell(*pixels, cols, rows) if pixels else None) or window_cell()
    if found is None or found == _terminal.cell:
        return False
    _terminal = replace(_terminal, cell=found)
    return True


def _ask(question: str) -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    return _ask_windows(question) if sys.platform == "win32" else _ask_posix(question)


def _ask_windows(question: str) -> str:
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    keys, screen = kernel32.GetStdHandle(-10), kernel32.GetStdHandle(-11)
    key_mode, screen_mode = ctypes.c_uint32(), ctypes.c_uint32()
    if not (kernel32.GetConsoleMode(keys, ctypes.byref(key_mode))
            and kernel32.GetConsoleMode(screen, ctypes.byref(screen_mode))):
        return ""
    kernel32.SetConsoleMode(screen, screen_mode.value | 0x0004)  # escape sequences out
    kernel32.SetConsoleMode(keys, 0x0200)                         # and in, nothing echoed
    answer = ""
    try:
        os.write(sys.stdout.fileno(), question.encode())
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not re.search(r"\x1b\[\?[\d;]*c$", answer):
            if msvcrt.kbhit():
                answer += msvcrt.getwch()
            else:
                time.sleep(0.003)
    finally:
        kernel32.SetConsoleMode(keys, key_mode.value)
        kernel32.SetConsoleMode(screen, screen_mode.value)
    return answer


def _ask_posix(question: str) -> str:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    answer = ""
    try:
        tty.setraw(fd)
        os.write(sys.stdout.fileno(), question.encode())
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not re.search(r"\x1b\[\?[\d;]*c$", answer):
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            answer += os.read(fd, 64).decode("latin-1")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return answer


# --- encoding -------------------------------------------------------------------------------------

def encode(rows: list[list[Colour | None]], background: Colour) -> str:
    """Pixels as colours (None: nothing drawn) as a Sixel image: see encode_indexed."""
    palette: list[Colour | None] = [None]
    index: dict[Colour | None, int] = {None: 0}
    numbered = []
    for row in rows:
        line = bytearray(len(row))
        for x, colour in enumerate(row):
            n = index.get(colour)
            if n is None:
                n = index[colour] = len(palette)
                palette.append(colour)
            line[x] = n if n < 256 else 0
        numbered.append(bytes(line))
    return encode_indexed(palette[:256], numbered, background)


_ONE_OF = [bytes(1 if v == n else 0 for v in range(256)) for n in range(256)]  # a register's pixels as 1s
_CHARS = bytes((v + 63) & 0xFF for v in range(256))  # six pixels' bits as a Sixel character, ? to ~
_RUN = re.compile(rb"(.)\1*", re.DOTALL)


def encode_indexed(palette: list[Colour | None], rows: list[bytes], background: Colour) -> str:
    """Rows of indices into a palette (None: nothing drawn) as a Sixel image, opaque: where nothing
    is drawn, the background, so a new picture covers the last one whole.

    A band of six rows goes register by register: each row's pixels of that register as 0s and
    1s (a translation), read as one number and shifted by the row's place in the band, add up to
    every column's six bits at once, a byte each. The loops run over registers and runs, never
    over pixels, which is what keeps twenty pictures a second cheap."""
    height = len(rows)
    width = len(rows[0]) if rows else 0
    defined: list[Colour] = [background]  # the registers' colours, in order
    register: dict[Colour, int] = {background: 0}
    table = bytearray(256)
    for n, colour in enumerate(palette):
        colour = background if colour is None else colour
        r = register.get(colour)
        if r is None:
            if len(defined) < MOST:
                r = register[colour] = len(defined)
                defined.append(colour)
            else:
                r = _nearest(colour, defined)
        table[n] = r
    numbered = [bytes(row).translate(table) for row in rows]
    out = [f'\x1bP0;1;0q"1;1;{width};{height}']
    out += [f"#{n};2;{r * 100 // 255};{g * 100 // 255};{b * 100 // 255}" for n, (r, g, b) in enumerate(defined)]
    for top in range(0, height, 6):
        band = numbered[top:top + 6]
        passes = []
        for n in sorted(set(b"".join(band))):
            bits = 0
            for k, row in enumerate(band):
                bits += int.from_bytes(row.translate(_ONE_OF[n]), "big") << k
            chars = bits.to_bytes(width, "big").translate(_CHARS).rstrip(b"?")  # nothing to the end: no more
            passes.append(f"#{n}" + "".join(
                f"!{len(run.group(0))}{chr(run.group(0)[0])}" if len(run.group(0)) > 3 else run.group(0).decode()
                for run in _RUN.finditer(chars)))
        out.append("$".join(passes))
        out.append("-")
    out.append("\x1b\\")
    return "".join(out)


def _nearest(colour: Colour, defined: list[Colour]) -> int:
    return min(range(len(defined)), key=lambda n: sum((a - b) ** 2 for a, b in zip(colour, defined[n])))


KITTY_CHUNK = 4096  # the base64 of a picture goes out in pieces of at most this


def encode_kitty(palette: list[Colour | None], rows: list[bytes], image: int) -> str:
    """Rows of indices into a palette as a kitty graphics picture with the id image: RGBA, nothing
    drawn left clear, packed with zlib, in base64 pieces. Sent again with the same id, it replaces
    the last one and its place on the screen; q=2 keeps the terminal from answering, and C=1 the
    cursor where it is."""
    table = [bytes((*colour, 255)) if colour is not None else bytes(4) for colour in palette]
    table += [bytes(4)] * (256 - len(table))
    pixels = b"".join(b"".join(map(table.__getitem__, row)) for row in rows)
    data = base64.standard_b64encode(zlib.compress(pixels)).decode()
    pieces = [data[i:i + KITTY_CHUNK] for i in range(0, len(data), KITTY_CHUNK)] or [""]
    width, height = (len(rows[0]) if rows else 0), len(rows)
    out = []
    for n, piece in enumerate(pieces):
        more = int(n < len(pieces) - 1)
        keys = f"a=T,f=32,s={width},v={height},i={image},p=1,o=z,q=2,C=1,m={more}" if n == 0 else f"m={more}"
        out.append(f"\x1b_G{keys};{piece}\x1b\\")
    return "".join(out)


def kitty_delete(image: int) -> str:
    """Takes a kitty picture off the screen and out of the terminal's memory."""
    return f"\x1b_Ga=d,d=I,i={image},q=2\x1b\\"


# --- the app's side -------------------------------------------------------------------------------

_numbers = itertools.count(7301)


class Picture:
    """What a widget wants on the screen in pixels: drawn by the widget, sent by the host."""

    def __init__(self, owner, place) -> None:
        self.owner = owner           # the widget: its screen
        self.place = place           # () -> its cells on the screen (x, y, width, height), or None
        self.data = ""               # the image: Sixel, or kitty graphics
        self.fresh = False           # changed since it was last sent
        self.number = next(_numbers)  # its id in kitty graphics
        self.shown = False           # on the screen now (kitty graphics: it stays until taken off)


class SixelHost:
    """Mixed into an App: sends the pictures of its widgets right after Textual's updates."""

    def _sixel_pictures(self) -> weakref.WeakSet:
        found = self.__dict__.get("_pictures")
        if found is None:
            found = self.__dict__["_pictures"] = weakref.WeakSet()
        return found

    def add_picture(self, picture: Picture) -> None:
        self._sixel_pictures().add(picture)

    def remove_picture(self, picture: Picture) -> None:
        self._sixel_pictures().discard(picture)
        self._take_off(picture)

    def _take_off(self, picture: Picture) -> None:
        """A kitty picture off the screen: text written over it does not take it off, as it does
        a Sixel image. Sent again when it can be seen (fresh)."""
        found = terminal()
        driver = getattr(self, "_driver", None)
        if picture.shown and found is not None and found.kitty and driver is not None:
            driver.write(kitty_delete(picture.number))
            driver.flush()
        picture.shown, picture.fresh = False, True

    def on_resize(self, event) -> None:
        """A font made bigger or smaller resizes the terminal: pictures drawn to the old cell size
        are not sent again, and their widgets draw them anew. The screen is written again whole:
        a picture shown before, bigger now than its cells, stays on beside them until text is
        written there, and a terminal keeping its rows and columns (xterm) makes Textual write
        nothing."""
        pixels = getattr(event, "pixel_size", None)
        if resized(event.size.width, event.size.height, (pixels.width, pixels.height) if pixels else None):
            for picture in self._sixel_pictures():
                picture.data = ""
            self.refresh()  # type: ignore[attr-defined]

    def _display(self, screen, renderable) -> None:
        """Textual's update, and the pictures it wrote over sent again right behind it, in one
        synchronized frame: Textual marks one itself when it found the terminal has them, and
        where it did not, but the terminal told us it has (probe), the frame is marked here."""
        self.__dict__["_sixel_update"] = renderable
        driver = getattr(self, "_driver", None)
        ours = self._sixel_sync() and renderable is not None
        if ours:
            driver.write(BEGIN)
        try:
            super()._display(screen, renderable)  # type: ignore[misc]
        finally:
            self.__dict__["_sixel_update"] = None
            if ours:
                driver.write(END)
                driver.flush()

    def _sixel_sync(self) -> bool:
        found = terminal()
        return bool(found and found.sync and getattr(self, "_driver", None) is not None
                    and not getattr(self, "_sync_available", False) and not getattr(self, "is_headless", True)
                    and self._sixel_pictures())

    def send_now(self, picture: Picture) -> bool:
        """A new picture, straight to the terminal: its cells are not written over first, so the
        old picture is replaced, never wiped and then drawn (that blinked). Kept for Textual's
        next update when it cannot go now (a dialog in front, say)."""
        driver = getattr(self, "_driver", None)
        if driver is None or getattr(self, "is_headless", True):
            return False
        cells = picture.place() if picture.data else None
        if cells is None or not in_view(self, picture, cells):
            self._take_off(picture)
            return False
        x, y, _, _ = cells
        sync = self._sixel_sync() or getattr(self, "_sync_available", False)
        image = f"\x1b7\x1b[{y + 1};{x + 1}H{picture.data}\x1b8"
        driver.write(f"{BEGIN}{image}{END}" if sync else image)
        driver.flush()
        picture.fresh, picture.shown = False, True
        return True

    def _end_update(self) -> None:
        try:
            self.send_pictures(self.__dict__.get("_sixel_update"))
        except Exception as error:  # noqa: BLE001 - a picture never takes Textual's own update down with it
            from .safework import log_error
            log_error("send_pictures", error)
        super()._end_update()  # type: ignore[misc]

    def send_pictures(self, update) -> None:
        driver = getattr(self, "_driver", None)
        if driver is None:
            return
        found = terminal()
        kitty = found is not None and found.kitty
        for picture in list(self._sixel_pictures()):
            cells = picture.place() if picture.data else None
            if cells is None or not in_view(self, picture, cells):
                if kitty and picture.shown:  # a dialog in front, Bit away: it would stay over them
                    driver.write(kitty_delete(picture.number))
                    picture.shown, picture.fresh = False, True
                continue
            # Text written over a Sixel image wipes it: then it goes again. Not a kitty picture.
            if not (picture.fresh or (not kitty and wrote_over(update, cells))):
                continue
            x, y, _, _ = cells
            driver.write(f"\x1b7\x1b[{y + 1};{x + 1}H{picture.data}\x1b8")
            picture.fresh, picture.shown = False, True


def wrote_over(update, cells: tuple[int, int, int, int]) -> bool:
    """Whether an update Textual wrote touched these cells (unknown updates: yes).

    A partial update writes more than its spans: a widget's piece of a line that a span reaches
    into goes out from the piece's own start (Textual's ChopsUpdate.render_segments), so a cell
    refreshed to the right of a picture writes over it too."""
    x, y, width, height = cells
    spans = getattr(update, "spans", None)  # a few pieces of lines (a partial update)
    if spans is not None:
        if not any(y <= row < y + height for row, _, _ in spans):
            return False
        if hasattr(update, "chops"):
            try:
                spans = written(update)
            except Exception:  # noqa: BLE001 - not what Textual writes after all: assume the worst
                return True
        return any(y <= row < y + height and end > x and start < x + width for row, start, end in spans)
    region = getattr(update, "region", None)  # a whole region (the layout)
    if region is not None:
        return not (region.x >= x + width or region.right <= x or region.y >= y + height or region.bottom <= y)
    return update is not None


def written(update) -> list[tuple[int, int, int]]:
    """The cells a partial update writes, as (row, start, end), the way render_segments writes
    them: each piece a span reaches into, from the piece's start to its end or the span's."""
    runs = []
    for row, x1, x2 in update.spans:
        for end, (x, strip) in zip(update.chop_ends[row], update.chops[row].items()):
            if strip is None or x > x2 or end <= x1:
                continue
            runs.append((row, x, end if x2 > x >= x1 and end <= x2 else min(end, x2)))
    return runs


def in_view(app, picture: Picture, cells: tuple[int, int, int, int]) -> bool:
    """The picture's widget is on the screen in front, and nothing covers its cells."""
    owner = picture.owner
    if owner is None or not owner.is_attached or app.screen is not owner.screen:
        return False
    x, y, width, height = cells
    for px, py in ((x, y), (x + width - 1, y), (x, y + height - 1), (x + width - 1, y + height - 1),
                   (x + width // 2, y + height // 2)):
        try:
            widget, _ = app.screen.get_widget_at(px, py)
        except Exception:  # noqa: BLE001 - off the screen, or a screen changing size: not now
            return False
        if widget is not owner:
            return False
    return True
