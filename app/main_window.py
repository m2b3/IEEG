from __future__ import annotations

from pathlib import Path

import numpy as np
import mne
from mne.io import BaseRaw

from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QDialog, QDialogButtonBox,
    QComboBox, QLineEdit, QFormLayout, QSpinBox, QToolBar, QToolButton, QVBoxLayout,
    QWidget, QDockWidget, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCursor, QKeySequence, QShortcut, QPixmap, QIcon, QColor

from app.menus import build_menubar
from app.plot import MultiChannelViewer
from app.console_viewer import ConsoleWindow
from app.computation_panel import ComputationPanel
from app.time_controls import TimeWindowControl
from app.annotations import (
    ANNOTATION_TYPES, ANNOTATION_STYLES, 
    ANNOTATION_SCOPES, SCOPE_SELECTED
)
from app.project_io import save_project, load_project
from app.referencing import (
    build_automatic_bipolar_montage,
    BipolarMontage,
    update_pair_channel2,
    extract_core_contact_label,
    parse_channel_label,
)


class MainWindow(QMainWindow):
    # ---------------- Lifecycle ----------------

    def __init__(self):
        super().__init__()

        self._base_title = "iEEG Tool"
        self.setWindowTitle(self._base_title)
        self.resize(1400, 800)

        # ---- Menu bar ----
        self._act_save, self._act_saveas, self._act_close = build_menubar(self)
        self._act_save.setEnabled(False)
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


        self.montage_label = QLabel("Montage: Monopolar")
        self.montage_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.montage_label.setStyleSheet("""
            QLabel {
                font-weight: 600;
                padding: 4px 8px;
                color: #dddddd;
                background-color: #2b2b2b;
                border-bottom: 1px solid #444444;
            }
        """)
        layout.addWidget(self.montage_label, 0)

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

        self.project_path: Path | None = None
        self.project_dirty: bool = False

        self._update_window_title()
        self._restoring_project = False

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

        # ---- Annotations dock ----
        self.anno_dock = QDockWidget("Annotations", self)
        self.anno_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.anno_list = QListWidget()
        self.anno_dock.setWidget(self.anno_list)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.anno_dock)
        self.anno_dock.hide()
        self._anno_items_by_id = {}
        self.anno_list.itemClicked.connect(self._on_annotation_item_clicked)

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
        self.viewer.annotationsChanged.connect(self._refresh_annotation_list)
        self.viewer.annotationsChanged.connect(self._mark_project_dirty)
        self.viewer.hiddenChannelsChanged.connect(self._mark_project_dirty)
        self.viewer.badChannelsChanged.connect(self._on_bad_channels_changed)
        self.viewer.requestEditAnnotation.connect(self._on_request_edit_annotation)
        self.viewer.annotationSelected.connect(self._on_plot_annotation_selected)
        
        # --- Shortcuts ---
        # --- Arrow-key scrolling (view navigation) ---
        self.sc_left  = QShortcut(QKeySequence(Qt.Key.Key_Left),  self)
        self.sc_right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.sc_up    = QShortcut(QKeySequence(Qt.Key.Key_Up),    self)
        self.sc_down  = QShortcut(QKeySequence(Qt.Key.Key_Down),  self)

        # (optional but recommended) make sure it works even when focus is inside child widgets
        for sc in (self.sc_left, self.sc_right, self.sc_up, self.sc_down):
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.sc_left.activated.connect(lambda: self._scroll_time(-1, mult=1))
        self.sc_right.activated.connect(lambda: self._scroll_time(+1, mult=1))
        self.sc_up.activated.connect(lambda: self._scroll_channels(-1, mult=1))
        self.sc_down.activated.connect(lambda: self._scroll_channels(+1, mult=1))

        # Shift = faster
        self.sc_shift_left  = QShortcut(QKeySequence("Shift+Left"),  self)
        self.sc_shift_right = QShortcut(QKeySequence("Shift+Right"), self)
        self.sc_shift_up    = QShortcut(QKeySequence("Shift+Up"),    self)
        self.sc_shift_down  = QShortcut(QKeySequence("Shift+Down"),  self)
        for sc in (self.sc_shift_left, self.sc_shift_right, self.sc_shift_up, self.sc_shift_down):
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.sc_shift_left.activated.connect(lambda: self._scroll_time(-1, mult=5))
        self.sc_shift_right.activated.connect(lambda: self._scroll_time(+1, mult=5))
        self.sc_shift_up.activated.connect(lambda: self._scroll_channels(-1, mult=5))
        self.sc_shift_down.activated.connect(lambda: self._scroll_channels(+1, mult=5))

        # Ctrl = very fast
        self.sc_ctrl_left  = QShortcut(QKeySequence("Ctrl+Left"),  self)
        self.sc_ctrl_right = QShortcut(QKeySequence("Ctrl+Right"), self)
        self.sc_ctrl_up    = QShortcut(QKeySequence("Ctrl+Up"),    self)
        self.sc_ctrl_down  = QShortcut(QKeySequence("Ctrl+Down"),  self)
        for sc in (self.sc_ctrl_left, self.sc_ctrl_right, self.sc_ctrl_up, self.sc_ctrl_down):
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.sc_ctrl_left.activated.connect(lambda: self._scroll_time(-1, mult=20))
        self.sc_ctrl_right.activated.connect(lambda: self._scroll_time(+1, mult=20))
        self.sc_ctrl_up.activated.connect(lambda: self._scroll_channels(-1, mult=20))
        self.sc_ctrl_down.activated.connect(lambda: self._scroll_channels(+1, mult=20))

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

        # Edit bipolar referencing 
        self.btn_edit_bipolar = QToolButton()
        self.btn_edit_bipolar.setText("Edit Bipolar…")
        self.btn_edit_bipolar.setEnabled(False)
        self.btn_edit_bipolar.clicked.connect(self.on_edit_bipolar_pairs)
        tb.addWidget(self.btn_edit_bipolar)

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

    # ---------------- File/data loading & Saving----------------

    def _open_raw_file(self, raw_path: Path) -> bool:
        """
        Load a raw EEG/iEEG file into the UI.
        Returns True on success, False on failure.
        """
        try:
            raw, picks = self._load_eeg_file(raw_path)
        except Exception as e:
            QMessageBox.critical(self, "Open EEG error", str(e))
            return False

        self.current_raw = raw
        self.current_picks = picks
        self.loaded_file = raw_path

        self.viewer.set_raw(raw, picks)

        ## Opening a raw file alone is not the same as opening a project
        self.project_path = None
        self.project_dirty = False

        self._enable_loaded_ui()
        self._act_save.setEnabled(False)  # no bound project yet
        self._update_window_title()

        return True

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
        base = getattr(self, "_base_title", "iEEG tool")
        if getattr(self, "project_dirty", False):
            base += " *"

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
    
    def on_new_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open EEG/iEEG file",
            "",
            "EEG files (*.edf *.bdf *.fif *.vhdr *.set *.cnt);;All files (*)",
        )
        if not path:
            return

        raw_path = Path(path)

        project_default = str(raw_path.with_suffix("")) + ".ieeg"
        proj_path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Create project file",
            project_default,
            "iEEG Project (*.ieeg);;All files (*)",
        )
        if not proj_path_str:
            return

        project_path = Path(proj_path_str)
        if project_path.suffix.lower() != ".ieeg":
            project_path = project_path.with_suffix(".ieeg")

        # Reuse the normal raw-file opening flow so UI/state setup stays centralized
        if not self._open_raw_file(raw_path):
            return
        self._enable_loaded_ui()

        # Project bookkeeping
        self.project_path = project_path
        self.project_dirty = False
        self._update_window_title()

        self._act_save.setEnabled(True)
        self._act_saveas.setEnabled(True)
        self._act_close.setEnabled(True)

        # Save initial empty project
        try:
            save_project(project_path, self)
            self.console.log(f"Project created: {project_path}")
            self._mark_project_clean()
        except Exception as e:
            # Roll back project binding if initial save failed
            self.project_path = None
            self.project_dirty = False
            self._act_save.setEnabled(False)
            QMessageBox.critical(self, "Create project error", str(e))

    def on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open project",
            "",
            "iEEG Project (*.ieeg);;All files (*)",
        )
        if not path:
            return

        project_path = Path(path)

        try:
            payload = load_project(project_path)
        except Exception as e:
            QMessageBox.critical(self, "Open project error", str(e))
            return

        source = payload.get("source")
        if not isinstance(source, dict):
            QMessageBox.critical(self, "Open project error", "Project is missing a valid 'source' section.")
            return

        raw_file = source.get("raw_file")
        if not isinstance(raw_file, str) or not raw_file.strip():
            QMessageBox.critical(self, "Open project error", "Project does not contain a valid raw_file path.")
            return

        raw_path = Path(raw_file)
        if not raw_path.exists():
            QMessageBox.critical(
                self,
                "Open project error",
                f"Raw EEG file not found:\n{raw_path}",
            )
            return

        # Reuse the standard raw-file opening flow
        if not self._open_raw_file(raw_path):
            return

        review = payload.get("review")
        if not isinstance(review, dict):
            review = {}

        annos = review.get("annotations", [])
        hidden_raw = review.get("hidden_channels", [])
        bad_raw = review.get("bad_channels", [])

        hidden = set(hidden_raw) if isinstance(hidden_raw, list) else set()
        bad = set(bad_raw) if isinstance(bad_raw, list) else set()

        self._restoring_project = True
        try:
            self.viewer.set_annotations_from_dicts(annos if isinstance(annos, list) else [])
            self.viewer.set_hidden_channels(hidden)
            self.viewer.set_bad_channels(bad)
        finally:
            self._restoring_project = False

        # Re-bind the loaded dataset to its project file
        self.project_path = project_path
        self.project_dirty = False

        self._enable_loaded_ui()
        self._act_save.setEnabled(True)
        self._update_window_title()

        self.console.log(f"Project opened: {project_path}")

    def on_save_project(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Save project", "Load a dataset first.")
            return

        if self.project_path is None:
            self.on_save_project_as()
            return

        try:
            save_project(self.project_path, self)
            self.console.log(f"Project saved: {self.project_path}")
            self._mark_project_clean()
        except Exception as e:
            QMessageBox.critical(self, "Save project error", str(e))

    def on_save_project_as(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Save project as", "Load a dataset first.")
            return

        default = ""
        if self.project_path is not None:
            default = str(self.project_path)
        elif self.loaded_file is not None:
            default = str(self.loaded_file.with_suffix(".ieeg"))

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save project as",
            default,
            "iEEG Project (*.ieeg);;All files (*)",
        )
        if not path:
            return

        p = Path(path)
        if p.suffix.lower() != ".ieeg":
            p = p.with_suffix(".ieeg")

        try:
            save_project(p, self)
            self.project_path = p
            self.console.log(f"Project saved: {p}")
            self._mark_project_clean()
            self._act_save.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Save project error", str(e))

    def _mark_project_dirty(self) -> None:
        if self.current_raw is None:
            return
        if getattr(self, "_restoring_project", False):
            return
        if not self.project_dirty:
            self.project_dirty = True
            self._update_window_title()

    def _mark_project_clean(self) -> None:
        if self.project_dirty:
            self.project_dirty = False
            self._update_window_title()

    def _enable_loaded_ui(self) -> None:
        self.tb.setEnabled(True)
        self.timeline.show()
        self._update_time_slider_range()
        self._sync_time_from_viewer(0.0)

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(True)

        self._act_save.setEnabled(True)
        self._act_saveas.setEnabled(True)
        self._act_close.setEnabled(True)

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
    
        hidden = self.viewer.get_hidden_channels()
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
        displayed_names = self.viewer.get_channel_names()
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

# ---------------- Keyboard shortcuts / cursor nudging -------------

    def _scroll_time(self, direction: int, mult: float = 1.0) -> None:
        """direction: -1 left, +1 right"""
        if self.viewer is None:
            return

        # scroll by a fraction of the visible window (feels natural)
        base = 0.10 * float(self.time_range.value())  # 10% of current window
        dt = direction * base * float(mult)

        new_t0 = float(self.viewer.time_start()) + dt

        raw = getattr(self.viewer, "_raw", None)
        if raw is not None and raw.n_times > 1:
            t_min = float(raw.times[0])
            t_max = float(raw.times[-1]) - float(self.time_range.value())
            new_t0 = max(t_min, min(new_t0, max(t_min, t_max)))

        self.viewer.set_time_start(new_t0)

    def _scroll_channels(self, direction: int, mult: int = 1) -> None:
            """direction: -1 up, +1 down (move channel window)"""
            if self.viewer is None:
                return
            step = int(direction * int(mult))
            self.viewer.set_channel_start(self.viewer.channel_start() + step)

# ---------------- Annotations -------------

    def on_annotate(self):
        if self.current_raw is None:
            QMessageBox.information(self, "Annotate", "Load a file before annotating.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Add annotation")

        layout = QFormLayout(dlg)

        combo = QComboBox(dlg)
        for t in ANNOTATION_TYPES:
            combo.addItem(self._color_icon(ANNOTATION_STYLES[t]), t)

        scope = QComboBox(dlg)
        scope.addItems(ANNOTATION_SCOPES)
        scope.setCurrentText(SCOPE_SELECTED)

        note = QLineEdit(dlg)
        note.setPlaceholderText("Optional note…")

        layout.addRow("Type:", combo)
        layout.addRow("Scope:", scope)
        layout.addRow("Note:", note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg
        )
        layout.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        kind = combo.currentText()
        scope_txt = scope.currentText()
        note_txt = note.text().strip()

        self.viewer.start_annotation_mode(kind=kind, note=note_txt, scope=scope_txt)
        self.console.log(f"Annotation mode: {kind} ({scope_txt}). Drag on signal. Esc to cancel.")

    def _color_icon(self, rgb: tuple[int, int, int]) -> QIcon:
        pm = QPixmap(12, 12)
        pm.fill(QColor(*rgb))
        return QIcon(pm)  
    
    def _refresh_annotation_list(self):
        """Rebuild the dock list from viewer annotations."""
        annos = self.viewer.get_annotations()

        self.anno_list.blockSignals(True)
        self.anno_list.clear()
        self._anno_items_by_id.clear()  # IMPORTANT: clear BEFORE rebuilding

        channel_names = self.viewer.get_channel_names()

        for a in annos:
            if a.abs_channel is None:
                ch_txt = "GLOBAL"
            else:
                if 0 <= a.abs_channel < len(channel_names):
                    ch_txt = channel_names[a.abs_channel]
                else:
                    ch_txt = str(a.abs_channel)

            txt = f"[{a.kind}] {ch_txt}  {a.t_start:.3f}–{a.t_end:.3f}"
            if a.note:
                txt += f"  |  {a.note}"

            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            self.anno_list.addItem(item)

            # Link id -> list item so plot-click can select it
            self._anno_items_by_id[str(a.id)] = item

        self.anno_list.blockSignals(False)

        # Show dock if there is at least one annotation
        if self.anno_list.count() > 0:
            self.anno_dock.show()
            self.anno_dock.raise_()

    def _on_annotation_item_clicked(self, item: QListWidgetItem):
        anno_id = item.data(Qt.ItemDataRole.UserRole)
        if not anno_id:
            return
        self.viewer.jump_to_annotation(str(anno_id))

    def _on_request_edit_annotation(self, anno_id: str):

        a = self.viewer.get_annotation_by_id(anno_id)
        if a is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit annotation")
        layout = QFormLayout(dlg)

        combo = QComboBox(dlg)
        for t in ANNOTATION_TYPES:
            combo.addItem(self._color_icon(ANNOTATION_STYLES[t]), t)
        combo.setCurrentText(a.kind)

        note = QLineEdit(dlg)
        note.setText(a.note)

        layout.addRow("Type:", combo)
        layout.addRow("Note:", note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg
        )
        layout.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.viewer.update_annotation(anno_id, kind=combo.currentText(), note=note.text().strip())

    def _on_plot_annotation_selected(self, anno_id: str):
        # Ensure dock is visible when user clicks an annotation
        if self.anno_dock.isHidden():
            self.anno_dock.show()

        item = self._anno_items_by_id.get(str(anno_id))
        if item is None:
            # list may be stale; refresh and retry once
            self._refresh_annotation_list()
            item = self._anno_items_by_id.get(str(anno_id))
            if item is None:
                return

        # Select + scroll into view
        self.anno_list.setCurrentItem(item)
        self.anno_list.scrollToItem(item)


# ---------------- Referencing  -------------
    def _refresh_display_name_dependent_ui(self) -> None:
        displayed_names = self.viewer.get_channel_names()
        self.comp_panel.set_data_context(self.current_raw, self.current_picks, displayed_names)
        self._refresh_annotation_list()      

    def on_reference_monopolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        self.viewer.set_monopolar_mode()
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(False)
        self.console.log("Reference mode: Monopolar")

    def on_reference_bipolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        channel_names = self.viewer._channel_names
        bad_channels = self.viewer.get_bad_channels()

        montage = build_automatic_bipolar_montage(
            channel_names,
            bad_channels=bad_channels,
        )

        if not montage.pairs:
            QMessageBox.warning(
                self,
                "Bipolar montage",
                "No valid bipolar pairs could be generated automatically.",
            )
            return

        self.viewer.set_bipolar_mode(montage)
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(True)
        self.console.log(f"Reference mode: Bipolar ({len(montage.pairs)} pairs)")

        if montage.skipped_channels:
            skipped = ", ".join(montage.skipped_channels)
            QMessageBox.information(
                self,
                "Automatic bipolar montage",
                "Some channels were not paired automatically:\n\n"
                f"{skipped}"
            )

            print("CHANNEL NAMES:")
            for ch in channel_names[:20]:
                print("  ", ch)

            print("GENERATED PAIRS:")
            for pair in montage.pairs[:20]:
                print("  ", pair)

            print("UNPARSED:")
            for ch in montage.unparsed_channels[:20]:
                print("  ", ch)

    def on_edit_bipolar_pairs(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Edit bipolar pairs", "Load a dataset first.")
            return

        if self.viewer.reference_mode() != "bipolar":
            QMessageBox.information(self, "Edit bipolar pairs", "Switch to bipolar mode first.")
            return

        montage = self.viewer.get_bipolar_montage()
        if montage is None or not montage.pairs:
            QMessageBox.information(self, "Edit bipolar pairs", "No bipolar montage to edit.")
            return

        bad_names = set(self.viewer.get_bad_channels())
        raw_names = [
            name for name in self.viewer.get_raw_channel_names()
            if name not in bad_names
        ]

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit bipolar pairs")
        dlg.resize(700, 500)

        layout = QVBoxLayout(dlg)

        table = QTableWidget(len(montage.pairs), 4, dlg)
        table.setHorizontalHeaderLabels(["Pair", "Ch1", "Ch2", "Origin"])
        table.verticalHeader().setVisible(False)

        combos: list[QComboBox] = []

        for row, pair in enumerate(montage.pairs):
            pair_item = QTableWidgetItem(pair.name)
            pair_item.setFlags(pair_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            ch1_item = QTableWidgetItem(pair.ch1)
            ch1_item.setFlags(ch1_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            origin_item = QTableWidgetItem(pair.origin)
            origin_item.setFlags(origin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            table.setItem(row, 0, pair_item)
            table.setItem(row, 1, ch1_item)
            table.setItem(row, 3, origin_item)

            combo = QComboBox(table)
            combo.addItems(raw_names)
            if pair.ch2 in raw_names:
                combo.setCurrentText(pair.ch2)

            def _update_pair_preview(new_text: str, row=row, pair=pair):
                name_item = table.item(row, 0)
                origin_item = table.item(row, 3)
                if name_item is None:
                    return

                # unchanged row -> restore original label and origin
                if new_text == pair.ch2:
                    name_item.setText(pair.name)
                    if origin_item is not None:
                        origin_item.setText(pair.origin)
                    return

                ch1_core = extract_core_contact_label(pair.ch1) or pair.ch1
                ch2_core = extract_core_contact_label(new_text) or new_text
                name_item.setText(f"{ch1_core}-{ch2_core}")

                if origin_item is not None:
                    origin_item.setText("manual")

            combo.currentTextChanged.connect(_update_pair_preview)
            table.setCellWidget(row, 2, combo)
            combos.append(combo)

        table.resizeColumnsToContents()
        layout.addWidget(table)

        reset_btn = QToolButton(dlg)
        reset_btn.setText("Back to default")
        layout.addWidget(reset_btn)

        def _reset_to_default() -> None:
            auto_montage = build_automatic_bipolar_montage(
                self.viewer.get_raw_channel_names(),
                bad_channels=self.viewer.get_bad_channels(),
            )
            auto_by_ch1 = {pair.ch1: pair for pair in auto_montage.pairs}

            for row, pair in enumerate(montage.pairs):
                auto_pair = auto_by_ch1.get(pair.ch1)
                if auto_pair is None:
                    continue

                combo = combos[row]
                old = combo.blockSignals(True)
                combo.setCurrentText(auto_pair.ch2)
                combo.blockSignals(old)

                name_item = table.item(row, 0)
                if name_item is not None:
                    name_item.setText(auto_pair.name)

                origin_item = table.item(row, 3)
                if origin_item is not None:
                    origin_item.setText(auto_pair.origin)

        reset_btn.clicked.connect(_reset_to_default)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_pairs = []
        seen_names = set()
        cross_group_warnings = []

        for pair, combo in zip(montage.pairs, combos):
            new_ch2 = combo.currentText().strip()

            if not new_ch2:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    "Empty channel name is not allowed.",
                )
                return

            if new_ch2 == pair.ch1:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    f"Invalid pair for {pair.ch1}: ch1 and ch2 cannot be the same.",
                )
                return

            if new_ch2 in bad_names:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    f"{new_ch2} is marked as bad and cannot be used in bipolar mode.",
                )
                return

            # only changed rows become manual
            if new_ch2 == pair.ch2:
                new_pair = pair
            else:
                new_pair = update_pair_channel2(pair, new_ch2)

            if new_pair.name in seen_names:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    f"Duplicate bipolar channel name: {new_pair.name}",
                )
                return
            seen_names.add(new_pair.name)

            parsed_ch1 = parse_channel_label(pair.ch1)
            parsed_ch2 = parse_channel_label(new_pair.ch2)
            if (
                parsed_ch1 is not None
                and parsed_ch2 is not None
                and parsed_ch1.electrode_prefix != parsed_ch2.electrode_prefix
            ):
                cross_group_warnings.append(
                    f"{new_pair.name} ({pair.ch1} vs {new_pair.ch2})"
                )

            new_pairs.append(new_pair)

        new_montage = BipolarMontage(
            pairs=new_pairs,
            unparsed_channels=list(montage.unparsed_channels),
            non_consecutive_channels=list(montage.non_consecutive_channels),
            bad_channel_skips=list(montage.bad_channel_skips),
        )

        self.viewer.set_bipolar_mode(new_montage)
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(bool(new_montage.pairs))
        self._mark_project_dirty()
        self.console.log("Bipolar pairs updated.")

        if cross_group_warnings:
            QMessageBox.warning(
                self,
                "Cross-electrode bipolar pairs",
                "Some edited pairs use channels from different electrode groups:\n\n"
                + "\n".join(cross_group_warnings),
            )
   
    def _on_bad_channels_changed(self) -> None:
        self._mark_project_dirty()

        if self.viewer.reference_mode() != "bipolar":
            return

        if self.current_raw is None:
            return

        channel_names = self.viewer.get_raw_channel_names()
        bad_channels = self.viewer.get_bad_channels()

        montage = build_automatic_bipolar_montage(
            channel_names,
            bad_channels=bad_channels,
        )

        self.viewer.set_bipolar_mode(montage)
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(bool(montage.pairs))