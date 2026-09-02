# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal


class RangeSlider(QtWidgets.QWidget):
    valuesChanged = Signal(float, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._minimum = 0.0
        self._maximum = 1.0
        self._lower_value = 0.0
        self._upper_value = 1.0
        self._drag_target: str | None = None
        self._handle_radius = 8
        self._groove_height = 6
        self.setMouseTracking(True)
        self.setMinimumHeight(32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def minimum(self) -> float:
        return float(self._minimum)

    def maximum(self) -> float:
        return float(self._maximum)

    def values(self) -> tuple[float, float]:
        return float(self._lower_value), float(self._upper_value)

    def setRange(self, minimum: float, maximum: float) -> None:
        minimum = float(minimum)
        maximum = float(maximum)
        if maximum < minimum:
            minimum, maximum = maximum, minimum

        self._minimum = minimum
        self._maximum = maximum
        self.setValues(self._lower_value, self._upper_value, emit=False)
        self.update()

    def setValues(self, lower: float, upper: float, *, emit: bool = True) -> None:
        if self._maximum <= self._minimum:
            self._lower_value = float(self._minimum)
            self._upper_value = float(self._maximum)
        else:
            lo = max(self._minimum, min(float(lower), self._maximum))
            hi = max(self._minimum, min(float(upper), self._maximum))
            if hi < lo:
                lo, hi = hi, lo
            self._lower_value = lo
            self._upper_value = hi

        self.update()
        if emit:
            self.valuesChanged.emit(self._lower_value, self._upper_value)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        groove_rect = self._groove_rect()
        lower_x = self._value_to_pos(self._lower_value)
        upper_x = self._value_to_pos(self._upper_value)

        groove_color = self.palette().color(QtGui.QPalette.ColorRole.Mid)
        selection_color = QtGui.QColor(70, 160, 220)
        handle_color = self.palette().color(QtGui.QPalette.ColorRole.Button)
        outline_color = self.palette().color(QtGui.QPalette.ColorRole.Dark)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(groove_color)
        painter.drawRoundedRect(groove_rect, 3, 3)

        selected = QtCore.QRectF(
            lower_x,
            groove_rect.top(),
            max(0.0, upper_x - lower_x),
            groove_rect.height(),
        )
        painter.setBrush(selection_color)
        painter.drawRoundedRect(selected, 3, 3)

        painter.setPen(QtGui.QPen(outline_color, 1))
        painter.setBrush(handle_color)
        for x in (lower_x, upper_x):
            painter.drawEllipse(
                QtCore.QPointF(x, groove_rect.center().y()),
                self._handle_radius,
                self._handle_radius,
            )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        lower_x = self._value_to_pos(self._lower_value)
        upper_x = self._value_to_pos(self._upper_value)
        click_x = float(event.position().x())

        dist_lower = abs(click_x - lower_x)
        dist_upper = abs(click_x - upper_x)

        if dist_lower <= self._handle_radius * 1.5 and dist_lower <= dist_upper:
            self._drag_target = "lower"
        elif dist_upper <= self._handle_radius * 1.5:
            self._drag_target = "upper"
        else:
            self._drag_target = "lower" if dist_lower <= dist_upper else "upper"
            self._move_active_handle(click_x)

        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_target is None:
            return super().mouseMoveEvent(event)

        self._move_active_handle(float(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_target is not None:
            self._move_active_handle(float(event.position().x()))
            self._drag_target = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _move_active_handle(self, pos_x: float) -> None:
        value = self._pos_to_value(pos_x)
        if self._drag_target == "lower":
            self.setValues(value, self._upper_value)
        elif self._drag_target == "upper":
            self.setValues(self._lower_value, value)

    def _groove_rect(self) -> QtCore.QRectF:
        margin = self._handle_radius + 2
        center_y = self.rect().center().y()
        return QtCore.QRectF(
            margin,
            center_y - self._groove_height / 2,
            max(1.0, self.width() - 2 * margin),
            self._groove_height,
        )

    def _value_to_pos(self, value: float) -> float:
        groove_rect = self._groove_rect()
        if self._maximum <= self._minimum:
            return float(groove_rect.left())
        ratio = (float(value) - self._minimum) / (self._maximum - self._minimum)
        ratio = max(0.0, min(ratio, 1.0))
        return float(groove_rect.left() + ratio * groove_rect.width())

    def _pos_to_value(self, pos_x: float) -> float:
        groove_rect = self._groove_rect()
        if groove_rect.width() <= 0 or self._maximum <= self._minimum:
            return float(self._minimum)
        ratio = (float(pos_x) - groove_rect.left()) / groove_rect.width()
        ratio = max(0.0, min(ratio, 1.0))
        return float(self._minimum + ratio * (self._maximum - self._minimum))
