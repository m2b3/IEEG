from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import mne
from mne.io import BaseRaw

from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QDialog, QDialogButtonBox,
    QComboBox, QLineEdit, QFormLayout, QSpinBox, QToolBar, QToolButton, QVBoxLayout,
    QWidget, QDockWidget, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextBrowser
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
    BipolarPair,
    update_pair_channel2,
    extract_core_contact_label,
    parse_channel_label,
)
from app.psd_panel import PSDIntervalDialog, PSDPanel
from app.filtering import (
    FilterSettings,
    FilterProfiles,
    NOTCH_OFF,
    NOTCH_50_HARM,
    NOTCH_60_HARM,
    validate_filter_settings,
    build_filtered_raw_by_group,
    is_filter_active,
)


class MainWindow(QMainWindow):
    # ---------------- Lifecycle ----------------

    def __init__(self):
        super().__init__()

        self._base_title = "iEEG Tool"
        self.setWindowTitle(self._base_title)
        self.resize(1400, 800)

        # ---- Menu bar ----
        self._act_reset_zoom: QAction | None = None
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


        self.top_controls = QFrame()
        self.top_controls.setStyleSheet("""
            QFrame {
                background-color: #2b2b2b;
                border-bottom: 1px solid #444444;
            }
            QLabel {
                color: #dddddd;
            }
        """)

        top = QHBoxLayout(self.top_controls)
        top.setContentsMargins(8, 4, 8, 4)
        top.setSpacing(8)

        # ---- Montage / reference display ----
        self.top_controls = QFrame()
        self.top_controls.setStyleSheet("""
            QFrame {
                background-color: #2b2b2b;
                border-bottom: 1px solid #444444;
            }
            QLabel {
                color: #dddddd;
            }
        """)

        top = QHBoxLayout(self.top_controls)
        top.setContentsMargins(8, 4, 8, 4)
        top.setSpacing(8)

        # ---- Montage label ----
        self.montage_label = QLabel("Montage: Monopolar")
        self.montage_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.montage_label.setStyleSheet("font-weight: 600; padding: 4px 8px;")
        top.addWidget(self.montage_label)

        top.addSpacing(16)

        # ---- Filter controls container ----
        self.filter_controls_widget = QWidget()
        filter_row = QHBoxLayout(self.filter_controls_widget)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        filter_row.addWidget(QLabel("Scope:"))
        self.filter_scope = QComboBox()
        self.filter_scope.addItems(["All", "Macro", "Micro"])
        self.filter_scope.currentTextChanged.connect(self._on_filter_scope_changed)
        filter_row.addWidget(self.filter_scope)

        filter_row.addWidget(QLabel("High Pass (Hz):"))
        self.filter_hp = QDoubleSpinBox()
        self.filter_hp.setDecimals(2)
        self.filter_hp.setRange(0.0, 10000.0)
        self.filter_hp.setSingleStep(0.5)
        self.filter_hp.setValue(0.0)
        self.filter_hp.setSpecialValueText("0")
        self.filter_hp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.filter_hp.setFixedWidth(90)
        filter_row.addWidget(self.filter_hp)

        filter_row.addWidget(QLabel("Low Pass (Hz):"))
        self.filter_lp = QDoubleSpinBox()
        self.filter_lp.setDecimals(2)
        self.filter_lp.setRange(0.0, 10000.0)
        self.filter_lp.setSingleStep(1.0)
        self.filter_lp.setValue(0.0)
        self.filter_lp.setSpecialValueText("0")
        self.filter_lp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.filter_lp.setFixedWidth(90)
        filter_row.addWidget(self.filter_lp)

        filter_row.addWidget(QLabel("Notch:"))
        self.filter_notch = QComboBox()
        self.filter_notch.addItems([
            NOTCH_OFF,
            NOTCH_50_HARM,
            NOTCH_60_HARM,
        ])
        filter_row.addWidget(self.filter_notch)

        self.btn_apply_filters = QToolButton()
        self.btn_apply_filters.setText("Apply filters")
        self.btn_apply_filters.clicked.connect(self.on_apply_filters)
        filter_row.addWidget(self.btn_apply_filters)

        self.btn_reset_filters = QToolButton()
        self.btn_reset_filters.setText("Back to default")
        self.btn_reset_filters.clicked.connect(self.on_reset_filters)
        filter_row.addWidget(self.btn_reset_filters)

        self.filter_summary = QLabel("")
        self.filter_summary.setStyleSheet("color: #bbbbbb; padding-left: 8px;")
        self.filter_summary.setMinimumWidth(260)
        self.filter_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        filter_row.addWidget(self.filter_summary, 1)

        self.filter_controls_widget.hide()
        top.addWidget(self.filter_controls_widget)

        top.addStretch(1)

        layout.addWidget(self.top_controls, 0)


        

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
        self._saved_bipolar_montage: BipolarMontage | None = None

        self.psd_panel: PSDPanel | None = None

        self.source_raw: BaseRaw | None = None   # original, never modified
        self.current_raw: BaseRaw | None = None  # active signal used everywhere
        self.current_picks: np.ndarray | None = None
        self.loaded_file: Path | None = None

        self.filter_profiles = FilterProfiles()
        self._psd_interval: tuple[float, float] | None = None

        self.channel_groups: dict[str, str] = {}

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
        self.anno_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.anno_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.anno_list.customContextMenuRequested.connect(self._on_annotation_list_context_menu)


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
        self.viewer.cursorMoved.connect(self._on_viewer_cursor_moved)

        self.viewer.annotationsChanged.connect(self._refresh_annotation_list)
        self.viewer.annotationsChanged.connect(self._mark_project_dirty)
        self.viewer.hiddenChannelsChanged.connect(self._mark_project_dirty)
        self.viewer.badChannelsChanged.connect(self._on_bad_channels_changed)
        self.viewer.requestEditAnnotation.connect(self._on_request_edit_annotation)
        self.viewer.annotationSelected.connect(self._on_plot_annotation_selected)
        self.viewer.requestOpenAnnotationsPanel.connect(self._open_annotations_panel)
        self.viewer.zoomStateChanged.connect(self._on_zoom_state_changed)

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

    def on_close_project(self) -> None:
        if self.current_raw is None:
            return

        self.current_raw = None
        self.current_picks = None
        self.loaded_file = None
        self.project_path = None
        self.project_dirty = False
        self._saved_bipolar_montage = None
        self._restoring_project = False
        
        self.source_raw = None
        self.filter_profiles = FilterProfiles()
        self._psd_interval = None
        self.channel_groups = {}

        # Avoid viewer->MainWindow signal side effects during teardown
        self.viewer.blockSignals(True)
        try:
            self.viewer.clear()
        finally:
            self.viewer.blockSignals(False)

        self.tb.setEnabled(False)
        self.timeline.hide()
        self.time_ctl.set_range(0.0, 0.0, 0.0)

        self.comp_dock.hide()
        self.anno_dock.hide()
        self.anno_list.clear()
        self._anno_items_by_id.clear()

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(False)

        self._act_save.setEnabled(False)
        self._act_saveas.setEnabled(False)
        self._act_close.setEnabled(False)

        self.btn_edit_bipolar.setEnabled(False)
        self.montage_label.setText("Montage: Monopolar")
        self._update_window_title()

        if hasattr(self, "filter_controls_widget"):
            self.filter_controls_widget.hide()
        
        self.filter_scope.setCurrentText("All")
        self._push_scope_profile_to_ui()
        self._update_filter_summary_label()

        if self.psd_panel is not None:
            self.psd_panel.close()
            self.psd_panel = None

        self.console.log("File closed.")

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
        self.gain.setRange(1.0, 20000.0)
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

        self.source_raw = raw
        self.current_picks = picks
        self.loaded_file = raw_path

        self._initialize_default_channel_groups(raw)

        self.filter_profiles = FilterProfiles()
        self.filter_scope.setCurrentText("All")
        self._rebuild_active_raw_from_source()
        self._push_scope_profile_to_ui()
        self._update_filter_summary_label()

        if self.current_raw is None:
            QMessageBox.critical(self, "Open EEG error", "Could not build active raw.")
            return False

        t0 = float(self.viewer.time_start())
        ch0 = int(self.viewer.channel_start())

        time_range = float(self.viewer._time_range)
        chan_range = int(self.viewer._chan_range)
        gain = float(self.viewer._gain_uv)

        self.viewer.set_raw(self.current_raw, self.current_picks)
        self.viewer.set_channel_groups(self.channel_groups)
        self.viewer.show()
        self.viewer.update()
        self.viewer.repaint()
        self.filter_controls_widget.show()

        self.viewer.set_view_params(
            time_range=time_range,
            chan_range=chan_range,
            gain=gain,
        )

        self.viewer.set_time_start(t0)
        self.viewer.set_channel_start(ch0)

        self._saved_bipolar_montage = None
        self._update_montage_label()

        self.project_path = None
        self.project_dirty = False

        self._enable_loaded_ui()
        self._act_save.setEnabled(False)
        self._update_window_title()

        return True

    def _extract_fdt_candidates_from_error(self, error: Exception) -> list[str]:
        """
        Extract .fdt filenames mentioned in an MNE EEGLAB error message.
        Returns basenames only.
        """
        text = str(error)
        matches = re.findall(r'([^\\/:*?"<>|\r\n]+\.fdt)', text, flags=re.IGNORECASE)
        out = []
        seen = set()
        for m in matches:
            name = Path(m).name
            low = name.lower()
            if low not in seen:
                seen.add(low)
                out.append(name)
        return out

    def _find_matching_fdt_for_set(self, set_path: Path, expected_names: list[str] | None = None) -> Path | None:
        """
        Find the most likely companion .fdt for a .set file.

        Priority:
        1) any expected name from the MNE error if present in the same folder
        2) same stem as the .set
        3) if there is exactly one .fdt in the folder, use it
        4) otherwise return None
        """
        set_path = Path(set_path)
        folder = set_path.parent

        if expected_names:
            for name in expected_names:
                candidate = folder / name
                if candidate.exists():
                    return candidate

        same_stem = folder / f"{set_path.stem}.fdt"
        if same_stem.exists():
            return same_stem

        candidates = sorted(folder.glob("*.fdt"))
        if len(candidates) == 1:
            return candidates[0]

        return None

    def _load_eeglab_with_local_fdt_fallback(self, set_path: Path):
        """
        Load an EEGLAB .set file, falling back to a local .fdt in the same folder.

        If MNE complains about missing .fdt files, this function:
        - extracts the filenames MNE tried
        - finds a likely local .fdt
        - copies that .fdt into a temp folder under the expected names
        - retries the load from the temp folder
        """
        set_path = Path(set_path)

        try:
            return mne.io.read_raw_eeglab(set_path, preload=False)
        except Exception as e:
            first_error = e

        expected_fdt_names = self._extract_fdt_candidates_from_error(first_error)

        fdt_path = self._find_matching_fdt_for_set(set_path, expected_names=expected_fdt_names)
        if fdt_path is None:
            folder_fdt = sorted(p.name for p in set_path.parent.glob("*.fdt"))
            raise RuntimeError(
                "Could not open EEGLAB .set file because no matching .fdt could be resolved.\n\n"
                f"SET: {set_path}\n"
                f"MNE expected: {expected_fdt_names or '[none detected]'}\n"
                f"Available .fdt files in folder: {folder_fdt or '[none]'}\n\n"
                "Make sure the correct companion .fdt is in the same folder as the .set."
            ) from first_error

        with tempfile.TemporaryDirectory(prefix="ieeg_eeglab_") as tmpdir:
            tmpdir = Path(tmpdir)
            tmp_set = tmpdir / set_path.name
            shutil.copy2(set_path, tmp_set)

            # Names MNE commonly tries:
            alias_names = set(expected_fdt_names)
            alias_names.add(f"{set_path.stem}.fdt")
            alias_names.add(fdt_path.name)

            for alias in alias_names:
                shutil.copy2(fdt_path, tmpdir / alias)

            try:
                return mne.io.read_raw_eeglab(tmp_set, preload=True)
            except Exception as e:
                raise RuntimeError(
                    "Could not open EEGLAB .set/.fdt pair.\n"
                    f"SET: {set_path}\n"
                    f"FDT source used: {fdt_path}\n"
                    f"Expected aliases: {sorted(alias_names)}\n\n"
                    f"Original error: {first_error}\n"
                    f"Fallback error: {e}"
                ) from e

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
            raw = self._load_eeglab_with_local_fdt_fallback(file_path)
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

        preprocessing = payload.get("preprocessing", {})
        if not isinstance(preprocessing, dict):
            preprocessing = {}

        filters_raw = preprocessing.get("filters", {})
        if not isinstance(filters_raw, dict):
            filters_raw = {}

        if "macro" in filters_raw or "micro" in filters_raw:
            macro_raw = filters_raw.get("macro", {})
            micro_raw = filters_raw.get("micro", {})

            if not isinstance(macro_raw, dict):
                macro_raw = {}
            if not isinstance(micro_raw, dict):
                micro_raw = {}

            self.filter_profiles = FilterProfiles(
                macro=FilterSettings(
                    highpass_hz=macro_raw.get("highpass_hz"),
                    lowpass_hz=macro_raw.get("lowpass_hz"),
                    notch_mode=str(macro_raw.get("notch_mode", NOTCH_OFF)),
                ),
                micro=FilterSettings(
                    highpass_hz=micro_raw.get("highpass_hz"),
                    lowpass_hz=micro_raw.get("lowpass_hz"),
                    notch_mode=str(micro_raw.get("notch_mode", NOTCH_OFF)),
                ),
            )
        else:
            legacy = FilterSettings(
                highpass_hz=filters_raw.get("highpass_hz"),
                lowpass_hz=filters_raw.get("lowpass_hz"),
                notch_mode=str(filters_raw.get("notch_mode", NOTCH_OFF)),
            )
            self.filter_profiles = FilterProfiles(
                macro=legacy,
                micro=FilterSettings(
                    highpass_hz=legacy.highpass_hz,
                    lowpass_hz=legacy.lowpass_hz,
                    notch_mode=legacy.notch_mode,
                ),
            )

        self._push_scope_profile_to_ui()
        self._rebuild_active_raw_from_source()
        self._refresh_active_signal_everywhere()
        self._update_filter_summary_label()

        review = payload.get("review")
        if not isinstance(review, dict):
            review = {}

        annos = review.get("annotations", [])
        hidden_raw = review.get("hidden_channels", [])
        bad_raw = review.get("bad_channels", [])
        saved_montage_raw = review.get("bipolar_montage")
        saved_channel_groups = review.get("channel_groups", {})

        hidden = set(hidden_raw) if isinstance(hidden_raw, list) else set()
        bad = set(bad_raw) if isinstance(bad_raw, list) else set()

        self._restoring_project = True
        try:
            self.viewer.set_annotations_from_dicts(annos if isinstance(annos, list) else [])
            self.viewer.set_hidden_channels(hidden)
            self.viewer.set_bad_channels(bad)
            self._saved_bipolar_montage = (
                self._restore_bipolar_montage_from_dict(saved_montage_raw)
                if isinstance(saved_montage_raw, dict)
                else None
            )
            self._restore_channel_groups(saved_channel_groups)
        finally:
            self._restoring_project = False

        # Re-bind the loaded dataset to its project file
        self.project_path = project_path
        self.project_dirty = False

        self._enable_loaded_ui()
        self._act_save.setEnabled(True)
        self._update_window_title()

        self.viewer.set_monopolar_mode()
        self.btn_edit_bipolar.setEnabled(False)
        self._refresh_display_name_dependent_ui()
        self._update_montage_label()

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

    def on_edit_channel_groups(self) -> None:
        if self.source_raw is None:
            QMessageBox.information(self, "Channel Groups", "Load a dataset first.")
            return

        if not self.channel_groups:
            self._initialize_default_channel_groups(self.source_raw)

        dlg = QDialog(self)
        dlg.setWindowTitle("Channel Groups")
        dlg.resize(700, 560)

        layout = QVBoxLayout(dlg)

        search = QLineEdit(dlg)
        search.setPlaceholderText("Search channel name...")
        layout.addWidget(search)

        table = QTableWidget(0, 2, dlg)
        table.setHorizontalHeaderLabels(["Channel", "Group"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(table)

        button_row = QHBoxLayout()

        btn_set_micro = QToolButton(dlg)
        btn_set_micro.setText("Set selected to Micro")
        button_row.addWidget(btn_set_micro)

        btn_set_macro = QToolButton(dlg)
        btn_set_macro.setText("Set selected to Macro")
        button_row.addWidget(btn_set_macro)

        button_row.addStretch(1)
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        channel_names = list(self.source_raw.ch_names)
        working_groups = dict(self.channel_groups)

        def _populate(filter_text: str = "") -> None:
            text = filter_text.strip().lower()
            table.setRowCount(0)

            for ch in channel_names:
                if text and text not in ch.lower():
                    continue

                row = table.rowCount()
                table.insertRow(row)

                item_name = QTableWidgetItem(ch)
                item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, item_name)

                item_group = QTableWidgetItem(working_groups.get(ch, "macro").capitalize())
                item_group.setFlags(item_group.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 1, item_group)

        def _set_selected_group(group: str) -> None:
            selected_rows = sorted({idx.row() for idx in table.selectionModel().selectedRows()})
            if not selected_rows:
                return

            for row in selected_rows:
                name_item = table.item(row, 0)
                group_item = table.item(row, 1)
                if name_item is None or group_item is None:
                    continue

                ch_name = name_item.text().strip()
                working_groups[ch_name] = group
                group_item.setText(group.capitalize())

        search.textChanged.connect(_populate)
        btn_set_micro.clicked.connect(lambda: _set_selected_group("micro"))
        btn_set_macro.clicked.connect(lambda: _set_selected_group("macro"))

        _populate()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.channel_groups = working_groups
        self.viewer.set_channel_groups(self.channel_groups)
        self._mark_project_dirty()

        if self.psd_panel is not None:
            self._refresh_psd_panel_context()

        n_micro = sum(1 for g in self.channel_groups.values() if g == "micro")
        n_macro = sum(1 for g in self.channel_groups.values() if g == "macro")
        self.console.log(f"Channel groups updated | Macro: {n_macro} | Micro: {n_micro}")

# ---------------- Project state helpers ----------------

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

        if hasattr(self, "filter_controls_widget"):
            self.filter_controls_widget.show()

        self.viewer.show()
        self.viewer.update()
        self._update_time_slider_range()
        self._sync_time_from_viewer(0.0)

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(True)

        self._act_save.setEnabled(True)
        self._act_saveas.setEnabled(True)
        self._act_close.setEnabled(True)

    def _restore_bipolar_montage_from_dict(self, data: dict) -> BipolarMontage | None:
        if not isinstance(data, dict):
            return None

        pairs_raw = data.get("pairs", [])
        if not isinstance(pairs_raw, list):
            return None

        pairs: list[BipolarPair] = []
        for item in pairs_raw:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            ch1 = item.get("ch1")
            ch2 = item.get("ch2")
            origin = item.get("origin", "manual")

            if not isinstance(name, str) or not isinstance(ch1, str) or not isinstance(ch2, str):
                continue

            pairs.append(
                BipolarPair(
                    name=name,
                    ch1=ch1,
                    ch2=ch2,
                    origin=str(origin),
                )
            )

        if not pairs:
            return None

        return BipolarMontage(
            pairs=pairs,
            unparsed_channels=list(data.get("unparsed_channels", [])),
            non_consecutive_channels=list(data.get("non_consecutive_channels", [])),
            bad_channel_skips=list(data.get("bad_channel_skips", [])),
        )

    # ---------------- Sync helpers  ----------------
    def _sync_comp_panel_context(self) -> None:
        """Push the current dataset/channel mapping into the computation panel."""
        displayed_names = self.viewer.get_channel_names()
        self.comp_panel.set_data_context(
            self.current_raw,
            self.current_picks,
            displayed_names,
        )

    def _sync_comp_panel_view_state(self, t0: float | None = None) -> None:
        """Push current main-view time/gain into the computation panel."""
        if t0 is None:
            t0 = float(self.viewer.time_start())

        self.comp_panel.set_main_time(
            float(t0),
            main_win_s=float(self.time_range.value()),
        )
        self.comp_panel.set_main_gain_uv(float(self.gain.value()))

    def _push_time_to_comp_panel(self, t0: float) -> None:
        self._sync_comp_panel_view_state(t0=float(t0))

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

    def _update_montage_label(self) -> None:
        mode = self.viewer.reference_mode()

        if mode == "bipolar":
            pretty = "Bipolar"
        elif mode == "average":
            pretty = "Average"
        elif mode == "median":
            pretty = "Median"
        elif mode == "common":
            ref_name = self.viewer.common_reference_name() or "?"
            pretty = f"Common ({ref_name})"
        else:
            pretty = "Monopolar"

        self.montage_label.setText(f"Montage: {pretty}")

    def _capture_reference_state(self) -> dict:
        mode = self.viewer.reference_mode()
        return {
            "mode": mode,
            "common_ref_name": self.viewer.common_reference_name(),
            "bipolar_montage": self.viewer.get_bipolar_montage() if mode == "bipolar" else None,
        }

    def _restore_reference_state(self, ref_state: dict) -> None:
        mode = ref_state.get("mode", "monopolar")

        if mode == "average":
            self.viewer.set_average_mode()
            self.btn_edit_bipolar.setEnabled(False)
        elif mode == "median":
            self.viewer.set_median_mode()
            self.btn_edit_bipolar.setEnabled(False)
        elif mode == "common":
            ref_name = ref_state.get("common_ref_name")
            if ref_name:
                self.viewer.set_common_reference_mode(ref_name)
            self.btn_edit_bipolar.setEnabled(False)
        elif mode == "bipolar":
            montage = ref_state.get("bipolar_montage")
            if montage is not None:
                self.viewer.set_bipolar_mode(montage)
                self.btn_edit_bipolar.setEnabled(True)
            else:
                self.viewer.set_monopolar_mode()
                self.btn_edit_bipolar.setEnabled(False)
        else:
            self.viewer.set_monopolar_mode()
            self.btn_edit_bipolar.setEnabled(False)

        self._update_montage_label()
   
    def _refresh_psd_panel_context(self) -> None:
        if self.psd_panel is None:
            return
        if self.current_raw is None or self.current_picks is None:
            return
        if self._psd_interval is None:
            return

        display_names = self.viewer.get_channel_names()

        macro_names: list[str] = []
        micro_names: list[str] = []

        for ch_name in display_names:
            group = self.channel_groups.get(str(ch_name), "macro")
            if group == "micro":
                micro_names.append(ch_name)
            else:
                macro_names.append(ch_name)

        start_s, stop_s = self._psd_interval
        self.psd_panel.set_psd_context(
            raw=self.current_raw,
            picks=self.current_picks,
            display_names=display_names,
            bad_names=self.viewer.get_bad_channels(),
            start_s=float(start_s),
            stop_s=float(stop_s),
            macro_names=macro_names,
            micro_names=micro_names,
        )
        
    def _refresh_active_signal_everywhere(self) -> None:
        if self.current_raw is None or self.current_picks is None:
            return

        self.viewer.replace_raw_preserve_view(self.current_raw, self.current_picks)

        self._update_montage_label()
        self._sync_comp_panel_context()
        self._sync_comp_panel_view_state()
        self._refresh_psd_panel_context()
        self._update_time_slider_range()

    def _rebuild_active_raw_from_source(self) -> None:
        if self.source_raw is None:
            return

        if (
            is_filter_active(self.filter_profiles.macro)
            or is_filter_active(self.filter_profiles.micro)
        ):
            self.current_raw = build_filtered_raw_by_group(
                self.source_raw,
                self.filter_profiles,
                self.channel_groups,
            )
        else:
            self.current_raw = self.source_raw
            
    def on_apply_filters(self) -> None:
        if self.source_raw is None:
            QMessageBox.information(self, "Filters", "Load a dataset first.")
            return

        new_settings = self._filter_settings_from_ui()
        ok, msg = validate_filter_settings(new_settings, sfreq=float(self.source_raw.info["sfreq"]))
        if not ok:
            QMessageBox.warning(self, "Filters", msg)
            return

        scope_key = self._scope_key_from_ui()

        if scope_key == "all":
            self.filter_profiles.macro = FilterSettings(
                highpass_hz=new_settings.highpass_hz,
                lowpass_hz=new_settings.lowpass_hz,
                notch_mode=new_settings.notch_mode,
            )
            self.filter_profiles.micro = FilterSettings(
                highpass_hz=new_settings.highpass_hz,
                lowpass_hz=new_settings.lowpass_hz,
                notch_mode=new_settings.notch_mode,
            )
        elif scope_key == "micro":
            self.filter_profiles.micro = new_settings
        else:
            self.filter_profiles.macro = new_settings

        self._rebuild_active_raw_from_source()
        self._refresh_active_signal_everywhere()
        self._update_filter_summary_label()
        self._mark_project_dirty()

        self.console.log(
            "Filters applied | "
            f"Scope: {self.filter_scope.currentText()} | "
            f"Macro: {self._fmt_filter_short(self.filter_profiles.macro)} | "
            f"Micro: {self._fmt_filter_short(self.filter_profiles.micro)}"
        )
        
    def on_reset_filters(self) -> None:
        if self.source_raw is None:
            QMessageBox.information(self, "Filters", "Load a dataset first.")
            return

        scope_key = self._scope_key_from_ui()

        if scope_key == "all":
            self.filter_profiles = FilterProfiles()
        elif scope_key == "micro":
            self.filter_profiles.micro = FilterSettings()
        else:
            self.filter_profiles.macro = FilterSettings()

        self._push_scope_profile_to_ui()
        self._rebuild_active_raw_from_source()
        self._refresh_active_signal_everywhere()
        self._update_filter_summary_label()
        self._mark_project_dirty()
        self.console.log(f"Filters reset | Scope: {self.filter_scope.currentText()}")
        
    def _initialize_default_channel_groups(self, raw: BaseRaw | None = None) -> None:
        if raw is None:
            raw = self.source_raw
        if raw is None:
            self.channel_groups = {}
            self.viewer.set_channel_groups(self.channel_groups)
            return

        self.channel_groups = {str(ch): "macro" for ch in raw.ch_names}
        self.viewer.set_channel_groups(self.channel_groups)


    def _restore_channel_groups(self, saved_groups) -> None:
        raw = self.source_raw
        if raw is None:
            self.channel_groups = {}
            self.viewer.set_channel_groups(self.channel_groups)
            return

        defaults = {str(ch): "macro" for ch in raw.ch_names}

        if not isinstance(saved_groups, dict):
            self.channel_groups = defaults
            self.viewer.set_channel_groups(self.channel_groups)
            return

        for ch_name, group in saved_groups.items():
            if ch_name in defaults and str(group).lower() in {"macro", "micro"}:
                defaults[ch_name] = str(group).lower()

        self.channel_groups = defaults
        self.viewer.set_channel_groups(self.channel_groups)

    def get_channel_group(self, ch_name: str) -> str:
        return self.channel_groups.get(ch_name, "macro")

    def get_channels_in_group(self, group: str) -> list[str]:
        group = str(group).lower()
        return [ch for ch, g in self.channel_groups.items() if g == group]

# ---------------- Viewer interaction callbacks ----------------
   
    def _on_time_range_changed(self, v: float):
        """Toolbar time_range changed -> update viewer + slider range."""
        self.viewer.set_view_params(time_range=v)
        self._update_time_slider_range()

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

    def _on_time_ctl_t0_changed(self, t0: float):
        # user moved the timeline in main window
        self.viewer.set_time_start(float(t0))

    def _refresh_display_name_dependent_ui(self) -> None:
        self._sync_comp_panel_context()
        self._refresh_annotation_list()

    def _capture_viewer_state(self) -> dict:
        return {
            "t0": float(self.viewer.time_start()),
            "ch0": int(self.viewer.channel_start()),
            "time_range": float(self.time_range.value()),
            "chan_range": int(self.chan_range.value()),
            "gain": float(self.gain.value()),
            "hidden": set(self.viewer.get_hidden_channels()),
            "bad": set(self.viewer.get_bad_channels()),
            "selected_abs": list(getattr(self.viewer, "_selected_abs_set", set())),
            "cursor_x": float(self.viewer.cursor_x()),
            "ref": self._capture_reference_state(),
        }

    def _restore_viewer_state(self, state: dict) -> None:
        self.viewer.set_view_params(
            time_range=float(state["time_range"]),
            chan_range=int(state["chan_range"]),
            gain=float(state["gain"]),
        )
        self.viewer.set_hidden_channels(set(state["hidden"]))
        self.viewer.set_bad_channels(set(state["bad"]))
        self.viewer.set_time_start(float(state["t0"]))
        self.viewer.set_channel_start(int(state["ch0"]))

        if state["selected_abs"]:
            self.viewer.set_selected_abs(list(state["selected_abs"]), emit=False)

        # optional, only if you want cursor restored too
        if hasattr(self.viewer, "_cursor_x"):
            self.viewer._cursor_x = float(state["cursor_x"])

        self._restore_reference_state(state["ref"])

    # ---------------- Time navigation ----------------

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

    def _on_viewer_cursor_moved(self, x: float) -> None:
        if self.comp_panel is None:
            return

        win = float(self.comp_panel.state.win)
        t0 = max(0.0, float(x) - 0.5 * win)
        self._sync_comp_panel_view_state(t0=t0)

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

# ---------------- Computation panel ----------------

    def _open_computation_panel(self, selected_abs: list[int]) -> None:
        self._sync_comp_panel_context()
        self.comp_panel.set_selected_channels_abs(selected_abs, replace=True)
        self._sync_comp_panel_view_state()
        self.comp_dock.show()
        self.comp_dock.raise_()

    def _on_comp_panel_selection_changed(self, selected_abs: list[int]):
        # Highlight the same channels in main viewer (and treat it as selection)
        self.viewer.set_selected_abs(selected_abs, anchor=(selected_abs[-1] if selected_abs else None), emit=True)


# ---------------- Referencing  -------------
 
    def on_reference_monopolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        self.viewer.set_monopolar_mode()
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(False)
        self._update_montage_label()
        self.console.log("Reference mode: Monopolar")
 
    def on_reference_average(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        self.viewer.set_average_mode()
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(False)
        self._mark_project_dirty()
        self._update_montage_label()
        self.console.log("Reference mode: Average")

    def on_reference_median(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        self.viewer.set_median_mode()
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(False)
        self._mark_project_dirty()
        self._update_montage_label()
        self.console.log("Reference mode: Median")

    def on_reference_bipolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        montage = self._saved_bipolar_montage

        if montage is None or not montage.pairs:
            channel_names = self.viewer.get_raw_channel_names()
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
        self._update_montage_label()
        self.console.log(f"Reference mode: Bipolar ({len(montage.pairs)} pairs)")

        if self._saved_bipolar_montage is None and montage.skipped_channels:
            skipped = ", ".join(montage.skipped_channels)
            QMessageBox.information(
                self,
                "Automatic bipolar montage",
                "Some channels were not paired automatically:\n\n"
                f"{skipped}"
            )

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
        hidden_names = set(self.viewer.get_hidden_channels())

        raw_names = [
            name for name in self.viewer.get_raw_channel_names()
            if name not in bad_names
        ]

        excluded_candidates = [
            ch for ch in montage.skipped_channels
            if ch not in bad_names and ch not in hidden_names
        ]

        # keep order, remove duplicates
        seen_excluded = set()
        excluded_names = []
        for ch in excluded_candidates:
            if ch not in seen_excluded:
                seen_excluded.add(ch)
                excluded_names.append(ch)

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit bipolar pairs")
        dlg.resize(820, 560)

        layout = QVBoxLayout(dlg)

        # --- top controls ---
        top_bar = QHBoxLayout()

        filter_label = QLabel("Filter:")
        filter_combo = QComboBox(dlg)
        filter_combo.addItems(["Default", "Origin: manual first"])

        add_pair_btn = QToolButton(dlg)
        add_pair_btn.setText("Add new pair")

        top_bar.addWidget(filter_label)
        top_bar.addWidget(filter_combo)
        top_bar.addStretch(1)
        top_bar.addWidget(add_pair_btn)

        layout.addLayout(top_bar)

        table = QTableWidget(0, 4, dlg)
        table.setHorizontalHeaderLabels(["Pair", "Ch1", "Ch2", "Origin"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        row_meta: list[dict] = []

        def _pair_display_name(ch1: str, ch2: str) -> str:
            ch1_core = extract_core_contact_label(ch1) or ch1
            ch2_core = extract_core_contact_label(ch2) or ch2
            return f"{ch1_core}-{ch2_core}"

        def _insert_row(
            *,
            row_index: int,
            pair_name: str,
            ch1_value: str,
            ch2_value: str,
            origin_value: str,
            editable_ch1: bool,
            editable_ch2: bool,
            ch1_choices: list[str] | None = None,
            ch2_choices: list[str] | None = None,
            source_pair=None,
            is_new: bool = False,
        ) -> None:
            table.insertRow(row_index)

            name_item = QTableWidgetItem(pair_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, 0, name_item)

            if editable_ch1:
                ch1_combo = QComboBox(table)
                for ch in (ch1_choices or []):
                    ch1_combo.addItem(ch)
                if ch1_value in (ch1_choices or []):
                    ch1_combo.setCurrentText(ch1_value)
                table.setCellWidget(row_index, 1, ch1_combo)
            else:
                ch1_item = QTableWidgetItem(ch1_value)
                ch1_item.setFlags(ch1_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, 1, ch1_item)
                ch1_combo = None

            if editable_ch2:
                ch2_combo = QComboBox(table)
                for ch in (ch2_choices or []):
                    ch2_combo.addItem(ch)
                if ch2_value in (ch2_choices or []):
                    ch2_combo.setCurrentText(ch2_value)
                table.setCellWidget(row_index, 2, ch2_combo)
            else:
                ch2_item = QTableWidgetItem(ch2_value)
                ch2_item.setFlags(ch2_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, 2, ch2_item)
                ch2_combo = None

            origin_item = QTableWidgetItem(origin_value)
            origin_item.setFlags(origin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, 3, origin_item)

            meta = {
                "source_pair": source_pair,
                "is_new": is_new,
                "editable_ch1": editable_ch1,
                "editable_ch2": editable_ch2,
                "ch1_combo": ch1_combo,
                "ch2_combo": ch2_combo,
            }
            row_meta.insert(row_index, meta)

            def _current_ch1() -> str:
                if ch1_combo is not None:
                    return ch1_combo.currentText().strip()
                return ch1_value

            def _current_ch2() -> str:
                if ch2_combo is not None:
                    return ch2_combo.currentText().strip()
                return ch2_value

            def _refresh_preview() -> None:
                name_item_local = table.item(row_index, 0)
                origin_item_local = table.item(row_index, 3)
                if name_item_local is None or origin_item_local is None:
                    return

                cur_ch1 = _current_ch1()
                cur_ch2 = _current_ch2()

                if not cur_ch1 or not cur_ch2:
                    return

                # unchanged existing row -> restore original label/origin
                if (not is_new) and source_pair is not None and cur_ch2 == source_pair.ch2:
                    name_item_local.setText(source_pair.name)
                    origin_item_local.setText(source_pair.origin)
                    return

                name_item_local.setText(_pair_display_name(cur_ch1, cur_ch2))
                origin_item_local.setText("manual")

            if ch1_combo is not None:
                ch1_combo.currentTextChanged.connect(lambda _text: _refresh_preview())
            if ch2_combo is not None:
                ch2_combo.currentTextChanged.connect(lambda _text: _refresh_preview())

        def _rebuild_table(order_mode: str) -> None:
            current_rows = []

            for row in range(table.rowCount()):
                meta = row_meta[row]

                name_item = table.item(row, 0)
                origin_item = table.item(row, 3)

                if meta["ch1_combo"] is not None:
                    ch1_value = meta["ch1_combo"].currentText().strip()
                else:
                    ch1_item = table.item(row, 1)
                    ch1_value = ch1_item.text().strip() if ch1_item is not None else ""

                if meta["ch2_combo"] is not None:
                    ch2_value = meta["ch2_combo"].currentText().strip()
                else:
                    ch2_item = table.item(row, 2)
                    ch2_value = ch2_item.text().strip() if ch2_item is not None else ""

                current_rows.append({
                    "pair_name": name_item.text().strip() if name_item is not None else "",
                    "ch1_value": ch1_value,
                    "ch2_value": ch2_value,
                    "origin_value": origin_item.text().strip() if origin_item is not None else "manual",
                    "editable_ch1": meta["editable_ch1"],
                    "editable_ch2": meta["editable_ch2"],
                    "source_pair": meta["source_pair"],
                    "is_new": meta["is_new"],
                })

            if order_mode == "Origin: manual first":
                current_rows.sort(key=lambda r: (0 if r["origin_value"] == "manual" else 1))

            table.setRowCount(0)
            row_meta.clear()

            for i, row_data in enumerate(current_rows):
                ch1_choices = excluded_names if row_data["editable_ch1"] else None
                ch2_choices = raw_names if row_data["editable_ch2"] else None

                _insert_row(
                    row_index=i,
                    pair_name=row_data["pair_name"],
                    ch1_value=row_data["ch1_value"],
                    ch2_value=row_data["ch2_value"],
                    origin_value=row_data["origin_value"],
                    editable_ch1=row_data["editable_ch1"],
                    editable_ch2=row_data["editable_ch2"],
                    ch1_choices=ch1_choices,
                    ch2_choices=ch2_choices,
                    source_pair=row_data["source_pair"],
                    is_new=row_data["is_new"],
                )

        # initial auto rows
        for row, pair in enumerate(montage.pairs):
            _insert_row(
                row_index=row,
                pair_name=pair.name,
                ch1_value=pair.ch1,
                ch2_value=pair.ch2,
                origin_value=pair.origin,
                editable_ch1=False,
                editable_ch2=True,
                ch1_choices=None,
                ch2_choices=raw_names,
                source_pair=pair,
                is_new=False,
            )

        def _add_new_pair_row() -> None:
            used_new_ch1 = set()

            for row, meta in enumerate(row_meta):
                if not meta["is_new"]:
                    continue
                ch1_combo = meta["ch1_combo"]
                if ch1_combo is not None:
                    used_new_ch1.add(ch1_combo.currentText().strip())

            available_ch1 = [ch for ch in excluded_names if ch not in used_new_ch1]
            if not available_ch1:
                QMessageBox.information(
                    self,
                    "Add new pair",
                    "No excluded channels are available to add.",
                )
                return

            default_ch1 = available_ch1[0]
            default_ch2 = raw_names[0] if raw_names else ""

            _insert_row(
                row_index=0,
                pair_name=_pair_display_name(default_ch1, default_ch2) if default_ch2 else default_ch1,
                ch1_value=default_ch1,
                ch2_value=default_ch2,
                origin_value="manual",
                editable_ch1=True,
                editable_ch2=True,
                ch1_choices=available_ch1,
                ch2_choices=raw_names,
                source_pair=None,
                is_new=True,
            )

            if filter_combo.currentText() == "Origin: manual first":
                _rebuild_table("Origin: manual first")

        add_pair_btn.clicked.connect(_add_new_pair_row)
        filter_combo.currentTextChanged.connect(_rebuild_table)

        reset_btn = QToolButton(dlg)
        reset_btn.setText("Back to default")
        layout.addWidget(reset_btn)

        def _reset_to_default() -> None:
            table.setRowCount(0)
            row_meta.clear()

            auto_montage = build_automatic_bipolar_montage(
                self.viewer.get_raw_channel_names(),
                bad_channels=self.viewer.get_bad_channels(),
            )

            for row, pair in enumerate(auto_montage.pairs):
                _insert_row(
                    row_index=row,
                    pair_name=pair.name,
                    ch1_value=pair.ch1,
                    ch2_value=pair.ch2,
                    origin_value=pair.origin,
                    editable_ch1=False,
                    editable_ch2=True,
                    ch1_choices=None,
                    ch2_choices=raw_names,
                    source_pair=pair,
                    is_new=False,
                )

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

        for row, meta in enumerate(row_meta):
            source_pair = meta["source_pair"]

            if meta["ch1_combo"] is not None:
                new_ch1 = meta["ch1_combo"].currentText().strip()
            else:
                ch1_item = table.item(row, 1)
                new_ch1 = ch1_item.text().strip() if ch1_item is not None else ""

            if meta["ch2_combo"] is not None:
                new_ch2 = meta["ch2_combo"].currentText().strip()
            else:
                ch2_item = table.item(row, 2)
                new_ch2 = ch2_item.text().strip() if ch2_item is not None else ""

            if new_ch1 == new_ch2:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    f"Invalid pair for {new_ch1}: ch1 and ch2 cannot be the same.",
                )
                return

            if source_pair is not None and not meta["is_new"] and new_ch2 == source_pair.ch2:
                new_pair = source_pair
            elif source_pair is not None and not meta["is_new"]:
                new_pair = update_pair_channel2(source_pair, new_ch2)
            else:
                new_pair = BipolarPair(
                    name=_pair_display_name(new_ch1, new_ch2),
                    ch1=new_ch1,
                    ch2=new_ch2,
                    origin="manual",
                )

            if new_pair.name in seen_names:
                QMessageBox.warning(
                    self,
                    "Edit bipolar pairs",
                    f"Duplicate bipolar channel name: {new_pair.name}",
                )
                return
            seen_names.add(new_pair.name)

            parsed_ch1 = parse_channel_label(new_pair.ch1)
            parsed_ch2 = parse_channel_label(new_pair.ch2)
            if (
                parsed_ch1 is not None
                and parsed_ch2 is not None
                and parsed_ch1.electrode_prefix != parsed_ch2.electrode_prefix
            ):
                cross_group_warnings.append(
                    f"{new_pair.name} ({new_pair.ch1} vs {new_pair.ch2})"
                )

            new_pairs.append(new_pair)

        new_montage = BipolarMontage(
            pairs=new_pairs,
            unparsed_channels=list(montage.unparsed_channels),
            non_consecutive_channels=list(montage.non_consecutive_channels),
            bad_channel_skips=list(montage.bad_channel_skips),
        )

        if cross_group_warnings:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Cross-electrode bipolar pairs")
            msg.setText("Some edited pairs use channels from different electrode groups.")
            msg.setInformativeText(
                "This may be intentional, but it is unusual clinically.\n\n"
                "Do you want to keep these edits?"
            )
            msg.setDetailedText("\n".join(cross_group_warnings))
            keep_btn = msg.addButton("Keep edit", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(cancel_btn)
            msg.exec()

            if msg.clickedButton() is not keep_btn:
                return

        has_manual_edit = any(pair.origin == "manual" for pair in new_montage.pairs)
        self._saved_bipolar_montage = new_montage if has_manual_edit else None

        self.viewer.set_bipolar_mode(new_montage)
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(bool(new_montage.pairs))
        self._mark_project_dirty()
        self._update_montage_label()
        self.console.log("Bipolar pairs updated.")
 
    def on_reference_common(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        channel_names = self.viewer.get_raw_channel_names()
        if not channel_names:
            QMessageBox.information(self, "Re-referencing", "No channels available.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Choose common reference")

        layout = QFormLayout(dlg)

        combo = QComboBox(dlg)
        combo.addItems(channel_names)

        current_ref = self.viewer.common_reference_name()
        if current_ref and current_ref in channel_names:
            combo.setCurrentText(current_ref)

        layout.addRow("Reference channel:", combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        layout.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        ref_name = combo.currentText().strip()
        if not ref_name:
            return

        self.viewer.set_common_reference_mode(ref_name)
        self._refresh_display_name_dependent_ui()
        self.btn_edit_bipolar.setEnabled(False)
        self._mark_project_dirty()
        self._update_montage_label()
        self.console.log(f"Reference mode: Common ({ref_name})")

# ---------------- Annotations -------------

    def _color_icon(self, rgb: tuple[int, int, int]) -> QIcon:
        pm = QPixmap(12, 12)
        pm.fill(QColor(*rgb))
        return QIcon(pm)  

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

    def _open_annotations_panel(self) -> None:
        self.anno_dock.show()
        self.anno_dock.raise_()
    
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

    def _on_annotation_list_context_menu(self, pos) -> None:
        items = self.anno_list.selectedItems()
        if not items:
            return

        menu = QMenu(self)
        act_delete = menu.addAction("Delete selected annotation(s)")

        chosen = menu.exec_(self.anno_list.mapToGlobal(pos))
        if chosen != act_delete:
            return

        ids = []
        for item in items:
            anno_id = item.data(Qt.ItemDataRole.UserRole)
            if anno_id:
                ids.append(str(anno_id))

        for anno_id in ids:
            self.viewer.delete_annotation(anno_id)

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

# ---------------- Zoom window  -------------
    def on_zoom_selection(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Zoom Selection", "Load a dataset first.")
            return

        self.viewer.start_zoom_selection_mode()
        self.console.log("Zoom selection mode: drag with left mouse. Esc to cancel.")

    def on_reset_zoom(self) -> None:
        self.viewer.reset_zoom_to_base()
        self.console.log("Zoom reset.")

    def _on_zoom_state_changed(self, has_zoom_base: bool) -> None:
        if self._act_reset_zoom is not None:
            self._act_reset_zoom.setEnabled(bool(has_zoom_base))

# ---------------- PSD pannel  -------------

    def open_psd_panel(self) -> None:
        if self.current_raw is None or self.current_picks is None:
            QMessageBox.information(self, "PSD Panel", "Load a dataset first.")
            return

        if self.psd_panel is not None and self.psd_panel.isVisible():
            self.psd_panel.raise_()
            self.psd_panel.activateWindow()
            return

        duration_s = float(self.current_raw.times[-1]) if self.current_raw.n_times > 1 else 0.0

        dlg = PSDIntervalDialog(duration_s, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start_s, stop_s = dlg.values()

        # CRITICAL: store interval so _refresh_psd_panel_context() can work
        self._psd_interval = (float(start_s), float(stop_s))

        self.psd_panel = PSDPanel(
            parent=None,
            mark_bad_callback=self._mark_channels_bad_from_psd,
            mark_good_callback=self._mark_channels_good_from_psd,
        )
        self.psd_panel.destroyed.connect(self._on_psd_panel_destroyed)

        # CRITICAL: do not call set_psd_context directly here with incomplete args
        self._refresh_psd_panel_context()

        self.psd_panel.show()
        self.psd_panel.raise_()
        self.psd_panel.activateWindow()

    def _on_psd_panel_destroyed(self, *args) -> None:
        self.psd_panel = None

    def _on_bad_channels_changed(self) -> None:
        self._mark_project_dirty()

        # Saved edited montage may no longer be valid if bad channels changed.
        self._saved_bipolar_montage = None

        # Always keep PSD state in sync if the panel is open
        if self.psd_panel is not None and self.psd_panel.isVisible():
            current_bad = set(self.viewer.get_bad_channels())
            self.psd_panel._bad_names = current_bad
            self.psd_panel._refresh_lists()
            self.psd_panel._sync_selection_to_lists()
            for group in ("macro", "micro"):
                self.psd_panel._refresh_plot(group)
        # Rebuild automatic bipolar montage only when relevant
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

    def _mark_channels_bad_from_psd(self, channel_names: list[str]) -> None:
        if self.current_raw is None or not channel_names:
            return

        current_bad = set(self.viewer.get_bad_channels())
        added: list[str] = []

        for name in channel_names:
            ch = str(name).strip()
            if not ch:
                continue
            if ch not in current_bad:
                current_bad.add(ch)
                added.append(ch)

        if not added:
            return

        self.viewer.set_bad_channels(current_bad)

        # keep PSD panel visual state in sync if it is open
        if self.psd_panel is not None:
            self.psd_panel._bad_names = set(current_bad)
            for group in ("macro", "micro"):
                self.psd_panel._refresh_plot(group)
        self.console.log(f"Marked as bad: {', '.join(added)}")

    def _mark_channels_good_from_psd(self, channel_names: list[str]) -> None:
        if self.current_raw is None or not channel_names:
            return

        current_bad = set(self.viewer.get_bad_channels())
        removed: list[str] = []

        for name in channel_names:
            ch = str(name).strip()
            if ch in current_bad:
                current_bad.remove(ch)
                removed.append(ch)

        if not removed:
            return

        self.viewer.set_bad_channels(current_bad)

        if self.psd_panel is not None:
            self.psd_panel._bad_names = set(current_bad)
            for group in ("macro", "micro"):
                self.psd_panel._refresh_plot(group)

        self.console.log(f"Unmarked as bad: {', '.join(removed)}")

# ---------------- Filters  -------------
    def on_toggle_permanent_filters(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Permanent Filters", "Load a dataset first.")
            return

        visible = self.filter_controls_widget.isVisible()
        self.filter_controls_widget.setVisible(not visible)

    def _scope_key_from_ui(self) -> str:
        txt = str(self.filter_scope.currentText()).strip().lower()
        if txt == "macro":
            return "macro"
        if txt == "micro":
            return "micro"
        return "all"

    def _get_profile(self, scope_key: str) -> FilterSettings:
        if scope_key == "micro":
            return self.filter_profiles.micro
        return self.filter_profiles.macro

    def _filter_settings_from_ui(self) -> FilterSettings:
        hp = float(self.filter_hp.value())
        lp = float(self.filter_lp.value())

        return FilterSettings(
            highpass_hz=(None if hp <= 0.0 else hp),
            lowpass_hz=(None if lp <= 0.0 else lp),
            notch_mode=str(self.filter_notch.currentText()),
        )

    def _push_scope_profile_to_ui(self, scope_key: str | None = None) -> None:
        if scope_key is None:
            scope_key = self._scope_key_from_ui()

        # For "All", show macro if macro==micro; otherwise keep current values untouched
        if scope_key == "all":
            macro = self.filter_profiles.macro
            micro = self.filter_profiles.micro
            same = (
                macro.highpass_hz == micro.highpass_hz
                and macro.lowpass_hz == micro.lowpass_hz
                and macro.notch_mode == micro.notch_mode
            )
            if not same:
                return
            settings = macro
        elif scope_key == "micro":
            settings = self.filter_profiles.micro
        else:
            settings = self.filter_profiles.macro

        hp = 0.0 if settings.highpass_hz is None else float(settings.highpass_hz)
        lp = 0.0 if settings.lowpass_hz is None else float(settings.lowpass_hz)

        self.filter_hp.setValue(hp)
        self.filter_lp.setValue(lp)
        self.filter_notch.setCurrentText(settings.notch_mode)

    def _fmt_filter_short(self, settings: FilterSettings) -> str:
        hp = "Off" if settings.highpass_hz is None else f"HP {settings.highpass_hz:g}"
        lp = "Off" if settings.lowpass_hz is None else f"LP {settings.lowpass_hz:g}"
        if settings.notch_mode == NOTCH_50_HARM:
            notch = "N50"
        elif settings.notch_mode == NOTCH_60_HARM:
            notch = "N60"
        else:
            notch = "NOff"
        return f"{hp} | {lp} | {notch}"

    def _update_filter_summary_label(self) -> None:
        macro_txt = self._fmt_filter_short(self.filter_profiles.macro)
        micro_txt = self._fmt_filter_short(self.filter_profiles.micro)
        self.filter_summary.setText(f"Macro: {macro_txt}   ·   Micro: {micro_txt}")

    def _on_filter_scope_changed(self, _text: str) -> None:
        self._push_scope_profile_to_ui()

# ---------------- User guide  -------------

    def on_open_user_guide(self) -> None:
        """
        Open the bundled markdown user guide in a simple dialog.
        For now, this is intentionally lightweight.
        """
        guide_path = Path(__file__).resolve().parent / "docs" / "user_guide.md"

        if not guide_path.exists():
            QMessageBox.information(
                self,
                "User Guide",
                f"Guide file not found:\n{guide_path}"
            )
            return

        try:
            markdown_text = guide_path.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "User Guide", f"Could not read guide:\n{e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("User Guide")
        dlg.resize(900, 700)

        layout = QVBoxLayout(dlg)

        viewer = QTextBrowser(dlg)
        viewer.setOpenExternalLinks(True)

        # Simple option 1: show raw markdown
        # viewer.setPlainText(markdown_text)

        # Simple option 2: let Qt display it as preformatted text
        viewer.setPlainText(markdown_text)

        layout.addWidget(viewer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dlg.close)
        layout.addWidget(buttons)

        dlg.exec()

 