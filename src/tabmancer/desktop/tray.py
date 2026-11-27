"""The dashboard window's place in the system tray (Sinan's choice, 2026-11-23): closing the window puts it there,
and tabmancer goes on (it reads the sessions, acts and sends its desktop notes) till you quit, with q in the
window or Quit in the icon's menu. Only where the desktop has a tray (available): elsewhere closing the window
quits, as before. Bit's on, calm and away join the menu with the pet."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .. import bitart
from .widgets import bit_pixmap

TIP = "tabmancer"
OPEN = "Open tabmancer"
QUIT = "Quit tabmancer"
KEPT_TITLE = "tabmancer is still running"
KEPT = "It keeps watching your sessions from here. To quit, pick Quit in this icon's menu, or press q in the window."
KEPT_MS = 6000
CLICKS = (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)


def available() -> bool:
    """Whether this desktop has a system tray (some Linux desktops have none)."""
    return QSystemTrayIcon.isSystemTrayAvailable()


class Tray(QObject):
    """Bit's icon in the tray and its menu. opened: Open picked, or the icon clicked; quit: Quit picked."""

    opened = Signal()
    quit = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.told = False  # where the window went, said once a run
        self.menu = QMenu(parent)
        opening = QAction(OPEN, self.menu)
        opening.triggered.connect(self.open_picked)
        self.menu.addAction(opening)
        self.menu.setDefaultAction(opening)
        self.menu.addSeparator()
        quitting = QAction(QUIT, self.menu)
        quitting.triggered.connect(self.quit_picked)
        self.menu.addAction(quitting)
        self.icon = QSystemTrayIcon(QIcon(bit_pixmap(bitart.Pose(eyes="happy"))), self)
        self.icon.setToolTip(TIP)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.clicked)
        self.icon.show()

    def open_picked(self) -> None:
        self.opened.emit()

    def quit_picked(self) -> None:
        self.quit.emit()

    def clicked(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """A click on the icon brings the window back; the right button opens the menu (Qt does that)."""
        if reason in CLICKS:
            self.opened.emit()

    def tell_kept(self) -> None:
        """The window went to the tray: said the first time in a run, with how to quit."""
        if self.told:
            return
        self.told = True
        self.icon.showMessage(KEPT_TITLE, KEPT, QSystemTrayIcon.MessageIcon.Information, KEPT_MS)

    def remove(self) -> None:
        """tabmancer quits: the icon goes at once, not when the process ends."""
        self.icon.hide()
