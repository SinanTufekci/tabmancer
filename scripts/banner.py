"""Draw docs/banner.svg: Bit, the name, and a few sessions changing state, one of them called back
from the dead. An SVG with CSS animation, so it moves in the README with no script and no GIF.

usage: python scripts/banner.py
"""

from __future__ import annotations

from pathlib import Path

from tabmancer.mascot import ARM, ARMS, EYES, LEGS, PALETTE, SPRITE
from tabmancer.theme import AMBER, BLUE, GREEN, MUTED, RED, TEXT, THEMES, mix

DOCS = Path(__file__).resolve().parent.parent / "docs"
W, H = 1200, 300
LOOP = 9  # seconds
ACCENT = THEMES["tabmancer-black"][1]
PIXEL = 9  # Bit's pixel, in the banner's units
MONO = "ui-monospace, 'Cascadia Mono', 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
EYE = "#e6f4ff"
# The moment pcb-router is called back: Bit raises its arms, sparks fly, the row comes alive.
RAISE = 0.52


def pixels(rows: list[str], x0: float, y0: float, colours: dict[str, str], cls: str = "") -> str:
    """Rects for every coloured pixel of a sprite, runs of one colour in a row merged."""
    out = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            key = row[x]
            if key not in colours:
                x += 1
                continue
            end = x
            while end < len(row) and row[end] == key:
                end += 1
            klass = f' class="{cls}-{key}"' if cls and key in "ac" else ""
            out.append(f'<rect x="{x0 + x * PIXEL}" y="{y0 + y * PIXEL}" width="{(end - x) * PIXEL}" '
                       f'height="{PIXEL}" fill="{colours[key]}"{klass}/>')
            x = end
    return "".join(out)


def arms(pose: str, x0: float, y0: float) -> str:
    out = []
    for row, col, length in ARMS[pose]:
        out.append(f'<rect x="{x0 + col * PIXEL}" y="{y0 + row * PIXEL}" width="{PIXEL}" '
                   f'height="{length * PIXEL}" fill="{ARM}"/>')
    return "".join(out)


def eyes(kind: str, x0: float, y0: float) -> str:
    return "".join(f'<rect x="{x0 + col * PIXEL}" y="{y0 + row * PIXEL}" width="{PIXEL}" height="{PIXEL}" '
                   f'fill="{EYE}"/>' for row, col in EYES[kind])


def bit(x0: float, y0: float) -> str:
    body = SPRITE + LEGS["stand"]
    return "".join([
        '<g class="bit">',
        pixels(body, x0, y0, PALETTE, cls="light"),
        f'<g class="eyes-open">{eyes("normal", x0, y0)}</g>',
        f'<g class="eyes-shut">{eyes("blink", x0, y0)}</g>',
        f'<g class="arms-down">{arms("down", x0, y0)}</g>',
        f'<g class="arms-up">{arms("up", x0, y0)}</g>',
        "</g>",
    ])


def sparks(cx: float, cy: float) -> str:
    """A ring of little squares that fly out from the row being called back."""
    out = []
    for i, (dx, dy) in enumerate(((-34, -16), (-20, -26), (0, -30), (22, -24), (36, -12), (-30, 10), (30, 12))):
        colour = ACCENT if i % 2 else "#f4c38f"
        out.append(f'<rect class="spark" x="{cx - 3}" y="{cy - 3}" width="6" height="6" fill="{colour}" '
                   f'style="--dx:{dx}px;--dy:{dy}px;animation-delay:{RAISE * LOOP + i * 0.03:.2f}s"/>')
    return "".join(out)


