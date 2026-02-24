import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal, Qt
from typing import cast, Optional, List
from pyqtgraph.GraphicsScene.GraphicsScene import GraphicsScene

from mne.io import BaseRaw

print("✅ LOADING app.plot from:", __file__)

class MultiChannelViewer(pg.GraphicsLayoutWidget):

    channelClicked = Signal(int)          # absolute channel index in "shown channels"
    channelWindowChanged = Signal(int)    # emits ch_start when visible window changes
    timeWindowChanged = Signal(float)  
    requestTimeRangeDelta = Signal(int)   # +1 zoom out, -1 zoom in
    requestChanRangeDelta = Signal(int)   # +1 show more channels, -1 show fewer

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        print("✅ MultiChannelViewer __init__ called")

        # ----- Raw/state -----
        self._raw: Optional[BaseRaw] = None
        self._fs: float = 1.0
        self._channel_names: List[str] = []
        self._picks: np.ndarray = np.array([], dtype=int)

        # ----- View params -----
        self._t_start: float = 0.0
        self._time_range: float = 3.0
        self._chan_range: int = 32
        self._gain_uv: float = 100.0        # display scale in µV (±)
        self._ch_start: int = 0
        self._spacing: float = 200.0        # vertical spacing in µV units

        self._visible_abs: np.ndarray = np.array([], dtype=int)  # absolute in displayed channel list
        self._curves: list[pg.PlotDataItem] = []
        self._labels: list[pg.TextItem] = []
        self._minmax_items: list[pg.TextItem] = []
        self._selected_abs: int | None = None

        # ----- Layout: label plot (left) + signal plot (right) -----
        self.label_plot = pg.PlotItem()
        self.signal_plot = pg.PlotItem()
        self.addItem(self.label_plot, 0, 0)
        self.addItem(self.signal_plot, 0, 1)

        self.label_plot.setMaximumWidth(260)

        for ax in ("bottom", "left", "right", "top"):
            self.label_plot.hideAxis(ax)
        self.signal_plot.hideAxis("left")
        self.signal_plot.showAxis("bottom")                
        self.signal_plot.setLabel("bottom", "Time (s)")    

        self.label_plot.setMenuEnabled(False)
        self.signal_plot.setMenuEnabled(False)

        self._label_vb = cast(pg.ViewBox, self.label_plot.getViewBox())
        self._sig_vb = cast(pg.ViewBox, self.signal_plot.getViewBox())

        self._label_vb.invertY(True)
        self._sig_vb.invertY(True)

        self._label_vb.setMouseEnabled(x=False, y=False)
        self._sig_vb.setMouseEnabled(x=False, y=False)

        self._label_vb.setYLink(self._sig_vb)

        # Label coordinate system wide enough for text
        self._label_vb.setXRange(0.0, 100.0, padding=0)
        self._label_vb.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)

        self.signal_plot.showGrid(x=True, y=True, alpha=0.15)

        scene = cast(GraphicsScene, self.scene())
        scene.sigMouseClicked.connect(self._on_mouse_clicked)

         # ---- Cursor (vertical line) ----
        self._cursor_line: pg.InfiniteLine | None = None
        self._cursor_x: float | None = None

        self._time_lines: list[pg.InfiniteLine] = []
        self._time_grid_step_s: float = 1.0   # choose 0.5, 1, 2, 5...

    # ---------------- Public API ----------------
    def channel_start(self) -> int:
        return self._ch_start

    def set_raw(self, raw: BaseRaw, picks: Optional[np.ndarray] = None):
        """
        Provide an MNE Raw (preload=False allowed).
        picks: indices into raw (e.g., EEG-only). If None, show all channels.
        """
        self._raw = raw
        self._fs = float(raw.info["sfreq"])

        if picks is None:
            self._picks = np.arange(raw.info["nchan"], dtype=int)
        else:
            self._picks = np.asarray(picks, dtype=int)

        self._channel_names = [raw.ch_names[i] for i in self._picks.tolist()]

        self._t_start = 0.0
        self._ch_start = 0
        self._selected_abs = None

        self.render()
        self.channelWindowChanged.emit(self._ch_start)

    def set_view_params(self, *, time_range=None, chan_range=None, gain=None):
        if time_range is not None:
            self._time_range = float(time_range)
            # clamp t_start if window size changed
            if self._raw is not None and self._raw.n_times > 1:
                total_s = float(self._raw.times[-1])
                max_t0 = max(0.0, total_s - self._time_range)
                self._t_start = float(np.clip(self._t_start, 0.0, max_t0))
                self.timeWindowChanged.emit(self._t_start)

        if chan_range is not None:
            self._chan_range = int(chan_range)
        if gain is not None:
            self._gain_uv = float(gain)
        self.render()

    def set_channel_start(self, ch_start: int):
        if self._raw is None or self._picks.size == 0:
            return
        n_channels = len(self._picks)
        n_vis = min(self._chan_range, n_channels)
        max_start = max(0, n_channels - n_vis)

        new_start = max(0, min(int(ch_start), max_start))
        if new_start == self._ch_start:
            return

        self._ch_start = new_start
        self.render()
        self.channelWindowChanged.emit(self._ch_start)

    def ensure_channel_visible(self, index: int):
        if self._raw is None or self._picks is None:
            return
        n_channels = len(self._picks)
        n_vis = min(self._chan_range, n_channels)

        if index < self._ch_start:
            self.set_channel_start(index)
        elif index >= self._ch_start + n_vis:
            self.set_channel_start(index - n_vis + 1)

    def highlight_channel(self, index: int):
        self._selected_abs = int(index)
        self.ensure_channel_visible(index)
        idx_vis = index - self._ch_start

        for i, c in enumerate(self._curves):
            c.setPen(pg.mkPen(width=3 if i == idx_vis else 1))

        for i, txt in enumerate(self._labels):
            txt.setColor((255, 255, 0) if i == idx_vis else (180, 180, 180))



    # ---------------- Interaction ----------------
    def wheelEvent(self, ev):
            delta = ev.angleDelta().y()
            if delta == 0:
                return
            
            pos = ev.position()  # QPointF in widget coords (Qt6)
            scene_pos = self.mapToScene(pos.toPoint())

            in_label = self._label_vb.sceneBoundingRect().contains(scene_pos)
            in_signal = self._sig_vb.sceneBoundingRect().contains(scene_pos)

            direction = -1 if delta > 0 else +1

            if in_signal:
                self.requestTimeRangeDelta.emit(direction)
                ev.accept()
                return

            if in_label:
                self.requestChanRangeDelta.emit(direction)
                ev.accept()
                return

            ev.ignore()
            
    def _on_mouse_clicked(self, event):
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

    def set_time_start(self, t_start: float):
            if self._raw is None:
                return
            # Clamp to valid range
            total_s = float(self._raw.times[-1]) if self._raw.n_times > 1 else 0.0
            max_t0 = max(0.0, total_s - self._time_range)
            self._t_start = float(np.clip(t_start, 0.0, max_t0))
            self.render()
            self.timeWindowChanged.emit(self._t_start)

    def time_start(self) -> float:
        return float(self._t_start)


    # ---------------- Rendering ----------------
    def render(self):
        if self._raw is None or self._picks is None:
            return

        raw = self._raw
        assert raw is not None


        picks = self._picks
        n_channels = len(picks)

        # Visible channel window
        n_vis = min(self._chan_range, n_channels)
        max_start = max(0, n_channels - n_vis)
        self._ch_start = max(0, min(self._ch_start, max_start))

        ch0 = self._ch_start
        ch1 = ch0 + n_vis
        self._visible_abs = np.arange(ch0, ch1, dtype=int)

        # Time window in samples
        start_samp = int(self._t_start * self._fs)
        end_samp = int((self._t_start + self._time_range) * self._fs)

        # Clamp to raw length
        n_samples = raw.n_times
        start_samp = max(0, min(start_samp, n_samples - 1))
        end_samp = max(start_samp + 1, min(end_samp, n_samples))

        # Slice only visible channels + window
        raw_ch_picks = picks[ch0:ch1]  # indices in raw
        data_v, times = raw[raw_ch_picks, start_samp:end_samp]  # data in VOLTS
        
        data_v = np.asarray(data_v)
        times = np.asarray(times)
        if times.ndim != 1 or times.size < 2:
            return

        # convert to µV
        data_uv = data_v * 1e6

        win_len = data_uv.shape[1]
        if win_len < 2:
            return

        # Decimate for speed
        max_points = 3000
        step = max(1, win_len // max_points)
        t_ds = times[::step]
        seg_ds = data_uv[:, ::step]


        # Clear plots
        self.signal_plot.clear()
        self.label_plot.clear()
        self._curves = []
        self._labels = []

        # Map "scale (µV): ±X" into a multiplicative factor:
        # if user sets ±100 µV, then a 100 µV wave should take "1 unit" visually.
        # We'll normalize by gain_uv:
        gain_factor = 1.0 / max(1e-9, self._gain_uv)

        # Plot traces
        for i in range(n_vis):
            y = (seg_ds[i] * gain_factor) + i * self._spacing
            curve = self.signal_plot.plot(t_ds, y)
            self._curves.append(curve)

        # Labels
        for i, abs_idx in enumerate(self._visible_abs):
            name = self._channel_names[int(abs_idx)]
            txt = pg.TextItem(text=name, anchor=(0, 0.5), color=(180, 180, 180))
            txt.setPos(2.0, i * self._spacing)
            self.label_plot.addItem(txt)
            self._labels.append(txt)

        # Ranges
        y0 = -0.5 * self._spacing
        y1 = (n_vis - 1) * self._spacing + 0.5 * self._spacing

        self._sig_vb.setXRange(float(t_ds[0]), float(t_ds[-1]), padding=0)
        self._sig_vb.setYRange(y0, y1, padding=0)
        self._label_vb.setYRange(y0, y1, padding=0)
        self._label_vb.setXRange(0.0, 100.0, padding=0)


        # ---- Vertical time section lines ----
        for ln in getattr(self, "_time_lines", []):
            self.signal_plot.removeItem(ln)
        self._time_lines = []


        step = self._nice_time_step(self._time_range, target_lines=10)

        # lines spanning the visible window
        t0 = float(t_ds[0])
        t1 = float(t_ds[-1])

        # align to step boundaries
        start = np.floor(t0 / step) * step
        xs = np.arange(start, t1 + step, step)

        for x in xs:
            ln = pg.InfiniteLine(pos=float(x), angle=90, movable=False)
            # make them subtle (no color spec if you prefer default; but you can set a light pen)
            ln.setZValue(-10)  # behind traces
            self.signal_plot.addItem(ln)
            self._time_lines.append(ln)

        # ---- Cursor (vertical movable line) ----
        # Keep previous cursor position if possible, otherwise set to middle
        t0 = np.asarray(t_ds).reshape(-1)[0].item()
        t1 = np.asarray(t_ds).reshape(-1)[-1].item()

        if self._cursor_x is None or not (t0 <= self._cursor_x <= t1):
            self._cursor_x = float(t0)

        self._cursor_line = pg.InfiniteLine(angle=90, movable=True)
        self._cursor_line.setPos(self._cursor_x)
        self.signal_plot.addItem(self._cursor_line)

        def _on_cursor_move():
            if self._cursor_line is None:
                return
            self._cursor_x = float(np.asarray(self._cursor_line.value()).item())

        self._cursor_line.sigPositionChanged.connect(_on_cursor_move)

        # clear previous min/max labels
        for it in self._minmax_items:
            self.signal_plot.removeItem(it)
        self._minmax_items = []

        t_left = float(t_ds[0])
        t_right = float(t_ds[-1])

        for i in range(n_vis):
            # real µV on this visible segment (downsampled)
            ch_uv = seg_ds[i]
            mn = float(np.min(ch_uv))
            mx = float(np.max(ch_uv))

            # display y position: use the trace center line (i * spacing)
            y_center = i * self._spacing

            left_txt = pg.TextItem(text=f"{mn:.0f} µV", anchor=(0, 0.5))
            right_txt = pg.TextItem(text=f"{mx:.0f} µV", anchor=(1, 0.5))

            left_txt.setPos(t_left, y_center)
            right_txt.setPos(t_right, y_center)

            self.signal_plot.addItem(left_txt)
            self.signal_plot.addItem(right_txt)
            self._minmax_items += [left_txt, right_txt]

        # Re-highlight
        if self._selected_abs is not None:
            self.highlight_channel(self._selected_abs)
        



    def _nice_time_step(self, window_s: float, target_lines: int = 10) -> float:
        raw = max(window_s / max(1, target_lines), 1e-6)
        exp = np.floor(np.log10(raw))
        base = raw / (10 ** exp)
        if base <= 1:
            nice = 1
        elif base <= 2:
            nice = 2
        elif base <= 5:
            nice = 5
        else:
            nice = 10
        return nice * (10 ** exp)