"""docs/banner.webp: what tabmancer is for, told in twelve seconds. Agents at work in their tabs,
Windows restarts and the tabs are gone, Bit calls them back, and the dashboard shows them all.

A page with a timeline is drawn frame by frame with headless Edge (the time comes in the address),
and the frames become one animated WebP. Windows only, like the real-window pictures it ends on.

usage: uv run --with pillow --with websockets python scripts/promo.py
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops

from tabmancer.mascot import ARM, ARMS, EYES, LEGS, PALETTE, SPRITE
from tabmancer.theme import AMBER, BLUE, GREEN, MUTED, RED, TEXT, THEMES

DOCS = Path(__file__).resolve().parent.parent / "docs"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
W, H = 1280, 640
LENGTH = 12.0  # seconds, then it starts again
FPS = 10
FONTS = ("https://fonts.googleapis.com/css2?family=Martian+Mono:wght@400;700;800"
         "&family=IBM+Plex+Sans:wght@400;500;600&display=swap")
ACCENT = THEMES["tabmancer-black"][1]
TABS = [  # (title, how it comes back)
    ("api-server", "resumed"), ("web-app", "resumed"), ("pcb-router", "cut off · carries on"),
    ("shop: lead", "resumed"), ("docs-site", "resumed"),
]

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<style>__FONTS__</style>
<style>
  :root { --bg: #0b0b0c; --panel: #161618; --tab: #1f1f22; --line: #2a2a2e; --text: __TEXT__; --muted: __MUTED__;
          --accent: __ACCENT__; --blue: __BLUE__; --green: __GREEN__; --amber: __AMBER__; --red: __RED__; }
  * { box-sizing: border-box; margin: 0; }
  html, body { width: __W__px; height: __H__px; overflow: hidden; background: var(--bg); }
  body { font-family: "IBM Plex Sans", "Segoe UI", sans-serif; color: var(--text); }
  .abs { position: absolute; }
  #scene { position: relative; width: __W__px; height: __H__px; overflow: hidden;
           background: radial-gradient(ellipse at 50% 120%, #1a1310 0%, var(--bg) 60%); }
  #caption { left: 0; right: 0; top: 46px; text-align: center; font: 600 34px/1.2 "IBM Plex Sans", sans-serif;
             letter-spacing: -0.01em; }
  #caption small { display: block; margin-top: 8px; font: 400 19px/1.3 "IBM Plex Sans", sans-serif; color: var(--muted); }
  #window { left: 140px; top: 200px; width: 1000px; height: 268px; border-radius: 10px; background: var(--panel);
            border: 1px solid var(--line); box-shadow: 0 30px 80px rgba(0,0,0,.6); overflow: hidden; }
  #strip { height: 44px; background: #0f0f11; display: flex; align-items: flex-end; padding: 0 10px; gap: 4px; }
  .tab { width: 178px; height: 36px; border-radius: 8px 8px 0 0; background: var(--tab); display: flex;
         align-items: center; gap: 10px; padding: 0 14px; font: 500 15px "Martian Mono", monospace; color: var(--text); }
  .tab .star { width: 16px; color: var(--blue); font-weight: 700; }
  #pane { padding: 18px 30px; font: 400 16px/1.8 "Martian Mono", monospace; color: var(--muted); }
  #pane b { color: var(--text); font-weight: 400; }
  .badge { font: 500 14px "IBM Plex Sans", sans-serif; padding: 3px 10px; border-radius: 999px; }
  #restart { left: 0; right: 0; top: 300px; text-align: center; font: 400 26px "IBM Plex Sans", sans-serif;
             color: #cfd3d8; letter-spacing: .02em; }
  #dots { display: inline-block; width: 40px; text-align: left; }
  #bit { left: 150px; top: 205px; image-rendering: pixelated; }
  #shot { left: 50%; top: 150px; width: 900px; border-radius: 10px; border: 1px solid var(--line);
          box-shadow: 0 30px 90px rgba(0,0,0,.7); transform-origin: 50% 0; }
  #name { left: 0; right: 0; text-align: center; font: 800 64px/1 "Martian Mono", monospace; letter-spacing: -0.03em; }
  #name span { color: var(--accent); }
  #install { left: 0; right: 0; text-align: center; font: 400 18px "Martian Mono", monospace; color: var(--muted); }
  #install b { color: var(--text); font-weight: 400; background: #17171a; border: 1px solid var(--line);
               padding: 6px 14px; border-radius: 8px; }
  .spark { width: 8px; height: 8px; background: var(--accent); }
</style></head><body><div id="scene">
  <div id="caption" class="abs"></div>
  <div id="window" class="abs"><div id="strip"></div><div id="pane"></div></div>
  <div id="restart" class="abs">Restarting<span id="dots"></span></div>
  <canvas id="bit" class="abs" width="22" height="17"></canvas>
  <img id="shot" class="abs" src="__SHOT__">
  <div id="name" class="abs"><span>tab</span>mancer</div>
  <div id="install" class="abs">every coding-agent session, one look &nbsp;·&nbsp; <b>uv tool install tabmancer</b></div>
  <div id="sparks"></div>
</div>
<script>
const T = parseFloat(new URLSearchParams(location.search).get("t") || "0");
const TABS = __TABS__;
const SPRITE = __SPRITE__, LEGS = __LEGS__, ARMS = __ARMS__, EYES = __EYES__, PALETTE = __PALETTE__, ARM = "__ARM__";
const STAR = "·✢✶✻✽✻✶✢";
const clamp = (x) => Math.max(0, Math.min(1, x));
const ease = (x) => 1 - Math.pow(1 - clamp(x), 3);
const span = (t, a, b) => ease((t - a) / (b - a));           // 0 before a, 1 after b
const pulse = (t, a, b, c, d) => span(t, a, b) * (1 - span(t, c, d));  // in a..b, out c..d
const $ = (id) => document.getElementById(id);
const star = (t, k) => STAR[Math.floor(t * 10 + k * 3) % STAR.length];

function caption(t) {
  const beats = [
    [0.2, 3.0, "Your coding agents, each in its tab", "Claude Code, Codex and Antigravity sessions, working away"],
    [3.3, 4.9, "Then Windows restarts", "an update, a crash, a lid closed: every tab is gone"],
    [5.1, 8.3, "tabmancer calls them back", "each in its own tab and folder; the ones cut off mid-reply carry on"],
  ];
  for (const [a, b, big, small] of beats) {
    const o = pulse(t, a, a + 0.35, b - 0.3, b);
    if (o > 0) { $("caption").innerHTML = big + "<small>" + small + "</small>"; $("caption").style.opacity = o; return; }
  }
  $("caption").style.opacity = 0;
}

function tabs(t) {
  const strip = $("strip"); strip.innerHTML = "";
  TABS.forEach(([title, back], k) => {
    const shown = span(t, 0.4 + k * 0.18, 0.7 + k * 0.18) * (1 - span(t, 3.4 + k * 0.12, 3.7 + k * 0.12))
                + span(t, 6.0 + k * 0.32, 6.3 + k * 0.32);
    const fall = span(t, 3.4 + k * 0.12, 3.75 + k * 0.12) * (1 - span(t, 5.9, 6.0));
    const el = document.createElement("div"); el.className = "tab";
    el.style.opacity = Math.min(1, shown);
    el.style.transform = `translateY(${fall * 70 - (1 - Math.min(1, shown)) * 8}px)`;
    const back_ = t > 6.0 + k * 0.32;
    const cut = back.startsWith("cut");
    const glyph = back_ && t < 7.6 + k * 0.1 ? (cut ? "▲" : "✓") : star(t, k);
    const colour = back_ && t < 7.6 + k * 0.1 ? (cut ? "var(--amber)" : "var(--green)") : "var(--blue)";
    el.innerHTML = `<span class="star" style="color:${colour}">${glyph}</span>${title}`;
    strip.appendChild(el);
  });
  const lines = t < 5.0
    ? [`<b>api-server</b>  ${star(t, 0)} adding rate limits to the public API`,
       `<b>web-app</b>     ${star(t, 1)} fixing the login redirect loop`,
       `<b>pcb-router</b>  ${star(t, 2)} routing the 3V3 plane on layer 3`,
       `<b>shop: lead</b>  ${star(t, 3)} handing out the checkout to its team`,
       `<b>docs-site</b>   ${star(t, 4)} rewriting the getting-started guide`]
    : [`<span style="color:var(--green)">✓ api-server</span>  resumed, its conversation as it was`,
       `<span style="color:var(--green)">✓ web-app</span>     resumed`,
       `<span style="color:var(--amber)">▲ pcb-router</span>  cut off mid-reply · <b>told to carry on</b>`,
       `<span style="color:var(--accent)">★ shop</span>        the team is back, members first, lead last`,
       `<span style="color:var(--green)">✓ docs-site</span>   resumed`];
  $("pane").innerHTML = lines.map((l, k) => `<div style="white-space:pre;opacity:${t < 5 ? span(t, 0.9 + k * .2, 1.2 + k * .2) : span(t, 6.1 + k * .32, 6.4 + k * .32)}">${l}</div>`).join("");
  const win = $("window");
  const up = span(t, 0.2, 0.7) * (1 - span(t, 4.3, 4.6)) + span(t, 5.5, 5.9) * (1 - span(t, 8.3, 8.7));
  win.style.opacity = up;
  win.style.left = (t > 5 ? 330 : 140) + "px";
  win.style.width = (t > 5 ? 900 : 1000) + "px";
  win.style.transform = `translateY(${(1 - up) * 24}px) scale(${0.98 + up * 0.02})`;
}

function restart(t) {
  const o = pulse(t, 4.3, 4.55, 5.0, 5.3);
  $("restart").style.opacity = o;
  $("dots").textContent = ".".repeat(1 + Math.floor(t * 4) % 3);
  $("scene").style.filter = `brightness(${1 - pulse(t, 4.25, 4.45, 4.9, 5.2) * 0.25})`;
}

function bit(t) {
  const c = $("bit"), g = c.getContext("2d");
  const shown = pulse(t, 5.2, 5.5, 8.2, 8.6);
  const scale = 9;
  c.style.width = 22 * scale + "px"; c.style.height = 17 * scale + "px";
  const hop = Math.max(0, Math.sin(Math.PI * clamp((t - 5.75) / 0.45))) * 14;
  c.style.opacity = shown;
  c.style.transform = `translateY(${(1 - shown) * 20 - hop}px)`;
  g.clearRect(0, 0, 22, 17);
  const rows = SPRITE.concat(LEGS.stand);
  rows.forEach((row, y) => [...row].forEach((key, x) => {
    if (PALETTE[key]) { g.fillStyle = PALETTE[key]; g.fillRect(x, y, 1, 1); }
  }));
  const raised = t > 5.75 && t < 7.4;
  for (const [row, col, len] of ARMS[raised ? "up" : "down"]) { g.fillStyle = ARM; g.fillRect(col, row, 1, len); }
  const blink = (t % 3.1) > 2.95;
  for (const [row, col] of EYES[raised ? "happy" : blink ? "blink" : "normal"]) { g.fillStyle = "#e6f4ff"; g.fillRect(col, row, 1, 1); }
  const glow = 0.55 + 0.45 * Math.sin(t * 5);
  g.globalAlpha = glow; g.fillStyle = "__ACCENT__";
  rows.forEach((row, y) => [...row].forEach((key, x) => { if (key === "a" || key === "c") g.fillRect(x, y, 1, 1); }));
  g.globalAlpha = 1;
  const sparks = $("sparks"); sparks.innerHTML = "";
  const p = clamp((t - 5.75) / 0.9);
  if (p > 0 && p < 1) for (let k = 0; k < 10; k++) {
    const a = -Math.PI * (0.15 + 0.7 * k / 9), r = 40 + p * 120;
    const s = document.createElement("div"); s.className = "spark abs";
    s.style.left = 150 + 99 + Math.cos(a) * r + "px"; s.style.top = 205 + 40 + Math.sin(a) * r + "px";
    s.style.opacity = 1 - p; s.style.background = k % 2 ? "__ACCENT__" : "#f4c38f";
    sparks.appendChild(s);
  }
}

function finale(t) {
  const up = span(t, 8.4, 9.2) * (1 - span(t, 11.5, 11.95));
  const shot = $("shot");
  shot.style.opacity = up;
  shot.style.transform = `translateX(-50%) translateY(${(1 - up) * 60}px) scale(${0.94 + up * 0.06})`;
  const name = $("name"), install = $("install");
  const n = span(t, 8.9, 9.5) * (1 - span(t, 11.5, 11.95));
  name.style.opacity = n; name.style.top = 40 + (1 - n) * 10 + "px";
  install.style.opacity = n; install.style.top = "118px";
  $("scene").style.opacity = span(t, 0, 0.25) * (1 - span(t, 11.75, 12));
}

function render(t) { caption(t); tabs(t); restart(t); bit(t); finale(t); }
window.render = render;
render(T);
document.fonts.ready.then(() => document.body.setAttribute("data-ready", "1"));
</script></body></html>"""


