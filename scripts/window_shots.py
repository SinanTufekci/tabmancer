"""Pictures of the dashboard in a real Windows Terminal window: the made-up sessions, the scenes of
scripts/demo_window.py, a still and a few quick frames of each. Windows only.

usage: uv run --with pillow python scripts/window_shots.py <out dir>
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
SCENES = 12
FRAMES = 10  # quick frames of each scene, for the moving picture
FRAME_GAP = 0.12
WT = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "wt.exe"
WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
WM_CLOSE = 0x0010

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDPIAware()


def terminal_windows() -> set[int]:
    found = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == WINDOW_CLASS and user32.IsWindowVisible(hwnd):
            found.add(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def picture(hwnd: int) -> Image.Image:
    """The window as it draws itself (PrintWindow with PW_RENDERFULLCONTENT): no other window on top
    gets into it, and it need not be in front."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bitmap)
    user32.PrintWindow(hwnd, mem, 2)
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    data = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bitmap, 0, h, data, ctypes.byref(info), 0)
    image = Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1).copy()
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return image


def wait_for(condition, seconds: float, what: str):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = condition()
        if found:
            return found
        time.sleep(0.1)
    raise SystemExit(f"waited {seconds:g} s for {what}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frames").mkdir(exist_ok=True)
    from tabmancer import demo  # the made-up sessions

    with tempfile.TemporaryDirectory(prefix="tabmancer-window-") as tmp:
        home, signals = Path(tmp), Path(tmp) / "signals"
        os.environ["TABMANCER_CONFIG_DIR"] = str(home / "config")
        demo.build_home(home / "claude")
        before = terminal_windows()
        python = Path(sys.executable)
        venv_python = HERE.parent / ".venv" / "Scripts" / "python.exe"  # the project's, with textual
        subprocess.run([str(WT), "-w", "new", "--size", "150,44", "new-tab", "--title", "tabmancer",
                        "-d", str(HERE.parent), str(venv_python if venv_python.exists() else python),
                        str(HERE / "demo_window.py"), str(home), str(signals)], check=True)
        hwnd = wait_for(lambda: next(iter(terminal_windows() - before), None), 20, "the new window")
        print("window", hwnd)
        try:
            for scene in range(SCENES):
                name = wait_for(lambda s=scene: (signals / f"{s}.ready").exists() and
                                (signals / f"{s}.ready").read_text(encoding="utf-8"), 60, f"scene {scene}")
                picture(hwnd).save(OUT / f"{scene:02d}-{name}.png")
                for frame in range(FRAMES):
                    time.sleep(FRAME_GAP)
                    picture(hwnd).save(OUT / "frames" / f"{scene:02d}-{frame:02d}.png")
                (signals / f"{scene}.done").write_text("", encoding="utf-8")
                print("scene", scene, name)
            wait_for(lambda: hwnd not in terminal_windows(), 15, "the window to close")
        finally:
            if hwnd in terminal_windows():  # ours, and still open: closed as a window is
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


if __name__ == "__main__":
    main()
