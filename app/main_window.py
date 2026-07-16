from __future__ import annotations

import csv
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TypedDict

import numpy as np
import mne
from mne.io import BaseRaw

from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QDoubleSpinBox, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QDialog, QDialogButtonBox,
    QCheckBox, QComboBox, QLineEdit, QFormLayout, QSpinBox, QToolBar, QToolButton, QVBoxLayout,
    QWidget, QDockWidget, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextBrowser, QTabWidget, QTabBar, QSizePolicy
)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QKeySequence, QShortcut, QPixmap, QIcon, QColor

from app.menus import build_menubar
from app.viewer.plot import MultiChannelViewer
from app.computation.panel import ComputationPanel
from app.viewer.time_controls import TimeWindowControl
from app.annotations import (
    ANNOTATION_TYPES, ANNOTATION_STYLES, 
    ANNOTATION_SCOPES, SCOPE_SELECTED
)
from app.project_io import save_project, load_project
from app.project_file_helpers import ProjectFileHelper
from app.referencing import (
    build_automatic_bipolar_montage,
    BipolarMontage,
    BipolarPair,
    update_pair_channel2,
    parse_channel_label,
    extract_core_contact_label,
    looks_like_bipolar_derivation_label,
    bipolar_pair_display_name,
    refresh_bipolar_montage_pair_names,
)
from app.preprocessing.psd_panel import PSDIntervalDialog, PSDPanel
from app.preprocessing.filtering import (
    FilterSettings,
    FilterProfiles,
    NOTCH_OFF,
    NOTCH_50_HARM,
    NOTCH_60_HARM,
    validate_filter_settings,
)
from app.viewer.display_theme import DEFAULT_DISPLAY_THEME, DISPLAY_THEME_CHOICES, get_display_theme
from app.viewer.scalogram_viewer import ScalogramViewerWindow, build_scalogram_context
from app.expert_event_grid import ExpertEventGridDialog
from app.diagnostics.performance_monitor import monitor, timed_mark
from app.ui_busy import busy_cursor


def _channel_label_sort_key(label: str) -> tuple:
    parsed = parse_channel_label(label)
    if parsed is not None:
        return (
            0,
            parsed.electrode_prefix.casefold(),
            parsed.contact_number,
            parsed.normalized_label.casefold(),
            str(label).casefold(),
        )
    return (1, str(label).casefold())


class _SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            left = self.data(Qt.ItemDataRole.UserRole)
            right = other.data(Qt.ItemDataRole.UserRole)
            if left is not None and right is not None:
                return left < right
        return super().__lt__(other)


class _SilentConsole:
    """Drop-in logger used when the separate Console window is disabled."""

    def log(self, _message: str) -> None:
        return

    def close(self) -> None:
        return


class MontageEditorRowMeta(TypedDict):
    source_pair: BipolarPair | None
    is_new: bool
    editable_ch1: bool
    editable_ch2: bool
    ch1_combo: QComboBox | None
    ch2_combo: QComboBox | None


class MontageEditorCurrentRow(TypedDict):
    pair_name: str
    ch1_value: str
    ch2_value: str
    origin_value: str
    editable_ch1: bool
    editable_ch2: bool
    source_pair: BipolarPair | None
    is_new: bool


class MontageEditorSortState(TypedDict):
    column: int | None
    order: Qt.SortOrder


