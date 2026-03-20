from __future__ import annotations

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor


class PSDIntervalDialog(QDialog):
    def __init__(self, recording_duration_s: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PSD interval")
        self.setModal(True)

        self._duration_s = max(0.0, float(recording_duration_s))

        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(3)
        self.start_spin.setRange(0.0, self._duration_s)
        self.start_spin.setSingleStep(1.0)
        self.start_spin.setSuffix(" s")
        self.start_spin.setValue(0.0)

        self.stop_spin = QDoubleSpinBox()
        self.stop_spin.setDecimals(3)
        self.stop_spin.setRange(0.0, self._duration_s)
        self.stop_spin.setSingleStep(1.0)
        self.stop_spin.setSuffix(" s")
        self.stop_spin.setValue(min(200.0, self._duration_s))

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff6b6b;")

        form = QFormLayout()
        form.addRow("Start time", self.start_spin)
        form.addRow("Stop time", self.stop_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        start_s = float(self.start_spin.value())
        stop_s = float(self.stop_spin.value())

        if start_s < 0.0:
            self.error_label.setText("Start time must be >= 0 s.")
            return

        if stop_s <= start_s:
            self.error_label.setText("Stop time must be greater than start time.")
            return

        if stop_s > self._duration_s:
            self.error_label.setText(
                f"Stop time must be <= recording duration ({self._duration_s:.3f} s)."
            )
            return

        self.accept()

    def values(self) -> tuple[float, float]:
        return float(self.start_spin.value()), float(self.stop_spin.value())


class PSDPanel(QWidget):
    GROUPS = ("macro", "micro")

    def __init__(self, parent=None, mark_bad_callback=None, mark_good_callback=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("PSD Panel")
        self.resize(1400, 850)

        self._mark_bad_callback = mark_bad_callback
        self._mark_good_callback = mark_good_callback

        self._raw = None
        self._picks = np.asarray([], dtype=int)
        self._display_names: list[str] = []
        self._bad_names: set[str] = set()

        self._start_s = 0.0
        self._stop_s = 0.0

        self._group_channels: dict[str, list[str]] = {
            "macro": [],
            "micro": [],
        }
        self._group_state: dict[str, dict[str, list[str]]] = {
            "macro": {"displayed": [], "excluded": []},
            "micro": {"displayed": [], "excluded": []},
        }

        self._psd_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        self._selected_channel: dict[str, str | None] = {
            "macro": None,
            "micro": None,
        }

        self._curve_items: dict[str, dict[str, pg.PlotCurveItem]] = {
            "macro": {},
            "micro": {},
        }
        self._curve_to_channel: dict[str, dict[int, str]] = {
            "macro": {},
            "micro": {},
        }

        self._lists: dict[str, dict[str, QListWidget]] = {}
        self._plots: dict[str, pg.PlotWidget] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        grid = QGridLayout()
        root.addLayout(grid)

        for col, group in enumerate(self.GROUPS):
            box = QGroupBox(group.capitalize())
            box_layout = QVBoxLayout(box)

            excluded_list = QListWidget()
            excluded_list.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            excluded_list.itemSelectionChanged.connect(
                lambda g=group: self._on_excluded_selection_changed(g)
            )

            displayed_list = QListWidget()
            displayed_list.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            displayed_list.itemSelectionChanged.connect(
                lambda g=group: self._on_displayed_selection_changed(g)
            )

            btn_exclude = QPushButton("<<")
            btn_include = QPushButton(">>")
            btn_exclude.clicked.connect(lambda checked=False, g=group: self._move_to_excluded(g))
            btn_include.clicked.connect(lambda checked=False, g=group: self._move_to_displayed(g))

            btn_exclude_all = QPushButton("Exclude all")
            btn_include_all = QPushButton("Include all")
            btn_exclude_all.clicked.connect(lambda checked=False, g=group: self._move_all_to_excluded(g))
            btn_include_all.clicked.connect(lambda checked=False, g=group: self._move_all_to_displayed(g))

            btn_mark_bad = QPushButton("Mark selected as bad")
            btn_unmark_bad = QPushButton("Unmark selected as bad")
            btn_mark_bad.clicked.connect(lambda checked=False, g=group: self._mark_selected_as_bad(g))
            btn_unmark_bad.clicked.connect(lambda checked=False, g=group: self._unmark_selected_as_bad(g))

            left_box = QGroupBox("Excluded channels")
            left_layout = QVBoxLayout(left_box)
            left_layout.addWidget(excluded_list)

            right_box = QGroupBox("Displayed channels")
            right_layout = QVBoxLayout(right_box)
            right_layout.addWidget(displayed_list)

            mid_btns = QVBoxLayout()
            mid_btns.addStretch(1)
            mid_btns.addWidget(btn_exclude)
            mid_btns.addWidget(btn_include)
            mid_btns.addSpacing(12)
            mid_btns.addWidget(btn_exclude_all)
            mid_btns.addWidget(btn_include_all)
            mid_btns.addSpacing(12)
            mid_btns.addWidget(btn_mark_bad)
            mid_btns.addWidget(btn_unmark_bad)
            mid_btns.addStretch(1)

            top = QHBoxLayout()
            top.addWidget(left_box, 1)
            top.addLayout(mid_btns)
            top.addWidget(right_box, 1)

            plot = pg.PlotWidget()
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.setLabel("bottom", "Frequency (Hz)")
            plot.setLabel("left", "Power Spectral Density (dB/Hz)")

            box_layout.addLayout(top, 1)
            box_layout.addWidget(plot, 2)

            self._lists[group] = {
                "excluded": excluded_list,
                "displayed": displayed_list,
            }
            self._plots[group] = plot

            grid.addWidget(box, 0, col)

    def set_psd_context(
        self,
        *,
        raw,
        picks,
        display_names,
        bad_names,
        start_s: float,
        stop_s: float,
        macro_names=None,
        micro_names=None,
    ) -> None:
        
        self._raw = raw
        self._picks = np.asarray([] if picks is None else picks, dtype=int)
        self._display_names = list(display_names or [])
        self._bad_names = set(bad_names or [])
        self._start_s = float(start_s)
        self._stop_s = float(stop_s)

        ordered_all = list(self._display_names)

        macro_set = set(macro_names or [])
        micro_set = set(micro_names or [])

        self._group_channels["macro"] = [ch for ch in ordered_all if ch in macro_set]
        self._group_channels["micro"] = [ch for ch in ordered_all if ch in micro_set]

        if not self._group_channels["macro"] and not self._group_channels["micro"]:
            self._group_channels["macro"] = list(ordered_all)
            self._group_channels["micro"] = []

        for group in self.GROUPS:
            self._group_state[group]["displayed"] = list(self._group_channels[group])
            self._group_state[group]["excluded"] = []
            displayed = self._group_state[group]["displayed"]
            self._selected_channel[group] = displayed[0] if displayed else None

        self._rebuild_psd_cache()
        self._refresh_lists()
        self._refresh_all_plots()
        self._sync_selection_to_lists()

    def _ordered(self, names: list[str]) -> list[str]:
        order = {name: i for i, name in enumerate(self._display_names)}
        return sorted(names, key=lambda x: order.get(x, 10**9))

    def _current_lists(self, group: str):
        return self._lists[group]["displayed"], self._lists[group]["excluded"]

    def _get_selected_channel_names(self, group: str) -> list[str]:
        displayed_list, excluded_list = self._current_lists(group)
        names: list[str] = []

        for item in displayed_list.selectedItems():
            names.append(item.text())
        for item in excluded_list.selectedItems():
            names.append(item.text())

        seen = set()
        out = []
        for name in names:
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _move_to_excluded(self, group: str) -> None:
        displayed_list, _ = self._current_lists(group)
        selected = [item.text() for item in displayed_list.selectedItems()]
        if not selected:
            return

        displayed = self._group_state[group]["displayed"]
        excluded = self._group_state[group]["excluded"]

        for ch in selected:
            if ch in displayed:
                displayed.remove(ch)
            if ch not in excluded:
                excluded.append(ch)

        self._group_state[group]["displayed"] = self._ordered(displayed)
        self._group_state[group]["excluded"] = self._ordered(excluded)

        if self._selected_channel[group] in selected:
            remaining = self._group_state[group]["displayed"] or self._group_state[group]["excluded"]
            self._selected_channel[group] = remaining[0] if remaining else None

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _move_to_displayed(self, group: str) -> None:
        _, excluded_list = self._current_lists(group)
        selected = [item.text() for item in excluded_list.selectedItems()]
        if not selected:
            return

        displayed = self._group_state[group]["displayed"]
        excluded = self._group_state[group]["excluded"]

        for ch in selected:
            if ch in excluded:
                excluded.remove(ch)
            if ch not in displayed:
                displayed.append(ch)

        self._group_state[group]["displayed"] = self._ordered(displayed)
        self._group_state[group]["excluded"] = self._ordered(excluded)

        if selected:
            self._selected_channel[group] = selected[0]

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _move_all_to_excluded(self, group: str) -> None:
        displayed = self._group_state[group]["displayed"]
        excluded = self._group_state[group]["excluded"]

        if not displayed:
            return

        for ch in list(displayed):
            if ch not in excluded:
                excluded.append(ch)

        self._group_state[group]["displayed"] = []
        self._group_state[group]["excluded"] = self._ordered(excluded)

        items = self._group_state[group]["excluded"]
        self._selected_channel[group] = items[0] if items else None

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _move_all_to_displayed(self, group: str) -> None:
        displayed = self._group_state[group]["displayed"]
        excluded = self._group_state[group]["excluded"]

        if not excluded:
            return

        for ch in list(excluded):
            if ch not in displayed:
                displayed.append(ch)

        self._group_state[group]["excluded"] = []
        self._group_state[group]["displayed"] = self._ordered(displayed)

        items = self._group_state[group]["displayed"]
        self._selected_channel[group] = items[0] if items else None

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _mark_selected_as_bad(self, group: str) -> None:
        selected = self._get_selected_channel_names(group)
        if not selected:
            return

        if self._mark_bad_callback is not None:
            self._mark_bad_callback(selected)

        for ch in selected:
            self._bad_names.add(ch)

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _unmark_selected_as_bad(self, group: str) -> None:
        selected = self._get_selected_channel_names(group)
        if not selected:
            return

        if self._mark_good_callback is not None:
            self._mark_good_callback(selected)

        for ch in selected:
            self._bad_names.discard(ch)

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot(group)

    def _rebuild_psd_cache(self) -> None:
        self._psd_cache.clear()

        if self._raw is None or self._picks.size == 0:
            return

        sfreq = float(self._raw.info["sfreq"])
        start_idx = max(0, int(round(self._start_s * sfreq)))
        stop_idx = min(int(self._raw.n_times), int(round(self._stop_s * sfreq)))

        if stop_idx <= start_idx:
            return

        data = self._raw.get_data(picks=self._picks, start=start_idx, stop=stop_idx)

        for i, ch_name in enumerate(self._display_names):
            if i >= data.shape[0]:
                continue

            x = np.asarray(data[i], dtype=float)
            if x.size < 8:
                continue

            freqs, psd = self._compute_psd_welch(x, sfreq)
            self._psd_cache[ch_name] = (freqs, psd)

    def _compute_psd_welch(self, x: np.ndarray, sfreq: float) -> tuple[np.ndarray, np.ndarray]:
        n = x.size
        if n < 8:
            return np.array([]), np.array([])

        nperseg = min(2048, n)
        noverlap = nperseg // 2
        step = max(1, nperseg - noverlap)

        window = np.hanning(nperseg)
        scale = sfreq * np.sum(window ** 2)

        segments = []
        for start in range(0, n - nperseg + 1, step):
            seg = x[start:start + nperseg]
            seg = seg - np.mean(seg)
            spec = np.fft.rfft(seg * window)
            pxx = (np.abs(spec) ** 2) / scale
            segments.append(pxx)

        if not segments:
            return np.array([]), np.array([])

        psd = np.mean(np.vstack(segments), axis=0)
        freqs = np.fft.rfftfreq(nperseg, d=1.0 / sfreq)
        return freqs, psd

    def _refresh_all_plots(self) -> None:
        for group in self.GROUPS:
            self._refresh_plot(group)

    def _refresh_plot(self, group: str) -> None:
        plot = self._plots[group]
        plot.clear()
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "Power Spectral Density (dB/Hz)")
        plot.setTitle(
            f"{group.capitalize()} PSD from {self._start_s:.3f} s to {self._stop_s:.3f} s"
        )

        self._curve_items[group] = {}
        self._curve_to_channel[group] = {}

        for ch_name in self._group_state[group]["displayed"]:
            cached = self._psd_cache.get(ch_name)
            if cached is None:
                continue

            freqs, psd = cached
            if freqs.size == 0:
                continue

            y = 10.0 * np.log10(np.maximum(psd, 1e-20))
            curve_item = pg.PlotCurveItem(freqs, y, pen=pg.mkPen(width=1))
            curve_item.setClickable(True, width=8)
            curve_item.sigClicked.connect(lambda curve, g=group: self._on_curve_clicked(g, curve))

            plot.addItem(curve_item)
            self._curve_items[group][ch_name] = curve_item
            self._curve_to_channel[group][id(curve_item)] = ch_name

        self._apply_selection_highlight(group)

    def _refresh_lists(self) -> None:
        for group in self.GROUPS:
            displayed_list, excluded_list = self._current_lists(group)
            displayed_list.clear()
            excluded_list.clear()
            excluded_list.addItems(self._group_state[group]["excluded"])
            displayed_list.addItems(self._group_state[group]["displayed"])
            self._apply_selection_highlight(group)

    def _apply_selection_highlight(self, group: str) -> None:
        selected = self._selected_channel[group]

        for ch_name, curve in self._curve_items[group].items():
            is_selected = (ch_name == selected)
            is_bad = (ch_name in self._bad_names)

            if is_selected and is_bad:
                curve.setPen(pg.mkPen("r", width=3))
            elif is_selected:
                curve.setPen(pg.mkPen("y", width=3))
            elif is_bad:
                curve.setPen(pg.mkPen("r", width=1))
            else:
                curve.setPen(pg.mkPen(width=1))

        for widget in self._current_lists(group):
            for i in range(widget.count()):
                item = widget.item(i)
                ch_name = item.text()

                font = item.font()
                font.setBold(ch_name == selected)
                item.setFont(font)

                if ch_name in self._bad_names:
                    item.setForeground(QColor("red"))
                else:
                    item.setForeground(QColor("white"))

    def _sync_selection_to_lists(self) -> None:
        for group in self.GROUPS:
            selected = self._selected_channel[group]
            if selected is None:
                continue

            displayed_list, excluded_list = self._current_lists(group)
            displayed_list.blockSignals(True)
            excluded_list.blockSignals(True)

            try:
                displayed_list.clearSelection()
                excluded_list.clearSelection()

                for i in range(displayed_list.count()):
                    item = displayed_list.item(i)
                    if item.text() == selected:
                        item.setSelected(True)
                        displayed_list.scrollToItem(item)
                        break

                for i in range(excluded_list.count()):
                    item = excluded_list.item(i)
                    if item.text() == selected:
                        item.setSelected(True)
                        excluded_list.scrollToItem(item)
                        break
            finally:
                displayed_list.blockSignals(False)
                excluded_list.blockSignals(False)

    def _on_curve_clicked(self, group: str, curve) -> None:
        ch_name = self._curve_to_channel[group].get(id(curve))
        if ch_name is None:
            return

        self._selected_channel[group] = ch_name
        self._sync_selection_to_lists()
        self._apply_selection_highlight(group)

    def _on_displayed_selection_changed(self, group: str) -> None:
        displayed_list, _ = self._current_lists(group)
        items = displayed_list.selectedItems()
        if not items:
            return

        self._selected_channel[group] = items[0].text()
        self._apply_selection_highlight(group)

    def _on_excluded_selection_changed(self, group: str) -> None:
        _, excluded_list = self._current_lists(group)
        items = excluded_list.selectedItems()
        if not items:
            return

        self._selected_channel[group] = items[0].text()
        self._apply_selection_highlight(group)