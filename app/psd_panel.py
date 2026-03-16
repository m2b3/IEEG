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

        self._selected_channel: str | None = None
        self._curve_items: dict[str, pg.PlotDataItem] = {}

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
    def __init__(self, parent=None, mark_bad_callback=None, mark_good_callback=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("PSD Panel")
        self.resize(1100, 700)

        self._mark_bad_callback = mark_bad_callback
        self._mark_good_callback = mark_good_callback

        self._raw = None
        self._picks = np.asarray([], dtype=int)
        self._display_names: list[str] = []
        self._bad_names: set[str] = set()

        self._start_s = 0.0
        self._stop_s = 0.0

        self._displayed_names: list[str] = []
        self._excluded_names: list[str] = []

        self._psd_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        self._selected_channel = None
        self._curve_items = {}

        self._build_ui()

    def _build_ui(self) -> None:
        self.excluded_list = QListWidget()
        self.excluded_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )

        self.displayed_list = QListWidget()
        self.displayed_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.displayed_list.itemSelectionChanged.connect(self._on_displayed_selection_changed)
        self.excluded_list.itemSelectionChanged.connect(self._on_excluded_selection_changed)

        self.btn_exclude = QPushButton("<<")
        self.btn_include = QPushButton(">>")
        self.btn_exclude.clicked.connect(self._move_to_excluded)
        self.btn_include.clicked.connect(self._move_to_displayed)

        self.btn_exclude_all = QPushButton("Exclude all")
        self.btn_include_all = QPushButton("Include all")
        self.btn_exclude_all.clicked.connect(self._move_all_to_excluded)
        self.btn_include_all.clicked.connect(self._move_all_to_displayed)

        self.btn_mark_bad = QPushButton("Mark selected as bad")
        self.btn_mark_bad.clicked.connect(self._mark_selected_as_bad)
        self.btn_unmark_bad = QPushButton("Unmark selected as bad")
        self.btn_unmark_bad.clicked.connect(self._unmark_selected_as_bad)
        
        left_box = QGroupBox("Excluded channels")
        left_layout = QVBoxLayout(left_box)
        left_layout.addWidget(self.excluded_list)

        right_box = QGroupBox("Displayed channels")
        right_layout = QVBoxLayout(right_box)
        right_layout.addWidget(self.displayed_list)

        mid_btns = QVBoxLayout()
        mid_btns.addStretch(1)
        mid_btns.addWidget(self.btn_exclude)
        mid_btns.addWidget(self.btn_include)
        mid_btns.addSpacing(12)
        mid_btns.addWidget(self.btn_exclude_all)
        mid_btns.addWidget(self.btn_include_all)
        mid_btns.addSpacing(12)
        mid_btns.addWidget(self.btn_mark_bad)
        mid_btns.addWidget(self.btn_unmark_bad)
        mid_btns.addStretch(1)

        top = QHBoxLayout()
        top.addWidget(left_box, 1)
        top.addLayout(mid_btns)
        top.addWidget(right_box, 1)

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Frequency (Hz)")
        self.plot.setLabel("left", "Power Spectral Density")

        layout = QVBoxLayout(self)
        layout.addLayout(top, 1)
        layout.addWidget(self.plot, 2)

    def set_psd_context(
        self,
        *,
        raw,
        picks,
        display_names,
        bad_names,
        start_s: float,
        stop_s: float,
    ) -> None:
        self._raw = raw
        self._picks = np.asarray([] if picks is None else picks, dtype=int)
        self._display_names = list(display_names or [])
        self._bad_names = set(bad_names or [])

        self._start_s = float(start_s)
        self._stop_s = float(stop_s)

        self._displayed_names = [ch for ch in self._display_names if ch not in self._bad_names]
        self._excluded_names = []

        self._rebuild_psd_cache()
        self._refresh_lists()
        self._refresh_plot()

    def _ordered(self, names: list[str]) -> list[str]:
        order = {name: i for i, name in enumerate(self._display_names)}
        return sorted(names, key=lambda x: order.get(x, 10**9))

    def _refresh_lists(self) -> None:
        self.excluded_list.clear()
        self.displayed_list.clear()
        self.excluded_list.addItems(self._excluded_names)
        self.displayed_list.addItems(self._displayed_names)

    def _move_to_excluded(self) -> None:
        selected = [item.text() for item in self.displayed_list.selectedItems()]
        if not selected:
            return

        for ch in selected:
            if ch in self._displayed_names:
                self._displayed_names.remove(ch)
            if ch not in self._excluded_names:
                self._excluded_names.append(ch)

        self._excluded_names = self._ordered(self._excluded_names)
        self._displayed_names = self._ordered(self._displayed_names)

        if self._selected_channel in selected:
            self._selected_channel = selected[0]

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()

    def _move_to_displayed(self) -> None:
        selected = [item.text() for item in self.excluded_list.selectedItems()]
        if not selected:
            return

        for ch in selected:
            if ch in self._excluded_names:
                self._excluded_names.remove(ch)
            if ch not in self._displayed_names:
                self._displayed_names.append(ch)

        self._excluded_names = self._ordered(self._excluded_names)
        self._displayed_names = self._ordered(self._displayed_names)

        if self._selected_channel in selected:
            self._selected_channel = selected[0]

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()

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

    def _refresh_plot(self) -> None:
        self.plot.clear()
        self.plot.setLabel("bottom", "Frequency (Hz)")
        self.plot.setLabel("left", "Power Spectral Density (dB/Hz)")
        self.plot.setTitle(f"PSD from {self._start_s:.3f} s to {self._stop_s:.3f} s")

        self._curve_items = {}

        for ch_name in self._displayed_names:
            cached = self._psd_cache.get(ch_name)
            if cached is None:
                continue

            freqs, psd = cached
            if freqs.size == 0:
                continue

            y = 10.0 * np.log10(np.maximum(psd, 1e-20))

            curve_item = pg.PlotCurveItem(freqs, y, pen=pg.mkPen(width=1))
            curve_item.setClickable(True, width=8)
            curve_item.sigClicked.connect(self._on_curve_clicked)

            # stocker le channel associé
            curve_item._channel_name = ch_name

            self.plot.addItem(curve_item)
            self._curve_items[ch_name] = curve_item

        self._apply_selection_highlight()

    def _on_curve_clicked(self, curve) -> None:
        ch_name = getattr(curve, "_channel_name", None)
        if ch_name is None:
            return

        self._selected_channel = ch_name
        self._sync_selection_to_lists()
        self._apply_selection_highlight()

    def _on_displayed_selection_changed(self) -> None:
        items = self.displayed_list.selectedItems()
        if not items:
            return

        self._selected_channel = items[0].text()
        self._apply_selection_highlight()

    def _on_excluded_selection_changed(self) -> None:
        items = self.excluded_list.selectedItems()
        if not items:
            return

        self._selected_channel = items[0].text()
        self._apply_selection_highlight()

    def _sync_selection_to_lists(self) -> None:
        if self._selected_channel is None:
            return

        self.displayed_list.blockSignals(True)
        self.excluded_list.blockSignals(True)

        try:
            self.displayed_list.clearSelection()
            self.excluded_list.clearSelection()

            for i in range(self.displayed_list.count()):
                item = self.displayed_list.item(i)
                if item.text() == self._selected_channel:
                    item.setSelected(True)
                    self.displayed_list.scrollToItem(item)
                    break

            for i in range(self.excluded_list.count()):
                item = self.excluded_list.item(i)
                if item.text() == self._selected_channel:
                    item.setSelected(True)
                    self.excluded_list.scrollToItem(item)
                    break
        finally:
            self.displayed_list.blockSignals(False)
            self.excluded_list.blockSignals(False)
                         
    def _apply_selection_highlight(self) -> None:
        selected = getattr(self, "_selected_channel", None)

        for ch_name, curve in self._curve_items.items():
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

        for widget in (self.displayed_list, self.excluded_list):
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

    def _move_all_to_excluded(self) -> None:
        if not self._displayed_names:
            return

        moved = list(self._displayed_names)

        for ch in moved:
            if ch not in self._excluded_names:
                self._excluded_names.append(ch)

        self._displayed_names = []
        self._excluded_names = self._ordered(self._excluded_names)

        if self._selected_channel not in self._excluded_names:
            self._selected_channel = self._excluded_names[0] if self._excluded_names else None

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()

    def _move_all_to_displayed(self) -> None:
        if not self._excluded_names:
            return

        moved = list(self._excluded_names)

        for ch in moved:
            if ch not in self._displayed_names:
                self._displayed_names.append(ch)

        self._excluded_names = []
        self._displayed_names = self._ordered(self._displayed_names)

        if self._selected_channel not in self._displayed_names:
            self._selected_channel = self._displayed_names[0] if self._displayed_names else None

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()

    def _get_selected_channel_names(self) -> list[str]:
        names: list[str] = []

        for item in self.displayed_list.selectedItems():
            names.append(item.text())

        for item in self.excluded_list.selectedItems():
            names.append(item.text())

        # remove duplicates while preserving order
        seen = set()
        out = []
        for name in names:
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _mark_selected_as_bad(self) -> None:
        selected = self._get_selected_channel_names()
        if not selected:
            return

        if self._mark_bad_callback is not None:
            self._mark_bad_callback(selected)

        for ch in selected:
            self._bad_names.add(ch)

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()

    def _unmark_selected_as_bad(self) -> None:
        selected = self._get_selected_channel_names()
        if not selected:
            return

        if self._mark_good_callback is not None:
            self._mark_good_callback(selected)

        for ch in selected:
            if ch in self._bad_names:
                self._bad_names.remove(ch)

        self._refresh_lists()
        self._sync_selection_to_lists()
        self._refresh_plot()
