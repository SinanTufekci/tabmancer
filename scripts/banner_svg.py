"""docs/banner.svg: a calm banner for the README and the profile, 1720x340 (the height of a row of the
profile's two-column project table at full width): Bit in its own pixels (bitart), a small dashboard of
sessions, the 14-day token chart. Three slow movements only: Bit blinks now and then, the working star
turns, the waiting dot breathes (docs/banner.webp, the moving story, is scripts/promo.py's).

usage: uv run python scripts/banner_svg.py [out.svg]"""

import sys
from pathlib import Path

from tabmancer import bitart

W, H = 1720, 340
ACCENT = (217, 119, 87)
MONO = "ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, Consolas, monospace"
SANS = "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"


def hexc(c):
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def bit_pixels(eyes: str):
    pose = bitart.themed(bitart.Pose(eyes=eyes, beacon=0.9, chest=0.8), ACCENT)
    return bitart.frame_pixels(bitart.Frame(pose))


def runs(pixels, x0, y0, scale, only=None):
    """Horizontal runs of one colour as rects, crisp; only: the (x, y) pixels to draw."""
    out = []
    for y, row in enumerate(pixels):
        x = 0
        while x < len(row):
            c = row[x]
            if c is None or (only is not None and (x, y) not in only):
                x += 1
                continue
            end = x
            while end + 1 < len(row) and row[end + 1] == c and (only is None or (end + 1, y) in only):
                end += 1
            out.append(f'<rect x="{x0 + x * scale:g}" y="{y0 + y * scale:g}" width="{(end - x + 1) * scale:g}" '
                       f'height="{scale:g}" fill="{hexc(c)}"/>')
            x = end + 1
    return "".join(out)


