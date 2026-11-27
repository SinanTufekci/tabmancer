"""W in the window (desktop/wrapped.py): the card shown as an image in its night sky; ← → the slides, 1 2 3 the
period counted again off the window's thread, f the shape, n the names, s and g saved, c copied by the window's
own clipboard, p the share menu; its words wrappedwords.py's. Offscreen, over an empty made-up week; the history,
saving and the share pages faked, every config folder in tmp. Skipped without the desktop extra
(pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import date, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from tabmancer import card, history, share, wrapped, wrappedwords
from tabmancer.desktop import wrapped as W
from tabmancer.tokens import Report

ACCENT = "#d97757"
RGB = (217, 119, 87)


def empty(days: int, whole: bool = False) -> Report:
    return Report(date.today() - timedelta(days=days - 1), days, whole=whole)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def faked(tmp_path, monkeypatch):
    """Every config folder in tmp; the kept days faked. asked: the days history.collect was asked for."""
    for name in ("TABMANCER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    asked: list = []
    monkeypatch.setattr(history, "collect", lambda days=None: asked.append(days) or
                        empty(days or 60, whole=days is None))
    return asked


def opened(qapp) -> W.WrappedDialog:
    dialog = W.WrappedDialog(None, empty(14), {}, ACCENT)
    dialog.resize(1000, 720)
    dialog.show()
    qapp.processEvents()
    return dialog


def settle(qapp, dialog) -> None:
    for _ in range(4):
        dialog.worker.settle(20)
        qapp.processEvents()


def test_a_card_becomes_an_image_pixel_for_pixel(qapp):
    canvas = wrapped.draw(wrapped.week(empty(14), {}), RGB, unit=1)
    image = W.image_of(canvas)
    assert (image.width(), image.height()) == (canvas.width, canvas.height) == wrapped.SHAPES["wide"]
    for x, y in ((0, 0), (canvas.width // 2, canvas.height // 2), (canvas.width - 1, canvas.height - 1)):
        r, g, b = canvas.palette[canvas.rows[y][x]]
        assert image.pixel(x, y) == 0xFF000000 | (r << 16) | (g << 8) | b
    assert "● week" in W.pills_html(["week", "month"], "week") and "○ month" in W.pills_html(["week", "month"], "week")


def test_the_keys_turn_the_slides_the_shape_the_names_and_the_period(qapp, faked):
    dialog = opened(qapp)
    assert dialog.slide == wrapped.SLIDES[0] and dialog.shape == "wide" and "Wrapped" in dialog.heading.text()
    assert not dialog.view.grab().isNull() and dialog.view.unit() >= 1
    QTest.keyClick(dialog, Qt.Key.Key_Right)
    assert dialog.slide == wrapped.SLIDES[1]
    QTest.keyClick(dialog, Qt.Key.Key_Left)
    QTest.keyClick(dialog, Qt.Key.Key_Left)
    assert dialog.slide == wrapped.SLIDES[-1]
    QTest.keyClick(dialog, "f")
    assert dialog.shape == "story" and "● story" in dialog.shapes.text()
    assert not dialog.view.grab().isNull()
    QTest.keyClick(dialog, "n")
    assert dialog.names and "n leaves it off" in dialog.about.text()
    QTest.keyClick(dialog, "2")
    assert dialog.counting
    settle(qapp, dialog)
    assert faked == [60] and dialog.period == 30 and not dialog.counting and "● month" in dialog.heading.text()
    QTest.keyClick(dialog, "3")
    settle(qapp, dialog)
    assert faked == [60, None] and dialog.period == 0 and dialog.week.whole
    QTest.keyClick(dialog, "W")
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_saving_copying_and_sharing(qapp, faked, monkeypatch, tmp_path):
    dialog = opened(qapp)
    monkeypatch.setattr(card, "save", lambda canvas, name, folder=None: tmp_path / name)
    QTest.keyClick(dialog, "s")
    assert f"Saved as {tmp_path / wrapped.filename(dialog.week, slide=dialog.slide)}" in dialog.note.text()

    monkeypatch.setattr(wrapped, "moving", lambda week, accent, shape, slide, names=False: b"GIF89a")
    monkeypatch.setattr(card, "save_data", lambda data, name, folder=None: tmp_path / name)
    QTest.keyClick(dialog, "g")
    settle(qapp, dialog)
    assert "a GIF, for a post that takes one" in dialog.note.text()

    QApplication.clipboard().clear()
    QTest.keyClick(dialog, "c")
    copied = QApplication.clipboard().image()
    assert (copied.width(), copied.height()) == tuple(side * wrapped.UNIT for side in wrapped.SHAPES["wide"])
    assert wrappedwords.COPIED.words.split(":")[0] in dialog.note.text()

    menu = dialog.share_menu()
    keys = [action.data() for action in menu.actions() if action.data()]
    assert keys == [key for key, _, _ in wrappedwords.share_choices()]
    texts = " ".join(action.text() for action in menu.actions())
    assert all(place.label in texts for place in share.PLACES) and wrapped.words(dialog.week) in texts
    menu.deleteLater()

    dialog.share(wrappedwords.WORDS)
    assert QApplication.clipboard().text() == wrapped.words(dialog.week)
    monkeypatch.setattr(share, "share", lambda place, week, accent, slide="summary", names=False:
                        share.Shared(tmp_path / "card.png", True, "page", f"{place.label} is open."))
    dialog.share("x")
    assert "Wrapped for X: X is open." in dialog.note.text()
    dialog.share("nowhere")
    assert "Wrapped for X" in dialog.note.text()  # nothing said for a key with no place
    dialog.close()
