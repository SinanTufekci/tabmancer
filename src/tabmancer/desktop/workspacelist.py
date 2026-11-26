"""o, in the window: the workspaces saved (named sets of project folders that open together: workspaces.py),
listed as the terminal dashboard's o lists them, in workspacewords.py's words. Enter opens one (the window
resolves it and opens its tabs on a thread: opening.Opener.open_workspace); d deletes one at once
(workspaces.drop). w, which saves one, asks for its name in naming.NameDialog."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QWidget

from .. import workspaces, workspacewords
from ..config import UI_TRIES
from ..workspaces import Workspace
from . import look
from .clisettings import PickRow, parts_html
from .formparts import keys_html
from .handover import label


class WorkspacesDialog(QDialog):
    """↑↓ pick a workspace, Enter (or a double click) opens it (chosen: its name, once), d or Delete deletes
    it (said: what came of it, an actions.Note; with the last one gone, it closes), Esc closes it.
    Window-modal, never waited on."""

    chosen = Signal(str)
    said = Signal(object)

    def __init__(self, parent: QWidget, saved: dict[str, Workspace], accent: str) -> None:
        super().__init__(parent)
        self.saved = dict(saved)
        self.names = list(saved)
        self.accent = accent
        self.at = 0
        self.answered = False
        self.setWindowTitle(workspacewords.TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the keys are the dialog's: nothing in it takes the focus
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label(look.span(workspacewords.TITLE, f"bold {look.TEXT}"))
        box.addWidget(self.heading)
        listed = QFrame()
        listed.setObjectName("listed")
        self.listed = QVBoxLayout(listed)
        self.listed.setContentsMargins(0, 4, 0, 4)
        self.listed.setSpacing(2)
        self.rows: list[PickRow] = []
        for at in range(len(self.names)):
            row = PickRow(at)
            row.clicked.connect(self.click)
            row.chosen.connect(self.double_click)
            self.listed.addWidget(row)
            self.rows.append(row)
        box.addWidget(listed)
        self.keys = label(keys_html(workspacewords.LIST_KEYS))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QFrame#listed {{ background:{look.BLACK}; border:none; }}")
        self.finished.connect(self.deleteLater)
        self.draw()

    def draw(self) -> None:
        """Each workspace's row as the terminal's list words it, the picked one lit."""
        for at, (name, row) in enumerate(zip(self.names, self.rows, strict=True)):
            row.setText(parts_html(workspacewords.option_parts(self.saved[name])))
            edge, ground = (self.accent, look.PANEL) if at == self.at else ("transparent", "transparent")
            row.setStyleSheet(f"background:{ground}; border-left:2px solid {edge}; padding:4px 10px;")

    def step(self, by: int) -> None:
        """↑↓: the next workspace, round the list."""
        if self.names:
            self.at = (self.at + by) % len(self.names)
            self.draw()

    def click(self, at: int) -> None:
        self.at = at
        self.draw()

    def double_click(self, at: int) -> None:
        self.at = at
        self.choose()

    def choose(self) -> None:
        """Enter: the picked workspace opened, once (the window resolves and opens it, on a thread)."""
        if self.answered or not self.names:
            return
        self.answered = True
        name = self.names[self.at]
        self.accept()
        self.chosen.emit(name)

    def delete(self) -> None:
        """d: the picked workspace deleted at once (workspaces.drop: the file read again, the others kept), and
        said; not deleted, said too, when workspaces.json could not be read or another tabmancer was changing
        it (UI_TRIES: ~75 ms at most on the window's thread)."""
        if not self.names:
            return
        name = self.names[self.at]
        if not workspaces.drop(name, UI_TRIES):
            self.said.emit(workspacewords.not_kept_note(name, deleting=True))
            return
        self.said.emit(workspacewords.deleted_note(name))
        self.names.pop(self.at)
        row = self.rows.pop()  # the rows say the names left, each at its place: the last one goes
        self.listed.removeWidget(row)
        row.deleteLater()
        if not self.names:
            self.reject()
            return
        self.at = min(self.at, len(self.names) - 1)
        self.draw()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.step(-1)
        elif key == Qt.Key.Key_Down:
            self.step(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.choose()
        elif key == Qt.Key.Key_Delete or (key == Qt.Key.Key_D
                                          and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.delete()
        else:
            super().keyPressEvent(event)  # Esc: closed, nothing opened
