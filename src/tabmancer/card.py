"""A picture drawn in pure Python, pixel art like Bit: a canvas of palette indices, a 5x7 pixel font
(capitals and small letters), and the PNG it is saved as, or copied to the clipboard as, to paste
where you share it. For the week's card (wrapped.py)."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from .bitart import Colour

GLYPH_W, GLYPH_H = 5, 7

# Rows top to bottom, five pixels across each.
_GLYPHS = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    " ": "00000 00000 00000 00000 00000 00000 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    ",": "00000 00000 00000 00000 01100 00100 01000", ":": "00000 01100 01100 00000 01100 01100 00000",
    "%": "11000 11001 00010 00100 01000 10011 00011", "+": "00000 00100 00100 11111 00100 00100 00000",
    "-": "00000 00000 00000 11111 00000 00000 00000", "/": "00000 00001 00010 00100 01000 10000 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "·": "00000 00000 00000 00100 00000 00000 00000", "'": "00100 00100 01000 00000 00000 00000 00000",
    "!": "00100 00100 00100 00100 00100 00000 00100", "?": "01110 10001 00001 00010 00100 00000 00100",
    "#": "01010 01010 11111 01010 11111 01010 01010", "&": "01100 10010 10100 01000 10101 10010 01101",
    "–": "00000 00000 00000 11111 00000 00000 00000", "_": "00000 00000 00000 00000 00000 00000 11111",
    "~": "00000 00000 01000 10101 00010 00000 00000",
}
# Small letters, five pixels high on the same line as the capitals; g j p q y hang two rows under it.
_SMALL = {
    "a": "00000 00000 01110 00001 01111 10001 01111", "b": "10000 10000 11110 10001 10001 10001 11110",
    "c": "00000 00000 01110 10000 10000 10001 01110", "d": "00001 00001 01111 10001 10001 10001 01111",
    "e": "00000 00000 01110 10001 11111 10000 01110", "f": "00110 01001 01000 11100 01000 01000 01000",
    "g": "00000 00000 01111 10001 10001 10001 01111 00001 01110",
    "h": "10000 10000 10110 11001 10001 10001 10001", "i": "00100 00000 01100 00100 00100 00100 01110",
    "j": "00010 00000 00110 00010 00010 00010 00010 10010 01100",
    "k": "10000 10000 10010 10100 11000 10100 10010", "l": "01100 00100 00100 00100 00100 00100 01110",
    "m": "00000 00000 11010 10101 10101 10101 10101", "n": "00000 00000 10110 11001 10001 10001 10001",
    "o": "00000 00000 01110 10001 10001 10001 01110",
    "p": "00000 00000 11110 10001 10001 10001 11110 10000 10000",
    "q": "00000 00000 01111 10001 10001 10001 01111 00001 00001",
    "r": "00000 00000 10110 11001 10000 10000 10000", "s": "00000 00000 01111 10000 01110 00001 11110",
    "t": "01000 01000 11100 01000 01000 01001 00110", "u": "00000 00000 10001 10001 10001 10011 01101",
    "v": "00000 00000 10001 10001 10001 01010 00100", "w": "00000 00000 10001 10001 10101 10101 01010",
    "x": "00000 00000 10001 01010 00100 01010 10001",
    "y": "00000 00000 10001 10001 10001 10001 01111 00001 01110",
    "z": "00000 00000 11111 00010 00100 01000 11111",
}
FONT: dict[str, list[str]] = {char: rows.split() for char, rows in {**_GLYPHS, **_SMALL}.items()}


def text_width(text: str, size: int = 1) -> int:
    """Pixels across a line of text: a glyph and a pixel's gap each, at size."""
    return max(0, len(text) * (GLYPH_W + 1) * size - size)


