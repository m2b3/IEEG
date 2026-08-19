# app/expert_event_grid.py
"""Reusable event grid widget for computed HFO review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Callable, List, cast

import numpy as np
import pyqtgraph as pg
from scipy.signal import butter, sosfiltfilt, spectrogram

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QComboBox,
)

# Review label colors
COLOR_REJECTED_ARTIFACT = QColor(220, 50, 50)  # red
COLOR_SPK_HFO = QColor(50, 200, 50)            # green
COLOR_NON_SPK_HFO = QColor(50, 100, 220)       # blue
COLOR_HFO = QColor(40, 120, 210)               # blue
COLOR_EHFO = QColor(15, 118, 110)              # teal
COLOR_SPK_EHFO = QColor(124, 58, 237)          # violet
COLOR_UNCLASSIFIED = QColor(120, 130, 145)     # gray
COLOR_DELETED = QColor(90, 90, 90)             # dark gray

STANDARD_HFO_REVIEW_CLASS_OPTIONS = [
    "artifact",
    "HFO",
    "non-spike HFO",
    "spike-HFO",
    "unclassified",
    "deleted",
]
EHFO_REVIEW_CLASS_OPTIONS = [
    "artifact",
    "HFO",
    "non-spike HFO",
    "spike-HFO",
    "eHFO",
    "spike-eHFO",
    "unclassified",
    "deleted",
]
HFO_REVIEW_CLASS_OPTIONS = EHFO_REVIEW_CLASS_OPTIONS


def hfo_review_class_options(*, include_ehfo: bool) -> list[str]:
    options = EHFO_REVIEW_CLASS_OPTIONS if include_ehfo else STANDARD_HFO_REVIEW_CLASS_OPTIONS
    return list(options)

# Grid dimensions
GRID_ROWS = 6
GRID_COLS = 4
GRID_TOTAL = GRID_ROWS * GRID_COLS

# Candidate HFOs are easier to review in a fixed centered context window.
EVENT_CONTEXT_WINDOW_SECONDS = 0.5
EVENT_CONTEXT_WINDOW_CHOICES_SECONDS = (0.25, 0.5, 1.0, 2.0)

EVENT_REGION_BRUSH = (185, 185, 185, 55)
EVENT_MARKER_COLOR = (215, 215, 215, 190)
NEUTRAL_WAVEFORM_DARK = "#f2f2f2"
NEUTRAL_WAVEFORM_LIGHT = "#202020"
SPECTROGRAM_FREQ_MAX_HZ = 400.0
SPECTROGRAM_DYNAMIC_RANGE_DB = 45.0

DISPLAY_FILTERS: dict[str, tuple[float | None, float | None]] = {
    "Default": (None, None),
    "80-300 Hz": (80.0, 300.0),
    "Ripple": (80.0, 250.0),
    "Fast ripple": (250.0, 500.0),
}
DEFAULT_DISPLAY_FILTER = "80-300 Hz"


def get_event_context_window(
    event: "ExpertEvent",
    window_s: float = EVENT_CONTEXT_WINDOW_SECONDS,
) -> tuple[float, float]:
    window_s = float(getattr(event, "review_context_window_s", window_s) or window_s)
    center_s = (float(event.start) + float(event.end)) / 2.0
    window_s = max(float(window_s), 1e-6)
    half_window_s = window_s / 2.0
    start_s = max(0.0, center_s - half_window_s)
    end_s = start_s + window_s
    return start_s, end_s


def get_neutral_waveform_color(*, dark_mode: bool = True) -> str:
    return NEUTRAL_WAVEFORM_DARK if dark_mode else NEUTRAL_WAVEFORM_LIGHT


def waveform_y_bounds(waveform: np.ndarray) -> tuple[float, float]:
    values = np.asarray(waveform, dtype=float).reshape(-1)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return -1.0, 1.0

    y_min = float(np.min(values))
    y_max = float(np.max(values))
    span = y_max - y_min
    if span <= 0.0:
        pad = max(abs(y_min) * 0.1, 1e-12)
    else:
        pad = span * 0.08
    return y_min - pad, y_max + pad


def add_event_region(
    plot_widget: pg.PlotWidget,
    start_s: float,
    end_s: float,
    y_min: float,
    y_max: float,
    color: QColor | None = None,
) -> None:
    if end_s <= start_s:
        end_s = start_s + 1e-6
    if y_max <= y_min:
        y_max = y_min + 1e-12

    if color is None:
        brush = pg.mkBrush(*EVENT_REGION_BRUSH)
        pen = pg.mkPen(EVENT_MARKER_COLOR, width=1)
        edge_pen = pg.mkPen(EVENT_MARKER_COLOR, width=1)
    else:
        region_color = QColor(color)
        region_color.setAlpha(62)
        edge_color = QColor(color)
        edge_color.setAlpha(225)
        brush = pg.mkBrush(region_color)
        pen = pg.mkPen(edge_color, width=1.5)
        edge_pen = pg.mkPen(edge_color, width=2)

    region = pg.BarGraphItem(
        x0=[float(start_s)],
        x1=[float(end_s)],
        y0=[float(y_min)],
        y1=[float(y_max)],
        brush=brush,
        pen=pen,
    )
    region.setZValue(-10)
    plot_widget.addItem(region)

    for x in (start_s, end_s):
        marker = pg.PlotDataItem(
            [float(x), float(x)],
            [float(y_min), float(y_max)],
            pen=edge_pen,
        )
        marker.setZValue(10)
        plot_widget.addItem(marker)


def waveform_time_axis(waveform: np.ndarray, start_s: float, end_s: float) -> np.ndarray:
    if end_s <= start_s:
        end_s = start_s + 1e-6
    return np.linspace(float(start_s), float(end_s), int(waveform.size), endpoint=False)


def set_plot_x_range(plot_widget: pg.PlotWidget, start_s: float, end_s: float) -> None:
    cast(Any, plot_widget).setXRange(float(start_s), float(end_s), 0)


def set_plot_y_range(plot_widget: pg.PlotWidget, low: float, high: float) -> None:
    cast(Any, plot_widget).setYRange(float(low), float(high), 0)


def hide_plot_axes(plot_widget: pg.PlotWidget) -> None:
    plot_item = cast(Any, plot_widget.getPlotItem())
    for axis_name in ("bottom", "left", "right", "top"):
        plot_item.hideAxis(axis_name)


def disable_plot_mouse(plot_widget: pg.PlotWidget) -> None:
    plot_item = cast(Any, plot_widget.getPlotItem())
    plot_item.setMouseEnabled(x=False, y=False)


def add_unavailable_label(plot_widget: pg.PlotWidget, text: str = "Waveform unavailable") -> None:
    plot_widget.plot([0, 1], [0, 0], pen=pg.mkPen(color="#555555", width=1))
    label = pg.TextItem(text=text, color="#aaaaaa", anchor=(0.5, 0.5))
    label.setZValue(20)
    plot_widget.addItem(label)
    label.setPos(0.5, 0.0)
    set_plot_x_range(plot_widget, 0, 1)
    set_plot_y_range(plot_widget, -1, 1)


def spectrogram_lookup_table() -> np.ndarray:
    color_map = pg.ColorMap(
        np.array([0.0, 0.18, 0.42, 0.68, 1.0], dtype=float),
        np.array(
            [
                [20, 24, 38, 255],
                [32, 70, 125, 255],
                [46, 134, 170, 255],
                [238, 174, 64, 255],
                [255, 247, 188, 255],
            ],
            dtype=np.ubyte,
        ),
    )
    return color_map.getLookupTable(0.0, 1.0, 256)


def _valid_times_for_waveform(times: Optional[np.ndarray], waveform: np.ndarray) -> Optional[np.ndarray]:
    if times is None:
        return None
    times = np.asarray(times, dtype=float).reshape(-1)
    if times.size != waveform.size:
        return None
    return times


def _sample_rate_from_times(times: np.ndarray) -> float | None:
    if times.size < 2:
        return None
    diffs = np.diff(np.asarray(times, dtype=float))
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return None
    median_dt = float(np.median(diffs))
    if median_dt <= 0.0:
        return None
    return 1.0 / median_dt


def _filtered_waveform_for_display(
    waveform: np.ndarray,
    times: np.ndarray,
    band: tuple[float | None, float | None],
) -> np.ndarray:
    low_hz, high_hz = band
    if low_hz is None and high_hz is None:
        return waveform

    sfreq = _sample_rate_from_times(times)
    if sfreq is None:
        return waveform

    nyquist = 0.5 * sfreq
    low = float(low_hz) if low_hz is not None and float(low_hz) > 0.0 else None
    high = float(high_hz) if high_hz is not None and float(high_hz) > 0.0 else None
    if high is not None:
        high = min(high, nyquist * 0.98)
    if low is not None and low >= nyquist * 0.98:
        return waveform
    if low is not None and high is not None and low >= high:
        return waveform

    if low is not None and high is not None:
        wn: float | tuple[float, float] = (low / nyquist, high / nyquist)
        btype = "bandpass"
    elif low is not None:
        wn = low / nyquist
        btype = "highpass"
    elif high is not None:
        wn = high / nyquist
        btype = "lowpass"
    else:
        return waveform

    try:
        sos = butter(4, wn, btype=btype, output="sos")
        return np.asarray(sosfiltfilt(sos, waveform), dtype=float)
    except ValueError:
        return waveform


WIDE_CONTEXT_SECONDS = 1.0


def get_event_wide_context_window(
    event: ExpertEvent,
    context_seconds: float = WIDE_CONTEXT_SECONDS,
) -> tuple[float, float]:
    """Return a wider time window centered on the event for raw-channel context."""
    center = (event.start + event.end) / 2.0
    half_window = context_seconds / 2.0
    return max(0.0, center - half_window), center + half_window


@dataclass
class ExpertEvent:
    """
    Represents a single event in the HFO review grid.
    
    Attributes:
        edf_file: Source recording name
        channel: Channel name
        start: Start time in seconds
        end: End time in seconds
        detector: Detector name that flagged this event
        artifact: True if accepted as HFO, False if rejected as artifact
        spike: True if the accepted HFO is spike-associated
        waveform: Optional waveform data array
        waveform_start: Absolute start time for waveform, if known
        waveform_end: Absolute end time for waveform, if known
        waveform_times: Exact sample times for waveform, when callback provides them
    """
    edf_file: str
    channel: str
    start: float
    end: float
    detector: str
    artifact: bool = False
    spike: bool = False
    waveform: Optional[np.ndarray] = None
    waveform_start: float | None = None
    waveform_end: float | None = None
    waveform_times: Optional[np.ndarray] = None
    waveform_unavailable: bool = False
    wide_waveform: Optional[np.ndarray] = None
    wide_waveform_start: Optional[float] = None
    wide_waveform_end: Optional[float] = None
    wide_waveform_times: Optional[np.ndarray] = None
    wide_waveform_unavailable: bool = False
    event_number: str = ""
    model_class: str = ""
    band_label: str = "80-300 Hz"
    low_freq_hz: float | None = None
    high_freq_hz: float | None = None
    boundary_warning: bool = False
    real_hfo_probability: float | None = None
    artifact_probability: float | None = None
    spike_hfo_probability: float | None = None
    classification_status: str = ""
    manual_class: str = ""
    manual_review_status: str = "unreviewed"
    source_event: Any | None = None
    detail_lines: List[str] | None = None
    review_context_window_s: float = EVENT_CONTEXT_WINDOW_SECONDS

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def model_label(self) -> str:
        model_class = str(self.model_class or "").strip()
        return model_class or "unclassified"
    
    @property
    def review_label(self) -> str:
        """Return the official event class after manual review, falling back to the model."""
        manual_class = str(self.manual_class or "").strip()
        if manual_class:
            return manual_class
        if self.model_label:
            return self.model_label
        if not self.artifact:
            return "Rejected artifact"
        if self.spike:
            return "spkHFO"
        return "non-spkHFO"
    
    @property
    def review_color(self) -> QColor:
        """Returns the color for this review label."""
        label = str(self.review_label).strip().lower().replace("_", "-").replace(" ", "-")
        if label in {"deleted", "excluded"}:
            return COLOR_DELETED
        if label in {"unclassified", "candidate", "unknown", "not-classified"}:
            return COLOR_UNCLASSIFIED
        if "artifact" in label:
            return COLOR_REJECTED_ARTIFACT
        if label in {"spike-ehfo", "spkehfo", "spk-ehfo"}:
            return COLOR_SPK_EHFO
        if label in {"ehfo", "e-hfo"}:
            return COLOR_EHFO
        if label == "hfo":
            return COLOR_HFO
        if "non-spike" in label or "non-spk" in label or label == "hfo":
            return COLOR_NON_SPK_HFO
        if label in {"spike-hfo", "spkhfo", "spk-hfo"}:
            return COLOR_SPK_HFO
        return COLOR_NON_SPK_HFO


class EventCellWidget(QFrame):
    """
    A single cell in the event grid displaying waveform snippet and event info.
    """
    
    clicked = Signal(ExpertEvent)
    
    def __init__(self, event: ExpertEvent, parent=None):
        super().__init__(parent)
        self._event = event
        self._setup_ui()
    
    def _setup_ui(self):
        self.setObjectName("eventCell")
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumSize(200, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Class color is reserved for the card border and badge outline.
        color = self._event.review_color
        self.setStyleSheet(f"""
            QFrame#eventCell {{
                border: 1px solid #d8dde6;
                border-left: 4px solid rgb({color.red()}, {color.green()}, {color.blue()});
                border-radius: 4px;
                background-color: #f8fafc;
            }}
            QFrame#eventCell:hover {{
                border: 1px solid #8aa6d6;
                border-left: 4px solid rgb({color.red()}, {color.green()}, {color.blue()});
                background-color: #ffffff;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        
        event_number = str(self._event.event_number or "").strip()
        event_text = f"Event {event_number}" if event_number else "Event"
        info_label = QLabel(f"{self._event.channel} | {event_text} | {self._event.detector}")
        info_label.setStyleSheet("color: #1f2937; font-size: 10px; font-weight: bold;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forward_clicks_from(info_label)
        layout.addWidget(info_label)
        
        # Waveform plot
        self._waveform_plot = pg.PlotWidget()
        self._waveform_plot.setBackground("#ffffff")
        self._waveform_plot.setMinimumHeight(60)
        self._waveform_plot.setMaximumHeight(80)
        
        hide_plot_axes(self._waveform_plot)
        disable_plot_mouse(self._waveform_plot)
        self._forward_clicks_from(self._waveform_plot)
        if hasattr(self._waveform_plot, "viewport"):
            self._forward_clicks_from(self._waveform_plot.viewport())
        
        # Plot waveform if available, otherwise show placeholder
        if self._event.waveform is not None and len(self._event.waveform) > 0:
            waveform = np.asarray(self._event.waveform, dtype=float).reshape(-1)
            start_s = float(self._event.waveform_start if self._event.waveform_start is not None else self._event.start)
            end_s = float(self._event.waveform_end if self._event.waveform_end is not None else self._event.end)
            times = _valid_times_for_waveform(self._event.waveform_times, waveform)
            if times is None:
                times = waveform_time_axis(waveform, start_s, end_s)
            plot_start_s, plot_end_s = get_event_context_window(self._event)
            y_min, y_max = waveform_y_bounds(waveform)
            add_event_region(self._waveform_plot, self._event.start, self._event.end, y_min, y_max)
            self._waveform_plot.plot(
                times,
                waveform,
                pen=pg.mkPen(color=get_neutral_waveform_color(dark_mode=False), width=1),
            )
            set_plot_x_range(self._waveform_plot, plot_start_s, plot_end_s)
        else:
            add_unavailable_label(self._waveform_plot)
        
        layout.addWidget(self._waveform_plot)
        
        # Time info
        time_label = QLabel(f"{self._event.start:.3f}s - {self._event.end:.3f}s")
        time_label.setStyleSheet("color: #4b5563; font-size: 9px;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forward_clicks_from(time_label)
        layout.addWidget(time_label)
        
    def _forward_clicks_from(self, widget: QWidget) -> None:
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.clicked.emit(self._event)
            return True
        return super().eventFilter(watched, event)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._event)
        super().mousePressEvent(event)
    
    @property
    def event(self) -> ExpertEvent:
        return self._event


class ZoomedEventView(QWidget):
    """
    Zoomed view of a single event with larger waveform display.
    """
    
    backClicked = Signal()  # Signal to return to grid
    nextEvent = Signal()    # Signal for next event (n key)
    prevEvent = Signal()    # Signal for previous event (b key)
    classChanged = Signal(ExpertEvent, str)
    contextWindowChanged = Signal(ExpertEvent, float)
    
    def __init__(
        self,
        event: ExpertEvent,
        parent=None,
        *,
        review_class_options: list[str] | tuple[str, ...] | None = None,
    ):
        super().__init__(parent)
        self._event = event
        self._review_class_options = list(
            review_class_options or STANDARD_HFO_REVIEW_CLASS_OPTIONS
        )
        self._setup_ui()
    
    def _setup_ui(self):
        self.setStyleSheet("background-color: #f3f6fa;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Header with event info
        self._header = QLabel()
        self._header.setStyleSheet("color: #111827; font-size: 15px; font-weight: bold;")
        self._header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._header)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Filtered band:")
        filter_label.setStyleSheet("color: #374151; font-size: 12px;")
        filter_row.addWidget(filter_label)

        self._display_filter = QComboBox()
        self._display_filter.addItems(list(DISPLAY_FILTERS.keys()))
        self._display_filter.setCurrentText(DEFAULT_DISPLAY_FILTER)
        self._display_filter.setStyleSheet("""
            QComboBox {
                background-color: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 150px;
            }
            QComboBox:hover { border-color: #94a3b8; }
        """)
        self._display_filter.currentTextChanged.connect(lambda _text: self._refresh_event())
        filter_row.addWidget(self._display_filter)

        context_label = QLabel("Context:")
        context_label.setStyleSheet("color: #374151; font-size: 12px;")
        filter_row.addWidget(context_label)
        self._context_combo = QComboBox()
        for seconds in EVENT_CONTEXT_WINDOW_CHOICES_SECONDS:
            self._context_combo.addItem(f"{seconds:g} s", userData=float(seconds))
        self._context_combo.setCurrentText("0.5 s")
        self._context_combo.setStyleSheet(self._display_filter.styleSheet())
        self._context_combo.currentIndexChanged.connect(self._on_context_changed)
        filter_row.addWidget(self._context_combo)
        analysis_label = QLabel("Analysis:")
        analysis_label.setStyleSheet("color: #374151; font-size: 12px;")
        filter_row.addWidget(analysis_label)
        self._analysis_combo = QComboBox()
        self._analysis_combo.addItem("Spectrogram", userData="spectrogram")
        self._analysis_combo.addItem("FFT", userData="fft")
        self._analysis_combo.setStyleSheet(self._display_filter.styleSheet())
        self._analysis_combo.currentIndexChanged.connect(lambda _index: self._refresh_event())
        filter_row.addWidget(self._analysis_combo)
        filter_row.addStretch()

        self._prev_btn = QPushButton("Previous")
        self._next_btn = QPushButton("Next")
        for button in (self._prev_btn, self._next_btn):
            button.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    color: #111827;
                    border: 1px solid #cbd5e1;
                    padding: 7px 12px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #eef2f7; }
            """)
        self._prev_btn.clicked.connect(self.prevEvent.emit)
        self._next_btn.clicked.connect(self.nextEvent.emit)
        filter_row.addWidget(self._prev_btn)
        filter_row.addWidget(self._next_btn)

        self._back_btn = QPushButton("Back to Grid")
        self._back_btn.setStyleSheet("""
            QPushButton {
                background-color: #1f2937;
                color: #ffffff;
                border: 1px solid #1f2937;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #374151;
            }
        """)
        self._back_btn.clicked.connect(self.backClicked.emit)
        filter_row.addWidget(self._back_btn)
        layout.addLayout(filter_row)
        
        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        plots_panel = QWidget()
        plots_layout = QVBoxLayout(plots_panel)
        plots_layout.setContentsMargins(0, 0, 0, 0)
        plots_layout.setSpacing(8)
        self._raw_plot = self._make_review_plot("Raw signal")
        self._filtered_plot = self._make_review_plot("Filtered 80-300 Hz")
        self._analysis_plot = self._make_review_plot("Spectrogram")
        for plot in (self._raw_plot, self._filtered_plot, self._analysis_plot):
            plots_layout.addWidget(plot, 1)
        content_row.addWidget(plots_panel, 1)

        self._detail_panel = QFrame()
        self._detail_panel.setObjectName("hfoZoomDetailPanel")
        self._detail_panel.setMinimumWidth(260)
        self._detail_panel.setMaximumWidth(340)
        self._detail_panel.setStyleSheet("""
            QFrame#hfoZoomDetailPanel {
                background-color: #ffffff;
                border: 1px solid #d8dde6;
                border-radius: 4px;
            }
        """)
        detail_layout = QVBoxLayout(self._detail_panel)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        detail_layout.setSpacing(8)
        self._time_info = QLabel()
        self._time_info.setStyleSheet("color: #374151; font-size: 12px;")
        self._time_info.setWordWrap(True)
        detail_layout.addWidget(self._time_info)
        self._stats_info = QLabel()
        self._stats_info.setStyleSheet("color: #111827; font-size: 12px;")
        self._stats_info.setWordWrap(True)
        detail_layout.addWidget(self._stats_info)
        review_label = QLabel("Official class")
        review_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        detail_layout.addWidget(review_label)
        self._class_combo = QComboBox()
        self._class_combo.addItems(self._review_class_options)
        self._class_combo.setStyleSheet("""
            QComboBox {
                background-color: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 5px 8px;
            }
            QComboBox:hover { border-color: #94a3b8; }
        """)
        self._class_combo.currentTextChanged.connect(self._on_class_changed)
        detail_layout.addWidget(self._class_combo)
        self._delete_btn = QPushButton("Delete / exclude event")
        self._delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #991b1b;
                border: 1px solid #fecaca;
                padding: 6px 10px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #fff1f2; }
        """)
        self._delete_btn.clicked.connect(lambda: self._on_class_changed("deleted"))
        detail_layout.addWidget(self._delete_btn)
        self._model_proposition = QLabel()
        self._model_proposition.setStyleSheet("color: #4b5563; font-size: 11px;")
        self._model_proposition.setWordWrap(True)
        detail_layout.addWidget(self._model_proposition)
        detail_layout.addStretch()
        content_row.addWidget(self._detail_panel)

        layout.addLayout(content_row, 1)

        self._refresh_event()
        
        # Instructions
        instructions = QLabel("Press n for next, b for previous, or Esc/Return to go back")
        instructions.setStyleSheet("color: #6b7280; font-size: 11px;")
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(instructions)
    
    def set_event(self, event: ExpertEvent):
        """Update the displayed event."""
        self._event = event
        self._sync_context_combo_to_event()
        self._refresh_event()
        self.update()

    def _refresh_event(self):
        self._sync_context_combo_to_event()
        event_number = str(self._event.event_number or "").strip()
        event_text = f"Event {event_number}" if event_number else "Event"
        self._header.setText(
            f"{self._event.channel} | {event_text} | "
            f"Detector: {self._event.detector}"
        )
        self._time_info.setText(
            f"Start: {self._event.start:.6f}s | "
            f"End: {self._event.end:.6f}s | "
            f"Duration: {self._event.duration*1000:.2f}ms | "
            f"Window: {float(self._event.review_context_window_s) * 1000:.0f}ms centered\n"
            f"Band: {self._event.band_label or '80-300 Hz'}\n"
            f"Boundary warning: {'yes' if self._event.boundary_warning else 'no'}"
        )
        self._stats_info.setText(
            "Classifier probabilities\n"
            f"Accepted HFO: {self._format_optional_probability(self._event.real_hfo_probability)}\n"
            f"Artifact: {self._format_optional_probability(self._event.artifact_probability)}\n"
            f"Spike association: {self._format_optional_probability(self._event.spike_hfo_probability)}\n"
            f"Status: {self._event.classification_status or 'unknown'}"
        )
        self._model_proposition.setText(
            f"Classifier proposition: {self._event.model_label}\n"
            f"Review status: {self._event.manual_review_status or 'unreviewed'}"
        )
        self._class_combo.blockSignals(True)
        self._class_combo.setCurrentText(self._event.review_label)
        self._class_combo.blockSignals(False)

        for plot in (self._raw_plot, self._filtered_plot, self._analysis_plot):
            plot.clear()
        start_s = float(self._event.start)
        end_s = float(self._event.end)
        if end_s <= start_s:
            end_s = start_s + 1e-6

        if self._event.waveform is not None and len(self._event.waveform) > 0:
            waveform = np.asarray(self._event.waveform, dtype=float).reshape(-1)
            waveform_start = float(self._event.waveform_start if self._event.waveform_start is not None else start_s)
            waveform_end = float(self._event.waveform_end if self._event.waveform_end is not None else end_s)
            times = _valid_times_for_waveform(self._event.waveform_times, waveform)
            if times is None:
                times = waveform_time_axis(waveform, waveform_start, waveform_end)
            band_name = str(self._display_filter.currentText())
            display_waveform = _filtered_waveform_for_display(
                waveform,
                times,
                DISPLAY_FILTERS.get(band_name, DISPLAY_FILTERS["Default"]),
            )
            plot_start_s, plot_end_s = get_event_context_window(self._event)
            self._draw_signal_panel(
                self._raw_plot,
                times,
                waveform,
                start_s,
                end_s,
                plot_start_s,
                plot_end_s,
            )
            self._draw_signal_panel(
                self._filtered_plot,
                times,
                display_waveform,
                start_s,
                end_s,
                plot_start_s,
                plot_end_s,
            )
            analysis_mode = str(self._analysis_combo.currentData() or "spectrogram")
            if analysis_mode == "fft":
                self._analysis_plot.setTitle("FFT", color="#111827", size="10pt")
                self._draw_fft_panel(
                    self._analysis_plot,
                    times,
                    waveform,
                )
            else:
                self._analysis_plot.setTitle("Spectrogram", color="#111827", size="10pt")
                self._draw_time_frequency_panel(
                    self._analysis_plot,
                    times,
                    display_waveform,
                    start_s,
                    end_s,
                    plot_start_s,
                    plot_end_s,
                )
        else:
            for plot in (self._raw_plot, self._filtered_plot, self._analysis_plot):
                add_unavailable_label(plot)

        self._raw_plot.setLabel("bottom", "Time", units="s")
        self._raw_plot.setLabel("left", "Raw", units="uV")
        self._filtered_plot.setLabel("bottom", "Time", units="s")
        self._filtered_plot.setLabel("left", "Filtered", units="uV")
        if str(self._analysis_combo.currentData() or "spectrogram") == "fft":
            self._analysis_plot.setLabel("bottom", "Frequency", units="Hz")
            self._analysis_plot.setLabel("left", "Amplitude")
        else:
            self._analysis_plot.setLabel("bottom", "Time", units="s")
            self._analysis_plot.setLabel("left", "Frequency", units="Hz")
        for plot in (self._raw_plot, self._filtered_plot, self._analysis_plot):
            plot.repaint()

    def _make_review_plot(self, title: str) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setBackground("#ffffff")
        plot.setMinimumHeight(120)
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.setTitle(title, color="#111827", size="10pt")
        return plot

    def _sync_context_combo_to_event(self) -> None:
        value = float(getattr(self._event, "review_context_window_s", EVENT_CONTEXT_WINDOW_SECONDS) or EVENT_CONTEXT_WINDOW_SECONDS)
        closest_index = 0
        closest_delta = float("inf")
        for idx in range(self._context_combo.count()):
            candidate = float(self._context_combo.itemData(idx) or EVENT_CONTEXT_WINDOW_SECONDS)
            delta = abs(candidate - value)
            if delta < closest_delta:
                closest_index = idx
                closest_delta = delta
        self._context_combo.blockSignals(True)
        self._context_combo.setCurrentIndex(closest_index)
        self._context_combo.blockSignals(False)

    def _on_context_changed(self, _index: int) -> None:
        raw_window_s = self._context_combo.currentData()
        try:
            window_s = float(
                raw_window_s
                if raw_window_s is not None
                else EVENT_CONTEXT_WINDOW_SECONDS
            )
        except (TypeError, ValueError):
            window_s = EVENT_CONTEXT_WINDOW_SECONDS
        self._event.review_context_window_s = window_s
        self.contextWindowChanged.emit(self._event, window_s)

    def _draw_signal_panel(
        self,
        plot: pg.PlotWidget,
        times: np.ndarray,
        waveform: np.ndarray,
        event_start_s: float,
        event_end_s: float,
        plot_start_s: float,
        plot_end_s: float,
    ) -> None:
        y_min, y_max = waveform_y_bounds(waveform)
        add_event_region(plot, event_start_s, event_end_s, y_min, y_max, self._event.review_color)
        plot.plot(
            times,
            waveform,
            pen=pg.mkPen(color=get_neutral_waveform_color(dark_mode=False), width=1.6),
        )
        set_plot_x_range(plot, plot_start_s, plot_end_s)
        set_plot_y_range(plot, y_min, y_max)

    def _draw_time_frequency_panel(
        self,
        plot: pg.PlotWidget,
        times: np.ndarray,
        waveform: np.ndarray,
        event_start_s: float,
        event_end_s: float,
        plot_start_s: float,
        plot_end_s: float,
    ) -> None:
        sfreq = _sample_rate_from_times(times)
        if sfreq is None or waveform.size < 16:
            add_unavailable_label(plot, "Time-frequency unavailable")
            return
        target_window_samples = int(round(0.064 * float(sfreq)))
        nperseg = min(int(waveform.size), max(32, min(128, target_window_samples)))
        if nperseg >= int(waveform.size):
            nperseg = max(16, int(waveform.size // 2))
        noverlap = min(nperseg - 1, int(round(nperseg * 0.75)))
        try:
            freqs, rel_times, power = spectrogram(
                np.asarray(waveform, dtype=float) - float(np.nanmean(waveform)),
                fs=float(sfreq),
                window="hann",
                nperseg=nperseg,
                noverlap=noverlap,
                detrend="constant",
                scaling="density",
                mode="psd",
            )
        except ValueError:
            add_unavailable_label(plot, "Time-frequency unavailable")
            return
        if power.size == 0 or rel_times.size == 0 or freqs.size == 0:
            add_unavailable_label(plot, "Time-frequency unavailable")
            return
        freq_mask = freqs <= min(SPECTROGRAM_FREQ_MAX_HZ, 0.5 * float(sfreq))
        freqs = freqs[freq_mask]
        power = power[freq_mask, :]
        if power.size == 0:
            add_unavailable_label(plot, "Time-frequency unavailable")
            return
        power_db = 10.0 * np.log10(np.maximum(power, 1e-18))
        finite = power_db[np.isfinite(power_db)]
        if finite.size:
            high_level = float(np.percentile(finite, 99.0))
            low_level = max(float(np.percentile(finite, 5.0)), high_level - SPECTROGRAM_DYNAMIC_RANGE_DB)
            if high_level <= low_level:
                high_level = low_level + 1.0
        else:
            low_level, high_level = -120.0, -60.0
        image = pg.ImageItem(axisOrder="row-major")
        image.setLookupTable(spectrogram_lookup_table())
        image.setImage(power_db, autoLevels=False, levels=(low_level, high_level))
        time_bin_s = float(np.median(np.diff(rel_times))) if rel_times.size >= 2 else float(nperseg) / float(sfreq)
        freq_bin_hz = float(np.median(np.diff(freqs))) if freqs.size >= 2 else float(sfreq) / float(nperseg)
        start_time = float(times[0]) + float(rel_times[0]) - 0.5 * time_bin_s
        end_time = float(times[0]) + float(rel_times[-1]) + 0.5 * time_bin_s
        freq_start = float(freqs[0]) if freqs.size else 0.0
        freq_end = (float(freqs[-1]) + 0.5 * freq_bin_hz) if freqs.size else 1.0
        image.setRect(QRectF(start_time, freq_start, max(1e-6, end_time - start_time), max(1e-6, freq_end - freq_start)))
        plot.addItem(image)
        for x_pos in (float(event_start_s), float(event_end_s)):
            line = pg.InfiniteLine(
                pos=x_pos,
                angle=90,
                pen=pg.mkPen(color=(255, 255, 255, 210), width=1.2),
                movable=False,
            )
            line.setZValue(20)
            plot.addItem(line)
        set_plot_x_range(plot, plot_start_s, plot_end_s)
        set_plot_y_range(plot, 0.0, max(1.0, freq_end))

    def _draw_fft_panel(
        self,
        plot: pg.PlotWidget,
        times: np.ndarray,
        waveform: np.ndarray,
    ) -> None:
        sfreq = _sample_rate_from_times(times)
        if sfreq is None or waveform.size < 4:
            add_unavailable_label(plot, "FFT unavailable")
            return
        centered = waveform - float(np.nanmean(waveform))
        freqs = np.fft.rfftfreq(int(centered.size), d=1.0 / float(sfreq))
        amplitude = np.abs(np.fft.rfft(centered))
        mask = freqs <= min(500.0, 0.5 * float(sfreq))
        if not np.any(mask):
            add_unavailable_label(plot, "FFT unavailable")
            return
        plot.plot(freqs[mask], amplitude[mask], pen=pg.mkPen(color="#202020", width=1.4))
        max_amp = float(np.nanmax(amplitude[mask])) if np.any(np.isfinite(amplitude[mask])) else 1.0
        set_plot_x_range(plot, 0.0, float(freqs[mask][-1]))
        set_plot_y_range(plot, 0.0, max(1e-9, max_amp * 1.08))

    def _on_class_changed(self, label: str) -> None:
        self.classChanged.emit(self._event, str(label))

    @staticmethod
    def _format_optional_probability(value: float | None) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(cast(Any, value)):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_N:
            self.nextEvent.emit()
        elif key == Qt.Key.Key_B:
            self.prevEvent.emit()
        elif key == Qt.Key.Key_Escape or key == Qt.Key.Key_Return:
            self.backClicked.emit()
        else:
            super().keyPressEvent(event)


class ExpertEventGrid(QWidget):
    """
    Main widget for displaying HFO events in a grid format.
    
    Signals:
        eventClicked(ExpertEvent): Emitted when an event cell is clicked
        eventSelected(ExpertEvent): Emitted when an event is selected (for navigation)
        requestJumpToTime(float, str): Emitted to request main viewer jump to time/channel
    """
    
    eventClicked = Signal(ExpertEvent)
    eventSelected = Signal(ExpertEvent)
    requestJumpToTime = Signal(float, str)  # time, channel
    filteredEventsChanged = Signal(object)
    eventClassChanged = Signal(object)
    
    def __init__(
        self,
        parent=None,
        *,
        title: str = "Expert Event Grid",
        review_class_options: list[str] | tuple[str, ...] | None = None,
    ):
        super().__init__(parent)
        self._title = str(title)
        self._review_class_options = list(
            review_class_options or STANDARD_HFO_REVIEW_CLASS_OPTIONS
        )
        self._all_events: List[ExpertEvent] = []
        self._events: List[ExpertEvent] = []
        self._current_page: int = 0
        self._total_pages: int = 0
        self._is_zoomed: bool = False
        self._zoomed_view: Optional[ZoomedEventView] = None
        self._current_zoomed_index: int = 0
        
        # Callback for fetching waveform data from raw
        self._get_waveform_callback: Optional[Callable] = None
        self._raw = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        self.setMinimumSize(720, 520)
        self.setAutoFillBackground(True)
        self.setStyleSheet("""
            QWidget { background-color: #f3f6fa; color: #111827; }
            QScrollArea { background-color: #f3f6fa; border: none; }
            QScrollArea > QWidget > QWidget { background-color: #f3f6fa; }
            QLabel { color: #111827; }
        """)
        
        # Main layout
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(10, 10, 10, 10)
        self._main_layout.setSpacing(10)
        
        # Header
        header = QLabel(self._title)
        header.setStyleSheet("""
            color: #111827;
            font-size: 16px;
            font-weight: bold;
        """)
        self._main_layout.addWidget(header)
        
        # Info bar
        self._info_label = QLabel("No events loaded")
        self._info_label.setStyleSheet("color: #4b5563; font-size: 12px;")
        self._main_layout.addWidget(self._info_label)

        self._filter_widget = QWidget()
        filter_layout = QHBoxLayout(self._filter_widget)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        self._channel_filter = QComboBox()
        self._type_filter = QComboBox()
        self._frequency_filter = QComboBox()
        self._order_filter = QComboBox()
        self._type_filter.addItems(["All active", *self._review_class_options])
        self._frequency_filter.addItem("All ranges", userData=None)
        self._frequency_filter.addItem("80-300 Hz", userData=(80.0, 300.0))
        self._frequency_filter.addItem("Ripple", userData=(80.0, 250.0))
        self._frequency_filter.addItem("Fast ripple", userData=(250.0, 500.0))
        self._order_filter.addItems(["Channel order", "Time order"])
        for combo in (
            self._channel_filter,
            self._type_filter,
            self._frequency_filter,
            self._order_filter,
        ):
            combo.setStyleSheet("""
                QComboBox {
                    background-color: #ffffff;
                    color: #111827;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    padding: 4px 8px;
                    min-width: 140px;
                }
            """)
            combo.currentTextChanged.connect(lambda _text: self._apply_event_filters())
        filter_layout.addWidget(QLabel("Channel"))
        filter_layout.addWidget(self._channel_filter)
        filter_layout.addWidget(QLabel("Type"))
        filter_layout.addWidget(self._type_filter)
        filter_layout.addWidget(QLabel("Range"))
        filter_layout.addWidget(self._frequency_filter)
        filter_layout.addWidget(QLabel("Order"))
        filter_layout.addWidget(self._order_filter)
        filter_layout.addStretch()
        self._main_layout.addWidget(self._filter_widget)
        
        # Navigation controls
        nav_layout = QHBoxLayout()
        
        self._prev_btn = QPushButton("Previous (b)")
        self._prev_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #eef2f7; }
            QPushButton:disabled { background-color: #eef2f7; color: #94a3b8; }
        """)
        self._prev_btn.clicked.connect(self._on_prev_page)
        
        self._page_label = QLabel("Page 0 / 0")
        self._page_label.setStyleSheet("color: #111827;")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self._next_btn = QPushButton("Next (n)")
        self._next_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #eef2f7; }
            QPushButton:disabled { background-color: #eef2f7; color: #94a3b8; }
        """)
        self._next_btn.clicked.connect(self._on_next_page)
        
        nav_layout.addWidget(self._prev_btn)
        nav_layout.addWidget(self._page_label, 1)
        nav_layout.addWidget(self._next_btn)
        
        self._main_layout.addLayout(nav_layout)
        
        # Grid container (scroll area)
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setStyleSheet("background-color: #f3f6fa; border: none;")
        
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background-color: #f3f6fa;")
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(10)
        
        self._grid_scroll.setWidget(self._grid_widget)
        self._main_layout.addWidget(self._grid_scroll, 1)
        
        # Zoomed view container (hidden initially)
        self._zoomed_container = QWidget()
        self._zoomed_container.setVisible(False)
        self._zoomed_layout = QVBoxLayout(self._zoomed_container)
        self._main_layout.addWidget(self._zoomed_container, 1)
        
        self._update_navigation()
    
    def _create_legend_item(self, text: str, color: QColor) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"""
            color: #111827;
            font-size: 11px;
            font-weight: bold;
            border: 1px solid rgb({color.red()}, {color.green()}, {color.blue()});
            border-radius: 3px;
            padding: 2px 6px;
            background-color: transparent;
        """)
        return label
    
    def set_raw(self, raw) -> None:
        """Set the MNE Raw object for waveform extraction."""
        self._raw = raw
    
    def set_waveform_callback(self, callback: Callable) -> None:
        """
        Set a callback function to fetch waveform data from raw.
        
        The callback should accept (channel_name, start_time, end_time)
        and return either waveform samples or (waveform_samples, sample_times).
        """
        self._get_waveform_callback = callback

    def set_events(self, events: List[ExpertEvent], *, title: str = "Loaded events") -> None:
        """Load already parsed events into the review grid."""
        self._all_events = list(events)
        self._events_title = str(title)
        self._current_page = 0
        self._is_zoomed = False
        self._grid_scroll.setVisible(True)
        self._zoomed_container.setVisible(False)
        self._populate_channel_filter()
        self._apply_event_filters()

    def _populate_channel_filter(self) -> None:
        current = str(self._channel_filter.currentText() or "All channels")
        channels = sorted({str(event.channel) for event in self._all_events})
        self._channel_filter.blockSignals(True)
        self._channel_filter.clear()
        self._channel_filter.addItem("All channels")
        for channel in channels:
            self._channel_filter.addItem(channel)
        if current in {"All channels", *channels}:
            self._channel_filter.setCurrentText(current)
        self._channel_filter.blockSignals(False)

    def _event_filter_type(self, event: ExpertEvent) -> str:
        label = str(event.review_label or "").strip().lower()
        normalized = label.replace("_", "-").replace(" ", "-")
        if normalized in {"deleted", "excluded"}:
            return "deleted"
        if "artifact" in normalized:
            return "artifact"
        if normalized in {"unclassified", "candidate", "unknown", "not-classified"}:
            return "unclassified"
        if normalized in {"spike-ehfo", "spkehfo", "spk-ehfo", "spk-ehfo"}:
            return "spike-eHFO"
        if normalized in {"ehfo", "e-hfo"}:
            return "eHFO"
        if normalized == "hfo":
            return "HFO"
        if normalized in {"hfo", "non-spike-hfo", "non-spkhfo", "non-spk-hfo"}:
            return "non-spike HFO"
        if "non-spike" in normalized or "non-spk" in normalized:
            return "non-spike HFO"
        if normalized in {"spike-hfo", "spkhfo", "spk-hfo", "spkhfo"}:
            return "spike-HFO"
        if "spike-hfo" in normalized or "spkhfo" in normalized or "spk-hfo" in normalized:
            return "spike-HFO"
        return "non-spike HFO"

    @staticmethod
    def _event_frequency_matches(event: ExpertEvent, frequency_filter: object) -> bool:
        band = ExpertEventGrid._frequency_filter_band(frequency_filter)
        if band is None:
            return True
        if event.low_freq_hz is None or event.high_freq_hz is None:
            return False
        try:
            event_low = float(event.low_freq_hz)
            event_high = float(event.high_freq_hz)
        except (TypeError, ValueError):
            return False
        filter_low, filter_high = band
        return abs(event_low - filter_low) < 1e-6 and abs(event_high - filter_high) < 1e-6

    @staticmethod
    def _frequency_filter_band(value: object) -> tuple[float, float] | None:
        if value is None:
            return None
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return None
        low_value = value[0]
        high_value = value[1]
        if low_value is None or high_value is None:
            return None
        try:
            low = float(cast(Any, low_value))
            high = float(cast(Any, high_value))
        except (TypeError, ValueError):
            return None
        return low, high

    def _apply_event_filters(self) -> None:
        channel_filter = str(self._channel_filter.currentText() or "All channels")
        type_filter = str(self._type_filter.currentText() or "All active")
        frequency_filter = self._frequency_filter.currentData()
        order_filter = str(self._order_filter.currentText() or "Channel order")

        events = list(self._all_events)
        if channel_filter != "All channels":
            events = [event for event in events if str(event.channel) == channel_filter]
        if type_filter == "All active":
            events = [event for event in events if self._event_filter_type(event) != "deleted"]
        else:
            events = [event for event in events if self._event_filter_type(event) == type_filter]
        events = [
            event for event in events if self._event_frequency_matches(event, frequency_filter)
        ]

        if order_filter == "Time order":
            events.sort(key=lambda e: (float(e.start), str(e.channel), str(e.event_number)))
            order_text = "Sorted by time"
        else:
            events.sort(key=lambda e: (str(e.channel), float(e.start), str(e.event_number)))
            order_text = "Sorted by channel"

        self._events = events
        self._total_pages = (len(self._events) + GRID_TOTAL - 1) // GRID_TOTAL if self._events else 0
        self._current_page = 0
        title = getattr(self, "_events_title", "Loaded events")
        type_counts = self._event_type_counts(self._all_events)
        count_text = self._event_count_text(type_counts)
        if self._events:
            self._info_label.setText(
                f"{title}: {len(self._events)} / {len(self._all_events)} events | "
                f"{order_text} | {count_text}"
            )
        else:
            self._info_label.setText(
                f"{title}: no events match the selected filters | {count_text}"
            )
        if self._is_zoomed:
            self._on_back_to_grid()
        self._update_grid()
        self.filteredEventsChanged.emit(list(self._events))

    def _event_type_counts(self, events: list[ExpertEvent]) -> dict[str, int]:
        counts = {label: 0 for label in self._review_class_options}
        for event in events:
            event_type = self._event_filter_type(event)
            counts[event_type] = counts.get(event_type, 0) + 1
        return counts

    def _event_count_text(self, type_counts: dict[str, int]) -> str:
        labels = [
            label
            for label in self._review_class_options
            if int(type_counts.get(label, 0)) > 0
        ]
        if not labels:
            labels = ["artifact", "non-spike HFO", "spike-HFO", "unclassified", "deleted"]
        return ", ".join(f"{label}: {int(type_counts.get(label, 0))}" for label in labels)

    def _coerce_waveform_result(self, result) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if result is None:
            return None, None

        times = None
        waveform = result
        if isinstance(result, dict):
            waveform = result.get("waveform", result.get("data"))
            times = result.get("times")
        elif isinstance(result, tuple) and len(result) == 2:
            waveform, times = result

        if waveform is None:
            return None, None

        waveform = np.asarray(waveform, dtype=float).reshape(-1)
        if waveform.size == 0:
            return None, None

        return waveform, _valid_times_for_waveform(times, waveform)

    def _fetch_waveform_window(
        self,
        event: ExpertEvent,
        start_s: float,
        end_s: float,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if self._get_waveform_callback is not None:
            try:
                result = self._get_waveform_callback(event.channel, start_s, end_s)
                return self._coerce_waveform_result(result)
            except Exception:
                pass

        return None, None

    def _has_waveform_covering(
        self,
        waveform: Optional[np.ndarray],
        waveform_start: float | None,
        waveform_end: float | None,
        start_s: float,
        end_s: float,
    ) -> bool:
        if waveform is None or len(waveform) == 0:
            return False
        if waveform_start is None or waveform_end is None:
            return False
        eps = 1e-9
        return float(waveform_start) <= float(start_s) + eps and float(waveform_end) >= float(end_s) - eps

    def _ensure_event_waveform(self, event: ExpertEvent, *, include_wide: bool = False) -> None:
        """Populate waveform fields using context-aware callback requests."""
        context_start, context_end = get_event_context_window(event)

        if event.waveform is not None and event.waveform_start is None:
            event.waveform = np.asarray(event.waveform, dtype=float).reshape(-1)
            event.waveform_start = float(event.start)
            event.waveform_end = float(event.end)
            event.waveform_times = None

        if not self._has_waveform_covering(
            event.waveform,
            event.waveform_start,
            event.waveform_end,
            context_start,
            context_end,
        ):
            # Prefer raw callback data because CSV waveforms are usually event-only snippets.
            waveform, times = self._fetch_waveform_window(event, context_start, context_end)
            if waveform is not None:
                event.waveform = waveform
                event.waveform_start = context_start
                event.waveform_end = context_end
                event.waveform_times = times
                event.waveform_unavailable = False
            else:
                event.waveform_unavailable = True

        if not include_wide:
            return

        wide_start, wide_end = get_event_wide_context_window(event)
        if self._has_waveform_covering(
            event.wide_waveform,
            event.wide_waveform_start,
            event.wide_waveform_end,
            wide_start,
            wide_end,
        ):
            return

        wide_waveform, wide_times = self._fetch_waveform_window(event, wide_start, wide_end)
        if wide_waveform is not None:
            event.wide_waveform = wide_waveform
            event.wide_waveform_start = wide_start
            event.wide_waveform_end = wide_end
            event.wide_waveform_times = wide_times
            event.wide_waveform_unavailable = False
        else:
            event.wide_waveform_unavailable = True
    
    def _update_grid(self):
        """Update the grid display with events for the current page."""
        # Clear existing cells
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        
        # Get events for this page
        start_idx = self._current_page * GRID_TOTAL
        end_idx = min(start_idx + GRID_TOTAL, len(self._events))
        page_events = self._events[start_idx:end_idx]
        
        # Create cells
        for i, event in enumerate(page_events):
            row = i // GRID_COLS
            col = i % GRID_COLS
            self._ensure_event_waveform(event, include_wide=False)
            
            cell = EventCellWidget(event)
            cell.clicked.connect(self._on_event_clicked)
            self._grid_layout.addWidget(cell, row, col)
        
        self._update_navigation()
    
    def _update_navigation(self):
        """Update navigation button states and page label."""
        if self._is_zoomed:
            self._prev_btn.setText("Previous event (b)")
            self._next_btn.setText("Next event (n)")
            self._page_label.setText(
                f"Event {self._current_zoomed_index + 1} / {max(1, len(self._events))}"
            )
            self._prev_btn.setEnabled(self._current_zoomed_index > 0)
            self._next_btn.setEnabled(self._current_zoomed_index < len(self._events) - 1)
            return

        self._prev_btn.setText("Previous page (b)")
        self._next_btn.setText("Next page (n)")
        self._page_label.setText(f"Page {self._current_page + 1} / {max(1, self._total_pages)}")
        self._prev_btn.setEnabled(self._current_page > 0)
        self._next_btn.setEnabled(self._current_page < self._total_pages - 1)
    
    def _on_event_clicked(self, event: ExpertEvent):
        """Handle event cell click - switch to zoomed view."""
        # Find the index of this event
        try:
            self._current_zoomed_index = self._events.index(event)
        except ValueError:
            self._current_zoomed_index = 0
        
        self._show_zoomed_view(event)

    def select_event_at(self, channel_name: str, time_s: float, *, tolerance_s: float = 0.250) -> bool:
        """Open the zoom view for the closest filtered event on a channel near time_s."""
        channel = str(channel_name)
        target = float(time_s)
        best_index: int | None = None
        best_delta = float("inf")
        for idx, event in enumerate(self._events):
            if str(event.channel) != channel:
                continue
            center = 0.5 * (float(event.start) + float(event.end))
            delta = abs(center - target)
            if delta < best_delta:
                best_delta = delta
                best_index = idx
        if best_index is None or best_delta > float(tolerance_s):
            return False
        self._current_zoomed_index = int(best_index)
        self._show_zoomed_view(self._events[self._current_zoomed_index])
        return True
    
    def _show_zoomed_view(self, event: ExpertEvent):
        """Show the zoomed view for a single event."""
        self._is_zoomed = True
        self._ensure_event_waveform(event, include_wide=False)
        
        # Hide grid, show zoomed view
        self._filter_widget.setVisible(False)
        self._grid_scroll.setVisible(False)
        self._zoomed_container.setVisible(True)
        
        # Create or update zoomed view
        if self._zoomed_view is None:
            self._zoomed_view = ZoomedEventView(
                event,
                review_class_options=self._review_class_options,
            )
            self._zoomed_view.backClicked.connect(self._on_back_to_grid)
            self._zoomed_view.nextEvent.connect(self._on_next_event)
            self._zoomed_view.prevEvent.connect(self._on_prev_event)
            self._zoomed_view.classChanged.connect(self._on_event_class_changed)
            self._zoomed_view.contextWindowChanged.connect(self._on_zoom_context_changed)
            self._zoomed_layout.addWidget(self._zoomed_view)
        else:
            self._zoomed_view.set_event(event)
        self._zoomed_view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._zoomed_view.repaint()
        self._update_navigation()
        
        # Emit signal for main viewer to jump
        self.requestJumpToTime.emit(event.start, event.channel)
        self.eventClicked.emit(event)

    def _on_zoom_context_changed(self, event: ExpertEvent, window_s: float) -> None:
        event.review_context_window_s = float(window_s)
        event.waveform_unavailable = False
        context_start, context_end = get_event_context_window(event)
        if not self._has_waveform_covering(
            event.waveform,
            event.waveform_start,
            event.waveform_end,
            context_start,
            context_end,
        ):
            event.waveform = None
            event.waveform_start = None
            event.waveform_end = None
            event.waveform_times = None
        self._ensure_event_waveform(event, include_wide=False)
        if self._zoomed_view is not None:
            self._zoomed_view.set_event(event)

    def _on_event_class_changed(self, event: ExpertEvent, label: str) -> None:
        normalized = self._normalize_event_class(label)
        event.manual_class = normalized
        event.manual_review_status = "deleted" if normalized == "deleted" else "reviewed"
        event.artifact = normalized not in {"artifact", "deleted", "unclassified"}
        event.spike = normalized in {"spike-HFO", "spike-eHFO"}
        if event.source_event is not None:
            try:
                setattr(event.source_event, "manual_class", normalized)
                setattr(
                    event.source_event,
                    "manual_review_status",
                    "deleted" if normalized == "deleted" else "reviewed",
                )
            except Exception:
                pass
        if self._zoomed_view is not None:
            self._zoomed_view.set_event(event)
        self._refresh_after_class_change(event)
        self.eventClassChanged.emit(event)

    def _normalize_event_class(self, label: object) -> str:
        text = str(label or "").strip()
        lowered = text.lower().replace("_", "-")
        if lowered in {"deleted", "excluded"}:
            return "deleted"
        if "artifact" in lowered:
            return "artifact"
        if lowered in {"spike-ehfo", "spike ehfo", "spkehfo", "spk-ehfo", "spk ehfo"}:
            return "spike-eHFO"
        if lowered in {"ehfo", "e-hfo"}:
            return "eHFO"
        if lowered in {"hfo", "real hfo", "real-hfo"}:
            return "HFO"
        if lowered in {"spike-hfo", "spkhfo", "spk-hfo", "spk hfo", "spike hfo"}:
            return "spike-HFO"
        if lowered in {"non-spike hfo", "non-spkhfo", "non-spk hfo"}:
            return "non-spike HFO"
        return "unclassified"

    def _refresh_after_class_change(self, changed_event: ExpertEvent) -> None:
        channel_filter = str(self._channel_filter.currentText() or "All channels")
        type_filter = str(self._type_filter.currentText() or "All active")
        frequency_filter = self._frequency_filter.currentData()
        order_filter = str(self._order_filter.currentText() or "Channel order")
        events = list(self._all_events)
        if channel_filter != "All channels":
            events = [event for event in events if str(event.channel) == channel_filter]
        if type_filter == "All active":
            events = [event for event in events if self._event_filter_type(event) != "deleted"]
        else:
            events = [event for event in events if self._event_filter_type(event) == type_filter]
        events = [
            event for event in events if self._event_frequency_matches(event, frequency_filter)
        ]
        if order_filter == "Time order":
            events.sort(key=lambda e: (float(e.start), str(e.channel), str(e.event_number)))
            order_text = "Sorted by time"
        else:
            events.sort(key=lambda e: (str(e.channel), float(e.start), str(e.event_number)))
            order_text = "Sorted by channel"
        self._events = events
        self._total_pages = (len(self._events) + GRID_TOTAL - 1) // GRID_TOTAL if self._events else 0
        title = getattr(self, "_events_title", "Loaded events")
        type_counts = self._event_type_counts(self._all_events)
        count_text = self._event_count_text(type_counts)
        if self._events:
            self._info_label.setText(
                f"{title}: {len(self._events)} / {len(self._all_events)} events | "
                f"{order_text} | {count_text}"
            )
        else:
            self._info_label.setText(
                f"{title}: no events match the selected filters | {count_text}"
            )
        if changed_event in self._events:
            self._current_zoomed_index = self._events.index(changed_event)
            self._current_page = self._current_zoomed_index // GRID_TOTAL
            self._update_grid()
            self._update_navigation()
        else:
            self._on_back_to_grid()
            self._current_page = 0
            self._update_grid()
        self.filteredEventsChanged.emit(list(self._events))
    
    def _on_back_to_grid(self):
        """Return to grid view from zoomed view."""
        self._is_zoomed = False
        self._filter_widget.setVisible(True)
        self._grid_scroll.setVisible(True)
        self._zoomed_container.setVisible(False)
        self._update_navigation()
    
    def _on_next_event(self):
        """Navigate to next event in zoomed mode."""
        if self._current_zoomed_index < len(self._events) - 1:
            self._current_zoomed_index += 1
            self._show_zoomed_view(self._events[self._current_zoomed_index])
    
    def _on_prev_event(self):
        """Navigate to previous event in zoomed mode."""
        if self._current_zoomed_index > 0:
            self._current_zoomed_index -= 1
            self._show_zoomed_view(self._events[self._current_zoomed_index])
    
    def _on_prev_page(self):
        """Navigate to previous page in grid mode."""
        if self._is_zoomed:
            self._on_prev_event()
            return
        if self._current_page > 0:
            self._current_page -= 1
            self._update_grid()
    
    def _on_next_page(self):
        """Navigate to next page in grid mode."""
        if self._is_zoomed:
            self._on_next_event()
            return
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._update_grid()
    
    def navigate_next(self):
        """Public method to navigate to next event (for keyboard shortcut)."""
        if self._is_zoomed:
            self._on_next_event()
        else:
            self._on_next_page()
    
    def navigate_prev(self):
        """Public method to navigate to previous event (for keyboard shortcut)."""
        if self._is_zoomed:
            self._on_prev_event()
        else:
            self._on_prev_page()
    
    def keyPressEvent(self, event):
        """Handle keyboard navigation."""
        key = event.key()
        
        if key == Qt.Key.Key_N:
            self.navigate_next()
        elif key == Qt.Key.Key_B:
            self.navigate_prev()
        elif key == Qt.Key.Key_Escape and self._is_zoomed:
            self._on_back_to_grid()
        else:
            super().keyPressEvent(event)
    
    @property
    def is_zoomed(self) -> bool:
        return self._is_zoomed
    
    @property
    def events(self) -> List[ExpertEvent]:
        return self._events

    @property
    def all_events(self) -> List[ExpertEvent]:
        return self._all_events
