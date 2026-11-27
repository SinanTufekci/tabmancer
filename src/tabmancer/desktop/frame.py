"""What the desktop windows share: one window a name (a door the same command knocks at instead of
opening a second), the layout kept in desktop.json, the keys dialog, the demo's made-up world, and Python's
cycle collector kept on the GUI thread while a window is up (Collector)."""

from __future__ import annotations

import base64
import contextlib
import gc
import hashlib
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Generator, Mapping
from functools import partial
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QMainWindow,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..config import config_dir, merge_json, read_json
from ..demo import ENVIRON as DEMO_ENV  # what demo.prepare points elsewhere, put back after (made_up_world)
from . import look

KNOCK_MS = 500  # how long a second window waits for the first to answer
LAYOUT_FILE = "desktop.json"
LAYOUT_VERSION = 1
COLLECT_MS = 1000  # how often the GUI thread looks whether Python's cycle collector is due, while a window is up
# Of the youngest generation's collections, every MIDDLE_EVERY-th takes the middle one too and every FULL_EVERY-th
# all of it: gc's own default schedule (thresholds 10 and 10), counted here, since 3.14's incremental collector
# counts no older generation (its get_threshold is (2000, 10, 0) and get_count's last stays 0).
MIDDLE_EVERY = 10
FULL_EVERY = 100


class Collector(QObject):
    """Python's cycle collector, on the GUI thread only while a window is up. Left automatic, it runs on whichever
    thread allocates past its threshold (the store's reader, most often), and a cycle holding a Qt object (a
    window or a dialog gone) is then freed there, off the thread Qt objects belong to: an access violation
    (Windows CI, 2026-11-26). So from the first window to the last automatic collection is off, and a timer
    here collects the youngest generation once it is past its threshold (pyqtgraph's GarbageCollector), the
    older ones as often as gc would (MIDDLE_EVERY, FULL_EVERY); the last window gone (closed, or deleted
    unclosed), all is collected here and it is as it was."""

    def __init__(self, app: QObject) -> None:
        super().__init__(app)
        self.held: set[int] = set()  # the windows up, by the number each was given
        self.made = 0
        self.was_on = True
        self.young = 0  # the youngest generation's collections here
        self.timer = QTimer(self)
        self.timer.setInterval(COLLECT_MS)
        self.timer.timeout.connect(self.check)

    def hold(self, window: QObject) -> Callable[[], None]:
        """A window up: automatic collection off till the last one goes. What lets go of it, which its closing
        calls; deleted unclosed, it lets go by itself."""
        self.made += 1
        if not self.held:
            self.was_on = gc.isenabled()
            gc.disable()
            self.timer.start()
        self.held.add(self.made)
        done = partial(self.let_go, self.made)
        window.destroyed.connect(done)
        return done

    def let_go(self, key: int, *_destroyed: object) -> None:
        if key not in self.held:
            return
        self.held.discard(key)
        if not self.held:
            self.timer.stop()
            gc.collect()
            if self.was_on:
                gc.enable()

    def check(self) -> None:
        """The youngest generation collected once it is past its threshold; with it, every MIDDLE_EVERY-th time
        the middle one (3.14: an increment of the old one), every FULL_EVERY-th time all. Only the youngest
        generation's count and threshold are read: they mean the same on every Python, the others do not."""
        if gc.get_count()[0] <= gc.get_threshold()[0]:
            return
        gc.collect(0)
        self.young += 1
        if self.young % FULL_EVERY == 0:
            gc.collect()
        elif self.young % MIDDLE_EVERY == 0:
            gc.collect(1)


def collector() -> Collector:
    """The application's Collector, made the first time a window asks."""
    app = QApplication.instance()
    found = app.findChild(Collector)
    return found if found is not None else Collector(app)


def door(kind: str, name: str = "") -> str:
    """The name an open window listens on: one per kind of window, name (a team's) and tabmancer
    folder (the demo's is a folder of its own)."""
    key = f"{config_dir()}\0{name}".encode()
    return f"tabmancer-{kind}-{hashlib.sha256(key).hexdigest()[:20]}"


def knock(name: str) -> bool:
    """Whether a window listens on this door; if one does, it comes to the front."""
    if sys.platform == "win32":  # this process may take the front (started from what you typed in): let that window
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    caller = QLocalSocket()
    caller.connectToServer(name)
    if not caller.waitForConnected(KNOCK_MS):
        return False
    caller.disconnectFromServer()
    return True


