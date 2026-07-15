from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMainWindow, QStatusBar, QWidget


@contextmanager
def busy_cursor(widget: QWidget | None = None, message: str | None = None) -> Iterator[None]:
    app = QApplication.instance()
    status_bar: QStatusBar | None = None

    if widget is not None and message:
        window = widget.window()
        if isinstance(window, QMainWindow):
            candidate = window.statusBar()
            if isinstance(candidate, QStatusBar):
                status_bar = candidate
                status_bar.showMessage(message)

    if app is not None:
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BusyCursor))
        app.processEvents()

    try:
        yield
    finally:
        if app is not None:
            QApplication.restoreOverrideCursor()
            app.processEvents()
        if status_bar is not None:
            status_bar.clearMessage()
