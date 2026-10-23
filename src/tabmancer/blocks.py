"""Characters for drawing 2x4 pixels per terminal cell (Unicode 16 block octants).

OCTANTS[mask] draws the pixels whose bits are set in mask, with bit 0 the top-left
pixel, bit 1 the top-right, then row by row down to bit 7 at the bottom right. Most
patterns are in the Block Octants block (U+1CD00); the rest reuse Block Elements and
the quarter blocks of the Legacy Computing blocks, so every pattern is one character
drawn in its own colours. (Drawing a pattern as its inverse with the colours swapped
leaves a hairline seam in Windows Terminal, so that trick is not used.)

Generated from the Unicode 16 character names, so it works on Pythons whose
unicodedata predates the octants.

A cell shows two colours only, so eight pixels go into a cell as the two colours that
change them least (octant), or, where the terminal has no octants, as two halves of
four pixels each (half). Drawn twice the size, a cell holds just two pixels, one over the
other, and both are kept (pair).
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

BACKGROUND = "#000000"  # Bit's strip: where nothing is drawn

_CODES = (
    0x00020, 0x1CEA8, 0x1CEAB, 0x1FB82, 0x1CD00, 0x02598, 0x1CD01, 0x1CD02,
    0x1CD03, 0x1CD04, 0x0259D, 0x1CD05, 0x1CD06, 0x1CD07, 0x1CD08, 0x02580,
    0x1CD09, 0x1CD0A, 0x1CD0B, 0x1CD0C, 0x1FBE6, 0x1CD0D, 0x1CD0E, 0x1CD0F,
    0x1CD10, 0x1CD11, 0x1CD12, 0x1CD13, 0x1CD14, 0x1CD15, 0x1CD16, 0x1CD17,
    0x1CD18, 0x1CD19, 0x1CD1A, 0x1CD1B, 0x1CD1C, 0x1CD1D, 0x1CD1E, 0x1CD1F,
    0x1FBE7, 0x1CD20, 0x1CD21, 0x1CD22, 0x1CD23, 0x1CD24, 0x1CD25, 0x1CD26,
    0x1CD27, 0x1CD28, 0x1CD29, 0x1CD2A, 0x1CD2B, 0x1CD2C, 0x1CD2D, 0x1CD2E,
    0x1CD2F, 0x1CD30, 0x1CD31, 0x1CD32, 0x1CD33, 0x1CD34, 0x1CD35, 0x1FB85,
    0x1CEA3, 0x1CD36, 0x1CD37, 0x1CD38, 0x1CD39, 0x1CD3A, 0x1CD3B, 0x1CD3C,
    0x1CD3D, 0x1CD3E, 0x1CD3F, 0x1CD40, 0x1CD41, 0x1CD42, 0x1CD43, 0x1CD44,
    0x02596, 0x1CD45, 0x1CD46, 0x1CD47, 0x1CD48, 0x0258C, 0x1CD49, 0x1CD4A,
    0x1CD4B, 0x1CD4C, 0x0259E, 0x1CD4D, 0x1CD4E, 0x1CD4F, 0x1CD50, 0x0259B,
    0x1CD51, 0x1CD52, 0x1CD53, 0x1CD54, 0x1CD55, 0x1CD56, 0x1CD57, 0x1CD58,
    0x1CD59, 0x1CD5A, 0x1CD5B, 0x1CD5C, 0x1CD5D, 0x1CD5E, 0x1CD5F, 0x1CD60,
    0x1CD61, 0x1CD62, 0x1CD63, 0x1CD64, 0x1CD65, 0x1CD66, 0x1CD67, 0x1CD68,
    0x1CD69, 0x1CD6A, 0x1CD6B, 0x1CD6C, 0x1CD6D, 0x1CD6E, 0x1CD6F, 0x1CD70,
    0x1CEA0, 0x1CD71, 0x1CD72, 0x1CD73, 0x1CD74, 0x1CD75, 0x1CD76, 0x1CD77,
    0x1CD78, 0x1CD79, 0x1CD7A, 0x1CD7B, 0x1CD7C, 0x1CD7D, 0x1CD7E, 0x1CD7F,
    0x1CD80, 0x1CD81, 0x1CD82, 0x1CD83, 0x1CD84, 0x1CD85, 0x1CD86, 0x1CD87,
    0x1CD88, 0x1CD89, 0x1CD8A, 0x1CD8B, 0x1CD8C, 0x1CD8D, 0x1CD8E, 0x1CD8F,
    0x02597, 0x1CD90, 0x1CD91, 0x1CD92, 0x1CD93, 0x0259A, 0x1CD94, 0x1CD95,
    0x1CD96, 0x1CD97, 0x02590, 0x1CD98, 0x1CD99, 0x1CD9A, 0x1CD9B, 0x0259C,
    0x1CD9C, 0x1CD9D, 0x1CD9E, 0x1CD9F, 0x1CDA0, 0x1CDA1, 0x1CDA2, 0x1CDA3,
    0x1CDA4, 0x1CDA5, 0x1CDA6, 0x1CDA7, 0x1CDA8, 0x1CDA9, 0x1CDAA, 0x1CDAB,
    0x02582, 0x1CDAC, 0x1CDAD, 0x1CDAE, 0x1CDAF, 0x1CDB0, 0x1CDB1, 0x1CDB2,
    0x1CDB3, 0x1CDB4, 0x1CDB5, 0x1CDB6, 0x1CDB7, 0x1CDB8, 0x1CDB9, 0x1CDBA,
    0x1CDBB, 0x1CDBC, 0x1CDBD, 0x1CDBE, 0x1CDBF, 0x1CDC0, 0x1CDC1, 0x1CDC2,
    0x1CDC3, 0x1CDC4, 0x1CDC5, 0x1CDC6, 0x1CDC7, 0x1CDC8, 0x1CDC9, 0x1CDCA,
    0x1CDCB, 0x1CDCC, 0x1CDCD, 0x1CDCE, 0x1CDCF, 0x1CDD0, 0x1CDD1, 0x1CDD2,
    0x1CDD3, 0x1CDD4, 0x1CDD5, 0x1CDD6, 0x1CDD7, 0x1CDD8, 0x1CDD9, 0x1CDDA,
    0x02584, 0x1CDDB, 0x1CDDC, 0x1CDDD, 0x1CDDE, 0x02599, 0x1CDDF, 0x1CDE0,
    0x1CDE1, 0x1CDE2, 0x0259F, 0x1CDE3, 0x02586, 0x1CDE4, 0x1CDE5, 0x02588,
)
OCTANTS = "".join(map(chr, _CODES))


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


@lru_cache(maxsize=8192)
def _distance(a: str, b: str) -> int:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b)))


def octant_cell(block: list[str] | tuple[str, ...], important: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    return octant(tuple(block), important)


@lru_cache(maxsize=1 << 16)  # cells repeat frame after frame
def octant(block: tuple[str, ...], important: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """Eight pixel colours (2 wide x 4 tall, row by row) as one character and its colours.

    A cell has only two colours. Every pair of the colours present is tried, and the
    pair that changes the picture least wins: each pixel snaps to the nearer of the
    two, and the error counts twice over for features (the eyes, the lights), so
    the face survives a crowded cell without everything around it taking its colour.
    """
    counts = Counter(block)
    if len(counts) == 1:
        return " ", BACKGROUND, block[0]

    def error(pair: tuple[str, str]) -> int:
        return sum(n * (2 if colour in important else 1) * min(_distance(colour, pair[0]), _distance(colour, pair[1]))
                   for colour, n in counts.items())

    colours = list(counts)
    fg, bg = min(((a, b) for i, a in enumerate(colours) for b in colours[i + 1:]), key=error)
    if fg == BACKGROUND:
        fg, bg = bg, fg
    mask = 0
    for bit, colour in enumerate(block):
        if colour == fg or (colour != bg and _distance(colour, fg) < _distance(colour, bg)):
            mask |= 1 << bit
    return OCTANTS[mask], fg, bg


@lru_cache(maxsize=1 << 16)
def half(block: tuple[str, ...]) -> tuple[str, str, str]:
    """The same eight pixels as an upper and a lower half, each in its commonest colour."""
    top = Counter(block[:4]).most_common(1)[0][0]
    bottom = Counter(block[4:]).most_common(1)[0][0]
    return ("▀", top, bottom) if top != bottom else (" ", top, bottom)


def pair(block: tuple[str, ...]) -> tuple[str, str, str]:
    """Two pixels, one over the other, as the halves of a cell: each kept as it is."""
    top, bottom = block
    return ("▀", top, bottom) if top != bottom else (" ", top, bottom)