def fonts() -> str:
    """The two typefaces, their Latin faces only, inlined: each frame loads the page afresh, and
    fetching the fonts every time made a frame take seconds."""
    agent = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    css = urllib.request.urlopen(urllib.request.Request(FONTS, headers=agent)).read().decode()
    faces = [block for name, block in re.findall(r"/\* ([a-z-]+) \*/\s*(@font-face \{[^}]+\})", css) if name == "latin"]

    def inline(match: re.Match) -> str:
        data = urllib.request.urlopen(urllib.request.Request(match[1], headers=agent)).read()
        return f"url(data:font/woff2;base64,{base64.b64encode(data).decode()})"

    return "\n".join(re.sub(r"url\((https://[^)]+)\)", inline, face) for face in faces)


def page(shot: Path) -> str:
    replacements = {
        "__FONTS__": fonts(),
        "__W__": str(W), "__H__": str(H), "__TEXT__": TEXT, "__MUTED__": MUTED, "__ACCENT__": ACCENT,
        "__BLUE__": BLUE, "__GREEN__": GREEN, "__AMBER__": AMBER, "__RED__": RED, "__ARM__": ARM,
        "__TABS__": json.dumps(TABS), "__SPRITE__": json.dumps(SPRITE), "__LEGS__": json.dumps(LEGS),
        "__ARMS__": json.dumps(ARMS), "__EYES__": json.dumps(EYES), "__PALETTE__": json.dumps(PALETTE),
        "__SHOT__": "data:image/png;base64," + base64.b64encode(shot.read_bytes()).decode(),
    }
    out = PAGE
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


