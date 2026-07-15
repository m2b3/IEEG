# app/computation/panel.py
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Literal, Optional, TypedDict, cast

import numpy as np
import pyqtgraph as pg
from scipy import signal
from mne.io import BaseRaw

from PySide6.QtCore import Qt, Slot, Signal, QRectF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog,
    QDialogButtonBox, QLineEdit, QSizePolicy, QButtonGroup,
    QFormLayout, QFrame, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QComboBox, QSpinBox, QGridLayout, QScrollArea,
)

from app.viewer.time_controls import TimeWindowControl
from app.computation.rei.algorithm import (
    EIChannelResult,
    EIComputationResult,
    compute_ei_for_gui,
    validate_gui_ei_timing,
)
from app.computation.gamma_spike.wire_algorithm import (
    GammaSpikeComputationResult,
    GammaSpikeEventResult,
    compute_gamma_spike_for_gui,
)
from app.diagnostics.performance_monitor import timed_mark


@dataclass
class PanelState:
    selected_abs: list[int]
    t0: float
    win: float
    link_time: bool = True
    algorithm: str = "ei"
    seizure_onset_s: float | None = None
    seizure_offset_s: float | None = None
    baseline_start_s: float = 0.0
    baseline_end_s: float = 0.0
    ictal_start_s: float = 0.0
    ictal_end_s: float = 0.0
    gamma_start_s: float | None = None
    gamma_end_s: float | None = None


@dataclass
class EIHeatmapRow:
    original_idx: int
    channel_name: str
    ei_score: float
    recruitment_delay: float
    peak_hfer: float
    mean_hfer: float


class GammaReviewRow(TypedDict):
    channel: str
    event_index: int
    event_number: int
    spike_label: str
    time_s: float
    event_start_time_s: float
    event_stop_time_s: float
    is_gamma: bool
    gamma_power: float | None
    gamma_frequency_hz: float | None
    gamma_duration_ms: float | None
    boundary_p1_time_s: float | None
    boundary_n1_time_s: float | None
    boundary_n2_time_s: float | None
    gamma_start_time_s: float | None
    gamma_stop_time_s: float | None
    error: str | None


class GammaReviewState(TypedDict):
    rows: list[GammaReviewRow]
    index: int
    current_page: int
    is_zoomed: bool


class GammaSummaryRow(TypedDict):
    channel: str
    channel_sort: str
    total_spikes: int
    gamma_spikes: int
    spike_gamma_rate: float
    spike_gamma_rate_text: str
    mean_gamma_power: float
    mean_gamma_power_text: str
    mean_gamma_duration: float
    mean_gamma_duration_text: str


class GammaSummarySortState(TypedDict):
    column: int
    order: Qt.SortOrder


class EISummaryRow(TypedDict):
    original_order: int
    display_order: int
    channel: str
    channel_sort: str
    ei_score: float
    rank: int
    hfer_activity: float
    hfer_activity_text: str
    recruitment_delay: float
    recruitment_delay_text: str


class EISummarySortState(TypedDict):
    column: int
    order: Qt.SortOrder
    channel_mode: Literal["display", "alphabetical"]


class _GammaSpikeCardFrame(QFrame):
    clicked = Signal(int)

    def __init__(self, event_index: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._event_index = int(event_index)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._event_index)
            event.accept()
            return
        super().mousePressEvent(event)


