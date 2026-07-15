# app/expert_event_grid.py
"""
Expert Event Grid Widget for visualizing expert-reviewed HFO annotations.

This module provides a modular widget that displays expert-reviewed events
in a 6x4 grid format, allowing users to browse through HFO events with
waveform previews, channel info, and review labels.

Integration with MainWindow:
    - Create an ExpertEventGridDialog as a separate review window
    - Call load_events_for_edf(edf_path, events_path) to load events
    - Connect the eventClicked signal to jump to event in main viewer
    - Handle keyboard navigation (n/b keys) in the main window or here
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Callable, List, cast

import numpy as np
import pyqtgraph as pg
from scipy.signal import butter, sosfiltfilt

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QComboBox,
    QDialog, QFileDialog, QMessageBox
)

# Review label colors
COLOR_REJECTED_ARTIFACT = QColor(220, 50, 50)  # red
COLOR_SPK_HFO = QColor(50, 200, 50)            # green
COLOR_NON_SPK_HFO = QColor(50, 100, 220)       # blue

# Grid dimensions
GRID_ROWS = 6
GRID_COLS = 4
GRID_TOTAL = GRID_ROWS * GRID_COLS

# Candidate HFOs are easier to review in a fixed centered context window.
EVENT_CONTEXT_WINDOW_SECONDS = 0.570

EVENT_REGION_BRUSH = (185, 185, 185, 55)
EVENT_MARKER_COLOR = (215, 215, 215, 190)
NEUTRAL_WAVEFORM_DARK = "#f2f2f2"
NEUTRAL_WAVEFORM_LIGHT = "#202020"

DISPLAY_FILTERS: dict[str, tuple[float | None, float | None]] = {
    "Default (unfiltered)": (None, None),
    "HFO 80-500 Hz": (80.0, 500.0),
    "Ripple 80-250 Hz": (80.0, 250.0),
    "Fast ripple 250-500 Hz": (250.0, 500.0),
}
DEFAULT_DISPLAY_FILTER = "HFO 80-500 Hz"


def _matches_edf_file(value: object, edf_file: str) -> bool:
    value_keys = _edf_file_match_keys(value)
    target_keys = _edf_file_match_keys(edf_file)
    return bool(value_keys and target_keys and value_keys.intersection(target_keys))


def _edf_file_match_keys(value: object) -> set[str]:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return set()

    # Event tables may store a full path, a basename, or just the recording id
    # without .edf/.bdf. Compare all stable forms case-insensitively.
    normalized = text.replace("\\", "/")
    name = Path(normalized).name
    stem = Path(name).stem if "." in name else name
    keys = {
        normalized.casefold(),
        name.casefold(),
        stem.casefold(),
    }
    return {key for key in keys if key}


def _event_edf_value(item: dict) -> object:
    for key in (
        "edf_file",
        "edf_path",
        "file",
        "filename",
        "recording",
        "recording_file",
        "raw_file",
        "source_file",
        "bids_file",
        "bids_path",
        "package_bids_edf_path",
        "local_raw_edf_path",
    ):
        value = item.get(key)
        if str(value or "").strip():
            return value
    return ""


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        text = str(value if value is not None else "").strip()
        return float(text) if text else float(default)
    except (TypeError, ValueError):
        return float(default)


def get_event_context_window(
    event: "ExpertEvent",
    window_s: float = EVENT_CONTEXT_WINDOW_SECONDS,
) -> tuple[float, float]:
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
) -> None:
    if end_s <= start_s:
        end_s = start_s + 1e-6
    if y_max <= y_min:
        y_max = y_min + 1e-12

    # Gray marks the candidate interval without letting class color bias waveform review.
    region = pg.BarGraphItem(
        x0=[float(start_s)],
        x1=[float(end_s)],
        y0=[float(y_min)],
        y1=[float(y_max)],
        brush=pg.mkBrush(*EVENT_REGION_BRUSH),
        pen=pg.mkPen(EVENT_MARKER_COLOR, width=1),
    )
    region.setZValue(-10)
    plot_widget.addItem(region)

    for x in (start_s, end_s):
        marker = pg.PlotDataItem(
            [float(x), float(x)],
            [float(y_min), float(y_max)],
            pen=pg.mkPen(EVENT_MARKER_COLOR, width=1),
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
    Represents a single expert-reviewed event.
    
    Attributes:
        edf_file: Source EDF file name
        channel: Channel name (or 'name')
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



    
    @property
    def duration(self) -> float:
        return self.end - self.start
    
    @property
    def review_label(self) -> str:
        """Returns the expert review label using the dataset convention."""
        if not self.artifact:
            return "Rejected artifact"
        if self.spike:
            return "spkHFO"
        return "non-spkHFO"
    
    @property
    def review_color(self) -> QColor:
        """Returns the color for this review label."""
        if not self.artifact:
            return COLOR_REJECTED_ARTIFACT
        if self.spike:
            return COLOR_SPK_HFO
        return COLOR_NON_SPK_HFO


def load_events_from_csv(events_path: Path, edf_file: str | None = None) -> List[ExpertEvent]:
    """
    Load expert events from a CSV or JSON file.
    
    Expected CSV columns:
        - edf_file: Source EDF file name
        - name OR channel: Channel name
        - start OR start_seconds: Start time in seconds
        - end OR end_seconds: End time in seconds
        - detector: Detector name
        - artifact: 1/0 or True/False; 1 means accepted HFO, 0 means rejected artifact
        - spike: 1/0 or True/False; among accepted HFOs, 1 means spike-associated
        - waveform OR hfo_waveforms: Optional waveform data (JSON string)
    
    Args:
        events_path: Path to the events file (CSV or JSON)
        edf_file: EDF file name to filter events
    
    Returns:
        List of ExpertEvent objects sorted by channel, then start time
    """
    import csv
    
    events: list[ExpertEvent] = []
    fallback_events: list[ExpertEvent] = []
    
    # Try JSON first, then CSV
    if events_path.suffix.lower() == '.json':
        with open(events_path, 'r') as f:
            data = json.load(f)
        
        # Handle both list and dict with 'events' key
        if isinstance(data, dict):
            data = data.get('events', [])
        
        for item in data:
            # Handle different column names
            channel = item.get('name') or item.get('channel', 'Unknown')
            start = item.get('start_seconds') or item.get('start') or 0.0
            end = item.get('end_seconds') or item.get('end') or 0.0
            detector = item.get('detector', 'unknown')
            artifact = _parse_bool(item.get('artifact', 0))
            spike = _parse_bool(item.get('spike', 0))
            
            # Parse waveform if present
            waveform = None
            wf_key = 'waveform' if 'waveform' in item else 'hfo_waveforms'
            if wf_key in item and item[wf_key]:
                try:
                    waveform = np.array(json.loads(item[wf_key]))
                except (json.JSONDecodeError, TypeError):
                    pass
            
            row_edf_file = _event_edf_value(item)
            parsed_event = ExpertEvent(
                edf_file=str(row_edf_file or edf_file or ""),
                channel=channel,
                start=_as_float(start),
                end=_as_float(end),
                detector=detector,
                artifact=artifact,
                spike=spike,
                waveform=waveform
            )
            fallback_events.append(parsed_event)
            if edf_file is None or _matches_edf_file(row_edf_file, edf_file):
                events.append(parsed_event)
    else:
        # CSV format
        with open(events_path, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Handle different column names
                channel = row.get('name') or row.get('channel', 'Unknown')
                start = row.get('start_seconds') or row.get('start') or '0.0'
                end = row.get('end_seconds') or row.get('end') or '0.0'
                detector = row.get('detector', 'unknown')
                artifact = _parse_bool(row.get('artifact', 0))
                spike = _parse_bool(row.get('spike', 0))
                
                # Parse waveform if present
                waveform = None
                wf_key = 'waveform' if 'waveform' in row else 'hfo_waveforms'
                if wf_key in row and row[wf_key]:
                    try:
                        waveform = np.array(json.loads(row[wf_key]))
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                row_edf_file = _event_edf_value(row)
                parsed_event = ExpertEvent(
                    edf_file=str(row_edf_file or edf_file or ""),
                    channel=channel,
                    start=_as_float(start),
                    end=_as_float(end),
                    detector=detector,
                    artifact=artifact,
                    spike=spike,
                    waveform=waveform
                )
                fallback_events.append(parsed_event)
                if edf_file is None or _matches_edf_file(row_edf_file, edf_file):
                    events.append(parsed_event)

    if not events and fallback_events:
        events = fallback_events
    
    # Sort by channel name, then by start time
    events.sort(key=lambda e: (e.channel, e.start))
    
    return events


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
                border: 2px solid rgb({color.red()}, {color.green()}, {color.blue()});
                border-radius: 4px;
                background-color: #1e1e1e;
            }}
            QFrame#eventCell:hover {{
                border: 2px solid white;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        
        # Channel name and detector
        info_label = QLabel(f"{self._event.channel} | {self._event.detector}")
        info_label.setStyleSheet("color: #dddddd; font-size: 10px; font-weight: bold;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forward_clicks_from(info_label)
        layout.addWidget(info_label)
        
        # Waveform plot
        self._waveform_plot = pg.PlotWidget()
        self._waveform_plot.setBackground("#1e1e1e")
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
                pen=pg.mkPen(color=get_neutral_waveform_color(dark_mode=True), width=1),
            )
            set_plot_x_range(self._waveform_plot, plot_start_s, plot_end_s)
        else:
            add_unavailable_label(self._waveform_plot)
        
        layout.addWidget(self._waveform_plot)
        
        # Time info
        time_label = QLabel(f"{self._event.start:.3f}s - {self._event.end:.3f}s")
        time_label.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forward_clicks_from(time_label)
        layout.addWidget(time_label)
        
        # Review label
        review_label = QLabel(self._event.review_label)
        review_label.setStyleSheet(f"""
            color: #f5f5f5;
            font-size: 9px;
            font-weight: bold;
            border: 1px solid rgb({color.red()}, {color.green()}, {color.blue()});
            border-radius: 3px;
            padding: 2px 4px;
            background-color: transparent;
        """)
        review_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forward_clicks_from(review_label)
        layout.addWidget(review_label)

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
    
    def __init__(self, event: ExpertEvent, parent=None):
        super().__init__(parent)
        self._event = event
        self._setup_ui()
    
    def _setup_ui(self):
        self.setStyleSheet("background-color: #2b2b2b;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Header with event info
        self._header = QLabel()
        self._header.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        self._header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._header)

        self._review_badge = QLabel()
        self._review_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._review_badge)
        
        # Time info
        self._time_info = QLabel()
        self._time_info.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        self._time_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._time_info)

        filter_row = QHBoxLayout()
        filter_row.addStretch()
        filter_label = QLabel("Display band:")
        filter_label.setStyleSheet("color: #dddddd; font-size: 12px;")
        filter_row.addWidget(filter_label)

        self._display_filter = QComboBox()
        self._display_filter.addItems(list(DISPLAY_FILTERS.keys()))
        self._display_filter.setCurrentText(DEFAULT_DISPLAY_FILTER)
        self._display_filter.setStyleSheet("""
            QComboBox {
                background-color: #3a3a3a;
                color: #ffffff;
                border: 1px solid #666666;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 150px;
            }
            QComboBox:hover { border-color: #888888; }
        """)
        self._display_filter.currentTextChanged.connect(lambda _text: self._refresh_event())
        filter_row.addWidget(self._display_filter)
        filter_row.addStretch()

        self._back_btn = QPushButton("Back to Grid")
        self._back_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #666666;
            }
        """)
        self._back_btn.clicked.connect(self.backClicked.emit)
        filter_row.addWidget(self._back_btn)
        layout.addLayout(filter_row)
        
        # Large waveform plot
        self._waveform_plot = pg.PlotWidget()
        self._waveform_plot.setBackground("#2b2b2b")
        self._waveform_plot.setMinimumHeight(400)
        
        # Show axes
        self._waveform_plot.showGrid(x=True, y=True, alpha=0.3)
        
        layout.addWidget(self._waveform_plot, 1)

        self._refresh_event()
        
        # Instructions
        instructions = QLabel("Press 'n' for next, 'b' for previous, or Esc/Return to go back")
        instructions.setStyleSheet("color: #888888; font-size: 11px;")
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(instructions)
    
    def set_event(self, event: ExpertEvent):
        """Update the displayed event."""
        self._event = event
        self._refresh_event()

    def _refresh_event(self):
        self._header.setText(
            f"Channel: {self._event.channel} | "
            f"Detector: {self._event.detector}"
        )
        color = self._event.review_color
        self._review_badge.setText(self._event.review_label)
        self._review_badge.setStyleSheet(f"""
            color: #ffffff;
            font-size: 12px;
            font-weight: bold;
            border: 1px solid rgb({color.red()}, {color.green()}, {color.blue()});
            border-radius: 4px;
            padding: 3px 8px;
            background-color: transparent;
        """)
        self._time_info.setText(
            f"Start: {self._event.start:.6f}s | "
            f"End: {self._event.end:.6f}s | "
            f"Duration: {self._event.duration*1000:.2f}ms | "
            f"Window: {EVENT_CONTEXT_WINDOW_SECONDS * 1000:.0f}ms centered"
        )

        self._waveform_plot.clear()
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
                DISPLAY_FILTERS.get(band_name, DISPLAY_FILTERS["Default (unfiltered)"]),
            )
            plot_start_s, plot_end_s = get_event_context_window(self._event)
            y_min, y_max = waveform_y_bounds(display_waveform)
            add_event_region(self._waveform_plot, start_s, end_s, y_min, y_max)
            self._waveform_plot.plot(
                times,
                display_waveform,
                pen=pg.mkPen(color=get_neutral_waveform_color(dark_mode=True), width=2),
            )
            set_plot_x_range(self._waveform_plot, plot_start_s, plot_end_s)
        else:
            add_unavailable_label(self._waveform_plot)

        self._waveform_plot.setLabel("bottom", "Time", units="s")
        self._waveform_plot.setLabel("left", "Amplitude", units="V")

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
    Main widget for displaying expert-reviewed events in a grid format.
    
    Signals:
        eventClicked(ExpertEvent): Emitted when an event cell is clicked
        eventSelected(ExpertEvent): Emitted when an event is selected (for navigation)
        requestJumpToTime(float, str): Emitted to request main viewer jump to time/channel
    """
    
    eventClicked = Signal(ExpertEvent)
    eventSelected = Signal(ExpertEvent)
    requestJumpToTime = Signal(float, str)  # time, channel
    
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.setMinimumSize(900, 700)
        self.setStyleSheet("background-color: #1e1e1e;")
        
        # Main layout
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(10, 10, 10, 10)
        self._main_layout.setSpacing(10)
        
        # Header
        header = QLabel("Expert Event Grid")
        header.setStyleSheet("""
            color: #ffffff;
            font-size: 16px;
            font-weight: bold;
        """)
        self._main_layout.addWidget(header)
        
        # Info bar
        self._info_label = QLabel("No events loaded")
        self._info_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        self._main_layout.addWidget(self._info_label)
        
        # Navigation controls
        nav_layout = QHBoxLayout()
        
        self._prev_btn = QPushButton("Previous (b)")
        self._prev_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #666666; }
            QPushButton:disabled { background-color: #333333; color: #666666; }
        """)
        self._prev_btn.clicked.connect(self._on_prev_page)
        
        self._page_label = QLabel("Page 0 / 0")
        self._page_label.setStyleSheet("color: #dddddd;")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self._next_btn = QPushButton("Next (n)")
        self._next_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #666666; }
            QPushButton:disabled { background-color: #333333; color: #666666; }
        """)
        self._next_btn.clicked.connect(self._on_next_page)
        
        nav_layout.addWidget(self._prev_btn)
        nav_layout.addWidget(self._page_label, 1)
        nav_layout.addWidget(self._next_btn)
        
        self._main_layout.addLayout(nav_layout)
        
        # Grid container (scroll area)
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setStyleSheet("background-color: #1e1e1e; border: none;")
        
        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(10)
        
        self._grid_scroll.setWidget(self._grid_widget)
        self._main_layout.addWidget(self._grid_scroll, 1)
        
        # Legend
        legend_layout = QHBoxLayout()
        legend_layout.addWidget(self._create_legend_item("Rejected artifact", COLOR_REJECTED_ARTIFACT))
        legend_layout.addWidget(self._create_legend_item("spkHFO", COLOR_SPK_HFO))
        legend_layout.addWidget(self._create_legend_item("non-spkHFO", COLOR_NON_SPK_HFO))
        legend_layout.addStretch()
        self._main_layout.addLayout(legend_layout)
        
        # Zoomed view container (hidden initially)
        self._zoomed_container = QWidget()
        self._zoomed_container.setVisible(False)
        self._zoomed_layout = QVBoxLayout(self._zoomed_container)
        self._main_layout.addWidget(self._zoomed_container, 1)
        
        self._update_navigation()
    
    def _create_legend_item(self, text: str, color: QColor) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"""
            color: #dddddd;
            font-size: 11px;
            font-weight: bold;
            border: 1px solid rgb({color.red()}, {color.green()}, {color.blue()});
            border-radius: 3px;
            padding: 2px 6px;
            background-color: transparent;
        """)
        return label
    
    def load_events_for_edf(self, edf_path: Path, events_path: Path) -> bool:
        """
        Load events for a specific EDF file.
        
        Args:
            edf_path: Path to the EDF file
            events_path: Path to the events file (CSV or JSON)
        
        Returns:
            True if events loaded successfully, False otherwise
        """
        edf_file = edf_path.name
        
        try:
            self._events = load_events_from_csv(events_path, edf_file)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error Loading Events",
                f"Failed to load events: {str(e)}"
            )
            return False
        
        if not self._events:
            self._info_label.setText(
                f"No expert HFO events found for {edf_file}. "
                "This grid does not show gamma-spike detector output."
            )
            self._update_grid()
            return True
        
        # Calculate total pages
        self._total_pages = (len(self._events) + GRID_TOTAL - 1) // GRID_TOTAL
        self._current_page = 0
        
        self._info_label.setText(
            f"Loaded {len(self._events)} events for {edf_file} | "
            f"Sorted by channel, then start time"
        )
        
        self._update_grid()
        return True
    
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
        if self._get_waveform_callback and self._raw is not None:
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
        ) and not event.waveform_unavailable:
            # Prefer raw callback data because CSV waveforms are usually event-only snippets.
            waveform, times = self._fetch_waveform_window(event, context_start, context_end)
            if waveform is not None:
                event.waveform = waveform
                event.waveform_start = context_start
                event.waveform_end = context_end
                event.waveform_times = times
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
        ) or event.wide_waveform_unavailable:
            return

        wide_waveform, wide_times = self._fetch_waveform_window(event, wide_start, wide_end)
        if wide_waveform is not None:
            event.wide_waveform = wide_waveform
            event.wide_waveform_start = wide_start
            event.wide_waveform_end = wide_end
            event.wide_waveform_times = wide_times
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
        self._page_label.setText(
            f"Page {self._current_page + 1} / {max(1, self._total_pages)}"
        )
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
    
    def _show_zoomed_view(self, event: ExpertEvent):
        """Show the zoomed view for a single event."""
        self._is_zoomed = True
        self._ensure_event_waveform(event, include_wide=False)
        
        # Hide grid, show zoomed view
        self._grid_scroll.setVisible(False)
        self._zoomed_container.setVisible(True)
        
        # Create or update zoomed view
        if self._zoomed_view is None:
            self._zoomed_view = ZoomedEventView(event)
            self._zoomed_view.backClicked.connect(self._on_back_to_grid)
            self._zoomed_view.nextEvent.connect(self._on_next_event)
            self._zoomed_view.prevEvent.connect(self._on_prev_event)
            self._zoomed_layout.addWidget(self._zoomed_view)
        else:
            self._zoomed_view.set_event(event)
        
        # Emit signal for main viewer to jump
        self.requestJumpToTime.emit(event.start, event.channel)
        self.eventClicked.emit(event)
    
    def _on_back_to_grid(self):
        """Return to grid view from zoomed view."""
        self._is_zoomed = False
        self._grid_scroll.setVisible(True)
        self._zoomed_container.setVisible(False)
    
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
        if self._current_page > 0:
            self._current_page -= 1
            self._update_grid()
    
    def _on_next_page(self):
        """Navigate to next page in grid mode."""
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


class ExpertEventGridDialog(QDialog):
    """
    Dialog wrapper for ExpertEventGrid that provides file loading functionality.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Expert Event Grid")
        self.resize(1000, 800)
        
        layout = QVBoxLayout(self)
        
        # Create the grid widget
        self._grid = ExpertEventGrid()
        layout.addWidget(self._grid)
        
        button_row = QHBoxLayout()
        self._load_btn = QPushButton("Load Events File...")
        self._load_btn.clicked.connect(self._on_load_events)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        button_row.addWidget(self._load_btn)
        button_row.addStretch()
        button_row.addWidget(self._close_btn)
        layout.addLayout(button_row)
        
        # Store current EDF path
        self._edf_path: Optional[Path] = None
    
    def set_edf_path(self, edf_path: Path):
        """Set the current EDF file path."""
        self._edf_path = edf_path
    
    def _on_load_events(self):
        """Open file dialog to select events file."""
        if self._edf_path is None:
            QMessageBox.warning(
                self,
                "No EDF File",
                "Please load an EDF file first before loading events."
            )
            return
        
        events_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Events File",
            str(self._edf_path.parent),
            "Events Files (*.csv *.json);;All Files (*)"
        )
        
        if events_path:
            self._grid.load_events_for_edf(self._edf_path, Path(events_path))
    
    def load_events_for_edf(self, edf_path: Path, events_path: Path) -> bool:
        """Load events for the given EDF file."""
        self._edf_path = edf_path
        return self._grid.load_events_for_edf(edf_path, events_path)
    
    @property
    def grid(self) -> ExpertEventGrid:
        return self._grid
