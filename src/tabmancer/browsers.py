"""Where Claude Code's sign-in page opens, for each account you sign in with from A.

The page signs in whichever claude.ai account the browser it opens in is signed in to: the email
Claude Code puts in its address is only a hint. Claude Code opens the page with the program named in
BROWSER, the address its one argument (2.1.318, checked with a stand-in program), and the system's
own way otherwise. So A can open it in the browser profile signed in to the account you want, where
one click finishes it, or in a private window, where you sign in afresh.

tabmancer reads only the browsers' list of their profiles, each one's name and the Google account it
syncs with (Chromium's "Local State" file), never their cookies or passwords; the sign-in stays
Claude Code's own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLACE = "TABMANCER_OPEN_IN"   # the place's id, for the program BROWSER names
PYTHON = "TABMANCER_PYTHON"   # the Python that runs this module there
OPENING = "TABMANCER_OPENING"  # set while this module opens an address: it never runs inside itself
DEFAULT = ""                  # the place that is the system's own way: no BROWSER at all


@dataclass(frozen=True)
class Place:
    id: str                 # "chrome:Profile 3", "edge:private"; DEFAULT for the system's own way
    words: str              # "Chrome · Work"
    argv: tuple[str, ...] = ()  # the browser and its flags: the address goes after them
    email: str = ""         # the Google account the profile syncs with, when it says


@dataclass(frozen=True)
class _Kind:
    key: str
    name: str
    private: str            # the flag for a private window
    windows: tuple[str, ...]  # where the program is, under Program Files or the local app folder
    mac: str                # the app's program, under /Applications
    linux: tuple[str, ...]  # its names on PATH
    data: dict[str, str]    # its profiles' folder, by system ("" when it keeps no Chromium profiles)


KINDS = (
    _Kind("chrome", "Chrome", "--incognito", (r"Google\Chrome\Application\chrome.exe",),
          "Google Chrome.app/Contents/MacOS/Google Chrome", ("google-chrome-stable", "google-chrome"),
          {"win32": r"Google\Chrome\User Data", "darwin": "Google/Chrome", "linux": "google-chrome"}),
    _Kind("edge", "Edge", "--inprivate", (r"Microsoft\Edge\Application\msedge.exe",),
          "Microsoft Edge.app/Contents/MacOS/Microsoft Edge", ("microsoft-edge-stable", "microsoft-edge"),
          {"win32": r"Microsoft\Edge\User Data", "darwin": "Microsoft Edge", "linux": "microsoft-edge"}),
    _Kind("brave", "Brave", "--incognito", (r"BraveSoftware\Brave-Browser\Application\brave.exe",),
          "Brave Browser.app/Contents/MacOS/Brave Browser", ("brave-browser", "brave"),
          {"win32": r"BraveSoftware\Brave-Browser\User Data", "darwin": "BraveSoftware/Brave-Browser",
           "linux": "BraveSoftware/Brave-Browser"}),
    _Kind("chromium", "Chromium", "--incognito", (r"Chromium\Application\chrome.exe",),
          "Chromium.app/Contents/MacOS/Chromium", ("chromium", "chromium-browser"),
          {"win32": r"Chromium\User Data", "darwin": "Chromium", "linux": "chromium"}),
    _Kind("firefox", "Firefox", "-private-window", (r"Mozilla Firefox\firefox.exe",),
          "Firefox.app/Contents/MacOS/firefox", ("firefox",), {}),
)


def _system() -> str:
    return sys.platform if sys.platform in ("win32", "darwin") else "linux"


def program(kind: _Kind) -> str | None:
    """Where the browser's program is, when it is installed."""
    system = _system()
    if system == "win32":
        bases = [os.environ.get(name) for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        found = [Path(base) / tail for base in bases if base for tail in kind.windows]
    elif system == "darwin":
        found = [Path(folder) / kind.mac for folder in ("/Applications", Path.home() / "Applications")]
    else:
        return next((path for name in kind.linux if (path := shutil.which(name))), None)
    return next((str(path) for path in found if path.is_file()), None)


def data_folder(kind: _Kind) -> Path | None:
    """The folder of the browser's profiles, where its "Local State" lists them."""
    tail = kind.data.get(_system())
    if not tail:
        return None
    system = _system()
    if system == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / tail if base else None
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / tail
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / tail


PROFILE_FOLDER = re.compile(r"[\w .-]+")  # "Default", "Profile 3": anything else is not passed on


def profiles(folder: Path) -> list[tuple[str, str, str]]:
    """The browser's profiles as (folder, name, Google account), in its own order: only these three
    are read from its Local State, nothing of what the profiles hold."""
    try:
        state = json.loads((folder / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    profile = state.get("profile") if isinstance(state, dict) else None
    cache = profile.get("info_cache") if isinstance(profile, dict) else None
    if not isinstance(cache, dict):
        return []
    order = profile.get("profiles_order")
    names = [n for n in order if n in cache] if isinstance(order, list) else []
    names += sorted((n for n in cache if n not in names), key=lambda n: (n != "Default", n))
    found = []
    for name in names:
        entry = cache[name]
        if not isinstance(entry, dict) or not isinstance(name, str) or not PROFILE_FOLDER.fullmatch(name):
            continue
        email = entry.get("user_name")
        found.append((name, str(entry.get("name") or name), email if isinstance(email, str) else ""))
    return found


def places() -> list[Place]:
    """Where a sign-in page can open: the system's own way first, then each installed browser's
    profiles, then its private window."""
    found = [Place(DEFAULT, "default browser")]
    for kind in KINDS:
        path = program(kind)
        if path is None:
            continue
        folder = data_folder(kind)
        listed = profiles(folder) if folder is not None else []
        for name, shown, email in listed:
            found.append(Place(f"{kind.key}:{name}", f"{kind.name} · {shown}",
                               (path, f"--profile-directory={name}"), email))
        if not listed:  # no profiles to pick: the browser itself
            found.append(Place(kind.key, kind.name, (path,)))
        found.append(Place(f"{kind.key}:private", f"{kind.name} · private window", (path, kind.private)))
    return found


def guess(email: str, among: list[Place]) -> str:
    """The place for an account nothing was picked for: the profile that syncs with the same Google
    account, if one does (an account signed in with Google is usually signed in there too)."""
    wanted = email.strip().lower()
    return next((p.id for p in among if wanted and p.email.strip().lower() == wanted), DEFAULT)


def launcher() -> Path:
    """The program Claude Code is given in BROWSER: a script in tabmancer's folder that hands the
    address to this module. Its words never change: the Python and the place come with the tab's
    environment (a path with letters beyond ASCII would not survive cmd.exe's code page in a .cmd)."""
    from .config import config_dir

    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    # -P: the folder it runs in is not where modules come from (a tabmancer/ there would be run instead).
    # Not -I, which turns off your own site-packages too, where `pip install --user` puts tabmancer.
    safe = "-P " if sys.version_info >= (3, 11) else ""
    if sys.platform == "win32":
        path = folder / "open-in.cmd"
        # %* as it came (Claude Code quotes the address): %1 would end it at its first "=".
        text = f'@echo off\r\n"%{PYTHON}%" {safe}-m tabmancer.browsers %*\r\n'
    else:
        path = folder / "open-in.sh"
        text = f'#!/bin/sh\nexec "${PYTHON}" {safe}-m tabmancer.browsers "$1"\n'
    try:
        same = path.read_bytes() == text.encode("ascii")
    except OSError:
        same = False
    if not same:
        path.write_bytes(text.encode("ascii"))
    if sys.platform != "win32":
        path.chmod(0o755)
    return path


def login_environment(place: str) -> tuple[tuple[str, str], ...]:
    """What the sign-in tab's environment needs for its page to open at the place: nothing for the
    system's own way, or when tabmancer's folder cannot be written to (the page opens as it did)."""
    if place == DEFAULT:
        return ()
    try:
        path = launcher()
    except OSError:
        return ()
    return (("BROWSER", str(path)), (PLACE, place), (PYTHON, sys.executable))


def _start(argv: list[str]) -> bool:
    """Start a program on its own, its output kept out of the terminal Claude Code draws in."""
    detached = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} \
        if sys.platform == "win32" else {"start_new_session": True}
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, **detached)
        return True
    except OSError:
        return False


def system_open(address: str) -> bool:
    """The system's own way to open a web address, which never asks BROWSER: in the sign-in tab that
    is this module's launcher, and Python's webbrowser.open (xdg-open too) would run it again, and it
    them, without end (it did: thousands of processes in a minute)."""
    if sys.platform == "win32":
        try:
            os.startfile(address)  # a web address: open_address checked it
            return True
        except OSError:
            return False
    return _start(["open" if sys.platform == "darwin" else "xdg-open", address])


def open_address(address: str, place: str) -> bool:
    """Open a web address at the place; where the place is gone, the system's own way."""
    if not re.match(r"https?://", address, re.IGNORECASE):
        return False  # only web pages: never a file or a program by its name
    chosen = next((p for p in places() if p.id == place and p.argv), None)
    if chosen is not None and _start([*chosen.argv, address]):
        return True
    return system_open(address)


def main(argv: list[str] | None = None) -> int:
    """Run by the launcher with the address Claude Code wants opened; 0 when it opened (Claude Code
    shows the address to copy otherwise)."""
    args = sys.argv[1:] if argv is None else argv
    if os.environ.get(OPENING):
        return 1  # started by itself, however that came about: stop here
    os.environ[OPENING] = "1"  # for whatever it starts
    os.environ.pop("BROWSER", None)  # nothing it starts comes back here that way
    if len(args) != 1:
        return 2
    return 0 if open_address(args[0], os.environ.get(PLACE, DEFAULT)) else 1


if __name__ == "__main__":
    sys.exit(main())
