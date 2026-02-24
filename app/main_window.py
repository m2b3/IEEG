from PySide6.QtWidgets import (
    QMainWindow, QMessageBox, QStatusBar, QWidget,
    QVBoxLayout, QHBoxLayout, QFrame, QSlider, QToolButton, QMenu,
    QToolBar, QLabel, QSpinBox, QDoubleSpinBox, QFileDialog, QApplication,
    QAbstractSpinBox
)
from PySide6.QtCore import Qt 
from PySide6.QtGui import QAction

from app.menus import build_menubar
from app.plot import MultiChannelViewer
from app.console_viewer import ConsoleWindow
from pathlib import Path

import numpy as np
import mne
import pyqtgraph as pg
from mne.io import BaseRaw


from typing import Tuple


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Halyzia — EEG Analysis")
        self.resize(1400, 800)

        # ---- Menu bar ----
        build_menubar(self)

        # ---- Central widget ----
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Main viewer (contains labels + signals)
        self.viewer = MultiChannelViewer()
        layout.addWidget(self.viewer, 1)

        # ---- Toolbar ----
        # build toolbar AFTER viewer exists if toolbar wiring references viewer
        self._build_toolbar()

        # If you added zoom signals in MultiChannelViewer, connect them here:
        # (only keep these if you actually declared these signals in plot.py)
        if hasattr(self.viewer, "requestTimeRangeDelta"):
            self.viewer.requestTimeRangeDelta.connect(self._zoom_time_range)
        if hasattr(self.viewer, "requestChanRangeDelta"):
            self.viewer.requestChanRangeDelta.connect(self._zoom_chan_range)

        # Timeline placeholder
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

        # Viewer interaction
        self.viewer.channelClicked.connect(self._on_channel_clicked)

        # Time slider -> viewer
        self.time_slider.valueChanged.connect(self._on_time_slider)

        # Viewer -> time slider (when time_range changes/clamp etc.)
        self.viewer.timeWindowChanged.connect(self._sync_time_slider_from_viewer)

        # ---- Status bar ----
        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

        # ---- Console ----
        # This can keep your app alive if not closed; consider parenting it:
        self.console = ConsoleWindow(parent=self)
        self.console.show()
        self.console.log("Console ready. Load EEG data to begin analysis.")

        self.current_raw: BaseRaw | None = None
        self.current_picks: np.ndarray | None = None

        


    # ---------------- Toolbar ----------------

    def _build_toolbar(self):
        tb = QToolBar("Controls")
        tb.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # Time range
        tb.addWidget(QLabel("Time Range (s):"))
        self.time_range = QDoubleSpinBox()
        self.time_range.setRange(0.5, 500.0)
        self.time_range.setSingleStep(0.5)
        self.time_range.setValue(10.0)  # Common for EEG viewing
        self.time_range.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.time_range)
        self._add_presets_button(tb, self.time_range, [1, 10, 20, 30, 50, 100])

        tb.addSeparator()

        # Channel range
        tb.addWidget(QLabel("Channels:"))
        self.chan_range = QSpinBox()
        self.chan_range.setRange(1, 512)  # Support high-density EEG
        self.chan_range.setValue(32)
        self.chan_range.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.chan_range)
        self._add_presets_button(tb, self.chan_range, [10, 20, 30, 40, 50, 60])

        tb.addSeparator()

        # Gain - typical EEG amplitudes
        tb.addWidget(QLabel("Amplitude (μV): ±"))
        self.gain = QDoubleSpinBox()
        self.gain.setRange(1.0, 1000.0)
        self.gain.setSingleStep(10.0)
        self.gain.setValue(100.0)  # Common EEG scale
        self.gain.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        tb.addWidget(self.gain)
        self._add_presets_button(tb, self.gain, [10, 50, 100, 200, 400, 800])


        # Connect controls
        def _on_time_range_changed(v):
            self.viewer.set_view_params(time_range=v)
            self._update_time_slider_range()

        self.time_range.valueChanged.connect(_on_time_range_changed)

        self.gain.valueChanged.connect(
            lambda v: self.viewer.set_view_params(gain=v)
        )
        self.chan_range.valueChanged.connect(
            lambda v: self.viewer.set_view_params(chan_range=v)
        )

    # ---------------- EEG File Loading ----------------

    def _load_eeg_file(self, file_path: Path):
        file_path = Path(file_path)
        mne.set_log_level("WARNING")

        if file_path.suffix.lower() in [".edf", ".bdf"]:
            raw = mne.io.read_raw_edf(file_path, preload=False)
        elif file_path.suffix.lower() == ".fif":
            raw = mne.io.read_raw_fif(file_path, preload=False)
        elif file_path.suffix.lower() == ".vhdr":
            raw = mne.io.read_raw_brainvision(file_path, preload=False)
        elif file_path.suffix.lower() == ".set":
            raw = mne.io.read_raw_eeglab(file_path, preload=False)
        elif file_path.suffix.lower() == ".cnt":
            raw = mne.io.read_raw_cnt(file_path, preload=False)
        else:
            raise ValueError(f"Unsupported file format: {file_path.suffix}")

        eeg_picks = mne.pick_types(raw.info, eeg=True, exclude="bads")

        return raw, eeg_picks


    def _load_numpy_file(self, file_path: str) -> Tuple[np.ndarray, float, list[str]]:
        """Fallback for numpy files (your original implementation)"""
        p = Path(file_path)
        
        if p.suffix.lower() == ".npy":
            data = np.load(p)
        elif p.suffix.lower() == ".npz":
            npz = np.load(p)
            data = npz["data"] if "data" in npz else npz[list(npz.keys())[0]]
        else:
            raise ValueError("Unsupported numpy file type")

        if data.ndim != 2:
            raise ValueError(f"Expected (n_channels, n_samples), got {data.shape}")

        n_channels = data.shape[0]
        channel_names = [f"CH{i+1:03d}" for i in range(n_channels)]
        fs = 500.0  # Default sampling rate for numpy files
        
        return data, fs, channel_names

    # ---------------- Open file ----------------

    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open EDF file",
            "",
            "EDF files (*.edf *.bdf);;All files (*)"
        )

        if not path:
            return

        try:
            mne.set_log_level("WARNING")

            raw = mne.io.read_raw_edf(path, preload=False)

            # Pick EEG channels only
            picks = mne.pick_types(raw.info, eeg=True, exclude="bads")

            self.current_raw = raw
            self.current_picks = picks

            n_channels = len(picks)

            self.chan_range.blockSignals(True)
            self.chan_range.setMaximum(n_channels)
            self.chan_range.setValue(min(32, n_channels))
            self.chan_range.blockSignals(False)

            # Send raw directly to viewer
            self.viewer.set_raw(raw, picks=picks)

            self.viewer.set_view_params(
                chan_range=self.chan_range.value(),
                gain=self.gain.value(),
            )

            self.timeline.show()
            self._update_time_slider_range()
            self._sync_time_slider_from_viewer(0.0)

            self.console.log(f"Loaded EDF: {Path(path).name}")
            self.console.log(f"Channels: {n_channels}")
            self.console.log(f"Sampling rate: {raw.info['sfreq']} Hz")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


    # ---------------- Interaction ----------------

    def _on_channel_clicked(self, abs_idx: int):
        if self.current_raw is None or self.current_picks is None:
            self.console.log(f"Selected channel: {abs_idx}")
            return

        raw_idx = int(self.current_picks[abs_idx])
        ch_name = self.current_raw.ch_names[raw_idx]
        ch_type = self.current_raw.get_channel_types(picks=[raw_idx])[0]
        self.console.log(f"Selected: {ch_name} (shown idx {abs_idx}, raw idx {raw_idx}, type: {ch_type})")


    def _on_time_slider(self, v: int):
        # slider in milliseconds
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

    def _add_presets_button(self, tb: QToolBar, target, values):
        btn = QToolButton()
        btn.setArrowType(Qt.ArrowType.DownArrow)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        menu = QMenu(btn)
        for v in values:
            act = QAction(str(v), menu)
            act.triggered.connect(lambda checked=False, val=v: target.setValue(val))
            menu.addAction(act)

        btn.setMenu(menu)
        btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        tb.addWidget(btn)     


    def closeEvent(self, event):
        try:
            if hasattr(self, "console") and self.console is not None:
                self.console.close()
        finally:
            QApplication.quit()
            event.accept()


    def _zoom_time_range(self, direction: int):
        # direction: -1 zoom in (smaller), +1 zoom out (bigger)
        step = self.time_range.singleStep()
        new_v = self.time_range.value() + direction * step
        new_v = max(self.time_range.minimum(), min(self.time_range.maximum(), new_v))
        self.time_range.setValue(new_v)

    def _zoom_chan_range(self, direction: int):
        step = 1  # or 2/4 if you want faster
        new_v = self.chan_range.value() + direction * step
        new_v = max(self.chan_range.minimum(), min(self.chan_range.maximum(), new_v))
        self.chan_range.setValue(new_v)