def listen(server: QLocalServer, name: str) -> None:
    if not server.listen(name) and server.serverError() == QAbstractSocket.SocketError.AddressInUseError:
        QLocalServer.removeServer(name)  # left by a window that ended without closing (Unix); knock found none
        server.listen(name)


def restore_layout(window: QMainWindow, key: str) -> set[str] | None:
    """The window's size, place and panels as it was last closed (desktop.json, under key). Says
    which panels (by object name) that layout knew, so one new since can go where it starts
    (place_new): Qt squeezes a panel its saved layout does not name in beside the others. A layout
    kept before it named them knew none. None: nothing kept or readable, the panels as they start."""
    saved = read_json(LAYOUT_FILE)
    kept = saved.get(key) if isinstance(saved, dict) else None
    if not isinstance(kept, dict):
        return None
    try:
        if kept.get("geometry"):
            window.restoreGeometry(QByteArray(base64.b64decode(kept["geometry"])))
        if kept.get("state") and not window.restoreState(QByteArray(base64.b64decode(kept["state"])),
                                                         LAYOUT_VERSION):
            return None
    except (TypeError, ValueError):
        return None  # a layout written by hand or cut short: the panels as they start
    named = kept.get("docks")
    return {name for name in named if isinstance(name, str)} if isinstance(named, list) else set()


def save_layout(window: QMainWindow, key: str) -> None:
    merge_json(LAYOUT_FILE, **{key: {
        "geometry": base64.b64encode(bytes(window.saveGeometry().data())).decode("ascii"),
        "state": base64.b64encode(bytes(window.saveState(LAYOUT_VERSION).data())).decode("ascii"),
        "docks": sorted(dock.objectName() for dock in window.findChildren(QDockWidget))}})


def place_new(window: QMainWindow, known: set[str] | None, dock: QDockWidget, beside: list[QDockWidget],
              area: Qt.DockWidgetArea) -> None:
    """A panel the kept layout did not know: a tab behind the first of beside that is docked and
    shown (it stays in front), else in area; shown either way. One it knew stays as it was kept,
    closed too."""
    if known is None or dock.objectName() in known:
        return
    for other in beside:
        if not other.isHidden() and not other.isFloating():
            window.tabifyDockWidget(other, dock)
            other.raise_()
            break
    else:
        window.addDockWidget(area, dock)
    dock.show()


class KeysDialog(QDialog):
    """?: every key, what it does, and where the ones this window does not do are done (elsewhere; own: a key's
    own words for that)."""

    def __init__(self, parent: QWidget, title: str, groups: list, here: set[str], elsewhere: str,
                 own: Mapping[str, str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        box = QVBoxLayout(self)
        text = QTextBrowser()
        text.setFont(look.font(9.5))
        rows = []
        for group, keys in groups:
            rows.append(look.span(group.upper(), f"bold {look.MUTED}"))
            for key, _action, words in keys:
                where = "" if key in here else look.span(f"   ({(own or {}).get(key, elsewhere)})", look.DIM)
                rows.append(look.span(f"{key:<6}", f"bold {look.TEXT if key in here else look.DIM}")
                            + look.span(words, look.TEXT if key in here else look.MUTED) + where)
            rows.append("")
        text.setHtml(f'<pre style="font-family:{look.mono_family()}">' + "<br>".join(rows) + "</pre>")
        box.addWidget(text)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}")


@contextlib.contextmanager
def made_up_world(demo: bool, tell: Callable[[str], None] | None = None) -> Generator[None]:
    """With demo, the made-up sessions and team of `tabmancer --demo`, in a folder of its own that
    goes afterwards: nothing real is read, and what the demo put in place of the world is put back.
    tell: what the stand-ins say would have happened (a tab opening), from any thread."""
    if not demo:
        yield
        return
    from .. import demo as made_up

    environ = {name: os.environ.get(name) for name in DEMO_ENV}
    home = Path(tempfile.mkdtemp(prefix="tabmancer-demo-"))
    kept = []
    try:
        live = made_up.prepare(home)
        told: list[str] = []
        for module, name, value in made_up.stand_ins(home, live, tell or told.append):
            kept.append((module, name, getattr(module, name)))
            setattr(module, name, value)
        yield
    finally:
        for module, name, value in reversed(kept):
            setattr(module, name, value)
        for name, value in environ.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(home, ignore_errors=True)