class MainWindow(QMainWindow):
    # ---------------- Lifecycle ----------------

    def __init__(self):
        super().__init__()
        monitor().mark("start", notes="MainWindow initialized")

        self._base_title = "iEEG Tool"
        self.setWindowTitle(self._base_title)
        self.resize(1400, 800)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            self.dockOptions()
            | QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        # ---- Menu bar ----
        self._act_scalogram: QAction | None = None
        self._act_reset_zoom: QAction | None = None
        self._act_save, self._act_saveas, self._act_close = build_menubar(self)
        self._act_save.setEnabled(False)
        self._act_saveas.setEnabled(False)
        self._act_close.setEnabled(False)

        for m in getattr(self, "_menus_disabled_until_loaded", []):
            m.setEnabled(False)

        # Disable TODO actions (always disabled)
        for action in getattr(self, "_todo_actions", []):
            action.setEnabled(False)

        # ---- Toolbar (controls) ----
        self._display_theme_key = DEFAULT_DISPLAY_THEME
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


        self.main_tabs = QTabWidget()
        self.main_tabs.setTabsClosable(True)
        self.main_tabs.tabCloseRequested.connect(self._on_main_tab_close_requested)

        self.viewer = MultiChannelViewer()
        self._viewer_tab_index = self.main_tabs.addTab(self.viewer, "Viewer")

        self.psd_panel = PSDPanel(
            parent=self,
            mark_bad_callback=self._mark_channels_bad_from_psd,
            mark_good_callback=self._mark_channels_good_from_psd,
        )
        self._psd_tab_index = self.main_tabs.addTab(self.psd_panel, "PSD")
        self.main_tabs.tabBar().setTabButton(self._viewer_tab_index, QTabBar.ButtonPosition.LeftSide, None)
        self.main_tabs.tabBar().setTabButton(self._viewer_tab_index, QTabBar.ButtonPosition.RightSide, None)
        self._set_psd_tab_visible(False)

        layout.addWidget(self.main_tabs, 1)

        # ---- Timeline (time slider) ----
        self.timeline = QFrame()
        self.timeline.setFixedHeight(70)
        self.timeline.setFrameShape(QFrame.Shape.StyledPanel)

        tl = QHBoxLayout(self.timeline)
        tl.setContentsMargins(12, 8, 12, 8)

        self.time_ctl = TimeWindowControl(label_prefix="t0")
        tl.addWidget(self.time_ctl, 1)

        # Debounce timeline drags so rapid slider movement does not trigger
        # expensive read/render cycles for every intermediate position. This
        # improves UI smoothness only; the final rendered data window is unchanged.
        self._timeline_render_timer = QTimer(self)
        self._timeline_render_timer.setSingleShot(True)
        self._timeline_render_timer.setInterval(150)
        self._pending_timeline_t0: float | None = None
        self._timeline_render_timer.timeout.connect(self._flush_pending_timeline_render)

        self.timeline.hide()
        layout.addWidget(self.timeline, 0)

        # ---- Console ----
        # Keep existing log calls harmless without opening a separate window.
        self.console = _SilentConsole()

        # ---- State ----
        self.current_raw: BaseRaw | None = None
        self.current_picks: np.ndarray | None = None
        self.loaded_file: Path | None = None

        self.project_path: Path | None = None
        self.project_dirty: bool = False
        self._saved_bipolar_montage: BipolarMontage | None = None

        self.source_raw: BaseRaw | None = None   # original, never modified

        self.filter_profiles = FilterProfiles()
        self._psd_interval: tuple[float, float] | None = None
        self._scalogram_windows: list[ScalogramViewerWindow] = []
        self._expert_event_grid_dialog: ExpertEventGridDialog | None = None
        self._expert_event_grid_loaded_file: Path | None = None

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
        self.comp_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.comp_dock.setMinimumSize(260, 220)
        self.comp_dock.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.comp_panel = ComputationPanel()
        self.comp_panel.set_ei_montage_callbacks(
            current_montage=self._current_montage_for_ei,
            switch_to_bipolar=self._switch_to_bipolar_for_ei,
        )
        self.comp_panel.set_ei_filter_callback(self._ei_notch_modes_by_group)
        self.comp_panel.set_ei_data_callback(self._get_ei_data_for_computation)
        self.comp_dock.setWidget(self.comp_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.comp_dock)
        self.comp_dock.hide()
        self._comp_dock_default_size_applied = False

        # ---- Annotations dock ----
        self.anno_dock = QDockWidget("Annotations", self)
        self.anno_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.anno_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.anno_dock.setMinimumSize(220, 180)
        self.anno_dock.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.anno_list = QListWidget()
        self.anno_list.setMinimumSize(180, 120)
        self.anno_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
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
        self.viewer.requestAmpRangeDelta.connect(self._zoom_amp_range)
        self.viewer.requestOpenComputationPanel.connect(self._open_computation_panel)
        self.viewer.requestEditChannelGroups.connect(self.on_edit_channel_groups)
        self.viewer.gammaSpikeMarkerClicked.connect(self._on_gamma_spike_marker_clicked)

        # Timeline sync
        self.viewer.timeWindowChanged.connect(self._sync_time_from_viewer)
        self.time_ctl.t0Changed.connect(self._on_time_ctl_t0_changed)
        self.time_ctl.slider.sliderReleased.connect(self._flush_pending_timeline_render)

        # keep panel time updated when main time moves
        self.viewer.timeWindowChanged.connect(self._push_time_to_comp_panel)
        
        # keep panel time updated when main window length changes
        self.time_range.valueChanged.connect(lambda v: self._push_time_to_comp_panel(self.viewer.time_start()))
        
        #Channel selection updated 
        self.comp_panel.panelSelectionChanged.connect(self._on_comp_panel_selection_changed)
        self.comp_panel.settingsChanged.connect(self._mark_project_dirty)
        self.comp_panel.seizureMarkersChanged.connect(self._on_ei_markers_changed)
        self.comp_panel.seizureMarkerEdited.connect(self._on_ei_marker_edited)
        self.comp_panel.gammaAnalysisWindowChanged.connect(self._on_gamma_analysis_window_changed)
        self.comp_panel.recruitmentMarkersChanged.connect(self._on_ei_recruitment_markers_changed)
        self.comp_panel.eiScoreLabelsChanged.connect(self._on_ei_score_labels_changed)
        self.comp_panel.eiSummaryChannelActivated.connect(self._on_ei_summary_channel_activated)
        self.comp_panel.eiSummaryOrderChanged.connect(self._on_ei_summary_order_changed)
        self.comp_panel.gammaSpikeMarkersChanged.connect(self._on_gamma_spike_markers_changed)
        self.comp_panel.gammaSpikeEventActivated.connect(self._on_gamma_spike_event_activated)
        self.comp_dock.visibilityChanged.connect(self._on_computation_dock_visibility_changed)
        
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
        self.viewer.scalogramRequested.connect(self._open_scalogram_for_selection)
        self.viewer.scalogramModeChanged.connect(self._on_scalogram_mode_changed)

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

        self.shortcut_psd_tab = QShortcut(QKeySequence("Ctrl+T"), self)
        self.shortcut_psd_tab.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_psd_tab.activated.connect(self.open_psd_panel)

        self._apply_display_theme(self._display_theme_key)

    def closeEvent(self, event):
        """Ensure the app quits cleanly when the main window closes."""
        if not self._confirm_close_unsaved_changes("quit"):
            event.ignore()
            return

        try:
            if self._expert_event_grid_dialog is not None:
                self._expert_event_grid_dialog.close()
            if hasattr(self, "console") and self.console is not None:
                self.console.close()
        finally:
            QApplication.quit()
            event.accept()

    def on_close_project(self) -> None:
        if self.current_raw is None:
            return

        if not self._confirm_close_unsaved_changes("close"):
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
            self.viewer.reset_empty()
        finally:
            self.viewer.blockSignals(False)

        self.tb.setEnabled(False)
        self.timeline.hide()
        self.time_ctl.set_range(0.0, 0.0, 0.0)

        self.comp_dock.hide()
        self.anno_dock.hide()
        if self._expert_event_grid_dialog is not None:
            self._expert_event_grid_dialog.close()
        self._expert_event_grid_loaded_file = None
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

        self.main_tabs.setCurrentWidget(self.viewer)
        self._set_psd_tab_visible(False)

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

        self.fit_traces = QCheckBox("Fit traces")
        self.fit_traces.setChecked(False)
        tb.addWidget(self.fit_traces)

        tb.addSeparator()

        tb.addWidget(QLabel("Theme:"))
        self.display_theme = QComboBox()
        for label, key in DISPLAY_THEME_CHOICES:
            self.display_theme.addItem(label, userData=key)
        self.display_theme.currentIndexChanged.connect(self._on_display_theme_changed)
        tb.addWidget(self.display_theme)

        tb.addSeparator()

        # Hidden channels menu button
        self.btn_hidden = QToolButton()
        self.btn_hidden.setText("Hidden...")
        self.btn_hidden.clicked.connect(self._show_hidden_channels_menu)
        tb.addWidget(self.btn_hidden)

        # Edit bipolar referencing 
        self.btn_edit_bipolar = QToolButton()
        self.btn_edit_bipolar.setText("Edit Bipolar...")
        self.btn_edit_bipolar.setEnabled(False)
        self.btn_edit_bipolar.clicked.connect(self.on_edit_bipolar_pairs)
        tb.addWidget(self.btn_edit_bipolar)

        # ---- Connect toolbar -> viewer ----
        self.time_range.valueChanged.connect(self._on_time_range_changed)
        self.time_range.valueChanged.connect(lambda _v: self._mark_project_dirty())
        self.gain.valueChanged.connect(lambda v: self.viewer.set_view_params(gain=v))
        self.gain.valueChanged.connect(lambda v: self.comp_panel.set_main_gain_uv(float(v)))
        self.gain.valueChanged.connect(lambda _v: self._mark_project_dirty())
        self.chan_range.valueChanged.connect(lambda v: self.viewer.set_view_params(chan_range=v))
        self.chan_range.valueChanged.connect(lambda _v: self._mark_project_dirty())
        self.fit_traces.toggled.connect(lambda checked: self.viewer.set_fit_visible_traces(checked))

    def _on_display_theme_changed(self, index: int) -> None:
        theme_key = self.display_theme.itemData(index)
        self._apply_display_theme(str(theme_key or DEFAULT_DISPLAY_THEME))

    def _apply_display_theme(self, theme_key: str) -> None:
        theme = get_display_theme(theme_key)
        self._display_theme_key = theme.key

        popup_style = f"""
            QMenuBar {{
                background-color: {theme.panel_background};
                color: {theme.text_color};
            }}
            QMenuBar::item:selected {{
                background-color: {theme.button_hover_background};
            }}
            QMenu {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
            }}
            QMenu::item {{
                padding: 4px 24px;
            }}
            QMenu::item:selected {{
                background-color: {theme.button_hover_background};
                color: {theme.input_text_color};
            }}
            QMenu::indicator,
            QMenu::indicator:non-exclusive:unchecked,
            QMenu::indicator:exclusive:unchecked {{
                width: 13px;
                height: 13px;
                margin-left: 4px;
                border: 1px solid {theme.border_color};
                background-color: {theme.input_background};
            }}
            QMenu::indicator:checked,
            QMenu::indicator:non-exclusive:checked,
            QMenu::indicator:exclusive:checked {{
                background-color: {theme.selected_label_color};
                border: 1px solid {theme.selected_label_color};
            }}
            QComboBox QAbstractItemView {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
                selection-background-color: {theme.button_hover_background};
                selection-color: {theme.input_text_color};
            }}
        """
        toolbar_style = f"""
            QToolBar {{
                background-color: {theme.panel_background};
                border-bottom: 1px solid {theme.border_color};
                spacing: 4px;
            }}
            QToolBar QLabel {{
                color: {theme.text_color};
            }}
            QToolBar QToolButton,
            QToolBar QComboBox,
            QToolBar QCheckBox,
            QToolBar QAbstractSpinBox {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
                padding: 2px 6px;
            }}
            QToolBar QCheckBox::indicator {{
                width: 13px;
                height: 13px;
                border: 1px solid {theme.border_color};
                background-color: {theme.input_background};
            }}
            QToolBar QCheckBox::indicator:checked {{
                background-color: {theme.selected_label_color};
                border: 1px solid {theme.selected_label_color};
            }}
            QToolBar QToolButton:hover {{
                background-color: {theme.button_hover_background};
            }}
            QToolBar QComboBox QAbstractItemView {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
                selection-background-color: {theme.button_hover_background};
                selection-color: {theme.input_text_color};
            }}
        """
        frame_style = f"""
            QFrame {{
                background-color: {theme.panel_background};
                border-bottom: 1px solid {theme.border_color};
            }}
            QLabel {{
                color: {theme.text_color};
            }}
            QToolButton,
            QComboBox,
            QAbstractSpinBox {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
                padding: 2px 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {theme.input_background};
                color: {theme.input_text_color};
                border: 1px solid {theme.border_color};
                selection-background-color: {theme.button_hover_background};
                selection-color: {theme.input_text_color};
            }}
        """
        timeline_style = f"""
            QFrame {{
                background-color: {theme.panel_background};
                border-top: 1px solid {theme.border_color};
            }}
            QLabel {{
                color: {theme.text_color};
            }}
        """

        self.setStyleSheet(popup_style)
        self.tb.setStyleSheet(toolbar_style)
        self.top_controls.setStyleSheet(frame_style)
        self.timeline.setStyleSheet(timeline_style)
        self.filter_summary.setStyleSheet(
            f"color: {theme.secondary_text_color}; padding-left: 8px;"
        )
        self.viewer.set_display_theme(theme.key)

        current_index = self.display_theme.findData(theme.key)
        if current_index >= 0 and self.display_theme.currentIndex() != current_index:
            self.display_theme.blockSignals(True)
            self.display_theme.setCurrentIndex(current_index)
            self.display_theme.blockSignals(False)

        for window in list(self._scalogram_windows):
            try:
                window.set_display_theme(theme.key)
            except Exception:
                pass

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
        perf_start = time.perf_counter()
        try:
            raw, picks = self._load_eeg_file(raw_path)
        except Exception as e:
            timed_mark(
                "after_open",
                perf_start,
                file_path=raw_path,
                notes=f"error: {e}",
            )
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

        time_range = float(self.time_range.value())
        chan_range = int(self.chan_range.value())
        gain = float(self.gain.value())

        self.viewer.set_raw(self.current_raw, self.current_picks)
        self.viewer.set_channel_groups(self.channel_groups)
        self.viewer.show()
        self.viewer.update()
        self.viewer.repaint()
        self.filter_controls_widget.hide()

        self.viewer.set_view_params(
            time_range=time_range,
            chan_range=chan_range,
            gain=gain,
        )

        self.viewer.set_time_start(0.0)
        self.viewer.set_channel_start(0)

        self._saved_bipolar_montage = None
        self._update_montage_label()

        self.project_path = None
        self.project_dirty = False
        self._expert_event_grid_loaded_file = None

        self._enable_loaded_ui()
        self._act_save.setEnabled(False)
        self._update_window_title()

        timed_mark(
            "after_open",
            perf_start,
            raw=self.current_raw,
            file_path=self.loaded_file,
            visible_window_s=float(self.time_range.value()),
            filter_mode=(
                f"macro={self._fmt_filter_short(self.filter_profiles.macro)}; "
                f"micro={self._fmt_filter_short(self.filter_profiles.micro)}"
            ),
            reference_mode=self.viewer.reference_mode(),
        )
        if self._channel_groups_need_sampling_review(raw):
            QTimer.singleShot(0, self._open_channel_groups_for_mixed_sampling)
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
        
    @staticmethod
    def _first_positive_float(value) -> float | None:
        if value is None:
            return None
        try:
            arr = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return None
        finite = arr[np.isfinite(arr) & (arr > 0)]
        if finite.size == 0:
            return None
        return float(finite[0])

    def _channel_sampling_rates_by_name(self, raw: BaseRaw | None = None) -> dict[str, float]:
        raw = raw or self.source_raw or self.current_raw
        if raw is None:
            return {}

        fallback_sfreq = float(raw.info.get("sfreq", 0.0) or 0.0)
        rates = {str(ch): fallback_sfreq for ch in raw.ch_names}

        # MNE exposes one display sfreq after loading, but EDF/BDF readers keep
        # original per-channel sample counts in _raw_extras. Use them for the
        # title/group review only; this does not change data accuracy or reads.
        for extra in getattr(raw, "_raw_extras", None) or []:
            if not isinstance(extra, dict):
                continue

            n_samps = extra.get("n_samps")
            record_length = self._first_positive_float(
                extra.get("record_length", extra.get("data_record_duration"))
            )
            if n_samps is None or record_length is None:
                continue

            try:
                samples_per_record = np.asarray(n_samps, dtype=float).reshape(-1)
            except (TypeError, ValueError):
                continue

            names = extra.get("ch_names") or raw.ch_names
            for ch_name, n_samples in zip(names, samples_per_record):
                ch_key = str(ch_name)
                if ch_key not in rates or not np.isfinite(n_samples) or n_samples <= 0:
                    continue
                rates[ch_key] = float(n_samples) / record_length

        return rates

    @staticmethod
    def _format_group_sampling_rates(channel_names: list[str], rates: dict[str, float]) -> str:
        values = [
            float(rates[ch])
            for ch in channel_names
            if ch in rates and np.isfinite(float(rates[ch])) and float(rates[ch]) > 0
        ]
        if not values:
            return "n/a"

        unique = sorted({round(value, 6) for value in values})
        if len(unique) == 1:
            return f"{unique[0]:g} Hz"
        return f"mixed {unique[0]:g}-{unique[-1]:g} Hz"

    def _has_mixed_channel_sampling(self, raw: BaseRaw | None = None) -> bool:
        rates = self._channel_sampling_rates_by_name(raw)
        unique = {
            round(float(rate), 6)
            for rate in rates.values()
            if np.isfinite(float(rate)) and float(rate) > 0
        }
        return len(unique) > 1

    def _channel_groups_need_sampling_review(self, raw: BaseRaw | None = None) -> bool:
        if not self._has_mixed_channel_sampling(raw):
            return False
        if raw is None:
            raw = self.source_raw
        if raw is None:
            return False

        groups = {
            str(self.channel_groups.get(str(ch), "macro")).casefold()
            for ch in raw.ch_names
        }
        return "micro" not in groups

    def _open_channel_groups_for_mixed_sampling(self) -> None:
        if not self._channel_groups_need_sampling_review(self.source_raw):
            return
        self.console.log(
            "Mixed channel sampling frequencies detected; opening Channel Groups."
        )
        self.on_edit_channel_groups()
        self._update_window_title()

    def _build_title_freq_text(self) -> str:
        raw = self.source_raw or self.current_raw
        if raw is None:
            return "Macro Fs: n/a"

        rates = self._channel_sampling_rates_by_name(raw)
        groups = self.channel_groups or {str(ch): "macro" for ch in raw.ch_names}
        parts: list[str] = []

        for label, group_name in (("Macro", "macro"), ("Micro", "micro")):
            channel_names = [
                str(ch)
                for ch in raw.ch_names
                if str(groups.get(str(ch), "macro")).casefold() == group_name
            ]
            if not channel_names:
                continue
            parts.append(
                f"{label} Fs: {self._format_group_sampling_rates(channel_names, rates)}"
            )

        if not parts:
            all_channels = [str(ch) for ch in raw.ch_names]
            parts.append(
                f"Macro Fs: {self._format_group_sampling_rates(all_channels, rates)}"
            )

        return " | ".join(parts)

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
        freq_txt = self._build_title_freq_text()

        self.setWindowTitle(
            f"{base} | Folder: {file_txt} | Ch: {n_sel}/{n_total} | Dur: {dur_s:.1f}s | {freq_txt}"
        )
    
    def on_new_project(self) -> None:
        if not self._confirm_close_unsaved_changes("close"):
            return

        raw_path = ProjectFileHelper.choose_raw_file(self)
        if raw_path is None:
            return

        project_path = ProjectFileHelper.choose_project_to_create(self, raw_path)
        if project_path is None:
            return

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
        if not self._confirm_close_unsaved_changes("close"):
            return

        project_path = ProjectFileHelper.choose_project_to_open(self)
        if project_path is None:
            return

        try:
            payload = load_project(project_path)
        except Exception as e:
            QMessageBox.critical(self, "Open project error", str(e))
            return

        source = payload.get("source")
        if not isinstance(source, dict):
            QMessageBox.critical(self, "Open project error", "Project is missing a valid 'source' section.")
            return

        if not (
            isinstance(source.get("raw_file"), str) and source.get("raw_file", "").strip()
        ) and not (
            isinstance(source.get("raw_file_relative"), str) and source.get("raw_file_relative", "").strip()
        ):
            QMessageBox.critical(
                self,
                "Open project error",
                "Project does not contain a valid raw_file path.",
            )
            return

        raw_path = ProjectFileHelper.resolve_project_raw_path(self, project_path, source)
        if raw_path is None:
            return

        # Reuse the standard raw-file opening flow
        if not self._open_raw_file(raw_path):
            return

        if self.loaded_file != raw_path:
            self.loaded_file = raw_path

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

        review = payload.get("review")
        if not isinstance(review, dict):
            review = {}

        computation = payload.get("computation")
        if not isinstance(computation, dict):
            computation = {}

        display = payload.get("display")
        if not isinstance(display, dict):
            display = {}

        annos = review.get("annotations", [])
        hidden_raw = review.get("hidden_channels", [])
        bad_raw = review.get("bad_channels", [])
        saved_montage_raw = review.get("bipolar_montage")
        saved_channel_groups = review.get("channel_groups", {})

        self._restore_channel_groups(saved_channel_groups)
        self._push_scope_profile_to_ui()
        self._rebuild_active_raw_from_source()
        self._refresh_active_signal_everywhere()
        self._restore_display_settings(display)
        self._update_filter_summary_label()

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
            self.comp_panel.restore_project_state(computation)
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

    def _save_project_to_path(self, path: Path) -> bool:
        try:
            save_project(path, self)
            self.project_path = path
            self.console.log(f"Project saved: {path}")
            self._mark_project_clean()
            self._act_save.setEnabled(True)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save project error", str(e))
            return False

    def _save_project_as_interactive(self) -> bool:
        default = ""
        if self.project_path is not None:
            default = str(self.project_path)
        elif self.loaded_file is not None:
            default = str(self.loaded_file.with_suffix(".ieeg"))

        p = ProjectFileHelper.choose_project_to_save_as(self, default)
        if p is None:
            return False

        return self._save_project_to_path(p)

    def _save_current_project_interactive(self) -> bool:
        if self.current_raw is None:
            return True

        if self.project_path is None:
            return self._save_project_as_interactive()

        return self._save_project_to_path(self.project_path)

    def _confirm_close_unsaved_changes(self, action: str) -> bool:
        if self.current_raw is None or not getattr(self, "project_dirty", False):
            return True

        item_name = "current project"
        if self.project_path is not None:
            item_name = self.project_path.name
        elif self.loaded_file is not None:
            item_name = self.loaded_file.name

        action_text = "quitting" if action == "quit" else "closing"

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Unsaved changes")
        msg.setText(f"Save changes to {item_name} before {action_text}?")
        msg.setInformativeText("Your review changes will be lost if you do not save them.")
        save_btn = msg.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        dont_save_btn = msg.addButton("Don't Save", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(save_btn)
        msg.setEscapeButton(cancel_btn)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is save_btn:
            return self._save_current_project_interactive()
        if clicked is dont_save_btn:
            return True
        return False

    def on_save_project(self) -> bool:
        if self.current_raw is None:
            QMessageBox.information(self, "Save project", "Load a dataset first.")
            return False

        if self.project_path is None:
            return self.on_save_project_as()

        return self._save_project_to_path(self.project_path)

    def on_save_project_as(self) -> bool:
        if self.current_raw is None:
            QMessageBox.information(self, "Save project as", "Load a dataset first.")
            return False

        return self._save_project_as_interactive()

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
        table.horizontalHeader().setSectionsClickable(True)
        table.horizontalHeader().setSortIndicatorShown(False)
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
        sort_column: int | None = None
        sort_order = Qt.SortOrder.AscendingOrder

        def _populate(filter_text: str = "") -> None:
            text = filter_text.strip().lower()
            table.setRowCount(0)

            for ch in channel_names:
                if text and text not in ch.lower():
                    continue

                row = table.rowCount()
                table.insertRow(row)

                item_name = _SortableTableWidgetItem(ch)
                item_name.setData(Qt.ItemDataRole.UserRole, _channel_label_sort_key(ch))
                item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, item_name)

                group = working_groups.get(ch, "macro")
                item_group = _SortableTableWidgetItem(group.capitalize())
                item_group.setData(Qt.ItemDataRole.UserRole, str(group).casefold())
                item_group.setFlags(item_group.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 1, item_group)

            if sort_column is not None:
                table.sortItems(sort_column, sort_order)

        def _sort_by_header(column: int) -> None:
            nonlocal sort_column, sort_order
            current_sort_column = int(column)
            if sort_column == current_sort_column:
                sort_order = (
                    Qt.SortOrder.DescendingOrder
                    if sort_order == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                sort_column = current_sort_column
                sort_order = Qt.SortOrder.AscendingOrder

            table.horizontalHeader().setSortIndicatorShown(True)
            table.horizontalHeader().setSortIndicator(current_sort_column, sort_order)
            table.sortItems(current_sort_column, sort_order)

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
                group_item.setData(Qt.ItemDataRole.UserRole, group.casefold())

        search.textChanged.connect(_populate)
        table.horizontalHeader().sectionClicked.connect(_sort_by_header)
        btn_set_micro.clicked.connect(lambda: _set_selected_group("micro"))
        btn_set_macro.clicked.connect(lambda: _set_selected_group("macro"))

        _populate()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.channel_groups = working_groups
        self.viewer.set_channel_groups(self.channel_groups)
        self._mark_project_dirty()
        self._update_window_title()

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

    def _restore_display_settings(self, display: dict) -> None:
        if not isinstance(display, dict):
            return

        def _clamp_float(value, spin: QDoubleSpinBox) -> float | None:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return None
            if not np.isfinite(numeric):
                return None
            return max(float(spin.minimum()), min(float(spin.maximum()), numeric))

        def _clamp_int(value, spin: QSpinBox) -> int | None:
            try:
                numeric = int(round(float(value)))
            except (TypeError, ValueError):
                return None
            return max(int(spin.minimum()), min(int(spin.maximum()), numeric))

        time_range = _clamp_float(display.get("time_range_s"), self.time_range)
        channel_range = _clamp_int(display.get("channel_range"), self.chan_range)
        gain = _clamp_float(display.get("amplitude_uv"), self.gain)

        if time_range is not None:
            self.time_range.setValue(time_range)
        if channel_range is not None:
            self.chan_range.setValue(channel_range)
        if gain is not None:
            self.gain.setValue(gain)

        self.viewer.set_view_params(
            time_range=time_range,
            chan_range=channel_range,
            gain=gain,
        )
        self._update_time_slider_range()
        self.comp_panel.set_main_gain_uv(float(self.gain.value()))

    def _enable_loaded_ui(self) -> None:
        self.tb.setEnabled(True)
        self.timeline.show()

        if hasattr(self, "filter_controls_widget"):
            self.filter_controls_widget.hide()

        self.viewer.show()
        self.viewer.update()
        self._update_time_slider_range()
        self._sync_time_from_viewer(self.viewer.time_start())

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
        display_channel_groups = {
            ch_name: self.viewer.get_channel_group(ch_name)
            for ch_name in displayed_names
        }
        self.comp_panel.set_data_context(
            self.current_raw,
            self.current_picks,
            displayed_names,
            channel_groups=display_channel_groups,
            bad_names=self.viewer.get_bad_channels(),
            source_file_path=self.loaded_file,
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
        self.montage_label.setText(f"Montage: {self._current_montage_for_ei()}")

    def _current_montage_for_ei(self) -> str:
        mode = self.viewer.reference_mode()

        if mode == "bipolar":
            return "Bipolar"
        elif mode == "average":
            return "Average"
        elif mode == "median":
            return "Median"
        elif mode == "common":
            ref_name = self.viewer.common_reference_name() or "?"
            return f"Common ({ref_name})"
        else:
            return "Monopolar"

    def _switch_to_bipolar_for_ei(self) -> tuple[bool, str]:
        if self.current_raw is None:
            return False, "Load a dataset first."
        if self.viewer.reference_mode() == "bipolar":
            return True, ""

        channel_names = self.viewer.get_raw_channel_names()
        montage = self._saved_bipolar_montage

        if montage is None or not montage.pairs:
            montage = build_automatic_bipolar_montage(
                channel_names,
                bad_channels=self.viewer.get_bad_channels(),
            )

        montage = refresh_bipolar_montage_pair_names(montage)

        if not montage.pairs:
            return False, "No valid bipolar pairs could be generated automatically."

        with busy_cursor(self, "Switching to bipolar reference..."):
            self.viewer.set_bipolar_mode(montage)
            self._refresh_display_name_dependent_ui()
            self.btn_edit_bipolar.setEnabled(True)
            self._update_montage_label()
            self._mark_project_dirty()
            self.console.log(f"Reference mode: Bipolar ({len(montage.pairs)} pairs)")
        return True, ""

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
        if self.current_raw is None or self.current_picks is None:
            return
        if self._psd_interval is None:
            return
        if not self._is_psd_tab_open():
            return

        display_names = self.viewer.get_channel_names()
        macro_names, micro_names = self._split_display_channels_for_psd(display_names)

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
            filter_profiles=self.filter_profiles,
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

        self.current_raw = self.source_raw
        self.viewer.set_display_filter_profiles(self.filter_profiles)
            
    def on_apply_filters(self) -> None:
        if self.source_raw is None:
            QMessageBox.information(self, "Filters", "Load a dataset first.")
            return

        perf_start = time.perf_counter()
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
            "Display filters applied windowed | "
            f"Scope: {self.filter_scope.currentText()} | "
            f"Macro: {self._fmt_filter_short(self.filter_profiles.macro)} | "
            f"Micro: {self._fmt_filter_short(self.filter_profiles.micro)}"
        )
        timed_mark(
            "after_filter_render",
            perf_start,
            raw=self.current_raw,
            file_path=self.loaded_file,
            visible_window_s=float(self.time_range.value()),
            filter_mode=(
                f"macro={self._fmt_filter_short(self.filter_profiles.macro)}; "
                f"micro={self._fmt_filter_short(self.filter_profiles.micro)}"
            ),
            reference_mode=self.viewer.reference_mode(),
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

        display_names = self.viewer.get_channel_names()
        display_name = (
            str(display_names[int(abs_idx)])
            if 0 <= int(abs_idx) < len(display_names)
            else str(abs_idx)
        )
        self.comp_panel.highlight_ei_summary_channel(display_name)

        raw_idx = int(self.current_picks[abs_idx]) if 0 <= int(abs_idx) < len(self.current_picks) else None
        ch_name = self.current_raw.ch_names[raw_idx] if raw_idx is not None else display_name
        ch_type = (
            self.current_raw.get_channel_types(picks=[raw_idx])[0]
            if raw_idx is not None
            else self.viewer.reference_mode()
        )
        self.console.log(
            f"Selected: {display_name} (shown idx {abs_idx}, raw: {ch_name}, type: {ch_type})"
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
        # User moved the main timeline slider. Debounce expensive render/read
        # work during drags; this improves UI smoothness, not data accuracy.
        self._pending_timeline_t0 = float(t0)
        self._timeline_render_timer.start()

    def _flush_pending_timeline_render(self) -> None:
        if self._pending_timeline_t0 is None:
            return
        t0 = float(self._pending_timeline_t0)
        self._pending_timeline_t0 = None
        self._timeline_render_timer.stop()
        self.viewer.set_time_start(t0)

    def _on_ei_markers_changed(self, onset_s, offset_s) -> None:
        onset = float(onset_s) if isinstance(onset_s, (int, float)) else None
        offset = float(offset_s) if isinstance(offset_s, (int, float)) else None
        self.viewer.set_seizure_markers(onset, offset)

    def _on_ei_marker_edited(self, _kind: str, value_s) -> None:
        if self.current_raw is None:
            return
        if not isinstance(value_s, (int, float)):
            return

        target = float(value_s)
        if not np.isfinite(target):
            return

        total_s = float(self.current_raw.times[-1]) if self.current_raw.n_times > 1 else 0.0
        target = max(0.0, min(target, total_s))
        view_range = float(getattr(self.viewer, "_time_range", 0.0) or self.time_range.value())
        self.viewer.set_time_start(target - 0.5 * view_range)
        self.viewer.set_cursor_x(target)
        self.time_ctl.set_t0(self.viewer.time_start())

    def _on_gamma_analysis_window_changed(self, start_s, end_s) -> None:
        start = float(start_s) if isinstance(start_s, (int, float)) else None
        end = float(end_s) if isinstance(end_s, (int, float)) else None
        self.viewer.set_analysis_window_markers(start, end)

    def _on_ei_recruitment_markers_changed(self, markers: dict) -> None:
        self.viewer.set_recruitment_markers(markers if isinstance(markers, dict) else {})

    def _on_ei_score_labels_changed(self, styles: dict) -> None:
        self.viewer.set_ei_label_styles(styles if isinstance(styles, dict) else {})

    def _on_ei_summary_order_changed(self, ordered_channel_names: list) -> None:
        if self.current_raw is None:
            return
        names = [str(name) for name in ordered_channel_names]
        self.viewer.set_display_order_by_channel_names(names)

    def _on_gamma_spike_markers_changed(self, markers: dict) -> None:
        self.viewer.set_gamma_spike_markers(markers if isinstance(markers, dict) else {})

    def _on_gamma_spike_event_activated(self, channel_name: str, time_s: float) -> None:
        self._jump_viewer_to_event(float(time_s), str(channel_name))

    def _on_gamma_spike_marker_clicked(self, channel_name: str, time_s: float) -> None:
        self.comp_panel.open_gamma_review_at(str(channel_name), float(time_s))

    def _on_computation_dock_visibility_changed(self, visible: bool) -> None:
        if visible:
            return
        if self.viewer is None:
            return
        self.viewer.set_seizure_markers(None, None)
        self.viewer.set_analysis_window_markers(None, None)
        self.viewer.set_recruitment_markers({})
        self.viewer.set_gamma_spike_markers({})
        self.viewer.set_ei_label_styles({})
        self.viewer.clear_display_order_override()

    def _refresh_display_name_dependent_ui(self) -> None:
        self._sync_comp_panel_context()
        self._refresh_annotation_list()
        self._refresh_psd_panel_context()

    def _is_psd_tab_open(self) -> bool:
        return bool(self.main_tabs.isTabVisible(self._psd_tab_index))

    def _set_psd_tab_visible(self, visible: bool) -> None:
        self.main_tabs.setTabVisible(self._psd_tab_index, bool(visible))
        if not visible:
            self.main_tabs.setCurrentWidget(self.viewer)

    def _on_main_tab_close_requested(self, index: int) -> None:
        if index == self._psd_tab_index:
            self._set_psd_tab_visible(False)

    def _split_display_channels_for_psd(self, display_names: list[str]) -> tuple[list[str], list[str]]:
        macro_names: list[str] = []
        micro_names: list[str] = []

        if self.viewer.reference_mode() == "bipolar":
            montage = self.viewer.get_bipolar_montage()
            pair_by_name = {}
            if montage is not None:
                pair_by_name = {pair.name: pair for pair in montage.pairs}

            for ch_name in display_names:
                pair = pair_by_name.get(ch_name)
                source_name = pair.ch1 if pair is not None else ch_name
                group = self.channel_groups.get(str(source_name), "macro")
                if group == "micro":
                    micro_names.append(ch_name)
                else:
                    macro_names.append(ch_name)
            return macro_names, micro_names

        for ch_name in display_names:
            group = self.channel_groups.get(str(ch_name), "macro")
            if group == "micro":
                micro_names.append(ch_name)
            else:
                macro_names.append(ch_name)

        return macro_names, micro_names

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

    def _zoom_amp_range(self, direction: int):
        """direction: -1 zoom in (lower gain, smaller amplitude), +1 zoom out (higher gain, larger amplitude)"""
        step = self.gain.singleStep() if self.gain.singleStep() else 10.0
        new_v = self.gain.value() + direction * step
        new_v = max(self.gain.minimum(), min(self.gain.maximum(), new_v))
        self.gain.setValue(new_v)

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

    def _resize_visible_dock(self, dock: QDockWidget, *, side_size: int, bottom_size: int) -> None:
        if dock.isFloating():
            dock.resize(max(dock.width(), side_size), max(dock.height(), bottom_size))
            return

        area = self.dockWidgetArea(dock)
        if area == Qt.DockWidgetArea.BottomDockWidgetArea:
            self.resizeDocks([dock], [int(bottom_size)], Qt.Orientation.Vertical)
        else:
            self.resizeDocks([dock], [int(side_size)], Qt.Orientation.Horizontal)

    def _open_computation_panel(self, selected_abs: list[int]) -> None:
        self._sync_comp_panel_context()
        self.comp_panel.set_selected_channels_abs(selected_abs, replace=True)
        self._sync_comp_panel_view_state()
        self.comp_dock.show()
        if not self._comp_dock_default_size_applied:
            self._resize_visible_dock(self.comp_dock, side_size=430, bottom_size=300)
            self._comp_dock_default_size_applied = True
        self.comp_dock.raise_()

    def open_computation_panel(self) -> None:
        if self.current_raw is None or self.current_picks is None:
            QMessageBox.information(self, "Computation Panel", "Load a dataset first.")
            return
        start = max(0, int(self.viewer.channel_start()))
        stop = min(len(self.current_picks), start + int(self.chan_range.value()))
        self._open_computation_panel(list(range(start, stop)))

    def _get_ei_data_for_computation(
        self,
        selected_abs: list[int],
        start_s: float,
        stop_s: float,
    ) -> tuple[np.ndarray, float, list[str]]:
        raw = self.source_raw if self.source_raw is not None else self.current_raw
        if raw is None or self.current_picks is None:
            raise RuntimeError("Load a dataset before running REI.")

        display_names = self.viewer.get_channel_names()
        fs = float(raw.info["sfreq"])
        t0 = float(min(start_s, stop_s))
        t1 = float(max(start_s, stop_s))
        if t1 <= t0:
            raise RuntimeError("Invalid REI data window.")

        start_samp = max(0, min(int(np.floor(t0 * fs)), raw.n_times - 1))
        stop_samp = max(start_samp + 1, min(int(np.ceil(t1 * fs)), raw.n_times))
        picks = np.asarray(self.current_picks, dtype=int)
        mode = self.viewer.reference_mode()
        bad_names = set(self.viewer.get_bad_channels())

        def read_raw_segment(channel_picks: list[int] | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            data = raw.get_data(picks=channel_picks, start=start_samp, stop=stop_samp)
            times = np.asarray(raw.times[start_samp:stop_samp], dtype=float)
            return np.asarray(data, dtype=float), times

        def raw_index_for_channel(ch_name: str) -> int | None:
            try:
                abs_idx = list(raw.ch_names).index(str(ch_name))
            except ValueError:
                return None
            if not (0 <= abs_idx < len(picks)):
                return None
            return int(picks[abs_idx])

        rows: list[np.ndarray] = []
        names: list[str] = []

        if mode == "bipolar":
            montage = self.viewer.get_bipolar_montage()
            pair_by_name = {
                pair.name: pair
                for pair in getattr(montage, "pairs", [])
            }
            for abs_idx in selected_abs:
                idx = int(abs_idx)
                if not (0 <= idx < len(display_names)):
                    continue
                display_name = str(display_names[idx])
                pair = pair_by_name.get(display_name)
                if pair is None:
                    continue
                ch1_pick = raw_index_for_channel(pair.ch1)
                ch2_pick = raw_index_for_channel(pair.ch2)
                if ch1_pick is None or ch2_pick is None:
                    continue
                data_v, _ = read_raw_segment([ch1_pick, ch2_pick])
                rows.append(np.asarray(data_v[0] - data_v[1], dtype=float) * 1e6)
                names.append(display_name)
        else:
            selected = [
                int(abs_idx)
                for abs_idx in selected_abs
                if 0 <= int(abs_idx) < len(display_names) and int(abs_idx) < len(picks)
            ]
            if not selected:
                raise RuntimeError("No selected channels are available for REI.")

            selected_picks = picks[np.asarray(selected, dtype=int)]
            selected_data, _ = read_raw_segment(selected_picks)
            selected_data = np.asarray(selected_data, dtype=float)

            ref = None
            if mode in {"average", "median"}:
                ref_abs = [
                    i for i, name in enumerate(raw.ch_names)
                    if str(name) not in bad_names and i < len(picks)
                ]
                if ref_abs:
                    ref_picks = picks[np.asarray(ref_abs, dtype=int)]
                    ref_data, _ = read_raw_segment(ref_picks)
                    ref_data = np.asarray(ref_data, dtype=float)
                    ref = (
                        np.nanmean(ref_data, axis=0)
                        if mode == "average"
                        else np.nanmedian(ref_data, axis=0)
                    )
            elif mode == "common":
                ref_name = self.viewer.common_reference_name()
                if ref_name:
                    ref_pick = raw_index_for_channel(ref_name)
                    if ref_pick is not None:
                        ref_data, _ = read_raw_segment([ref_pick])
                        ref = np.asarray(ref_data[0], dtype=float)

            for row_index, abs_idx in enumerate(selected):
                signal = np.asarray(selected_data[row_index], dtype=float)
                if ref is not None:
                    signal = signal - ref
                rows.append(signal * 1e6)
                names.append(str(display_names[abs_idx]))

        if not rows or min(row.size for row in rows) < 2:
            raise RuntimeError("Could not extract enough selected channel data for REI.")

        min_len = min(row.size for row in rows)
        data = np.vstack([row[:min_len] for row in rows])
        return data, fs, names

    def _on_comp_panel_selection_changed(self, selected_abs: list[int]):
        # Highlight the same channels in main viewer (and treat it as selection)
        self.viewer.set_selected_abs(selected_abs, anchor=(selected_abs[-1] if selected_abs else None), emit=True)

    def _on_ei_summary_channel_activated(self, channel_name: str) -> None:
        display_names = self.viewer.get_channel_names()
        idx = self._find_channel_index_by_label(display_names, str(channel_name))
        if idx is None:
            return
        self.viewer.center_channel_on(int(idx))
        self.viewer.set_selected_abs([int(idx)], anchor=int(idx), emit=True)

# ---------------- Referencing  -------------
 
    def on_reference_monopolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        with busy_cursor(self, "Switching to monopolar reference..."):
            perf_start = time.perf_counter()
            self.viewer.set_monopolar_mode()
            self._refresh_display_name_dependent_ui()
            self.btn_edit_bipolar.setEnabled(False)
            self._update_montage_label()
            self.console.log("Reference mode: Monopolar")
            timed_mark(
                "after_reference_change",
                perf_start,
                raw=self.current_raw,
                file_path=self.loaded_file,
                visible_window_s=float(self.time_range.value()),
                reference_mode=self.viewer.reference_mode(),
                notes="monopolar",
            )
 
    def on_reference_average(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        with busy_cursor(self, "Computing average reference..."):
            perf_start = time.perf_counter()
            self.viewer.set_average_mode()
            self._refresh_display_name_dependent_ui()
            self.btn_edit_bipolar.setEnabled(False)
            self._mark_project_dirty()
            self._update_montage_label()
            self.console.log("Reference mode: Average")
            timed_mark(
                "after_reference_change",
                perf_start,
                raw=self.current_raw,
                file_path=self.loaded_file,
                visible_window_s=float(self.time_range.value()),
                reference_mode=self.viewer.reference_mode(),
                notes="average",
            )

    def on_reference_median(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        with busy_cursor(self, "Computing median reference..."):
            perf_start = time.perf_counter()
            self.viewer.set_median_mode()
            self._refresh_display_name_dependent_ui()
            self.btn_edit_bipolar.setEnabled(False)
            self._mark_project_dirty()
            self._update_montage_label()
            self.console.log("Reference mode: Median")
            timed_mark(
                "after_reference_change",
                perf_start,
                raw=self.current_raw,
                file_path=self.loaded_file,
                visible_window_s=float(self.time_range.value()),
                reference_mode=self.viewer.reference_mode(),
                notes="median",
            )

    def on_reference_bipolar(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Re-referencing", "Load a dataset first.")
            return

        channel_names = self.viewer.get_raw_channel_names()
        already_bipolar = [
            name for name in channel_names
            if looks_like_bipolar_derivation_label(name)
        ]
        if already_bipolar:
            examples = ", ".join(already_bipolar[:8])
            extra = "" if len(already_bipolar) <= 8 else f", ... (+{len(already_bipolar) - 8} more)"
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Bipolar montage")
            msg.setText("These channel names already look like bipolar derivations.")
            msg.setInformativeText(
                f"{examples}{extra}\n\n"
                "Applying Bipolar rereferencing anyway may create second-order derivations."
            )
            apply_anyway = msg.addButton("Apply anyway", QMessageBox.ButtonRole.AcceptRole)
            cancel = msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(cancel)
            msg.exec()

            if msg.clickedButton() is not apply_anyway:
                self.console.log(
                    "Bipolar re-referencing cancelled: raw channel labels already look bipolar."
                )
                return

            self.console.log(
                "Bipolar re-referencing confirmed despite already-bipolar-looking labels."
            )

        perf_start = time.perf_counter()
        montage = self._saved_bipolar_montage

        with busy_cursor(self, "Computing bipolar reference..."):
            if montage is None or not montage.pairs:
                bad_channels = self.viewer.get_bad_channels()

                montage = build_automatic_bipolar_montage(
                    channel_names,
                    bad_channels=bad_channels,
                )

            montage = refresh_bipolar_montage_pair_names(montage)

            if montage.pairs:
                self.viewer.set_bipolar_mode(montage)
                self._refresh_display_name_dependent_ui()
                self.btn_edit_bipolar.setEnabled(True)
                self._update_montage_label()
                self.console.log(f"Reference mode: Bipolar ({len(montage.pairs)} pairs)")
                timed_mark(
                    "after_reference_change",
                    perf_start,
                    raw=self.current_raw,
                    file_path=self.loaded_file,
                    visible_window_s=float(self.time_range.value()),
                    reference_mode=self.viewer.reference_mode(),
                    notes=f"bipolar pairs={len(montage.pairs)}",
                )

        if not montage.pairs:
            QMessageBox.warning(
                self,
                "Bipolar montage",
                "No valid bipolar pairs could be generated automatically.",
            )
            return

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

        add_pair_btn = QToolButton(dlg)
        add_pair_btn.setText("Add new pair")

        top_bar.addStretch(1)
        top_bar.addWidget(add_pair_btn)

        layout.addLayout(top_bar)

        table = QTableWidget(0, 4, dlg)
        table.setHorizontalHeaderLabels(["Pair", "Ch1", "Ch2", "Origin"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        layout.addWidget(table)

        row_meta: list[MontageEditorRowMeta] = []
        sort_state: MontageEditorSortState = {
            "column": None,
            "order": Qt.SortOrder.AscendingOrder,
        }

        def _pair_display_name(ch1: str, ch2: str) -> str:
            return bipolar_pair_display_name(ch1, ch2)

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
            source_pair: BipolarPair | None = None,
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

        def _rebuild_table() -> None:
            current_rows: list[MontageEditorCurrentRow] = []

            for row in range(table.rowCount()):
                meta = row_meta[row]

                name_item = table.item(row, 0)
                origin_item = table.item(row, 3)

                ch1_combo = meta["ch1_combo"]
                if ch1_combo is not None:
                    ch1_value = ch1_combo.currentText().strip()
                else:
                    ch1_item = table.item(row, 1)
                    ch1_value = ch1_item.text().strip() if ch1_item is not None else ""

                ch2_combo = meta["ch2_combo"]
                if ch2_combo is not None:
                    ch2_value = ch2_combo.currentText().strip()
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

            sort_column = sort_state["column"]
            sort_order = sort_state["order"]
            reverse = sort_order == Qt.SortOrder.DescendingOrder

            if sort_column == 0:
                current_rows.sort(key=lambda r: r["pair_name"].casefold(), reverse=reverse)
            elif sort_column == 3:
                current_rows.sort(
                    key=lambda r: (
                        0 if r["origin_value"].casefold() == "manual" else 1,
                        r["pair_name"].casefold(),
                    ),
                    reverse=reverse,
                )

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

        def _on_header_clicked(column: int) -> None:
            if column not in (0, 3):
                return

            if sort_state["column"] == column:
                sort_state["order"] = (
                    Qt.SortOrder.DescendingOrder
                    if sort_state["order"] == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                sort_state["column"] = column
                sort_state["order"] = Qt.SortOrder.AscendingOrder

            header.setSortIndicatorShown(True)
            header.setSortIndicator(column, sort_state["order"])
            _rebuild_table()

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

            if sort_state["column"] in (0, 3):
                _rebuild_table()

        add_pair_btn.clicked.connect(_add_new_pair_row)
        header.sectionClicked.connect(_on_header_clicked)

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

            if sort_state["column"] in (0, 3):
                _rebuild_table()

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

        with busy_cursor(self, "Applying bipolar pair edits..."):
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

        with busy_cursor(self, f"Computing common reference: {ref_name}..."):
            perf_start = time.perf_counter()
            self.viewer.set_common_reference_mode(ref_name)
            self._refresh_display_name_dependent_ui()
            self.btn_edit_bipolar.setEnabled(False)
            self._mark_project_dirty()
            self._update_montage_label()
            self.console.log(f"Reference mode: Common ({ref_name})")
            timed_mark(
                "after_reference_change",
                perf_start,
                raw=self.current_raw,
                file_path=self.loaded_file,
                visible_window_s=float(self.time_range.value()),
                reference_mode=self.viewer.reference_mode(),
                notes=f"common={ref_name}",
            )

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
        note.setPlaceholderText("Optional note...")

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
        self._resize_visible_dock(self.anno_dock, side_size=320, bottom_size=240)
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

            txt = f"[{a.kind}] {ch_txt}  {a.t_start:.3f}-{a.t_end:.3f}"
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
            self._resize_visible_dock(self.anno_dock, side_size=320, bottom_size=240)
            self.anno_dock.raise_()

    def _on_annotation_item_clicked(self, item: QListWidgetItem):
        anno_id = item.data(Qt.ItemDataRole.UserRole)
        if not anno_id:
            return
        self.viewer.jump_to_annotation(str(anno_id))

    def _on_annotation_list_context_menu(self, pos) -> None:
        clicked_item = self.anno_list.itemAt(pos)
        if clicked_item is not None and clicked_item not in self.anno_list.selectedItems():
            self.anno_list.setCurrentItem(clicked_item)
            clicked_item.setSelected(True)

        items = self.anno_list.selectedItems()
        if not items:
            return

        menu = QMenu(self)
        act_edit = menu.addAction("Edit annotation...")
        act_delete = menu.addAction("Delete selected annotation(s)")

        chosen = menu.exec_(self.anno_list.mapToGlobal(pos))
        if chosen is None:
            return

        ids = []
        for item in items:
            anno_id = item.data(Qt.ItemDataRole.UserRole)
            if anno_id:
                ids.append(str(anno_id))

        if chosen == act_edit:
            if ids:
                self._on_request_edit_annotation(ids[0])
            return

        if chosen != act_delete:
            return

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

        max_time = 0.0
        if self.current_raw is not None and self.current_raw.n_times > 1:
            max_time = float(self.current_raw.times[-1])

        start_spin = QDoubleSpinBox(dlg)
        start_spin.setDecimals(3)
        start_spin.setRange(0.0, max_time if max_time > 0.0 else max(float(a.t_start), float(a.t_end), 1.0))
        start_spin.setSingleStep(0.05)
        start_spin.setValue(float(a.t_start))

        end_spin = QDoubleSpinBox(dlg)
        end_spin.setDecimals(3)
        end_spin.setRange(0.0, max_time if max_time > 0.0 else max(float(a.t_start), float(a.t_end), 1.0))
        end_spin.setSingleStep(0.05)
        end_spin.setValue(float(a.t_end))

        layout.addRow("Type:", combo)
        layout.addRow("Start (s):", start_spin)
        layout.addRow("End (s):", end_spin)
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

        start_s = float(start_spin.value())
        end_s = float(end_spin.value())
        if end_s < start_s:
            start_s, end_s = end_s, start_s

        self.viewer.update_annotation(
            anno_id,
            kind=combo.currentText(),
            note=note.text().strip(),
            t_start=start_s,
            t_end=end_s,
        )

    def _on_plot_annotation_selected(self, anno_id: str):
        # Ensure dock is visible when user clicks an annotation
        if self.anno_dock.isHidden():
            self.anno_dock.show()
            self._resize_visible_dock(self.anno_dock, side_size=320, bottom_size=240)

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
    def on_toggle_scalogram_mode(self, checked: bool) -> None:
        if self.current_raw is None:
            if self._act_scalogram is not None:
                self._act_scalogram.blockSignals(True)
                self._act_scalogram.setChecked(False)
                self._act_scalogram.blockSignals(False)
            QMessageBox.information(self, "Scalogram", "Load a dataset first.")
            return

        if checked:
            self.viewer.start_scalogram_selection_mode()
            self.console.log(
                "Scalogram mode: drag on one channel to select a time interval. "
                "The mode resets automatically after opening the window. Esc cancels."
            )
        else:
            self.viewer.stop_scalogram_selection_mode()
            self.console.log("Scalogram mode cancelled.")

    def _on_scalogram_mode_changed(self, active: bool) -> None:
        if self._act_scalogram is None:
            return
        if self._act_scalogram.isChecked() == bool(active):
            return
        self._act_scalogram.blockSignals(True)
        self._act_scalogram.setChecked(bool(active))
        self._act_scalogram.blockSignals(False)

    def _open_scalogram_for_selection(self, abs_idx: int, start_s: float, stop_s: float) -> None:
        segment = self.viewer.get_channel_segment(abs_idx, start_s, stop_s)
        if segment is None:
            QMessageBox.warning(self, "Scalogram", "Unable to extract the selected channel interval.")
            return

        signal_uv, absolute_times = segment
        if absolute_times.size < 2:
            QMessageBox.information(self, "Scalogram", "Select a slightly longer interval.")
            return

        display_names = self.viewer.get_channel_names()
        if not (0 <= int(abs_idx) < len(display_names)):
            return

        rel_times = absolute_times - float(absolute_times[0])
        sampling_rate = float(self.current_raw.info["sfreq"]) if self.current_raw is not None else 0.0
        context_duration = float(rel_times[-1]) if rel_times.size else float(stop_s - start_s)
        if context_duration <= 0.0 and sampling_rate > 0.0:
            context_duration = 1.0 / sampling_rate
        context = build_scalogram_context(
            channel_name=display_names[int(abs_idx)],
            loaded_file=self.loaded_file,
            start_time=float(absolute_times[0]),
            duration=float(context_duration),
            sampling_rate=sampling_rate,
        )
        window = ScalogramViewerWindow(
            context=context,
            signal_uv=signal_uv,
            relative_times_s=rel_times,
            theme=self._display_theme_key,
            parent=self,
        )
        window.destroyed.connect(lambda *_args, w=window: self._discard_scalogram_window(w))
        self._scalogram_windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()
        self.console.log(
            f"Opened scalogram for {context.channel_name} | "
            f"{context.start_time:.3f}s to {context.start_time + context.duration:.3f}s"
        )

    def _discard_scalogram_window(self, window: ScalogramViewerWindow) -> None:
        self._scalogram_windows = [w for w in self._scalogram_windows if w is not window]

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
            QMessageBox.information(self, "PSD", "Load a dataset first.")
            return

        perf_start = time.perf_counter()
        total_s = float(self.current_raw.times[-1]) if self.current_raw.n_times > 1 else 0.0
        dlg = PSDIntervalDialog(total_s, self)

        if self._psd_interval is not None:
            start_s, stop_s = self._psd_interval
            dlg.start_spin.setValue(float(start_s))
            dlg.stop_spin.setValue(float(stop_s))

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start_s, stop_s = dlg.values()
        self._psd_interval = (float(start_s), float(stop_s))

        display_names = list(self.viewer.get_channel_names())
        bad_names = list(getattr(self.viewer, "_bad_channels", set()))
        macro_names, micro_names = self._split_display_channels_for_psd(display_names)

        self.psd_panel.set_psd_context(
            raw=self.current_raw,
            picks=self.current_picks,
            display_names=display_names,
            bad_names=bad_names,
            start_s=float(start_s),
            stop_s=float(stop_s),
            macro_names=macro_names,
            micro_names=micro_names,
            filter_profiles=self.filter_profiles,
        )

        self._set_psd_tab_visible(True)
        self.main_tabs.setCurrentWidget(self.psd_panel)
        timed_mark(
            "after_PSD",
            perf_start,
            raw=self.current_raw,
            file_path=self.loaded_file,
            visible_window_s=float(stop_s) - float(start_s),
            reference_mode=self.viewer.reference_mode(),
            notes=f"interval={float(start_s):.3f}-{float(stop_s):.3f}s",
        )

    def _on_bad_channels_changed(self) -> None:
        self._mark_project_dirty()

        # Saved edited montage may no longer be valid if bad channels changed.
        self._saved_bipolar_montage = None

        # Always keep PSD state in sync if the tab is open, even when it is not current.
        if self._is_psd_tab_open():
            self.psd_panel.update_bad_names(self.viewer.get_bad_channels())

        if self.current_raw is not None:
            self._sync_comp_panel_context()

    # ---------------- Expert Event Grid -------------

    def open_expert_event_grid(self) -> None:
        """Open the Expert Event Grid as a separate window."""
        if self.loaded_file is None:
            QMessageBox.information(
                self,
                "Expert Event Grid",
                "Load an EDF file first."
            )
            return

        if self._expert_event_grid_dialog is None:
            dlg = ExpertEventGridDialog(self)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.destroyed.connect(lambda *_args: setattr(self, "_expert_event_grid_dialog", None))
            dlg.grid.requestJumpToTime.connect(self._jump_viewer_to_event)
            dlg.grid.eventClicked.connect(self._on_expert_event_clicked)
            self._expert_event_grid_dialog = dlg

        self._expert_event_grid_dialog.set_edf_path(self.loaded_file)
        self._expert_event_grid_dialog.grid.set_raw(self.source_raw or self.current_raw)
        self._expert_event_grid_dialog.grid.set_waveform_callback(self._extract_waveform_from_raw)

        if self._expert_event_grid_loaded_file != self.loaded_file:
            auto_path = self._find_expert_hfo_events_path(self.loaded_file)
            if auto_path is not None:
                if self._expert_event_grid_dialog.load_events_for_edf(self.loaded_file, auto_path):
                    self._expert_event_grid_loaded_file = self.loaded_file
                    self.console.log(f"Auto-loaded expert HFO events: {auto_path}")
            else:
                self.console.log("No matching expert HFO events file found automatically.")

        self._expert_event_grid_dialog.show()
        self._expert_event_grid_dialog.raise_()
        self._expert_event_grid_dialog.activateWindow()

    def _find_expert_hfo_manifest(self, raw_path: Path) -> Path | None:
        """Find the expert recording manifest near the BIDS package or known local complement."""
        raw_path = Path(raw_path)
        candidates: list[Path] = []

        for parent in [raw_path.parent, *raw_path.parents]:
            candidates.append(parent / "manifest" / "expert_recording_manifest.csv")
            candidates.append(parent / "updated_dataset" / "manifest" / "expert_recording_manifest.csv")
            if parent.name.lower() == "bids":
                candidates.append(parent.parent / "manifest" / "expert_recording_manifest.csv")
                candidates.append(parent.parent / "updated_dataset" / "manifest" / "expert_recording_manifest.csv")

        complement_root = Path.home() / "Documents" / "omni dataset complement"
        candidates.append(complement_root / "manifest" / "expert_recording_manifest.csv")
        candidates.append(complement_root / "updated_dataset" / "manifest" / "expert_recording_manifest.csv")

        seen: set[Path] = set()
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                resolved = candidate.expanduser()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists():
                return resolved

        return None

    def _paths_match_manifest_entry(self, manifest_path: Path, raw_path: Path, entry_path: str) -> bool:
        if not entry_path:
            return False

        raw_resolved = raw_path.expanduser().resolve()
        entry = Path(entry_path)
        entry_candidates = [entry]
        if not entry.is_absolute():
            entry_candidates.append(manifest_path.parent.parent / entry)

        for candidate in entry_candidates:
            try:
                if candidate.expanduser().resolve() == raw_resolved:
                    return True
            except OSError:
                pass

        raw_parts = tuple(p.lower() for p in raw_path.parts)
        entry_parts = tuple(p.lower() for p in entry.parts)
        if "bids" in raw_parts and "bids" in entry_parts:
            raw_bids = raw_parts[raw_parts.index("bids"):]
            entry_bids = entry_parts[entry_parts.index("bids"):]
            if raw_bids == entry_bids:
                return True

        return bool(entry_parts) and len(entry_parts) <= len(raw_parts) and raw_parts[-len(entry_parts):] == entry_parts

    def _resolve_manifest_annotation_path(self, manifest_path: Path, annotation_path: str) -> Path | None:
        if not annotation_path:
            return None

        path = Path(annotation_path)
        candidates = [path]
        package_root = manifest_path.parent.parent
        if not path.is_absolute():
            candidates.append(package_root / path)
        elif package_root.name.lower() == "updated_dataset":
            parts_lower = [part.lower() for part in path.parts]
            try:
                complement_idx = parts_lower.index("omni dataset complement")
            except ValueError:
                complement_idx = -1
            if complement_idx >= 0 and complement_idx + 1 < len(path.parts):
                suffix = Path(*path.parts[complement_idx + 1:])
                candidates.append(package_root / suffix)

        for candidate in candidates:
            resolved = candidate.expanduser()
            if resolved.exists():
                return resolved

        return None

    def _find_expert_hfo_events_path(self, raw_path: Path) -> Path | None:
        raw_path = Path(raw_path)
        manifest_path = self._find_expert_hfo_manifest(raw_path)

        if manifest_path is not None:
            try:
                with manifest_path.open("r", newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        edf_entries = [
                            (row.get("package_bids_edf_path") or "").strip(),
                            (row.get("local_raw_edf_path") or "").strip(),
                        ]
                        if not any(
                            self._paths_match_manifest_entry(manifest_path, raw_path, edf_entry)
                            for edf_entry in edf_entries
                        ):
                            continue

                        annotation_path = self._resolve_manifest_annotation_path(
                            manifest_path,
                            (row.get("package_annotation_path") or "").strip(),
                        )
                        match_status = (row.get("match_status") or "").strip()
                        if annotation_path is not None:
                            if match_status:
                                self.console.log(f"Expert HFO manifest match: {match_status}")
                            return annotation_path
                        if match_status:
                            self.console.log(
                                f"Expert HFO manifest row matched, but annotation file was not found ({match_status})."
                            )
                        return None
            except Exception as e:
                self.console.log(f"Could not read expert HFO manifest: {e}")

        return self._find_expert_hfo_events_by_convention(raw_path)

    def _find_expert_hfo_events_by_convention(self, raw_path: Path) -> Path | None:
        raw_path = Path(raw_path)
        parts_lower = [part.lower() for part in raw_path.parts]
        try:
            bids_idx = parts_lower.index("bids")
        except ValueError:
            return None

        package_root = Path(*raw_path.parts[:bids_idx])
        relative_to_bids = Path(*raw_path.parts[bids_idx + 1:])
        expected = (
            package_root
            / "derivatives"
            / "expert_hfo"
            / relative_to_bids.with_name(f"{raw_path.stem}_expert_hfo_events.csv")
        )
        return expected if expected.exists() else None

    def _channel_match_key(self, label: str) -> str:
        text = str(label or "").strip()
        if not text:
            return ""
        if looks_like_bipolar_derivation_label(text):
            return re.sub(r"\s+", "", text).casefold()
        core = extract_core_contact_label(text)
        if core:
            return core.casefold()
        return re.sub(r"\s+", "", text).casefold()

    def _find_channel_index_by_label(self, names: list[str], channel: str) -> int | None:
        if not channel:
            return None
        if channel in names:
            return names.index(channel)

        stripped_channel = str(channel).strip()
        for idx, name in enumerate(names):
            if str(name).strip() == stripped_channel:
                return idx

        channel_key = self._channel_match_key(stripped_channel)
        if not channel_key:
            return None
        for idx, name in enumerate(names):
            if self._channel_match_key(name) == channel_key:
                return idx
        return None

    def _split_bipolar_event_label(self, channel: str) -> tuple[str, str] | None:
        text = str(channel or "").strip()
        if not looks_like_bipolar_derivation_label(text):
            return None
        if text.upper().startswith("EEG "):
            text = text[4:].strip()
        parts = [part.strip() for part in re.split(r"\s*(?:-|\u2013|\u2014)\s*", text) if part.strip()]
        if len(parts) != 2:
            return None
        return parts[0], parts[1]

    def _find_display_channel_index_for_expert_channel(self, channel: str) -> int | None:
        display_names = self.viewer.get_channel_names()
        idx = self._find_channel_index_by_label(display_names, channel)
        if idx is not None:
            return idx

        # If the expert event is a bipolar derivation but the main display is
        # monopolar, select the first source contact so the jump still lands nearby.
        pair = self._split_bipolar_event_label(channel)
        if pair is None:
            return None
        return self._find_channel_index_by_label(display_names, pair[0])

    def _resolve_raw_waveform_channels(self, raw: BaseRaw, channel: str) -> tuple[int, int | None] | None:
        raw_names = list(raw.ch_names)
        idx = self._find_channel_index_by_label(raw_names, channel)
        if idx is not None:
            return idx, None

        pair = self._split_bipolar_event_label(channel)
        if pair is None:
            return None

        left_idx = self._find_channel_index_by_label(raw_names, pair[0])
        right_idx = self._find_channel_index_by_label(raw_names, pair[1])
        if left_idx is None or right_idx is None:
            return None
        return left_idx, right_idx

    def _jump_viewer_to_event(self, time_s: float, channel: str) -> None:
        """
        Jump the main viewer to a specific time and optionally highlight a channel.

        Args:
            time_s: Time in seconds to jump to
            channel: Channel name to highlight (optional)
        """
        if self.current_raw is None:
            return

        view_range = float(getattr(self.viewer, "_time_range", 0.0) or 0.0)
        target_t0 = max(0.0, float(time_s) - 0.5 * view_range)
        self.viewer.set_time_start(target_t0)

        idx = self._find_display_channel_index_for_expert_channel(channel)
        if idx is not None:
            self.viewer.set_channel_start(max(0, idx - 5))
            self.viewer.set_selected_abs([idx], anchor=idx, emit=True)

        # Update time controls
        self.time_ctl.set_t0(self.viewer.time_start())

    def _on_expert_event_clicked(self, event) -> None:
        """Handle when an expert event is clicked in the grid."""
        # The requestJumpToTime signal already handles jumping to the event
        # This is for any additional handling if needed
        self.console.log(f"Event clicked: {event.channel} at {event.start:.3f}s")

    def load_expert_events(self, events_path: Path) -> bool:
        """
        Load expert events from a file for the currently loaded EDF.

        Args:
            events_path: Path to the events CSV/JSON file

        Returns:
            True if events loaded successfully
        """
        if self.loaded_file is None:
            QMessageBox.warning(
                self,
                "No EDF Loaded",
                "Please load an EDF file before loading events."
            )
            return False

        if self._expert_event_grid_dialog is None:
            self.open_expert_event_grid()
            if self._expert_event_grid_dialog is None:
                return False

        self._expert_event_grid_dialog.grid.set_raw(self.source_raw or self.current_raw)
        self._expert_event_grid_dialog.grid.set_waveform_callback(self._extract_waveform_from_raw)
        success = self._expert_event_grid_dialog.load_events_for_edf(self.loaded_file, events_path)

        if success and self._expert_event_grid_dialog.grid.events:
            self._expert_event_grid_loaded_file = self.loaded_file
            self._expert_event_grid_dialog.show()
            self.console.log(
                f"Loaded {len(self._expert_event_grid_dialog.grid.events)} expert events"
            )

        return success

    def _extract_waveform_from_raw(self, channel: str, start_s: float, end_s: float) -> tuple[np.ndarray, np.ndarray]:
        """
        Extract waveform data from the raw file for display in the event grid.

        Args:
            channel: Channel name
            start_s: Start time in seconds
            end_s: End time in seconds

        Returns:
            Tuple of waveform data and exact sample times.
        """
        raw = self.source_raw or self.current_raw
        if raw is None:
            return np.array([]), np.array([])

        try:
            resolved = self._resolve_raw_waveform_channels(raw, channel)
            if resolved is None:
                self.console.log(f"Expert event waveform: channel not found in raw data ({channel})")
                return np.array([]), np.array([])

            left_idx, right_idx = resolved
            sfreq = float(raw.info["sfreq"])
            start_idx = int(np.floor(max(0.0, float(start_s)) * sfreq))
            end_idx = int(np.ceil(max(float(end_s), float(start_s)) * sfreq))

            # Clamp to valid range
            start_idx = max(0, start_idx)
            end_idx = min(raw.n_times, end_idx)

            if end_idx <= start_idx:
                return np.array([]), np.array([])

            picks = [left_idx] if right_idx is None else [left_idx, right_idx]
            data = raw.get_data(picks=picks, start=start_idx, stop=end_idx)
            times = np.asarray(raw.times[start_idx:end_idx], dtype=float)
            if right_idx is None:
                waveform = data[0]
            else:
                waveform = data[0] - data[1]
            return np.asarray(waveform, dtype=float).reshape(-1), np.asarray(times, dtype=float).reshape(-1)

        except Exception as e:
            self.console.log(f"Error extracting waveform: {e}")
            return np.array([]), np.array([])

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

        self.psd_panel.update_bad_names(current_bad)
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

        self.psd_panel.update_bad_names(current_bad)

        self.console.log(f"Unmarked as bad: {', '.join(removed)}")

# ---------------- Filters  -------------
    def on_toggle_permanent_filters(self) -> None:
        if self.current_raw is None:
            QMessageBox.information(self, "Display filters", "Load a dataset first.")
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

    def _ei_notch_modes_by_group(self) -> dict[str, str]:
        return {
            "macro": str(self.filter_profiles.macro.notch_mode),
            "micro": str(self.filter_profiles.micro.notch_mode),
        }

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

 