class Canvas:
    """Rows of palette indices, the ground being 0."""

    def __init__(self, width: int, height: int, ground: Colour) -> None:
        self.width, self.height = width, height
        self.palette: list[Colour] = [ground]
        self.index: dict[Colour, int] = {ground: 0}
        self.rows = [bytearray(width) for _ in range(height)]

    def ink(self, colour: Colour) -> int:
        found = self.index.get(colour)
        if found is None:
            if len(self.palette) >= 256:  # a PNG palette's most: the nearest one there is
                return min(range(len(self.palette)),
                           key=lambda n: sum((a - b) ** 2 for a, b in zip(colour, self.palette[n])))
            found = self.index[colour] = len(self.palette)
            self.palette.append(colour)
        return found

    def rect(self, x: int, y: int, width: int, height: int, colour: Colour) -> None:
        n = self.ink(colour)
        left, right = max(0, x), min(self.width, x + width)
        if right <= left:
            return
        for row in self.rows[max(0, y):max(0, min(self.height, y + height))]:
            row[left:right] = bytes([n]) * (right - left)

    def box(self, x: int, y: int, width: int, height: int, radius: int, colour: Colour) -> None:
        """A box with its corners rounded off by radius pixels."""
        n = self.ink(colour)
        for dy in range(height):
            if not 0 <= y + dy < self.height:
                continue
            edge, cut = min(dy, height - 1 - dy), 0
            if edge < radius:
                off = radius - edge - 0.5
                cut = radius - int((radius * radius - off * off) ** 0.5 + 0.5)
            left, right = max(0, x + cut), min(self.width, x + width - cut)
            if right > left:  # a slice at a time: the story card's panel is close to a million pixels
                self.rows[y + dy][left:right] = bytes([n]) * (right - left)

    def text(self, x: int, y: int, text: str, colour: Colour, size: int = 1) -> int:
        """Letters at (x, y), their top left, as they are written (a small letter the font has not
        drawn, as its capital); another character the font has not drawn is left a gap. Returns the
        width written."""
        n = self.ink(colour)
        for k, char in enumerate(text):
            glyph = FONT.get(char) or FONT.get(char.upper())
            if glyph is None:
                continue
            left = x + k * (GLYPH_W + 1) * size
            for gy, line in enumerate(glyph):
                for gx, bit in enumerate(line):
                    if bit == "1":
                        for yy in range(y + gy * size, y + (gy + 1) * size):
                            if 0 <= yy < self.height:
                                row = self.rows[yy]
                                for xx in range(left + gx * size, left + (gx + 1) * size):
                                    if 0 <= xx < self.width:
                                        row[xx] = n
        return text_width(text, size)

    def blit(self, x: int, y: int, palette: list[Colour | None], rows: list[bytes]) -> None:
        """Another picture's pixels over this one, where they are drawn (index 0: nothing)."""
        inks = [None if colour is None else self.ink(colour) for colour in palette]
        for dy, source in enumerate(rows):
            if not 0 <= y + dy < self.height:
                continue
            row = self.rows[y + dy]
            for dx, n in enumerate(source):
                if n and 0 <= x + dx < self.width and inks[n] is not None:
                    row[x + dx] = inks[n]

    def png(self) -> bytes:
        return png(self.width, self.height, self.palette, self.rows)

    def rgb(self, x: int, y: int) -> Colour:
        return self.palette[self.rows[y][x]]


def png(width: int, height: int, palette: list[Colour], rows: list[bytes | bytearray],
        clear: bool = False) -> bytes:
    """An indexed-colour PNG: a palette of up to 256 colours, a byte a pixel; clear: the first
    colour see-through (pictures over another, Bit's frames in a banner)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(row) for row in rows)  # each row unfiltered
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", b"".join(bytes(colour) for colour in palette))
            + (chunk(b"tRNS", b"\x00") if clear else b"")
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --- the clipboard -----------------------------------------------------------------------------

def dib(canvas: Canvas) -> bytes:
    """The picture as Windows keeps a bitmap on the clipboard (CF_DIB): a header, then the rows
    bottom up, four bytes a pixel (blue, green, red, unused)."""
    header = struct.pack("<IiiHHIIiiII", 40, canvas.width, canvas.height, 1, 32, 0, canvas.width * canvas.height * 4,
                         2835, 2835, 0, 0)
    table = [bytes((b, g, r, 0)) for r, g, b in canvas.palette]
    return header + b"".join(b"".join(table[n] for n in row) for row in reversed(canvas.rows))


def copy_picture(canvas: Canvas) -> bool:
    """The picture on the clipboard, to paste where you share it: whether it went there."""
    try:
        if sys.platform == "win32":
            return _copy_windows(canvas)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(canvas.png())
        path = handle.name
        try:
            if sys.platform == "darwin":
                script = f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'
                return subprocess.run(["osascript", "-e", script], check=False, capture_output=True,
                                      timeout=10).returncode == 0
            for tool in (["wl-copy", "--type", "image/png"], ["xclip", "-selection", "clipboard", "-t", "image/png"]):
                if shutil.which(tool[0]):
                    with open(path, "rb") as source:
                        return subprocess.run(tool, check=False, stdin=source, capture_output=True,
                                              timeout=10).returncode == 0
            return False
        finally:
            os.unlink(path)
    except (OSError, subprocess.SubprocessError):
        return False


def _copy_windows(canvas: Canvas) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes, kernel32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes, kernel32.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes, user32.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes, user32.RegisterClipboardFormatW.restype = [wintypes.LPCWSTR], wintypes.UINT

    def put(kind: int, data: bytes) -> bool:
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE: the clipboard owns it once set
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(kind, handle):
            kernel32.GlobalFree(handle)
            return False
        return True

    for _ in range(10):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        done = put(8, dib(canvas))  # CF_DIB: what most programs paste
        put(user32.RegisterClipboardFormatW("PNG"), canvas.png())  # browsers and editors take this one
        return done
    finally:
        user32.CloseClipboard()


def save(canvas: Canvas, name: str, folder: Path | None = None) -> Path:
    """The picture as a PNG in your Downloads (or the folder given)."""
    if folder is None:
        downloads = Path.home() / "Downloads"
        folder = downloads if downloads.is_dir() else Path.home()
    path = folder / name
    path.write_bytes(canvas.png())
    return path
