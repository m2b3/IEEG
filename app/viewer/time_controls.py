# app/time_controls.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSlider

class TimeWindowControl(QWidget):
    """
    Reusable 'timeline' control:
      - Label: "t0: X.XX s"
      - Slider: t0 in milliseconds
    """
    t0Changed = Signal(float)  # seconds

    def __init__(self, parent: QWidget | None = None, label_prefix: str = "t0"):
        super().__init__(parent)
        self._label_prefix = label_prefix

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel(f"{self._label_prefix}: 0.00 s")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.setValue(0)

        lay.addWidget(self.label)
        lay.addWidget(self.slider, 1)

        self.slider.valueChanged.connect(self._on_slider)

    def _on_slider(self, v_ms: int):
        t0 = v_ms / 1000.0
        self.label.setText(f"{self._label_prefix}: {t0:.2f} s")
        self.t0Changed.emit(t0)

    def set_enabled(self, enabled: bool):
        self.slider.setEnabled(enabled)

    def set_t0(self, t0_s: float):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(float(t0_s) * 1000.0)))
        self.slider.blockSignals(False)
        self.label.setText(f"{self._label_prefix}: {float(t0_s):.2f} s")

    def set_range(self, total_s: float, window_s: float, current_t0: float):
        total_s = float(max(0.0, total_s))
        window_s = float(max(0.0, window_s))
        max_t0 = max(0.0, total_s - window_s)

        self.slider.blockSignals(True)
        self.slider.setMinimum(0)
        self.slider.setMaximum(int(round(max_t0 * 1000.0)))
        self.slider.setValue(int(round(float(current_t0) * 1000.0)))
        self.slider.blockSignals(False)
        self.label.setText(f"{self._label_prefix}: {float(current_t0):.2f} s")
