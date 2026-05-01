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
from typing import Optional, Callable, List

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QGraphicsView,
    QGraphicsScene, QGraphicsRectItem, QGraphicsTextItem,
    QGraphicsLineItem, QDialog, QDialogButtonBox,
    QFileDialog, QMessageBox
)

# Review label colors
COLOR_REJECTED_ARTIFACT = QColor(220, 50, 50)  # red
COLOR_SPK_HFO = QColor(50, 200, 50)            # green
COLOR_NON_SPK_HFO = QColor(50, 100, 220)       # blue

# Grid dimensions
GRID_ROWS = 6
GRID_COLS = 4
GRID_TOTAL = GRID_ROWS * GRID_COLS


def _matches_edf_file(value: object, edf_file: str) -> bool:
    return Path(str(value or "")).name == Path(str(edf_file)).name


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
    """
    edf_file: str
    channel: str
    start: float
    end: float
    detector: str
    artifact: bool = False
    spike: bool = False
    waveform: Optional[np.ndarray] = None
    
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
            
            parsed_event = ExpertEvent(
                edf_file=item.get('edf_file', edf_file or ""),
                channel=channel,
                start=_as_float(start),
                end=_as_float(end),
                detector=detector,
                artifact=artifact,
                spike=spike,
                waveform=waveform
            )
            fallback_events.append(parsed_event)
            if edf_file is None or _matches_edf_file(item.get('edf_file'), edf_file):
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
                
                parsed_event = ExpertEvent(
                    edf_file=row.get('edf_file', edf_file or ""),
                    channel=channel,
                    start=_as_float(start),
                    end=_as_float(end),
                    detector=detector,
                    artifact=artifact,
                    spike=spike,
                    waveform=waveform
                )
                fallback_events.append(parsed_event)
                if edf_file is None or _matches_edf_file(row.get('edf_file'), edf_file):
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
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumSize(200, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Set border color based on review label
        color = self._event.review_color
        self.setStyleSheet(f"""
            QFrame {{
                border: 2px solid rgb({color.red()}, {color.green()}, {color.blue()});
                border-radius: 4px;
                background-color: #1e1e1e;
            }}
            QFrame:hover {{
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
        layout.addWidget(info_label)
        
        # Waveform plot
        self._waveform_plot = pg.PlotWidget()
        self._waveform_plot.setBackground("#1e1e1e")
        self._waveform_plot.setMinimumHeight(60)
        self._waveform_plot.setMaximumHeight(80)
        
        # Hide axes
        plot_item = self._waveform_plot.getPlotItem()
        for ax in ("bottom", "left", "right", "top"):
            plot_item.hideAxis(ax)
        
        plot_item.setMouseEnabled(x=False, y=False)
        
        # Plot waveform if available, otherwise show placeholder
        if self._event.waveform is not None and len(self._event.waveform) > 0:
            pen = pg.mkPen(color=color, width=1)
            self._waveform_plot.plot(self._event.waveform, pen=pen)
        else:
            # Show a flat line as placeholder
            self._waveform_plot.plot([0, 1], [0, 0], pen=pg.mkPen(color='#666666', width=1))
        
        layout.addWidget(self._waveform_plot)
        
        # Time info
        time_label = QLabel(f"{self._event.start:.3f}s - {self._event.end:.3f}s")
        time_label.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(time_label)
        
        # Duration bar
        duration_bar = QFrame()
        duration_bar.setFixedHeight(8)
        duration_bar.setStyleSheet(f"""
            QFrame {{
                background-color: rgb({color.red()}, {color.green()}, {color.blue()});
                border-radius: 4px;
            }}
        """)
        layout.addWidget(duration_bar)
        
        # Review label
        review_label = QLabel(self._event.review_label)
        review_label.setStyleSheet(f"""
            color: rgb({color.red()}, {color.green()}, {color.blue()});
            font-size: 9px;
            font-weight: bold;
        """)
        review_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(review_label)
    
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
        
        # Time info
        self._time_info = QLabel()
        self._time_info.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        self._time_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._time_info)
        
        # Large waveform plot
        self._waveform_plot = pg.PlotWidget()
        self._waveform_plot.setBackground("#2b2b2b")
        self._waveform_plot.setMinimumHeight(300)
        
        # Show axes
        self._waveform_plot.showGrid(x=True, y=True, alpha=0.3)
        
        layout.addWidget(self._waveform_plot)
        self._refresh_event()
        
        # Instructions
        instructions = QLabel("Press 'n' for next, 'b' for previous, or click to return to grid")
        instructions.setStyleSheet("color: #888888; font-size: 11px;")
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(instructions)
        
        # Back button
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
        layout.addWidget(self._back_btn)
    
    def set_event(self, event: ExpertEvent):
        """Update the displayed event."""
        self._event = event
        self._refresh_event()

    def _refresh_event(self):
        self._header.setText(
            f"Channel: {self._event.channel} | "
            f"Detector: {self._event.detector} | "
            f"Review: {self._event.review_label}"
        )
        self._time_info.setText(
            f"Start: {self._event.start:.6f}s | "
            f"End: {self._event.end:.6f}s | "
            f"Duration: {self._event.duration*1000:.2f}ms"
        )

        self._waveform_plot.clear()
        color = self._event.review_color
        start_s = float(self._event.start)
        end_s = float(self._event.end)
        if end_s <= start_s:
            end_s = start_s + 1e-6

        if self._event.waveform is not None and len(self._event.waveform) > 0:
            waveform = np.asarray(self._event.waveform, dtype=float).reshape(-1)
            times = np.linspace(start_s, end_s, waveform.size)
            self._waveform_plot.plot(times, waveform, pen=pg.mkPen(color=color, width=2))
        else:
            self._waveform_plot.plot([start_s, end_s], [0, 0], pen=pg.mkPen(color='#666666', width=1))

        self._waveform_plot.setXRange(start_s, end_s, padding=0)
        self._waveform_plot.setLabel("bottom", "Time", units="s")
    
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
        label = QLabel(f"* {text}")
        label.setStyleSheet(f"""
            color: rgb({color.red()}, {color.green()}, {color.blue()});
            font-size: 11px;
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
            self._info_label.setText(f"No events found for {edf_file}")
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
        and return a numpy array of waveform samples.
        """
        self._get_waveform_callback = callback
    
    def _get_waveform(self, event: ExpertEvent) -> Optional[np.ndarray]:
        """Get waveform for an event, either from stored data or via callback."""
        if event.waveform is not None:
            return event.waveform
        
        if self._get_waveform_callback and self._raw is not None:
            try:
                return self._get_waveform_callback(
                    event.channel,
                    event.start,
                    event.end
                )
            except Exception:
                pass
        
        return None
    
    def _update_grid(self):
        """Update the grid display with events for the current page."""
        # Clear existing cells
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Get events for this page
        start_idx = self._current_page * GRID_TOTAL
        end_idx = min(start_idx + GRID_TOTAL, len(self._events))
        page_events = self._events[start_idx:end_idx]
        
        # Create cells
        for i, event in enumerate(page_events):
            row = i // GRID_COLS
            col = i % GRID_COLS
            if event.waveform is None:
                event.waveform = self._get_waveform(event)
            
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
