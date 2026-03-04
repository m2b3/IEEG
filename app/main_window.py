from __future__ import annotations

from pathlib import Path

import numpy as np
import mne
from mne.io import BaseRaw

from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QSpinBox, QToolBar, QToolButton, QVBoxLayout, QWidget, QDockWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCursor, QKeySequence, QShortcut

from app.menus import build_menubar
from app.plot import MultiChannelViewer
from app.console_viewer import ConsoleWindow
from app.computation_panel import ComputationPanel
from app.time_controls import TimeWindowControl

class MainWindow(QMainWindow):
    # ---------------- Lifecycle ----------------

    def __init__(self):
        super().__init__()

        self._base_title = "Halyzia Shell"
        self.setWindowTitle(self._base_title)
        self.resize(1400, 800)

        # ---- Menu bar ----
        self._act_saveas, self._act_close = build_menubar(self)
        self._act_saveas.setEnabled(False)
        self._act_close.setEnabled(False)

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(False)
        
        # ---- Toolbar (controls) ----
        self._build_toolbar()
        self.tb.setEnabled(False) 

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

        self.time_ctl = TimeWindowControl(label_prefix="t0")
        tl.addWidget(self.time_ctl, 1)

        self.timeline.hide()
        layout.addWidget(self.timeline, 0)

        # ---- Console ----
        self.console = ConsoleWindow(parent=self)
        self.console.show()
        self.console.log("Console ready. Load EEG data to begin analysis.")

        # ---- State ----
        self.current_raw: BaseRaw | None = None
        self.current_picks: np.ndarray | None = None
        self.loaded_file: Path | None = None

        self._update_window_title()

        # ---- Computation dock (WIP) ----
        self.comp_dock = QDockWidget("Computation Panel", self)
        self.comp_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        self.comp_panel = ComputationPanel()
        self.comp_dock.setWidget(self.comp_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.comp_dock)
        self.comp_dock.hide()

        # ---- Connections ----
        self.viewer.channelClicked.connect(self._on_channel_clicked)
        self.viewer.requestTimeRangeDelta.connect(self._zoom_time_range)
        self.viewer.requestChanRangeDelta.connect(self._zoom_chan_range)
        self.viewer.requestOpenComputationPanel.connect(self._open_computation_panel)

        # Timeline sync
        self.viewer.timeWindowChanged.connect(self._sync_time_from_viewer)
        self.time_ctl.t0Changed.connect(self._on_time_ctl_t0_changed)

        # keep panel time updated when main time moves
        self.viewer.timeWindowChanged.connect(self._push_time_to_comp_panel)
        # keep panel time updated when main window length changes
        self.time_range.valueChanged.connect(lambda v: self._push_time_to_comp_panel(self.viewer.time_start()))
        #Channel selection updated 
        self.comp_panel.panelSelectionChanged.connect(self._on_comp_panel_selection_changed)
        # Make computation panel follow the viewer cursor (instead of window start)
        self.viewer.cursorMoved.connect(self._push_time_to_comp_panel)
        self.viewer.cursorMoved.connect(self._on_viewer_cursor_moved)

        # --- Shortcuts (work even when focus is in child widgets) ---
        self.sc_left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.sc_right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.sc_left.activated.connect(lambda: self._nudge_cursor(-1, 1))
        self.sc_right.activated.connect(lambda: self._nudge_cursor(+1, 1))

        self.sc_shift_left = QShortcut(QKeySequence("Shift+Left"), self)
        self.sc_shift_right = QShortcut(QKeySequence("Shift+Right"), self)
        self.sc_shift_left.activated.connect(lambda: self._nudge_cursor(-1, 10))
        self.sc_shift_right.activated.connect(lambda: self._nudge_cursor(+1, 10))

        self.sc_ctrl_left = QShortcut(QKeySequence("Ctrl+Left"), self)
        self.sc_ctrl_right = QShortcut(QKeySequence("Ctrl+Right"), self)
        self.sc_ctrl_left.activated.connect(lambda: self._nudge_cursor(-1, 50))
        self.sc_ctrl_right.activated.connect(lambda: self._nudge_cursor(+1, 50))

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

        tb.addSeparator()

        # Hidden channels menu button
        self.btn_hidden = QToolButton()
        self.btn_hidden.setText("Hidden…")
        self.btn_hidden.clicked.connect(self._show_hidden_channels_menu)
        tb.addWidget(self.btn_hidden)

        # ---- Connect toolbar -> viewer ----
        self.time_range.valueChanged.connect(self._on_time_range_changed)
        self.gain.valueChanged.connect(lambda v: self.viewer.set_view_params(gain=v))
        self.gain.valueChanged.connect(lambda v: self.comp_panel.set_main_gain_uv(float(v)))
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
            self._sync_time_from_viewer(0.0)

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

    def _show_hidden_channels_menu(self):
    
        hidden = sorted(self.viewer._hidden_channels)
        menu = QMenu()

        if not hidden:
            act = menu.addAction("(No hidden channels)")
            act.setEnabled(False)
        else:
            act_unhide_all = menu.addAction("Unhide all")
            menu.addSeparator()
            act_unhide_all.triggered.connect(self.viewer.unhide_all_channels)

            for ch in hidden:
                act = menu.addAction(f"Show {ch}")
                act.triggered.connect(lambda checked=False, name=ch: self.viewer.unhide_channel(name))

        menu.exec_(QCursor.pos())

