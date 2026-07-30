from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class TimelineEvent:
    source: str
    channel: str
    start_s: float
    end_s: float
    label: str
    color: QColor
    event_id: str = ""

    @property
    def center_s(self) -> float:
        return 0.5 * (float(self.start_s) + float(self.end_s))


class EventTimelineOverlay(QWidget):
    """Compact global event lane synchronized to the main EEG viewer."""

    eventClicked = Signal(str, str, float)  # source, channel, center time

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[TimelineEvent] = []
        self._duration_s = 0.0
        self._view_start_s = 0.0
        self._view_end_s = 0.0
        self._hit_rects: list[tuple[QRectF, TimelineEvent]] = []
        self.setMinimumHeight(44)
        self.setMaximumHeight(54)
        self.setMouseTracking(True)
        self.hide()

    def set_duration(self, duration_s: float | None) -> None:
        try:
            value = float(duration_s if duration_s is not None else 0.0)
        except (TypeError, ValueError):
            value = 0.0
        self._duration_s = max(0.0, value if np.isfinite(value) else 0.0)
        self._view_end_s = min(max(self._view_end_s, self._view_start_s), self._duration_s)
        self._sync_visible()

    def set_view_window(self, start_s: float, duration_s: float) -> None:
        try:
            start = float(start_s)
            duration = float(duration_s)
        except (TypeError, ValueError):
            return
        if not np.isfinite(start) or not np.isfinite(duration):
            return
        self._view_start_s = max(0.0, start)
        self._view_end_s = max(self._view_start_s, self._view_start_s + max(0.0, duration))
        self.update()

    def set_events(self, events: list[dict[str, Any]]) -> None:
        cleaned: list[TimelineEvent] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            try:
                start_s = float(item.get("start_s", item.get("time_s", np.nan)))
                end_s = float(item.get("end_s", item.get("time_s", start_s)))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(start_s) or not np.isfinite(end_s):
                continue
            if end_s < start_s:
                start_s, end_s = end_s, start_s
            color_value = item.get("color", QColor(120, 130, 145))
            color = color_value if isinstance(color_value, QColor) else QColor(str(color_value))
            if not color.isValid():
                color = QColor(120, 130, 145)
            cleaned.append(
                TimelineEvent(
                    source=str(item.get("source", "")),
                    channel=str(item.get("channel", "")),
                    start_s=max(0.0, start_s),
                    end_s=max(0.0, end_s),
                    label=str(item.get("label", "")),
                    color=color,
                    event_id=str(item.get("event_id", "")),
                )
            )
        cleaned.sort(key=lambda event: (event.start_s, event.end_s, event.source, event.channel))
        self._events = cleaned
        self._sync_visible()

    def clear_events(self) -> None:
        self._events = []
        self._hit_rects = []
        self._sync_visible()

    def _sync_visible(self) -> None:
        self.setVisible(bool(self._events) and self._duration_s > 0.0)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor("#f8fafc"))
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        baseline_y = rect.center().y()
        left = 12
        right = max(left + 1, rect.width() - 12)
        width = right - left
        self._hit_rects = []

        if self._duration_s <= 0.0:
            return

        view_start_x = left + width * max(0.0, min(1.0, self._view_start_s / self._duration_s))
        view_end_x = left + width * max(0.0, min(1.0, self._view_end_s / self._duration_s))
        view_rect = QRectF(view_start_x, 8, max(1.0, view_end_x - view_start_x), rect.height() - 16)
        painter.fillRect(view_rect, QColor(15, 23, 42, 22))
        painter.setPen(QPen(QColor(15, 23, 42, 95), 1))
        painter.drawRect(view_rect)

        painter.setPen(QPen(QColor("#64748b"), 1.5))
        painter.drawLine(left, baseline_y, right, baseline_y)
        tick_half_height = 4
        for x in (left, right, view_start_x, view_end_x):
            painter.drawLine(int(round(x)), baseline_y - tick_half_height, int(round(x)), baseline_y + tick_half_height)

        lanes = {"hfo": baseline_y - 10, "gamma": baseline_y + 10, "rei": baseline_y + 19}
        marker_radius = 2.6
        for event in self._events:
            source = event.source.lower()
            y = lanes.get(source, baseline_y)
            center = max(0.0, min(self._duration_s, float(event.center_s)))
            x = left + width * max(0.0, min(1.0, center / self._duration_s))
            color = QColor(event.color)
            color.setAlpha(245)
            outline = QColor(color)
            outline.setAlpha(255)

            if source == "hfo":
                tick_y0 = baseline_y - 15
                tick_y1 = baseline_y + 3
            elif source == "gamma":
                tick_y0 = baseline_y - 3
                tick_y1 = baseline_y + 15
            else:
                tick_y0 = max(4, baseline_y - 4)
                tick_y1 = min(rect.height() - 4, baseline_y + 19)

            painter.setPen(QPen(outline.darker(110), 2.2))
            painter.drawLine(int(round(x)), int(round(tick_y0)), int(round(x)), int(round(tick_y1)))
            event_rect = QRectF(
                x - marker_radius,
                y - marker_radius,
                2.0 * marker_radius,
                2.0 * marker_radius,
            )
            painter.setPen(QPen(outline.darker(120), 1))
            painter.setBrush(color)
            painter.drawEllipse(event_rect)
            hit_y0 = min(tick_y0, y - marker_radius)
            hit_y1 = max(tick_y1, y + marker_radius)
            self._hit_rects.append((QRectF(x - 5, hit_y0 - 3, 10, hit_y1 - hit_y0 + 6), event))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()
        best_event: TimelineEvent | None = None
        best_distance = float("inf")
        for rect, timeline_event in self._hit_rects:
            if not rect.contains(pos):
                continue
            distance = abs(rect.center().x() - pos.x())
            if distance < best_distance:
                best_event = timeline_event
                best_distance = distance
        if best_event is None:
            super().mousePressEvent(event)
            return
        self.eventClicked.emit(best_event.source, best_event.channel, best_event.center_s)
        event.accept()
