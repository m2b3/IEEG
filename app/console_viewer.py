# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from PySide6.QtWidgets import QMainWindow, QPlainTextEdit
from PySide6.QtCore import Slot

class ConsoleWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Console")
        self.resize(900, 250)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setCentralWidget(self.text)

    @Slot(str)
    def log(self, msg: str):
        self.text.appendPlainText(msg)

    def clear(self):
        self.text.clear()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