def text(x: float, y: float, words: str, colour: str, size: int = 17, weight: str = "normal",
         cls: str = "", extra: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return (f'<text x="{x}" y="{y}" fill="{colour}" font-size="{size}" font-weight="{weight}"{klass}{extra}>'
            f"{words}</text>")


def draw() -> str:
    bit_x, bit_y = 70, 70
    left = 330  # the name and what it does
    panel_x, panel_y, panel_w, panel_h = 760, 64, 400, 172
    row = [panel_y + 62, panel_y + 98, panel_y + 134]
    name_x, status_x = panel_x + 22, panel_x + 220
    badge = mix("#000000", AMBER, 0.22)
    fade = f"{LOOP}s infinite"
    css = f"""
    text {{ font-family: {MONO}; }}
    .bit {{ animation: hop {fade}; transform-box: fill-box; transform-origin: center bottom; }}
    .light-a, .light-c {{ animation: glow 1.8s ease-in-out infinite; }}
    .eyes-shut {{ opacity: 0; animation: shut {fade}; }}
    .eyes-open {{ animation: open {fade}; }}
    .arms-up {{ opacity: 0; animation: up {fade}; }}
    .arms-down {{ animation: down {fade}; }}
    .spark {{ opacity: 0; animation: spark {LOOP}s ease-out infinite; transform-box: fill-box; }}
    .s-work {{ animation: s-work {fade}; }}
    .s-wait {{ opacity: 0; animation: s-wait {fade}; }}
    .s-done {{ opacity: 0; animation: s-done {fade}; }}
    .r-dead {{ animation: r-dead {fade}; }}
    .r-alive {{ opacity: 0; animation: r-alive {fade}; }}
    .star {{ animation: turn 1.2s linear infinite; transform-box: fill-box; transform-origin: center; }}
    .chip {{ animation: glow 2.4s ease-in-out infinite; }}
    .halo {{ opacity: 0; animation: halo {fade}; }}
    @keyframes glow {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.45; }} }}
    @keyframes turn {{ to {{ transform: rotate(360deg); }} }}
    @keyframes hop {{ 0%, {RAISE * 100 - 3:.0f}%, {RAISE * 100 + 8:.0f}%, 100% {{ transform: translateY(0); }}
                      {RAISE * 100:.0f}% {{ transform: translateY(-14px); }} }}
    @keyframes open {{ 0%, 31%, 34%, 79%, 82%, 100% {{ opacity: 1; }} 32%, 33%, 80%, 81% {{ opacity: 0; }} }}
    @keyframes shut {{ 0%, 31%, 34%, 79%, 82%, 100% {{ opacity: 0; }} 32%, 33%, 80%, 81% {{ opacity: 1; }} }}
    @keyframes up {{ 0%, {RAISE * 100 - 4:.0f}%, {RAISE * 100 + 14:.0f}%, 100% {{ opacity: 0; }}
                     {RAISE * 100 - 3:.0f}%, {RAISE * 100 + 13:.0f}% {{ opacity: 1; }} }}
    @keyframes down {{ 0%, {RAISE * 100 - 4:.0f}%, {RAISE * 100 + 14:.0f}%, 100% {{ opacity: 1; }}
                       {RAISE * 100 - 3:.0f}%, {RAISE * 100 + 13:.0f}% {{ opacity: 0; }} }}
    @keyframes spark {{ 0% {{ opacity: 0; transform: translate(0, 0); }}
                        2% {{ opacity: 1; }}
                        14% {{ opacity: 0; transform: translate(var(--dx), var(--dy)); }}
                        100% {{ opacity: 0; transform: translate(var(--dx), var(--dy)); }} }}
    @keyframes s-work {{ 0%, 30% {{ opacity: 1; }} 33%, 97% {{ opacity: 0; }} 100% {{ opacity: 1; }} }}
    @keyframes s-wait {{ 0%, 30% {{ opacity: 0; }} 33%, 63% {{ opacity: 1; }} 66%, 100% {{ opacity: 0; }} }}
    @keyframes s-done {{ 0%, 63% {{ opacity: 0; }} 66%, 97% {{ opacity: 1; }} 100% {{ opacity: 0; }} }}
    @keyframes r-dead {{ 0%, {RAISE * 100:.0f}% {{ opacity: 1; }} {RAISE * 100 + 2:.0f}%, 97% {{ opacity: 0; }}
                         100% {{ opacity: 1; }} }}
    @keyframes r-alive {{ 0%, {RAISE * 100:.0f}% {{ opacity: 0; }} {RAISE * 100 + 2:.0f}%, 97% {{ opacity: 1; }}
                          100% {{ opacity: 0; }} }}
    @keyframes halo {{ 0%, {RAISE * 100:.0f}% {{ opacity: 0; }} {RAISE * 100 + 2:.0f}% {{ opacity: 0.9; }}
                       {RAISE * 100 + 16:.0f}%, 100% {{ opacity: 0; }} }}
    @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
    """
    parts = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        f'aria-label="tabmancer: Bit beside a few coding-agent sessions, one called back after it was cut off">'),
        f"<style>{css}</style>",
        f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="18" fill="#0b0b0c" stroke="#1d1d20"/>',
        # Bit on a ground line of its own
        bit(bit_x, bit_y),
        f'<rect x="{bit_x - 10}" y="{bit_y + 17 * PIXEL + 4}" width="{22 * PIXEL + 20}" height="2" fill="#26262a"/>',
        # the name and what it does
        text(left, 128, f'<tspan fill="{ACCENT}">tab</tspan>mancer', TEXT, 62, "bold"),
        text(left, 170, "raises your coding-agent sessions", MUTED, 19),
        text(left, 197, "from the dead.", MUTED, 19),
        text(left, 236, "a terminal dashboard for", "#6e7681", 14),
        text(left, 256, "Claude Code, Codex and Antigravity", "#6e7681", 14),
        # the sessions, as the dashboard lists them
        (f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="10" fill="#101012" '
        f'stroke="#1d1d20"/>'),
        text(name_x, panel_y + 28, "OPEN NOW", GREEN, 14, "bold"), text(name_x + 82, panel_y + 28, "· 3", MUTED, 14),
        # api-server: working, then waiting for you, then done
        text(name_x, row[0], "api-server", TEXT),
        (f'<g class="s-work"><text x="{status_x}" y="{row[0]}" fill="{BLUE}" font-size="17" class="star">✶</text>'
        f'{text(status_x + 20, row[0], "working", BLUE)}</g>'),
        f'<g class="s-wait">{text(status_x, row[0], "● waiting for you", RED)}</g>',
        f'<g class="s-done">{text(status_x, row[0], "✓ done", MUTED)}</g>',
        # a team, as one row of chips
        text(name_x, row[1], f'<tspan fill="{MUTED}">TEAM</tspan> shop', TEXT),
        text(status_x, row[1], f'<tspan fill="{ACCENT}">★</tspan>lead<tspan fill="{RED}" class="chip">●</tspan> '
                               f'api<tspan fill="{BLUE}">✶</tspan> ui<tspan fill="{RED}" class="chip">●</tspan>',
             TEXT),
        # pcb-router: cut off, then called back
        (f'<rect class="halo" x="{panel_x + 8}" y="{row[2] - 22}" width="{panel_w - 16}" height="30" rx="6" '
        f'fill="{mix("#000000", ACCENT, 0.35)}"/>'),
        text(name_x, row[2], "pcb-router", TEXT),
        (f'<g class="r-dead"><rect x="{status_x - 4}" y="{row[2] - 16}" width="98" height="22" rx="4" fill="{badge}"/>'
        f'{text(status_x, row[2], "▲ cut off", AMBER, 17, "bold")}</g>'),
        (f'<g class="r-alive"><text x="{status_x}" y="{row[2]}" fill="{BLUE}" font-size="17" class="star">✶</text>'
        f'{text(status_x + 20, row[2], "working again", BLUE)}</g>'),
        sparks(status_x + 60, row[2] - 6),
        # the footer's keys, as the dashboard shows them
        text(name_x, panel_y + panel_h - 14, f'<tspan fill="{ACCENT}" font-weight="bold">⏎</tspan> Open  '
                                             f'<tspan fill="{ACCENT}" font-weight="bold">1·2·3</tspan> Cards  '
                                             f'<tspan fill="{ACCENT}" font-weight="bold">h</tspan> Hand over  '
                                             f'<tspan fill="{ACCENT}" font-weight="bold">?</tspan> Keys', MUTED, 13),
        "</svg>",
    ]
    return "".join(parts)


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    (DOCS / "banner.svg").write_text(draw(), encoding="utf-8")
    print(f"wrote {DOCS / 'banner.svg'}")


if __name__ == "__main__":
    main()
