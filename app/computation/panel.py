# app/computation/panel.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import json
import re
import time
from typing import Any, Callable, Literal, Optional, TypedDict, cast

import numpy as np
import pyqtgraph as pg
from scipy import signal
from mne.io import BaseRaw

from PySide6.QtCore import Qt, Slot, Signal, QRectF, QObject, QThread, QTimer, QSettings
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog,
    QDialogButtonBox, QLineEdit, QSizePolicy, QButtonGroup, QAbstractButton,
    QFormLayout, QFrame, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QSpinBox, QGridLayout, QScrollArea, QGraphicsRectItem,
    QSplitter, QFileDialog, QMainWindow, QStatusBar,
)

from app.viewer.time_controls import TimeWindowControl
from app.computation.rei.algorithm import (
    DEFAULT_REI_HIGH_FREQ_HZ,
    DEFAULT_REI_LOW_FREQ_HZ,
    EIChannelResult,
    EIComputationResult,
    compute_ei_for_gui,
    validate_gui_ei_timing,
)
from app.computation.gamma_spike.wire_algorithm import (
    GammaSpikeCancelled,
    GammaSpikeComputationResult,
    GammaSpikeEventResult,
    compute_gamma_spike_segmented_for_gui,
)
from app.computation.hfo import HFOComputationResult, compute_hfo_for_gui
from app.computation.hfo.algorithm import (
    HFO_CLASSIFIER_EHFO,
    HFO_CLASSIFIER_PYHFO_OMNI_LEGACY,
    HFO_CLASSIFIER_PYHFO_PYBRAIN,
)
from app.computation.hfo.detectors.omni_hfo_detector import DEFAULT_CANDIDATE_DETECTORS, OMNI_TARGET_FS_HZ
from app.computation.exporters import (
    export_ei_result,
    export_gamma_spike_result,
    export_hfo_result,
)
from app.computation.importers import ImportedComputationResult, import_computation_result
from app.expert_event_grid import ExpertEvent, ExpertEventGrid
from app.diagnostics.performance_monitor import timed_mark
from app.preprocessing.filtering import NOTCH_OFF
from app.ui_busy import busy_cursor

DEFAULT_HFO_BAND_PRESET = "Default"
RIPPLE_HFO_BAND_PRESET = "Ripples"
FAST_RIPPLE_HFO_BAND_PRESET = "Fast ripples"
CUSTOM_HFO_BAND_PRESET = "Custom"
HFO_BAND_PRESETS: dict[str, tuple[float, float] | None] = {
    DEFAULT_HFO_BAND_PRESET: None,
    RIPPLE_HFO_BAND_PRESET: (80.0, 250.0),
    FAST_RIPPLE_HFO_BAND_PRESET: (250.0, 500.0),
    CUSTOM_HFO_BAND_PRESET: None,
}
DISABLED_HFO_BAND_PRESETS: set[str] = set()
HFO_DEFAULT_BANDS_BY_CLASSIFIER: dict[str, tuple[float, float]] = {
    HFO_CLASSIFIER_PYHFO_PYBRAIN: (80.0, 500.0),
    HFO_CLASSIFIER_PYHFO_OMNI_LEGACY: (80.0, 300.0),
    HFO_CLASSIFIER_EHFO: (80.0, 300.0),
}
HFO_DETECTOR_VERSIONS: tuple[str, ...] = (
    HFO_CLASSIFIER_PYHFO_PYBRAIN,
    HFO_CLASSIFIER_PYHFO_OMNI_LEGACY,
    HFO_CLASSIFIER_EHFO,
)
DEFAULT_HFO_DETECTOR_VERSION = HFO_CLASSIFIER_PYHFO_PYBRAIN
HFO_CLASSIFIER_DISPLAY_NAMES: dict[str, str] = {
    classifier: f"{classifier}-{low:g}-{high:g} Hz"
    for classifier, (low, high) in HFO_DEFAULT_BANDS_BY_CLASSIFIER.items()
}
HFO_BAND_PRESET_DISPLAY_NAMES: dict[str, str] = {
    DEFAULT_HFO_BAND_PRESET: "Default",
    RIPPLE_HFO_BAND_PRESET: "Ripples 80-250 Hz",
    FAST_RIPPLE_HFO_BAND_PRESET: "Fast ripples 250-500 Hz",
    CUSTOM_HFO_BAND_PRESET: "Custom",
}
DISABLED_HFO_CLASSIFIER_OPTIONS: set[str] = set()
HFO_DISPLAY_CLASSES = (
    "artifact",
    "HFO",
    "non-spike HFO",
    "spike-HFO",
    "eHFO",
    "spike-eHFO",
    "unclassified",
    "deleted",
)
HFO_GUI_SETTINGS_ORG = "EpilepsyTools"
HFO_GUI_SETTINGS_APP = "I_EEG"
HFO_GUI_SETTINGS_KEY = "hfo/advanced_defaults"


def _hfo_default_band_for_classifier(classifier_name: object) -> tuple[float, float]:
    return HFO_DEFAULT_BANDS_BY_CLASSIFIER.get(
        str(classifier_name),
        HFO_DEFAULT_BANDS_BY_CLASSIFIER[DEFAULT_HFO_DETECTOR_VERSION],
    )


def _hfo_band_for_preset(preset: object, classifier_name: object) -> tuple[float, float] | None:
    preset_text = _normalize_hfo_band_preset_name(preset)
    if preset_text == DEFAULT_HFO_BAND_PRESET:
        return _hfo_default_band_for_classifier(classifier_name)
    return HFO_BAND_PRESETS.get(preset_text)


def _normalize_hfo_band_preset_name(preset: object) -> str:
    text = str(preset or "").strip()
    lowered = text.lower().replace("_", " ").replace("-", " ")
    compact = re.sub(r"\s+", " ", lowered).strip()
    if not compact:
        return DEFAULT_HFO_BAND_PRESET
    if compact in {"default", "hfo 80 300 hz", "pyhfo pybrain 80 500 hz"}:
        return DEFAULT_HFO_BAND_PRESET
    if compact in {"ripple", "ripples", "ripple 80 250 hz", "ripples 80 250 hz"}:
        return RIPPLE_HFO_BAND_PRESET
    if compact in {
        "fast ripple",
        "fast ripples",
        "fast ripple 250 500 hz",
        "fast ripples 250 500 hz",
    }:
        return FAST_RIPPLE_HFO_BAND_PRESET
    if compact == "custom":
        return CUSTOM_HFO_BAND_PRESET
    return text if text in HFO_BAND_PRESETS else CUSTOM_HFO_BAND_PRESET


def _normalize_hfo_classifier_name(classifier_name: object) -> str:
    text = str(classifier_name or "").strip()
    if text in HFO_DETECTOR_VERSIONS:
        return text
    for internal_name, display_name in HFO_CLASSIFIER_DISPLAY_NAMES.items():
        if text == display_name:
            return internal_name
    for internal_name in HFO_DETECTOR_VERSIONS:
        if text.startswith(f"{internal_name}-"):
            return internal_name
    return DEFAULT_HFO_DETECTOR_VERSION


def _set_combo_current_data(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


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
    hfo_start_s: float | None = None
    hfo_end_s: float | None = None


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
    model_class: str
    manual_class: str | None
    manual_review_status: str
    source_event: GammaSpikeEventResult
    error: str | None


class _GammaSpikeWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, float)
    failed = Signal(str, float)
    cancelled = Signal(float)

    def __init__(
        self,
        compute_callback: Callable[[Callable[[str], None]], GammaSpikeComputationResult],
    ) -> None:
        super().__init__()
        self._compute_callback = compute_callback
        self._cancel_requested = False

    @Slot()
    def run(self) -> None:
        perf_start = time.perf_counter()
        try:
            result = self._compute_callback(self._report_progress)
        except GammaSpikeCancelled:
            self.cancelled.emit(time.perf_counter() - perf_start)
        except Exception as exc:
            self.failed.emit(str(exc), time.perf_counter() - perf_start)
        else:
            self.finished.emit(result, time.perf_counter() - perf_start)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _report_progress(self, message: str) -> None:
        if self._cancel_requested:
            raise GammaSpikeCancelled()
        self.progress.emit(str(message))
        if self._cancel_requested:
            raise GammaSpikeCancelled()


