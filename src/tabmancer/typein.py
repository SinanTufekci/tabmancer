"""Type a prompt into an open session, the same as you would: its console gets the key presses.

Windows only. A session open in a Windows Terminal tab reads its keys from a console of its own
(the pseudoconsole behind the tab). A process with no console can attach to that one and write
key presses into its input, which the session reads like any typing: no window comes to the
front, and no other window can get the keys. The dashboard has a console of its own, so a short
helper process does the writing (this module, run with -m).

Only a session Claude Code lists as open is typed into. Its pid file says which process it is
and when that process started, so a pid Windows has since given to another program is never
written to.
"""

from __future__ import annotations

import subprocess
import sys
import time

from .sessions import _registry_sessions

DETACHED_PROCESS = 0x00000008  # the helper starts with no console, so it can attach to the session's
ENTER_AFTER = 0.5  # seconds between the words and Enter: arriving together, they would read as a paste
HELPER_SECONDS = 15


def session_pid(session_id: str) -> int | None:
    """The process of an open Claude session: from its pid file, and only while the process that
    wrote it is still the one running under that pid (the start time matches)."""
    for info in _registry_sessions():
        if info.get("sessionId") == session_id:
            try:
                return int(info["pid"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def one_line(text: str) -> str:
    """Enter sends the prompt, so a prompt of several lines goes in as one."""
    return " ".join(text.split())


def type_into(session_id: str, text: str) -> bool:
    """Type the prompt into the open session and press Enter. False if it could not be done:
    not on Windows, the session is not open, or its console would not take the keys."""
    if sys.platform != "win32" or not one_line(text):
        return False
    pid = session_pid(session_id)
    if pid is None:
        return False
    return write_keys(pid, one_line(text))


def write_keys(pid: int, text: str) -> bool:
    """The helper, run for one process's console."""
    try:
        result = subprocess.run([sys.executable, "-m", "tabmancer.typein", str(pid)], input=text,
                                capture_output=True, text=True, encoding="utf-8", timeout=HELPER_SECONDS,
                                check=False, creationflags=DETACHED_PROCESS)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# --- the helper -------------------------------------------------------------------

def _keys(text: str, enter: bool = False) -> list[tuple[bool, int, int, int]]:
    """(down, virtual key, scan code, character) for each key press and release: a character
    outside the basic plane goes in as its two UTF-16 halves, as a keyboard sends it."""
    import ctypes

    user32 = ctypes.windll.user32
    presses = []
    if enter:
        presses.append((0x0D, 0x1C, 0x0D))  # VK_RETURN, its scan code, "\r"
    else:
        units = text.encode("utf-16-le")
        for i in range(0, len(units), 2):
            unit = int.from_bytes(units[i:i + 2], "little")
            vk = user32.VkKeyScanW(unit) & 0xFF if not 0xD800 <= unit <= 0xDFFF else 0
            vk = 0 if vk == 0xFF else vk  # no key on this layout types it: the character alone
            presses.append((vk, user32.MapVirtualKeyW(vk, 0) if vk else 0, unit))
    return [(down, vk, scan, char) for vk, scan, char in presses for down in (True, False)]


def _write(pid: int, text: str) -> int:
    """Attach to the process's console and type into it. The exit code: 0 when every key went in."""
    import ctypes
    from ctypes import wintypes

    class KeyEvent(ctypes.Structure):
        _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD), ("vk", wintypes.WORD),
                    ("scan", wintypes.WORD), ("char", wintypes.WCHAR), ("state", wintypes.DWORD)]

    class Event(ctypes.Union):
        _fields_ = [("key", KeyEvent), ("whole", ctypes.c_byte * 16)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("kind", wintypes.WORD), ("event", Event)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def send(handle: int, keys: list[tuple[bool, int, int, int]]) -> bool:
        records = (InputRecord * len(keys))()
        for record, (down, vk, scan, char) in zip(records, keys):
            record.kind = 1  # KEY_EVENT
            record.event.key = KeyEvent(down, 1, vk, scan, chr(char), 0)
        written = wintypes.DWORD()
        ok = kernel32.WriteConsoleInputW(handle, records, len(keys), ctypes.byref(written))
        return bool(ok) and written.value == len(keys)

    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        return 3
    try:
        # GENERIC_READ | GENERIC_WRITE, shared both ways, OPEN_EXISTING: the console's input
        handle = kernel32.CreateFileW("CONIN$", 0xC0000000, 0x3, None, 3, 0, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return 4
        try:
            if not send(handle, _keys(text)):
                return 5
            time.sleep(ENTER_AFTER)
            return 0 if send(handle, _keys("", enter=True)) else 5
        finally:
            kernel32.CloseHandle(handle)
    finally:
        kernel32.FreeConsole()


if __name__ == "__main__":
    # UTF-8 as sent, whatever the code page: a prompt in Turkish or with an emoji goes in whole
    sys.exit(_write(int(sys.argv[1]), sys.stdin.buffer.read().decode("utf-8")) if len(sys.argv) == 2 else 2)
