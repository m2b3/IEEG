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
        self.setMinimumHeight(58)
        self.setMaximumHeight(66)
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
        baseline_y = max(18, rect.center().y() - 5)
        left = 12
        right = max(left + 1, rect.width() - 12)
        width = right - left
        self._hit_rects = []

        if self._duration_s <= 0.0:
            return

        view_start_x = left + width * max(0.0, min(1.0, self._view_start_s / self._duration_s))
        view_end_x = left + width * max(0.0, min(1.0, self._view_end_s / self._duration_s))
        view_rect = QRectF(view_start_x, 8, max(1.0, view_end_x - view_start_x), max(8, baseline_y + 8 - 8))
        painter.fillRect(view_rect, QColor(15, 23, 42, 22))
        painter.setPen(QPen(QColor(15, 23, 42, 95), 1))
        painter.drawRect(view_rect)

        painter.setPen(QPen(QColor("#64748b"), 1.5))
        painter.drawLine(left, baseline_y, right, baseline_y)
        self._draw_time_labels(painter, left, right, baseline_y)

        lanes = {"hfo": baseline_y - 10, "gamma": baseline_y + 10, "rei": baseline_y + 19}
        marker_radius = 3.4
        for event in self._events:
            source = event.source.lower()
            y = lanes.get(source, baseline_y)
            center = max(0.0, min(self._duration_s, float(event.center_s)))
            x = left + width * max(0.0, min(1.0, center / self._duration_s))
            color = QColor(event.color)
            color.setAlpha(245)
            outline = QColor(color)
            outline.setAlpha(255)

            event_rect = QRectF(
                x - marker_radius,
                y - marker_radius,
                2.0 * marker_radius,
                2.0 * marker_radius,
            )
            painter.setPen(QPen(outline.darker(120), 1))
            painter.setBrush(color)
            painter.drawEllipse(event_rect)
            self._hit_rects.append((QRectF(x - 6, y - 8, 12, 16), event))

    def _draw_time_labels(self, painter: QPainter, left: int, right: int, baseline_y: int) -> None:
        if self._duration_s <= 0.0:
            return
        width = max(1, int(right) - int(left))
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QPen(QColor("#475569"), 1))
        metrics = painter.fontMetrics()
        label_y = int(baseline_y) + 17
        candidates = [
            (0.0, int(left), Qt.AlignmentFlag.AlignLeft),
            (0.5 * self._duration_s, int(left + 0.5 * width), Qt.AlignmentFlag.AlignHCenter),
            (self._duration_s, int(right), Qt.AlignmentFlag.AlignRight),
        ]
        if width >= 520:
            candidates.insert(1, (0.25 * self._duration_s, int(left + 0.25 * width), Qt.AlignmentFlag.AlignHCenter))
            candidates.insert(-1, (0.75 * self._duration_s, int(left + 0.75 * width), Qt.AlignmentFlag.AlignHCenter))

        used_rects: list[QRectF] = []
        for time_s, x, alignment in candidates:
            text = self._format_time_label(time_s)
            text_width = metrics.horizontalAdvance(text)
            if alignment == Qt.AlignmentFlag.AlignLeft:
                text_rect = QRectF(x, label_y, text_width + 2, metrics.height() + 2)
            elif alignment == Qt.AlignmentFlag.AlignRight:
                text_rect = QRectF(x - text_width - 2, label_y, text_width + 2, metrics.height() + 2)
            else:
                text_rect = QRectF(x - 0.5 * text_width - 1, label_y, text_width + 2, metrics.height() + 2)
            if any(text_rect.intersects(existing.adjusted(-4, 0, 4, 0)) for existing in used_rects):
                continue
            painter.drawText(text_rect, int(alignment | Qt.AlignmentFlag.AlignTop), text)
            used_rects.append(text_rect)

    @staticmethod
    def _format_time_label(seconds: float) -> str:
        value = max(0.0, float(seconds))
        if value < 60.0:
            return f"{value:.1f}s" if value < 10.0 else f"{value:.0f}s"
        minutes = int(value // 60.0)
        sec = int(round(value - 60.0 * minutes))
        if sec == 60:
            minutes += 1
            sec = 0
        if minutes < 60:
            return f"{minutes}:{sec:02d}"
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}:{minutes:02d}:{sec:02d}"

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
