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
    QCheckBox, QDoubleSpinBox, QPushButton, QGroupBox, QDialog, QDialogButtonBox, QLineEdit, QComboBox  
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
      - editable channel selection (abs indices, displayed channel list)
      - time controls (linked/unlinked to main window time)
      - plot: mean voltage vs time (V)
    """

    panelSelectionChanged = Signal(list)  # abs indices

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._raw: BaseRaw | None = None
        self._picks: np.ndarray | None = None  # abs_idx -> raw_idx
        self._ch_names_displayed: list[str] = []  # abs_idx -> display name (matches viewer list)

        self.state = PanelState(selected_abs=[], t0=0.0, win=5.0, link_time=True)

        self._main_gain_uv: float = 100.0   # keep in sync with MainWindow gain spinbox
        self._correction_factor: float = 0.01  # must match viewer _draw_traces

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- Channel selector ---
        self.gb_ch = QGroupBox("Channels")
        ch_layout = QVBoxLayout(self.gb_ch)

        self.list_channels = QListWidget()
        self.list_channels.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        ch_layout.addWidget(self.list_channels, 1)

        btn_row = QHBoxLayout()
        self.btn_remove = QPushButton("Remove selected")
        self.btn_clear = QPushButton("Clear")
        self.btn_add = QPushButton("Add…")
        btn_row.insertWidget(0, self.btn_add)  # put it first (optional)
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
        t_layout.addWidget(self.time_ctl)
        root.addWidget(gb_t, 0)

        self.time_ctl.set_enabled(False)

        # --- Plot ---
        gb_p = QGroupBox("Output")
        p_layout = QVBoxLayout(gb_p)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "Time (s)")
        self.plot.setLabel("left", "Mean voltage (µV)")
        self.curve = self.plot.plot([], [])
        p_layout.addWidget(self.plot, 1)
        root.addWidget(gb_p, 3)

        self.chk_match_main = QCheckBox("Match main display scaling")
        self.chk_match_main.setChecked(True)
        self.chk_match_main.toggled.connect(lambda _: self._request_update_plot())
        p_layout.insertWidget(0, self.chk_match_main)  # add above the plot

        # --- Algorithm selector ---
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))

        self.cbo_algo = QComboBox()
        self.cbo_algo.addItem("Mean (across selected channels)", userData="mean")

        algo_row.addWidget(self.cbo_algo, 1)
        p_layout.addLayout(algo_row)

        # --- Wiring ---
        self.btn_remove.clicked.connect(self._remove_selected_items)
        self.btn_clear.clicked.connect(self._clear_channels)
        self.chk_link_time.toggled.connect(self._on_link_time_toggled)
        self.spin_win.valueChanged.connect(self._on_win_changed)
        self.btn_add.clicked.connect(self._open_add_channels_dialog)
        self.time_ctl.t0Changed.connect(self._on_panel_t0_changed)
        self.cbo_algo.currentIndexChanged.connect(self._on_algo_changed)

        # --- Throttled updates (smooth while dragging) ---
        self._update_timer = QTimer(self)  # or QTimer(self) if you imported it
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._update_plot)

    # ---------- Public API used by MainWindow ----------
    def set_data_context(self, raw: BaseRaw | None, picks: np.ndarray | None, displayed_names: list[str]):
        self._raw = raw
        self._picks = picks
        self._ch_names_displayed = displayed_names or []
        self._refresh_channel_list_titles()

    def set_selected_channels_abs(self, selected_abs: list[int], *, replace: bool = True):
        # Keep only valid indices
        selected_abs = [int(i) for i in selected_abs if int(i) >= 0]
        if replace:
            self.state.selected_abs = sorted(set(selected_abs))
        else:
            self.state.selected_abs = sorted(set(self.state.selected_abs).union(selected_abs))

        self._sync_list_widget_from_state()
        self._request_update_plot()
        self.panelSelectionChanged.emit(self.state.selected_abs)
        self._update_channels_title()

    def set_main_time(self, t0: float, main_win_s: float):
        if not self.state.link_time:
            return

        self.state.t0 = float(t0)
        self.state.win = float(np.clip(self.state.win, 1.0, 10.0))

        self.spin_win.blockSignals(True)
        self.spin_win.setValue(self.state.win)
        self.spin_win.blockSignals(False)

        # update slider range + position to match main time
        self._update_slider_range()
        self.time_ctl.set_t0(self.state.t0)   # <-- IMPORTANT (UI sync)

        self._request_update_plot()

    # ---------- Internals ----------
    def _abs_to_display_name(self, abs_idx: int) -> str:
        if 0 <= abs_idx < len(self._ch_names_displayed):
            return self._ch_names_displayed[abs_idx]
        return f"ch[{abs_idx}]"

    def _refresh_channel_list_titles(self):
        # Update visible text for existing items
        for r in range(self.list_channels.count()):
            it = self.list_channels.item(r)
            abs_idx = int(it.data(Qt.ItemDataRole.UserRole))
            it.setText(self._abs_to_display_name(abs_idx))

    def _sync_list_widget_from_state(self):
        self.list_channels.blockSignals(True)
        self.list_channels.clear()
        for abs_idx in self.state.selected_abs:
            it = QListWidgetItem(self._abs_to_display_name(abs_idx))
            it.setData(Qt.ItemDataRole.UserRole, int(abs_idx))
            self.list_channels.addItem(it)
        self.list_channels.blockSignals(False)

    def _remove_selected_items(self):
        to_remove = {int(it.data(Qt.ItemDataRole.UserRole)) for it in self.list_channels.selectedItems()}
        if not to_remove:
            return
        remaining = [a for a in self.state.selected_abs if a not in to_remove]
        self.set_selected_channels_abs(remaining, replace=True)

    def _clear_channels(self):
        self.set_selected_channels_abs([], replace=True)

    @Slot(bool)
    def _on_link_time_toggled(self, on: bool):
        self.state.link_time = bool(on)
        self.time_ctl.set_enabled(not self.state.link_time)
        # when unlinking, keep current values; when relinking, MainWindow will push main time

    @Slot(float)
    def _on_win_changed(self, v: float):
        # Clamp win to [1, 10]
        self.state.win = float(np.clip(v, 1.0, 10.0))
        self._update_time_label()

        # Update the slider range because max t0 depends on window length
        if self._raw is not None and self._raw.n_times > 1:
            total_s = float(self._raw.times[-1])
            self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

        # Recompute plot in all cases (linked or not)
        self._request_update_plot()
        
    def _update_slider_range(self):
        if self._raw is None or self._raw.n_times <= 1:
            self.time_ctl.set_range(0.0, 0.0, 0.0)  # or your local slider equivalent
            return
        total_s = float(self._raw.times[-1])
        self.time_ctl.set_range(total_s, self.state.win, self.state.t0)

    def _update_time_label(self):
        t0 = self.state.t0
        t1 = t0 + self.state.win
        self.lbl_t.setText(f"t: [{t0:.2f}, {t1:.2f}] s  (win={self.state.win:.1f}s)")

    def _update_plot(self):
        self._update_time_label()

        if self._raw is None or self._picks is None:
            self.curve.setData([], [])
            return

        if not self.state.selected_abs:
            self.curve.setData([], [])
            return

        # ---- Clamp t0 so the [t0, t0+win] window is valid ----
        total_s = float(self._raw.times[-1]) if self._raw.n_times > 1 else 0.0
        max_t0 = max(0.0, total_s - float(self.state.win))
        self.state.t0 = float(np.clip(float(self.state.t0), 0.0, max_t0))

        fs = float(self._raw.info["sfreq"])
        start = int(self.state.t0 * fs)
        end = int((self.state.t0 + self.state.win) * fs)

        n = int(self._raw.n_times)
        start = max(0, min(start, max(0, n - 1)))
        end = max(start + 1, min(end, n))

        raw_idx = self._picks[np.asarray(self.state.selected_abs, dtype=int)]
        data_v, times = self._raw[raw_idx, start:end]
        data_v = np.asarray(data_v)
        times = np.asarray(times)

        if data_v.size == 0 or times.size < 2:
            self.curve.setData([], [])
            return

        # Decide algorithm (default to mean if not set)
        algo = getattr(self.state, "algorithm", "mean")

        # ---- Compute output signal in VOLTS (analysis space) ----
        if algo == "single":
            # First selected channel only
            y_v = data_v[0]
            y_label = "Voltage (V)"
        elif algo == "mean":
            # Mean across selected channels
            y_v = np.mean(data_v, axis=0)
            y_label = "Mean voltage (V)"
        else:
            # Placeholder algorithms: fall back to mean for now (or return empty)
            y_v = np.mean(data_v, axis=0)
            y_label = f"{algo} (fallback mean) (V)"

        # ---- Optional: match viewer display (µV + viewer gain scaling) ----
        if self.chk_match_main.isChecked():
            y = y_v * 1e6  # convert to µV like the viewer does :contentReference[oaicite:0]{index=0}
            gain_factor = 1.0 / max(1e-9, (self._main_gain_uv * self._correction_factor))  # same formula as viewer :contentReference[oaicite:1]{index=1}
            y = y * gain_factor
            self.plot.setLabel("left", "Amplitude (µV, main-scaled)")
        else:
            y = y_v
            self.plot.setLabel("left", y_label)

        # ---- Downsample + plot ----
        max_pts = 2000
        step = max(1, y.size // max_pts)
        self.curve.setData(times[::step], y[::step])

        max_pts = 2000
        step = max(1, y.size // max_pts)
        self.curve.setData(times[::step], y[::step])

        t0 = float(self.state.t0)
        t1 = t0 + float(self.state.win)

        plot_item = self.plot.getPlotItem()
        if plot_item is not None:
            vb = plot_item.getViewBox()
            if vb is not None:
                vb.setRange(xRange=(t0, t1), padding=0.0)

    def _open_add_channels_dialog(self):
        """
        Open a searchable multi-select dialog listing all displayed channels.
        Adds chosen channels to the panel selection (dedup).
        """
        if not self._ch_names_displayed:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Add channels")
        dlg.setModal(True)
        dlg.resize(420, 520)

        layout = QVBoxLayout(dlg)

        search = QLineEdit()
        search.setPlaceholderText("Search channels…")
        layout.addWidget(search)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(lst, 1)

        # Fill list with all channels (abs indices)
        for abs_idx, name in enumerate(self._ch_names_displayed):
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, int(abs_idx))
            lst.addItem(it)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def apply_filter(text: str):
            t = (text or "").strip().lower()
            for i in range(lst.count()):
                it = lst.item(i)
                it.setHidden(t not in it.text().lower())

        def select_all_visible():
            # optional helper: Ctrl+A selects only visible items
            for i in range(lst.count()):
                it = lst.item(i)
                if not it.isHidden():
                    it.setSelected(True)

        search.textChanged.connect(apply_filter)

        # Optional: Enter = OK when something selected
        search.returnPressed.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).click())

        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        # Start with current selection highlighted in the dialog (nice UX)
        current = set(self.state.selected_abs)
        for i in range(lst.count()):
            it = lst.item(i)
            abs_idx = int(it.data(Qt.ItemDataRole.UserRole))
            if abs_idx in current:
                it.setSelected(True)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen_abs: list[int] = []
        for it in lst.selectedItems():
            chosen_abs.append(int(it.data(Qt.ItemDataRole.UserRole)))

        if not chosen_abs:
            return

        # Add (dedup) to panel selection and refresh
        self.set_selected_channels_abs(chosen_abs, replace=False)

# ---------------- Other methods ----------------

    def _update_channels_title(self):

        # if you kept gb_ch as local var, promote it to self.gb_ch
        self.gb_ch.setTitle(f"Channels ({len(self.state.selected_abs)})")

    def _request_update_plot(self, delay_ms: int = 40) -> None:
        """Throttle plot updates: restart a single-shot timer."""
        if hasattr(self, "_update_timer") and self._update_timer.isActive():
            self._update_timer.stop()
        self._update_timer.start(int(delay_ms))

    def _on_panel_t0_changed(self, t0: float):
        if self.state.link_time:
            return
        self.state.t0 = float(t0)
        self._request_update_plot()

    def set_main_gain_uv(self, gain_uv: float) -> None:
        self._main_gain_uv = float(gain_uv)
        self._request_update_plot()

    def _on_algo_changed(self, _idx: int):
        self.state.algorithm = str(self.cbo_algo.currentData() or "mean")
        self._request_update_plot()