def main(out_path):
    scale = 2.4
    bx, by = 120, (H - 78 * scale) / 2 + 6
    open_eyes, closed = bit_pixels("open"), bit_pixels("blink")
    blink_at = {(x, y) for y, row in enumerate(open_eyes) for x, c in enumerate(row) if c != closed[y][x]}
    bit = runs(open_eyes, bx, by, scale)
    lids = runs(closed, bx, by, scale, only=blink_at)

    rows = [  # project, title, status words, colour, glyph
        ("web-app", "Fix the login redirect loop", "waiting for you", "#f2665c", "dot"),
        ("api-server", "Rate limiting for the public API", "working", "#6cb4ff", "star"),
        ("pcb-router", "Route the power plane, 4 layers", "cut off", "#e9a23b", "▲"),
        ("ml-experiments", "Compare three tokenizer settings", "limit · 20:00", "#e9a23b", "◐"),
        ("docs-site", "Rewrite the getting-started guide", "done", "#7d8590", "√"),
    ]
    px, py, pw = 470, 38, 820  # the dashboard panel
    lines = []
    for i, (project, title, words, colour, glyph) in enumerate(rows):
        y = py + 90 + i * 35
        sx = px + 590
        if glyph == "dot":
            mark = (f'<circle cx="{sx + 5}" cy="{y - 5}" r="5" fill="{colour}">'
                    '<animate attributeName="opacity" values="1;0.35;1" dur="2.8s" repeatCount="indefinite"/></circle>')
        elif glyph == "star":
            mark = (f'<g transform="translate({sx + 5} {y - 5})"><g>'
                    f'<path d="M0,-7 L1.6,-1.6 7,0 1.6,1.6 0,7 -1.6,1.6 -7,0 -1.6,-1.6 Z" fill="{colour}"/>'
                    '<animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="6s" '
                    'repeatCount="indefinite"/></g></g>')
        else:
            mark = f'<text x="{sx}" y="{y}" fill="{colour}" font-size="16">{glyph}</text>'
        if i == 1:  # the highlighted row
            lines.append(f'<rect x="{px + 14}" y="{y - 24}" width="{pw - 28}" height="34" rx="6" fill="#d97757" '
                         'fill-opacity="0.12"/>')
        lines.append(f'<text x="{px + 34}" y="{y}" fill="#e6e3dd" font-size="16">{project}</text>'
                     f'<text x="{px + 210}" y="{y}" fill="#9aa3ad" font-size="16">{title}</text>'
                     f'{mark}<text x="{sx + 20}" y="{y}" fill="{colour}" font-size="16">{words}</text>')

    days = [0, 0, 0.6, 1.4, 1.1, 0, 2.2, 3.1, 2.6, 0.4, 3.8, 5.1, 4.4, 6.0]
    cx, cy, cw, ch = 1390, 112, 250, 150  # the chart
    peak = max(days)
    pitch = cw / len(days)
    bars = []
    for n, v in enumerate(days):
        if not v:
            continue
        h = max(4, v / peak * (ch - 8))
        t = v / peak
        c = tuple(round(a + (b - a) * t) for a, b in zip((74, 64, 60), ACCENT))
        bars.append(f'<rect x="{cx + n * pitch + 2.5:.1f}" y="{cy + ch - h:.1f}" width="{pitch - 5:.1f}" '
                    f'height="{h:.1f}" rx="2.5" fill="{hexc(c)}"/>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">
<title id="t">tabmancer</title>
<desc id="d">Bit, tabmancer's robot, beside a small dashboard of coding-agent sessions (one waits for you, one works, one was cut off, one stopped at a usage limit, one is done) and a chart of the tokens they used over 14 days.</desc>
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d1320"/><stop offset="1" stop-color="#080b12"/></linearGradient>
<radialGradient id="glow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#d97757" stop-opacity="0.32"/><stop offset="1" stop-color="#d97757" stop-opacity="0"/></radialGradient>
<radialGradient id="beacon" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#ff9a6e" stop-opacity="0.9"/><stop offset="1" stop-color="#ff9a6e" stop-opacity="0"/></radialGradient>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#ffffff" stroke-opacity="0.025"/></pattern>
</defs>
<rect width="{W}" height="{H}" fill="url(#bg)"/>
<rect width="{W}" height="{H}" fill="url(#grid)"/>
<circle cx="{bx + 55 * scale}" cy="{by + 42 * scale}" r="190" fill="url(#glow)"/>
<circle cx="{bx + 55 * scale}" cy="{by + 10 * scale}" r="26" fill="url(#beacon)" opacity="0.5"><animate attributeName="opacity" values="0.25;0.7;0.25" dur="3.6s" repeatCount="indefinite"/></circle>
<g shape-rendering="crispEdges">{bit}</g>
<g shape-rendering="crispEdges" opacity="0">{lids}<animate attributeName="opacity" values="0;1;0" keyTimes="0;0.955;0.985" calcMode="discrete" dur="7s" repeatCount="indefinite"/></g>
<rect x="{px}" y="{py}" width="{pw}" height="{H - 2 * py}" rx="14" fill="#0f1623" stroke="#ffffff" stroke-opacity="0.08"/>
<g font-family="{MONO}">
<text x="{px + 34}" y="{py + 38}" font-size="17" font-weight="600" fill="#e07a55">◆ tabmancer</text>
<text x="{px + 175}" y="{py + 38}" font-size="15" fill="#7d8590">13 sessions, last 14 days</text>
<text x="{px + 590}" y="{py + 38}" font-size="15" fill="#7d8590">session</text>
<rect x="{px + 664}" y="{py + 27}" width="80" height="10" rx="3" fill="#1f2a3a"/><rect x="{px + 664}" y="{py + 27}" width="27" height="10" rx="3" fill="#4cc38a"/>
<text x="{px + 752}" y="{py + 38}" font-size="15" fill="#4cc38a">34%</text>
<line x1="{px + 20}" y1="{py + 56}" x2="{px + pw - 20}" y2="{py + 56}" stroke="#ffffff" stroke-opacity="0.07"/>
{"".join(lines)}
</g>
<text x="{cx}" y="{cy - 22}" font-family="{SANS}" font-size="14" font-weight="600" letter-spacing="2.5" fill="#7d8aa0">TOKENS · 14 DAYS</text>
{"".join(bars)}
<line x1="{cx}" y1="{cy + ch + 1}" x2="{cx + cw}" y2="{cy + ch + 1}" stroke="#ffffff" stroke-opacity="0.12"/>
</svg>
'''
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg)
    print(len(svg), "bytes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "docs" / "banner.svg"))