# ---------------- Viewer interaction callbacks ----------------

    def _push_time_to_comp_panel(self, t0: float):
        main_win = float(self.time_range.value())  # toolbar value
        self.comp_panel.set_main_time(float(t0), main_win_s=main_win)
        self.comp_panel.set_main_gain_uv(float(self.gain.value()))

    def _open_computation_panel(self, selected_abs: list[int]):
        # give the panel access to the current dataset mapping
        displayed_names = getattr(self.viewer, "_channel_names", [])
        self.comp_panel.set_data_context(self.current_raw, self.current_picks, displayed_names)

        # default channels = selection at creation/open
        self.comp_panel.set_selected_channels_abs(selected_abs, replace=True)

        # default time = linked to main view (panel will clamp win to 1..10)
        self._push_time_to_comp_panel(self.viewer.time_start())

        self.comp_dock.show()
        self.comp_dock.raise_()

    def _on_comp_panel_selection_changed(self, selected_abs: list[int]):
        # Highlight the same channels in main viewer (and treat it as selection)
        self.viewer.set_selected_abs(selected_abs, anchor=(selected_abs[-1] if selected_abs else None), emit=True)

    def _on_viewer_cursor_moved(self, x: float):
        if not hasattr(self, "comp_panel") or self.comp_panel is None:
            return

        # Center cursor in the computation window
        win = float(self.comp_panel.state.win)  # panel's own fixed window length
        t0 = max(0.0, float(x) - 0.5 * win)

        # push t0 only (panel keeps its own window length)
        self.comp_panel.set_main_time(t0, main_win_s=win)
    

    def _on_time_ctl_t0_changed(self, t0: float):
        # user moved the timeline in main window
        self.viewer.set_time_start(float(t0))

    def _sync_time_from_viewer(self, t0: float):
        # viewer scrolled/updated time -> update main timeline control
        self.time_ctl.set_t0(float(t0))

    def _update_time_slider_range(self):
        if self.current_raw is None or self.current_raw.n_times <= 1:
            self.time_ctl.set_range(0.0, 0.0, 0.0)
            return

        total_s = float(self.current_raw.times[-1])
        window_s = float(self.time_range.value())
        self.time_ctl.set_range(total_s, window_s, float(self.viewer.time_start()))

# ---------------- Keyboard shortcuts / cursor nudging ----------------

    def keyPressEvent(self, event):
        key = event.key()

        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            super().keyPressEvent(event)
            return

        if self.viewer is None:
            return

        # 1-sample step
        fs = getattr(self.viewer, "_fs", 1.0)
        base_step = 1.0 / max(1.0, float(fs))

        mods = event.modifiers()

        if mods & Qt.KeyboardModifier.ShiftModifier:
            base_step *= 10

        if mods & Qt.KeyboardModifier.ControlModifier:
            base_step *= 50

        dx = -base_step if key == Qt.Key.Key_Left else +base_step

        x = float(self.viewer.cursor_x())
        new_x = x + dx

        raw = getattr(self.viewer, "_raw", None)
        if raw is not None and raw.n_times > 1:
            t_min = float(raw.times[0])
            t_max = float(raw.times[-1])
            new_x = max(t_min, min(new_x, t_max))

        # Move cursor
        self.viewer.set_cursor_x(new_x)

        # IMPORTANT: manually emit so computation panel updates
        self.viewer.cursorMoved.emit(float(new_x))

        event.accept()

    def _nudge_cursor(self, direction: int, mult: float = 1.0) -> None:
        """direction: -1 for left, +1 for right"""
        if self.viewer is None:
            return

        fs = float(getattr(self.viewer, "_fs", 1.0))
        step = (1.0 / max(1.0, fs)) * float(mult)

        x = float(self.viewer.cursor_x())
        new_x = x + direction * step

        raw = getattr(self.viewer, "_raw", None)
        if raw is not None and raw.n_times > 1:
            t_min = float(raw.times[0])
            t_max = float(raw.times[-1])
            new_x = max(t_min, min(new_x, t_max))

        self.viewer.set_cursor_x(new_x)
        # ensure computation panel + anything else listening updates
        self.viewer.cursorMoved.emit(float(new_x))