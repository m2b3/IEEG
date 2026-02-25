from __future__ import annotations

from pathlib import Path

import numpy as np
import mne
from mne.io import BaseRaw

from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QSlider,
    QSpinBox, QStatusBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from app.menus import build_menubar
from app.plot import MultiChannelViewer
from app.console_viewer import ConsoleWindow


class MainWindow(QMainWindow):
    # ---------------- Lifecycle ----------------

    def __init__(self):
        super().__init__()

        self._base_title = "Halyzia Shell"
        self.setWindowTitle(self._base_title)
        self.resize(1400, 800)
        \

        # ---- Menu bar ----
        self._act_saveas, self._act_close = build_menubar(self)
        self._act_saveas.setEnabled(False)
        self._act_close.setEnabled(False)

        # Only File + Help clickable until a file is loaded
        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(False)
        self._act_saveas.setEnabled(False)
        self._act_close.setEnabled(False)

        # ---- Toolbar (controls) ----
        self._build_toolbar()
        # Toolbar visible but not clickable
        self.tb.setEnabled(False)   # (make sure you stored it as self.tb in _build_toolbar)

        # ---- Central widget (viewer + timeline) ----
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.viewer = MultiChannelViewer()
        layout.addWidget(self.viewer, 1)

    

        # ---- Timeline (time slider) ----
        self.timeline = QFrame()
        self.timeline.setFixedHeight(70)
        self.timeline.setFrameShape(QFrame.Shape.StyledPanel)

        tl = QHBoxLayout(self.timeline)
        tl.setContentsMargins(12, 8, 12, 8)

        self.t_label = QLabel("t0: 0.00 s")
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setMinimum(0)
        self.time_slider.setMaximum(0)
        self.time_slider.setValue(0)

        tl.addWidget(self.t_label)
        tl.addWidget(self.time_slider, 1)

        self.timeline.hide()
        layout.addWidget(self.timeline, 0)

        # ---- Console ----
        self.console = ConsoleWindow(parent=self)
        self.console.show()
        self.console.log("Console ready. Load EEG data to begin analysis.")

        # ---- State ----
        self.current_raw: BaseRaw | None = None
        self.current_picks: np.ndarray | None = None

        # ---- Connections ----
        self.viewer.channelClicked.connect(self._on_channel_clicked)
        self.viewer.timeWindowChanged.connect(self._sync_time_slider_from_viewer)
        self.time_slider.valueChanged.connect(self._on_time_slider)

        # Zoom requests from the viewer (wheel over signal vs labels)
        if hasattr(self.viewer, "requestTimeRangeDelta"):
            self.viewer.requestTimeRangeDelta.connect(self._zoom_time_range)
        if hasattr(self.viewer, "requestChanRangeDelta"):
            self.viewer.requestChanRangeDelta.connect(self._zoom_chan_range)

        # Top text of the open file path
        self.loaded_file: Path | None = None
        self.halyzia_folder: Path | None = None
        self._update_window_title()


    def closeEvent(self, event):
        """Ensure the app quits cleanly when the main window closes."""
        try:
            if hasattr(self, "console") and self.console is not None:
                self.console.close()
        finally:
            QApplication.quit()
            event.accept()

    # ---------------- UI construction ----------------

    def _build_toolbar(self):
        self.tb = QToolBar("Controls")
        self.tb.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.tb)
        tb = self.tb 

        # Time range
        tb.addWidget(QLabel("Time Range (s):"))
        self.time_range = QDoubleSpinBox()
        self.time_range.setRange(0.5, 120.0) 
        self.time_range.setSingleStep(0.5)
        self.time_range.setValue(10.0)
        self.time_range.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.time_range)
        self._add_presets_button(tb, self.time_range, [1, 10, 20, 30, 50, 100])

        tb.addSeparator()

        # Channel range
        tb.addWidget(QLabel("Channels:"))
        self.chan_range = QSpinBox()
        self.chan_range.setRange(1, 512)
        self.chan_range.setValue(32)
        self.chan_range.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.chan_range)
        self._add_presets_button(tb, self.chan_range, [10, 20, 30, 40, 50, 60])

        tb.addSeparator()

        # Amplitiude (µV)
        tb.addWidget(QLabel("Amplitude (μV): ±"))
        self.gain = QDoubleSpinBox()
        self.gain.setRange(1.0, 1000.0)
        self.gain.setSingleStep(10.0)
        self.gain.setValue(100.0)
        self.gain.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.gain)
        self._add_presets_button(tb, self.gain, [10, 50, 100, 200, 400, 800])

        # ---- Connect toolbar -> viewer ----
        self.time_range.valueChanged.connect(self._on_time_range_changed)
        self.gain.valueChanged.connect(lambda v: self.viewer.set_view_params(gain=v))
        self.chan_range.valueChanged.connect(lambda v: self.viewer.set_view_params(chan_range=v))

    
    def _add_presets_button(self, tb: QToolBar, target, values):
        """Small down-arrow button that pops a menu of preset values."""
        btn = QToolButton()
        btn.setArrowType(Qt.ArrowType.DownArrow)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        menu = QMenu(btn)
        for v in values:
            act = QAction(str(v), menu)
            act.triggered.connect(lambda checked=False, val=v: target.setValue(val))
            menu.addAction(act)

        btn.setMenu(menu)
        # Hide the extra tiny menu-indicator arrow so only one arrow is shown
        btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")

        tb.addWidget(btn)

    # ---------------- File/data loading ----------------

    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open EDF file",
            "",
            "EEG/iEEG files (*.edf *.bdf *.fif *.vhdr *.set *.cnt);;All files (*)",
        )
        if not path:
            return

        try:
            raw, picks = self._load_eeg_file(Path(path))

            self.loaded_file = Path(path)
            self.current_raw = raw
            self.current_picks = picks
            self._update_window_title()

            n_channels = len(picks)

            self.chan_range.blockSignals(True)
            self.chan_range.setMaximum(n_channels)
            self.chan_range.setValue(min(self.chan_range.value(), n_channels))
            self.chan_range.blockSignals(False)

            self.viewer.set_raw(raw, picks=picks)
            self.viewer.set_view_params(
                chan_range=self.chan_range.value(),
                gain=self.gain.value(),
                time_range=self.time_range.value(),
            )

            self.tb.setEnabled(True)

            self.timeline.show()
            self._update_time_slider_range()
            self._sync_time_slider_from_viewer(0.0)

            self.console.log(f"Loaded: {Path(path).name}")
            self.console.log(f"Channels: {n_channels}")
            self.console.log(f"Sampling rate: {raw.info['sfreq']} Hz")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(True)
        self._act_saveas.setEnabled(True)
        self._act_close.setEnabled(True)
        self.tb.setEnabled(True)

    def _load_eeg_file(self, file_path: Path):
        """Load EEG file via MNE and return (raw, eeg_picks)."""
        file_path = Path(file_path)
        mne.set_log_level("WARNING")

        suf = file_path.suffix.lower()
        if suf in [".edf", ".bdf"]:
            raw = mne.io.read_raw_edf(file_path, preload=False)
        elif suf == ".fif":
            raw = mne.io.read_raw_fif(file_path, preload=False)
        elif suf == ".vhdr":
            raw = mne.io.read_raw_brainvision(file_path, preload=False)
        elif suf == ".set":
            raw = mne.io.read_raw_eeglab(file_path, preload=False)
        elif suf == ".cnt":
            raw = mne.io.read_raw_cnt(file_path, preload=False)
        else:
            raise ValueError(f"Unsupported file format: {file_path.suffix}")

        picks = np.arange(raw.info["nchan"], dtype=int)
        return raw, picks
    

    def _update_window_title(self):
        base = getattr(self, "_base_title", "Halyzia Shell")

        # If nothing loaded yet
        if self.current_raw is None:
            self.setWindowTitle(base)
            return

        raw = self.current_raw
        picks = self.current_picks

        # File
        file_txt = str(self.loaded_file) if self.loaded_file else "no file"

        # Channels
        n_total = int(raw.info["nchan"])
        n_sel = int(len(picks)) if picks is not None else n_total

        # Duration
        dur_s = float(raw.times[-1]) if raw.n_times > 1 else 0.0

        # Sampling freq (global for a single Raw)
        sfreq = float(raw.info["sfreq"])

        self.setWindowTitle(
            f"{base} | Folder: {file_txt} | Ch: {n_sel}/{n_total} | Dur: {dur_s:.1f}s | Fs: {sfreq:.1f}Hz"
        )

    # ---------------- UI ↔ viewer syncing ----------------

    def _on_time_range_changed(self, v: float):
        """Toolbar time_range changed -> update viewer + slider range."""
        self.viewer.set_view_params(time_range=v)
        self._update_time_slider_range()

    def _on_time_slider(self, v: int):
        """Slider is in milliseconds."""
        t0 = v / 1000.0
        self.viewer.set_time_start(t0)
        self.t_label.setText(f"t0: {self.viewer.time_start():.2f} s")

    def _sync_time_slider_from_viewer(self, t0: float):
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(int(round(t0 * 1000.0)))
        self.time_slider.blockSignals(False)
        self.t_label.setText(f"t0: {t0:.2f} s")

    def _update_time_slider_range(self):
        if self.current_raw is None:
            self.time_slider.setMaximum(0)
            return

        total_s = float(self.current_raw.times[-1]) if self.current_raw.n_times > 1 else 0.0
        window_s = float(self.time_range.value())
        max_t0 = max(0.0, total_s - window_s)

        self.time_slider.blockSignals(True)
        self.time_slider.setMinimum(0)
        self.time_slider.setMaximum(int(round(max_t0 * 1000.0)))
        self.time_slider.setValue(int(round(self.viewer.time_start() * 1000.0)))
        self.time_slider.blockSignals(False)

    # ---------------- Viewer interaction callbacks ----------------

    def _on_channel_clicked(self, abs_idx: int):
        if self.current_raw is None or self.current_picks is None:
            self.console.log(f"Selected channel: {abs_idx}")
            return

        raw_idx = int(self.current_picks[abs_idx])
        ch_name = self.current_raw.ch_names[raw_idx]
        ch_type = self.current_raw.get_channel_types(picks=[raw_idx])[0]
        self.console.log(
            f"Selected: {ch_name} (shown idx {abs_idx}, raw idx {raw_idx}, type: {ch_type})"
        )

    def _zoom_time_range(self, direction: int):
        """direction: -1 zoom in (smaller), +1 zoom out (bigger)"""
        step = self.time_range.singleStep()
        new_v = self.time_range.value() + direction * step
        new_v = max(self.time_range.minimum(), min(self.time_range.maximum(), new_v))
        self.time_range.setValue(new_v)

    def _zoom_chan_range(self, direction: int):
        """direction: -1 zoom in (fewer channels), +1 zoom out (more channels)"""
        step = self.chan_range.singleStep() if self.chan_range.singleStep() else 1
        new_v = self.chan_range.value() + direction * step
        new_v = max(self.chan_range.minimum(), min(self.chan_range.maximum(), new_v))
        self.chan_range.setValue(new_v)