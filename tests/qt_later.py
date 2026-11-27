"""The later fixture: Qt's single-shot timers for a test, dropped when it ends. A run() test arms a quit as its
safety net and looks every 100 ms for the window it opened; left pending on the shared QApplication, either fires
in a later test's event loop and quits its run before the window has read anything."""

import pytest
import shiboken6
from PySide6.QtCore import QObject, QTimer


@pytest.fixture
def later(qapp):
    """later(ms, call): QTimer.singleShot(ms, call), never run once the test is over (its context deleted)."""
    owner = QObject()
    yield lambda ms, call: QTimer.singleShot(ms, owner, call)
    shiboken6.delete(owner)
