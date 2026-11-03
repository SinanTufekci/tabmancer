"""docs/banner.svg: the banner of the README and the profile, 1720x340 (the height of a row of the
profile's two-column project table at full width): Bit, a small dashboard of sessions, the 14-day token
chart. Bit is the dashboard's own, going through its moves in a 41-second loop (bit_sprites.py): it
waves, walks, has a coffee, whistles, works at its laptop, thinks, has an idea, cheers, walks back,
dances, spins, gets dizzy, stretches, yawns, nods off and sleeps, wakes with a hop, looks about.
Otherwise the working star turns and the waiting dot breathes (docs/banner.webp, the moving story, is
scripts/promo.py's).

usage: uv run python scripts/banner_svg.py [out.svg]"""

import sys
from pathlib import Path

import bit_sprites
from bit_sprites import Move, play

W, H = 1720, 340
ACCENT = (217, 119, 87)
MONO = "ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, Consolas, monospace"
SANS = "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"


def hexc(c):
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


# Bit's loop: (move, frames at 20 a second); it walks right and back again, so it ends where it began.
MOVES = [Move("idle", 30), Move("wave", 36, eyes="happy"), Move("walk", 26, direction=1), Move("coffee", 80),
         Move("whistle", 50), Move("work", 80), Move("think", 40), Move("idea", 30),
         Move("cheer", 40, jumps=2, eyes="happy", burst=True), Move("walk", 26, direction=-1),
         Move("dance", 60, eyes="happy", burst=True), Move("spin", 30, burst=True), Move("dizzy", 40),
         Move("stretch", 40), Move("yawn", 40), Move("nod", 50), Move("sleep", 70, eyes="sleep"),
         Move("wake", 24, jumps=1), Move("look", 30, eyes="left"), Move("idle", 30)]


def main(out_path):
    scale = 2.4
    bx, by = 90, (H - 78 * scale) / 2 + 6
    sprite_defs, bit, _ = bit_sprites.svg(play(MOVES, hexc(ACCENT)), bx, by, scale)

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
{sprite_defs}
</defs>
<rect width="{W}" height="{H}" fill="url(#bg)"/>
<rect width="{W}" height="{H}" fill="url(#grid)"/>
<circle cx="{bx + 75 * scale}" cy="{by + 42 * scale}" r="210" fill="url(#glow)"/>
{bit}
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