class _HFOWorker(QObject):
    finished = Signal(object, float)
    failed = Signal(str, float)
    cancelled = Signal(float)

    def __init__(
        self,
        compute_callback: Callable[[Callable[[], None]], HFOComputationResult],
    ) -> None:
        super().__init__()
        self._compute_callback = compute_callback
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise GammaSpikeCancelled()

    @Slot()
    def run(self) -> None:
        perf_start = time.perf_counter()
        try:
            self._raise_if_cancelled()
            result = self._compute_callback(self._raise_if_cancelled)
            self._raise_if_cancelled()
        except GammaSpikeCancelled:
            self.cancelled.emit(time.perf_counter() - perf_start)
        except Exception as exc:
            self.failed.emit(str(exc), time.perf_counter() - perf_start)
        else:
            self.finished.emit(result, time.perf_counter() - perf_start)


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
    non_gamma_spikes: int
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

    def mousePressEvent(self, event: QMouseEvent) -> None:
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
    hfoMarkersChanged = Signal(dict)  # display channel name -> [{time_s, kind, event_id}]
    hfoEventActivated = Signal(str, float)  # channel name, absolute time_s

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(240, 220)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self._raw: BaseRaw | None = None
        self._picks: np.ndarray | None = None           # abs_idx -> raw_idx
        self._source_file_path: Path | None = None
        self._ch_names_displayed: list[str] = []        # abs_idx -> display name
        self._channel_groups: dict[str, str] = {}   # display_name -> "macro" | "micro"
        self._bad_names: set[str] = set()
        self._current_montage_callback: Callable[[], str] | None = None
        self._switch_to_bipolar_callback: Callable[[], tuple[bool, str]] | None = None
        self._ei_filter_callback: Callable[[], dict[str, str]] | None = None
        self._ei_data_callback: Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]] | None = None
        self._ei_data_snapshot_callback: Callable[
            [],
            Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]],
        ] | None = None
        self.ei_result_metadata: dict | None = None
        self._last_ei_result: EIComputationResult | None = None
        self._ei_summary_dialog: QDialog | None = None
        self._ei_heatmap_dialog: QDialog | None = None
        self._ei_summary_table: QTableWidget | None = None
        self._ei_summary_row_by_channel: dict[str, int] = {}
        self._last_gamma_result: GammaSpikeComputationResult | None = None
        self._gamma_summary_dialog: QDialog | None = None
        self._gamma_review_dialog: QDialog | None = None
        self._hfo_summary_dialog: QDialog | None = None
        self._hfo_event_grid_dialog: QDialog | None = None
        self._last_hfo_result: HFOComputationResult | None = None
        self._pending_hfo_event_selection: tuple[str, float] | None = None
        self._pending_gamma_review_selection: tuple[str, float] | None = None
        self._last_export_dir: Path | None = None
        self._gamma_cancel_requested = False
        self._gamma_thread: QThread | None = None
        self._gamma_worker: _GammaSpikeWorker | None = None
        self._gamma_perf_start: float | None = None
        self._gamma_run_window_s: tuple[float, float] | None = None
        self._gamma_completion_status_active = False
        self._hfo_thread: QThread | None = None
        self._hfo_worker: _HFOWorker | None = None
        self._hfo_status_timer: QTimer | None = None
        self._hfo_perf_start: float | None = None
        self._hfo_run_window_s: tuple[float, float] | None = None
        self._hfo_cancel_requested = False
        self._hfo_expected_runtime_s: float | None = None
        self._hfo_runtime_complexity: float | None = None
        self._hfo_seconds_per_complexity_unit: float | None = None

        self.state = PanelState(selected_abs=[], t0=0.0, win=5.0, link_time=True)
        self._gamma_default_window_applied = False
        self._hfo_default_window_applied = False
        self.ei_params = {
            "expected_reference": "raw_or_bipolar",
            "exclude_bad_channels": True,
            "use_display_filter": False,
            "analysis_filter": "butterworth_bandpass",
            "filter_order": 4,
            "low_freq": DEFAULT_REI_LOW_FREQ_HZ,
            "high_freq": DEFAULT_REI_HIGH_FREQ_HZ,
            "zero_phase": True,
            "notch_filter": False,
            "line_freq": 60.0,
            "threshold_sigma": 10.0,
            "energy_window_sec": 0.5,
            "hfer_window_sec": 0.25,
        }
        default_hfo_low, default_hfo_high = _hfo_default_band_for_classifier(DEFAULT_HFO_DETECTOR_VERSION)
        self.hfo_params = {
            "detector_version": DEFAULT_HFO_DETECTOR_VERSION,
            "active_candidate_detectors": list(DEFAULT_CANDIDATE_DETECTORS),
            "band_preset": DEFAULT_HFO_BAND_PRESET,
            "low_freq": default_hfo_low,
            "high_freq": default_hfo_high,
            "threshold_sigma": 5.0,
            "min_duration_ms": 6.0,
            "max_duration_ms": 500.0,
            "boundary_padding_s": 1.0,
            "merge_gap_ms": 10.0,
            "min_cycles": 6.0,
            "detector_parameters": {
                "ste": {
                    "rms_window_s": 0.003,
                    "min_window_s": 0.006,
                    "min_gap_s": 0.010,
                    "epoch_len": 600,
                    "min_osc": 6,
                    "rms_thres": 5.0,
                    "peak_thres": 3.0,
                },
                "mni": {
                    "epoch_time_s": 10.0,
                    "epo_chf_hz": 60.0,
                    "per_chf": 0.95,
                    "min_win_s": 0.010,
                    "min_gap_s": 0.010,
                    "threshold_percentile": 0.999999,
                    "base_seg_s": 0.125,
                    "base_shift_s": 0.5,
                    "base_threshold": 0.67,
                    "base_min": 5,
                },
                "hilbert": {
                    "sd_threshold": 5.0,
                    "min_window_s": 0.010,
                    "epoch_len_s": 3600.0,
                },
            },
            "notch_behavior": "Uses active notch if enabled",
            "output_model": "expert_event_grid",
        }
        self._built_in_hfo_params = deepcopy(self.hfo_params)

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

        self.btn_algo_hfo = QPushButton("HFO")
        self.btn_algo_hfo.setCheckable(True)
        self.btn_algo_hfo.setProperty("algorithm", "hfo")

        self.algo_buttons.addButton(self.btn_algo_ei)
        self.algo_buttons.addButton(self.btn_algo_gamma)
        self.algo_buttons.addButton(self.btn_algo_hfo)
        algo_row.addWidget(self.btn_algo_ei)
        algo_row.addWidget(self.btn_algo_gamma)
        algo_row.addWidget(self.btn_algo_hfo)
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
            "editable zero-phase Butterworth bandpass filter is applied."
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
        self.edit_ei_low_freq = QDoubleSpinBox()
        self.edit_ei_high_freq = QDoubleSpinBox()
        for spin, value in (
            (self.edit_ei_low_freq, float(self.ei_params["low_freq"])),
            (self.edit_ei_high_freq, float(self.ei_params["high_freq"])),
        ):
            spin.setRange(1.0, 10_000.0)
            spin.setDecimals(1)
            spin.setSingleStep(5.0)
            spin.setSuffix(" Hz")
            spin.setValue(float(value))
        preprocessing_form.addRow("Low frequency:", self.edit_ei_low_freq)
        preprocessing_form.addRow("High frequency:", self.edit_ei_high_freq)
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

        self.hfo_time_widget = QWidget()
        hfo_time_layout = QVBoxLayout(self.hfo_time_widget)
        hfo_time_layout.setContentsMargins(0, 0, 0, 0)
        hfo_time_layout.setSpacing(8)

        hfo_info_box = QGroupBox("High Frequency Oscillation (HFO) setup")
        hfo_info_layout = QVBoxLayout(hfo_info_box)
        hfo_time_layout.addWidget(hfo_info_box)

        hfo_window_form = QFormLayout()
        self.edit_hfo_start = QLineEdit()
        self.edit_hfo_start.setPlaceholderText("seconds")
        self.edit_hfo_end = QLineEdit()
        self.edit_hfo_end.setPlaceholderText("seconds")
        hfo_window_form.addRow("Analysis start (s):", self.edit_hfo_start)
        hfo_window_form.addRow("Analysis end (s):", self.edit_hfo_end)
        hfo_time_layout.addLayout(hfo_window_form)

        hfo_detector_form = QFormLayout()
        self.combo_hfo_detector_version = QComboBox()
        for detector_version in HFO_DETECTOR_VERSIONS:
            self.combo_hfo_detector_version.addItem(
                HFO_CLASSIFIER_DISPLAY_NAMES.get(detector_version, detector_version),
                userData=detector_version,
            )
        _set_combo_current_data(self.combo_hfo_detector_version, DEFAULT_HFO_DETECTOR_VERSION)
        self._disable_unvalidated_hfo_classifier_options()
        hfo_detector_form.addRow("Classifier:", self.combo_hfo_detector_version)
        hfo_time_layout.addLayout(hfo_detector_form)

        hfo_band_form = QFormLayout()
        self.combo_hfo_band_preset = QComboBox()
        for preset_name in HFO_BAND_PRESETS:
            self.combo_hfo_band_preset.addItem(
                HFO_BAND_PRESET_DISPLAY_NAMES.get(preset_name, preset_name),
                userData=preset_name,
            )
        self._disable_unvalidated_hfo_band_presets()
        _set_combo_current_data(self.combo_hfo_band_preset, DEFAULT_HFO_BAND_PRESET)
        hfo_band_form.addRow("Band preset:", self.combo_hfo_band_preset)
        hfo_time_layout.addLayout(hfo_band_form)

        self.btn_hfo_advanced = QPushButton("Advanced parameters...")
        hfo_time_layout.addWidget(self.btn_hfo_advanced)

        self.hfo_advanced_frame = QFrame()
        hfo_advanced_layout = QVBoxLayout(self.hfo_advanced_frame)
        hfo_advanced_layout.setContentsMargins(0, 0, 0, 0)
        hfo_advanced_layout.setSpacing(8)

        hfo_filter_box = QGroupBox("HFO detection")
        hfo_filter_form = QFormLayout(hfo_filter_box)
        self.edit_hfo_low_freq = QDoubleSpinBox()
        self.edit_hfo_high_freq = QDoubleSpinBox()
        for spin, value in (
            (self.edit_hfo_low_freq, float(self.hfo_params["low_freq"])),
            (self.edit_hfo_high_freq, float(self.hfo_params["high_freq"])),
        ):
            spin.setRange(1.0, 10_000.0)
            spin.setDecimals(1)
            spin.setSingleStep(5.0)
            spin.setSuffix(" Hz")
            spin.setValue(float(value))
        hfo_filter_form.addRow("Low frequency:", self.edit_hfo_low_freq)
        hfo_filter_form.addRow("High frequency:", self.edit_hfo_high_freq)
        hfo_filter_form.addRow("Notch handling:", QLabel("Uses active notch if enabled"))
        hfo_advanced_layout.addWidget(hfo_filter_box)

        hfo_detection_box = QGroupBox("Detector parameters")
        hfo_detection_form = QFormLayout(hfo_detection_box)
        self.chk_hfo_ste = QCheckBox("STE")
        self.chk_hfo_mni = QCheckBox("MNI")
        self.chk_hfo_hilbert = QCheckBox("Hilbert")
        for checkbox in (self.chk_hfo_ste, self.chk_hfo_mni, self.chk_hfo_hilbert):
            checkbox.setChecked(True)
        detector_row = QHBoxLayout()
        detector_row.addWidget(self.chk_hfo_ste)
        detector_row.addWidget(self.chk_hfo_mni)
        detector_row.addWidget(self.chk_hfo_hilbert)
        detector_row.addStretch(1)
        hfo_detection_form.addRow("Candidate detectors:", detector_row)
        self.edit_hfo_threshold_sigma = QDoubleSpinBox()
        self.edit_hfo_threshold_sigma.setRange(0.1, 100.0)
        self.edit_hfo_threshold_sigma.setDecimals(1)
        self.edit_hfo_threshold_sigma.setSingleStep(0.5)
        self.edit_hfo_threshold_sigma.setValue(float(self.hfo_params["threshold_sigma"]))
        self.edit_hfo_min_duration = QDoubleSpinBox()
        self.edit_hfo_min_duration.setRange(1.0, 10_000.0)
        self.edit_hfo_min_duration.setDecimals(1)
        self.edit_hfo_min_duration.setSingleStep(1.0)
        self.edit_hfo_min_duration.setSuffix(" ms")
        self.edit_hfo_min_duration.setValue(float(self.hfo_params["min_duration_ms"]))
        self.edit_hfo_max_duration = QDoubleSpinBox()
        self.edit_hfo_max_duration.setRange(1.0, 60_000.0)
        self.edit_hfo_max_duration.setDecimals(1)
        self.edit_hfo_max_duration.setSingleStep(10.0)
        self.edit_hfo_max_duration.setSuffix(" ms")
        self.edit_hfo_max_duration.setValue(float(self.hfo_params["max_duration_ms"]))
        self.edit_hfo_boundary_padding = QDoubleSpinBox()
        self.edit_hfo_boundary_padding.setRange(0.0, 10.0)
        self.edit_hfo_boundary_padding.setDecimals(3)
        self.edit_hfo_boundary_padding.setSingleStep(0.5)
        self.edit_hfo_boundary_padding.setSuffix(" s")
        self.edit_hfo_boundary_padding.setValue(float(self.hfo_params["boundary_padding_s"]))
        self.edit_hfo_merge_gap = QDoubleSpinBox()
        self.edit_hfo_merge_gap.setRange(0.0, 10_000.0)
        self.edit_hfo_merge_gap.setDecimals(1)
        self.edit_hfo_merge_gap.setSingleStep(1.0)
        self.edit_hfo_merge_gap.setSuffix(" ms")
        self.edit_hfo_merge_gap.setValue(float(self.hfo_params["merge_gap_ms"]))
        self.edit_hfo_min_cycles = QDoubleSpinBox()
        self.edit_hfo_min_cycles.setRange(1.0, 100.0)
        self.edit_hfo_min_cycles.setDecimals(1)
        self.edit_hfo_min_cycles.setSingleStep(0.5)
        self.edit_hfo_min_cycles.setValue(float(self.hfo_params["min_cycles"]))
        hfo_detection_form.addRow("Threshold sigma:", self.edit_hfo_threshold_sigma)
        hfo_detection_form.addRow("Minimum duration:", self.edit_hfo_min_duration)
        hfo_detection_form.addRow("Maximum duration:", self.edit_hfo_max_duration)
        hfo_detection_form.addRow("Ignore edges:", self.edit_hfo_boundary_padding)
        hfo_detection_form.addRow("Merge gap:", self.edit_hfo_merge_gap)
        hfo_detection_form.addRow("Minimum cycles:", self.edit_hfo_min_cycles)
        self._hfo_detector_param_spins: dict[tuple[str, str], QDoubleSpinBox | QSpinBox] = {}
        self._hfo_detector_param_boxes: dict[str, QGroupBox] = {}
        hfo_advanced_layout.addWidget(hfo_detection_box)
        self._add_hfo_detector_parameter_box(
            hfo_advanced_layout,
            "STE parameters",
            "ste",
            [
                ("rms_window_s", "RMS window", " s", 0.001, 10.0, 4, 0.001, "float"),
                ("min_window_s", "Minimum window", " s", 0.001, 10.0, 4, 0.001, "float"),
                ("min_gap_s", "Minimum gap", " s", 0.0, 10.0, 4, 0.001, "float"),
                ("epoch_len", "Epoch length", " s", 1, 100000, 0, 1, "int"),
                ("min_osc", "Minimum oscillations", "", 1, 100, 0, 1, "int"),
                ("rms_thres", "RMS threshold", "", 0.1, 100.0, 2, 0.5, "float"),
                ("peak_thres", "Peak threshold", "", 0.1, 100.0, 2, 0.5, "float"),
            ],
        )
        self._add_hfo_detector_parameter_box(
            hfo_advanced_layout,
            "MNI parameters",
            "mni",
            [
                ("epoch_time_s", "Epoch time", " s", 0.1, 100000.0, 2, 1.0, "float"),
                ("epo_chf_hz", "Epoch CHF", " Hz", 0.1, 10000.0, 2, 1.0, "float"),
                ("per_chf", "Percent CHF", "", 0.0001, 1.0, 4, 0.01, "float"),
                ("min_win_s", "Minimum window", " s", 0.001, 10.0, 4, 0.001, "float"),
                ("min_gap_s", "Minimum gap", " s", 0.0, 10.0, 4, 0.001, "float"),
                ("threshold_percentile", "Threshold percentile", "", 0.000001, 0.999999, 6, 0.000001, "float"),
                ("base_seg_s", "Baseline segment", " s", 0.001, 10.0, 4, 0.001, "float"),
                ("base_shift_s", "Baseline shift", " s", 0.0, 10.0, 4, 0.01, "float"),
                ("base_threshold", "Baseline threshold", "", 0.0, 10.0, 3, 0.01, "float"),
                ("base_min", "Baseline minimum", "", 0, 1000, 0, 1, "int"),
            ],
        )
        self._add_hfo_detector_parameter_box(
            hfo_advanced_layout,
            "Hilbert parameters",
            "hilbert",
            [
                ("sd_threshold", "SD threshold", "", 0.1, 100.0, 2, 0.5, "float"),
                ("min_window_s", "Minimum window", " s", 0.001, 10.0, 4, 0.001, "float"),
                ("epoch_len_s", "Epoch length", " s", 1.0, 100000.0, 1, 60.0, "float"),
            ],
        )
        self._lock_hfo_legacy_parameter_controls()

        self.hfo_advanced_dialog: QDialog | None = None
        self.hfo_time_widget.hide()
        t_layout.addWidget(self.hfo_time_widget)

        root.addWidget(self.gb_t, 0)

        # --- Output actions ---
        gb_p = QGroupBox("Output")
        p_layout = QVBoxLayout(gb_p)

        self.btn_run = QPushButton("Run REI")
        p_layout.addWidget(self.btn_run)

        self.btn_cancel_gamma = QPushButton("Cancel gamma run")
        self.btn_cancel_gamma.setEnabled(False)
        self.btn_cancel_gamma.hide()
        p_layout.addWidget(self.btn_cancel_gamma)

        self.btn_cancel_hfo = QPushButton("Cancel HFO run")
        self.btn_cancel_hfo.setEnabled(False)
        self.btn_cancel_hfo.hide()
        p_layout.addWidget(self.btn_cancel_hfo)

        self.btn_import_results = QPushButton("Import results...")
        p_layout.addWidget(self.btn_import_results)

        self.btn_open_ei_summary = QPushButton("Open REI summary")
        self.btn_open_ei_summary.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_summary)

        self.btn_open_ei_heatmap = QPushButton("Open REI heatmap")
        self.btn_open_ei_heatmap.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_heatmap)

        self.btn_export_ei = QPushButton("Export REI results")
        self.btn_export_ei.setEnabled(False)
        p_layout.addWidget(self.btn_export_ei)

        self.btn_open_gamma_summary = QPushButton("Open channel-level summary")
        self.btn_open_gamma_summary.setEnabled(False)
        self.btn_open_gamma_summary.hide()
        p_layout.addWidget(self.btn_open_gamma_summary)

        self.btn_open_gamma_review = QPushButton("Open spike grid")
        self.btn_open_gamma_review.setEnabled(False)
        self.btn_open_gamma_review.hide()
        p_layout.addWidget(self.btn_open_gamma_review)

        self.btn_export_gamma = QPushButton("Export gamma results")
        self.btn_export_gamma.setEnabled(False)
        self.btn_export_gamma.hide()
        p_layout.addWidget(self.btn_export_gamma)

        self.btn_open_hfo_summary = QPushButton("Open HFO summary")
        self.btn_open_hfo_summary.setEnabled(False)
        self.btn_open_hfo_summary.hide()
        p_layout.addWidget(self.btn_open_hfo_summary)

        self.btn_open_hfo_event_grid = QPushButton("Open HFO event grid")
        self.btn_open_hfo_event_grid.setEnabled(False)
        self.btn_open_hfo_event_grid.hide()
        p_layout.addWidget(self.btn_open_hfo_event_grid)

        self.btn_export_hfo = QPushButton("Export HFO events")
        self.btn_export_hfo.setEnabled(False)
        self.btn_export_hfo.hide()
        p_layout.addWidget(self.btn_export_hfo)

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
        self.btn_hfo_advanced.clicked.connect(self._open_hfo_advanced_dialog)
        self.edit_hfo_start.textChanged.connect(self._on_hfo_window_text_changed)
        self.edit_hfo_end.textChanged.connect(self._on_hfo_window_text_changed)
        self.combo_hfo_detector_version.currentTextChanged.connect(
            self._on_hfo_detector_version_changed
        )
        self.combo_hfo_band_preset.currentTextChanged.connect(self._on_hfo_band_preset_changed)
        self.edit_hfo_low_freq.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_high_freq.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_threshold_sigma.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_min_duration.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_max_duration.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_boundary_padding.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_merge_gap.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_hfo_min_cycles.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        for spin in getattr(self, "_hfo_detector_param_spins", {}).values():
            spin.valueChanged.connect(self._on_hfo_advanced_parameter_changed)
        self.chk_hfo_ste.toggled.connect(self._on_hfo_advanced_parameter_changed)
        self.chk_hfo_mni.toggled.connect(self._on_hfo_advanced_parameter_changed)
        self.chk_hfo_hilbert.toggled.connect(self._on_hfo_advanced_parameter_changed)
        self.edit_ei_low_freq.valueChanged.connect(self._on_ei_frequency_changed)
        self.edit_ei_high_freq.valueChanged.connect(self._on_ei_frequency_changed)
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
        self.btn_cancel_gamma.clicked.connect(self._cancel_gamma_run)
        self.btn_cancel_hfo.clicked.connect(self._cancel_hfo_run)
        self.btn_import_results.clicked.connect(self._import_algorithm_results)
        self.btn_open_ei_summary.clicked.connect(self._open_ei_summary_dialog)
        self.btn_open_ei_heatmap.clicked.connect(self._open_ei_heatmap_dialog)
        self.btn_export_ei.clicked.connect(self._export_ei_results)
        self.btn_open_gamma_summary.clicked.connect(self._open_gamma_summary_dialog)
        self.btn_open_gamma_review.clicked.connect(self._open_gamma_review_dialog)
        self.btn_export_gamma.clicked.connect(self._export_gamma_results)
        self.btn_open_hfo_summary.clicked.connect(self._open_hfo_summary_dialog)
        self.btn_open_hfo_event_grid.clicked.connect(self._open_hfo_event_grid_dialog)
        self.btn_export_hfo.clicked.connect(self._export_hfo_results)

        self.btn_sel_all.clicked.connect(self._select_all_channels)
        self.btn_sel_macro.clicked.connect(lambda: self._select_group_channels("macro"))
        self.btn_sel_micro.clicked.connect(lambda: self._select_group_channels("micro"))
        self.gb_ch.toggled.connect(self._sync_section_visibility)
        self.gb_t.toggled.connect(self._sync_section_visibility)

        self._load_saved_hfo_gui_defaults()
        self._on_algorithm_button_clicked(self.btn_algo_ei)

    # ---------- Public API used by MainWindow ----------

    def _add_hfo_detector_parameter_box(
        self,
        parent_layout: QVBoxLayout,
        title: str,
        detector_key: str,
        fields: list[tuple[str, str, str, float, float, int, float, str]],
    ) -> None:
        box = QGroupBox(title)
        self._hfo_detector_param_boxes[str(detector_key)] = box
        form = QFormLayout(box)
        detector_params = self.hfo_params.get("detector_parameters", {})
        if not isinstance(detector_params, dict):
            detector_params = {}
        values = detector_params.get(detector_key, {})
        if not isinstance(values, dict):
            values = {}
        for key, label, suffix, minimum, maximum, decimals, step, kind in fields:
            if kind == "int":
                spin: QDoubleSpinBox | QSpinBox = QSpinBox()
                spin.setRange(int(minimum), int(maximum))
                spin.setSingleStep(max(1, int(step)))
                try:
                    spin.setValue(int(round(float(values.get(key, minimum)))))
                except (TypeError, ValueError):
                    spin.setValue(int(minimum))
            else:
                spin = QDoubleSpinBox()
                spin.setRange(float(minimum), float(maximum))
                spin.setDecimals(int(decimals))
                spin.setSingleStep(float(step))
                try:
                    spin.setValue(float(values.get(key, minimum)))
                except (TypeError, ValueError):
                    spin.setValue(float(minimum))
            if suffix:
                spin.setSuffix(str(suffix))
            spin.setMinimumWidth(130)
            if key in {"epoch_len", "epoch_time_s", "epoch_len_s"}:
                spin.setToolTip(
                    "Detector cycle/chunk length. It may be longer than the selected analysis interval."
                )
            else:
                spin.setToolTip("Detector-specific parameter. Defaults match the current validated setup.")
            self._hfo_detector_param_spins[(str(detector_key), str(key))] = spin
            form.addRow(label + ":", spin)
        parent_layout.addWidget(box)

    def set_data_context(
        self,
        raw: BaseRaw | None,
        picks: np.ndarray | None,
        displayed_names: list[str],
        channel_groups: dict[str, str] | None = None,
        bad_names: list[str] | set[str] | None = None,
        source_file_path: Path | str | None = None,
    ) -> None:
        self._raw = raw
        self._picks = picks
        self._source_file_path = Path(source_file_path) if source_file_path else None
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
        self._clear_ei_outputs()
        if self.state.algorithm == "gamma_spike":
            self._set_gamma_window_to_full_recording(emit=True)
        if self.state.algorithm == "hfo":
            self._set_hfo_window_to_full_recording(emit=True)
        self._clear_gamma_outputs()
        self._clear_hfo_outputs()
        
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
        self._clear_ei_outputs()
        self._clear_gamma_outputs()
        self._clear_hfo_outputs()
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

    def hideEvent(self, event) -> None:
        if self._gamma_completion_status_active:
            self._clear_status_message()
            self._gamma_completion_status_active = False
        super().hideEvent(event)

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

    def set_ei_data_snapshot_callback(
        self,
        callback: Callable[
            [],
            Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]],
        ],
    ) -> None:
        self._ei_data_snapshot_callback = callback

    def project_state(self) -> dict:
        self._sync_ei_windows_from_ui(emit=False)
        self._sync_ei_frequency_from_ui(emit=False)
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
            "hfo": {
                "analysis_start_s": self._parse_float_text(self.edit_hfo_start),
                "analysis_end_s": self._parse_float_text(self.edit_hfo_end),
                "params": dict(self.hfo_params),
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
        hfo = data.get("hfo", {})
        if not isinstance(hfo, dict):
            hfo = {}
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

        for edit, value in (
            (self.edit_hfo_start, hfo.get("analysis_start_s", 0.0)),
            (
                self.edit_hfo_end,
                hfo.get(
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

        saved_params = ei.get("params")
        if isinstance(saved_params, dict):
            saved_low = saved_params.get("low_freq")
            saved_high = saved_params.get("high_freq")
            if self._is_legacy_default_ei_frequency(saved_low, saved_high):
                saved_params = {
                    **saved_params,
                    "low_freq": DEFAULT_REI_LOW_FREQ_HZ,
                    "high_freq": DEFAULT_REI_HIGH_FREQ_HZ,
                }
            for key, spin in (
                ("low_freq", self.edit_ei_low_freq),
                ("high_freq", self.edit_ei_high_freq),
            ):
                value = saved_params.get(key)
                if isinstance(value, (int, float)):
                    self.ei_params[key] = float(value)
                    spin.blockSignals(True)
                    spin.setValue(float(value))
                    spin.blockSignals(False)
        self._sync_ei_frequency_from_ui(emit=False)

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
        saved_hfo_params = hfo.get("params")
        if isinstance(saved_hfo_params, dict):
            self._restore_hfo_params(saved_hfo_params)
        self.state.hfo_start_s = self._parse_float_text(self.edit_hfo_start)
        self.state.hfo_end_s = self._parse_float_text(self.edit_hfo_end)
        self._sync_ei_windows_from_ui(emit=False)
        saved_metadata = ei.get("last_result_metadata")
        self.ei_result_metadata = saved_metadata if isinstance(saved_metadata, dict) else None

        algorithm = str(data.get("algorithm", self.state.algorithm) or "ei")
        if algorithm == "mean":
            algorithm = "ei"
        button_by_algorithm = {
            "ei": self.btn_algo_ei,
            "gamma_spike": self.btn_algo_gamma,
            "hfo": self.btn_algo_hfo,
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
            if item is None:
                continue
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
                if item is None:
                    continue
                item.setHidden(text not in item.text().lower())

        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        search.textChanged.connect(apply_filter)
        if ok_button is not None:
            search.returnPressed.connect(ok_button.click)

        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        current = set(self.state.selected_abs)
        for i in range(lst.count()):
            item = lst.item(i)
            if item is None:
                continue
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
            tolerance_s = max(1e-9, 0.5 / self._sampling_frequency_hz())
            if start_s < 0.0 or end_s > total_s + tolerance_s:
                raise ValueError("Gamma analysis window must stay inside the recording.")
            end_s = min(float(end_s), float(total_s))
        self.state.gamma_start_s = float(start_s)
        self.state.gamma_end_s = float(end_s)
        return float(start_s), float(end_s)

    def _set_hfo_window_to_full_recording(self, *, emit: bool = True) -> None:
        total_s = self._total_duration_s()
        if total_s is None:
            self._set_hfo_window_fields(0.0, max(1.0, float(self.state.win)), emit=emit)
            return
        self._set_hfo_window_fields(0.0, float(total_s), emit=emit)

    def _set_hfo_window_fields(self, start_s: float, end_s: float, *, emit: bool = True) -> None:
        self.edit_hfo_start.blockSignals(True)
        self.edit_hfo_start.setText(f"{start_s:g}")
        self.edit_hfo_start.blockSignals(False)
        self.edit_hfo_end.blockSignals(True)
        self.edit_hfo_end.setText(f"{end_s:g}")
        self.edit_hfo_end.blockSignals(False)
        self.state.hfo_start_s = float(start_s)
        self.state.hfo_end_s = float(end_s)
        if emit:
            self.settingsChanged.emit()

    def _on_hfo_window_text_changed(self, _text: str) -> None:
        self.state.hfo_start_s = self._parse_float_text(self.edit_hfo_start)
        self.state.hfo_end_s = self._parse_float_text(self.edit_hfo_end)
        self._clear_hfo_outputs()
        self.settingsChanged.emit()

    def _read_hfo_window_from_ui(self) -> tuple[float, float]:
        start_s = self._parse_float_text(self.edit_hfo_start)
        end_s = self._parse_float_text(self.edit_hfo_end)
        if start_s is None:
            raise ValueError("Enter a valid HFO analysis start time in seconds.")
        if end_s is None:
            raise ValueError("Enter a valid HFO analysis end time in seconds.")
        if end_s <= start_s:
            raise ValueError("HFO analysis end must be after analysis start.")
        total_s = self._total_duration_s()
        if total_s is not None:
            tolerance_s = max(1e-9, 0.5 / self._sampling_frequency_hz())
            if start_s < 0.0 or end_s > total_s + tolerance_s:
                raise ValueError("HFO analysis window must stay inside the recording.")
            end_s = min(float(end_s), float(total_s))
        self.state.hfo_start_s = float(start_s)
        self.state.hfo_end_s = float(end_s)
        return float(start_s), float(end_s)

    def _on_hfo_detector_version_changed(self, _text: str) -> None:
        detector_version = _normalize_hfo_classifier_name(
            self.combo_hfo_detector_version.currentData()
            or DEFAULT_HFO_DETECTOR_VERSION
        )
        if detector_version in DISABLED_HFO_CLASSIFIER_OPTIONS:
            _set_combo_current_data(self.combo_hfo_detector_version, DEFAULT_HFO_DETECTOR_VERSION)
            detector_version = DEFAULT_HFO_DETECTOR_VERSION
        self.hfo_params["detector_version"] = detector_version
        target_preset = _normalize_hfo_band_preset_name(
            self.combo_hfo_band_preset.currentData() or DEFAULT_HFO_BAND_PRESET
        )
        band = _hfo_band_for_preset(target_preset, detector_version)
        if band is None:
            band = (
                float(self.edit_hfo_low_freq.value()),
                float(self.edit_hfo_high_freq.value()),
            )
        self.hfo_params["band_preset"] = target_preset
        self.hfo_params["low_freq"] = float(band[0])
        self.hfo_params["high_freq"] = float(band[1])
        self.edit_hfo_low_freq.blockSignals(True)
        self.edit_hfo_high_freq.blockSignals(True)
        self.edit_hfo_low_freq.setValue(float(band[0]))
        self.edit_hfo_high_freq.setValue(float(band[1]))
        self.edit_hfo_low_freq.blockSignals(False)
        self.edit_hfo_high_freq.blockSignals(False)
        self._clear_hfo_outputs()
        self.settingsChanged.emit()

    def _on_hfo_band_preset_changed(self, _text: str) -> None:
        preset = _normalize_hfo_band_preset_name(
            self.combo_hfo_band_preset.currentData() or DEFAULT_HFO_BAND_PRESET
        )
        if preset in DISABLED_HFO_BAND_PRESETS:
            self.combo_hfo_band_preset.blockSignals(True)
            _set_combo_current_data(self.combo_hfo_band_preset, DEFAULT_HFO_BAND_PRESET)
            self.combo_hfo_band_preset.blockSignals(False)
            preset = DEFAULT_HFO_BAND_PRESET
        self.hfo_params["band_preset"] = preset
        detector_version = _normalize_hfo_classifier_name(
            self.hfo_params.get("detector_version", DEFAULT_HFO_DETECTOR_VERSION)
        )
        band = _hfo_band_for_preset(preset, detector_version)
        if band is not None:
            low_freq, high_freq = band
            for spin, value in (
                (self.edit_hfo_low_freq, low_freq),
                (self.edit_hfo_high_freq, high_freq),
            ):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)
        self._sync_hfo_params_from_ui(emit=True, update_preset=False)

    def _on_hfo_advanced_parameter_changed(self, _value=None) -> None:
        self._ensure_one_hfo_candidate_detector_selected()
        self._sync_hfo_detector_parameter_enabled()

    def _collect_hfo_params_from_ui(self, *, update_preset: bool = True) -> dict:
        params = deepcopy(self.hfo_params)
        low_freq = float(self.edit_hfo_low_freq.value())
        high_freq = float(self.edit_hfo_high_freq.value())
        detector_version = _normalize_hfo_classifier_name(
            self.combo_hfo_detector_version.currentData()
            or DEFAULT_HFO_DETECTOR_VERSION
        )
        if detector_version in DISABLED_HFO_CLASSIFIER_OPTIONS:
            detector_version = DEFAULT_HFO_DETECTOR_VERSION
        params["detector_version"] = detector_version
        params["low_freq"] = low_freq
        params["high_freq"] = high_freq
        params["threshold_sigma"] = float(self.edit_hfo_threshold_sigma.value())
        params["min_duration_ms"] = float(self.edit_hfo_min_duration.value())
        params["max_duration_ms"] = float(self.edit_hfo_max_duration.value())
        params["boundary_padding_s"] = float(self.edit_hfo_boundary_padding.value())
        params["merge_gap_ms"] = float(self.edit_hfo_merge_gap.value())
        params["min_cycles"] = float(self.edit_hfo_min_cycles.value())
        detector_parameters: dict[str, dict[str, float | int]] = {}
        for (detector_key, param_key), spin in getattr(self, "_hfo_detector_param_spins", {}).items():
            detector_parameters.setdefault(str(detector_key), {})[str(param_key)] = (
                int(spin.value()) if isinstance(spin, QSpinBox) else float(spin.value())
            )
        params["detector_parameters"] = detector_parameters
        active_candidate_detectors: list[str] = []
        self._ensure_one_hfo_candidate_detector_selected()
        if self.chk_hfo_ste.isChecked():
            active_candidate_detectors.append("ste")
        if self.chk_hfo_mni.isChecked():
            active_candidate_detectors.append("mni")
        if self.chk_hfo_hilbert.isChecked():
            active_candidate_detectors.append("hilbert")
        params["active_candidate_detectors"] = active_candidate_detectors
        if update_preset:
            matched_preset = CUSTOM_HFO_BAND_PRESET
            for preset_name in HFO_BAND_PRESETS:
                band = _hfo_band_for_preset(preset_name, detector_version)
                if band is None:
                    continue
                preset_low, preset_high = band
                if abs(low_freq - preset_low) < 1e-9 and abs(high_freq - preset_high) < 1e-9:
                    matched_preset = preset_name
                    break
            params["band_preset"] = matched_preset
        return params

    def _sync_hfo_detector_parameter_enabled(self) -> None:
        detector_enabled = {
            "ste": self.chk_hfo_ste.isChecked(),
            "mni": self.chk_hfo_mni.isChecked(),
            "hilbert": self.chk_hfo_hilbert.isChecked(),
        }
        for detector_key, box in getattr(self, "_hfo_detector_param_boxes", {}).items():
            enabled = bool(detector_enabled.get(str(detector_key), True))
            box.setEnabled(enabled)
            box.setToolTip(
                ""
                if enabled
                else "This detector is disabled; its parameters are kept but not used."
            )

    def _sync_hfo_params_from_ui(
        self,
        *,
        emit: bool = True,
        update_preset: bool = True,
    ) -> None:
        params = self._collect_hfo_params_from_ui(update_preset=update_preset)
        low_freq = float(params["low_freq"])
        detector_version = _normalize_hfo_classifier_name(params["detector_version"])
        if detector_version in DISABLED_HFO_CLASSIFIER_OPTIONS:
            _set_combo_current_data(self.combo_hfo_detector_version, DEFAULT_HFO_DETECTOR_VERSION)
            detector_version = DEFAULT_HFO_DETECTOR_VERSION
            params["detector_version"] = detector_version
        self.hfo_params = params
        self._sync_hfo_detector_parameter_enabled()
        if update_preset:
            matched_preset = str(params.get("band_preset", CUSTOM_HFO_BAND_PRESET))
            if self.combo_hfo_band_preset.currentData() != matched_preset:
                self.combo_hfo_band_preset.blockSignals(True)
                _set_combo_current_data(self.combo_hfo_band_preset, matched_preset)
                self.combo_hfo_band_preset.blockSignals(False)
        if emit:
            self._clear_hfo_outputs()
            self.settingsChanged.emit()

    def _restore_hfo_params(self, saved_params: dict, *, apply_to_params: bool = True) -> None:
        detector_version = _normalize_hfo_classifier_name(
            saved_params.get("detector_version", DEFAULT_HFO_DETECTOR_VERSION)
        )
        if detector_version in DISABLED_HFO_CLASSIFIER_OPTIONS:
            detector_version = DEFAULT_HFO_DETECTOR_VERSION
        if detector_version not in HFO_DETECTOR_VERSIONS:
            detector_version = DEFAULT_HFO_DETECTOR_VERSION
        preset = _normalize_hfo_band_preset_name(
            saved_params.get("band_preset", DEFAULT_HFO_BAND_PRESET)
        )
        if preset in DISABLED_HFO_BAND_PRESETS:
            preset = DEFAULT_HFO_BAND_PRESET
        if preset not in HFO_BAND_PRESETS and preset != CUSTOM_HFO_BAND_PRESET:
            preset = DEFAULT_HFO_BAND_PRESET
        default_low, default_high = (
            _hfo_band_for_preset(preset, detector_version)
            or _hfo_default_band_for_classifier(detector_version)
        )
        values = {
            "low_freq": saved_params.get("low_freq", default_low),
            "high_freq": saved_params.get("high_freq", default_high),
            "threshold_sigma": saved_params.get("threshold_sigma", self.hfo_params["threshold_sigma"]),
            "min_duration_ms": saved_params.get("min_duration_ms", self.hfo_params["min_duration_ms"]),
            "max_duration_ms": saved_params.get("max_duration_ms", self.hfo_params.get("max_duration_ms", 500.0)),
            "boundary_padding_s": saved_params.get("boundary_padding_s", self.hfo_params.get("boundary_padding_s", 1.0)),
            "merge_gap_ms": saved_params.get("merge_gap_ms", self.hfo_params["merge_gap_ms"]),
            "min_cycles": saved_params.get("min_cycles", self.hfo_params["min_cycles"]),
        }
        if preset != CUSTOM_HFO_BAND_PRESET:
            values["low_freq"], values["high_freq"] = default_low, default_high
        spin_by_key = {
            "low_freq": self.edit_hfo_low_freq,
            "high_freq": self.edit_hfo_high_freq,
            "threshold_sigma": self.edit_hfo_threshold_sigma,
            "min_duration_ms": self.edit_hfo_min_duration,
            "max_duration_ms": self.edit_hfo_max_duration,
            "boundary_padding_s": self.edit_hfo_boundary_padding,
            "merge_gap_ms": self.edit_hfo_merge_gap,
            "min_cycles": self.edit_hfo_min_cycles,
        }
        for key, spin in spin_by_key.items():
            try:
                value = float(values[key])
            except (TypeError, ValueError):
                continue
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        saved_detector_params = saved_params.get("detector_parameters")
        if not isinstance(saved_detector_params, dict):
            saved_detector_params = self.hfo_params.get("detector_parameters", {})
        for (detector_key, param_key), spin in getattr(self, "_hfo_detector_param_spins", {}).items():
            detector_values = saved_detector_params.get(detector_key, {})
            if not isinstance(detector_values, dict) or param_key not in detector_values:
                detector_values = self.hfo_params.get("detector_parameters", {}).get(detector_key, {})
            try:
                value = float(detector_values[param_key])
            except (KeyError, TypeError, ValueError):
                continue
            spin.blockSignals(True)
            spin.setValue(int(round(value)) if isinstance(spin, QSpinBox) else value)
            spin.blockSignals(False)
        self.combo_hfo_detector_version.blockSignals(True)
        _set_combo_current_data(self.combo_hfo_detector_version, detector_version)
        self.combo_hfo_detector_version.blockSignals(False)
        self.combo_hfo_band_preset.blockSignals(True)
        _set_combo_current_data(self.combo_hfo_band_preset, preset)
        self.combo_hfo_band_preset.blockSignals(False)
        active_candidate_detectors = saved_params.get(
            "active_candidate_detectors",
            DEFAULT_CANDIDATE_DETECTORS,
        )
        if not isinstance(active_candidate_detectors, (list, tuple, set)):
            active_candidate_detectors = DEFAULT_CANDIDATE_DETECTORS
        active_set = {str(item).lower() for item in active_candidate_detectors}
        if not active_set:
            active_set = {str(item).lower() for item in DEFAULT_CANDIDATE_DETECTORS}
        for checkbox, detector_key in (
            (self.chk_hfo_ste, "ste"),
            (self.chk_hfo_mni, "mni"),
            (self.chk_hfo_hilbert, "hilbert"),
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(detector_key in active_set)
            checkbox.blockSignals(False)
        if apply_to_params:
            self.hfo_params["detector_version"] = detector_version
            self.hfo_params["band_preset"] = preset
            self._sync_hfo_params_from_ui(emit=False, update_preset=True)
        else:
            self._sync_hfo_detector_parameter_enabled()

    def _load_saved_hfo_gui_defaults(self) -> None:
        settings = QSettings(HFO_GUI_SETTINGS_ORG, HFO_GUI_SETTINGS_APP)
        raw_value = settings.value(HFO_GUI_SETTINGS_KEY, "")
        if not raw_value:
            self._sync_hfo_detector_parameter_enabled()
            return
        try:
            saved_params = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            self._sync_hfo_detector_parameter_enabled()
            return
        if isinstance(saved_params, dict):
            self._restore_hfo_params(saved_params)
        else:
            self._sync_hfo_detector_parameter_enabled()

    def _save_hfo_gui_defaults(self) -> None:
        draft_params = self._collect_hfo_params_from_ui(update_preset=True)
        ok, message = self._validate_hfo_parameter_values(params=draft_params)
        if not ok:
            QMessageBox.warning(self, "HFO advanced parameters", message)
            return
        self.hfo_params = draft_params
        settings = QSettings(HFO_GUI_SETTINGS_ORG, HFO_GUI_SETTINGS_APP)
        settings.setValue(HFO_GUI_SETTINGS_KEY, json.dumps(self.hfo_params, sort_keys=True))
        settings.sync()
        self._clear_hfo_outputs()
        self.settingsChanged.emit()
        self._show_status_message("HFO advanced parameters saved.", timeout_ms=8000)

    def _restore_hfo_advanced_draft_from_active(self) -> None:
        self._restore_hfo_params(deepcopy(self.hfo_params), apply_to_params=False)

    def _reset_hfo_advanced_draft_to_defaults(self) -> None:
        self._restore_hfo_params(deepcopy(self._built_in_hfo_params), apply_to_params=False)
        self._show_status_message("HFO advanced parameters reset to defaults. Click Save to apply.", timeout_ms=8000)

    def _close_hfo_advanced_dialog(self) -> None:
        self._restore_hfo_advanced_draft_from_active()
        if self.hfo_advanced_dialog is not None:
            self.hfo_advanced_dialog.hide()

    def _choose_export_dir(self, title: str) -> Path | None:
        start_dir = (
            str(self._last_export_dir)
            if self._last_export_dir is not None
            else str(Path.home())
        )
        selected = QFileDialog.getExistingDirectory(
            self,
            title,
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return None
        return Path(selected)

    def _choose_import_dir(self, title: str) -> Path | None:
        start_dir = (
            str(self._last_export_dir)
            if self._last_export_dir is not None
            else str(Path.home())
        )
        selected = QFileDialog.getExistingDirectory(
            self,
            title,
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return None
        return Path(selected)

    def _import_algorithm_results(self) -> None:
        input_dir = self._choose_import_dir("Select exported result folder")
        if input_dir is None:
            return
        try:
            imported = import_computation_result(input_dir)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Import results",
                f"Could not import this result folder:\n{exc}",
            )
            return

        missing_channels = self._missing_import_channels(imported)
        if missing_channels:
            preview = "\n".join(f"- {name}" for name in missing_channels[:20])
            suffix = "\n..." if len(missing_channels) > 20 else ""
            QMessageBox.warning(
                self,
                "Import results",
                "The imported result contains channels that are not present in "
                "the current recording/montage:\n"
                f"{preview}{suffix}\n\nLoad the matching recording or montage before importing.",
            )
            return

        source_mismatch = self._import_source_file_mismatch(imported)
        if source_mismatch is not None:
            QMessageBox.warning(
                self,
                "Import results",
                source_mismatch,
            )
            return

        self._last_export_dir = input_dir
        if imported.algorithm == "hfo" and isinstance(imported.result, HFOComputationResult):
            self.btn_algo_hfo.setChecked(True)
            self._on_algorithm_button_clicked(self.btn_algo_hfo)
            self._show_hfo_result(imported.result)
            return
        if imported.algorithm == "gamma_spike" and isinstance(imported.result, GammaSpikeComputationResult):
            self.btn_algo_gamma.setChecked(True)
            self._on_algorithm_button_clicked(self.btn_algo_gamma)
            self._show_gamma_result(imported.result)
            total_spikes = sum(int(channel.spike_count) for channel in imported.result.channels)
            gamma_spikes = self._gamma_positive_count(imported.result)
            self._show_status_message(
                "Gamma results imported. "
                f"Total spikes: {total_spikes}. "
                f"Gamma-positive spikes: {gamma_spikes}.",
                timeout_ms=20000,
            )
            return
        if imported.algorithm == "ei" and isinstance(imported.result, EIComputationResult):
            self.btn_algo_ei.setChecked(True)
            self._on_algorithm_button_clicked(self.btn_algo_ei)
            self._show_ei_result(imported.result)
            self._show_status_message(
                f"REI results imported. Channels: {len(imported.result.channels)}.",
                timeout_ms=20000,
            )
            return

        QMessageBox.critical(
            self,
            "Import results",
            "Imported result type did not match the detected algorithm.",
        )

    def _missing_import_channels(self, imported: ImportedComputationResult) -> list[str]:
        displayed = {str(name) for name in self._ch_names_displayed}
        if not displayed:
            return []
        imported_channels: set[str] = set()
        result = imported.result
        if isinstance(result, HFOComputationResult):
            imported_channels.update(str(channel.channel) for channel in result.channels)
            imported_channels.update(str(event.channel) for event in result.events)
        elif isinstance(result, GammaSpikeComputationResult):
            imported_channels.update(str(channel.channel) for channel in result.channels)
        elif isinstance(result, EIComputationResult):
            imported_channels.update(str(channel.channel) for channel in result.channels)
            imported_channels.update(str(channel) for channel in result.heatmap_channels)
        return sorted(channel for channel in imported_channels if channel and channel not in displayed)

    def _import_source_file_mismatch(self, imported: ImportedComputationResult) -> str | None:
        if self._source_file_path is None:
            return None
        result_metadata = getattr(imported.result, "metadata", None)
        metadata = result_metadata if isinstance(result_metadata, dict) else {}
        imported_path_text = str(metadata.get("source_file_path") or "").strip()
        imported_name = str(metadata.get("source_file_name") or "").strip()
        current_path = Path(self._source_file_path)
        current_name = current_path.name

        if imported_path_text:
            imported_path = Path(imported_path_text)
            try:
                same_path = imported_path.resolve(strict=False) == current_path.resolve(strict=False)
            except OSError:
                same_path = str(imported_path).casefold() == str(current_path).casefold()
            if not same_path and imported_path.name.casefold() != current_name.casefold():
                return (
                    "The imported results were produced from a different recording file.\n\n"
                    f"Current file: {current_name}\n"
                    f"Imported file: {imported_path.name or imported_path_text}"
                )
            return None

        if imported_name and imported_name.casefold() != current_name.casefold():
            return (
                "The imported results were produced from a different recording file.\n\n"
                f"Current file: {current_name}\n"
                f"Imported file: {imported_name}"
            )
        return None

    def _confirm_export_overwrite(
        self,
        output_dir: Path,
        filenames: list[str],
        *,
        title: str,
    ) -> bool:
        existing: list[str] = []
        for name in filenames:
            path = output_dir / name
            if not path.exists():
                continue
            if path.is_dir():
                png_count = len(list(path.glob("*.png")))
                existing.append(
                    f"{name}/ ({png_count} PNG files)"
                    if png_count
                    else f"{name}/"
                )
            else:
                existing.append(name)
        if not existing:
            return True

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle(title)
        message.setText("Overwrite existing export files?")
        message.setInformativeText(
            "The selected folder already contains:\n"
            + "\n".join(f"- {name}" for name in existing)
            + "\n\nContinuing will replace these files."
        )
        message.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        message.setDefaultButton(QMessageBox.StandardButton.No)
        result = message.exec()
        if result == QMessageBox.StandardButton.Yes:
            return True
        try:
            return int(result) == int(QMessageBox.StandardButton.Yes)
        except (TypeError, ValueError):
            return False

    # ---------- Internals : EI controls ----------

    def _parse_float_text(self, edit: QLineEdit) -> float | None:
        text = edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _on_algorithm_button_clicked(self, button: QAbstractButton | None = None) -> None:
        if button is None:
            button = self.algo_buttons.checkedButton()
        if button is None:
            return

        algorithm = str(button.property("algorithm") or "ei")
        self.state.algorithm = algorithm
        is_ei = algorithm == "ei"
        is_gamma = algorithm == "gamma_spike"
        is_hfo = algorithm == "hfo"

        self.gamma_time_widget.setVisible(self.gb_t.isChecked() and is_gamma)
        self.ei_time_widget.setVisible(self.gb_t.isChecked() and is_ei)
        self.hfo_time_widget.setVisible(self.gb_t.isChecked() and is_hfo)

        self.btn_open_ei_summary.setVisible(is_ei)
        self.btn_open_ei_heatmap.setVisible(is_ei)
        self.btn_export_ei.setVisible(is_ei)
        self.btn_open_gamma_summary.setVisible(is_gamma)
        self.btn_open_gamma_review.setVisible(is_gamma)
        self.btn_export_gamma.setVisible(is_gamma)
        self.btn_open_hfo_summary.setVisible(is_hfo)
        self.btn_open_hfo_event_grid.setVisible(is_hfo)
        self.btn_export_hfo.setVisible(is_hfo)
        self.btn_cancel_gamma.setVisible(is_gamma and self._gamma_thread is not None)
        self.btn_cancel_hfo.setVisible(is_hfo and self._hfo_thread is not None)
        self.btn_open_ei_summary.setEnabled(self._last_ei_result is not None)
        self.btn_open_ei_heatmap.setEnabled(
            self._last_ei_result is not None and bool(self._last_ei_result.heatmap.size)
        )
        self.btn_export_ei.setEnabled(self._last_ei_result is not None)
        self.btn_open_gamma_summary.setEnabled(self._last_gamma_result is not None)
        self.btn_open_gamma_review.setEnabled(self._last_gamma_result is not None)
        self.btn_export_gamma.setEnabled(self._last_gamma_result is not None)
        self.btn_open_hfo_summary.setEnabled(self._last_hfo_result is not None)
        self.btn_open_hfo_event_grid.setEnabled(self._last_hfo_result is not None)
        self.btn_export_hfo.setEnabled(self._last_hfo_result is not None)

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
        elif is_hfo:
            self.btn_run.setText("Run HFO Detector")
            if (
                not self._hfo_default_window_applied
                or self._parse_float_text(self.edit_hfo_start) is None
                or self._parse_float_text(self.edit_hfo_end) is None
            ):
                self._set_hfo_window_to_full_recording(emit=True)
                self._hfo_default_window_applied = True
            else:
                self._on_hfo_window_text_changed("")
        else:
            self.btn_run.setText("Run")

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

    def _open_hfo_advanced_dialog(self) -> None:
        if self.hfo_advanced_dialog is None:
            self.hfo_advanced_dialog = QDialog(self)
            self.hfo_advanced_dialog.setWindowTitle("HFO advanced parameters")
            self.hfo_advanced_dialog.setModal(False)
            self.hfo_advanced_dialog.resize(720, 760)
            self.hfo_advanced_dialog.setMinimumSize(560, 520)

            layout = QVBoxLayout(self.hfo_advanced_dialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(self.hfo_advanced_frame)
            layout.addWidget(scroll, 1)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            reset_button = buttons.addButton(
                "Back to default",
                QDialogButtonBox.ButtonRole.ResetRole,
            )
            save_button = buttons.addButton(
                "Save",
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            reset_button.clicked.connect(self._reset_hfo_advanced_draft_to_defaults)
            save_button.clicked.connect(self._save_hfo_gui_defaults)
            buttons.rejected.connect(self._close_hfo_advanced_dialog)
            layout.addWidget(buttons)

        self._restore_hfo_advanced_draft_from_active()
        self.hfo_advanced_dialog.show()
        self.hfo_advanced_dialog.raise_()
        self.hfo_advanced_dialog.activateWindow()

    def _disable_unvalidated_hfo_band_presets(self) -> None:
        model = self.combo_hfo_band_preset.model()
        for idx in range(self.combo_hfo_band_preset.count()):
            label = str(self.combo_hfo_band_preset.itemText(idx))
            if label not in DISABLED_HFO_BAND_PRESETS:
                continue
            item = getattr(model, "item", lambda _idx: None)(idx)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip("Not validated yet for the legacy pyHFO integration.")

    def _disable_unvalidated_hfo_classifier_options(self) -> None:
        if not DISABLED_HFO_CLASSIFIER_OPTIONS:
            return
        model = self.combo_hfo_detector_version.model()
        for idx in range(self.combo_hfo_detector_version.count()):
            label = str(self.combo_hfo_detector_version.itemData(idx))
            if label not in DISABLED_HFO_CLASSIFIER_OPTIONS:
                continue
            item = getattr(model, "item", lambda _idx: None)(idx)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip("This HFO classifier option is not available in this build.")

    def _lock_hfo_legacy_parameter_controls(self) -> None:
        for control in (self.edit_hfo_low_freq, self.edit_hfo_high_freq):
            control.setEnabled(True)
            control.setToolTip("Editable when Band preset is Custom; presets fill these values automatically.")
        for control in (
            self.edit_hfo_threshold_sigma,
            self.edit_hfo_min_duration,
            self.edit_hfo_max_duration,
            self.edit_hfo_boundary_padding,
            self.edit_hfo_merge_gap,
            self.edit_hfo_min_cycles,
        ):
            control.setEnabled(True)
            control.setToolTip("Editable detector parameter. Defaults match the validated legacy pyHFO setup.")

    def _ensure_one_hfo_candidate_detector_selected(self) -> None:
        checkboxes = (self.chk_hfo_ste, self.chk_hfo_mni, self.chk_hfo_hilbert)
        if any(checkbox.isChecked() for checkbox in checkboxes):
            return
        sender = self.sender()
        fallback = sender if sender in checkboxes else self.chk_hfo_ste
        fallback.blockSignals(True)
        fallback.setChecked(True)
        fallback.blockSignals(False)

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
        is_hfo = self.state.algorithm == "hfo"
        self.gamma_time_widget.setVisible(time_visible and is_gamma)
        self.ei_time_widget.setVisible(time_visible and is_ei)
        self.hfo_time_widget.setVisible(time_visible and is_hfo)

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
            self._clear_ei_outputs()
            self.settingsChanged.emit()

    def _sync_ei_frequency_from_ui(self, *, emit: bool = True) -> None:
        self.ei_params["low_freq"] = float(self.edit_ei_low_freq.value())
        self.ei_params["high_freq"] = float(self.edit_ei_high_freq.value())
        if emit:
            self._clear_ei_outputs()
            self.settingsChanged.emit()

    def _on_ei_frequency_changed(self, _value=None) -> None:
        self._sync_ei_frequency_from_ui(emit=True)

    @staticmethod
    def _is_legacy_default_ei_frequency(low_freq, high_freq) -> bool:
        try:
            low = float(low_freq)
            high = float(high_freq)
        except (TypeError, ValueError):
            return False
        return abs(low - 70.0) < 1e-9 and abs(high - DEFAULT_REI_HIGH_FREQ_HZ) < 1e-9

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
        fs = self._sampling_frequency_hz()
        if fs <= 0.0:
            return float(self._raw.times[-1])
        return float(self._raw.n_times) / fs

    def _sampling_frequency_hz(self) -> float:
        if self._raw is None:
            return 1.0
        try:
            fs = float(self._raw.info["sfreq"])
        except Exception:
            return 1.0
        return fs if np.isfinite(fs) and fs > 0.0 else 1.0

    def _validate_hfo_parameter_values(
        self,
        *,
        params: dict | None = None,
        analysis_duration_s: float | None = None,
    ) -> tuple[bool, str]:
        params = params if isinstance(params, dict) else self.hfo_params
        low_freq = float(params["low_freq"])
        high_freq = float(params["high_freq"])
        if low_freq <= 0.0 or high_freq <= low_freq:
            return False, "HFO frequency range must have positive low < high values."
        detection_nyquist = 0.5 * OMNI_TARGET_FS_HZ
        if high_freq >= detection_nyquist:
            return False, (
                "HFO high frequency must stay below the 1000 Hz detection Nyquist "
                f"({detection_nyquist:g} Hz)."
            )
        if low_freq >= high_freq:
            return False, "HFO low frequency must be lower than high frequency."
        if float(params["threshold_sigma"]) <= 0.0:
            return False, "HFO threshold sigma must be positive."
        if float(params["min_duration_ms"]) < 1.0:
            return False, "HFO minimum duration must be at least 1 ms at 1000 Hz processing."
        if float(params["max_duration_ms"]) < 1.0:
            return False, "HFO maximum duration must be at least 1 ms at 1000 Hz processing."
        if float(params["max_duration_ms"]) <= float(params["min_duration_ms"]):
            return False, "HFO maximum duration must be longer than minimum duration."
        if float(params["boundary_padding_s"]) < 0.0:
            return False, "HFO boundary padding cannot be negative."
        if float(params["merge_gap_ms"]) < 0.0:
            return False, "HFO merge gap cannot be negative."
        if float(params["min_cycles"]) < 1.0:
            return False, "HFO minimum cycles must be at least 1."

        active = {str(name).lower() for name in params.get("active_candidate_detectors", [])}
        if not active:
            return False, "Select at least one HFO candidate detector."
        detector_params = params.get("detector_parameters", {})
        if not isinstance(detector_params, dict):
            return False, "HFO detector parameters are malformed."

        def _section(detector_key: str) -> dict:
            values = detector_params.get(detector_key, {})
            return values if isinstance(values, dict) else {}

        def _positive(value: Any, label: str) -> tuple[bool, str]:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return False, f"{label} must be numeric."
            if not np.isfinite(numeric) or numeric <= 0.0:
                return False, f"{label} must be positive."
            return True, ""

        def _non_negative(value: Any, label: str) -> tuple[bool, str]:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return False, f"{label} must be numeric."
            if not np.isfinite(numeric) or numeric < 0.0:
                return False, f"{label} cannot be negative."
            return True, ""

        checks: list[tuple[Any, str, str]] = []
        if "ste" in active:
            ste_params = _section("ste")
            checks.extend(
                [
                    (ste_params.get("rms_window_s"), "positive", "STE RMS window"),
                    (ste_params.get("min_window_s"), "positive", "STE minimum window"),
                    (ste_params.get("min_gap_s"), "non_negative", "STE minimum gap"),
                    (ste_params.get("epoch_len"), "positive", "STE epoch length"),
                    (ste_params.get("min_osc"), "positive", "STE minimum oscillations"),
                    (ste_params.get("rms_thres"), "positive", "STE RMS threshold"),
                    (ste_params.get("peak_thres"), "positive", "STE peak threshold"),
                ]
            )
        if "mni" in active:
            mni_params = _section("mni")
            checks.extend(
                [
                    (mni_params.get("epoch_time_s"), "positive", "MNI epoch time"),
                    (mni_params.get("epo_chf_hz"), "positive", "MNI epoch CHF"),
                    (mni_params.get("min_win_s"), "positive", "MNI minimum window"),
                    (mni_params.get("min_gap_s"), "non_negative", "MNI minimum gap"),
                    (mni_params.get("base_seg_s"), "positive", "MNI baseline segment"),
                    (mni_params.get("base_shift_s"), "non_negative", "MNI baseline shift"),
                    (mni_params.get("base_threshold"), "non_negative", "MNI baseline threshold"),
                    (mni_params.get("base_min"), "non_negative", "MNI baseline minimum"),
                ]
            )
            per_chf = float(mni_params.get("per_chf", 0.0))
            if not np.isfinite(per_chf) or per_chf <= 0.0 or per_chf > 1.0:
                return False, "MNI percent CHF must be > 0 and <= 1."
            threshold_percentile = float(mni_params.get("threshold_percentile", 0.0))
            if (
                not np.isfinite(threshold_percentile)
                or threshold_percentile <= 0.0
                or threshold_percentile >= 1.0
            ):
                return False, "MNI threshold percentile must be > 0 and < 1."
        if "hilbert" in active:
            hilbert_params = _section("hilbert")
            checks.extend(
                [
                    (hilbert_params.get("sd_threshold"), "positive", "Hilbert SD threshold"),
                    (hilbert_params.get("min_window_s"), "positive", "Hilbert minimum window"),
                    (hilbert_params.get("epoch_len_s"), "positive", "Hilbert epoch length"),
                ]
            )

        for value, kind, label in checks:
            ok, message = _positive(value, label) if kind == "positive" else _non_negative(value, label)
            if not ok:
                return False, message

        if analysis_duration_s is not None:
            try:
                duration_s = float(analysis_duration_s)
            except (TypeError, ValueError):
                duration_s = 0.0
            if not np.isfinite(duration_s) or duration_s <= 0.0:
                return False, "HFO analysis duration must be positive."
            minimum_event_s = float(params["min_duration_ms"]) / 1000.0
            maximum_event_s = float(params["max_duration_ms"]) / 1000.0
            boundary_padding_s = float(params["boundary_padding_s"])
            if minimum_event_s >= duration_s:
                return False, "HFO minimum duration must be shorter than the analysis interval."
            if maximum_event_s >= duration_s:
                return False, "HFO maximum duration must be shorter than the analysis interval."
            if 2.0 * boundary_padding_s >= duration_s:
                return False, "HFO boundary padding must leave some analyzable signal in the selected interval."
            for detector_key, param_key, label in (
                ("ste", "min_window_s", "STE minimum window"),
                ("mni", "min_win_s", "MNI minimum window"),
                ("hilbert", "min_window_s", "Hilbert minimum window"),
            ):
                if detector_key not in active:
                    continue
                value = float(_section(detector_key).get(param_key, 0.0))
                if value >= duration_s:
                    return False, f"{label} must be shorter than the analysis interval."

        return True, ""

    def _validate_ei_inputs(self) -> tuple[bool, str]:
        if self._raw is None or self._picks is None:
            return False, "Load a dataset before running REI."
        if not self.state.selected_abs:
            return False, "Select at least one channel before running REI."

        low_freq = float(self.edit_ei_low_freq.value())
        high_freq = float(self.edit_ei_high_freq.value())
        if low_freq <= 0.0 or high_freq <= low_freq:
            return False, "REI frequency range must have positive low < high values."
        nyquist = 0.5 * self._sampling_frequency_hz()
        if high_freq >= nyquist:
            return False, (
                f"REI high frequency must be below Nyquist ({nyquist:g} Hz)."
            )

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

    def _validate_hfo_inputs(self) -> tuple[bool, str]:
        if self._raw is None or self._picks is None:
            return False, "Load a dataset before running the HFO detector."
        if not self.state.selected_abs:
            return False, "Select at least one channel before running the HFO detector."

        try:
            self._read_hfo_window_from_ui()
        except ValueError as exc:
            return False, str(exc)

        start_s, end_s = self.state.hfo_start_s, self.state.hfo_end_s
        ok, message = self._validate_hfo_parameter_values(
            analysis_duration_s=(
                float(end_s) - float(start_s)
                if start_s is not None and end_s is not None
                else None
            ),
        )
        if not ok:
            return False, message
        fs = self._sampling_frequency_hz()
        if fs < OMNI_TARGET_FS_HZ:
            return False, (
                "HFO detection requires recordings sampled at 1000 Hz or higher. "
                f"The loaded recording is {fs:g} Hz."
            )
        detection_nyquist = 0.5 * OMNI_TARGET_FS_HZ
        low_freq = float(self.hfo_params["low_freq"])
        if low_freq >= detection_nyquist:
            return False, (
                "HFO low frequency must be below the 1000 Hz detection Nyquist "
                f"({detection_nyquist:g} Hz)."
            )
        return True, ""

    def _run_computation(self) -> None:
        if self._gamma_completion_status_active:
            self._clear_status_message()
            self._gamma_completion_status_active = False
        if self.state.algorithm == "ei":
            ok, message = self._validate_ei_inputs()
            if not ok:
                QMessageBox.warning(self, "REI computation", message)
                return
            if not self._confirm_ei_montage_before_run():
                return
            if not self._confirm_ei_notch_before_run():
                return
            error_message: str | None = None
            with busy_cursor(self, "Running REI computation..."):
                perf_start = time.perf_counter()
                try:
                    result = self._compute_ei_result()
                except Exception as exc:
                    timed_mark("after_REI", perf_start, raw=self._raw, notes=f"error: {exc}")
                    error_message = str(exc)
                else:
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
                    elapsed_s = max(0.0, time.perf_counter() - perf_start)
                    top_channel = self._top_rei_channel(result)
                    top_text = (
                        f" Top channel: {top_channel.channel} "
                        f"(REI {float(top_channel.ei):.3f})."
                        if top_channel is not None
                        else ""
                    )
                    self._show_status_message(
                        "REI analysis finished. "
                        f"Channels: {len(channel_results)}.{top_text} "
                        f"Runtime: {self._format_duration(elapsed_s)}.",
                        timeout_ms=0,
                    )
            if error_message is not None:
                QMessageBox.warning(self, "REI computation", error_message)
            return

        if self.state.algorithm == "gamma_spike":
            if self._gamma_thread is not None:
                QMessageBox.information(
                    self,
                    "Gamma spike detector",
                    "A gamma spike detection run is already in progress.",
                )
                return
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
            if not self._confirm_gamma_notch_before_run():
                return

            perf_start = time.perf_counter()
            try:
                compute_callback = self._build_gamma_spike_compute_callback(start_s, stop_s)
            except Exception as exc:
                timed_mark(
                    "after_gamma_spike_detector",
                    perf_start,
                    raw=self._raw,
                    visible_window_s=max(0.0, stop_s - start_s),
                    notes=f"setup error: {exc}",
                )
                QMessageBox.warning(self, "Gamma spike detector", str(exc))
                return
            self._start_gamma_worker(compute_callback, start_s, stop_s, perf_start)
            return

        if self.state.algorithm == "hfo":
            if self._hfo_thread is not None:
                QMessageBox.information(
                    self,
                    "HFO detector",
                    "An HFO detection run is already in progress.",
                )
                return
            ok, message = self._validate_hfo_inputs()
            if not ok:
                QMessageBox.warning(self, "HFO detector", message)
                return
            try:
                start_s, stop_s = self._read_hfo_window_from_ui()
            except ValueError as exc:
                QMessageBox.warning(self, "HFO detector", str(exc))
                return
            perf_start = time.perf_counter()
            try:
                compute_callback = self._build_hfo_compute_callback(start_s, stop_s)
            except Exception as exc:
                timed_mark(
                    "after_hfo_detector",
                    perf_start,
                    raw=self._raw,
                    visible_window_s=max(0.0, stop_s - start_s),
                    notes=f"setup error: {exc}",
                )
                QMessageBox.warning(self, "HFO detector", str(exc))
                return
            self._start_hfo_worker(compute_callback, start_s, stop_s, perf_start)
            return

        QMessageBox.warning(
            self,
            "Computation",
            "Select REI, the gamma spike detector, or HFO before running a computation.",
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
        self._sync_ei_frequency_from_ui(emit=False)

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

        result = compute_ei_for_gui(
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
            low_freq=float(self.ei_params["low_freq"]),
            high_freq=float(self.ei_params["high_freq"]),
            metadata=self._build_ei_metadata(
                self._current_montage_name(),
                seizure_onset_s=seizure_onset,
                seizure_offset_s=seizure_offset,
                baseline_window_s=(baseline_start, baseline_end),
                ictal_window_s=(ictal_start, ictal_end),
                notch_modes_by_channel=notch_modes_by_channel,
            ),
        )
        if self._source_file_path is not None:
            result.metadata["source_file_name"] = self._source_file_path.name
            result.metadata["source_file_path"] = str(self._source_file_path)
        return result

    def _compute_hfo_result(self) -> HFOComputationResult:
        start_s, stop_s = self._read_hfo_window_from_ui()
        return self._build_hfo_compute_callback(start_s, stop_s)(lambda: None)

    def _hfo_band_label_from_params(self, hfo_params: dict) -> str:
        preset = _normalize_hfo_band_preset_name(
            hfo_params.get("band_preset", DEFAULT_HFO_BAND_PRESET)
        )
        low = float(hfo_params.get("low_freq", 0.0) or 0.0)
        high = float(hfo_params.get("high_freq", 0.0) or 0.0)
        return f"{preset} {low:g}-{high:g} Hz"

    def _build_hfo_compute_callback(
        self,
        start_s: float,
        stop_s: float,
    ) -> Callable[[Callable[[], None]], HFOComputationResult]:
        if self._ei_data_callback is None and self._ei_data_snapshot_callback is None:
            raise RuntimeError("HFO data extraction is not available.")

        selected_abs = list(self.state.selected_abs)
        selected_names = [
            str(self._ch_names_displayed[int(idx)])
            for idx in selected_abs
            if 0 <= int(idx) < len(self._ch_names_displayed)
        ]
        notch_modes_by_selected_channel = self._ei_notch_modes_for_channels(selected_names)
        bad_channels = {
            str(name)
            for name in self._bad_channel_names()
        }
        hfo_params = dict(self.hfo_params)
        metadata = self._build_hfo_metadata(
            analysis_window_s=(float(start_s), float(stop_s)),
            notch_modes_by_channel=notch_modes_by_selected_channel,
        )
        source_file_path = self._source_file_path

        if self._ei_data_snapshot_callback is not None:
            data_callback = self._ei_data_snapshot_callback()
        elif self._ei_data_callback is not None:
            data_callback = self._ei_data_callback
        else:
            raise RuntimeError("HFO data extraction is not available.")

        def compute(raise_if_cancelled: Callable[[], None]) -> HFOComputationResult:
            raise_if_cancelled()
            data, fs, channel_names = data_callback(
                selected_abs,
                float(start_s),
                float(stop_s),
            )
            raise_if_cancelled()
            channel_names = list(channel_names)
            channel_name_set = set(map(str, channel_names))
            bad_channels_for_data = {
                str(name)
                for name in bad_channels
                if str(name) in channel_name_set
            }
            notch_modes_by_channel = {
                str(name): str(notch_modes_by_selected_channel.get(str(name), NOTCH_OFF))
                for name in channel_names
            }
            result = compute_hfo_for_gui(
                data=np.asarray(data, dtype=float),
                fs=float(fs),
                channel_names=channel_names,
                data_start_s=float(start_s),
                analysis_window_s=(float(start_s), float(stop_s)),
                detector_version=str(
                    _normalize_hfo_classifier_name(
                        hfo_params.get("detector_version", DEFAULT_HFO_DETECTOR_VERSION)
                    )
                ),
                active_candidate_detectors=list(
                    hfo_params.get(
                        "active_candidate_detectors",
                        DEFAULT_CANDIDATE_DETECTORS,
                    )
                ),
                band_label=self._hfo_band_label_from_params(hfo_params),
                low_freq_hz=float(hfo_params["low_freq"]),
                high_freq_hz=float(hfo_params["high_freq"]),
                threshold_sigma=float(hfo_params["threshold_sigma"]),
                min_duration_ms=float(hfo_params["min_duration_ms"]),
                max_duration_ms=float(hfo_params["max_duration_ms"]),
                boundary_padding_s=float(hfo_params["boundary_padding_s"]),
                merge_gap_ms=float(hfo_params["merge_gap_ms"]),
                min_cycles=float(hfo_params["min_cycles"]),
                detector_parameters=dict(hfo_params.get("detector_parameters", {})),
                notch_modes_by_channel=notch_modes_by_channel,
                bad_channels=bad_channels_for_data,
                reference_mode="none",
                checkpoint_paths={},
                device="cpu",
                metadata=dict(metadata),
            )
            raise_if_cancelled()
            if source_file_path is not None:
                result.metadata["source_file_name"] = source_file_path.name
                result.metadata["source_file_path"] = str(source_file_path)
            return result

        return compute

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

    def _confirm_gamma_notch_before_run(self) -> bool:
        selected_names = [
            str(self._ch_names_displayed[int(idx)])
            for idx in self.state.selected_abs
            if 0 <= int(idx) < len(self._ch_names_displayed)
        ]
        notch_modes = self._ei_notch_modes_for_channels(selected_names)
        active_modes = {
            str(mode)
            for mode in notch_modes.values()
            if str(mode) != NOTCH_OFF
        }
        if active_modes:
            return True

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Gamma spike detector")
        message.setText("Run gamma spike analysis without a notch filter?")
        message.setInformativeText(
            "No notch filter is selected for the gamma spike channels. "
            "Line noise may affect gamma power measurements."
        )
        message.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        message.setDefaultButton(QMessageBox.StandardButton.No)
        result = message.exec()
        return result == QMessageBox.StandardButton.Yes or result == int(
            QMessageBox.StandardButton.Yes
        )

    def _confirm_ei_notch_before_run(self) -> bool:
        selected_names = [
            str(self._ch_names_displayed[int(idx)])
            for idx in self.state.selected_abs
            if 0 <= int(idx) < len(self._ch_names_displayed)
        ]
        notch_modes = self._ei_notch_modes_for_channels(selected_names)
        active_modes = {
            str(mode)
            for mode in notch_modes.values()
            if str(mode) != NOTCH_OFF
        }
        if active_modes:
            return True

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("REI computation")
        message.setText("Run REI without a notch filter?")
        message.setInformativeText(
            "No notch filter is selected for the REI channels. "
            "Line noise may affect the HFER measurement."
        )
        message.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        message.setDefaultButton(QMessageBox.StandardButton.No)
        result = message.exec()
        return result == QMessageBox.StandardButton.Yes or result == int(
            QMessageBox.StandardButton.Yes
        )

    def _build_gamma_spike_compute_callback(
        self,
        start_s: float,
        stop_s: float,
    ) -> Callable[[Callable[[str], None]], GammaSpikeComputationResult]:
        if self._ei_data_callback is None and self._ei_data_snapshot_callback is None:
            raise RuntimeError("Gamma spike data extraction is not available.")

        selected_abs = list(self.state.selected_abs)
        selected_names = [
            str(self._ch_names_displayed[int(idx)])
            for idx in selected_abs
            if 0 <= int(idx) < len(self._ch_names_displayed)
        ]
        notch_modes_by_channel = self._ei_notch_modes_for_channels(selected_names)
        recording_duration_s = self._total_duration_s()
        source_file_path = self._source_file_path

        if self._ei_data_snapshot_callback is not None:
            data_callback = self._ei_data_snapshot_callback()
        elif self._ei_data_callback is not None:
            data_callback = self._ei_data_callback
        else:
            raise RuntimeError("Gamma spike data extraction is not available.")

        def load_gamma_data(window_start_s: float, window_stop_s: float):
            return data_callback(
                selected_abs,
                float(window_start_s),
                float(window_stop_s),
            )

        def load_gamma_indexed_data(
            selected_positions: list[int],
            window_start_s: float,
            window_stop_s: float,
        ):
            subset_abs = [
                selected_abs[int(position)]
                for position in selected_positions
                if 0 <= int(position) < len(selected_abs)
            ]
            return data_callback(
                subset_abs,
                float(window_start_s),
                float(window_stop_s),
            )

        def compute(progress_callback: Callable[[str], None]) -> GammaSpikeComputationResult:
            result = compute_gamma_spike_segmented_for_gui(
                data_loader=load_gamma_data,
                analysis_window_s=(float(start_s), float(stop_s)),
                recording_duration_s=recording_duration_s,
                chunk_minutes=10.0,
                chunk_context_seconds=10.0,
                filter_context_seconds=30.0,
                notch_modes_by_channel=notch_modes_by_channel,
                indexed_data_loader=load_gamma_indexed_data,
                progress_callback=progress_callback,
                matlab2_compat=True,
            )
            if source_file_path is not None:
                result.metadata["source_file_name"] = source_file_path.name
                result.metadata["source_file_path"] = str(source_file_path)
            return result

        return compute

    def _compute_gamma_spike_result(
        self,
        start_s: float,
        stop_s: float,
    ) -> GammaSpikeComputationResult:
        return self._build_gamma_spike_compute_callback(start_s, stop_s)(lambda _msg: None)

    def _start_gamma_worker(
        self,
        compute_callback: Callable[[Callable[[str], None]], GammaSpikeComputationResult],
        start_s: float,
        stop_s: float,
        perf_start: float,
    ) -> None:
        thread = QThread(self)
        worker = _GammaSpikeWorker(compute_callback)
        worker.moveToThread(thread)

        self._gamma_thread = thread
        self._gamma_worker = worker
        self._gamma_perf_start = perf_start
        self._gamma_run_window_s = (float(start_s), float(stop_s))
        self._gamma_cancel_requested = False

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_gamma_worker_progress)
        worker.finished.connect(self._on_gamma_worker_finished)
        worker.failed.connect(self._on_gamma_worker_failed)
        worker.cancelled.connect(self._on_gamma_worker_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_gamma_worker_refs)

        self.btn_run.setEnabled(False)
        self.btn_cancel_gamma.setVisible(True)
        self.btn_cancel_gamma.setEnabled(True)
        self._gamma_completion_status_active = False
        self._show_status_message("Gamma spike detection started...")
        thread.start()

    def _cancel_gamma_run(self) -> None:
        self._gamma_cancel_requested = True
        if self._gamma_worker is not None:
            self._gamma_worker.request_cancel()
        self.btn_cancel_gamma.setEnabled(False)
        self._show_status_message("Cancelling gamma spike detection...")

    def _start_hfo_worker(
        self,
        compute_callback: Callable[[Callable[[], None]], HFOComputationResult],
        start_s: float,
        stop_s: float,
        perf_start: float,
    ) -> None:
        thread = QThread(self)
        worker = _HFOWorker(compute_callback)
        worker.moveToThread(thread)

        self._hfo_thread = thread
        self._hfo_worker = worker
        self._hfo_perf_start = perf_start
        self._hfo_run_window_s = (float(start_s), float(stop_s))
        self._hfo_cancel_requested = False
        self._hfo_runtime_complexity = self._estimate_hfo_runtime_complexity(start_s, stop_s)
        self._hfo_expected_runtime_s = self._estimate_hfo_runtime_seconds(
            self._hfo_runtime_complexity
        )

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_hfo_worker_finished)
        worker.failed.connect(self._on_hfo_worker_failed)
        worker.cancelled.connect(self._on_hfo_worker_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_hfo_worker_refs)

        self.btn_run.setEnabled(False)
        self.btn_cancel_hfo.setVisible(True)
        self.btn_cancel_hfo.setEnabled(True)
        self._show_status_message("HFO detection started...")
        self._start_hfo_status_timer()
        thread.start()

    def _cancel_hfo_run(self) -> None:
        self._hfo_cancel_requested = True
        if self._hfo_worker is not None:
            self._hfo_worker.request_cancel()
        self.btn_cancel_hfo.setEnabled(False)
        self._show_status_message(
            "Cancelling HFO detection... The current detector/classifier step may need to finish first.",
            timeout_ms=0,
        )

    def _start_hfo_status_timer(self) -> None:
        if self._hfo_status_timer is None:
            self._hfo_status_timer = QTimer(self)
            self._hfo_status_timer.timeout.connect(self._update_hfo_status_message)
        self._hfo_status_timer.start(1000)
        self._update_hfo_status_message()

    def _stop_hfo_status_timer(self) -> None:
        if self._hfo_status_timer is not None:
            self._hfo_status_timer.stop()

    def _update_hfo_status_message(self) -> None:
        if self._hfo_thread is None or self._hfo_perf_start is None:
            self._stop_hfo_status_timer()
            return
        start = self._hfo_perf_start if self._hfo_perf_start is not None else time.perf_counter()
        elapsed_s = max(0.0, time.perf_counter() - start)
        window = self._hfo_run_window_s
        window_text = ""
        if window is not None:
            window_text = f" Window: {window[0]:.3f}-{window[1]:.3f}s."
        prefix = "Cancelling HFO detection." if self._hfo_cancel_requested else "HFO detection processing."
        suffix = " Waiting for current step to finish." if self._hfo_cancel_requested else ""
        estimate_text = self._hfo_estimate_status_text(elapsed_s)
        self._show_status_message(
            f"{prefix}{window_text} Time so far: {self._format_duration(elapsed_s)}."
            f" {estimate_text}{suffix}",
            timeout_ms=0,
        )

    @Slot(object, float)
    def _on_hfo_worker_finished(self, result: object, elapsed_s: float) -> None:
        if not isinstance(result, HFOComputationResult):
            self._on_hfo_worker_failed("HFO detector returned an unexpected result.", elapsed_s)
            return
        self._stop_hfo_status_timer()
        self._show_hfo_result(result)
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        active_counts = self._hfo_active_label_counts(result)
        start_s, stop_s = self._hfo_run_window_s or (0.0, 0.0)
        perf_start = self._hfo_perf_start if self._hfo_perf_start is not None else time.perf_counter()
        timed_mark(
            "after_hfo_detector",
            perf_start,
            raw=self._raw,
            visible_window_s=max(0.0, stop_s - start_s),
            notes=(
                f"events={metadata.get('total_events', 0)}; "
                f"detector={metadata.get('detector_version', '')}"
            ),
        )
        self._update_hfo_runtime_estimator(elapsed_s)
        self._show_status_message(
            "HFO analysis finished. "
            f"Events: {metadata.get('total_events', len(result.events))}. "
            f"non-spkHFO: {active_counts.get('non-spike HFO', 0)}. "
            f"spkHFO: {active_counts.get('spike-HFO', 0)}. "
            f"Classification: {metadata.get('classification_status', 'unknown')}. "
            f"Runtime: {self._format_duration(elapsed_s)}.",
            timeout_ms=0,
        )
        self._finish_hfo_worker_ui()

    @Slot(str, float)
    def _on_hfo_worker_failed(self, error_message: str, elapsed_s: float) -> None:
        self._stop_hfo_status_timer()
        start_s, stop_s = self._hfo_run_window_s or (0.0, 0.0)
        perf_start = self._hfo_perf_start if self._hfo_perf_start is not None else time.perf_counter()
        timed_mark(
            "after_hfo_detector",
            perf_start,
            raw=self._raw,
            visible_window_s=max(0.0, stop_s - start_s),
            notes=f"error: {error_message}",
        )
        self._show_status_message(
            "HFO detection failed "
            f"after {self._format_duration(elapsed_s)}."
        )
        self._finish_hfo_worker_ui()
        QMessageBox.warning(self, "HFO detector", str(error_message))

    @Slot(float)
    def _on_hfo_worker_cancelled(self, elapsed_s: float) -> None:
        self._stop_hfo_status_timer()
        start_s, stop_s = self._hfo_run_window_s or (0.0, 0.0)
        perf_start = self._hfo_perf_start if self._hfo_perf_start is not None else time.perf_counter()
        timed_mark(
            "after_hfo_detector",
            perf_start,
            raw=self._raw,
            visible_window_s=max(0.0, stop_s - start_s),
            notes="cancelled",
        )
        self._show_status_message(
            "HFO detection cancelled.",
            timeout_ms=0,
        )
        self._finish_hfo_worker_ui()

    def _finish_hfo_worker_ui(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel_hfo.setEnabled(False)
        self.btn_cancel_hfo.hide()

    def _clear_hfo_worker_refs(self) -> None:
        self._hfo_thread = None
        self._hfo_worker = None
        self._hfo_perf_start = None
        self._hfo_run_window_s = None
        self._hfo_cancel_requested = False
        self._hfo_expected_runtime_s = None
        self._hfo_runtime_complexity = None

    def _estimate_hfo_runtime_complexity(self, start_s: float, stop_s: float) -> float:
        duration_min = max(0.0, float(stop_s) - float(start_s)) / 60.0
        channel_count = max(1, len(self.state.selected_abs))
        detectors = self.hfo_params.get("active_candidate_detectors", DEFAULT_CANDIDATE_DETECTORS)
        detector_count = max(1, len(detectors) if isinstance(detectors, (list, tuple, set)) else 1)
        return max(1e-6, float(duration_min) * float(channel_count) * float(detector_count))

    def _estimate_hfo_runtime_seconds(self, complexity: float | None) -> float | None:
        if complexity is None or self._hfo_seconds_per_complexity_unit is None:
            return None
        return max(0.0, float(complexity) * float(self._hfo_seconds_per_complexity_unit))

    def _update_hfo_runtime_estimator(self, elapsed_s: float) -> None:
        complexity = self._hfo_runtime_complexity
        if complexity is None or float(complexity) <= 0.0 or float(elapsed_s) <= 0.0:
            return
        observed = float(elapsed_s) / float(complexity)
        if self._hfo_seconds_per_complexity_unit is None:
            self._hfo_seconds_per_complexity_unit = observed
        else:
            previous = float(self._hfo_seconds_per_complexity_unit)
            self._hfo_seconds_per_complexity_unit = 0.65 * previous + 0.35 * observed

    def _hfo_estimate_status_text(self, elapsed_s: float) -> str:
        expected_s = self._hfo_expected_runtime_s
        if expected_s is None:
            return "Estimated remaining: calculating."
        remaining_s = max(0.0, float(expected_s) - float(elapsed_s))
        return f"Estimated remaining: {self._format_duration(remaining_s)}."

    @Slot(str)
    def _on_gamma_worker_progress(self, message: str) -> None:
        self._gamma_completion_status_active = False
        start = self._gamma_perf_start if self._gamma_perf_start is not None else time.perf_counter()
        elapsed_s = max(0.0, time.perf_counter() - start)
        progress_fraction = self._gamma_progress_fraction(str(message))
        timing_text = f"time so far: {self._format_duration(elapsed_s)}"
        if progress_fraction is not None and progress_fraction > 0.0:
            eta_s = elapsed_s * (1.0 - progress_fraction) / progress_fraction
            timing_text += f", estimated time remaining: {self._format_duration(eta_s)}"
        self._show_status_message(
            f"Gamma spike detection processing. {message} ({timing_text})"
        )

    @Slot(object, float)
    def _on_gamma_worker_finished(self, result: object, elapsed_s: float) -> None:
        if not isinstance(result, GammaSpikeComputationResult):
            self._on_gamma_worker_failed("Gamma spike detector returned an unexpected result.", elapsed_s)
            return
        self._show_gamma_result(result)
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        start_s, stop_s = self._gamma_run_window_s or (0.0, 0.0)
        perf_start = self._gamma_perf_start if self._gamma_perf_start is not None else time.perf_counter()
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
        total_spikes = int(metadata.get("total_spikes", 0) or 0)
        gamma_spikes = self._gamma_positive_count(result)
        self._gamma_completion_status_active = True
        self._show_status_message(
            "Gamma analysis finished. "
            f"Total spikes: {total_spikes}. "
            f"Gamma-positive spikes: {gamma_spikes}. "
            f"Runtime: {self._format_duration(elapsed_s)}.",
            timeout_ms=0,
        )
        self._finish_gamma_worker_ui()

    @Slot(str, float)
    def _on_gamma_worker_failed(self, error_message: str, elapsed_s: float) -> None:
        self._gamma_completion_status_active = False
        start_s, stop_s = self._gamma_run_window_s or (0.0, 0.0)
        perf_start = self._gamma_perf_start if self._gamma_perf_start is not None else time.perf_counter()
        timed_mark(
            "after_gamma_spike_detector",
            perf_start,
            raw=self._raw,
            visible_window_s=max(0.0, stop_s - start_s),
            notes=f"error: {error_message}",
        )
        self._show_status_message(
            "Gamma spike detection failed "
            f"after {self._format_duration(elapsed_s)}."
        )
        self._finish_gamma_worker_ui()
        QMessageBox.warning(self, "Gamma spike detector", str(error_message))

    @Slot(float)
    def _on_gamma_worker_cancelled(self, elapsed_s: float) -> None:
        self._gamma_completion_status_active = False
        start_s, stop_s = self._gamma_run_window_s or (0.0, 0.0)
        perf_start = self._gamma_perf_start if self._gamma_perf_start is not None else time.perf_counter()
        timed_mark(
            "after_gamma_spike_detector",
            perf_start,
            raw=self._raw,
            visible_window_s=max(0.0, stop_s - start_s),
            notes="cancelled",
        )
        self._show_status_message(
            "Gamma spike detection cancelled after "
            f"{self._format_duration(elapsed_s)}."
        )
        self._finish_gamma_worker_ui()

    def _finish_gamma_worker_ui(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel_gamma.setEnabled(False)
        self.btn_cancel_gamma.hide()
        self._gamma_cancel_requested = False

    @Slot()
    def _clear_gamma_worker_refs(self) -> None:
        self._gamma_thread = None
        self._gamma_worker = None
        self._gamma_perf_start = None
        self._gamma_run_window_s = None

    @staticmethod
    def _gamma_positive_count(result: GammaSpikeComputationResult) -> int:
        total = 0
        for channel_result in result.channels:
            for event in channel_result.events:
                if (
                    event.gamma_power is not None
                    and event.gamma_duration_ms is not None
                    and (
                        float(event.gamma_power) > 0.0
                        or float(event.gamma_duration_ms) > 0.0
                    )
                ):
                    total += 1
        return int(total)

    @staticmethod
    def _gamma_progress_fraction(message: str) -> float | None:
        fractions = [
            (int(current), int(total))
            for current, total in re.findall(r"(\d+)\s*/\s*(\d+)", str(message))
            if int(total) > 0
        ]
        if not fractions:
            return None
        if len(fractions) >= 2 and "chunk" in str(message).lower():
            chunk_current, chunk_total = fractions[0]
            channel_current, channel_total = fractions[1]
            total_steps = max(1, chunk_total * channel_total)
            completed = max(0, chunk_current - 1) * channel_total + channel_current
            return min(1.0, max(0.0, completed / total_steps))
        current, total = fractions[-1]
        return min(1.0, max(0.0, current / total))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, int(round(float(seconds))))
        minutes, secs = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}h {minutes:02d}m {secs:02d}s"
        if minutes:
            return f"{minutes:d}m {secs:02d}s"
        return f"{secs:d}s"

    @staticmethod
    def _top_rei_channel(result: EIComputationResult) -> EIChannelResult | None:
        channels = list(result.channels or [])
        if not channels:
            return None
        return min(
            channels,
            key=lambda channel: (
                int(getattr(channel, "rank", 10**9)),
                -float(getattr(channel, "ei", 0.0) or 0.0),
                str(getattr(channel, "channel", "")),
            ),
        )

    def _show_status_message(self, message: str, *, timeout_ms: int = 15000) -> None:
        try:
            window = self.window()
            if isinstance(window, QMainWindow):
                status_bar = window.statusBar()
                if isinstance(status_bar, QStatusBar):
                    status_bar.showMessage(str(message), int(timeout_ms))
        except RuntimeError:
            return

    def _clear_status_message(self) -> None:
        try:
            window = self.window()
            if isinstance(window, QMainWindow):
                status_bar = window.statusBar()
                if isinstance(status_bar, QStatusBar):
                    status_bar.clearMessage()
        except RuntimeError:
            return

    def _clear_gamma_outputs(self) -> None:
        self._last_gamma_result = None
        self._gamma_summary_dialog = None
        self._gamma_review_dialog = None
        self.gammaSpikeMarkersChanged.emit({})
        if hasattr(self, "btn_open_gamma_summary"):
            self.btn_open_gamma_summary.setEnabled(False)
        if hasattr(self, "btn_open_gamma_review"):
            self.btn_open_gamma_review.setEnabled(False)
        if hasattr(self, "btn_export_gamma"):
            self.btn_export_gamma.setEnabled(False)

    def _clear_hfo_outputs(self) -> None:
        self._last_hfo_result = None
        self._hfo_summary_dialog = None
        self._hfo_event_grid_dialog = None
        self._pending_hfo_event_selection = None
        self.hfoMarkersChanged.emit({})
        if hasattr(self, "btn_open_hfo_summary"):
            self.btn_open_hfo_summary.setEnabled(False)
        if hasattr(self, "btn_open_hfo_event_grid"):
            self.btn_open_hfo_event_grid.setEnabled(False)
        if hasattr(self, "btn_export_hfo"):
            self.btn_export_hfo.setEnabled(False)

    def _show_hfo_result(self, result: HFOComputationResult) -> None:
        self._last_hfo_result = result
        self._hfo_summary_dialog = None
        self._hfo_event_grid_dialog = None
        self.btn_open_hfo_summary.setEnabled(True)
        self.btn_open_hfo_event_grid.setEnabled(True)
        self.btn_export_hfo.setEnabled(True)
        self.hfoMarkersChanged.emit(
            self._hfo_markers_from_events(self._hfo_events_for_review_grid(result))
        )
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        active_counts = self._hfo_active_label_counts(result)
        action_text = "HFO results imported" if bool(metadata.get("imported", False)) else "HFO detection complete"
        self._show_status_message(
            f"{action_text}: "
            f"{metadata.get('total_events', len(result.events))} events, "
            f"non-spkHFO {active_counts.get('non-spike HFO', 0)}, "
            f"spkHFO {active_counts.get('spike-HFO', 0)}, "
            f"classification {metadata.get('classification_status', 'unknown')}.",
            timeout_ms=20000,
        )

    def _hfo_active_label_counts(self, result: HFOComputationResult) -> dict[str, int]:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        classifier_name = str(metadata.get("detector_version", "") or "")
        counts = {label: 0 for label in HFO_DISPLAY_CLASSES if label != "deleted"}
        for event in result.events:
            label = self._normalize_hfo_display_class(
                self._hfo_display_class(event),
                classifier_name=classifier_name,
            )
            if label == "deleted":
                continue
            counts[label] = counts.get(label, 0) + 1
        return counts

    def _open_hfo_summary_dialog(self) -> None:
        result = self._last_hfo_result
        if result is None:
            QMessageBox.information(self, "HFO summary", "Run the HFO detector first.")
            return

        dialog = QDialog()
        dialog.setWindowTitle("HFO channel summary")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setSizeGripEnabled(True)
        dialog.resize(1040, 520)
        dialog.setMinimumSize(720, 420)
        layout = QVBoxLayout(dialog)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Channel level:"))
        level_combo = QComboBox()
        level_combo.addItem("All channels", userData="all")
        level_combo.addItem("At least one HFO", userData="hfo")
        level_combo.addItem("At least one spkHFO", userData="spike_hfo")
        controls_row.addWidget(level_combo)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        columns = [
            "Channel",
            "Candidates",
            "Accepted HFO",
            "non-spkHFO",
            "spkHFO",
            "Artifact",
            "HFO/min",
            "spkHFO/min",
            "Artifact %",
        ]
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(columns)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicatorShown(True)
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        window_s = metadata.get("analysis_window_s", [0.0, 0.0])
        duration_min = 0.0
        if isinstance(window_s, list) and len(window_s) >= 2:
            duration_min = max(0.0, float(window_s[1]) - float(window_s[0])) / 60.0

        summary_rows: list[dict[str, object]] = []
        for display_order, channel_result in enumerate(result.channels):
            channel_events = list(channel_result.events)
            active_channel_events = [
                event for event in channel_events
                if self._normalize_hfo_display_class(
                    self._hfo_display_class(event),
                    classifier_name=str(metadata.get("detector_version", "") or ""),
                ) != "deleted"
            ]
            event_labels = [
                self._normalize_hfo_display_class(
                    self._hfo_display_class(event),
                    classifier_name=str(metadata.get("detector_version", "") or ""),
                )
                for event in active_channel_events
            ]
            candidate_count = len(active_channel_events)
            deleted_count = len(channel_events) - candidate_count
            artifact_count = sum(1 for label in event_labels if label == "artifact")
            spike_count = sum(1 for label in event_labels if label in {"spike-HFO", "spike-eHFO"})
            non_spike_count = sum(1 for label in event_labels if label in {"non-spike HFO", "HFO", "eHFO"})
            accepted_count = non_spike_count + spike_count
            accepted_rate = float(accepted_count) / duration_min if duration_min > 0.0 else 0.0
            spike_rate = float(spike_count) / duration_min if duration_min > 0.0 else 0.0
            artifact_pct = 100.0 * float(artifact_count) / float(candidate_count) if candidate_count else 0.0
            channel_name = str(channel_result.channel)
            summary_rows.append(
                {
                    "display_order": int(display_order),
                    "channel": channel_name,
                    "channel_sort": channel_name.casefold(),
                    "candidate_count": int(candidate_count),
                    "accepted_count": int(accepted_count),
                    "non_spike_count": int(non_spike_count),
                    "spike_count": int(spike_count),
                    "artifact_count": int(artifact_count),
                    "deleted_count": int(deleted_count),
                    "accepted_rate": float(accepted_rate),
                    "spike_rate": float(spike_rate),
                    "artifact_pct": float(artifact_pct),
                }
            )

        sort_state: dict[str, object] = {
            "column": 0,
            "order": Qt.SortOrder.AscendingOrder,
            "channel_mode": "display",
        }

        def filtered_rows() -> list[dict[str, object]]:
            mode = str(level_combo.currentData() or "all")
            if mode == "hfo":
                return [
                    row for row in summary_rows
                    if int(row["accepted_count"]) > 0
                ]
            if mode == "spike_hfo":
                return [
                    row for row in summary_rows
                    if int(row["spike_count"]) > 0
                ]
            return list(summary_rows)

        def populate(rows: list[dict[str, object]]) -> None:
            table.setSortingEnabled(False)
            table.setRowCount(0)
            for row_data in rows:
                row_idx = table.rowCount()
                table.insertRow(row_idx)
                values = [
                    str(row_data["channel"]),
                    str(int(row_data["candidate_count"])),
                    str(int(row_data["accepted_count"])),
                    str(int(row_data["non_spike_count"])),
                    str(int(row_data["spike_count"])),
                    str(int(row_data["artifact_count"])),
                    f"{float(row_data['accepted_rate']):.3f}",
                    f"{float(row_data['spike_rate']):.3f}",
                    f"{float(row_data['artifact_pct']):.1f}",
                ]
                channel_name = str(row_data["channel"])
                for col_idx, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, channel_name)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col_idx > 0:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(row_idx, col_idx, item)
            table.setSortingEnabled(False)
            self.eiSummaryOrderChanged.emit([str(row["channel"]) for row in rows])

        def sorted_rows_for_current_state() -> list[dict[str, object]]:
            rows = filtered_rows()
            column = int(sort_state["column"])
            reverse = sort_state["order"] == Qt.SortOrder.DescendingOrder
            if column == 0:
                if sort_state["channel_mode"] == "alphabetical":
                    return sorted(rows, key=lambda row: str(row["channel_sort"]))
                return sorted(rows, key=lambda row: int(row["display_order"]))
            key_by_column: dict[int, str] = {
                1: "candidate_count",
                2: "accepted_count",
                3: "non_spike_count",
                4: "spike_count",
                5: "artifact_count",
                6: "accepted_rate",
                7: "spike_rate",
                8: "artifact_pct",
            }
            key_name = key_by_column.get(column, "display_order")
            return sorted(
                rows,
                key=lambda row: float(row[key_name]),
                reverse=reverse,
            )

        def refresh_summary_table() -> None:
            populate(sorted_rows_for_current_state())

        def sort_summary_table(column: int) -> None:
            if int(column) == 0:
                sort_state["column"] = 0
                sort_state["order"] = Qt.SortOrder.AscendingOrder
                sort_state["channel_mode"] = (
                    "alphabetical"
                    if sort_state["channel_mode"] == "display"
                    else "display"
                )
                header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
                refresh_summary_table()
                return

            if sort_state["column"] == int(column):
                sort_state["order"] = (
                    Qt.SortOrder.DescendingOrder
                    if sort_state["order"] == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                sort_state["column"] = int(column)
                sort_state["order"] = Qt.SortOrder.DescendingOrder
            sort_state["channel_mode"] = "display"
            header.setSortIndicator(int(column), sort_state["order"])
            refresh_summary_table()

        def activate_summary_row(row: int, _column: int) -> None:
            item = table.item(int(row), 0)
            if item is None:
                return
            channel_name = item.data(Qt.ItemDataRole.UserRole)
            if channel_name is None:
                channel_name = item.text()
            self.eiSummaryChannelActivated.emit(str(channel_name))

        header.sectionClicked.connect(sort_summary_table)
        level_combo.currentIndexChanged.connect(lambda _index: refresh_summary_table())
        table.cellClicked.connect(activate_summary_row)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        refresh_summary_table()
        layout.addWidget(table)
        active_counts = self._hfo_active_label_counts(result)
        edge_padding_s = metadata.get("boundary_padding_s")
        edge_excluded = int(metadata.get("boundary_excluded_events", 0) or 0)
        edge_text = ""
        try:
            if float(edge_padding_s or 0.0) > 0.0 or edge_excluded > 0:
                edge_text = (
                    f"   edge ignored: {float(edge_padding_s or 0.0):g}s"
                    f" ({edge_excluded} candidate events excluded)"
                )
        except (TypeError, ValueError):
            if edge_excluded > 0:
                edge_text = f"   edge excluded: {edge_excluded} candidate events"
        footer = QLabel(
            f"Total events: {len(result.events)}   "
            f"HFO: {active_counts.get('HFO', 0)}   "
            f"non-spkHFO: {active_counts.get('non-spike HFO', 0)}   "
            f"spkHFO: {active_counts.get('spike-HFO', 0)}   "
            f"eHFO: {active_counts.get('eHFO', 0)}   "
            f"spk-eHFO: {active_counts.get('spike-eHFO', 0)}   "
            f"artifact: {active_counts.get('artifact', 0)}"
            f"{edge_text}"
        )
        layout.addWidget(footer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self._hfo_summary_dialog = dialog
        dialog.destroyed.connect(lambda *_args: setattr(self, "_hfo_summary_dialog", None))
        dialog.show()

    def _open_hfo_event_grid_dialog(self) -> None:
        result = self._last_hfo_result
        if result is None:
            QMessageBox.information(self, "HFO event grid", "Run the HFO detector first.")
            return

        dialog = QDialog()
        dialog.setWindowTitle("HFO event grid")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setSizeGripEnabled(True)
        dialog.resize(1160, 820)
        dialog.setMinimumSize(720, 520)
        dialog.setStyleSheet("QDialog { background-color: #f3f6fa; color: #111827; }")
        layout = QVBoxLayout(dialog)

        grid = ExpertEventGrid(title="HFO Event Grid")
        grid.set_raw(self._raw)
        grid.set_waveform_callback(self._fetch_hfo_event_waveform)
        grid.set_events(self._hfo_events_for_review_grid(result), title="Computed HFO events")
        grid.requestJumpToTime.connect(
            lambda time_s, channel: self.hfoEventActivated.emit(str(channel), float(time_s))
        )
        grid.filteredEventsChanged.connect(
            lambda events: self.hfoMarkersChanged.emit(
                self._hfo_markers_from_events(list(events))
            )
        )
        grid.eventClassChanged.connect(
            lambda _event: self._on_hfo_event_class_changed(result)
        )
        layout.addWidget(grid)
        pending_selection = self._pending_hfo_event_selection
        self._pending_hfo_event_selection = None
        if pending_selection is not None:
            grid.select_event_at(pending_selection[0], pending_selection[1])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self._hfo_event_grid_dialog = dialog
        dialog.destroyed.connect(lambda *_args: setattr(self, "_hfo_event_grid_dialog", None))
        dialog.show()

    def open_hfo_event_at(self, channel_name: str, time_s: float) -> None:
        result = self._last_hfo_result
        if result is None:
            return
        if self._hfo_event_grid_dialog is not None:
            self._hfo_event_grid_dialog.close()
            self._hfo_event_grid_dialog = None
        self._pending_hfo_event_selection = (str(channel_name), float(time_s))
        self._open_hfo_event_grid_dialog()

    def _fetch_hfo_event_waveform(
        self,
        channel_name: str,
        start_s: float,
        stop_s: float,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        if self._ei_data_callback is None:
            return None, None
        try:
            channel_text = str(channel_name)
            abs_idx = self._ch_names_displayed.index(channel_text)
            start = max(0.0, float(start_s))
            stop = max(start, float(stop_s))
        except (ValueError, TypeError):
            return None, None
        if stop <= start:
            return None, None

        try:
            data, fs, _names = self._ei_data_callback([int(abs_idx)], start, stop)
        except Exception:
            return None, None

        arr = np.asarray(data, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 2:
            return None, None
        sfreq = float(fs)
        if sfreq <= 0.0:
            return None, None
        waveform = np.asarray(arr[0], dtype=float).reshape(-1)
        times = start + np.arange(waveform.size, dtype=float) / sfreq
        return waveform, times

    def _hfo_events_for_review_grid(self, result: HFOComputationResult) -> list[ExpertEvent]:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        source_file = str(metadata.get("source_file_name") or metadata.get("source_file_path") or "")
        detection_fs = float(metadata.get("detection_fs", metadata.get("input_fs", 1000.0)) or 1000.0)
        data_start_s = float(metadata.get("data_start_s", 0.0) or 0.0)
        classification_status = self._effective_hfo_classification_status(result)
        classifier_name = str(metadata.get("detector_version", "") or "")
        channel_event_counts: dict[str, int] = {}

        events: list[ExpertEvent] = []
        for index, event in enumerate(result.events, start=1):
            label = self._hfo_display_class(event)
            if not label or label == "candidate":
                label = "unclassified"
            normalized_label = self._normalize_hfo_display_class(
                label,
                classifier_name=classifier_name,
            )
            if self._hfo_classification_failed(classification_status):
                normalized_label = "unclassified"
            accepted_hfo = normalized_label not in {"artifact", "deleted", "unclassified"}
            spike_hfo = normalized_label in {"spike-HFO", "spike-eHFO"}
            detector = str(event.detector)
            channel_key = str(event.channel)
            channel_event_counts[channel_key] = channel_event_counts.get(channel_key, 0) + 1
            event_id = str(channel_event_counts[channel_key])
            real_hfo_probability = self._coalesce_hfo_score(
                getattr(event, "real_hfo_probability", None),
            )
            artifact_probability = self._coalesce_hfo_score(
                getattr(event, "artifact_probability", None),
                getattr(event, "artifact_score", None),
            )
            if real_hfo_probability is None and artifact_probability is not None:
                real_hfo_probability = 1.0 - float(artifact_probability)
            if artifact_probability is None and real_hfo_probability is not None:
                artifact_probability = 1.0 - float(real_hfo_probability)
            spike_hfo_probability = self._coalesce_hfo_score(
                getattr(event, "spike_hfo_probability", None),
                getattr(event, "spike_score", None),
            )
            band_label = (
                f"{float(event.low_freq_hz):g}-{float(event.high_freq_hz):g} Hz"
                if event.low_freq_hz is not None and event.high_freq_hz is not None
                else "80-300 Hz"
            )

            waveform = None
            waveform_start = None
            waveform_end = None
            waveform_times = None
            if event.waveform is not None:
                waveform = np.asarray(event.waveform, dtype=float).reshape(-1)
                if waveform.size:
                    if event.real_start_sample is not None:
                        waveform_start = data_start_s + float(event.real_start_sample) / detection_fs
                        waveform_end = waveform_start + float(waveform.size) / detection_fs
                    else:
                        waveform_start = float(event.start_time_s)
                        waveform_end = float(event.end_time_s)
                    waveform_times = np.linspace(
                        float(waveform_start),
                        float(waveform_end),
                        int(waveform.size),
                        endpoint=False,
                    )

            events.append(
                ExpertEvent(
                    edf_file=source_file,
                    channel=str(event.channel),
                    start=float(event.start_time_s),
                    end=float(event.end_time_s),
                    detector=detector,
                    artifact=accepted_hfo,
                    spike=spike_hfo,
                    event_number=event_id,
                    model_class=normalized_label,
                    band_label=band_label,
                    boundary_warning=bool(getattr(event, "boundary_warning", event.is_boundary)),
                    real_hfo_probability=real_hfo_probability,
                    artifact_probability=artifact_probability,
                    spike_hfo_probability=spike_hfo_probability,
                    classification_status=classification_status,
                    manual_class=self._normalize_hfo_display_class(
                        getattr(event, "manual_class", None),
                        classifier_name=classifier_name,
                    )
                    if getattr(event, "manual_class", None)
                    else "",
                    manual_review_status=str(getattr(event, "manual_review_status", "unreviewed") or "unreviewed"),
                    source_event=event,
                    waveform=waveform,
                    waveform_start=waveform_start,
                    waveform_end=waveform_end,
                    waveform_times=waveform_times,
                    waveform_unavailable=False,
                    detail_lines=[
                        f"real HFO: {self._format_optional_score(getattr(event, 'real_hfo_probability', None))}",
                        f"artifact: {self._format_optional_score(getattr(event, 'artifact_probability', event.artifact_score))}",
                        f"spike-HFO: {self._format_optional_score(getattr(event, 'spike_hfo_probability', event.spike_score))}",
                        f"model class: {getattr(event, 'final_model_class', event.classification_label) or ''}",
                        f"classification status: {classification_status}",
                        f"manual class: {getattr(event, 'manual_class', None) or ''}",
                        f"review: {getattr(event, 'manual_review_status', 'unreviewed')}",
                        f"band: {event.low_freq_hz:g}-{event.high_freq_hz:g} Hz",
                        f"boundary: {'yes' if event.is_boundary else 'no'}",
                    ],
                )
            )
        return events

    def _hfo_display_class(self, event) -> str:
        return str(
            getattr(event, "manual_class", None)
            or getattr(event, "final_model_class", None)
            or getattr(event, "classification_label", "")
            or ""
        ).strip()

    def _refresh_hfo_review_metadata(self, result: HFOComputationResult) -> None:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        classifier_name = str(metadata.get("detector_version", "") or "")
        official_counts: dict[str, int] = {}
        reviewed_count = 0
        deleted_count = 0
        for event in result.events:
            label = self._normalize_hfo_display_class(
                self._hfo_display_class(event),
                classifier_name=classifier_name,
            )
            official_counts[label] = official_counts.get(label, 0) + 1
            if label == "deleted":
                deleted_count += 1
            if str(getattr(event, "manual_review_status", "") or "").strip().lower() == "reviewed":
                reviewed_count += 1
        metadata["official_label_counts"] = official_counts
        metadata["manual_reviewed_events"] = int(reviewed_count)
        metadata["manual_deleted_events"] = int(deleted_count)
        result.metadata = metadata

    def _on_hfo_event_class_changed(self, result: HFOComputationResult) -> None:
        self._refresh_hfo_review_metadata(result)
        self.settingsChanged.emit()

    def _normalize_hfo_display_class(
        self,
        label: object,
        *,
        classifier_name: str | None = None,
    ) -> str:
        text = str(label or "").strip()
        lowered = text.lower().replace("_", "-")
        if not lowered or lowered in {"candidate", "unknown", "none"}:
            return "unclassified"
        if lowered in {"deleted", "excluded"}:
            return "deleted"
        if "artifact" in lowered:
            return "artifact"
        if lowered in {"spike-ehfo", "spike ehfo", "spkehfo", "spk-ehfo", "spk ehfo"}:
            return "spike-eHFO"
        if lowered in {"ehfo", "e-hfo"}:
            return "eHFO"
        if lowered in {"hfo", "real hfo", "real-hfo", "non-spike hfo", "non-spkhfo", "non-spk hfo"}:
            if str(classifier_name or "").strip() == HFO_CLASSIFIER_EHFO and lowered in {"hfo", "real hfo", "real-hfo"}:
                return "HFO"
            return "non-spike HFO"
        if lowered in {"spike-hfo", "spkhfo", "spk-hfo", "spk hfo", "spike hfo"}:
            return "spike-HFO"
        return text

    def _effective_hfo_classification_status(self, result: HFOComputationResult) -> str:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        status = str(metadata.get("classification_status", "") or "").strip()
        if status:
            return status
        if any(
            self._hfo_display_class(event)
            and self._coalesce_hfo_score(
                getattr(event, "real_hfo_probability", None),
                getattr(event, "artifact_probability", None),
                getattr(event, "artifact_score", None),
                getattr(event, "spike_hfo_probability", None),
                getattr(event, "spike_score", None),
            ) is not None
            for event in result.events
        ):
            return "ok"
        return "unknown"

    @staticmethod
    def _hfo_classification_failed(status: object) -> bool:
        text = str(status or "").strip().lower()
        return bool(text and text not in {"ok", "no_events"})

    def _hfo_display_event_number(self, value: object) -> str:
        text = str(value or "").strip()
        match = re.search(r"(\d+)$", text)
        if match:
            return str(int(match.group(1)))
        return text

    @staticmethod
    def _coerce_float(value: object, fallback: float) -> float:
        try:
            numeric = float(cast(Any, value))
        except (TypeError, ValueError):
            return float(fallback)
        return numeric if np.isfinite(numeric) else float(fallback)

    def _coalesce_hfo_score(self, *values: object) -> float | None:
        for value in values:
            if value is None:
                continue
            try:
                score = float(cast(Any, value))
            except (TypeError, ValueError):
                continue
            if np.isfinite(score):
                return score
        return None

    def _hfo_markers_from_events(self, events: list[ExpertEvent]) -> dict[str, list[dict[str, float | str]]]:
        markers: dict[str, list[dict[str, float | str]]] = {}
        for event in events:
            if self._normalize_hfo_display_class(event.review_label) == "deleted":
                continue
            channel = str(event.channel)
            center_time_s = 0.5 * (float(event.start) + float(event.end))
            kind = str(event.review_label or "unclassified")
            markers.setdefault(channel, []).append(
                {
                    "time_s": float(center_time_s),
                    "start_time_s": float(event.start),
                    "end_time_s": float(event.end),
                    "kind": kind,
                    "event_id": str(event.event_number or ""),
                }
            )
        return markers

    def _export_hfo_results(self) -> None:
        result = self._last_hfo_result
        if result is None:
            QMessageBox.information(
                self,
                "Export HFO events",
                "Run the HFO detector before exporting events.",
            )
            return
        output_dir = self._choose_export_dir("Select folder for HFO export")
        if output_dir is None:
            return
        if not self._confirm_export_overwrite(
            output_dir,
            [
                "hfo_channel_summary.csv",
                "hfo_events.csv",
                "hfo_metadata.json",
                "README.txt",
            ],
            title="Export HFO events",
        ):
            return
        try:
            written_paths = export_hfo_result(output_dir, result)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export HFO events",
                f"Could not export HFO events:\n{exc}",
            )
            return
        self._last_export_dir = output_dir
        QMessageBox.information(
            self,
            "Export HFO events",
            f"Exported {len(written_paths)} files to:\n{output_dir}",
        )

    @staticmethod
    def _format_optional_score(value: float | None) -> str:
        if value is None:
            return ""
        try:
            score = float(value)
        except (TypeError, ValueError):
            return ""
        if not np.isfinite(score):
            return ""
        return f"{score:.3f}"

    def _show_gamma_result(self, result: GammaSpikeComputationResult) -> None:
        self._last_gamma_result = result
        self._gamma_summary_dialog = None
        self._gamma_review_dialog = None
        self.btn_open_gamma_summary.setEnabled(True)
        self.btn_open_gamma_review.setEnabled(True)
        self.btn_export_gamma.setEnabled(True)
        self.gammaSpikeMarkersChanged.emit(
            self._gamma_spike_markers_from_result(result, mode="all")
        )

    def _export_gamma_results(self) -> None:
        result = self._last_gamma_result
        if result is None:
            QMessageBox.information(
                self,
                "Export gamma results",
                "Run the gamma spike detector before exporting results.",
            )
            return
        output_dir = self._choose_export_dir("Select folder for gamma export")
        if output_dir is None:
            return
        if not self._confirm_export_overwrite(
            output_dir,
            [
                "gamma_channel_summary.csv",
                "gamma_spike_events.csv",
                "gamma_metadata.json",
                "README.txt",
            ],
            title="Export gamma results",
        ):
            return
        try:
            written_paths = export_gamma_spike_result(output_dir, result)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export gamma results",
                f"Could not export gamma results:\n{exc}",
            )
            return
        self._last_export_dir = output_dir
        QMessageBox.information(
            self,
            "Export gamma results",
            f"Exported {len(written_paths)} files to:\n{output_dir}",
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

        regular_border = "#4091ff"
        gamma_border = "#ff9743"
        grid_settings = {
            "columns": 6,
            "rows": 4,
            "card_height": 150,
        }

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

        controls.addSpacing(12)
        controls.addWidget(QLabel("Columns:"))
        grid_cols_spin = QSpinBox()
        grid_cols_spin.setRange(2, 10)
        grid_cols_spin.setValue(int(grid_settings["columns"]))
        controls.addWidget(grid_cols_spin)

        controls.addWidget(QLabel("Rows:"))
        grid_rows_spin = QSpinBox()
        grid_rows_spin.setRange(1, 8)
        grid_rows_spin.setValue(int(grid_settings["rows"]))
        controls.addWidget(grid_rows_spin)

        controls.addWidget(QLabel("Card size:"))
        card_size_spin = QSpinBox()
        card_size_spin.setRange(110, 280)
        card_size_spin.setSingleStep(10)
        card_size_spin.setSuffix(" px")
        card_size_spin.setValue(int(grid_settings["card_height"]))
        controls.addWidget(card_size_spin)

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
        gamma_filter_check.setChecked(True)
        gamma_filter_check.hide()
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
        zoom_plot.setMinimumHeight(180)
        zoom_plot.setBackground("w")
        zoom_plot.setLabel("bottom", "Time", units="s")
        zoom_plot.setLabel("left", "Raw", units="uV")
        zoom_plot.setTitle("Raw signal", color="#111111", size="10pt")
        zoom_plot.showGrid(x=True, y=True, alpha=0.25)

        filtered_zoom_plot = pg.PlotWidget()
        filtered_zoom_plot.setMinimumHeight(180)
        filtered_zoom_plot.setBackground("w")
        filtered_zoom_plot.setLabel("bottom", "Time", units="s")
        filtered_zoom_plot.setLabel("left", "Filtered", units="uV")
        filtered_zoom_plot.setTitle("Filtered 30-100 Hz", color="#111111", size="10pt")
        filtered_zoom_plot.showGrid(x=True, y=True, alpha=0.25)

        tf_plot = pg.PlotWidget()
        tf_plot.setMinimumHeight(220)
        tf_plot.setBackground("w")
        tf_plot.setLabel("bottom", "Time", units="s")
        tf_plot.setLabel("left", "Frequency", units="Hz")
        tf_plot.showGrid(x=True, y=True, alpha=0.18)
        tf_color_bar: Any | None = None

        zoom_plot_splitter = QSplitter(Qt.Orientation.Vertical)
        zoom_plot_splitter.setChildrenCollapsible(False)
        zoom_plot_splitter.addWidget(zoom_plot)
        zoom_plot_splitter.addWidget(filtered_zoom_plot)
        zoom_plot_splitter.addWidget(tf_plot)
        zoom_plot_splitter.setStretchFactor(0, 2)
        zoom_plot_splitter.setStretchFactor(1, 2)
        zoom_plot_splitter.setStretchFactor(2, 2)
        zoom_plot_splitter.setSizes([180, 180, 240])
        zoom_layout.addWidget(zoom_plot_splitter, 1)

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
            "The filtered panel is 30-100 Hz for display only; saved gamma results are unchanged."
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
            return self._gamma_row_official_class(row) == "gamma"

        def gamma_row_color_tuple(row: GammaReviewRow) -> tuple[int, int, int]:
            return (255, 151, 67) if is_gamma_row(row) else (64, 145, 255)

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
                number = float(cast(Any, value))
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

            event_start = self._coerce_float(row.get("event_start_time_s"), time_s)
            event_stop = self._coerce_float(row.get("event_stop_time_s"), time_s)
            if np.isfinite(event_start) and np.isfinite(event_stop):
                if event_stop < event_start:
                    event_start, event_stop = event_stop, event_start
                if event_stop <= event_start:
                    event_stop = event_start + 1e-6
                clipped_x0 = max(float(t_arr[0]), event_start)
                clipped_x1 = min(float(t_arr[-1]), event_stop)
                if clipped_x1 > clipped_x0:
                    event_color = gamma_row_color_tuple(row)
                    event_region = QGraphicsRectItem(
                        QRectF(
                            clipped_x0,
                            marker_y0,
                            max(0.0, clipped_x1 - clipped_x0),
                            max(0.0, marker_y1 - marker_y0),
                        )
                    )
                    event_region.setBrush(pg.mkBrush(*event_color, 48))
                    event_region.setPen(pg.mkPen((*event_color, 220), width=1.5))
                    event_region.setZValue(8)
                    plot.addItem(event_region)

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
                    x = float(cast(Any, value))
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
                gamma_region = QGraphicsRectItem(
                    QRectF(
                        clipped_x0,
                        marker_y0,
                        max(0.0, clipped_x1 - clipped_x0),
                        max(0.0, marker_y1 - marker_y0),
                    )
                )
                gamma_region.setBrush(pg.mkBrush(255, 151, 67, 45))
                gamma_region.setPen(pg.mkPen((255, 151, 67, 190), width=1.5, style=Qt.PenStyle.DashLine))
                gamma_region.setZValue(10)
                plot.addItem(gamma_region)
                for x_edge in (clipped_x0, clipped_x1):
                    edge_line = plot.plot(
                        [x_edge, x_edge],
                        [marker_y0, marker_y1],
                        pen=pg.mkPen((255, 151, 67, 220), width=2, style=Qt.PenStyle.DashLine),
                    )
                    edge_line.setZValue(14)

        def update_time_frequency(row: GammaReviewRow, times: np.ndarray | None, waveform: np.ndarray | None) -> None:
            nonlocal tf_color_bar
            tf_plot.clear()
            tf_plot.setBackground("w")
            try:
                time_s = float(row["time_s"])
            except (TypeError, ValueError):
                time_s = 0.0

            t_arr = None if times is None else np.asarray(times, dtype=float).reshape(-1)
            y_arr = None if waveform is None else np.asarray(waveform, dtype=float).reshape(-1)
            if t_arr is None or y_arr is None or t_arr.size < 16 or t_arr.size != y_arr.size:
                tf_plot.setXRange(time_s - 1.0, time_s + 1.0, padding=0.02)
                tf_plot.setYRange(20.0, 140.0, padding=0.02)
                return

            dt = float(np.median(np.diff(t_arr)))
            if not np.isfinite(dt) or dt <= 0.0:
                return
            fs = 1.0 / dt
            nperseg = int(min(max(32, round(0.064 * fs)), y_arr.size))
            if nperseg < 16:
                return
            hop_samples = max(1, int(round(0.006 * fs)))
            noverlap = int(max(0, nperseg - hop_samples))
            nfft_target = max(nperseg, int(round(0.5 * fs)))
            nfft = int(2 ** np.ceil(np.log2(max(16, nfft_target))))
            nfft = min(max(nfft, nperseg), 4096)
            try:
                freqs, rel_times, power = signal.spectrogram(
                    y_arr - float(np.nanmean(y_arr)),
                    fs=fs,
                    window="hann",
                    nperseg=nperseg,
                    noverlap=noverlap,
                    nfft=nfft,
                    detrend=False,
                    scaling="spectrum",
                    mode="magnitude",
                )
            except Exception:
                return

            freq_mask = (freqs >= 20.0) & (freqs <= min(160.0, 0.5 * fs))
            if not np.any(freq_mask) or rel_times.size == 0:
                return

            freqs = np.asarray(freqs[freq_mask], dtype=float)
            power = np.asarray(power[freq_mask, :], dtype=float)
            log_power = np.log10(np.maximum(power, 1e-12))
            finite_power = log_power[np.isfinite(log_power)]
            if finite_power.size:
                low_level = float(np.percentile(finite_power, 5.0))
                high_level = float(np.percentile(finite_power, 98.0))
                if high_level <= low_level:
                    high_level = low_level + 1.0
            else:
                low_level, high_level = -12.0, -6.0

            tf_image = pg.ImageItem(axisOrder="row-major")
            tf_image.setImage(log_power, levels=(low_level, high_level), autoLevels=False)
            try:
                tf_image.setOpts(autoDownsample=False)
            except Exception:
                pass
            color_map = cast(Any, pg.colormap.get("viridis"))
            if color_map is not None:
                tf_image.setLookupTable(np.asarray(color_map.getLookupTable(), dtype=np.float64))

            x0 = float(t_arr[0] + rel_times[0])
            if rel_times.size >= 2:
                dt_spec = float(np.median(np.diff(rel_times)))
                width = float((rel_times[-1] - rel_times[0]) + dt_spec)
                x0 -= 0.5 * dt_spec
            else:
                width = max(dt, float(t_arr[-1] - t_arr[0]))
                x0 -= 0.5 * width
            if freqs.size >= 2:
                df = float(np.median(np.diff(freqs)))
                y0 = float(freqs[0]) - 0.5 * df
                height = float((freqs[-1] - freqs[0]) + df)
            else:
                y0 = float(freqs[0]) - 1.0
                height = 2.0

            tf_image.setRect(QRectF(x0, y0, max(width, dt), max(height, 1.0)))
            tf_plot.addItem(tf_image)
            if tf_color_bar is None:
                tf_color_bar = pg.ColorBarItem(
                    values=(low_level, high_level),
                    colorMap=color_map if color_map is not None else "viridis",
                    label="log10 power",
                    interactive=False,
                )
                tf_color_bar.setImageItem(tf_image, insert_in=tf_plot.getPlotItem())
            else:
                tf_color_bar.setImageItem(tf_image, insert_in=tf_plot.getPlotItem())
                tf_color_bar.setLevels((low_level, high_level))
            tf_plot.setXRange(float(t_arr[0]), float(t_arr[-1]), padding=0.02)
            tf_plot.setYRange(max(20.0, y0), min(160.0, y0 + height), padding=0.02)

            spike_line = pg.InfiniteLine(
                pos=time_s,
                angle=90,
                pen=pg.mkPen((35, 35, 35), width=1.5, style=Qt.PenStyle.DashLine),
            )
            spike_line.setZValue(20)
            tf_plot.addItem(spike_line)

        def reset_waveform_view(
            plot: pg.PlotWidget,
            row: GammaReviewRow,
            times: np.ndarray | None,
            waveform: np.ndarray | None,
        ) -> None:
            try:
                time_s = float(row["time_s"])
            except (TypeError, ValueError):
                time_s = 0.0
            t_arr = None if times is None else np.asarray(times, dtype=float).reshape(-1)
            y_arr = None if waveform is None else np.asarray(waveform, dtype=float).reshape(-1)
            if t_arr is None or y_arr is None or t_arr.size < 2 or t_arr.size != y_arr.size:
                plot.setXRange(time_s - 0.45, time_s + 0.45, padding=0.02)
                plot.enableAutoRange(axis="y", enable=True)
                return
            plot.setXRange(float(t_arr[0]), float(t_arr[-1]), padding=0.02)
            finite_y = y_arr[np.isfinite(y_arr)]
            if not finite_y.size:
                plot.enableAutoRange(axis="y", enable=True)
                return
            y_min = float(np.min(finite_y))
            y_max = float(np.max(finite_y))
            if y_max <= y_min:
                y_min -= 1.0
                y_max += 1.0
            y_pad = 0.08 * (y_max - y_min)
            plot.setYRange(y_min - y_pad, y_max + y_pad, padding=0.02)

        def reset_current_gamma_zoom() -> None:
            rows = list(state["rows"])
            index = int(state["index"])
            if index < 0 or index >= len(rows):
                return
            row = rows[index]
            times, waveform = self._fetch_gamma_event_waveform(row, half_window_s=0.45)
            tf_times, tf_waveform = self._fetch_gamma_event_waveform(row, half_window_s=1.0)
            plotted_waveform = (
                maybe_gamma_filter_trace(times, waveform)
                if times is not None and waveform is not None
                else waveform
            )
            reset_waveform_view(zoom_plot, row, times, waveform)
            reset_waveform_view(filtered_zoom_plot, row, times, plotted_waveform)
            update_time_frequency(row, tf_times, tf_waveform)
            self.gammaSpikeEventActivated.emit(str(row["channel"]), float(row["time_s"]))

        def on_zoom_plot_clicked(event: Any) -> None:
            try:
                is_double = bool(event.double())
            except Exception:
                is_double = False
            if not is_double:
                return
            reset_current_gamma_zoom()
            try:
                event.accept()
            except Exception:
                pass

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
            info_parts = [
                event_type,
                f"Classifier proposition: {row.get('model_class', '')}",
                f"Review: {row.get('manual_review_status', 'unreviewed')}",
                "Dashed line: detected spike",
            ]
            if is_gamma_row(row):
                info_parts.append("Orange selection: estimated gamma activity window")
            zoom_event_info.setText(" | ".join(info_parts))
            zoom_event_info.setStyleSheet(
                f"color: #111111; background: #ffffff; border: 1px solid #d0d0d0; "
                f"border-left: 5px solid {event_color}; border-radius: 4px; "
                "padding: 4px 8px; font-weight: 600;"
            )
            gamma_class_combo.blockSignals(True)
            gamma_class_combo.setCurrentText(self._gamma_row_official_class(row))
            gamma_class_combo.blockSignals(False)
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

            for plot in (zoom_plot, filtered_zoom_plot):
                plot.clear()
                plot.setBackground("w")
            times, waveform = self._fetch_gamma_event_waveform(row, half_window_s=0.45)
            tf_times, tf_waveform = self._fetch_gamma_event_waveform(row, half_window_s=1.0)
            filtered_waveform = waveform
            if times is not None and waveform is not None:
                filtered_waveform = maybe_gamma_filter_trace(times, waveform)
                zoom_plot.plot(times, waveform, pen=pg.mkPen("#222222", width=1.5))
                filtered_zoom_plot.plot(times, filtered_waveform, pen=pg.mkPen("#222222", width=1.5))
            else:
                filtered_waveform = None
            draw_analysis_markers(zoom_plot, row, times, waveform)
            draw_analysis_markers(filtered_zoom_plot, row, times, filtered_waveform)
            reset_waveform_view(zoom_plot, row, times, waveform)
            reset_waveform_view(filtered_zoom_plot, row, times, filtered_waveform)
            update_time_frequency(row, tf_times, tf_waveform)
            self.gammaSpikeEventActivated.emit(channel, time_s)

        review_row = QHBoxLayout()
        review_row.addWidget(QLabel("Official class:"))
        gamma_class_combo = QComboBox()
        gamma_class_combo.addItem("gamma", userData="gamma")
        gamma_class_combo.addItem("non-gamma", userData="non-gamma")
        gamma_class_combo.addItem("unclassified", userData="unclassified")
        gamma_class_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff;
                color: #111111;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 130px;
            }
        """)
        review_row.addWidget(gamma_class_combo)
        review_row.addStretch(1)
        zoom_layout.insertLayout(3, review_row)

        def apply_gamma_manual_class(label: str) -> None:
            rows = list(state["rows"])
            index = int(state["index"])
            if index < 0 or index >= len(rows):
                return
            row = rows[index]
            normalized = self._normalize_gamma_manual_class(label)
            row["manual_class"] = normalized
            row["manual_review_status"] = "reviewed"
            source_event = row.get("source_event")
            if isinstance(source_event, GammaSpikeEventResult):
                source_event.manual_class = normalized
                source_event.manual_review_status = "reviewed"
            self.gammaSpikeMarkersChanged.emit(
                self._gamma_spike_markers_from_review_rows(list(state["rows"]))
            )
            update_zoom(row)

        gamma_class_combo.currentTextChanged.connect(apply_gamma_manual_class)

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
            state["current_page"] = index // grid_page_size()
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
            card_height = int(grid_settings["card_height"])
            plot_height = max(70, card_height - 58)
            card.setMinimumHeight(card_height)
            plot.setMinimumHeight(plot_height)
            plot.setMaximumHeight(plot_height + 30)
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
            time_s = self._coerce_float(row_data.get("time_s"), 0.0)
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

            start = self._coerce_float(row_data.get("event_start_time_s"), time_s)
            stop = self._coerce_float(row_data.get("event_stop_time_s"), time_s)
            footer = QLabel(f"{start:.3f} - {stop:.3f} s")
            footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            footer.setStyleSheet("color: #555; font-size: 10px;")
            layout.addWidget(footer)

            card.clicked.connect(show_zoom)
            plot_scene = cast(Any, plot.scene())
            plot_scene.sigMouseClicked.connect(lambda _event, idx=global_index: show_zoom(idx))
            return card

        def update_page_controls() -> None:
            page_size = grid_page_size()
            total_pages = max(1, (len(state["rows"]) + page_size - 1) // page_size)
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
            columns = grid_columns()
            page_size = grid_page_size()
            start_idx = int(state["current_page"]) * page_size
            end_idx = min(start_idx + page_size, len(rows))
            for local_index, row_data in enumerate(rows[start_idx:end_idx]):
                row = local_index // columns
                col = local_index % columns
                grid_layout.addWidget(make_card(row_data, start_idx + local_index), row, col)

            for col in range(columns):
                grid_layout.setColumnStretch(col, 1)

        def grid_columns() -> int:
            return max(1, int(grid_settings["columns"]))

        def grid_page_size() -> int:
            return max(1, grid_columns() * max(1, int(grid_settings["rows"])))

        def apply_grid_settings() -> None:
            current_global_index = max(0, int(state["index"]))
            grid_settings["columns"] = int(grid_cols_spin.value())
            grid_settings["rows"] = int(grid_rows_spin.value())
            grid_settings["card_height"] = int(card_size_spin.value())
            if state["rows"]:
                current_global_index = max(0, min(current_global_index, len(state["rows"]) - 1))
                state["current_page"] = current_global_index // grid_page_size()
            else:
                state["current_page"] = 0
            update_grid()

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
                    candidate_time = self._coerce_float(row_data.get("time_s"), np.inf)
                    if not np.isfinite(candidate_time):
                        continue
                    delta = abs(candidate_time - float(pending_time))
                    if delta < best_delta:
                        best_index = int(idx)
                        best_delta = float(delta)
                self._pending_gamma_review_selection = None
                if best_index is not None:
                    state["index"] = int(best_index)
                    state["current_page"] = int(best_index) // grid_page_size()
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
        cast(Any, zoom_plot.scene()).sigMouseClicked.connect(on_zoom_plot_clicked)
        cast(Any, filtered_zoom_plot.scene()).sigMouseClicked.connect(on_zoom_plot_clicked)
        level_combo.currentIndexChanged.connect(lambda _index: populate())
        channel_combo.currentIndexChanged.connect(lambda _index: populate())
        min_power.valueChanged.connect(lambda _value: populate())
        grid_cols_spin.valueChanged.connect(lambda _value: apply_grid_settings())
        grid_rows_spin.valueChanged.connect(lambda _value: apply_grid_settings())
        card_size_spin.valueChanged.connect(lambda _value: apply_grid_settings())

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
            official_classes = {
                id(event): self._gamma_event_official_class(event)
                for event in channel_result.events
            }
            gamma_spikes = sum(
                1 for event in channel_result.events
                if official_classes.get(id(event)) == "gamma"
            )
            if marker_mode == "gamma" and gamma_spikes == 0:
                continue
            if marker_mode == "non_gamma" and gamma_spikes > 0:
                continue

            events: list[dict[str, float | str]] = []
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            fs = float(metadata.get("fs", 0.0) or 0.0)
            data_start_s = float(metadata.get("data_start_s", 0.0) or 0.0)
            for event in channel_result.events:
                official_class = official_classes.get(id(event), "non-gamma")
                is_gamma = official_class == "gamma"
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
                start_time_s = time_s
                end_time_s = time_s
                if fs > 0.0:
                    try:
                        p1 = event.boundary_p1_sample
                        n2 = event.boundary_n2_sample
                        if p1 is not None and n2 is not None:
                            start_time_s = data_start_s + float(p1) / fs
                            end_time_s = data_start_s + float(n2) / fs
                    except (TypeError, ValueError):
                        start_time_s = time_s
                        end_time_s = time_s
                if end_time_s < start_time_s:
                    start_time_s, end_time_s = end_time_s, start_time_s
                events.append(
                    {
                        "time_s": time_s,
                        "start_time_s": start_time_s,
                        "end_time_s": end_time_s,
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
            time_s = self._coerce_float(row.get("time_s"), np.nan)
            if not np.isfinite(time_s):
                continue
            kind = "gamma" if self._gamma_row_official_class(row) == "gamma" else "regular"
            start_time_s = self._coerce_float(row.get("event_start_time_s"), time_s)
            end_time_s = self._coerce_float(row.get("event_stop_time_s"), time_s)
            markers.setdefault(channel, []).append(
                {
                    "time_s": time_s,
                    "start_time_s": min(start_time_s, end_time_s),
                    "end_time_s": max(start_time_s, end_time_s),
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
                model_class = "gamma" if is_gamma else "non-gamma"
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
                        "model_class": model_class,
                        "manual_class": self._normalize_gamma_manual_class(
                            getattr(event, "manual_class", None)
                        )
                        if getattr(event, "manual_class", None)
                        else None,
                        "manual_review_status": str(
                            getattr(event, "manual_review_status", "unreviewed")
                            or "unreviewed"
                        ),
                        "source_event": event,
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

    def _normalize_gamma_manual_class(self, label: object) -> str:
        text = str(label or "").strip().lower().replace("_", "-")
        if text in {"gamma", "gamma spike", "gamma-spike"}:
            return "gamma"
        if text in {"non-gamma", "nongamma", "non gamma", "regular", "regular spike"}:
            return "non-gamma"
        if text in {"unclassified", "candidate", "unknown", "not-classified"}:
            return "unclassified"
        return "unclassified" if not text else str(label)

    def _gamma_event_official_class(self, event: GammaSpikeEventResult) -> str:
        manual_class = getattr(event, "manual_class", None)
        if manual_class:
            return self._normalize_gamma_manual_class(manual_class)
        return "gamma" if self._gamma_event_is_gamma(event) else "non-gamma"

    def _gamma_row_official_class(self, row: GammaReviewRow) -> str:
        manual_class = row.get("manual_class")
        if manual_class:
            return self._normalize_gamma_manual_class(manual_class)
        model_class = str(row.get("model_class", "") or "").strip()
        if model_class:
            return self._normalize_gamma_manual_class(model_class)
        return "gamma" if bool(row.get("is_gamma", False)) else "non-gamma"

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
                if self._gamma_event_official_class(event) == "gamma"
            ]
            gamma_spikes = len(gamma_events)
            non_gamma_spikes = max(0, total_spikes - gamma_spikes)
            rate = (
                float(gamma_spikes) / float(total_spikes)
                if total_spikes > 0
                else 0.0
            )
            powers = np.asarray(
                [
                    float(event.gamma_power)
                    for event in gamma_events
                    if event.gamma_power is not None
                ],
                dtype=float,
            )
            durations = np.asarray(
                [
                    float(event.gamma_duration_ms)
                    for event in gamma_events
                    if event.gamma_duration_ms is not None
                ],
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
            "display_filter_used_for_computation": False,
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

    def _build_hfo_metadata(
        self,
        *,
        analysis_window_s: tuple[float, float],
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
            "algorithm": "HFO",
            "montage_used": self._current_montage_name(),
            "analysis_window_s": list(map(float, analysis_window_s)),
            "manual_analysis_window": True,
            "notch_filter": bool(active_notch_modes),
            "notch_modes": active_notch_modes,
            "notch_modes_by_channel": {
                str(channel): str(mode)
                for channel, mode in (notch_modes_by_channel or {}).items()
                if str(mode) != "Off"
            },
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

        if hasattr(self, "btn_export_ei"):
            self.btn_export_ei.setEnabled(False)

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
        self.btn_export_ei.setEnabled(True)

    def _export_ei_results(self) -> None:
        result = self._last_ei_result
        if result is None:
            QMessageBox.information(
                self,
                "Export REI results",
                "Run REI before exporting results.",
            )
            return
        output_dir = self._choose_export_dir("Select folder for REI export")
        if output_dir is None:
            return
        if not self._confirm_export_overwrite(
            output_dir,
            [
                "rei_summary.csv",
                "rei_heatmap.csv",
                "rei_heatmap.png",
                "rei_metadata.json",
                "README.txt",
            ],
            title="Export REI results",
        ):
            return
        try:
            written_paths = export_ei_result(output_dir, result)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export REI results",
                f"Could not export REI results:\n{exc}",
            )
            return
        self._last_export_dir = output_dir
        QMessageBox.information(
            self,
            "Export REI results",
            f"Exported {len(written_paths)} files to:\n{output_dir}",
        )

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
        heatmap_plot_left_axis = cast(Any, heatmap_plot.getAxis("left"))
        heatmap_plot_left_axis.setWidth(140)
        cast(Any, heatmap_plot.getViewBox()).invertY(True)
        score_plot.setYLink(heatmap_plot)
        cast(Any, score_plot.getViewBox()).invertY(True)

        heatmap_image = pg.ImageItem(axisOrder="row-major")
        heatmap_plot.addItem(heatmap_image)
        color_map = cast(Any, pg.colormap.get("viridis"))
        color_bar: Any | None = None
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
        heatmap_view_box = cast(Any, heatmap_plot.getViewBox())
        score_view_box = cast(Any, score_plot.getViewBox())
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
            heatmap_plot_left_axis.setTicks(
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
