from __future__ import annotations

from typing import Optional, List, cast

import numpy as np
import pyqtgraph as pg
from pyqtgraph.GraphicsScene.GraphicsScene import GraphicsScene

from mne.io import BaseRaw
from PySide6.QtCore import Signal, Qt, QEvent
from PySide6 import QtCore, QtGui, QtWidgets 

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
    selectionChanged = Signal(list)
    requestOpenComputationPanel = Signal(list)  # emits selected abs indices

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

        self._selected_abs_set: set[int] = set()
        self._selection_anchor_abs: Optional[int] = None

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

        # Signal plot axes (hide until loaded)
        for ax in ("bottom", "left", "right", "top"):
            self.signal_plot.hideAxis(ax)

        # Disable default right-click menus (keep it simple)
        self.label_plot.setMenuEnabled(False)
        self.signal_plot.setMenuEnabled(False)

        # ViewBoxes 
        self._label_vb = cast(pg.ViewBox, self.label_plot.getViewBox())
        self._sig_vb = cast(pg.ViewBox, self.signal_plot.getViewBox())

        # Channel 0 at top
        self._label_vb.invertY(True)
        self._sig_vb.invertY(True)

        # control scrolling/zoom ourselves
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

        # Wheel handling via eventFilter (avoid pyqtgraph defaults)
        self.viewport().installEventFilter(self)
        self.installEventFilter(self)

        # Track RMB state for "RMB + wheel" zoom (more reliable than ev.buttons())
        self._rmb_down: bool = False

        # per-dataset display annotations
        self._hidden_channels: set[str] = set()
        self._bad_channels: set[str] = set()

        # row->channel mapping for the last render (needed for right-click)
        self._last_visible_ch_indices: list[int] = []

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

        # Reset per-dataset annotations (do NOT carry across datasets)
        self._hidden_channels.clear()
        self._bad_channels.clear()
        self._last_visible_abs = []

        # Show axis + grid now that we have data
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
            self._clamp_ch_start()

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
        
        all_vis = self._all_visible_abs_indices()
        n_channels = len(all_vis)
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

        all_vis = self._all_visible_abs_indices()
        if index not in all_vis:
            return  # hidden

        pos = all_vis.index(index)
        n_vis = int(min(self._chan_range, len(all_vis)))

        if pos < self._ch_start:
            self.set_channel_start(pos)
        elif pos >= self._ch_start + n_vis:
            self.set_channel_start(pos - n_vis + 1)

    def highlight_selected_channels(self):
        """Highlight selected channels (thicker trace + yellow label)."""
        if self._visible_abs.size == 0:
            return

        for row, c in enumerate(self._curves):
            abs_idx = int(self._visible_abs[row])
            ch_name = self._channel_names[abs_idx]

            is_bad = ch_name in self._bad_channels
            is_selected = abs_idx in self._selected_abs_set

            width = 3 if is_selected else 1
            color = "r" if is_bad else "w"
            c.setPen(pg.mkPen(color, width=width))

        for row, txt in enumerate(self._labels):
            abs_idx = int(self._visible_abs[row])
            txt.setColor((255, 255, 0) if abs_idx in self._selected_abs_set else (180, 180, 180))

    def set_selected_abs(self, selected_abs: list[int], *, anchor: int | None = None, emit: bool = True):
        self._selected_abs_set = set(int(i) for i in selected_abs)
        if anchor is not None:
            self._selection_anchor_abs = int(anchor)

        self.highlight_selected_channels()
        if emit and hasattr(self, "selectionChanged"):
            self.selectionChanged.emit(sorted(self._selected_abs_set))

    # ---------------- Interaction ----------------

    def _wheel_dy(self, ev) -> int:
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

            step_dir = -1 if dy > 0 else +1  # wheel up => -1
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

                    ev.ignore()
                    return True

            return super().eventFilter(obj, ev)

    def _on_mouse_clicked(self, event):
        """Left click selects channel. Right click on signal opens menu."""
        if self._raw is None or self._visible_abs.size == 0:
            return

        pos = event.scenePos()
        in_label = self._label_vb.sceneBoundingRect().contains(pos)
        in_signal = self._sig_vb.sceneBoundingRect().contains(pos)

        # ---- Right click: context menu only in signal area ----
        if event.button() == Qt.MouseButton.RightButton and in_signal:
            data_point = self._sig_vb.mapSceneToView(pos)
            y = float(data_point.y())
            row = int(y // float(self._spacing))
            if row < 0 or row >= len(self._last_visible_abs):
                return

            abs_idx = int(self._last_visible_abs[row])

            # If you right-click a channel that isn't selected, select only that one first
            if abs_idx not in self._selected_abs_set:
                self._selected_abs_set = {abs_idx}
                self._selection_anchor_abs = abs_idx
                self.highlight_selected_channels()
                # optional: self.selectionChanged.emit(sorted(self._selected_abs_set))

            selected_abs = sorted(self._selected_abs_set)
            selected_names = [self._channel_names[i] for i in selected_abs]

            menu = QtWidgets.QMenu()
            act_open_panel = menu.addAction("Open Computation Panel")
            menu.addSeparator()

            act_hide = menu.addAction(f"Hide ({len(selected_names)})")

            # If ALL are bad => offer Unmark, else offer Mark (sets all to bad)
            all_bad = all(name in self._bad_channels for name in selected_names)
            act_bad = menu.addAction("Unmark as bad" if all_bad else "Mark as bad")

            chosen = menu.exec_(QtGui.QCursor.pos())
            if chosen == act_open_panel:
                self.requestOpenComputationPanel.emit(selected_abs)
            elif chosen == act_hide:
                self._hide_channels(selected_names)
            elif chosen == act_bad:
                self._set_bad_channels(selected_names, bad=not all_bad)

            return

        # ---- Left click: selection ----
        if event.button() != Qt.MouseButton.LeftButton:
            return

        vb = self._label_vb if in_label else self._sig_vb
        data_point = vb.mapSceneToView(pos)
        y = float(data_point.y())

        centers = np.arange(len(self._visible_abs)) * self._spacing
        idx_vis = int(np.argmin(np.abs(centers - y)))
        idx_vis = max(0, min(idx_vis, len(self._visible_abs) - 1))

        idx_abs = int(self._visible_abs[idx_vis])

        mods = QtWidgets.QApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Visible list excluding hidden channels (needed for shift-range)
        all_vis = self._all_visible_abs_indices()

        if not ctrl and not shift:
            # Normal click => single selection
            self._selected_abs_set = {idx_abs}
            self._selection_anchor_abs = idx_abs

        elif ctrl and not shift:
            # Ctrl-click => toggle one
            if idx_abs in self._selected_abs_set:
                self._selected_abs_set.remove(idx_abs)
            else:
                self._selected_abs_set.add(idx_abs)

        else:
            # Shift or Ctrl+Shift => range selection
            if self._selection_anchor_abs is None:
                self._selection_anchor_abs = idx_abs

            if (idx_abs in all_vis) and (self._selection_anchor_abs in all_vis):
                i1 = all_vis.index(self._selection_anchor_abs)
                i2 = all_vis.index(idx_abs)
                lo, hi = (i1, i2) if i1 <= i2 else (i2, i1)
                range_set = set(all_vis[lo : hi + 1])

                if ctrl:
                    self._selected_abs_set |= range_set
                else:
                    self._selected_abs_set = range_set

        # Keep existing "clicked" semantic (primary)
        self.channelClicked.emit(idx_abs)

        # Tell others (computation panel later)
        self.selectionChanged.emit(sorted(self._selected_abs_set))

        # Repaint highlight
        self.highlight_selected_channels()
    # ---------------- Rendering ----------------
    def render(self):
        """Redraw the viewer for the current raw + view parameters."""
        if self._raw is None or self._picks.size == 0:
            return

        raw = self._raw
        picks = self._picks

        # Visible abs indices (displayed list) excluding hidden
        visible_abs = self._visible_window_abs_indices()
        n_vis = len(visible_abs)
        if n_vis == 0:
            return

        self._last_visible_abs = visible_abs
        self._visible_abs = np.asarray(visible_abs, dtype=int)

        seg_ds_uv, t_ds = self._get_visible_data_for_abs(raw, picks, visible_abs)
        if seg_ds_uv is None or t_ds is None:
            return

        self._clear_plots()
        self._draw_traces(seg_ds_uv, t_ds, visible_abs)
        self._draw_labels(n_vis)

        self._set_ranges(t_ds, n_vis)
        self._draw_time_lines(t_ds)
        self._draw_cursor(t_ds)
        self._draw_minmax(seg_ds_uv, t_ds, n_vis)

        if self._selected_abs_set:
            self.highlight_selected_channels()

# ---------------- Data fetch ----------------
    def _get_visible_data_for_abs(
        self,
        raw: BaseRaw,
        picks: np.ndarray,
        visible_abs: list[int],
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        start_samp = int(self._t_start * self._fs)
        end_samp = int((self._t_start + self._time_range) * self._fs)

        n_samples = int(raw.n_times)
        start_samp = max(0, min(start_samp, n_samples - 1))
        end_samp = max(start_samp + 1, min(end_samp, n_samples))

        raw_ch_picks = picks[np.asarray(visible_abs, dtype=int)]
        data_v, times = raw[raw_ch_picks, start_samp:end_samp]

        data_v = np.asarray(data_v)
        times = np.asarray(times)

        if times.ndim != 1 or times.size < 2 or data_v.shape[1] < 2:
            return None, None

        data_uv = data_v * 1e6

        max_points = 3000
        win_len = int(data_uv.shape[1])
        step = max(1, win_len // max_points)

        t_ds = times[::step]
        seg_ds_uv = data_uv[:, ::step]
        return seg_ds_uv, t_ds
    # ---------------- Render helpers ----------------
    def _clear_plots(self):
        self.signal_plot.clear()
        self.label_plot.clear()

        self._curves = []
        self._labels = []
        self._time_lines = []
        self._cursor_line = None
        self._minmax_items = []
    
    def _draw_traces(self, seg_ds_uv: np.ndarray, t_ds: np.ndarray, visible_abs: list[int]):
        correction_factor = 0.01
        gain_factor = 1.0 / max(1e-9, (self._gain_uv * correction_factor))

        n_vis = min(seg_ds_uv.shape[0], len(visible_abs))
        for row in range(n_vis):
            abs_idx = visible_abs[row]
            ch_name = self._channel_names[abs_idx]
            pen = pg.mkPen("r", width=1.5) if (ch_name in self._bad_channels) else pg.mkPen("w", width=1)

            y = (seg_ds_uv[row] * gain_factor) + row * self._spacing
            curve = self.signal_plot.plot(t_ds, y, pen=pen)
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
        xpad = 0.06 * max(1e-9, (t1 - t0))
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
            ln.setZValue(-10)
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
        margin = getattr(self, "_amp_left_margin", 0.08 * width)
        x_left = t0 - 0.5 * margin

        for i in range(n_vis):
            y_center = i * self._spacing
            txt = pg.TextItem(
                text=f"±{self._gain_uv:.0f} µV",
                anchor=(0.5, 0.5),
                color=(160, 160, 160),
            )
            txt.setPos(x_left, y_center)
            self.signal_plot.addItem(txt)
            self._minmax_items.append(txt)

 # ---------------- Hide / Bad ----------------
    def _hide_channel(self, ch_name: str):
        self._hidden_channels.add(ch_name)

        # if we just hid the selected channel, clear selection
        if self._selected_abs is not None:
            sel_name = self._channel_names[int(self._selected_abs)]
            if sel_name == ch_name:
                self._selected_abs = None
                
        self._clamp_ch_start()
        self.render()

    def unhide_channel(self, ch_name: str):
        self._hidden_channels.discard(ch_name)
        self._clamp_ch_start()
        self.render()

    def unhide_all_channels(self):
        self._hidden_channels.clear()
        self._clamp_ch_start()
        self.render()

    def _toggle_bad_channel(self, ch_name: str):
        if ch_name in self._bad_channels:
            self._bad_channels.remove(ch_name)
        else:
            self._bad_channels.add(ch_name)
        self.render()

    def _hide_channels(self, ch_names: list[str]):
        if not ch_names:
            return
        self._hidden_channels.update(ch_names)
        self._clamp_ch_start()
        self.render()

    def _set_bad_channels(self, ch_names: list[str], bad: bool):
        if not ch_names:
            return
        if bad:
            self._bad_channels.update(ch_names)
        else:
            for n in ch_names:
                self._bad_channels.discard(n)
        self.render()

    # ---------------- Visible channel logic (DISPLAYED indices) ----------------
    def _all_visible_abs_indices(self) -> list[int]:
        if self._raw is None:
            return []
        return [i for i, name in enumerate(self._channel_names) if name not in self._hidden_channels]

    def _visible_window_abs_indices(self) -> list[int]:
        all_vis = self._all_visible_abs_indices()
        count = int(self._chan_range)

        max_start = max(0, len(all_vis) - count)
        self._ch_start = max(0, min(int(self._ch_start), max_start))

        return all_vis[self._ch_start : self._ch_start + count]

    def _clamp_ch_start(self):
        all_vis = self._all_visible_abs_indices()
        count = int(self._chan_range)
        max_start = max(0, len(all_vis) - count)
        self._ch_start = max(0, min(int(self._ch_start), max_start))

# ---------------- Utility ----------------
    def _clamp_time_start(self):
        if self._raw is None or self._raw.n_times <= 1:
            self._t_start = 0.0
            return

        total_s = float(self._raw.times[-1])
        max_t0 = max(0.0, total_s - float(self._time_range))
        self._t_start = float(np.clip(self._t_start, 0.0, max_t0))

    @staticmethod
    def _nice_time_step(window_s: float, target_lines: int = 10) -> float:
        # Keep your existing implementation below this in your file.
        # (I’m not rewriting it because it was truncated in your paste.)
        # Return something sensible if not implemented yet:
        if window_s <= 0:
            return 1.0
        raw = window_s / max(1, target_lines)
        # snap to 1/2/5 * 10^k
        p = 10 ** np.floor(np.log10(raw))
        m = raw / p
        if m <= 1:
            s = 1
        elif m <= 2:
            s = 2
        elif m <= 5:
            s = 5
        else:
            s = 10
        return float(s * p)

