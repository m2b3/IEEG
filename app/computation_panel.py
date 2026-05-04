# app/computation_panel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyqtgraph as pg
from mne.io import BaseRaw

from PySide6.QtCore import Qt, Slot, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog,
    QDialogButtonBox, QLineEdit, QComboBox, QSizePolicy,
)

from app.time_controls import TimeWindowControl


@dataclass
class PanelState:
    selected_abs: list[int]
    t0: float
    win: float
    link_time: bool = True
    algorithm: str = "mean"


class ComputationPanel(QWidget):
    """
    Dock content widget:
      - editable channel selection (absolute indices from displayed channel list)
      - time controls (linked/unlinked to main window time)
      - plot of the mean signal across selected channels
    """

    panelSelectionChanged = Signal(list)  # absolute channel indices

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

        self.state = PanelState(selected_abs=[], t0=0.0, win=5.0, link_time=True)

        # Keep in sync with the main viewer display scaling.
        self._main_gain_uv: float = 100.0
        self._correction_factor: float = 0.01

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- Channel selector ---
        self.gb_ch = QGroupBox("Channels")
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
        gb_t = QGroupBox("Time")
        t_layout = QVBoxLayout(gb_t)

        self.chk_link_time = QCheckBox("Link to main time window")
        self.chk_link_time.setChecked(True)
        t_layout.addWidget(self.chk_link_time)

        info_row = QHBoxLayout()
        self.lbl_t = QLabel("t: [0.00, 5.00] s")
        info_row.addWidget(self.lbl_t, 1)
        t_layout.addLayout(info_row)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Window length (s):"))
        self.spin_win = QDoubleSpinBox()
        self.spin_win.setRange(1.0, 10.0)
        self.spin_win.setSingleStep(0.5)
        self.spin_win.setValue(5.0)
        spin_row.addWidget(self.spin_win)
        t_layout.addLayout(spin_row)

        self.time_ctl = TimeWindowControl(label_prefix="t0")
        self.time_ctl.set_enabled(False)
        t_layout.addWidget(self.time_ctl)

        root.addWidget(gb_t, 0)

        # --- Plot ---
        gb_p = QGroupBox("Output")
        p_layout = QVBoxLayout(gb_p)

        self.chk_match_main = QCheckBox("Match main display scaling")
        self.chk_match_main.setChecked(True)
        p_layout.addWidget(self.chk_match_main)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))

        self.cbo_algo = QComboBox()
        self.cbo_algo.addItem("Mean (across selected channels)", userData="mean")
        algo_row.addWidget(self.cbo_algo, 1)
        p_layout.addLayout(algo_row)

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

        root.addWidget(gb_p, 3)

        # --- Wiring ---
        self.btn_add.clicked.connect(self._open_add_channels_dialog)
        self.btn_remove.clicked.connect(self._remove_selected_items)
        self.btn_clear.clicked.connect(self._clear_channels)

        self.chk_link_time.toggled.connect(self._on_link_time_toggled)
        self.spin_win.valueChanged.connect(self._on_win_changed)
        self.time_ctl.t0Changed.connect(self._on_panel_t0_changed)

        self.chk_match_main.toggled.connect(lambda _: self._request_update_plot())
        self.cbo_algo.currentIndexChanged.connect(self._on_algo_changed)

        self.btn_sel_all.clicked.connect(self._select_all_channels)
        self.btn_sel_macro.clicked.connect(lambda: self._select_group_channels("macro"))
        self.btn_sel_micro.clicked.connect(lambda: self._select_group_channels("micro"))

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
    ) -> None:
        self._raw = raw
        self._picks = picks
        self._ch_names_displayed = list(displayed_names or [])

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
        self._request_update_plot()
        
    def set_selected_channels_abs(self, selected_abs: list[int], *, replace: bool = True) -> None:
        cleaned = sorted({int(i) for i in selected_abs if int(i) >= 0})

        if replace:
            self.state.selected_abs = cleaned
        else:
            self.state.selected_abs = sorted(set(self.state.selected_abs).union(cleaned))

        self._sync_list_widget_from_state()
        self._update_channels_title()
        self._request_update_plot()
        self.panelSelectionChanged.emit(self.state.selected_abs)

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

    @Slot(float)
    def _on_win_changed(self, value: float) -> None:
        self.state.win = float(np.clip(value, 1.0, 10.0))
        self._update_time_label()

        if self._raw is not None and self._raw.n_times > 1:
            total_s = float(self._raw.times[-1])
            self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

        self._request_update_plot()

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
        data_v, times = self._raw[raw_idx, start:end]

        data_v = np.asarray(data_v)
        times = np.asarray(times)

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
                view_box.setRange(xRange=(t0, t1), padding=0.0)

    def _request_update_plot(self, delay_ms: int = 40) -> None:
        """Throttle plot updates by restarting a single-shot timer."""
        if self._update_timer.isActive():
            self._update_timer.stop()
        self._update_timer.start(int(delay_ms))

    # ---------- Small UI helpers ----------

    def _update_channels_title(self) -> None:
        self.gb_ch.setTitle(f"Channels ({len(self.state.selected_abs)})")

    def _on_algo_changed(self, _idx: int) -> None:
        self.state.algorithm = str(self.cbo_algo.currentData() or "mean")
        self._request_update_plot()

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
