from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from scipy import signal

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from app.display_theme import DEFAULT_DISPLAY_THEME, DisplayTheme, get_display_theme
from app.range_slider import RangeSlider


@dataclass(slots=True)
class ScalogramContext:
    channel_name: str
    source_folder: str
    recording_name: str
    start_time: float
    duration: float
    sampling_rate: float


class ResettablePlotWidget(pg.PlotWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent=parent)
        self._default_x_range: tuple[float, float] | None = None
        self._default_y_range: tuple[float, float] | None = None
        self.setMenuEnabled(False)

    def set_default_view(self, *, x_range: tuple[float, float], y_range: tuple[float, float]) -> None:
        self._default_x_range = (float(x_range[0]), float(x_range[1]))
        self._default_y_range = (float(y_range[0]), float(y_range[1]))
        self.reset_to_default_view()

    def reset_to_default_view(self) -> None:
        if self._default_x_range is not None:
            self.setXRange(*self._default_x_range, padding=0.02)
        if self._default_y_range is not None:
            self.setYRange(*self._default_y_range, padding=0.05)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_to_default_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ScalogramViewerWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        *,
        context: ScalogramContext,
        signal_uv: np.ndarray,
        relative_times_s: np.ndarray,
        theme: str = DEFAULT_DISPLAY_THEME,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent=parent)
        self._context = context
        self._theme: DisplayTheme = get_display_theme(theme)
        self._signal_uv = np.asarray(signal_uv, dtype=float).reshape(-1)
        self._times_s = np.asarray(relative_times_s, dtype=float).reshape(-1)
        self._nyquist = max(0.0, float(context.sampling_rate) / 2.0)
        self._freq_bins_hz = np.array([], dtype=float)
        self._scalogram_times_s = np.array([], dtype=float)
        self._power = np.empty((0, 0), dtype=float)
        self._current_display_freqs = np.array([], dtype=float)
        self._current_display_power = np.empty((0, 0), dtype=float)

        self.setWindowTitle(f"Scalogram - {context.channel_name}")
        self.resize(1100, 780)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        root.addWidget(self._build_context_panel())
        root.addWidget(self._build_filter_panel())
        root.addWidget(self._build_raw_panel(), 1)
        root.addWidget(self._build_scalogram_panel(), 2)

        self._apply_theme()
        self._plot_raw_signal()
        self._compute_scalogram()
        self._apply_frequency_filter()

    def _build_context_panel(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Context")
        form = QtWidgets.QFormLayout(box)
        form.addRow("Channel:", QtWidgets.QLabel(self._context.channel_name))
        form.addRow("Source folder:", QtWidgets.QLabel(self._context.source_folder or "-"))
        form.addRow("Recording:", QtWidgets.QLabel(self._context.recording_name or "-"))
        form.addRow("Start time:", QtWidgets.QLabel(f"{self._context.start_time:.3f} s"))
        form.addRow("Duration:", QtWidgets.QLabel(f"{self._context.duration:.3f} s"))
        return box

    def _build_filter_panel(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Frequency Filter")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        self.freq_range_label = QtWidgets.QLabel()
        self.freq_range_label.setMinimumWidth(240)
        row.addWidget(self.freq_range_label)
        row.addStretch(1)

        self.btn_apply_filter = QtWidgets.QPushButton("Apply Filter")
        self.btn_apply_filter.clicked.connect(self._apply_frequency_filter)
        row.addWidget(self.btn_apply_filter)

        self.btn_reset_filter = QtWidgets.QPushButton("Reset to Default")
        self.btn_reset_filter.clicked.connect(self._reset_frequency_filter)
        row.addWidget(self.btn_reset_filter)
        layout.addLayout(row)

        self.freq_slider = RangeSlider()
        self.freq_slider.setRange(0.0, self._nyquist)
        self.freq_slider.setValues(0.0, self._nyquist, emit=False)
        self.freq_slider.valuesChanged.connect(self._update_frequency_label)
        layout.addWidget(self.freq_slider)
        self._update_frequency_label(0.0, self._nyquist)
        return box

    def _build_raw_panel(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Raw Signal")
        layout = QtWidgets.QVBoxLayout(box)
        self.raw_plot = ResettablePlotWidget()
        self.raw_plot.showGrid(x=True, y=True, alpha=0.2)
        self.raw_plot.setLabel("left", "Amplitude", units="uV")
        self.raw_plot.setLabel("bottom", "Time", units="s")
        layout.addWidget(self.raw_plot)
        return box

    def _build_scalogram_panel(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Scalogram")
        layout = QtWidgets.QVBoxLayout(box)
        self.scalogram_plot = ResettablePlotWidget()
        self.scalogram_plot.showGrid(x=True, y=True, alpha=0.15)
        self.scalogram_plot.setLabel("left", "Frequency", units="Hz")
        self.scalogram_plot.setLabel("bottom", "Time", units="s")
        self.scalogram_plot.getPlotItem().setLimits(xMin=0.0, xMax=max(0.0, self._context.duration))

        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.scalogram_plot.addItem(self.image_item)

        self.color_bar = pg.ColorBarItem(
            values=(0.0, 1.0),
            colorMap=pg.colormap.get("inferno"),
            label="Power",
            interactive=False,
            width=14,
        )
        self.color_bar.setImageItem(self.image_item, insert_in=self.scalogram_plot.getPlotItem())

        self.hover_label = QtWidgets.QLabel("Move over the scalogram to inspect time, frequency, and power.")
        self.hover_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._hover_proxy = pg.SignalProxy(
            self.scalogram_plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_scalogram_mouse_moved,
        )

        layout.addWidget(self.scalogram_plot, 1)
        layout.addWidget(self.hover_label)
        return box

    def _plot_raw_signal(self) -> None:
        self.raw_plot.clear()
        pen = pg.mkPen(self._theme.raw_signal_color, width=1.2)
        self.raw_plot.plot(self._times_s, self._signal_uv, pen=pen)

        finite_signal = self._signal_uv[np.isfinite(self._signal_uv)]
        ymin = float(np.min(finite_signal)) if finite_signal.size else -1.0
        ymax = float(np.max(finite_signal)) if finite_signal.size else 1.0
        if np.isclose(ymin, ymax):
            ymin -= 1.0
            ymax += 1.0
        margin = 0.1 * max(1.0, ymax - ymin)
        self.raw_plot.set_default_view(
            x_range=(0.0, max(self._context.duration, 1e-3)),
            y_range=(ymin - margin, ymax + margin),
        )

    def _compute_scalogram(self) -> None:
        if self._signal_uv.size < 8 or self._context.sampling_rate <= 0:
            self._freq_bins_hz = np.array([], dtype=float)
            self._power = np.empty((0, 0), dtype=float)
            self._scalogram_times_s = np.array([], dtype=float)
            return
        if not np.any(np.isfinite(self._signal_uv)):
            self._freq_bins_hz = np.array([], dtype=float)
            self._power = np.empty((0, 0), dtype=float)
            self._scalogram_times_s = np.array([], dtype=float)
            return

        fs = float(self._context.sampling_rate)
        n_samples = int(self._signal_uv.size)
        nperseg = min(max(64, n_samples // 8), 1024, n_samples)
        if nperseg < 16:
            nperseg = n_samples
        noverlap = min(nperseg // 2, max(0, nperseg - 8))
        signal_uv = np.nan_to_num(self._signal_uv, nan=0.0, posinf=0.0, neginf=0.0)

        try:
            freqs, times, power = signal.spectrogram(
                signal_uv,
                fs=fs,
                window="hann",
                nperseg=nperseg,
                noverlap=noverlap,
                detrend="constant",
                scaling="density",
                mode="psd",
            )
            self._freq_bins_hz = np.asarray(freqs, dtype=float)
            self._scalogram_times_s = np.asarray(times, dtype=float)
            self._power = np.asarray(power, dtype=float)
            
            # Validate output shapes
            if self._power.ndim != 2 or self._freq_bins_hz.ndim != 1 or self._scalogram_times_s.ndim != 1:
                raise ValueError(f"Invalid spectrogram output shapes: power{self._power.shape}, freqs{self._freq_bins_hz.shape}, times{self._scalogram_times_s.shape}")
            if self._power.shape != (self._freq_bins_hz.size, self._scalogram_times_s.size):
                raise ValueError(f"Scalogram shape mismatch: power{self._power.shape}, freqs{self._freq_bins_hz.shape}, times{self._scalogram_times_s.shape}")
        except Exception as e:
            import sys
            print(f"Warning: Scalogram computation failed: {e}", file=sys.stderr)
            self._freq_bins_hz = np.array([], dtype=float)
            self._power = np.empty((0, 0), dtype=float)
            self._scalogram_times_s = np.array([], dtype=float)

    def set_display_theme(self, theme_key: str) -> None:
        self._theme = get_display_theme(theme_key)
        self._apply_theme()
        self._plot_raw_signal()
        self._apply_frequency_filter()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background-color: {self._theme.window_background};
            }}
            QWidget {{
                color: {self._theme.text_color};
            }}
            QGroupBox {{
                background-color: {self._theme.panel_background};
                border: 1px solid {self._theme.border_color};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 8px;
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px 0 4px;
            }}
            QPushButton {{
                background-color: {self._theme.button_background};
                color: {self._theme.text_color};
                border: 1px solid {self._theme.border_color};
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: {self._theme.button_hover_background};
            }}
            """
        )
        self.hover_label.setStyleSheet(f"color: {self._theme.secondary_text_color};")
        self.raw_plot.setBackground(self._theme.viewer_background)
        self.scalogram_plot.setBackground(self._theme.viewer_background)
        self._apply_plot_theme(self.raw_plot.getPlotItem())
        self._apply_plot_theme(self.scalogram_plot.getPlotItem())
        self.color_bar.axis.setPen(pg.mkPen(self._theme.axis_color, width=1))
        self.color_bar.axis.setTextPen(pg.mkPen(self._theme.axis_color, width=1))

    def _apply_plot_theme(self, plot_item: pg.PlotItem) -> None:
        axis_pen = pg.mkPen(self._theme.axis_color, width=1)
        for axis_name in ("bottom", "left", "right", "top"):
            axis = plot_item.getAxis(axis_name)
            if axis is None:
                continue
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)

    def _update_frequency_label(self, f_min: float, f_max: float) -> None:
        self.freq_range_label.setText(f"Displayed range: {f_min:.1f} Hz - {f_max:.1f} Hz")

    def _reset_frequency_filter(self) -> None:
        self.freq_slider.setValues(0.0, self._nyquist)
        self._apply_frequency_filter()

    def _frequency_bounds(self, freqs: np.ndarray) -> tuple[float, float]:
        finite = np.asarray(freqs, dtype=float).reshape(-1)
        finite = np.sort(finite[np.isfinite(finite)])
        if finite.size == 0:
            return 0.0, max(1.0, self._nyquist)

        if finite.size == 1:
            all_freqs = np.asarray(self._freq_bins_hz, dtype=float).reshape(-1)
            all_freqs = np.sort(all_freqs[np.isfinite(all_freqs)])
            diffs = np.diff(all_freqs)
            diffs = diffs[diffs > 0]
            step = float(np.median(diffs)) if diffs.size else max(1.0, self._nyquist)
            y0 = float(finite[0] - step / 2.0)
            y1 = float(finite[0] + step / 2.0)
        else:
            low_step = max(1e-6, float(finite[1] - finite[0]))
            high_step = max(1e-6, float(finite[-1] - finite[-2]))
            y0 = float(finite[0] - low_step / 2.0)
            y1 = float(finite[-1] + high_step / 2.0)

        y0 = max(0.0, y0)
        if self._nyquist > 0:
            y1 = min(float(self._nyquist), y1)
        if y1 <= y0:
            y1 = y0 + 1e-6
        return y0, y1

    def _apply_frequency_filter(self) -> None:
        if self._power.size == 0 or self._freq_bins_hz.size == 0:
            self._current_display_freqs = np.array([], dtype=float)
            self._current_display_power = np.empty((0, 0), dtype=float)
            self.hover_label.setText("Scalogram unavailable for this interval.")
            return

        f_min, f_max = self.freq_slider.values()
        if f_max <= f_min:
            f_max = min(self._nyquist, f_min + 1.0)
            self.freq_slider.setValues(f_min, f_max, emit=False)
            self._update_frequency_label(f_min, f_max)

        mask = (self._freq_bins_hz >= max(0.0, f_min)) & (self._freq_bins_hz <= min(self._nyquist, f_max))
        if not np.any(mask):
            mask = self._freq_bins_hz >= 0.0

        display_freqs = self._freq_bins_hz[mask]
        display_power = self._power[mask, :]
        if display_freqs.size == 0 or display_power.size == 0:
            self._current_display_freqs = np.array([], dtype=float)
            self._current_display_power = np.empty((0, 0), dtype=float)
            self.hover_label.setText("No frequency bins available in the selected range.")
            return

        self._current_display_freqs = display_freqs
        self._current_display_power = display_power

        power_db = 10.0 * np.log10(np.maximum(display_power, 1e-12))
        finite_power_db = power_db[np.isfinite(power_db)]
        if finite_power_db.size == 0:
            self._current_display_freqs = np.array([], dtype=float)
            self._current_display_power = np.empty((0, 0), dtype=float)
            self.hover_label.setText("Scalogram unavailable for this interval.")
            return

        level_min = float(np.min(finite_power_db))
        level_max = float(np.max(finite_power_db))
        power_db = np.where(np.isfinite(power_db), power_db, level_min)

        x0 = 0.0
        x1 = max(float(self._context.duration), 1e-3)
        y0, y1 = self._frequency_bounds(display_freqs)

        self.image_item.setImage(power_db, autoLevels=False)
        self.image_item.setRect(QtCore.QRectF(x0, y0, max(1e-6, x1 - x0), max(1e-6, y1 - y0)))

        if np.isclose(level_min, level_max):
            level_max = level_min + 1.0
        self.image_item.setLevels((level_min, level_max))
        self.color_bar.setLevels(values=(level_min, level_max))

        self.scalogram_plot.set_default_view(
            x_range=(0.0, x1),
            y_range=(max(0.0, y0), max(y0 + 1e-6, y1)),
        )
        self.hover_label.setText("Move over the scalogram to inspect time, frequency, and power.")

    def _on_scalogram_mouse_moved(self, payload) -> None:
        if self._current_display_power.size == 0 or self._current_display_freqs.size == 0:
            return

        pos = payload[0]
        if not self.scalogram_plot.sceneBoundingRect().contains(pos):
            return

        view_pos = self.scalogram_plot.getPlotItem().vb.mapSceneToView(pos)
        rel_t = float(view_pos.x())
        freq = float(view_pos.y())
        if rel_t < 0.0 or rel_t > self._context.duration:
            return

        col = int(np.argmin(np.abs(self._scalogram_times_s - rel_t)))
        row = int(np.argmin(np.abs(self._current_display_freqs - freq)))
        if not (0 <= row < self._current_display_power.shape[0] and 0 <= col < self._current_display_power.shape[1]):
            return

        power = float(self._current_display_power[row, col])
        abs_t = self._context.start_time + rel_t
        self.hover_label.setText(
            f"Time: {rel_t:.3f} s (abs {abs_t:.3f} s) | "
            f"Frequency: {self._current_display_freqs[row]:.2f} Hz | "
            f"Power: {power:.4g}"
        )


def build_scalogram_context(
    *,
    channel_name: str,
    loaded_file: Path | None,
    start_time: float,
    duration: float,
    sampling_rate: float,
) -> ScalogramContext:
    source_folder = ""
    recording_name = ""
    if loaded_file is not None:
        source_folder = str(loaded_file.parent)
        recording_name = loaded_file.name

    return ScalogramContext(
        channel_name=channel_name,
        source_folder=source_folder,
        recording_name=recording_name,
        start_time=float(start_time),
        duration=float(duration),
        sampling_rate=float(sampling_rate),
    )