class ComputationPanel(QWidget):
    """
    Dock content widget:
      - editable channel selection (absolute indices from displayed channel list)
      - time controls (linked/unlinked to main window time)
      - computation setup for REI and gamma spike detection
    """

    panelSelectionChanged = Signal(list)  # absolute channel indices
    settingsChanged = Signal()
    seizureMarkersChanged = Signal(object, object)  # onset_s, offset_s
    seizureMarkerEdited = Signal(str, object)  # "onset" | "offset", value_s
    gammaAnalysisWindowChanged = Signal(object, object)  # start_s, end_s
    recruitmentMarkersChanged = Signal(dict)  # display channel name -> absolute time_s
    eiScoreLabelsChanged = Signal(dict)  # display channel name -> {score_norm, rank}
    eiSummaryChannelActivated = Signal(str)
    eiSummaryOrderChanged = Signal(list)
    gammaSpikeMarkersChanged = Signal(dict)  # display channel name -> [{time_s, kind}]
    gammaSpikeEventActivated = Signal(str, float)  # channel name, absolute time_s

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(240, 220)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self._raw: BaseRaw | None = None
        self._picks: np.ndarray | None = None           # abs_idx -> raw_idx
        self._ch_names_displayed: list[str] = []        # abs_idx -> display name
        self._channel_groups: dict[str, str] = {}   # display_name -> "macro" | "micro"
        self._bad_names: set[str] = set()
        self._current_montage_callback: Callable[[], str] | None = None
        self._switch_to_bipolar_callback: Callable[[], tuple[bool, str]] | None = None
        self._ei_filter_callback: Callable[[], dict[str, str]] | None = None
        self._ei_data_callback: Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]] | None = None
        self.ei_result_metadata: dict | None = None
        self._last_ei_result: EIComputationResult | None = None
        self._ei_summary_dialog: QDialog | None = None
        self._ei_heatmap_dialog: QDialog | None = None
        self._ei_summary_table: QTableWidget | None = None
        self._ei_summary_row_by_channel: dict[str, int] = {}
        self._last_gamma_result: GammaSpikeComputationResult | None = None
        self._gamma_summary_dialog: QDialog | None = None
        self._gamma_review_dialog: QDialog | None = None
        self._pending_gamma_review_selection: tuple[str, float] | None = None

        self.state = PanelState(selected_abs=[], t0=0.0, win=5.0, link_time=True)
        self._gamma_default_window_applied = False
        self.ei_params = {
            "expected_reference": "raw_or_bipolar",
            "exclude_bad_channels": True,
            "use_display_filter": False,
            "analysis_filter": "butterworth_bandpass",
            "filter_order": 4,
            "low_freq": 70.0,
            "high_freq": 140.0,
            "zero_phase": True,
            "notch_filter": False,
            "line_freq": 60.0,
            "threshold_sigma": 10.0,
            "energy_window_sec": 0.5,
            "hfer_window_sec": 0.25,
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- Algorithm selector ---
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))

        self.algo_buttons = QButtonGroup(self)
        self.algo_buttons.setExclusive(True)

        self.btn_algo_ei = QPushButton("REI")
        self.btn_algo_ei.setCheckable(True)
        self.btn_algo_ei.setChecked(True)
        self.btn_algo_ei.setProperty("algorithm", "ei")

        self.btn_algo_gamma = QPushButton("Gamma spikes")
        self.btn_algo_gamma.setCheckable(True)
        self.btn_algo_gamma.setProperty("algorithm", "gamma_spike")

        self.algo_buttons.addButton(self.btn_algo_ei)
        self.algo_buttons.addButton(self.btn_algo_gamma)
        algo_row.addWidget(self.btn_algo_ei)
        algo_row.addWidget(self.btn_algo_gamma)
        algo_row.addStretch(1)
        root.addLayout(algo_row)

        # --- Channel selector ---
        self.gb_ch = QGroupBox("Channels")
        self.gb_ch.setCheckable(True)
        self.gb_ch.setChecked(True)
        ch_layout = QVBoxLayout(self.gb_ch)

        self.list_channels = QListWidget()
        self.list_channels.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_channels.setMinimumHeight(80)
        self.list_channels.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        ch_layout.addWidget(self.list_channels, 1)

        quick_row = QHBoxLayout()
        self.btn_sel_all = QPushButton("All")
        self.btn_sel_macro = QPushButton("Macro")
        self.btn_sel_micro = QPushButton("Micro")

        quick_row.addWidget(self.btn_sel_all)
        quick_row.addWidget(self.btn_sel_macro)
        quick_row.addWidget(self.btn_sel_micro)
        ch_layout.addLayout(quick_row)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add...")
        self.btn_remove = QPushButton("Remove selected")
        self.btn_clear = QPushButton("Clear")
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        btn_row.addWidget(self.btn_clear)
        ch_layout.addLayout(btn_row)

        root.addWidget(self.gb_ch, 2)

        # --- Time controls ---
        self.gb_t = QGroupBox("Time")
        self.gb_t.setCheckable(True)
        self.gb_t.setChecked(True)
        t_layout = QVBoxLayout(self.gb_t)

        self.gamma_time_widget = QWidget()
        gamma_time_layout = QVBoxLayout(self.gamma_time_widget)
        gamma_time_layout.setContentsMargins(0, 0, 0, 0)
        gamma_time_layout.setSpacing(8)

        self.chk_link_time = QCheckBox("Link to main time window")
        self.chk_link_time.setChecked(True)
        self.chk_link_time.hide()

        gamma_form = QFormLayout()
        self.edit_gamma_start = QLineEdit()
        self.edit_gamma_start.setPlaceholderText("seconds")
        self.edit_gamma_end = QLineEdit()
        self.edit_gamma_end.setPlaceholderText("seconds")
        gamma_form.addRow("Analysis start (s):", self.edit_gamma_start)
        gamma_form.addRow("Analysis end (s):", self.edit_gamma_end)
        gamma_time_layout.addLayout(gamma_form)

        info_row = QHBoxLayout()
        self.lbl_t = QLabel("t: [0.00, 5.00] s")
        self.lbl_t.hide()
        info_row.addWidget(self.lbl_t, 1)

        spin_row = QHBoxLayout()
        self.lbl_gamma_window_length = QLabel("Window length (s):")
        self.lbl_gamma_window_length.hide()
        spin_row.addWidget(self.lbl_gamma_window_length)
        self.spin_win = QDoubleSpinBox()
        self.spin_win.setRange(1.0, 1_000_000.0)
        self.spin_win.setSingleStep(0.5)
        self.spin_win.setValue(5.0)
        self.spin_win.hide()
        spin_row.addWidget(self.spin_win)

        self.time_ctl = TimeWindowControl(label_prefix="t0")
        self.time_ctl.set_enabled(False)
        self.time_ctl.hide()

        self.gamma_time_widget.hide()
        t_layout.addWidget(self.gamma_time_widget)

        self.ei_time_widget = QWidget()
        ei_time_layout = QVBoxLayout(self.ei_time_widget)
        ei_time_layout.setContentsMargins(0, 0, 0, 0)
        ei_time_layout.setSpacing(8)

        info_box = QGroupBox("Recruitment Energy Index (REI) setup")
        info_layout = QHBoxLayout(info_box)
        info_layout.addWidget(QLabel("Recommended montage: Bipolar"), 1)
        self.btn_ei_info = QPushButton("i")
        self.btn_ei_info.setFixedSize(22, 22)
        self.btn_ei_info.setToolTip(
            "REI preprocessing: confirmed bad channels are excluded and an internal "
            "70-140 Hz zero-phase Butterworth bandpass filter is applied."
        )
        self.btn_ei_info.setStyleSheet("border-radius: 11px; font-weight: bold;")
        info_layout.addWidget(self.btn_ei_info)
        ei_time_layout.addWidget(info_box)

        seizure_form = QFormLayout()
        self.edit_seizure_onset = QLineEdit()
        self.edit_seizure_onset.setPlaceholderText("seconds")
        self.edit_seizure_offset = QLineEdit()
        self.edit_seizure_offset.setPlaceholderText("seconds")
        seizure_form.addRow("Seizure onset (s):", self.edit_seizure_onset)
        seizure_form.addRow("Seizure offset (s):", self.edit_seizure_offset)
        ei_time_layout.addLayout(seizure_form)

        windows_box = QGroupBox("Windows")
        windows_form = QFormLayout(windows_box)
        self.edit_baseline_start = QDoubleSpinBox()
        self.edit_baseline_end = QDoubleSpinBox()
        self.edit_ictal_start = QDoubleSpinBox()
        self.edit_ictal_end = QDoubleSpinBox()
        for spin in (
            self.edit_baseline_start,
            self.edit_baseline_end,
            self.edit_ictal_start,
            self.edit_ictal_end,
        ):
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setDecimals(3)
            spin.setSingleStep(1.0)
            spin.setSuffix(" s")
        windows_form.addRow("Baseline start:", self.edit_baseline_start)
        windows_form.addRow("Baseline end:", self.edit_baseline_end)
        windows_form.addRow("Ictal start:", self.edit_ictal_start)
        windows_form.addRow("Ictal end:", self.edit_ictal_end)
        self.btn_default_windows = QPushButton("Use default windows")
        windows_form.addRow("", self.btn_default_windows)
        ei_time_layout.addWidget(windows_box)

        self.btn_advanced = QPushButton("Advanced parameters...")
        ei_time_layout.addWidget(self.btn_advanced)

        self.advanced_frame = QFrame()
        advanced_layout = QVBoxLayout(self.advanced_frame)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)

        assumptions_box = QGroupBox("Analysis assumptions")
        assumptions_form = QFormLayout(assumptions_box)
        assumptions_form.addRow("Input data:", QLabel("Raw or bipolar"))
        assumptions_form.addRow("Exclude bad channels:", QLabel("Yes"))
        assumptions_form.addRow("Use display filter:", QLabel("No"))
        advanced_layout.addWidget(assumptions_box)

        preprocessing_box = QGroupBox("REI preprocessing")
        preprocessing_form = QFormLayout(preprocessing_box)
        preprocessing_form.addRow("Analysis filter:", QLabel("Butterworth bandpass"))
        preprocessing_form.addRow("Filter order:", QLabel("4"))
        preprocessing_form.addRow("Bandpass:", QLabel("70-140 Hz"))
        preprocessing_form.addRow("Zero phase:", QLabel("Yes"))
        preprocessing_form.addRow("Notch filter:", QLabel("Uses active notch if enabled"))
        preprocessing_form.addRow("Line frequency:", QLabel("50/60 Hz + harmonics"))
        advanced_layout.addWidget(preprocessing_box)

        params_box = QGroupBox("REI computation")
        params_form = QFormLayout(params_box)
        params_form.addRow("Threshold sigma:", QLabel("10"))
        params_form.addRow("Energy window:", QLabel("0.5 s"))
        params_form.addRow("HFER window:", QLabel("0.25 s"))
        advanced_layout.addWidget(params_box)

        self.advanced_dialog: QDialog | None = None
        self.ei_time_widget.hide()
        t_layout.addWidget(self.ei_time_widget)

        root.addWidget(self.gb_t, 0)

        # --- Output actions ---
        gb_p = QGroupBox("Output")
        p_layout = QVBoxLayout(gb_p)

        self.btn_run = QPushButton("Run REI")
        p_layout.addWidget(self.btn_run)

        self.btn_open_ei_summary = QPushButton("Open REI summary")
        self.btn_open_ei_summary.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_summary)

        self.btn_open_ei_heatmap = QPushButton("Open REI heatmap")
        self.btn_open_ei_heatmap.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_heatmap)

        self.btn_open_gamma_summary = QPushButton("Open channel-level summary")
        self.btn_open_gamma_summary.setEnabled(False)
        self.btn_open_gamma_summary.hide()
        p_layout.addWidget(self.btn_open_gamma_summary)

        self.btn_open_gamma_review = QPushButton("Open spike grid")
        self.btn_open_gamma_review.setEnabled(False)
        self.btn_open_gamma_review.hide()
        p_layout.addWidget(self.btn_open_gamma_review)

        root.addWidget(gb_p, 0)


        # --- Wiring ---
        self.btn_add.clicked.connect(self._open_add_channels_dialog)
        self.btn_remove.clicked.connect(self._remove_selected_items)
        self.btn_clear.clicked.connect(self._clear_channels)

        self.chk_link_time.toggled.connect(self._on_link_time_toggled)
        self.spin_win.valueChanged.connect(self._on_win_changed)
        self.time_ctl.t0Changed.connect(self._on_panel_t0_changed)
        self.edit_gamma_start.textChanged.connect(self._on_gamma_window_text_changed)
        self.edit_gamma_end.textChanged.connect(self._on_gamma_window_text_changed)

        self.algo_buttons.buttonClicked.connect(self._on_algorithm_button_clicked)
        self.btn_advanced.clicked.connect(self._open_advanced_dialog)
        self.btn_default_windows.clicked.connect(self._apply_default_ei_windows_from_onset)
        self.edit_seizure_onset.textChanged.connect(self._on_ei_onset_text_changed)
        self.edit_seizure_offset.textChanged.connect(self._on_ei_offset_text_changed)
        for spin in (
            self.edit_baseline_start,
            self.edit_baseline_end,
            self.edit_ictal_start,
            self.edit_ictal_end,
        ):
            spin.valueChanged.connect(self._sync_ei_windows_from_ui)
        self.btn_run.clicked.connect(self._run_computation)
        self.btn_open_ei_summary.clicked.connect(self._open_ei_summary_dialog)
        self.btn_open_ei_heatmap.clicked.connect(self._open_ei_heatmap_dialog)
        self.btn_open_gamma_summary.clicked.connect(self._open_gamma_summary_dialog)
        self.btn_open_gamma_review.clicked.connect(self._open_gamma_review_dialog)

        self.btn_sel_all.clicked.connect(self._select_all_channels)
        self.btn_sel_macro.clicked.connect(lambda: self._select_group_channels("macro"))
        self.btn_sel_micro.clicked.connect(lambda: self._select_group_channels("micro"))
        self.gb_ch.toggled.connect(self._sync_section_visibility)
        self.gb_t.toggled.connect(self._sync_section_visibility)

        self._on_algorithm_button_clicked(self.btn_algo_ei)

    # ---------- Public API used by MainWindow ----------

    def set_data_context(
        self,
        raw: BaseRaw | None,
        picks: np.ndarray | None,
        displayed_names: list[str],
        channel_groups: dict[str, str] | None = None,
        bad_names: list[str] | set[str] | None = None,
    ) -> None:
        self._raw = raw
        self._picks = picks
        self._ch_names_displayed = list(displayed_names or [])
        self._bad_names = {str(name) for name in (bad_names or [])}

        cleaned_groups: dict[str, str] = {}
        for ch_name in self._ch_names_displayed:
            g = str((channel_groups or {}).get(ch_name, "macro")).strip().lower()
            cleaned_groups[ch_name] = g if g in {"macro", "micro"} else "macro"

        self._channel_groups = cleaned_groups

        # keep only still-valid selections after channel list changes
        self.state.selected_abs = [
            idx for idx in self.state.selected_abs
            if 0 <= idx < len(self._ch_names_displayed) and not self._is_bad_abs_idx(idx)
        ]

        self._refresh_channel_list_titles()
        self._sync_list_widget_from_state()
        self._update_channels_title()
        self._update_group_button_titles()
        if self.state.algorithm == "ei":
            self._clear_ei_outputs()
        if self.state.algorithm == "gamma_spike":
            self._set_gamma_window_to_full_recording(emit=True)
            self._clear_gamma_outputs()
        
    def set_selected_channels_abs(self, selected_abs: list[int], *, replace: bool = True) -> None:
        cleaned = sorted(
            {
                int(i)
                for i in selected_abs
                if int(i) >= 0
                and int(i) < len(self._ch_names_displayed)
                and not self._is_bad_abs_idx(int(i))
            }
        )

        if replace:
            self.state.selected_abs = cleaned
        else:
            self.state.selected_abs = sorted(set(self.state.selected_abs).union(cleaned))

        self._sync_list_widget_from_state()
        self._update_channels_title()
        if self.state.algorithm == "ei":
            self._clear_ei_outputs()
        if self.state.algorithm == "gamma_spike":
            self._clear_gamma_outputs()
        self.panelSelectionChanged.emit(self.state.selected_abs)
        self.settingsChanged.emit()

    def set_main_time(self, t0: float, main_win_s: float) -> None:
        del main_win_s  # kept for API compatibility

        if self.state.algorithm == "gamma_spike":
            return
        if not self.state.link_time:
            return

        self.state.t0 = float(t0)
        self.state.win = max(1.0, float(self.state.win))

        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)

        self._update_slider_range()
        self.time_ctl.set_t0(self.state.t0)
        self._update_time_label()
        if self.state.algorithm == "gamma_spike":
            self._set_gamma_window_from_state(emit=True)

    def set_main_gain_uv(self, gain_uv: float) -> None:
        del gain_uv  # kept for API compatibility; no local mean preview is shown.

    def set_ei_montage_callbacks(
        self,
        *,
        current_montage: Callable[[], str],
        switch_to_bipolar: Callable[[], tuple[bool, str]],
    ) -> None:
        self._current_montage_callback = current_montage
        self._switch_to_bipolar_callback = switch_to_bipolar

    def set_ei_filter_callback(
        self,
        callback: Callable[[], dict[str, str]],
    ) -> None:
        self._ei_filter_callback = callback

    def set_ei_data_callback(
        self,
        callback: Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]],
    ) -> None:
        self._ei_data_callback = callback

    def project_state(self) -> dict:
        self._sync_ei_windows_from_ui(emit=False)
        return {
            "algorithm": self.state.algorithm,
            "selected_abs": list(self.state.selected_abs),
            "time": {
                "t0": float(self.state.t0),
                "window_s": float(self.state.win),
                "link_time": bool(self.state.link_time),
            },
            "ei": {
                "seizure_onset_s": self._parse_float_text(self.edit_seizure_onset),
                "seizure_offset_s": self._parse_float_text(self.edit_seizure_offset),
                "baseline_start_s": float(self.edit_baseline_start.value()),
                "baseline_end_s": float(self.edit_baseline_end.value()),
                "ictal_start_s": float(self.edit_ictal_start.value()),
                "ictal_end_s": float(self.edit_ictal_end.value()),
                "params": dict(self.ei_params),
                "last_result_metadata": self.ei_result_metadata,
            },
            "gamma_spike": {
                "analysis_start_s": self._parse_float_text(self.edit_gamma_start),
                "analysis_end_s": self._parse_float_text(self.edit_gamma_end),
            },
        }

    def restore_project_state(self, data: dict | None) -> None:
        if not isinstance(data, dict):
            return

        time_settings = data.get("time", data.get("mean", {}))
        if not isinstance(time_settings, dict):
            time_settings = {}
        ei = data.get("ei", {})
        if not isinstance(ei, dict):
            ei = {}
        gamma = data.get("gamma_spike", {})
        if not isinstance(gamma, dict):
            gamma = {}
        selected_abs = data.get("selected_abs", [])
        if isinstance(selected_abs, list):
            cleaned_abs = []
            for value in selected_abs:
                try:
                    cleaned_abs.append(int(value))
                except (TypeError, ValueError):
                    continue
            self.set_selected_channels_abs(cleaned_abs, replace=True)

        self.state.t0 = float(time_settings.get("t0", self.state.t0) or 0.0)
        self.state.win = max(
            1.0,
            float(time_settings.get("window_s", self.state.win) or self.state.win),
        )
        self.state.link_time = bool(time_settings.get("link_time", self.state.link_time))

        self.chk_link_time.blockSignals(True)
        self.chk_link_time.setChecked(self.state.link_time)
        self.chk_link_time.blockSignals(False)
        self.time_ctl.set_enabled(not self.state.link_time)

        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)

        def _set_line_edit_float(edit: QLineEdit, value) -> None:
            edit.blockSignals(True)
            edit.setText("" if value is None else f"{float(value):g}")
            edit.blockSignals(False)

        for edit, value in (
            (self.edit_seizure_onset, ei.get("seizure_onset_s")),
            (self.edit_seizure_offset, ei.get("seizure_offset_s")),
        ):
            try:
                _set_line_edit_float(edit, value)
            except (TypeError, ValueError):
                _set_line_edit_float(edit, None)

        for edit, value in (
            (self.edit_gamma_start, gamma.get("analysis_start_s", 0.0)),
            (
                self.edit_gamma_end,
                gamma.get(
                    "analysis_end_s",
                    self._total_duration_s(),
                ),
            ),
        ):
            try:
                _set_line_edit_float(edit, value)
            except (TypeError, ValueError):
                _set_line_edit_float(edit, None)

        for spin, key in (
            (self.edit_baseline_start, "baseline_start_s"),
            (self.edit_baseline_end, "baseline_end_s"),
            (self.edit_ictal_start, "ictal_start_s"),
            (self.edit_ictal_end, "ictal_end_s"),
        ):
            value = ei.get(key)
            if isinstance(value, (int, float)):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)

        self.state.seizure_onset_s = self._parse_float_text(self.edit_seizure_onset)
        self.state.seizure_offset_s = self._parse_float_text(self.edit_seizure_offset)
        self.seizureMarkersChanged.emit(
            self.state.seizure_onset_s,
            self.state.seizure_offset_s,
        )
        self.state.gamma_start_s = self._parse_float_text(self.edit_gamma_start)
        self.state.gamma_end_s = self._parse_float_text(self.edit_gamma_end)
        self.gammaAnalysisWindowChanged.emit(
            self.state.gamma_start_s,
            self.state.gamma_end_s,
        )
        self._sync_ei_windows_from_ui(emit=False)
        saved_metadata = ei.get("last_result_metadata")
        self.ei_result_metadata = saved_metadata if isinstance(saved_metadata, dict) else None

        algorithm = str(data.get("algorithm", self.state.algorithm) or "ei")
        if algorithm == "mean":
            algorithm = "ei"
        button_by_algorithm = {
            "ei": self.btn_algo_ei,
            "gamma_spike": self.btn_algo_gamma,
        }
        button = button_by_algorithm.get(algorithm, self.btn_algo_ei)
        button.setChecked(True)
        self._on_algorithm_button_clicked(button)

    # ---------- Internals : channel name mapping ----------

    def _abs_to_display_name(self, abs_idx: int) -> str:
        if 0 <= abs_idx < len(self._ch_names_displayed):
            return self._ch_names_displayed[abs_idx]
        return f"ch[{abs_idx}]"

    def _is_bad_abs_idx(self, abs_idx: int) -> bool:
        return self._abs_to_display_name(abs_idx) in self._bad_names

    def _available_channel_abs(self) -> list[int]:
        return [
            abs_idx
            for abs_idx in range(len(self._ch_names_displayed))
            if not self._is_bad_abs_idx(abs_idx)
        ]

    def _refresh_channel_list_titles(self) -> None:
        for row in range(self.list_channels.count()):
            item = self.list_channels.item(row)
            abs_idx = int(item.data(Qt.ItemDataRole.UserRole))
            item.setText(self._abs_to_display_name(abs_idx))

    def _sync_list_widget_from_state(self) -> None:
        self.list_channels.blockSignals(True)
        self.list_channels.clear()

        for abs_idx in self.state.selected_abs:
            item = QListWidgetItem(self._abs_to_display_name(abs_idx))
            item.setData(Qt.ItemDataRole.UserRole, int(abs_idx))
            self.list_channels.addItem(item)

        self.list_channels.blockSignals(False)

    # ---------- Internals : channel selection ----------

    def _remove_selected_items(self) -> None:
        to_remove = {
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.list_channels.selectedItems()
        }
        if not to_remove:
            return

        remaining = [idx for idx in self.state.selected_abs if idx not in to_remove]
        self.set_selected_channels_abs(remaining, replace=True)

    def _clear_channels(self) -> None:
        self.set_selected_channels_abs([], replace=True)

    def _open_add_channels_dialog(self) -> None:
        """Open a searchable multi-select dialog listing all displayed channels."""
        available_abs = self._available_channel_abs()
        if not available_abs:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Add channels")
        dlg.setModal(True)
        dlg.resize(420, 520)

        layout = QVBoxLayout(dlg)

        search = QLineEdit()
        search.setPlaceholderText("Search channels...")
        layout.addWidget(search)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(lst, 1)

        for abs_idx in available_abs:
            item = QListWidgetItem(self._abs_to_display_name(abs_idx))
            item.setData(Qt.ItemDataRole.UserRole, int(abs_idx))
            lst.addItem(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)

        def apply_filter(text: str) -> None:
            text = (text or "").strip().lower()
            for i in range(lst.count()):
                item = lst.item(i)
                item.setHidden(text not in item.text().lower())

        search.textChanged.connect(apply_filter)
        search.returnPressed.connect(
            lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).click()
        )

        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        current = set(self.state.selected_abs)
        for i in range(lst.count()):
            item = lst.item(i)
            abs_idx = int(item.data(Qt.ItemDataRole.UserRole))
            if abs_idx in current:
                item.setSelected(True)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen_abs = [
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in lst.selectedItems()
        ]
        if not chosen_abs:
            return

        self.set_selected_channels_abs(chosen_abs, replace=False)

    # ---------- Internals : time controls ----------

    @Slot(bool)
    def _on_link_time_toggled(self, on: bool) -> None:
        self.state.link_time = bool(on)
        self.time_ctl.set_enabled(not self.state.link_time)
        self.settingsChanged.emit()

    @Slot(float)
    def _on_win_changed(self, value: float) -> None:
        self.state.win = max(1.0, float(value))
        self._update_time_label()
        if self.state.algorithm == "gamma_spike":
            self._set_gamma_window_from_state(emit=True)

        if self._raw is not None and self._raw.n_times > 1:
            total_s = float(self._raw.times[-1])
            self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

        self.settingsChanged.emit()

    def _update_slider_range(self) -> None:
        if self._raw is None or self._raw.n_times <= 1:
            self.time_ctl.set_range(0.0, 0.0, 0.0)
            return

        total_s = float(self._raw.times[-1])
        self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

    def _update_time_label(self) -> None:
        t0 = self.state.t0
        t1 = t0 + self.state.win
        self.lbl_t.setText(f"t: [{t0:.2f}, {t1:.2f}] s  (win={self.state.win:.1f}s)")

    @Slot(float)
    def _on_panel_t0_changed(self, t0: float) -> None:
        if self.state.link_time:
            return
        self.state.t0 = float(t0)
        self._update_time_label()
        if self.state.algorithm == "gamma_spike":
            self._set_gamma_window_from_state(emit=True)
        self.settingsChanged.emit()

    def _set_gamma_window_from_state(self, *, emit: bool = True) -> None:
        start_s = float(self.state.t0)
        end_s = float(self.state.t0) + float(self.state.win)
        self._set_gamma_window_fields(start_s, end_s, emit=emit)

    def _set_gamma_window_to_full_recording(self, *, emit: bool = True) -> None:
        total_s = self._total_duration_s()
        if total_s is None:
            self._set_gamma_window_fields(0.0, max(1.0, float(self.state.win)), emit=emit)
            return
        self.state.t0 = 0.0
        self.state.win = max(1.0, float(total_s))
        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)
        self._set_gamma_window_fields(0.0, float(total_s), emit=emit)

    def _set_gamma_window_fields(self, start_s: float, end_s: float, *, emit: bool = True) -> None:
        self.edit_gamma_start.blockSignals(True)
        self.edit_gamma_start.setText(f"{start_s:g}")
        self.edit_gamma_start.blockSignals(False)
        self.edit_gamma_end.blockSignals(True)
        self.edit_gamma_end.setText(f"{end_s:g}")
        self.edit_gamma_end.blockSignals(False)
        self.state.gamma_start_s = start_s
        self.state.gamma_end_s = end_s
        if emit:
            self.gammaAnalysisWindowChanged.emit(start_s, end_s)

    def _on_gamma_window_text_changed(self, _text: str) -> None:
        self.state.gamma_start_s = self._parse_float_text(self.edit_gamma_start)
        self.state.gamma_end_s = self._parse_float_text(self.edit_gamma_end)
        self._clear_gamma_outputs()
        self.gammaAnalysisWindowChanged.emit(
            self.state.gamma_start_s,
            self.state.gamma_end_s,
        )
        self.settingsChanged.emit()

    def _read_gamma_window_from_ui(self) -> tuple[float, float]:
        start_s = self._parse_float_text(self.edit_gamma_start)
        end_s = self._parse_float_text(self.edit_gamma_end)
        if start_s is None:
            raise ValueError("Enter a valid gamma analysis start time in seconds.")
        if end_s is None:
            raise ValueError("Enter a valid gamma analysis end time in seconds.")
        if end_s <= start_s:
            raise ValueError("Gamma analysis end must be after analysis start.")
        total_s = self._total_duration_s()
        if total_s is not None:
            if start_s < 0.0 or end_s > total_s:
                raise ValueError("Gamma analysis window must stay inside the recording.")
        self.state.gamma_start_s = float(start_s)
        self.state.gamma_end_s = float(end_s)
        return float(start_s), float(end_s)

    # ---------- Internals : EI controls ----------

    def _parse_float_text(self, edit: QLineEdit) -> float | None:
        text = edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _on_algorithm_button_clicked(self, button=None) -> None:
        if button is None or not hasattr(button, "property"):
            button = self.algo_buttons.checkedButton()
        if button is None:
            return

        algorithm = str(button.property("algorithm") or "ei")
        self.state.algorithm = algorithm
        is_ei = algorithm == "ei"
        is_gamma = algorithm == "gamma_spike"

        self.gamma_time_widget.setVisible(self.gb_t.isChecked() and is_gamma)
        self.ei_time_widget.setVisible(self.gb_t.isChecked() and is_ei)

        self.btn_open_ei_summary.setVisible(is_ei)
        self.btn_open_ei_heatmap.setVisible(is_ei)
        self.btn_open_gamma_summary.setVisible(is_gamma)
        self.btn_open_gamma_review.setVisible(is_gamma)

        if is_ei:
            self.btn_run.setText("Run REI")
        elif is_gamma:
            self.btn_run.setText("Run Gamma Spike Detector")
            if (
                not self._gamma_default_window_applied
                or self._parse_float_text(self.edit_gamma_start) is None
                or self._parse_float_text(self.edit_gamma_end) is None
            ):
                self._set_gamma_window_to_full_recording(emit=True)
                self._gamma_default_window_applied = True
            else:
                self._on_gamma_window_text_changed("")
        else:
            self.btn_run.setText("Run")

        if is_ei:
            self._clear_ei_outputs()
        if is_gamma:
            self._clear_gamma_outputs()
        if not is_gamma:
            self.gammaAnalysisWindowChanged.emit(None, None)
        self.settingsChanged.emit()

    def _open_advanced_dialog(self) -> None:
        if self.advanced_dialog is None:
            self.advanced_dialog = QDialog(self)
            self.advanced_dialog.setWindowTitle("REI advanced parameters")
            self.advanced_dialog.setModal(False)
            self.advanced_dialog.resize(420, 520)

            layout = QVBoxLayout(self.advanced_dialog)
            layout.addWidget(self.advanced_frame)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.advanced_dialog.hide)
            layout.addWidget(buttons)

        self.advanced_dialog.show()
        self.advanced_dialog.raise_()
        self.advanced_dialog.activateWindow()

    def _sync_section_visibility(self, _checked: bool = True) -> None:
        channel_visible = self.gb_ch.isChecked()
        for widget in (
            self.list_channels,
            self.btn_sel_all,
            self.btn_sel_macro,
            self.btn_sel_micro,
            self.btn_add,
            self.btn_remove,
            self.btn_clear,
        ):
            widget.setVisible(channel_visible)

        time_visible = self.gb_t.isChecked()
        is_ei = self.state.algorithm == "ei"
        is_gamma = self.state.algorithm == "gamma_spike"
        self.gamma_time_widget.setVisible(time_visible and is_gamma)
        self.ei_time_widget.setVisible(time_visible and is_ei)

    def _on_ei_onset_text_changed(self, _text: str) -> None:
        self.state.seizure_onset_s = self._parse_float_text(self.edit_seizure_onset)
        self._clear_ei_outputs()
        self.seizureMarkersChanged.emit(
            self.state.seizure_onset_s,
            self.state.seizure_offset_s,
        )
        self.seizureMarkerEdited.emit("onset", self.state.seizure_onset_s)
        self._apply_default_ei_windows_from_onset()
        self.settingsChanged.emit()

    def _on_ei_offset_text_changed(self, _text: str) -> None:
        self.state.seizure_offset_s = self._parse_float_text(self.edit_seizure_offset)
        self._clear_ei_outputs()
        self.seizureMarkersChanged.emit(
            self.state.seizure_onset_s,
            self.state.seizure_offset_s,
        )
        self.seizureMarkerEdited.emit("offset", self.state.seizure_offset_s)
        self.settingsChanged.emit()

    def _apply_default_ei_windows_from_onset(self) -> None:
        onset = self._parse_float_text(self.edit_seizure_onset)
        if onset is None:
            return

        defaults = (
            (self.edit_baseline_start, onset - 70.0),
            (self.edit_baseline_end, onset - 10.0),
            (self.edit_ictal_start, onset - 5.0),
            (self.edit_ictal_end, onset + 20.0),
        )
        for spin, value in defaults:
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

        self._sync_ei_windows_from_ui()

    def _sync_ei_windows_from_ui(self, _value=None, *, emit: bool = True) -> None:
        self.state.baseline_start_s = float(self.edit_baseline_start.value())
        self.state.baseline_end_s = float(self.edit_baseline_end.value())
        self.state.ictal_start_s = float(self.edit_ictal_start.value())
        self.state.ictal_end_s = float(self.edit_ictal_end.value())
        if emit:
            self.settingsChanged.emit()

    def _read_ei_inputs_from_ui(self) -> tuple[float, float, float, float, float, float]:
        seizure_onset = self._parse_float_text(self.edit_seizure_onset)
        seizure_offset = self._parse_float_text(self.edit_seizure_offset)
        if seizure_onset is None:
            raise ValueError("Enter a valid seizure onset time in seconds.")
        if seizure_offset is None:
            raise ValueError("Enter a valid seizure offset time in seconds.")

        self._sync_ei_windows_from_ui(emit=False)
        return (
            float(seizure_onset),
            float(seizure_offset),
            float(self.state.baseline_start_s),
            float(self.state.baseline_end_s),
            float(self.state.ictal_start_s),
            float(self.state.ictal_end_s),
        )

    def _total_duration_s(self) -> float | None:
        if self._raw is None or self._raw.n_times <= 1:
            return None
        return float(self._raw.times[-1])

    def _validate_ei_inputs(self) -> tuple[bool, str]:
        if self._raw is None or self._picks is None:
            return False, "Load a dataset before running REI."
        if not self.state.selected_abs:
            return False, "Select at least one channel before running REI."

        try:
            onset, offset, baseline_start, baseline_end, ictal_start, ictal_end = (
                self._read_ei_inputs_from_ui()
            )
        except ValueError as exc:
            return False, str(exc)

        if offset <= onset:
            return False, "Seizure offset must be after seizure onset."

        if baseline_end <= baseline_start:
            return False, "Baseline end must be after baseline start."
        if ictal_end <= ictal_start:
            return False, "Ictal end must be after ictal start."
        if baseline_end > onset:
            return False, "Baseline window must end at or before seizure onset."
        if ictal_start > onset:
            return False, "Ictal window must start at or before seizure onset."
        if ictal_end > offset:
            return False, "Ictal window must end at or before seizure offset."

        total_s = self._total_duration_s()
        try:
            validate_gui_ei_timing(
                seizure_onset_s=onset,
                seizure_offset_s=offset,
                baseline_window_s=(baseline_start, baseline_end),
                ictal_window_s=(ictal_start, ictal_end),
                recording_duration_s=total_s,
            )
        except ValueError as exc:
            return False, str(exc)

        self.state.seizure_onset_s = onset
        self.state.seizure_offset_s = offset
        return True, ""

    def _run_computation(self) -> None:
        if self.state.algorithm == "ei":
            ok, message = self._validate_ei_inputs()
            if not ok:
                QMessageBox.warning(self, "REI computation", message)
                return
            if not self._confirm_ei_montage_before_run():
                return
            perf_start = time.perf_counter()
            try:
                result = self._compute_ei_result()
            except Exception as exc:
                timed_mark("after_REI", perf_start, raw=self._raw, notes=f"error: {exc}")
                QMessageBox.warning(self, "REI computation", str(exc))
                return
            self._show_ei_result(result)
            self.ei_result_metadata = result.metadata
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            baseline_window = metadata.get("baseline_window_s", "")
            ictal_window = metadata.get("ictal_window_s", "")
            channel_results = (
                list(result.channels)
                if result.channels is not None
                else []
            )
            visible_window_s = None
            if isinstance(ictal_window, list) and len(ictal_window) >= 2:
                visible_window_s = float(ictal_window[1]) - float(ictal_window[0])
            timed_mark(
                "after_REI",
                perf_start,
                raw=self._raw,
                visible_window_s=visible_window_s,
                notes=(
                    f"channels={len(channel_results)}; "
                    f"baseline={baseline_window}; ictal={ictal_window}"
                ),
            )
            return

        if self.state.algorithm == "gamma_spike":
            if self._raw is None or self._picks is None:
                QMessageBox.warning(
                    self,
                    "Gamma spike detector",
                    "Load a dataset before running the gamma spike detector.",
                )
                return
            if not self.state.selected_abs:
                QMessageBox.warning(
                    self,
                    "Gamma spike detector",
                    "Select at least one channel before running the gamma spike detector.",
                )
                return

            if self._ei_data_callback is None:
                QMessageBox.warning(
                    self,
                    "Gamma spike detector",
                    "Gamma spike data extraction is not available.",
                )
                return

            try:
                start_s, stop_s = self._read_gamma_window_from_ui()
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    "Gamma spike detector",
                    str(exc),
                )
                return

            perf_start = time.perf_counter()
            try:
                result = self._compute_gamma_spike_result(start_s, stop_s)
            except Exception as exc:
                timed_mark(
                    "after_gamma_spike_detector",
                    perf_start,
                    raw=self._raw,
                    visible_window_s=max(0.0, stop_s - start_s),
                    notes=f"error: {exc}",
                )
                QMessageBox.warning(self, "Gamma spike detector", str(exc))
                return

            self._show_gamma_result(result)
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            timed_mark(
                "after_gamma_spike_detector",
                perf_start,
                raw=self._raw,
                visible_window_s=max(0.0, stop_s - start_s),
                notes=(
                    f"channels={len(result.channels)}; "
                    f"start_s={start_s:.3f}; stop_s={stop_s:.3f}; "
                    f"spikes={metadata.get('total_spikes', 0)}; "
                    f"gamma={metadata.get('gamma_success_count', 0)}"
                ),
            )
            # Keep the computation flow quiet: users can open the summary table
            # when needed, but it should not interrupt the main viewer.
            return

        QMessageBox.warning(
            self,
            "Computation",
            "Select REI or the gamma spike detector before running a computation.",
        )

    def _compute_ei_result(self) -> EIComputationResult:
        if self._ei_data_callback is None:
            raise RuntimeError("REI data extraction is not available.")

        (
            seizure_onset,
            seizure_offset,
            baseline_start,
            baseline_end,
            ictal_start,
            ictal_end,
        ) = self._read_ei_inputs_from_ui()

        data_start_s = min(baseline_start, ictal_start)
        data_stop_s = max(baseline_end, ictal_end)
        data, fs, channel_names = self._ei_data_callback(
            list(self.state.selected_abs),
            data_start_s,
            data_stop_s,
        )

        bad_channels = {
            str(name)
            for name in self._bad_channel_names()
            if str(name) in set(map(str, channel_names))
        }
        notch_modes_by_channel = self._ei_notch_modes_for_channels(channel_names)

        return compute_ei_for_gui(
            data=data,
            fs=float(fs),
            channel_names=list(channel_names),
            data_start_s=data_start_s,
            seizure_onset_s=seizure_onset,
            seizure_offset_s=seizure_offset,
            baseline_window_s=(baseline_start, baseline_end),
            ictal_window_s=(ictal_start, ictal_end),
            channel_groups=self._channel_groups,
            bad_channels=bad_channels,
            notch_modes_by_channel=notch_modes_by_channel,
            metadata=self._build_ei_metadata(
                self._current_montage_name(),
                seizure_onset_s=seizure_onset,
                seizure_offset_s=seizure_offset,
                baseline_window_s=(baseline_start, baseline_end),
                ictal_window_s=(ictal_start, ictal_end),
                notch_modes_by_channel=notch_modes_by_channel,
            ),
        )

    def _ei_notch_modes_for_channels(self, channel_names: list[str]) -> dict[str, str]:
        if self._ei_filter_callback is None:
            return {}
        try:
            modes_by_group = self._ei_filter_callback() or {}
        except Exception:
            return {}

        modes: dict[str, str] = {}
        for name in channel_names:
            channel_name = str(name)
            group = str(self._channel_groups.get(channel_name, "macro")).lower()
            mode = str(modes_by_group.get(group, modes_by_group.get("macro", "Off")))
            modes[channel_name] = mode
        return modes

    def _compute_gamma_spike_result(
        self,
        start_s: float,
        stop_s: float,
    ) -> GammaSpikeComputationResult:
        if self._ei_data_callback is None:
            raise RuntimeError("Gamma spike data extraction is not available.")

        filter_context_s = 30.0
        total_s = self._total_duration_s()
        padded_start_s = max(0.0, float(start_s) - filter_context_s)
        padded_stop_s = float(stop_s) + filter_context_s
        if total_s is not None:
            padded_stop_s = min(float(total_s), padded_stop_s)

        # Gamma-spike filtering is context-sensitive. Extract hidden padding so
        # the GUI path matches the reference segmented pipeline more closely;
        # only spikes inside the requested analysis window are reported.
        data, fs, channel_names = self._ei_data_callback(
            list(self.state.selected_abs),
            padded_start_s,
            padded_stop_s,
        )
        return compute_gamma_spike_for_gui(
            data=data,
            fs=float(fs),
            channel_names=list(channel_names),
            data_start_s=float(padded_start_s),
            analysis_window_s=(float(start_s), float(stop_s)),
            filter_context_seconds=filter_context_s,
        )

    def _clear_gamma_outputs(self) -> None:
        self._last_gamma_result = None
        self._gamma_summary_dialog = None
        self._gamma_review_dialog = None
        self.gammaSpikeMarkersChanged.emit({})
        if hasattr(self, "btn_open_gamma_summary"):
            self.btn_open_gamma_summary.setEnabled(False)
        if hasattr(self, "btn_open_gamma_review"):
            self.btn_open_gamma_review.setEnabled(False)

    def _show_gamma_result(self, result: GammaSpikeComputationResult) -> None:
        self._last_gamma_result = result
        self._gamma_summary_dialog = None
        self._gamma_review_dialog = None
        self.btn_open_gamma_summary.setEnabled(True)
        self.btn_open_gamma_review.setEnabled(True)
        self.gammaSpikeMarkersChanged.emit(
            self._gamma_spike_markers_from_result(result, mode="all")
        )

    def open_gamma_review_at(self, channel_name: str, time_s: float) -> None:
        self._pending_gamma_review_selection = (str(channel_name), float(time_s))
        if self._gamma_review_dialog is not None:
            self._gamma_review_dialog.close()
            self._gamma_review_dialog = None
        self._open_gamma_review_dialog()

    def _open_gamma_summary_dialog(self) -> None:
        result = self._last_gamma_result
        if result is None:
            QMessageBox.information(
                self,
                "Gamma summary",
                "Run the gamma spike detector first.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Gamma spike channel summary")
        dialog.resize(840, 420)

        layout = QVBoxLayout(dialog)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Spike level:"))
        level_combo = QComboBox()
        level_combo.addItem("All spikes", userData="all")
        level_combo.addItem("Gamma only", userData="gamma")
        level_combo.addItem("Non-gamma only", userData="non_gamma")
        controls_row.addWidget(level_combo)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(
            [
                "Channel",
                "Total spikes",
                "Gamma-spikes",
                "Spike-gamma rate",
                "Mean gamma power",
                "Mean gamma duration",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicatorShown(True)
        layout.addWidget(table)

        all_rows = self._gamma_summary_rows(result)

        sort_state: GammaSummarySortState = {
            "column": 2,
            "order": Qt.SortOrder.DescendingOrder,
        }

        def filtered_rows() -> list[GammaSummaryRow]:
            mode = str(level_combo.currentData() or "all")
            if mode == "gamma":
                return [
                    row
                    for row in all_rows
                    if int(row["gamma_spikes"]) > 0
                ]
            if mode == "non_gamma":
                return [
                    row
                    for row in all_rows
                    if int(row["gamma_spikes"]) == 0
                ]
            return list(all_rows)

        def populate(rows: list[GammaSummaryRow]) -> None:
            table.setSortingEnabled(False)
            table.setRowCount(0)
            for row_data in rows:
                row_idx = table.rowCount()
                table.insertRow(row_idx)

                values = [
                    str(row_data["channel"]),
                    str(int(row_data["total_spikes"])),
                    str(int(row_data["gamma_spikes"])),
                    str(row_data["spike_gamma_rate_text"]),
                    str(row_data["mean_gamma_power_text"]),
                    str(row_data["mean_gamma_duration_text"]),
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col > 0:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    table.setItem(row_idx, col, item)
            table.setSortingEnabled(False)
            self.eiSummaryOrderChanged.emit(
                [str(row["channel"]) for row in rows]
            )

        def sort_rows(rows: list[GammaSummaryRow]) -> list[GammaSummaryRow]:
            column = int(sort_state["column"])
            reverse = sort_state["order"] == Qt.SortOrder.DescendingOrder
            if column == 0:
                return sorted(rows, key=lambda row: row["channel_sort"], reverse=reverse)
            if column == 1:
                return sorted(rows, key=lambda row: row["total_spikes"], reverse=reverse)
            if column == 3:
                return sorted(rows, key=lambda row: row["spike_gamma_rate"], reverse=reverse)
            if column == 4:
                return sorted(rows, key=lambda row: row["mean_gamma_power"], reverse=reverse)
            if column == 5:
                return sorted(rows, key=lambda row: row["mean_gamma_duration"], reverse=reverse)
            return sorted(rows, key=lambda row: row["gamma_spikes"], reverse=reverse)

        def refresh_table() -> None:
            rows = sort_rows(filtered_rows())
            populate(rows)
            header.setSortIndicator(
                int(sort_state["column"]),
                sort_state["order"],
            )
            self.gammaSpikeMarkersChanged.emit(
                self._gamma_spike_markers_from_result(
                    result,
                    mode=str(level_combo.currentData() or "all"),
                )
            )

        def sort_summary_table(column: int) -> None:
            if sort_state["column"] == column:
                sort_state["order"] = (
                    Qt.SortOrder.DescendingOrder
                    if sort_state["order"] == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                sort_state["column"] = int(column)
                sort_state["order"] = (
                    Qt.SortOrder.AscendingOrder
                    if column == 0
                    else Qt.SortOrder.DescendingOrder
                )
            refresh_table()

        header.sectionClicked.connect(sort_summary_table)
        level_combo.currentIndexChanged.connect(lambda _index: refresh_table())
        refresh_table()

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        total_gamma_spikes = sum(int(row["gamma_spikes"]) for row in all_rows)
        footer = QLabel(
            "Total spikes: "
            f"{metadata.get('total_spikes', 0)}   "
            "Gamma-positive spikes: "
            f"{total_gamma_spikes}"
        )
        layout.addWidget(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        self._gamma_summary_dialog = dialog

    def _open_gamma_review_dialog(self) -> None:
        result = self._last_gamma_result
        if result is None:
            QMessageBox.information(
                self,
                "Gamma review",
                "Run the gamma spike detector first.",
            )
            return

        all_rows = self._gamma_event_review_rows(result)
        if not all_rows:
            QMessageBox.information(
                self,
                "Gamma review",
                "No retained spikes are available for review.",
            )
            return

        grid_cols = 6
        grid_rows = 4
        grid_total = grid_cols * grid_rows
        regular_border = "#4091ff"
        gamma_border = "#ff9743"

        dialog = QDialog(self)
        dialog.setWindowTitle("Gamma spike grid")
        dialog.resize(1180, 760)

        root = QVBoxLayout(dialog)

        controls_widget = QWidget()
        controls = QHBoxLayout(controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Spike level:"))
        level_combo = QComboBox()
        level_combo.addItem("All spikes", userData="all")
        level_combo.addItem("Gamma only", userData="gamma")
        level_combo.addItem("Non-gamma only", userData="non_gamma")
        controls.addWidget(level_combo)

        controls.addWidget(QLabel("Channel:"))
        channel_combo = QComboBox()
        channel_combo.addItem("All channels", userData="")
        for channel_name in sorted({str(row["channel"]) for row in all_rows}, key=str.casefold):
            channel_combo.addItem(channel_name, userData=channel_name)
        controls.addWidget(channel_combo)

        controls.addWidget(QLabel("Min power:"))
        min_power = QDoubleSpinBox()
        min_power.setRange(0.0, 1e9)
        min_power.setDecimals(4)
        min_power.setSingleStep(0.1)
        min_power.setValue(0.0)
        controls.addWidget(min_power)

        controls.addStretch(1)
        root.addWidget(controls_widget)

        grid_panel = QWidget()
        grid_panel_layout = QVBoxLayout(grid_panel)
        grid_panel_layout.setContentsMargins(0, 0, 0, 0)

        page_row = QHBoxLayout()
        prev_page_btn = QPushButton("Previous page")
        next_page_btn = QPushButton("Next page")
        page_label = QLabel("Page 1 / 1")
        page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_row.addWidget(prev_page_btn)
        page_row.addWidget(page_label, 1)
        page_row.addWidget(next_page_btn)
        grid_panel_layout.addLayout(page_row)

        legend_row = QHBoxLayout()
        gamma_legend = QLabel("Gamma spike")
        gamma_legend.setStyleSheet(
            f"border: 2px solid {gamma_border}; border-radius: 4px; "
            "padding: 2px 8px; background: #ffffff; color: #111111; font-weight: 600;"
        )
        regular_legend = QLabel("Non-gamma spike")
        regular_legend.setStyleSheet(
            f"border: 2px solid {regular_border}; border-radius: 4px; "
            "padding: 2px 8px; background: #ffffff; color: #111111; font-weight: 600;"
        )
        legend_row.addWidget(gamma_legend)
        legend_row.addWidget(regular_legend)
        legend_row.addStretch(1)
        grid_panel_layout.addLayout(legend_row)

        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setSpacing(8)
        grid_scroll.setWidget(grid_widget)
        grid_panel_layout.addWidget(grid_scroll, 1)
        root.addWidget(grid_panel, 1)

        zoom_panel = QWidget()
        zoom_panel.setVisible(False)
        zoom_layout = QVBoxLayout(zoom_panel)
        zoom_layout.setContentsMargins(0, 0, 0, 0)

        zoom_nav = QHBoxLayout()
        grid_btn = QPushButton("▦")
        grid_btn.setToolTip("Return to spike grid")
        prev_event_btn = QPushButton("← Previous spike")
        next_event_btn = QPushButton("Next spike →")
        zoom_nav.addWidget(grid_btn)
        zoom_nav.addWidget(prev_event_btn)
        zoom_nav.addWidget(next_event_btn)
        gamma_filter_check = QCheckBox("Display 30-100 Hz")
        zoom_nav.addWidget(gamma_filter_check)
        zoom_nav.addStretch(1)
        zoom_layout.addLayout(zoom_nav)

        zoom_title = QLabel("Selected spike")
        zoom_title.setStyleSheet("font-weight: 600;")
        zoom_layout.addWidget(zoom_title)

        zoom_event_info = QLabel("")
        zoom_event_info.setWordWrap(True)
        zoom_event_info.setStyleSheet(
            "color: #111111; background: #ffffff; border: 1px solid #d0d0d0; "
            "border-radius: 4px; padding: 4px 8px;"
        )
        zoom_layout.addWidget(zoom_event_info)

        zoom_plot = pg.PlotWidget()
        zoom_plot.setMinimumHeight(360)
        zoom_plot.setBackground("w")
        zoom_plot.setLabel("bottom", "Time", units="s")
        zoom_plot.setLabel("left", "Amplitude", units="uV")
        zoom_plot.showGrid(x=True, y=True, alpha=0.25)
        zoom_layout.addWidget(zoom_plot, 1)

        zoom_metrics = QTableWidget(1, 6)
        zoom_metrics.setHorizontalHeaderLabels(
            [
                "Gamma power",
                "Gamma frequency",
                "Gamma duration",
                "P1 boundary",
                "N1 boundary",
                "N2 boundary",
            ]
        )
        zoom_metrics.verticalHeader().setVisible(False)
        zoom_metrics.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for col in range(6):
            zoom_metrics.horizontalHeader().setSectionResizeMode(
                col,
                QHeaderView.ResizeMode.Stretch,
            )
        zoom_layout.addWidget(zoom_metrics, 0)

        boundary_info = QLabel(
            "P1/N1/N2 circles are waveform landmarks for the detected spike.  "
            "P1: the beginning of the spike; "
            "N1: the main spike peak; "
            "N2: the end of the spike.  "
            "The 30-100 Hz toggle changes display only, not saved gamma results."
        )
        boundary_info.setWordWrap(True)
        boundary_info.setStyleSheet(
            "color: #444; background: #f7f7f7; border: 1px solid #d0d0d0; "
            "border-radius: 4px; padding: 6px;"
        )
        zoom_layout.addWidget(boundary_info, 0)
        root.addWidget(zoom_panel, 1)

        state: GammaReviewState = {
            "rows": [],
            "index": -1,
            "current_page": 0,
            "is_zoomed": False,
        }

        def is_gamma_row(row: GammaReviewRow) -> bool:
            return bool(row.get("is_gamma", False))

        def filtered_rows() -> list[GammaReviewRow]:
            mode = str(level_combo.currentData() or "all")
            channel_filter = str(channel_combo.currentData() or "")
            power_cutoff = float(min_power.value())
            rows: list[GammaReviewRow] = []
            for row in all_rows:
                if mode == "gamma" and not is_gamma_row(row):
                    continue
                if mode == "non_gamma" and is_gamma_row(row):
                    continue
                if channel_filter and str(row["channel"]) != channel_filter:
                    continue
                power = row.get("gamma_power")
                if power_cutoff > 0.0:
                    if power is None or not np.isfinite(float(power)) or float(power) < power_cutoff:
                        continue
                rows.append(row)
            return rows

        def format_float(value: object, decimals: int = 3) -> str:
            if value is None:
                return ""
            try:
                number = float(value)
            except (TypeError, ValueError):
                return ""
            if not np.isfinite(number):
                return ""
            return f"{number:.{decimals}f}"

        def set_metric_values(values: list[str]) -> None:
            zoom_metrics.setRowCount(1)
            for col in range(zoom_metrics.columnCount()):
                value = values[col] if col < len(values) else ""
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                zoom_metrics.setItem(0, col, item)

        def maybe_gamma_filter_trace(times: np.ndarray, waveform: np.ndarray) -> np.ndarray:
            if not gamma_filter_check.isChecked():
                return waveform
            if times.size < 8 or waveform.size != times.size:
                return waveform
            dt = float(np.median(np.diff(times)))
            if not np.isfinite(dt) or dt <= 0.0:
                return waveform
            fs = 1.0 / dt
            high = min(100.0, 0.45 * fs)
            low = 30.0
            if high <= low:
                return waveform
            try:
                sos = signal.butter(
                    4,
                    [low, high],
                    btype="bandpass",
                    fs=fs,
                    output="sos",
                )
                return np.asarray(signal.sosfiltfilt(sos, waveform), dtype=float)
            except Exception:
                return waveform

        def clear_grid() -> None:
            while grid_layout.count():
                item = grid_layout.takeAt(0)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        def draw_analysis_markers(
            plot: pg.PlotWidget,
            row: GammaReviewRow,
            times: np.ndarray | None,
            waveform: np.ndarray | None,
        ) -> None:
            try:
                time_s = float(row["time_s"])
            except (TypeError, ValueError):
                return

            t_arr = None if times is None else np.asarray(times, dtype=float).reshape(-1)
            y_arr = None if waveform is None else np.asarray(waveform, dtype=float).reshape(-1)
            if t_arr is None or y_arr is None or t_arr.size < 2 or t_arr.size != y_arr.size:
                return

            finite_y = y_arr[np.isfinite(y_arr)]
            if finite_y.size:
                y_min = float(np.min(finite_y))
                y_max = float(np.max(finite_y))
            else:
                y_min, y_max = -1.0, 1.0
            if y_max <= y_min:
                y_min -= 1.0
                y_max += 1.0
            y_pad = 0.08 * (y_max - y_min)
            marker_y0 = y_min - y_pad
            marker_y1 = y_max + y_pad

            if float(t_arr[0]) <= time_s <= float(t_arr[-1]):
                spike_line = plot.plot(
                    [time_s, time_s],
                    [marker_y0, marker_y1],
                    pen=pg.mkPen((35, 35, 35), width=2, style=Qt.PenStyle.DashLine),
                )
                spike_line.setZValue(15)

            for label, key, color in (
                ("P1", "boundary_p1_time_s", (80, 180, 80)),
                ("N1", "boundary_n1_time_s", (230, 100, 80)),
                ("N2", "boundary_n2_time_s", (180, 80, 200)),
            ):
                value = row.get(key)
                if value is None:
                    continue
                try:
                    x = float(value)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(x):
                    continue
                if x < float(t_arr[0]) or x > float(t_arr[-1]):
                    continue
                y = float(np.interp(x, t_arr, y_arr))
                point = pg.ScatterPlotItem(
                    [x],
                    [y],
                    size=11,
                    pen=pg.mkPen(color, width=2),
                    brush=pg.mkBrush(255, 255, 255, 230),
                )
                point.setZValue(25)
                plot.addItem(point)
                text = pg.TextItem(label, anchor=(0.5, 1.2), color=color)
                text.setPos(x, y)
                plot.addItem(text)

            gamma_start = row.get("gamma_start_time_s")
            gamma_stop = row.get("gamma_stop_time_s")
            if gamma_start is None or gamma_stop is None:
                return
            try:
                x0 = float(gamma_start)
                x1 = float(gamma_stop)
            except (TypeError, ValueError):
                return
            if np.isfinite(x0) and np.isfinite(x1) and x1 >= x0:
                clipped_x0 = max(float(t_arr[0]), x0)
                clipped_x1 = min(float(t_arr[-1]), x1)
                if clipped_x1 <= clipped_x0:
                    return
                gamma_y = marker_y0 + 0.08 * (marker_y1 - marker_y0)
                gamma_segment = plot.plot(
                    [clipped_x0, clipped_x1],
                    [gamma_y, gamma_y],
                    pen=pg.mkPen((255, 151, 67, 190), width=8),
                )
                gamma_segment.setZValue(12)

        def update_zoom(row: GammaReviewRow) -> None:
            channel = str(row["channel"])
            time_s = float(row["time_s"])
            event_number = int(row.get("event_number", 0) or 0)
            zoom_title.setText(
                f"{channel} - Event {event_number}"
                if event_number > 0
                else channel
            )
            event_type = "Gamma spike" if is_gamma_row(row) else "Non-gamma spike"
            event_color = gamma_border if is_gamma_row(row) else regular_border
            zoom_event_info.setText(
                f"{event_type} | Dashed line: detected spike | "
                "Orange segment: estimated gamma activity window"
            )
            zoom_event_info.setStyleSheet(
                f"color: #111111; background: #ffffff; border: 1px solid #d0d0d0; "
                f"border-left: 5px solid {event_color}; border-radius: 4px; "
                "padding: 4px 8px; font-weight: 600;"
            )
            set_metric_values(
                [
                    format_float(row.get("gamma_power"), 4),
                    f"{format_float(row.get('gamma_frequency_hz'), 1)} Hz",
                    f"{format_float(row.get('gamma_duration_ms'), 1)} ms",
                    format_float(row.get("boundary_p1_time_s"), 4),
                    format_float(row.get("boundary_n1_time_s"), 4),
                    format_float(row.get("boundary_n2_time_s"), 4),
                ]
            )

            zoom_plot.clear()
            zoom_plot.setBackground("w")
            times, waveform = self._fetch_gamma_event_waveform(row, half_window_s=0.45)
            plotted_waveform = waveform
            if times is not None and waveform is not None:
                plotted_waveform = maybe_gamma_filter_trace(times, waveform)
                zoom_plot.plot(times, plotted_waveform, pen=pg.mkPen("#222222", width=1.5))
                zoom_plot.setXRange(float(times[0]), float(times[-1]), padding=0.02)
            else:
                zoom_plot.setXRange(time_s - 0.45, time_s + 0.45, padding=0.02)
            draw_analysis_markers(zoom_plot, row, times, plotted_waveform)

            self.gammaSpikeEventActivated.emit(channel, time_s)

        def update_zoom_nav() -> None:
            rows = list(state["rows"])
            index = int(state["index"])
            prev_event_btn.setEnabled(index > 0)
            next_event_btn.setEnabled(0 <= index < len(rows) - 1)

        def show_grid() -> None:
            state["is_zoomed"] = False
            controls_widget.setVisible(True)
            zoom_panel.setVisible(False)
            grid_panel.setVisible(True)
            update_grid()

        def show_zoom(index: int) -> None:
            rows = list(state["rows"])
            if not rows:
                return
            index = max(0, min(int(index), len(rows) - 1))
            state["index"] = index
            state["current_page"] = index // grid_total
            state["is_zoomed"] = True
            controls_widget.setVisible(False)
            grid_panel.setVisible(False)
            zoom_panel.setVisible(True)
            update_zoom(rows[index])
            update_zoom_nav()

        def make_card(row_data: GammaReviewRow, global_index: int) -> QWidget:
            border = gamma_border if is_gamma_row(row_data) else regular_border
            card = _GammaSpikeCardFrame(global_index)
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card.setStyleSheet(
                "QFrame {"
                f"border: 2px solid {border};"
                "border-radius: 6px;"
                "background-color: #ffffff;"
                "}"
                "QLabel { border: none; background: transparent; }"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(3)

            channel_name = str(row_data.get("channel", ""))
            event_number = int(row_data.get("event_number", int(global_index) + 1))
            title = QLabel(f"{channel_name} | Event {event_number}")
            title.setStyleSheet("font-weight: 600; color: #111111;")
            layout.addWidget(title)

            plot = pg.PlotWidget()
            plot.setMinimumHeight(90)
            plot.setMaximumHeight(130)
            plot.setMenuEnabled(False)
            plot.setBackground("w")
            plot.hideAxis("left")
            plot.hideAxis("bottom")
            plot.showGrid(x=True, y=False, alpha=0.18)
            times, waveform = self._fetch_gamma_event_waveform(row_data, half_window_s=0.18)
            if times is not None and waveform is not None:
                mini_waveform = np.asarray(waveform, dtype=float).reshape(-1)
                plot.plot(times, mini_waveform, pen=pg.mkPen("#222222", width=1))
                plot.setXRange(float(times[0]), float(times[-1]), padding=0.0)
            try:
                time_s = float(row_data.get("time_s", 0.0))
            except (TypeError, ValueError):
                time_s = 0.0
            if times is not None and waveform is not None:
                mini_times = np.asarray(times, dtype=float).reshape(-1)
                finite_y = mini_waveform[np.isfinite(mini_waveform)]
                if (
                    mini_times.size >= 2
                    and finite_y.size
                    and float(mini_times[0]) <= time_s <= float(mini_times[-1])
                ):
                    y_min = float(np.min(finite_y))
                    y_max = float(np.max(finite_y))
                    if y_max <= y_min:
                        y_min -= 1.0
                        y_max += 1.0
                    y_pad = 0.08 * (y_max - y_min)
                    card_line = plot.plot(
                        [time_s, time_s],
                        [y_min - y_pad, y_max + y_pad],
                        pen=pg.mkPen((35, 35, 35), width=1, style=Qt.PenStyle.DashLine),
                    )
                    card_line.setZValue(15)
            plot.setMouseEnabled(x=False, y=False)
            layout.addWidget(plot, 1)

            start = float(row_data.get("event_start_time_s", time_s))
            stop = float(row_data.get("event_stop_time_s", time_s))
            footer = QLabel(f"{start:.3f} - {stop:.3f} s")
            footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            footer.setStyleSheet("color: #555; font-size: 10px;")
            layout.addWidget(footer)

            card.clicked.connect(show_zoom)
            plot.scene().sigMouseClicked.connect(lambda _event, idx=global_index: show_zoom(idx))
            return card

        def update_page_controls() -> None:
            total_pages = max(1, (len(state["rows"]) + grid_total - 1) // grid_total)
            state["current_page"] = max(
                0,
                min(int(state["current_page"]), total_pages - 1),
            )
            page_label.setText(f"Page {int(state['current_page']) + 1} / {total_pages}")
            prev_page_btn.setEnabled(int(state["current_page"]) > 0)
            next_page_btn.setEnabled(int(state["current_page"]) < total_pages - 1)

        def update_grid() -> None:
            clear_grid()
            rows = list(state["rows"])
            update_page_controls()
            start_idx = int(state["current_page"]) * grid_total
            end_idx = min(start_idx + grid_total, len(rows))
            for local_index, row_data in enumerate(rows[start_idx:end_idx]):
                row = local_index // grid_cols
                col = local_index % grid_cols
                grid_layout.addWidget(make_card(row_data, start_idx + local_index), row, col)

        def populate() -> None:
            previous_index = int(state["index"])
            rows = filtered_rows()
            state["rows"] = rows
            pending = self._pending_gamma_review_selection
            if pending is not None:
                pending_channel, pending_time = pending
                best_index = None
                best_delta = float("inf")
                for idx, row_data in enumerate(rows):
                    if str(row_data.get("channel", "")) != str(pending_channel):
                        continue
                    try:
                        delta = abs(float(row_data.get("time_s", np.inf)) - float(pending_time))
                    except (TypeError, ValueError):
                        continue
                    if delta < best_delta:
                        best_index = int(idx)
                        best_delta = float(delta)
                self._pending_gamma_review_selection = None
                if best_index is not None:
                    state["index"] = int(best_index)
                    state["current_page"] = int(best_index) // grid_total
                    self.gammaSpikeMarkersChanged.emit(
                        self._gamma_spike_markers_from_review_rows(rows)
                    )
                    if state["is_zoomed"]:
                        show_zoom(best_index)
                    else:
                        update_grid()
                    return

            if rows:
                state["index"] = max(0, min(previous_index, len(rows) - 1))
            else:
                state["index"] = -1
            self.gammaSpikeMarkersChanged.emit(
                self._gamma_spike_markers_from_review_rows(rows)
            )
            if state["is_zoomed"] and rows:
                show_zoom(int(state["index"]))
            else:
                show_grid()

        def go_previous() -> None:
            show_zoom(int(state["index"]) - 1)

        def go_next() -> None:
            show_zoom(int(state["index"]) + 1)

        def prev_page() -> None:
            if int(state["current_page"]) > 0:
                state["current_page"] = int(state["current_page"]) - 1
                update_grid()

        def next_page() -> None:
            state["current_page"] = int(state["current_page"]) + 1
            update_grid()

        prev_page_btn.clicked.connect(prev_page)
        next_page_btn.clicked.connect(next_page)
        prev_event_btn.clicked.connect(go_previous)
        next_event_btn.clicked.connect(go_next)
        grid_btn.clicked.connect(show_grid)
        gamma_filter_check.toggled.connect(lambda _checked: show_zoom(int(state["index"])))
        level_combo.currentIndexChanged.connect(lambda _index: populate())
        channel_combo.currentIndexChanged.connect(lambda _index: populate())
        min_power.valueChanged.connect(lambda _value: populate())

        populate()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        root.addWidget(buttons)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._gamma_review_dialog = dialog

    def _gamma_spike_markers_from_result(
        self,
        result: GammaSpikeComputationResult,
        *,
        mode: str,
    ) -> dict[str, list[dict[str, float | str]]]:
        marker_mode = str(mode or "all")
        markers: dict[str, list[dict[str, float | str]]] = {}
        for channel_result in result.channels:
            gamma_events = [
                event
                for event in channel_result.events
                if (
                    event.gamma_power is not None
                    and event.gamma_duration_ms is not None
                    and (
                        float(event.gamma_power) > 0.0
                        or float(event.gamma_duration_ms) > 0.0
                    )
                )
            ]
            gamma_ids = {id(event) for event in gamma_events}
            gamma_spikes = len(gamma_events)
            if marker_mode == "gamma" and gamma_spikes == 0:
                continue
            if marker_mode == "non_gamma" and gamma_spikes > 0:
                continue

            events: list[dict[str, float | str]] = []
            for event in channel_result.events:
                is_gamma = id(event) in gamma_ids
                if marker_mode == "gamma" and not is_gamma:
                    continue
                if marker_mode == "non_gamma" and is_gamma:
                    continue
                try:
                    time_s = float(event.time_s)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(time_s):
                    continue
                events.append(
                    {
                        "time_s": time_s,
                        "kind": "gamma" if is_gamma else "regular",
                    }
                )
            if events:
                markers[str(channel_result.channel)] = events
        return markers

    def _gamma_spike_markers_from_review_rows(
        self,
        rows: list[GammaReviewRow],
    ) -> dict[str, list[dict[str, float | str]]]:
        markers: dict[str, list[dict[str, float | str]]] = {}
        for row in rows:
            channel = str(row.get("channel", ""))
            if not channel:
                continue
            try:
                time_s = float(row.get("time_s", np.nan))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(time_s):
                continue
            kind = "gamma" if bool(row.get("is_gamma", False)) else "regular"
            markers.setdefault(channel, []).append(
                {
                    "time_s": time_s,
                    "kind": kind,
                }
            )
        return markers

    def _fetch_gamma_event_waveform(
        self,
        row: GammaReviewRow,
        *,
        half_window_s: float,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        if self._ei_data_callback is None:
            return None, None
        try:
            channel_name = str(row["channel"])
            center_s = float(row["time_s"])
        except (KeyError, TypeError, ValueError):
            return None, None

        try:
            abs_idx = self._ch_names_displayed.index(channel_name)
        except ValueError:
            return None, None

        start_s = max(0.0, center_s - float(half_window_s))
        stop_s = center_s + float(half_window_s)
        try:
            data, fs, _names = self._ei_data_callback([int(abs_idx)], start_s, stop_s)
        except Exception:
            return None, None

        arr = np.asarray(data, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 2:
            return None, None
        sfreq = float(fs)
        if sfreq <= 0:
            return None, None
        waveform = np.asarray(arr[0], dtype=float).reshape(-1)
        times = start_s + np.arange(waveform.size, dtype=float) / sfreq
        return times, waveform

    def _gamma_event_review_rows(
        self,
        result: GammaSpikeComputationResult,
    ) -> list[GammaReviewRow]:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        fs = float(metadata.get("fs", 0.0) or 0.0)
        data_start_s = float(metadata.get("data_start_s", 0.0) or 0.0)

        def sample_to_time(sample0: float | None) -> float | None:
            if sample0 is None or fs <= 0.0:
                return None
            try:
                sample = float(sample0)
            except (TypeError, ValueError):
                return None
            if not np.isfinite(sample):
                return None
            return data_start_s + sample / fs

        rows: list[GammaReviewRow] = []
        channel_counts: dict[str, int] = {}
        for channel_result in result.channels:
            channel_name = str(channel_result.channel)
            for event_index, event in enumerate(channel_result.events):
                is_gamma = self._gamma_event_is_gamma(event)
                duration_ms = event.gamma_duration_ms
                gamma_start_s = None
                gamma_stop_s = None
                if is_gamma and duration_ms is not None:
                    try:
                        half_duration_s = 0.5 * float(duration_ms) / 1000.0
                    except (TypeError, ValueError):
                        half_duration_s = 0.0
                    if np.isfinite(half_duration_s) and half_duration_s > 0.0:
                        gamma_start_s = float(event.time_s) - half_duration_s
                        gamma_stop_s = float(event.time_s) + half_duration_s

                p1_time = sample_to_time(event.boundary_p1_sample)
                n1_time = sample_to_time(event.boundary_n1_sample)
                n2_time = sample_to_time(event.boundary_n2_sample)
                event_start_s = p1_time if p1_time is not None else float(event.time_s) - 0.075
                event_stop_s = n2_time if n2_time is not None else float(event.time_s) + 0.075
                if event_stop_s < event_start_s:
                    event_start_s, event_stop_s = event_stop_s, event_start_s
                channel_counts[channel_name] = channel_counts.get(channel_name, 0) + 1

                rows.append(
                    {
                        "channel": channel_name,
                        "event_index": int(event_index),
                        "event_number": int(channel_counts[channel_name]),
                        "spike_label": f"{channel_name}-{channel_counts[channel_name]}",
                        "time_s": float(event.time_s),
                        "event_start_time_s": float(event_start_s),
                        "event_stop_time_s": float(event_stop_s),
                        "is_gamma": bool(is_gamma),
                        "gamma_power": event.gamma_power,
                        "gamma_frequency_hz": event.gamma_frequency_hz,
                        "gamma_duration_ms": event.gamma_duration_ms,
                        "boundary_p1_time_s": p1_time,
                        "boundary_n1_time_s": n1_time,
                        "boundary_n2_time_s": n2_time,
                        "gamma_start_time_s": gamma_start_s,
                        "gamma_stop_time_s": gamma_stop_s,
                        "error": event.error,
                    }
                )
        rows.sort(key=lambda row: (str(row["channel"]).casefold(), float(row["time_s"])))
        return rows

    def _gamma_event_is_gamma(self, event: GammaSpikeEventResult) -> bool:
        if event.gamma_power is None or event.gamma_duration_ms is None:
            return False
        try:
            power = float(event.gamma_power)
            duration = float(event.gamma_duration_ms)
        except (TypeError, ValueError):
            return False
        return bool(
            np.isfinite(power)
            and np.isfinite(duration)
            and (power > 0.0 or duration > 0.0)
        )

    def _gamma_summary_rows(
        self,
        result: GammaSpikeComputationResult,
    ) -> list[GammaSummaryRow]:
        rows: list[GammaSummaryRow] = []
        for channel_result in result.channels:
            total_spikes = int(channel_result.spike_count)
            gamma_events = [
                event
                for event in channel_result.events
                if (
                    event.gamma_power is not None
                    and event.gamma_duration_ms is not None
                    and (
                        float(event.gamma_power) > 0.0
                        or float(event.gamma_duration_ms) > 0.0
                    )
                )
            ]
            gamma_spikes = len(gamma_events)
            non_gamma_spikes = max(0, total_spikes - gamma_spikes)
            rate = (
                float(gamma_spikes) / float(total_spikes)
                if total_spikes > 0
                else 0.0
            )
            powers = np.asarray(
                [float(event.gamma_power) for event in gamma_events],
                dtype=float,
            )
            durations = np.asarray(
                [float(event.gamma_duration_ms) for event in gamma_events],
                dtype=float,
            )
            finite_powers = powers[np.isfinite(powers)]
            finite_durations = durations[np.isfinite(durations)]
            mean_power = (
                float(np.mean(finite_powers))
                if finite_powers.size
                else float("-inf")
            )
            mean_duration = (
                float(np.mean(finite_durations))
                if finite_durations.size
                else float("-inf")
            )
            rows.append(
                {
                    "channel": str(channel_result.channel),
                    "channel_sort": str(channel_result.channel).casefold(),
                    "total_spikes": int(total_spikes),
                    "gamma_spikes": int(gamma_spikes),
                    "non_gamma_spikes": int(non_gamma_spikes),
                    "spike_gamma_rate": float(rate),
                    "spike_gamma_rate_text": f"{100.0 * rate:.1f}%",
                    "mean_gamma_power": mean_power,
                    "mean_gamma_power_text": (
                        f"{mean_power:.4g}" if np.isfinite(mean_power) else ""
                    ),
                    "mean_gamma_duration": mean_duration,
                    "mean_gamma_duration_text": (
                        f"{mean_duration:.1f} ms"
                        if np.isfinite(mean_duration)
                        else ""
                    ),
                }
            )
        return rows

    def _bad_channel_names(self) -> set[str]:
        return {
            str(name)
            for name in getattr(self, "_bad_names", set())
        }

    def _current_montage_name(self) -> str:
        if self._current_montage_callback is None:
            return "Unknown"
        try:
            montage = str(self._current_montage_callback() or "Unknown").strip()
        except Exception:
            montage = "Unknown"
        return montage or "Unknown"

    def _confirm_ei_montage_before_run(self) -> bool:
        current_montage = self._current_montage_name()
        if current_montage.casefold() == "bipolar":
            return True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Recommended montage: Bipolar")
        msg.setText(
            "The Recruitment Energy Index (REI) is designed for recruitment-focused iEEG analysis. "
            "Using another montage may affect REI scores and "
            "channel rankings.\n\n"
            f"Current montage: {current_montage}"
        )
        switch_btn = msg.addButton("Switch to Bipolar", QMessageBox.ButtonRole.AcceptRole)
        run_anyway_btn = msg.addButton("Run Anyway", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(switch_btn)
        msg.setEscapeButton(cancel_btn)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is run_anyway_btn:
            return True
        if clicked is not switch_btn:
            return False
        if self._switch_to_bipolar_callback is None:
            self._show_nonblocking_ei_error("Bipolar conversion is not available.")
            return False

        try:
            ok, error = self._switch_to_bipolar_callback()
        except Exception as exc:
            ok = False
            error = str(exc)

        if not ok:
            self._show_nonblocking_ei_error(error or "Bipolar conversion is not available.")
            return False
        QMessageBox.information(
            self,
            "Recommended montage: Bipolar",
            "Switched to bipolar montage. Review the selected channels and run REI again.",
        )
        return False

    def _show_nonblocking_ei_error(self, message: str) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("REI computation")
        msg.setText(str(message))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.open()

    def _build_ei_metadata(
        self,
        montage_used: str,
        *,
        seizure_onset_s: float | None = None,
        seizure_offset_s: float | None = None,
        baseline_window_s: tuple[float, float] | None = None,
        ictal_window_s: tuple[float, float] | None = None,
        notch_modes_by_channel: dict[str, str] | None = None,
    ) -> dict:
        active_notch_modes = sorted(
            {
                str(mode)
                for mode in (notch_modes_by_channel or {}).values()
                if str(mode) != "Off"
            }
        )
        return {
            "algorithm": "Recruitment Energy Index",
            "montage_used": montage_used,
            "recommended_montage": "bipolar",
            "seizure_onset_s": seizure_onset_s,
            "seizure_offset_s": seizure_offset_s,
            "baseline_window_s": (
                list(map(float, baseline_window_s))
                if baseline_window_s is not None
                else None
            ),
            "ictal_window_s": (
                list(map(float, ictal_window_s))
                if ictal_window_s is not None
                else None
            ),
            "bad_channels_excluded": True,
            "uses_display_filter": False,
            "analysis_filter": {
                "type": "butterworth_bandpass",
                "order": int(self.ei_params["filter_order"]),
                "low_hz": float(self.ei_params["low_freq"]),
                "high_hz": float(self.ei_params["high_freq"]),
                "zero_phase": bool(self.ei_params["zero_phase"]),
            },
            "notch_filter": bool(active_notch_modes),
            "notch_modes": active_notch_modes,
            "notch_modes_by_channel": {
                str(channel): str(mode)
                for channel, mode in (notch_modes_by_channel or {}).items()
                if str(mode) != "Off"
            },
            "threshold_sigma": float(self.ei_params["threshold_sigma"]),
            "energy_window_sec": float(self.ei_params["energy_window_sec"]),
            "hfer_window_sec": float(self.ei_params["hfer_window_sec"]),
        }

    def _clear_ei_outputs(self) -> None:
        self._last_ei_result = None
        self.ei_result_metadata = None
        self.recruitmentMarkersChanged.emit({})
        self.eiScoreLabelsChanged.emit({})
        self._ei_summary_table = None
        self._ei_summary_row_by_channel = {}

        if hasattr(self, "btn_open_ei_summary"):
            self.btn_open_ei_summary.setEnabled(False)

        if hasattr(self, "btn_open_ei_heatmap"):
            self.btn_open_ei_heatmap.setEnabled(False)

    def _show_ei_result(self, result: EIComputationResult) -> None:
        self._last_ei_result = result
        self.ei_result_metadata = result.metadata
        self._ei_summary_table = None
        self._ei_summary_row_by_channel = {}
        self.recruitmentMarkersChanged.emit(
            self._recruitment_markers_from_result(result)
        )
        self.eiScoreLabelsChanged.emit(
            self._ei_score_label_styles_from_result(result)
        )
        heatmap_channels = (
            list(result.heatmap_channels)
            if result.heatmap_channels is not None
            else []
        )
        if heatmap_channels:
            self.eiSummaryOrderChanged.emit(
                [str(channel_name) for channel_name in heatmap_channels]
            )

        self.btn_open_ei_summary.setEnabled(True)
        self.btn_open_ei_heatmap.setEnabled(bool(result.heatmap.size))

    def _ei_score_label_styles_from_result(
        self,
        result: EIComputationResult,
    ) -> dict[str, dict[str, float | int]]:
        scores = [float(row.ei) for row in result.channels if np.isfinite(float(row.ei))]
        max_score = max(scores) if scores else 0.0
        if max_score <= 0.0:
            max_score = 1.0

        styles: dict[str, dict[str, float | int]] = {}
        for channel_result in result.channels:
            score = float(channel_result.ei)
            score_norm = max(0.0, min(1.0, score / max_score)) if np.isfinite(score) else 0.0
            styles[str(channel_result.channel)] = {
                "score_norm": float(score_norm),
                "rank": int(channel_result.rank),
            }
        return styles

    def _recruitment_markers_from_result(
        self,
        result: EIComputationResult,
    ) -> dict[str, float]:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        seizure_onset = metadata.get("seizure_onset_s", self.state.seizure_onset_s)
        if not isinstance(seizure_onset, (int, float)):
            return {}

        markers: dict[str, float] = {}
        for channel_result in result.channels:
            recruitment_time = (
                float(seizure_onset)
                + float(channel_result.onset_sec_from_seizure_onset)
            )
            if np.isfinite(recruitment_time):
                markers[str(channel_result.channel)] = float(recruitment_time)
        return markers

    def _compute_recruitment_delay(
        self,
        channel_result: EIChannelResult,
        metadata: dict | None,
    ) -> tuple[float, bool]:
        return float(channel_result.onset_sec_from_seizure_onset), True

    def _open_ei_summary_dialog(self) -> None:
        result = self._last_ei_result
        if result is None:
            QMessageBox.information(self, "REI summary", "Run REI first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("REI summary")
        dialog.resize(720, 420)

        layout = QVBoxLayout(dialog)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            [
                "Channel",
                "REI score",
                "Rank",
                "Peak HFER activity",
                "Recruitment delay (s)",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._ei_summary_table = table
        self._ei_summary_row_by_channel = {}

        metadata = result.metadata or {}
        hfer_activity_by_channel: dict[str, float] = {}
        heatmap = np.asarray(result.heatmap, dtype=float)
        heatmap_channels = list(result.heatmap_channels or [])
        display_order_by_channel = {
            str(channel_name): int(order)
            for order, channel_name in enumerate(heatmap_channels)
        }
        if heatmap.ndim == 2 and heatmap.size and heatmap_channels:
            n_rows = min(int(heatmap.shape[0]), len(heatmap_channels))
            for row_idx in range(n_rows):
                row = np.asarray(heatmap[row_idx], dtype=float)
                finite_values = row[np.isfinite(row)]
                if finite_values.size:
                    hfer_activity_by_channel[str(heatmap_channels[row_idx])] = float(
                        np.max(finite_values)
                    )

        summary_rows: list[EISummaryRow] = []
        for original_order, channel_result in enumerate(result.channels):
            recruitment_delay, has_delay_metadata = self._compute_recruitment_delay(
                channel_result,
                metadata,
            )
            channel_name = str(channel_result.channel)
            hfer_activity = hfer_activity_by_channel.get(channel_name)
            summary_rows.append(
                {
                    "original_order": int(original_order),
                    "display_order": int(
                        display_order_by_channel.get(channel_name, original_order)
                    ),
                    "channel": channel_name,
                    "channel_sort": channel_name.casefold(),
                    "ei_score": float(channel_result.ei),
                    "rank": int(channel_result.rank),
                    "hfer_activity": (
                        float(hfer_activity)
                        if hfer_activity is not None
                        else float("-inf")
                    ),
                    "hfer_activity_text": (
                        f"{float(hfer_activity):.4g}"
                        if hfer_activity is not None
                        else ""
                    ),
                    "recruitment_delay": (
                        float(recruitment_delay) if has_delay_metadata else float("inf")
                    ),
                    "recruitment_delay_text": (
                        f"{recruitment_delay:+.3f}" if has_delay_metadata else ""
                    ),
                }
            )

        def populate_summary_table(rows: list[EISummaryRow]) -> None:
            table.setSortingEnabled(False)
            table.setRowCount(0)
            self._ei_summary_row_by_channel = {}
            for row_data in rows:
                row_idx = table.rowCount()
                table.insertRow(row_idx)
                channel_name = str(row_data["channel"])
                self._ei_summary_row_by_channel[channel_name] = int(row_idx)

                values = [
                    channel_name,
                    f"{float(row_data['ei_score']):.4f}",
                    str(int(row_data["rank"])),
                    str(row_data["hfer_activity_text"]),
                    str(row_data["recruitment_delay_text"]),
                ]

                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, channel_name)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col in {1, 2, 3, 4}:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    table.setItem(row_idx, col, item)
            table.setSortingEnabled(False)
            self.eiSummaryOrderChanged.emit(
                [str(row["channel"]) for row in rows]
            )

        def activate_summary_row(row: int, _column: int) -> None:
            item = table.item(int(row), 0)
            if item is None:
                return
            channel_name = item.data(Qt.ItemDataRole.UserRole)
            if channel_name is None:
                channel_name = item.text()
            self.eiSummaryChannelActivated.emit(str(channel_name))

        sort_state: EISummarySortState = {
            "column": -1,
            "order": Qt.SortOrder.AscendingOrder,
            "channel_mode": "display",
        }

        def sort_summary_table(column: int) -> None:
            if column == 0:
                if sort_state["channel_mode"] == "display":
                    sort_state["channel_mode"] = "alphabetical"
                    sorted_rows = sorted(
                        summary_rows,
                        key=lambda row: str(row["channel_sort"]),
                    )
                    header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
                else:
                    sort_state["channel_mode"] = "display"
                    sorted_rows = sorted(
                        summary_rows,
                        key=lambda row: int(row["display_order"]),
                    )
                    header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
                sort_state["column"] = 0
                populate_summary_table(sorted_rows)
                return

            if sort_state["column"] == column:
                sort_state["order"] = (
                    Qt.SortOrder.DescendingOrder
                    if sort_state["order"] == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                sort_state["order"] = (
                    Qt.SortOrder.DescendingOrder
                    if column in {1, 3}
                    else Qt.SortOrder.AscendingOrder
                )
            sort_state["column"] = column
            sort_state["channel_mode"] = "display"

            reverse = sort_state["order"] == Qt.SortOrder.DescendingOrder
            if column == 1:
                sorted_rows = sorted(
                    summary_rows,
                    key=lambda row: row["ei_score"],
                    reverse=reverse,
                )
            elif column == 2:
                sorted_rows = sorted(
                    summary_rows,
                    key=lambda row: row["rank"],
                    reverse=reverse,
                )
            elif column == 3:
                sorted_rows = sorted(
                    summary_rows,
                    key=lambda row: row["hfer_activity"],
                    reverse=reverse,
                )
            elif column == 4:
                sorted_rows = sorted(
                    summary_rows,
                    key=lambda row: row["recruitment_delay"],
                    reverse=reverse,
                )
            else:
                sorted_rows = sorted(
                    summary_rows,
                    key=lambda row: row["original_order"],
                    reverse=reverse,
                )
            header.setSortIndicator(column, sort_state["order"])
            populate_summary_table(sorted_rows)

        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(sort_summary_table)
        table.cellClicked.connect(activate_summary_row)
        default_rows = sorted(
            summary_rows,
            key=lambda row: int(row["display_order"]),
        )
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        populate_summary_table(default_rows)
        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        self._ei_summary_dialog = dialog

    def highlight_ei_summary_channel(self, channel_name: str) -> bool:
        table = self._ei_summary_table
        if table is None:
            return False
        row = self._ei_summary_row_by_channel.get(str(channel_name))
        if row is None:
            return False
        if not (0 <= int(row) < table.rowCount()):
            return False
        item_or_none = table.item(int(row), 0)
        if item_or_none is None:
            return False
        item: QTableWidgetItem = item_or_none
        table.setCurrentCell(int(row), 0)
        table.selectRow(int(row))
        table.scrollToItem(item)
        return True

    def _open_ei_heatmap_dialog(self) -> None:
        result = self._last_ei_result
        if result is None:
            QMessageBox.information(self, "REI heatmap", "Run REI first.")
            return

        if not result.heatmap.size:
            QMessageBox.information(self, "REI heatmap", "No heatmap data available.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("REI heatmap")
        dialog.resize(980, 620)

        layout = QVBoxLayout(dialog)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Sort channels by:"))
        sort_combo = QComboBox()
        sort_combo.addItem("REI score", userData="ei_score")
        sort_combo.addItem("Recruitment delay", userData="recruitment_delay")
        sort_combo.addItem("Peak HFER activity", userData="peak_hfer")
        sort_combo.addItem("Mean HFER activity", userData="mean_hfer")
        sort_combo.addItem("Original channel order", userData="original")
        sort_combo.addItem("Channel name", userData="channel_name")
        controls_row.addWidget(sort_combo)

        controls_row.addSpacing(16)
        controls_row.addWidget(QLabel("Show top N channels:"))
        top_n_spin = QSpinBox()
        heatmap_channel_names = (
            list(result.heatmap_channels)
            if result.heatmap_channels is not None
            else []
        )
        max_channels = max(1, min(len(heatmap_channel_names), int(result.heatmap.shape[0])))
        top_n_spin.setRange(1, max_channels)
        top_n_spin.setValue(min(30, max_channels))
        controls_row.addWidget(top_n_spin)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        score_plot = pg.PlotWidget()
        score_plot.setMinimumWidth(120)
        score_plot.showGrid(x=True, y=False, alpha=0.15)
        score_plot.setLabel("bottom", "REI score")
        score_plot.hideAxis("left")

        heatmap_plot = pg.PlotWidget()
        heatmap_plot.showGrid(x=True, y=True, alpha=0.15)
        heatmap_plot.setLabel("bottom", "Time from seizure onset (s)")
        heatmap_plot.setLabel("left", "Channel")
        heatmap_plot.getAxis("left").setWidth(140)
        heatmap_plot.getViewBox().invertY(True)
        score_plot.setYLink(heatmap_plot)
        score_plot.getViewBox().invertY(True)

        heatmap_image = pg.ImageItem(axisOrder="row-major")
        heatmap_plot.addItem(heatmap_image)
        color_map = pg.colormap.get("viridis")
        color_bar: pg.ColorBarItem | None = None
        if color_map is not None:
            lookup_table = np.asarray(color_map.getLookupTable(), dtype=np.float64)
            heatmap_image.setLookupTable(lookup_table)
            color_bar = pg.ColorBarItem(
                values=(0.0, 1.0),
                colorMap=color_map,
                label="log10 HFER",
                interactive=False,
            )
            color_bar.setImageItem(heatmap_image, insert_in=heatmap_plot.getPlotItem())
        onset_line = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            pen=pg.mkPen((230, 230, 230), width=1.2, style=Qt.PenStyle.DashLine),
        )
        heatmap_plot.addItem(onset_line)
        heatmap_view_box = heatmap_plot.getViewBox()
        score_view_box = score_plot.getViewBox()
        plot_splitter = QSplitter(Qt.Orientation.Horizontal)
        plot_splitter.setChildrenCollapsible(False)
        plot_splitter.addWidget(score_plot)
        plot_splitter.addWidget(heatmap_plot)
        plot_splitter.setStretchFactor(0, 0)
        plot_splitter.setStretchFactor(1, 1)
        plot_splitter.setSizes([160, 760])
        layout.addWidget(plot_splitter, 1)

        warned_missing_recruitment_metadata = {"shown": False}

        def redraw_heatmap() -> None:
            sort_mode = str(sort_combo.currentData() or "ei_score")
            top_n = int(top_n_spin.value())
            view = self._prepare_ei_heatmap_view(result, sort_mode=sort_mode, top_n=top_n)
            if view is None:
                return

            heatmap_data, times, channel_names, ei_scores, missing_delay_metadata = view
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            seizure_onset = metadata.get("seizure_onset_s")
            ictal_window = metadata.get("ictal_window_s")
            time_bounds: tuple[float, float] | None = None
            if (
                isinstance(seizure_onset, (int, float))
                and isinstance(ictal_window, (list, tuple))
                and len(ictal_window) >= 2
                and isinstance(ictal_window[0], (int, float))
                and isinstance(ictal_window[1], (int, float))
            ):
                relative_ictal_start = float(ictal_window[0]) - float(seizure_onset)
                relative_ictal_end = float(ictal_window[1]) - float(seizure_onset)
                times = times + relative_ictal_start
                if relative_ictal_end > relative_ictal_start:
                    time_bounds = (relative_ictal_start, relative_ictal_end)
            if missing_delay_metadata and sort_mode == "recruitment_delay":
                if not warned_missing_recruitment_metadata["shown"]:
                    QMessageBox.warning(
                        dialog,
                        "REI heatmap",
                        "Recruitment delay metadata is incomplete. Falling back to "
                        "REI onset from seizure onset for sorting.",
                    )
                    warned_missing_recruitment_metadata["shown"] = True

            log_heatmap = np.log10(np.maximum(heatmap_data, 1e-6))
            heatmap_image.setImage(log_heatmap, autoLevels=True)
            if color_bar is not None:
                heatmap_min = float(np.nanmin(log_heatmap))
                heatmap_max = float(np.nanmax(log_heatmap))
                if np.isfinite(heatmap_min) and np.isfinite(heatmap_max):
                    if heatmap_max <= heatmap_min:
                        heatmap_max = heatmap_min + 1.0
                    color_bar.setLevels((heatmap_min, heatmap_max))

            n_rows = int(heatmap_data.shape[0])
            if time_bounds is not None:
                x_start = float(time_bounds[0])
                width = float(time_bounds[1] - time_bounds[0])
                heatmap_view_box.setRange(
                    xRange=time_bounds,
                    padding=0.0,
                )
            elif times.size >= 2:
                dt = float(np.median(np.diff(times)))
                x_start = float(times[0]) - (0.5 * dt)
                width = float(times[-1] - times[0] + dt)
                heatmap_view_box.setRange(
                    xRange=(float(times[0]), float(times[-1])),
                    padding=0.0,
                )
            elif times.size == 1:
                dt = 1.0
                x_start = float(times[0]) - 0.5
                width = 1.0
                heatmap_view_box.setRange(
                    xRange=(float(times[0]) - 0.5, float(times[0]) + 0.5),
                    padding=0.0,
                )
            else:
                dt = 1.0
                x_start = -0.5
                width = 1.0
                heatmap_view_box.setRange(
                    xRange=(0.0, 1.0),
                    padding=0.0,
                )

            heatmap_image.setRect(QRectF(x_start, -0.5, width, max(1.0, float(n_rows))))
            heatmap_view_box.setRange(
                yRange=(-0.5, max(0.5, float(n_rows) - 0.5)),
                padding=0.0,
            )
            heatmap_plot.getAxis("left").setTicks(
                [[(row_idx, channel_name) for row_idx, channel_name in enumerate(channel_names)]]
            )

            score_plot.clear()
            y_positions = np.arange(n_rows, dtype=float)
            score_bars = pg.BarGraphItem(
                x0=np.zeros(n_rows, dtype=float),
                x1=np.asarray(ei_scores, dtype=float),
                y0=y_positions - 0.4,
                y1=y_positions + 0.4,
                brush=pg.mkBrush(86, 156, 214, 220),
                pen=pg.mkPen(None),
            )
            score_plot.addItem(score_bars)
            score_view_box.setRange(
                yRange=(-0.5, max(0.5, float(n_rows) - 0.5)),
                padding=0.0,
            )
            max_score = float(np.max(ei_scores)) if ei_scores.size else 1.0
            score_view_box.setRange(
                xRange=(0.0, max(1.0, max_score * 1.05)),
                padding=0.0,
            )

        sort_combo.currentIndexChanged.connect(lambda _idx: redraw_heatmap())
        top_n_spin.valueChanged.connect(lambda _value: redraw_heatmap())
        redraw_heatmap()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        self._ei_heatmap_dialog = dialog

    def _prepare_ei_heatmap_view(
        self,
        result: EIComputationResult,
        *,
        sort_mode: str,
        top_n: int,
    ) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray, bool] | None:
        heatmap = np.asarray(result.heatmap, dtype=float)
        if heatmap.ndim != 2 or heatmap.size == 0:
            return None

        times = np.asarray(result.heatmap_times, dtype=float)
        channel_names = list(result.heatmap_channels or [])
        n_rows = min(int(heatmap.shape[0]), len(channel_names))
        if n_rows <= 0:
            return None

        heatmap = heatmap[:n_rows, :]
        channel_names = channel_names[:n_rows]

        channel_info = {str(row.channel): row for row in result.channels}
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        has_recruitment_metadata = False

        rows: list[EIHeatmapRow] = []
        for original_idx, channel_name in enumerate(channel_names):
            channel_result = cast(EIChannelResult | None, channel_info.get(str(channel_name)))
            onset_sec = (
                float(channel_result.onset_sec_from_seizure_onset)
                if channel_result is not None
                else 0.0
            )
            ei_score = float(channel_result.ei) if channel_result is not None else 0.0
            if channel_result is not None:
                recruitment_delay, row_has_delay_metadata = self._compute_recruitment_delay(
                    channel_result,
                    metadata,
                )
                has_recruitment_metadata = has_recruitment_metadata or row_has_delay_metadata
            else:
                recruitment_delay = onset_sec
                row_has_delay_metadata = False

            row_heatmap = np.asarray(heatmap[original_idx], dtype=float)
            rows.append(
                EIHeatmapRow(
                    original_idx=int(original_idx),
                    channel_name=str(channel_name),
                    ei_score=float(ei_score),
                    recruitment_delay=float(recruitment_delay),
                    peak_hfer=float(np.max(row_heatmap)) if row_heatmap.size else 0.0,
                    mean_hfer=float(np.mean(row_heatmap)) if row_heatmap.size else 0.0,
                )
            )

        if sort_mode == "ei_score":
            rows.sort(key=lambda row: row.ei_score, reverse=True)
        elif sort_mode == "recruitment_delay":
            rows.sort(key=lambda row: row.recruitment_delay)
        elif sort_mode == "peak_hfer":
            rows.sort(key=lambda row: row.peak_hfer, reverse=True)
        elif sort_mode == "mean_hfer":
            rows.sort(key=lambda row: row.mean_hfer, reverse=True)
        elif sort_mode == "channel_name":
            rows.sort(key=lambda row: row.channel_name.lower())
        else:
            rows.sort(key=lambda row: row.original_idx)

        top_n = max(1, min(int(top_n), len(rows)))
        rows = rows[:top_n]

        selected_indices = np.asarray([row.original_idx for row in rows], dtype=int)
        selected_names = [row.channel_name for row in rows]
        selected_scores = np.asarray([row.ei_score for row in rows], dtype=float)

        return (
            heatmap[selected_indices, :],
            times,
            selected_names,
            selected_scores,
            not has_recruitment_metadata,
        )

    # ---------- Small UI helpers ----------

    def _update_channels_title(self) -> None:
        self.gb_ch.setTitle(f"Channels ({len(self.state.selected_abs)})")

    def _select_all_channels(self) -> None:
        all_abs = self._available_channel_abs()
        self.set_selected_channels_abs(all_abs, replace=True)

    def _select_group_channels(self, group: str) -> None:
        group = str(group).strip().lower()
        if group not in {"macro", "micro"}:
            return

        chosen = []
        for abs_idx in self._available_channel_abs():
            ch_name = self._ch_names_displayed[abs_idx]
            ch_group = str(self._channel_groups.get(ch_name, "macro")).strip().lower()
            if ch_group == group:
                chosen.append(abs_idx)

        self.set_selected_channels_abs(chosen, replace=True)

    def _update_group_button_titles(self) -> None:
        n_all = 0
        n_macro = 0
        n_micro = 0

        for abs_idx, ch_name in enumerate(self._ch_names_displayed):
            if self._is_bad_abs_idx(abs_idx):
                continue
            n_all += 1
            group = str(self._channel_groups.get(ch_name, "macro")).strip().lower()
            if group == "micro":
                n_micro += 1
            else:
                n_macro += 1

        self.btn_sel_all.setText(f"All ({n_all})")
        self.btn_sel_macro.setText(f"Macro ({n_macro})")
        self.btn_sel_micro.setText(f"Micro ({n_micro})")
