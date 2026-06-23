# app/computation_panel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from mne.io import BaseRaw

from PySide6.QtCore import Qt, Slot, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog,
    QDialogButtonBox, QLineEdit, QSizePolicy, QButtonGroup,
    QFormLayout, QFrame, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget,
)

from app.time_controls import TimeWindowControl
from app.EI_algorithm import EIComputationResult, compute_ei_for_gui, validate_gui_ei_timing


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


class ComputationPanel(QWidget):
    """
    Dock content widget:
      - editable channel selection (absolute indices from displayed channel list)
      - time controls (linked/unlinked to main window time)
      - plot of the mean signal across selected channels
    """

    panelSelectionChanged = Signal(list)  # absolute channel indices
    settingsChanged = Signal()

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

        # --- Plot ---
        gb_p = QGroupBox("Output")
        p_layout = QVBoxLayout(gb_p)

        self.btn_run = QPushButton("Run computation")
        p_layout.addWidget(self.btn_run)

        self.output_tabs = QTabWidget()
        p_layout.addWidget(self.output_tabs, 1)

        self.mean_output_tab = QWidget()
        mean_output_layout = QVBoxLayout(self.mean_output_tab)
        mean_output_layout.setContentsMargins(0, 0, 0, 0)
        mean_output_layout.setSpacing(8)

        self.chk_match_main = QCheckBox("Match main display scaling")
        self.chk_match_main.setChecked(True)
        mean_output_layout.addWidget(self.chk_match_main)

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
        mean_output_layout.addWidget(self.plot, 1)
        self._mean_tab_index = self.output_tabs.addTab(self.mean_output_tab, "Mean plot")

        self.ei_summary_tab = QWidget()
        ei_summary_layout = QVBoxLayout(self.ei_summary_tab)
        ei_summary_layout.setContentsMargins(0, 0, 0, 0)
        ei_summary_layout.setSpacing(8)

        self.ei_summary_table = QTableWidget(0, 5)
        self.ei_summary_table.setHorizontalHeaderLabels(
            ["Channel", "Group", "EI score", "Rank", "EI onset vs seizure onset"]
        )
        self.ei_summary_table.verticalHeader().setVisible(False)
        self.ei_summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ei_summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ei_summary_table.setAlternatingRowColors(True)
        self.ei_summary_table.setSortingEnabled(True)
        self.ei_summary_table.setMinimumHeight(220)
        header = self.ei_summary_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        ei_summary_layout.addWidget(self.ei_summary_table, 1)
        self._ei_summary_tab_index = self.output_tabs.addTab(self.ei_summary_tab, "EI summary")

        self.ei_heatmap_tab = QWidget()
        ei_heatmap_layout = QVBoxLayout(self.ei_heatmap_tab)
        ei_heatmap_layout.setContentsMargins(0, 0, 0, 0)
        ei_heatmap_layout.setSpacing(8)

        self.heatmap_plot = pg.PlotWidget()
        self.heatmap_plot.setMinimumSize(260, 220)
        self.heatmap_plot.showGrid(x=True, y=True, alpha=0.15)
        self.heatmap_plot.setLabel("bottom", "Time (s)")
        self.heatmap_plot.setLabel("left", "Channel")
        self.heatmap_image = pg.ImageItem()
        self.heatmap_plot.addItem(self.heatmap_image)
        ei_heatmap_layout.addWidget(self.heatmap_plot, 1)
        self._ei_heatmap_tab_index = self.output_tabs.addTab(self.ei_heatmap_tab, "EI heatmap")
        self.output_tabs.setTabVisible(self._ei_summary_tab_index, False)
        self.output_tabs.setTabVisible(self._ei_heatmap_tab_index, False)

        root.addWidget(gb_p, 3)

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
            if 0 <= idx < len(self._ch_names_displayed)
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
        cleaned = sorted({int(i) for i in selected_abs if int(i) >= 0})

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
        if not self._ch_names_displayed:
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

        for abs_idx, name in enumerate(self._ch_names_displayed):
            item = QListWidgetItem(name)
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
        self.output_tabs.setTabVisible(self._mean_tab_index, not is_ei)
        self.output_tabs.setTabVisible(self._ei_summary_tab_index, is_ei)
        self.output_tabs.setTabVisible(self._ei_heatmap_tab_index, is_ei)
        self.output_tabs.setCurrentIndex(
            self._ei_summary_tab_index if is_ei else self._mean_tab_index
        )
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
        self._apply_default_ei_windows_from_onset()
        self.settingsChanged.emit()

    def _on_ei_offset_text_changed(self, _text: str) -> None:
        self.state.seizure_offset_s = self._parse_float_text(self.edit_seizure_offset)
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
        self.ei_summary_table.setRowCount(0)
        self.heatmap_image.setVisible(False)

    def _show_ei_result(self, result: EIComputationResult) -> None:
        self.ei_summary_table.setSortingEnabled(False)
        self.ei_summary_table.setRowCount(0)
        metadata = result.metadata or {}
        seizure_onset = metadata.get("seizure_onset_s")
        ictal_window = metadata.get("ictal_window_s")
        ictal_start = float(ictal_window[0]) if isinstance(ictal_window, list) and ictal_window else 0.0

        for channel_result in result.channels:
            row = self.ei_summary_table.rowCount()
            self.ei_summary_table.insertRow(row)
            if isinstance(seizure_onset, (int, float)):
                onset_delta = (
                    ictal_start
                    + float(channel_result.onset_sec_in_ictal_window)
                    - float(seizure_onset)
                )
                onset_text = f"{onset_delta:+.3f} s"
            else:
                onset_text = f"{channel_result.onset_sec_in_ictal_window:.3f} s"
            values = [
                channel_result.channel,
                channel_result.group.capitalize(),
                f"{channel_result.ei:.4f}",
                str(channel_result.rank),
                onset_text,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col in {2, 3, 4}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.ei_summary_table.setItem(row, col, item)

        self.ei_summary_table.setSortingEnabled(True)
        self.output_tabs.setCurrentIndex(self._ei_summary_tab_index)

        if result.heatmap.size:
            heatmap = np.asarray(result.heatmap, dtype=float)
            self.heatmap_image.setImage(heatmap, autoLevels=True)
            self.heatmap_image.setVisible(True)
            self.heatmap_plot.setXRange(
                float(result.heatmap_times[0]) if result.heatmap_times.size else 0.0,
                float(result.heatmap_times[-1]) if result.heatmap_times.size else 1.0,
            )
            self.heatmap_plot.setYRange(-0.5, max(0.5, heatmap.shape[0] - 0.5))
        else:
            self.heatmap_image.setVisible(False)

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
        all_abs = list(range(len(self._ch_names_displayed)))
        self.set_selected_channels_abs(all_abs, replace=True)

    def _select_group_channels(self, group: str) -> None:
        group = str(group).strip().lower()
        if group not in {"macro", "micro"}:
            return

        chosen = []
        for abs_idx, ch_name in enumerate(self._ch_names_displayed):
            ch_group = str(self._channel_groups.get(ch_name, "macro")).strip().lower()
            if ch_group == group:
                chosen.append(abs_idx)

        self.set_selected_channels_abs(chosen, replace=True)

    def _update_group_button_titles(self) -> None:
        n_all = len(self._ch_names_displayed)
        n_macro = 0
        n_micro = 0

        for ch_name in self._ch_names_displayed:
            group = str(self._channel_groups.get(ch_name, "macro")).strip().lower()
            if group == "micro":
                n_micro += 1
            else:
                n_macro += 1

        self.btn_sel_all.setText(f"All ({n_all})")
        self.btn_sel_macro.setText(f"Macro ({n_macro})")
        self.btn_sel_micro.setText(f"Micro ({n_micro})")
