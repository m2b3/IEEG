from __future__ import annotations

from typing import Optional, List, cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal, Qt, QEvent
from pyqtgraph.GraphicsScene.GraphicsScene import GraphicsScene

from mne.io import BaseRaw


class MultiChannelViewer(pg.GraphicsLayoutWidget):
    """
    Widget that displays multiple EEG channels stacked vertically.

    Layout:
      - Left: label_plot (channel names)
      - Right: signal_plot (signals over time)

    Responsibilities:
      - Keep current "view state" (time start, time range, channel range, gain)
      - Pull visible data window from MNE Raw on demand
      - Render curves + labels + cursor + time grid + min/max tags
      - Emit signals for UI coordination (MainWindow)
    """

    # ---------------- Signals ----------------
    channelClicked = Signal(int)          # absolute channel index in displayed channel list
    channelWindowChanged = Signal(int)    # emits ch_start when visible channel window changes
    timeWindowChanged = Signal(float)     # emits t_start when visible time window changes

    # Wheel zoom requests (handled by MainWindow via spinboxes)
    requestTimeRangeDelta = Signal(int)   # +1 zoom out, -1 zoom in
    requestChanRangeDelta = Signal(int)   # +1 show more channels, -1 show fewer

    # ---------------- Init ----------------
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        # ---- Raw/state ----
        self._raw: Optional[BaseRaw] = None
        self._fs: float = 1.0
        self._picks: np.ndarray = np.array([], dtype=int)
        self._channel_names: List[str] = []

        # ---- View params ----
        self._t_start: float = 0.0
        self._time_range: float = 3.0
        self._chan_range: int = 32
        self._gain_uv: float = 100.0
        self._ch_start: int = 0

        # Vertical stacking
        self._spacing: float = 200.0  # y spacing in "display units"

        # ---- Render caches ----
        self._visible_abs: np.ndarray = np.array([], dtype=int)  # abs indices in displayed channel list
        self._curves: list[pg.PlotDataItem] = []
        self._labels: list[pg.TextItem] = []
        self._minmax_items: list[pg.TextItem] = []

        self._selected_abs: Optional[int] = None

        # ---- Cursor + grid ----
        self._cursor_line: Optional[pg.InfiniteLine] = None
        self._cursor_x: Optional[float] = None
        self._time_lines: list[pg.InfiniteLine] = []

        # ---- Layout: label plot (left) + signal plot (right) ----
        self.label_plot = pg.PlotItem()
        self.signal_plot = pg.PlotItem()
        self.addItem(self.label_plot, 0, 0)
        self.addItem(self.signal_plot, 0, 1)

        self.label_plot.setMaximumWidth(100)   # try 140–200
        self.label_plot.setMinimumWidth(60)

        # Hide label plot axes
        for ax in ("bottom", "left", "right", "top"):
            self.label_plot.hideAxis(ax)

        # Signal plot axes (HIDE EVERYTHING until a file is loaded)
        for ax in ("bottom", "left", "right", "top"):
            self.signal_plot.hideAxis(ax)

        # Disable default right-click menus (keep it simple)
        self.label_plot.setMenuEnabled(False)
        self.signal_plot.setMenuEnabled(False)

        # ViewBoxes (for coordinate transforms + hit testing)
        self._label_vb = cast(pg.ViewBox, self.label_plot.getViewBox())
        self._sig_vb = cast(pg.ViewBox, self.signal_plot.getViewBox())

        # Match "EEG viewer convention": channel 0 at top, increasing downward
        self._label_vb.invertY(True)
        self._sig_vb.invertY(True)

        # We'll control scrolling/zoom ourselves
        self._label_vb.setMouseEnabled(x=False, y=False)
        self._sig_vb.setMouseEnabled(x=False, y=False)

        # Keep label plot vertically aligned with signal plot
        self._label_vb.setYLink(self._sig_vb)

        # Label plot x-range just needs enough space for text
        self._label_vb.setXRange(0.0, 100.0, padding=0)
        self._label_vb.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)

        self.signal_plot.showGrid(x=True, y=True, alpha=0.15)

        # Scene mouse click -> channel selection
        scene = cast(GraphicsScene, self.scene())
        scene.sigMouseClicked.connect(self._on_mouse_clicked)

        # eventFilter on th eviewport for wheel events
        self.viewport().installEventFilter(self)

        # Track RMB state for "RMB + wheel" zoom (more reliable than ev.buttons())
        self._rmb_down: bool = False

        # eventFilter on the viewport for wheel + mouse buttons
        self.viewport().installEventFilter(self)
        self.installEventFilter(self)

    # ---------------- Public API ----------------
    def channel_start(self) -> int:
        return int(self._ch_start)

    def time_start(self) -> float:
        return float(self._t_start)

    def set_raw(self, raw: BaseRaw, picks: Optional[np.ndarray] = None):
        """
        Attach an MNE Raw object (preload=False allowed).
        'picks' are indices into raw channels (e.g. EEG-only).
        """
        self._raw = raw
        self._fs = float(raw.info["sfreq"])

        if picks is None:
            self._picks = np.arange(raw.info["nchan"], dtype=int)
        else:
            self._picks = np.asarray(picks, dtype=int)

        self._channel_names = [raw.ch_names[i] for i in self._picks.tolist()]

        # Reset view
        self._t_start = 0.0
        self._ch_start = 0
        self._selected_abs = None

        # Now that we have data, show the time axis + light grid
        self.signal_plot.showAxis("bottom")
        self.signal_plot.setLabel("bottom", "Time (s)")
        self.signal_plot.showGrid(x=True, y=True, alpha=0.15)

        self.render()
        self.channelWindowChanged.emit(self._ch_start)

    def set_view_params(self, *, time_range=None, chan_range=None, gain=None):
        """
        Update view parameters. Any parameter can be None (unchanged).
        """
        if time_range is not None:
            self._time_range = float(time_range)
            self._clamp_time_start()
            self.timeWindowChanged.emit(self._t_start)

        if chan_range is not None:
            self._chan_range = int(chan_range)

        if gain is not None:
            self._gain_uv = float(gain)

        self.render()

    def set_time_start(self, t_start: float):
        """Move the visible time window start (seconds)."""
        if self._raw is None:
            return

        self._t_start = float(t_start)
        self._clamp_time_start()
        self.render()
        self.timeWindowChanged.emit(self._t_start)

    def set_channel_start(self, ch_start: int):
        """Move the visible channel window start (index in displayed channel list)."""
        if self._raw is None or self._picks.size == 0:
            return

        n_channels = int(len(self._picks))
        n_vis = int(min(self._chan_range, n_channels))
        max_start = max(0, n_channels - n_vis)

        new_start = max(0, min(int(ch_start), max_start))
        if new_start == self._ch_start:
            return

        self._ch_start = new_start
        self.render()
        self.channelWindowChanged.emit(self._ch_start)

    def ensure_channel_visible(self, index: int):
        """Scroll channel window so 'index' (absolute) becomes visible."""
        if self._raw is None or self._picks.size == 0:
            return

        n_channels = int(len(self._picks))
        n_vis = int(min(self._chan_range, n_channels))

        if index < self._ch_start:
            self.set_channel_start(index)
        elif index >= self._ch_start + n_vis:
            self.set_channel_start(index - n_vis + 1)

    def highlight_channel(self, index: int):
        """Highlight a channel (thicker trace + yellow label)."""
        self._selected_abs = int(index)
        self.ensure_channel_visible(index)

        idx_vis = index - self._ch_start
        for i, c in enumerate(self._curves):
            c.setPen(pg.mkPen(width=3 if i == idx_vis else 1))

        for i, txt in enumerate(self._labels):
            txt.setColor((255, 255, 0) if i == idx_vis else (180, 180, 180))

    # ---------------- Interaction ----------------
    def wheelEvent(self, ev):
        # Trackpad smooth scrolling
        pd = ev.pixelDelta()
        ad = ev.angleDelta()

        # Prefer pixelDelta when present (trackpad), else use angleDelta (mouse wheel)
        dy = pd.y() if not pd.isNull() else ad.y()
        if dy == 0:
            ev.ignore()
            return

        step_dir = -1 if dy > 0 else +1  # wheel up => -1, wheel down => +1

        pos = ev.position()
        scene_pos = self.mapToScene(pos.toPoint())

        in_label = self._label_vb.sceneBoundingRect().contains(scene_pos)
        in_signal = self._sig_vb.sceneBoundingRect().contains(scene_pos)

        # Option A: RMB + wheel on signal area => time zoom (no Ctrl)
        if self._rmb_down and in_signal:
            self.requestTimeRangeDelta.emit(step_dir)
            ev.accept()
            return

        # Normal wheel: scroll channels when over label OR signal
        if in_label or in_signal:
            self.set_channel_start(self._ch_start + step_dir)
            ev.accept()
            return

        ev.ignore()
        
    def _on_mouse_clicked(self, event):
        """Select channel by clicking near its trace/label."""
        if self._raw is None or self._visible_abs.size == 0:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.scenePos()
        in_label = self._label_vb.sceneBoundingRect().contains(pos)
        vb = self._label_vb if in_label else self._sig_vb

        data_point = vb.mapSceneToView(pos)
        y = float(data_point.y())

        centers = np.arange(len(self._visible_abs)) * self._spacing
        idx_vis = int(np.argmin(np.abs(centers - y)))
        idx_vis = max(0, min(idx_vis, len(self._visible_abs) - 1))

        idx_abs = int(self._visible_abs[idx_vis])
        self.channelClicked.emit(idx_abs)
        self.highlight_channel(idx_abs)

    def _wheel_dy(self, ev) -> int:
        # Qt6: prefer angleDelta then pixelDelta
        ad = ev.angleDelta()
        if ad is not None and ad.y() != 0:
            return int(ad.y())

        pd = ev.pixelDelta()
        if pd is not None and not pd.isNull() and pd.y() != 0:
            return int(pd.y())

        return 0

    def _handle_wheel(self, ev, region: str):
        dy = self._wheel_dy(ev)
        if dy == 0:
            ev.ignore()
            return

        # convention: wheel up = zoom in / fewer channels
        step_dir = -1 if dy > 0 else +1
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # SHIFT = zoom
        if shift:
            if region == "signal":
                self.requestTimeRangeDelta.emit(step_dir)
                ev.accept()
                return
            if region == "label":
                self.requestChanRangeDelta.emit(step_dir)
                ev.accept()
                return

        # No shift = scroll channels
        if region in ("label", "signal"):
            self.set_channel_start(self._ch_start + step_dir)
            ev.accept()
            return

        ev.ignore()

    def eventFilter(self, obj, ev):
        if obj in (self.viewport(), self):
            if ev.type() == QEvent.Type.Wheel:
                scene_pos = self.mapToScene(ev.position().toPoint())

                in_label = self._label_vb.sceneBoundingRect().contains(scene_pos)
                in_signal = self._sig_vb.sceneBoundingRect().contains(scene_pos)

                if in_signal:
                    self._handle_wheel(ev, "signal")
                    return True
                if in_label:
                    self._handle_wheel(ev, "label")
                    return True

                # swallow wheel anyway to prevent pyqtgraph default zoom/pan
                ev.ignore()
                return True

        return super().eventFilter(obj, ev)

    # ---------------- Rendering ----------------
    def render(self):
        """Redraw the viewer for the current raw + view parameters."""
        if self._raw is None or self._picks.size == 0:
            return

        raw = self._raw
        picks = self._picks
        n_channels = int(len(picks))

        # 1) Determine visible channel window
        n_vis, ch0, ch1 = self._compute_visible_channels(n_channels)
        if n_vis <= 0:
            return

        # 2) Pull visible time window data from raw (V -> µV), decimate for speed
        seg_ds_uv, t_ds = self._get_visible_data(raw, picks, ch0, ch1)
        if seg_ds_uv is None or t_ds is None:
            return

        # 3) Clear previous items
        self._clear_plots()

        # 4) Draw traces + labels
        self._draw_traces(seg_ds_uv, t_ds, n_vis)
        self._draw_labels(n_vis)

        # 5) Set view ranges
        self._set_ranges(t_ds, n_vis)

        # 6) Overlays (grid lines, cursor, min/max tags)
        self._draw_time_lines(t_ds)
        self._draw_cursor(t_ds)
        self._draw_minmax(seg_ds_uv, t_ds, n_vis)

        # 7) Restore highlight if any
        if self._selected_abs is not None:
            self.highlight_channel(self._selected_abs)

    # ---------------- Render helpers ----------------
    def _compute_visible_channels(self, n_channels: int) -> tuple[int, int, int]:
        n_vis = int(min(self._chan_range, n_channels))
        max_start = max(0, n_channels - n_vis)
        self._ch_start = max(0, min(self._ch_start, max_start))

        ch0 = int(self._ch_start)
        ch1 = int(ch0 + n_vis)

        self._visible_abs = np.arange(ch0, ch1, dtype=int)
        return n_vis, ch0, ch1

    def _get_visible_data(
        self,
        raw: BaseRaw,
        picks: np.ndarray,
        ch0: int,
        ch1: int,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        # Convert view window -> sample indices
        start_samp = int(self._t_start * self._fs)
        end_samp = int((self._t_start + self._time_range) * self._fs)

        # Clamp to raw bounds
        n_samples = int(raw.n_times)
        start_samp = max(0, min(start_samp, n_samples - 1))
        end_samp = max(start_samp + 1, min(end_samp, n_samples))

        raw_ch_picks = picks[ch0:ch1]  # indices in raw
        data_v, times = raw[raw_ch_picks, start_samp:end_samp]  # volts + seconds

        data_v = np.asarray(data_v)
        times = np.asarray(times)

        if times.ndim != 1 or times.size < 2:
            return None, None

        # V -> µV
        data_uv = data_v * 1e6
        if data_uv.shape[1] < 2:
            return None, None

        # Decimate for responsiveness
        max_points = 3000
        win_len = int(data_uv.shape[1])
        step = max(1, win_len // max_points)

        t_ds = times[::step]
        seg_ds_uv = data_uv[:, ::step]
        return seg_ds_uv, t_ds

    def _clear_plots(self):
        self.signal_plot.clear()
        self.label_plot.clear()

        self._curves = []
        self._labels = []
        self._time_lines = []
        self._cursor_line = None
        self._minmax_items = []

    def _draw_traces(self, seg_ds_uv: np.ndarray, t_ds: np.ndarray, n_vis: int):
        # Normalize amplitude by gain (±gain maps to ±1 display unit)

        correction_factor = 0.01  # Adjust this factor based on your observation
        gain_factor = 1.0 / (self._gain_uv * correction_factor)
    

        for i in range(n_vis):
            y = (seg_ds_uv[i] * gain_factor) + i * self._spacing
            curve = self.signal_plot.plot(t_ds, y)
            self._curves.append(curve)

    def _draw_labels(self, n_vis: int):
        for i, abs_idx in enumerate(self._visible_abs):
            name = self._channel_names[int(abs_idx)]
            txt = pg.TextItem(text=name, anchor=(0, 0.5), color=(180, 180, 180))
            txt.setPos(2.0, i * self._spacing)
            self.label_plot.addItem(txt)
            self._labels.append(txt)

    def _set_ranges(self, t_ds: np.ndarray, n_vis: int):
        y0 = -0.5 * self._spacing
        y1 = (n_vis - 1) * self._spacing + 0.5 * self._spacing

        t0 = float(t_ds[0])
        t1 = float(t_ds[-1])
        xpad = 0.06 * max(1e-9, (t1 - t0))  # ~6% left margin for amplitude text
        self._sig_vb.setXRange(t0 - xpad, t1, padding=0)
        self._sig_vb.setYRange(y0, y1, padding=0)

        self._label_vb.setYRange(y0, y1, padding=0)
        self._label_vb.setXRange(0.0, 100.0, padding=0)

    def _draw_time_lines(self, t_ds: np.ndarray):
        step = self._nice_time_step(self._time_range, target_lines=10)

        t0 = float(t_ds[0])
        t1 = float(t_ds[-1])

        start = np.floor(t0 / step) * step
        xs = np.arange(start, t1 + step, step)

        for x in xs:
            ln = pg.InfiniteLine(pos=float(x), angle=90, movable=False)
            ln.setZValue(-10)  # behind traces
            self.signal_plot.addItem(ln)
            self._time_lines.append(ln)

    def _draw_cursor(self, t_ds: np.ndarray):
        t0 = float(np.asarray(t_ds).reshape(-1)[0].item())
        t1 = float(np.asarray(t_ds).reshape(-1)[-1].item())

        if self._cursor_x is None or not (t0 <= self._cursor_x <= t1):
            self._cursor_x = float(t0)

        self._cursor_line = pg.InfiniteLine(angle=90, movable=True)
        self._cursor_line.setPos(self._cursor_x)
        self.signal_plot.addItem(self._cursor_line)

        self._cursor_line.sigPositionChanged.connect(self._on_cursor_moved)

    def _on_cursor_moved(self):
        if self._cursor_line is None:
            return
        self._cursor_x = float(np.asarray(self._cursor_line.value()).item())

    def _draw_minmax(self, seg_ds_uv: np.ndarray, t_ds: np.ndarray, n_vis: int):
        t0 = float(t_ds[0])
        t1 = float(t_ds[-1])
        width = max(1e-9, (t1 - t0))

        # marge qu'on a effectivement réservée dans _set_ranges
        margin = getattr(self, "_amp_left_margin", 0.08 * width)

        # x placé AU MILIEU de la marge, donc toujours visible
        x_left = t0 - 0.5 * margin

        for i in range(n_vis):
            y_center = i * self._spacing
            txt = pg.TextItem(
                text=f"±{self._gain_uv:.0f} µV",
                anchor=(0.5, 0.5),        # centré
                color=(160, 160, 160),
            )
            txt.setPos(x_left, y_center)
            self.signal_plot.addItem(txt)
            self._minmax_items.append(txt)

    # ---------------- Utility ----------------
    def _clamp_time_start(self):
        """Clamp t_start so the visible window remains within raw duration."""
        if self._raw is None or self._raw.n_times <= 1:
            self._t_start = 0.0
            return

        total_s = float(self._raw.times[-1])
        max_t0 = max(0.0, total_s - float(self._time_range))
        self._t_start = float(np.clip(self._t_start, 0.0, max_t0))

    @staticmethod
    def _nice_time_step(window_s: float, target_lines: int = 10) -> float:
        """
        Pick a "nice" spacing for vertical time lines, based on time window.
        Returns one of {1, 2, 5} * 10^k.
        """
        raw = max(float(window_s) / max(1, int(target_lines)), 1e-6)
        exp = float(np.floor(np.log10(raw)))
        base = raw / (10 ** exp)

        if base <= 1:
            nice = 1.0
        elif base <= 2:
            nice = 2.0
        elif base <= 5:
            nice = 5.0
        else:
            nice = 10.0

        return float(nice * (10 ** exp))