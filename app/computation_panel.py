# app/computation_panel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, cast

import numpy as np
import pyqtgraph as pg
from mne.io import BaseRaw

from PySide6.QtCore import Qt, Slot, Signal, QTimer, QRectF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog,
    QDialogButtonBox, QLineEdit, QSizePolicy, QButtonGroup,
    QFormLayout, QFrame, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QComboBox, QSpinBox, QSplitter,
)

from app.time_controls import TimeWindowControl
from app.EI_algorithm import (
    EIChannelResult,
    EIComputationResult,
    compute_ei_for_gui,
    validate_gui_ei_timing,
)


@dataclass
class PanelState:
    selected_abs: list[int]
    t0: float
    win: float
    link_time: bool = True
    algorithm: str = "mean"
    seizure_onset_s: float | None = None
    seizure_offset_s: float | None = None
    baseline_start_s: float = 0.0
    baseline_end_s: float = 0.0
    ictal_start_s: float = 0.0
    ictal_end_s: float = 0.0


@dataclass
class EIHeatmapRow:
    original_idx: int
    channel_name: str
    ei_score: float
    recruitment_delay: float
    peak_hfer: float
    mean_hfer: float


class ComputationPanel(QWidget):
    """
    Dock content widget:
      - editable channel selection (absolute indices from displayed channel list)
      - time controls (linked/unlinked to main window time)
      - plot of the mean signal across selected channels
    """

    panelSelectionChanged = Signal(list)  # absolute channel indices
    settingsChanged = Signal()
    seizureMarkersChanged = Signal(object, object)  # onset_s, offset_s
    seizureMarkerEdited = Signal(str, object)  # "onset" | "offset", value_s
    recruitmentMarkersChanged = Signal(dict)  # display channel name -> absolute time_s
    eiSummaryChannelActivated = Signal(str)
    eiSummaryOrderChanged = Signal(list)

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
        self._ei_data_callback: Callable[[list[int], float, float], tuple[np.ndarray, float, list[str]]] | None = None
        self.ei_result_metadata: dict | None = None
        self._last_ei_result: EIComputationResult | None = None
        self._ei_summary_dialog: QDialog | None = None
        self._ei_heatmap_dialog: QDialog | None = None
        self._ei_summary_table: QTableWidget | None = None
        self._ei_summary_row_by_channel: dict[str, int] = {}

        self.state = PanelState(selected_abs=[], t0=0.0, win=5.0, link_time=True)

        # Keep in sync with the main viewer display scaling.
        self._main_gain_uv: float = 100.0
        self._correction_factor: float = 0.01
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

        self.btn_algo_mean = QPushButton("Mean")
        self.btn_algo_mean.setCheckable(True)
        self.btn_algo_mean.setChecked(True)
        self.btn_algo_mean.setProperty("algorithm", "mean")

        self.btn_algo_ei = QPushButton("EI")
        self.btn_algo_ei.setCheckable(True)
        self.btn_algo_ei.setProperty("algorithm", "ei")

        self.algo_buttons.addButton(self.btn_algo_mean)
        self.algo_buttons.addButton(self.btn_algo_ei)
        algo_row.addWidget(self.btn_algo_mean)
        algo_row.addWidget(self.btn_algo_ei)
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

        self.mean_time_widget = QWidget()
        mean_time_layout = QVBoxLayout(self.mean_time_widget)
        mean_time_layout.setContentsMargins(0, 0, 0, 0)
        mean_time_layout.setSpacing(8)

        self.chk_link_time = QCheckBox("Link to main time window")
        self.chk_link_time.setChecked(True)
        mean_time_layout.addWidget(self.chk_link_time)

        info_row = QHBoxLayout()
        self.lbl_t = QLabel("t: [0.00, 5.00] s")
        info_row.addWidget(self.lbl_t, 1)
        mean_time_layout.addLayout(info_row)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Window length (s):"))
        self.spin_win = QDoubleSpinBox()
        self.spin_win.setRange(1.0, 10.0)
        self.spin_win.setSingleStep(0.5)
        self.spin_win.setValue(5.0)
        spin_row.addWidget(self.spin_win)
        mean_time_layout.addLayout(spin_row)

        self.time_ctl = TimeWindowControl(label_prefix="t0")
        self.time_ctl.set_enabled(False)
        mean_time_layout.addWidget(self.time_ctl)

        t_layout.addWidget(self.mean_time_widget)

        self.ei_time_widget = QWidget()
        ei_time_layout = QVBoxLayout(self.ei_time_widget)
        ei_time_layout.setContentsMargins(0, 0, 0, 0)
        ei_time_layout.setSpacing(8)

        info_box = QGroupBox("EI setup")
        info_layout = QHBoxLayout(info_box)
        info_layout.addWidget(QLabel("Recommended montage: Bipolar"), 1)
        self.btn_ei_info = QPushButton("i")
        self.btn_ei_info.setFixedSize(22, 22)
        self.btn_ei_info.setToolTip(
            "EI preprocessing: confirmed bad channels are excluded and an internal "
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

        preprocessing_box = QGroupBox("EI preprocessing")
        preprocessing_form = QFormLayout(preprocessing_box)
        preprocessing_form.addRow("Analysis filter:", QLabel("Butterworth bandpass"))
        preprocessing_form.addRow("Filter order:", QLabel("4"))
        preprocessing_form.addRow("Bandpass:", QLabel("70-140 Hz"))
        preprocessing_form.addRow("Zero phase:", QLabel("Yes"))
        preprocessing_form.addRow("Notch filter:", QLabel("No"))
        preprocessing_form.addRow("Line frequency:", QLabel("60 Hz"))
        advanced_layout.addWidget(preprocessing_box)

        params_box = QGroupBox("EI computation")
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

        self.btn_run = QPushButton("Run computation")
        p_layout.addWidget(self.btn_run)

        self.btn_open_ei_summary = QPushButton("Open EI summary")
        self.btn_open_ei_summary.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_summary)

        self.btn_open_ei_heatmap = QPushButton("Open EI heatmap")
        self.btn_open_ei_heatmap.setEnabled(False)
        p_layout.addWidget(self.btn_open_ei_heatmap)

        self.chk_match_main = QCheckBox("Match main display scaling")
        self.chk_match_main.setChecked(True)
        p_layout.addWidget(self.chk_match_main)

        self.plot = pg.PlotWidget()
        self.plot.setMinimumSize(220, 140)
        self.plot.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "Time (s)")
        self.plot.setLabel("left", "Mean voltage (uV)")
        self.curve = self.plot.plot([], [])
        p_layout.addWidget(self.plot, 1)

        root.addWidget(gb_p, 2)


        # --- Wiring ---
        self.btn_add.clicked.connect(self._open_add_channels_dialog)
        self.btn_remove.clicked.connect(self._remove_selected_items)
        self.btn_clear.clicked.connect(self._clear_channels)

        self.chk_link_time.toggled.connect(self._on_link_time_toggled)
        self.spin_win.valueChanged.connect(self._on_win_changed)
        self.time_ctl.t0Changed.connect(self._on_panel_t0_changed)

        self.chk_match_main.toggled.connect(lambda _: self._request_update_plot())
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

        self.btn_sel_all.clicked.connect(self._select_all_channels)
        self.btn_sel_macro.clicked.connect(lambda: self._select_group_channels("macro"))
        self.btn_sel_micro.clicked.connect(lambda: self._select_group_channels("micro"))
        self.gb_ch.toggled.connect(self._sync_section_visibility)
        self.gb_t.toggled.connect(self._sync_section_visibility)

        # --- Throttled updates ---
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._update_plot)

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
        else:
            self._request_update_plot()
        
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
        else:
            self._request_update_plot()
        self.panelSelectionChanged.emit(self.state.selected_abs)
        self.settingsChanged.emit()

    def set_main_time(self, t0: float, main_win_s: float) -> None:
        del main_win_s  # kept for API compatibility

        if not self.state.link_time:
            return

        self.state.t0 = float(t0)
        self.state.win = float(np.clip(self.state.win, 1.0, 10.0))

        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)

        self._update_slider_range()
        self.time_ctl.set_t0(self.state.t0)
        self._request_update_plot()

    def set_main_gain_uv(self, gain_uv: float) -> None:
        self._main_gain_uv = float(gain_uv)
        self._request_update_plot()

    def set_ei_montage_callbacks(
        self,
        *,
        current_montage: Callable[[], str],
        switch_to_bipolar: Callable[[], tuple[bool, str]],
    ) -> None:
        self._current_montage_callback = current_montage
        self._switch_to_bipolar_callback = switch_to_bipolar

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
            "mean": {
                "t0": float(self.state.t0),
                "window_s": float(self.state.win),
                "link_time": bool(self.state.link_time),
                "match_main_scaling": bool(self.chk_match_main.isChecked()),
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
        }

    def restore_project_state(self, data: dict | None) -> None:
        if not isinstance(data, dict):
            return

        mean = data.get("mean", {})
        if not isinstance(mean, dict):
            mean = {}
        ei = data.get("ei", {})
        if not isinstance(ei, dict):
            ei = {}
        selected_abs = data.get("selected_abs", [])
        if isinstance(selected_abs, list):
            cleaned_abs = []
            for value in selected_abs:
                try:
                    cleaned_abs.append(int(value))
                except (TypeError, ValueError):
                    continue
            self.set_selected_channels_abs(cleaned_abs, replace=True)

        self.state.t0 = float(mean.get("t0", self.state.t0) or 0.0)
        self.state.win = float(np.clip(float(mean.get("window_s", self.state.win) or self.state.win), 1.0, 10.0))
        self.state.link_time = bool(mean.get("link_time", self.state.link_time))

        self.chk_link_time.blockSignals(True)
        self.chk_link_time.setChecked(self.state.link_time)
        self.chk_link_time.blockSignals(False)
        self.time_ctl.set_enabled(not self.state.link_time)

        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)
        self.chk_match_main.setChecked(bool(mean.get("match_main_scaling", self.chk_match_main.isChecked())))

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
        self._sync_ei_windows_from_ui(emit=False)
        saved_metadata = ei.get("last_result_metadata")
        self.ei_result_metadata = saved_metadata if isinstance(saved_metadata, dict) else None

        algorithm = str(data.get("algorithm", self.state.algorithm) or "mean")
        button = self.btn_algo_ei if algorithm == "ei" else self.btn_algo_mean
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
        self.state.win = float(np.clip(value, 1.0, 10.0))
        self._update_time_label()

        if self._raw is not None and self._raw.n_times > 1:
            total_s = float(self._raw.times[-1])
            self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

        self._request_update_plot()
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
        self._request_update_plot()
        self.settingsChanged.emit()

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

        algorithm = str(button.property("algorithm") or "mean")
        self.state.algorithm = algorithm
        is_ei = algorithm == "ei"

        self.mean_time_widget.setVisible(self.gb_t.isChecked() and not is_ei)
        self.ei_time_widget.setVisible(self.gb_t.isChecked() and is_ei)
        self.plot.setVisible(not is_ei)
        self.chk_match_main.setVisible(not is_ei)

        self.btn_open_ei_summary.setVisible(is_ei)
        self.btn_open_ei_heatmap.setVisible(is_ei)

        self.btn_run.setText("Run EI" if is_ei else "Run computation")

        if is_ei:
            self.curve.setData([], [])
            self._clear_ei_outputs()
        else:
            self._request_update_plot()
        self.settingsChanged.emit()

    def _open_advanced_dialog(self) -> None:
        if self.advanced_dialog is None:
            self.advanced_dialog = QDialog(self)
            self.advanced_dialog.setWindowTitle("EI advanced parameters")
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
        self.mean_time_widget.setVisible(time_visible and not is_ei)
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
            return False, "Load a dataset before running EI."
        if not self.state.selected_abs:
            return False, "Select at least one channel before running EI."

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
                QMessageBox.warning(self, "EI computation", message)
                return
            if not self._confirm_ei_montage_before_run():
                return
            try:
                result = self._compute_ei_result()
            except Exception as exc:
                QMessageBox.warning(self, "EI computation", str(exc))
                return
            self._show_ei_result(result)
            self.ei_result_metadata = result.metadata
            return

        self._request_update_plot(delay_ms=0)

    def _compute_ei_result(self) -> EIComputationResult:
        if self._ei_data_callback is None:
            raise RuntimeError("EI data extraction is not available.")

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
            metadata=self._build_ei_metadata(
                self._current_montage_name(),
                seizure_onset_s=seizure_onset,
                seizure_offset_s=seizure_offset,
                baseline_window_s=(baseline_start, baseline_end),
                ictal_window_s=(ictal_start, ictal_end),
            ),
        )

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
            "The Epileptogenicity Index (EI) was originally designed and validated primarily "
            "on bipolar iEEG recordings. Using another montage may affect EI scores and "
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
            "Switched to bipolar montage. Review the selected channels and run EI again.",
        )
        return False

    def _show_nonblocking_ei_error(self, message: str) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("EI computation")
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
    ) -> dict:
        return {
            "algorithm": "Epileptogenicity Index",
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
            "notch_filter": False,
            "threshold_sigma": float(self.ei_params["threshold_sigma"]),
            "energy_window_sec": float(self.ei_params["energy_window_sec"]),
            "hfer_window_sec": float(self.ei_params["hfer_window_sec"]),
        }

    def _clear_ei_outputs(self) -> None:
        self._last_ei_result = None
        self.ei_result_metadata = None
        self.recruitmentMarkersChanged.emit({})
        self._ei_summary_table = None
        self._ei_summary_row_by_channel = {}

        if hasattr(self, "btn_open_ei_summary"):
            self.btn_open_ei_summary.setEnabled(False)

        if hasattr(self, "btn_open_ei_heatmap"):
            self.btn_open_ei_heatmap.setEnabled(False)

    def _show_ei_result(self, result: EIComputationResult) -> None:
        self._last_ei_result = result
        self.ei_result_metadata = result.metadata
        self.recruitmentMarkersChanged.emit(
            self._recruitment_markers_from_result(result)
        )

        self.btn_open_ei_summary.setEnabled(True)
        self.btn_open_ei_heatmap.setEnabled(bool(result.heatmap.size))

        self._open_ei_summary_dialog()

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
            QMessageBox.information(self, "EI summary", "Run EI first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("EI summary")
        dialog.resize(720, 420)

        layout = QVBoxLayout(dialog)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            [
                "Channel",
                "EI score",
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

        summary_rows: list[dict[str, float | int | str]] = []
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

        def populate_summary_table(rows: list[dict[str, float | int | str]]) -> None:
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

        sort_state = {
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

            key_map = {
                1: "ei_score",
                2: "rank",
                3: "hfer_activity",
                4: "recruitment_delay",
            }
            key_name = key_map.get(column, "original_order")
            reverse = sort_state["order"] == Qt.SortOrder.DescendingOrder
            sorted_rows = sorted(
                summary_rows,
                key=lambda row: row[key_name],
                reverse=reverse,
            )
            header.setSortIndicator(column, sort_state["order"])
            populate_summary_table(sorted_rows)

        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(sort_summary_table)
        table.cellClicked.connect(activate_summary_row)
        populate_summary_table(summary_rows)
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
        table.setCurrentCell(int(row), 0)
        table.selectRow(int(row))
        table.scrollToItem(table.item(int(row), 0))
        return True

    def _open_ei_heatmap_dialog(self) -> None:
        result = self._last_ei_result
        if result is None:
            QMessageBox.information(self, "EI heatmap", "Run EI first.")
            return

        if not result.heatmap.size:
            QMessageBox.information(self, "EI heatmap", "No heatmap data available.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("EI heatmap")
        dialog.resize(980, 620)

        layout = QVBoxLayout(dialog)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Sort channels by:"))
        sort_combo = QComboBox()
        sort_combo.addItem("EI score", userData="ei_score")
        sort_combo.addItem("Recruitment delay", userData="recruitment_delay")
        sort_combo.addItem("Peak HFER activity", userData="peak_hfer")
        sort_combo.addItem("Mean HFER activity", userData="mean_hfer")
        sort_combo.addItem("Original channel order", userData="original")
        sort_combo.addItem("Channel name", userData="channel_name")
        controls_row.addWidget(sort_combo)

        controls_row.addSpacing(16)
        controls_row.addWidget(QLabel("Show top N channels:"))
        top_n_spin = QSpinBox()
        max_channels = max(1, min(len(result.heatmap_channels), int(result.heatmap.shape[0])))
        top_n_spin.setRange(1, max_channels)
        top_n_spin.setValue(min(30, max_channels))
        controls_row.addWidget(top_n_spin)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        score_plot = pg.PlotWidget()
        score_plot.setMinimumWidth(120)
        score_plot.showGrid(x=True, y=False, alpha=0.15)
        score_plot.setLabel("bottom", "EI score")
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
                        "EI heatmap",
                        "Recruitment delay metadata is incomplete. Falling back to "
                        "EI onset from seizure onset for sorting.",
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

    # ---------- Internals : plotting ----------

    def _get_time_slice(self) -> tuple[int, int] | None:
        if self._raw is None:
            return None

        total_s = float(self._raw.times[-1]) if self._raw.n_times > 1 else 0.0
        max_t0 = max(0.0, total_s - float(self.state.win))
        self.state.t0 = float(np.clip(self.state.t0, 0.0, max_t0))

        fs = float(self._raw.info["sfreq"])
        start = int(self.state.t0 * fs)
        end = int((self.state.t0 + self.state.win) * fs)

        n_times = int(self._raw.n_times)
        start = max(0, min(start, max(0, n_times - 1)))
        end = max(start + 1, min(end, n_times))
        return start, end

    def _compute_mean_signal(self, start: int, end: int) -> tuple[np.ndarray, np.ndarray] | None:
        if self._raw is None or self._picks is None or not self.state.selected_abs:
            return None

        raw_idx = self._picks[np.asarray(self.state.selected_abs, dtype=int)]
        data_v = self._raw.get_data(picks=raw_idx, start=int(start), stop=int(end))
        times = np.asarray(self._raw.times[int(start):int(end)], dtype=float)

        data_v = np.asarray(data_v)

        if data_v.size == 0 or times.size < 2:
            return None

        mean_v = np.mean(data_v, axis=0)
        return mean_v, times

    def _scale_for_display(self, y_v: np.ndarray) -> tuple[np.ndarray, str]:
        if self.chk_match_main.isChecked():
            y_uv = y_v * 1e6
            gain_factor = 1.0 / max(1e-9, self._main_gain_uv * self._correction_factor)
            return y_uv * gain_factor, "Amplitude (uV, main-scaled)"

        return y_v, "Mean voltage (V)"

    def _update_plot(self) -> None:
        if self.state.algorithm == "ei":
            return

        self._update_time_label()

        if self._raw is None or self._picks is None or not self.state.selected_abs:
            self.curve.setData([], [])
            return

        time_slice = self._get_time_slice()
        if time_slice is None:
            self.curve.setData([], [])
            return

        start, end = time_slice
        result = self._compute_mean_signal(start, end)
        if result is None:
            self.curve.setData([], [])
            return

        y_v, times = result
        y, y_label = self._scale_for_display(y_v)
        self.plot.setLabel("left", y_label)

        max_pts = 2000
        step = max(1, y.size // max_pts)
        self.curve.setData(times[::step], y[::step])

        t0 = float(self.state.t0)
        t1 = t0 + float(self.state.win)

        plot_item = self.plot.getPlotItem()
        if plot_item is not None:
            view_box = plot_item.getViewBox()
            if view_box is not None:
                view_box.setRange(xRange=(t0, t1))

    def _request_update_plot(self, delay_ms: int = 40) -> None:
        """Throttle plot updates by restarting a single-shot timer."""
        if self._update_timer.isActive():
            self._update_timer.stop()
        self._update_timer.start(int(delay_ms))

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