class LoadingError(Exception):
    """The page was loading when it was asked something."""


class Edge:
    """One headless Edge with the page open, driven over its DevTools protocol (on 127.0.0.1, for as
    long as this runs): each frame is the page's render(t), then a screenshot. Starting Edge for every
    frame took seconds a frame."""

    PORT = 9339

    def __init__(self, url: str, profile: Path) -> None:
        from websockets.sync.client import connect

        self.profile = profile
        self.process = subprocess.Popen([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                         f"--remote-debugging-port={self.PORT}", f"--user-data-dir={profile}",
                                         f"--window-size={W},{H}", url],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.PORT}/json", timeout=1))
                target = next(p for p in pages if p.get("type") == "page")
                break
            except (OSError, StopIteration, ValueError):
                time.sleep(0.1)
        else:
            raise SystemExit("Edge did not start")
        self.socket = connect(target["webSocketDebuggerUrl"], max_size=None)
        self.calls = 0
        self.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        for _ in range(100):  # the page may still be loading: until it is, and its fonts with it
            if self.evaluate("document.readyState === 'complete' && typeof render === 'function'"
                             ).get("result", {}).get("value"):
                break
            time.sleep(0.1)
        self.evaluate("document.fonts.ready.then(() => true)")

    def call(self, method: str, **params):
        self.calls += 1
        self.socket.send(json.dumps({"id": self.calls, "method": method, "params": params}))
        while True:
            answer = json.loads(self.socket.recv())
            if answer.get("id") == self.calls:
                if "error" in answer:
                    if "context was destroyed" in str(answer["error"]) or "Cannot find context" in str(answer["error"]):
                        raise LoadingError(method)
                    raise SystemExit(f"{method}: {answer['error']}")
                return answer.get("result", {})

    def evaluate(self, expression: str):
        for _ in range(50):
            try:
                return self.call("Runtime.evaluate", expression=expression, awaitPromise=True)
            except LoadingError:  # asked while the page was (re)loading: once it has
                time.sleep(0.1)
        raise SystemExit(f"the page never answered: {expression}")

    def frame(self, t: float) -> Image.Image:
        self.evaluate(f"render({t:.4f})")
        # Painted before the picture: two animation frames on (the first frame came out of the last state).
        self.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        shot = self.call("Page.captureScreenshot", format="png",
                         clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
        return Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")

    def close(self) -> None:
        with contextlib.suppress(Exception):  # gone already
            self.call("Browser.close")
            self.socket.close()
        # Edge's helpers (its crash reporter) can outlive it: every process started with this run's own
        # profile folder is ended, and no other.
        script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{self.profile}*' }} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, check=False)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=10)


def main() -> None:
    times = [k / FPS for k in range(int(LENGTH * FPS))]
    looking = len(sys.argv) > 1  # a few moments only, to look at them: promo.py 1.0 4.5 7 10
    if looking:
        times = [float(t) for t in sys.argv[1:]]
    with tempfile.TemporaryDirectory(prefix="tabmancer-promo-", ignore_cleanup_errors=True) as tmp:
        html = Path(tmp) / "promo.html"
        html.write_text(page(DOCS / "picker.png"), encoding="utf-8")
        edge = Edge(html.as_uri(), Path(tmp) / "profile")
        try:
            frames = [edge.frame(t) for t in times]
        finally:
            edge.close()
    if looking:
        for t, frame in zip(times, frames):
            frame.save(Path(tempfile.gettempdir()) / f"promo-{t:g}.png")
        print("wrote", ", ".join(f"promo-{t:g}.png" for t in times), "to", tempfile.gettempdir())
        return
    # A frame like the one before only makes that one last longer: merged here, its time added, because
    # the encoder dropped such frames with their time (twelve seconds came out as under ten).
    kept, durations = [frames[0]], [round(1000 / FPS)]
    for frame in frames[1:]:
        if ImageChops.difference(frame, kept[-1]).getbbox() is None:
            durations[-1] += round(1000 / FPS)
        else:
            kept.append(frame)
            durations.append(round(1000 / FPS))
    out = DOCS / "banner.webp"
    kept[0].save(out, save_all=True, append_images=kept[1:], duration=durations, loop=0, quality=85, method=6)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
