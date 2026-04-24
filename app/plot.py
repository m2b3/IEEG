from __future__ import annotations

from typing import Optional, List, cast, Any

import numpy as np
import pyqtgraph as pg
from pyqtgraph.GraphicsScene.GraphicsScene import GraphicsScene

from mne.io import BaseRaw
from PySide6.QtCore import Signal, Qt, QEvent
from PySide6 import QtCore, QtGui, QtWidgets 
from app.annotations import (
    Annotation, new_id,
    ANNOTATION_STYLES,
    SCOPE_CLICKED, SCOPE_SELECTED, SCOPE_GLOBAL,
    ANNOTATION_TYPES,
)
from app.referencing import BipolarMontage, BipolarPair

class AnnotationRect(QtWidgets.QGraphicsRectItem):
    def __init__(self, viewer: "MultiChannelViewer", anno_id: str, *args):
        super().__init__(*args)
        self._viewer = viewer
        self._anno_id = str(anno_id)
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.RightButton:
            return super().mousePressEvent(event)

        menu = QtWidgets.QMenu()
        act_edit = menu.addAction("Edit annotation...")
        act_del = menu.addAction("Delete annotation")

        chosen = menu.exec_(event.screenPos())
        if chosen == act_edit:
            # MainWindow is already connected to this signal :contentReference[oaicite:4]{index=4}
            self._viewer.requestEditAnnotation.emit(self._anno_id)
        elif chosen == act_del:
            self._viewer.delete_annotation(self._anno_id)

        event.accept()

    def contextMenuEvent(self, event):
        menu = QtWidgets.QMenu()
        act_edit = menu.addAction("Edit annotation...")
        act_del = menu.addAction("Delete annotation")

        chosen = menu.exec_(event.screenPos())
        if chosen == act_edit:
            self._viewer.requestEditAnnotation.emit(self._anno_id)
        elif chosen == act_del:
            self._viewer.delete_annotation(self._anno_id)

        event.accept()

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
    cursorMoved = Signal(float)  # cursor x in seconds

    # Wheel zoom requests (handled by MainWindow via spinboxes)
    requestTimeRangeDelta = Signal(int)   # +1 zoom out, -1 zoom in
    requestChanRangeDelta = Signal(int)   # +1 show more channels, -1 show fewer
    # Annotations
    annotationsChanged = Signal()
    requestEditAnnotation = Signal(str)  # anno_id
    annotationSelected = QtCore.Signal(str)  # emitted when user clicks an annotation in the plot
    requestOpenAnnotationsPanel = Signal()
    #  Saving data
    hiddenChannelsChanged = Signal()
    badChannelsChanged = Signal()
    #  Zoom window
    zoomStateChanged = Signal(bool)  # True when a zoom base view exists
    scalogramRequested = Signal(int, float, float)  # abs channel, start_s, stop_s
    scalogramModeChanged = Signal(bool)

    # ---------------- Init ----------------
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        # ---- Raw/state ----
        self._raw: Optional[BaseRaw] = None
        self._fs: float = 1.0
        self._picks: np.ndarray = np.array([], dtype=int)
        self._channel_names: List[str] = []

        self._reference_mode: str = "monopolar"
        self._bipolar_montage: BipolarMontage | None = None
        self._display_names: List[str] = []
        self._monopolar_abs_to_pick_idx: list[int] = []
        self._bipolar_pairs: list[BipolarPair] = []

        # ---- View params ----
        self._t_start: float = 0.0
        self._time_range: float = 3.0
        self._chan_range: int = 32
        self._gain_uv: float = 100.0
        self._ch_start: int = 0

        # Vertical stacking
        self._spacing: float = 200.0  # y spacing in "display units"
        self._vertical_margin_factor: float = 0.75

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
        self._context_menu_active = False

        # ---- Layout: label plot (left) + signal plot (right) ----
        self.label_plot = pg.PlotItem()
        self.signal_plot = pg.PlotItem()
        self.addItem(self.label_plot, 0, 0)
        self.addItem(self.signal_plot, 0, 1)

        self.label_plot.setMaximumWidth(100)   # try 140-200
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

        # colors of micro channel
        self._channel_groups: dict[str, str] = {}
        self._micro_trace_color = (79, 195, 247)   # cyan / light blue
        self._micro_label_color = (79, 195, 247)
        self._macro_trace_color = (255, 255, 255)
        self._macro_label_color = (180, 180, 180)

        # row->channel mapping for the last render (needed for right-click)
        self._last_visible_ch_indices: list[int] = []

        # ---- Annotations (persistent) ----
        self._annotations: list[Annotation] = []
        self._anno_rois: dict[str, pg.RectROI] = {}
        self._anno_labels: dict[str, pg.TextItem] = {}
        self._annotation_items: list[object] = []

        # Annotation mode (armed by MainWindow)
        self._annotation_mode: bool = False
        self._pending_kind: str | None = None
        self._pending_note: str = ""
        self._pending_scope: str = SCOPE_CLICKED

        # Drag state for creation
        self._anno_drag_active: bool = False
        self._anno_drag_start_t: float | None = None
        self._anno_drag_y: float | None = None
        self._anno_preview: pg.RectROI | None = None

        #  Montage 
        self._cached_bipolar_data_key = None
        self._cached_bipolar_data = None
        self._common_ref_name: str | None = None

        # ---- Zoom selection ----
        self._zoom_mode: bool = False
        self._zoom_base_view: dict[str, float | int] | None = None
        self._zoom_history: list[dict[str, float | int]] = []

        self._zoom_drag_active: bool = False
        self._zoom_drag_start_t: float | None = None
        self._zoom_drag_start_y: float | None = None
        self._zoom_preview: QtWidgets.QGraphicsRectItem | None = None

        # ---- Scalogram selection ----
        self._scalogram_mode: bool = False
        self._scalogram_drag_active: bool = False
        self._scalogram_drag_start_t: float | None = None
        self._scalogram_drag_abs: int | None = None
        self._scalogram_preview: QtWidgets.QGraphicsRectItem | None = None
        self._min_scalogram_duration_s: float = 0.05

    def clear(self) -> None:
        """Reset viewer to an empty state."""
        self._raw = None
        self._fs = 1.0
        self._picks = np.array([], dtype=int)
        self._channel_names = []

        self._reference_mode = "monopolar"
        self._bipolar_montage = None
        self._display_names = []
        self._monopolar_abs_to_pick_idx = []
        self._bipolar_pairs = []

        self._common_ref_name = None

        self._zoom_mode = False
        self._zoom_base_view = None
        self._zoom_history = []
        self._zoom_drag_active = False
        self._zoom_drag_start_t = None
        self._zoom_drag_start_y = None
        self.stop_scalogram_selection_mode()

        self._t_start = 0.0
        self._ch_start = 0
        self._visible_abs = np.array([], dtype=int)
        self._last_visible_abs = []
        self._last_visible_ch_indices = []

        self._selected_abs_set.clear()
        self._selection_anchor_abs = None

        self._hidden_channels.clear()
        self._bad_channels.clear()

        self._channel_groups.clear()

        self._annotations.clear()
        self._clear_annotation_items()

        for roi in self._anno_rois.values():
            try:
                self.signal_plot.removeItem(roi)
            except Exception as e:
                import sys
                print(f"Warning: Failed to remove annotation ROI: {e}", file=sys.stderr)
        self._anno_rois.clear()

        for txt in self._anno_labels.values():
            try:
                self.signal_plot.removeItem(txt)
            except Exception as e:
                import sys
                print(f"Warning: Failed to remove annotation label: {e}", file=sys.stderr)
        self._anno_labels.clear()

        self.stop_annotation_mode()
        self._clear_plots()
        self._clear_bipolar_cache()
        self._common_ref_name = None

        self.zoomStateChanged.emit(False)

        # Keep bottom axis available so the plot comes back cleanly on next load
        self.signal_plot.showAxis("bottom")
        self.signal_plot.setLabel("bottom", "")

        self.annotationsChanged.emit()
        self.hiddenChannelsChanged.emit()
        self.badChannelsChanged.emit()
        self.repaint()
        
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
        self._channel_groups = {str(ch): "macro" for ch in self._channel_names}
        
        self._reference_mode = "monopolar"
        self._bipolar_montage = None
        self._display_names = list(self._channel_names)
        self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))
        self._bipolar_pairs = []

        # Reset view
        self._t_start = 0.0
        self._ch_start = 0
        self._selected_abs_set.clear()   

        # Reset per-dataset annotations (do NOT carry across datasets)
        self._hidden_channels.clear()
        self._bad_channels.clear()
        self._last_visible_abs = []

        self._annotations.clear()
        self._clear_annotation_items()
        self.annotationsChanged.emit()

        self._zoom_mode = False
        self._zoom_base_view = None
        self._zoom_history = []
        self._zoom_drag_active = False
        self._zoom_drag_start_t = None
        self._zoom_drag_start_y = None
        self.zoomStateChanged.emit(False)
        self.stop_scalogram_selection_mode()

        # Show axis + grid now that we have data
        self.signal_plot.showAxis("bottom")
        self.signal_plot.setLabel("bottom", "Time (s)")
        self.signal_plot.showGrid(x=True, y=True, alpha=0.15)
        self.signal_plot.update()

        self.render()
        self.channelWindowChanged.emit(self._ch_start)
        self._clear_bipolar_cache()

    # ---------------- View state ----------------

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

    def time_start(self) -> float:
        return float(self._t_start)

    def channel_start(self) -> int:
        return int(self._ch_start)

    def cursor_x(self) -> float:
        return float(self._cursor_x) if self._cursor_x is not None else float(self._t_start)
 
    def reference_mode(self) -> str:
        return str(self._reference_mode)

    def common_reference_name(self) -> str | None:
        return self._common_ref_name


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
        self._draw_annotations(n_vis)

        self._set_ranges(t_ds, n_vis)
        self._draw_time_lines(t_ds)
        self._draw_cursor(t_ds)
        self._draw_minmax(seg_ds_uv, t_ds, n_vis)

        if self._selected_abs_set:
            self.highlight_selected_channels()

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
            group = self.get_channel_group(ch_name)

            if ch_name in self._bad_channels:
                pen = pg.mkPen("r", width=1.5)
            elif group == "micro":
                pen = pg.mkPen(self._micro_trace_color, width=1)
            else:
                pen = pg.mkPen(self._macro_trace_color, width=1)

            plot_row = (n_vis - 1 - row)
            y = (seg_ds_uv[row] * gain_factor) + plot_row * self._spacing

            curve = self.signal_plot.plot(t_ds, y, pen=pen)
            self._curves.append(curve)
    
    def _draw_labels(self, n_vis: int) -> None:
        self._labels = []

        for row in range(n_vis):
            abs_idx = int(self._visible_abs[row])
            ch_name = self.get_channel_names()[abs_idx]

            plot_row = (n_vis - 1 - row)
            y = plot_row * float(self._spacing)

            group = self.get_channel_group(ch_name)
            label_color = self._micro_label_color if group == "micro" else self._macro_label_color

            txt = pg.TextItem(
                text=ch_name,
                anchor=(1.0, 0.5),
                color=label_color,
            )
            
            txt.setPos(98.0, y)
            self.label_plot.addItem(txt)
            self._labels.append(txt)

    def _draw_annotations(self, n_vis: int) -> None:
        """Draw annotation overlays on the signal plot."""
        self._clear_annotation_items()

        if not self._annotations:
            return

        h = 0.9 * float(self._spacing)
        visible_abs = list(self._last_visible_abs)
        abs_to_row = {a: i for i, a in enumerate(visible_abs)}

        for a in self._annotations:
            kind = a.kind
            t0 = float(a.t_start)
            t1 = float(a.t_end)
            abs_ch = a.abs_channel
            note = a.note

            rgb = ANNOTATION_STYLES.get(kind, (0, 200, 0))
            brush = pg.mkBrush(rgb[0], rgb[1], rgb[2], 60)

            # Global annotation
            if abs_ch is None:
                y0 = -0.5 * self._spacing
                height = (n_vis - 1) * self._spacing + self._spacing

            else:
                if abs_ch not in abs_to_row:
                    continue
                data_row = abs_to_row[int(abs_ch)]
                plot_row = (n_vis - 1 - data_row)
                yc = plot_row * float(self._spacing)
                y0 = yc - h / 2.0
                height = h

            rect = AnnotationRect(
                self,
                a.id,
                t0,
                y0,
                max(1e-6, t1 - t0),
                height,
            )
            # keep your styling
            rect.setBrush(brush)
            rect.setPen(pg.mkPen(None))
            rect.setZValue(-5)

            self.signal_plot.addItem(rect)
            self._annotation_items.append(rect)
                            
    def _set_ranges(self, t_ds: np.ndarray, n_vis: int):
        ypad = float(self._vertical_margin_factor) * float(self._spacing)
        y0 = -ypad
        y1 = (n_vis - 1) * float(self._spacing) + ypad

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

    def _draw_minmax(self, seg_ds_uv: np.ndarray, t_ds: np.ndarray, n_vis: int):
        t0 = float(t_ds[0])
        t1 = float(t_ds[-1])
        width = max(1e-9, (t1 - t0))
        margin = getattr(self, "_amp_left_margin", 0.08 * width)
        x_left = t0 - 0.5 * margin

        for i in range(n_vis):
            plot_row = (n_vis - 1 - i)
            y_center = plot_row * self._spacing
            txt = pg.TextItem(
                text=f"+/-{self._gain_uv:.0f} uV",
                anchor=(0.5, 0.5),
                color=(160, 160, 160),
            )
            txt.setPos(x_left, y_center)
            self.signal_plot.addItem(txt)
            self._minmax_items.append(txt)

    def set_channel_groups(self, groups: dict[str, str] | None) -> None:
        self._channel_groups = {}
        if isinstance(groups, dict):
            for ch_name, group in groups.items():
                g = str(group).lower()
                if g in {"macro", "micro"}:
                    self._channel_groups[str(ch_name)] = g
        self.render()

    def get_channel_group(self, ch_name: str) -> str:
        ch_name = str(ch_name)

        direct = self._channel_groups.get(ch_name)
        if direct in {"macro", "micro"}:
            return direct

        if self._reference_mode == "bipolar":
            for pair in self._bipolar_pairs:
                if pair.name != ch_name:
                    continue

                ch1_group = self._channel_groups.get(str(pair.ch1), "macro")
                ch2_group = self._channel_groups.get(str(pair.ch2), "macro")
                if ch1_group == "micro" and ch2_group == "micro":
                    return "micro"
                return "macro"

        return "macro"
    
    # ---------------- Visible data & window helpers ---------------
   
    def _visible_window_abs_indices(self) -> list[int]:
        all_vis = self._all_visible_abs_indices()
        count = int(self._chan_range)

        max_start = max(0, len(all_vis) - count)
        self._ch_start = max(0, min(int(self._ch_start), max_start))

        return all_vis[self._ch_start : self._ch_start + count]
      
    def _all_visible_abs_indices(self) -> list[int]:
        names = self.get_channel_names()
        visible: list[int] = []

        for abs_idx, ch_name in enumerate(names):
            raw_name = self._channel_names[abs_idx] if abs_idx < len(self._channel_names) else ch_name

            # Hidden channels disappear from the main viewer
            if raw_name in self._hidden_channels:
                continue

            # BAD channels stay visible; they are only recolored/highlighted
            visible.append(abs_idx)

        return visible

    def _all_nonbad_abs_indices(self) -> list[int]:
        names = self.get_channel_names()
        usable = []

        for abs_idx, ch_name in enumerate(names):
            raw_name = self._channel_names[abs_idx] if abs_idx < len(self._channel_names) else ch_name

            if raw_name in self._bad_channels:
                continue

            usable.append(abs_idx)

        return usable

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

        if self._reference_mode == "monopolar":
            raw_ch_picks = picks[np.asarray(visible_abs, dtype=int)]
            data_v, times = raw[raw_ch_picks, start_samp:end_samp]

            data_v = np.asarray(data_v, dtype=float)
            times = np.asarray(times, dtype=float)


        elif self._reference_mode in ("average", "median"):
            # Reference pool: all non-bad channels, even if some are hidden
            ref_abs = self._all_nonbad_abs_indices()
            if not ref_abs:
                return None, None

            ref_raw_picks = picks[np.asarray(ref_abs, dtype=int)]
            ref_data, times = raw[ref_raw_picks, start_samp:end_samp]
            ref_data = np.asarray(ref_data, dtype=float)
            times = np.asarray(times, dtype=float)

            vis_raw_picks = picks[np.asarray(visible_abs, dtype=int)]
            data_v, _ = raw[vis_raw_picks, start_samp:end_samp]
            data_v = np.asarray(data_v, dtype=float)

            # Exclude manually annotated bad segments from the reference computation
            bad_mask = self._build_bad_segment_mask_for_abs(ref_abs, times)
            if bad_mask.shape == ref_data.shape:
                ref_data = ref_data.copy()
                ref_data[bad_mask] = np.nan

            if self._reference_mode == "average":
                ref = np.nanmean(ref_data, axis=0, keepdims=True)
            else:
                ref = np.nanmedian(ref_data, axis=0, keepdims=True)

            # fallback for samples where everything got masked
            ref = np.where(np.isnan(ref), 0.0, ref)

            data_v = data_v - ref

        
        elif self._reference_mode == "common":
            if not self._common_ref_name:
                return None, None

            if self._common_ref_name not in self._channel_names:
                return None, None

            ref_abs = self._channel_names.index(self._common_ref_name)
            ref_raw_pick = int(picks[ref_abs])

            vis_raw_picks = picks[np.asarray(visible_abs, dtype=int)]
            data_v, times = raw[vis_raw_picks, start_samp:end_samp]
            data_v = np.asarray(data_v, dtype=float)
            times = np.asarray(times, dtype=float)

            ref_data, _ = raw[[ref_raw_pick], start_samp:end_samp]
            ref_data = np.asarray(ref_data, dtype=float)

            if ref_data.ndim != 2 or ref_data.shape[0] == 0:
                return None, None

            data_v = data_v - ref_data[0:1, :]

        elif self._reference_mode == "bipolar":
            if self._bipolar_montage is None or not self._bipolar_montage.pairs:
                return None, None

            full_data = self._get_or_compute_bipolar_data()
            if full_data is None:
                return None, None

            valid_visible_abs = [
                abs_idx for abs_idx in visible_abs
                if 0 <= abs_idx < full_data.shape[0]
            ]
            if not valid_visible_abs:
                return None, None

            data_v = full_data[np.asarray(valid_visible_abs, dtype=int), start_samp:end_samp]
            times = np.asarray(raw.times[start_samp:end_samp], dtype=float)

        else:
            return None, None

        if data_v.ndim != 2 or times.ndim != 1:
            return None, None

        if times.size < 2 or data_v.shape[1] < 2:
            return None, None

        data_uv = data_v * 1e6

        max_points = 3000
        win_len = int(data_uv.shape[1])
        step = max(1, win_len // max_points)

        t_ds = times[::step]
        seg_ds_uv = data_uv[:, ::step]
        return seg_ds_uv, t_ds

    def _clamp_time_start(self):
        if self._raw is None or self._raw.n_times <= 1:
            self._t_start = 0.0
            return

        total_s = float(self._raw.times[-1])
        max_t0 = max(0.0, total_s - float(self._time_range))
        self._t_start = float(np.clip(self._t_start, 0.0, max_t0))

    def _clamp_ch_start(self):
        all_vis = self._all_visible_abs_indices()
        count = int(self._chan_range)
        max_start = max(0, len(all_vis) - count)
        self._ch_start = max(0, min(int(self._ch_start), max_start))

    def replace_raw_preserve_view(self, raw: BaseRaw, picks: Optional[np.ndarray] = None):
        """
        Replace the backing raw object while preserving current viewer state.
        Use this when the logical dataset is the same (e.g. filters applied),
        not when loading a completely new file.
        """
        self._raw = raw
        self._fs = float(raw.info["sfreq"])

        if picks is None:
            self._picks = np.arange(raw.info["nchan"], dtype=int)
        else:
            self._picks = np.asarray(picks, dtype=int)

        self._channel_names = [raw.ch_names[i] for i in self._picks.tolist()]

        # keep reference mode / montage / selections / zoom / hidden / bad / annotations
        # but make display names consistent with current reference mode
        if self._reference_mode in ("monopolar", "average", "median", "common"):
            self._display_names = list(self._channel_names)
            self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))

        self._clamp_time_start()
        self._clamp_ch_start()
        self._clear_bipolar_cache()
        self.render()
        self.channelWindowChanged.emit(self._ch_start)
        self.timeWindowChanged.emit(self._t_start)

    def _select_single_channel_abs(self, abs_idx: int, *, emit: bool = True) -> None:
        self._selected_abs_set = {int(abs_idx)}
        self._selection_anchor_abs = int(abs_idx)
        self.highlight_selected_channels()
        if emit:
            self.selectionChanged.emit(sorted(self._selected_abs_set))

    def _selected_channel_names(self) -> list[str]:
        return [self._channel_names[i] for i in sorted(self._selected_abs_set)]

    def _show_context_menu_for_scene_pos(self, scene_pos) -> bool:
        if self._raw is None or self._visible_abs.size == 0:
            return False

        if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
            return False

        data_point = self._sig_vb.mapSceneToView(scene_pos)
        y = float(data_point.y())
        abs_idx = self._abs_channel_from_y(y)
        if abs_idx is None:
            return False

        if abs_idx not in self._selected_abs_set:
            self._select_single_channel_abs(abs_idx)

        selected_abs = sorted(self._selected_abs_set)
        selected_names = [self._channel_names[i] for i in selected_abs]

        menu = QtWidgets.QMenu()
        act_open_panel = menu.addAction("Open Computation Panel")
        act_open_annos = menu.addAction("Open Annotations Panel")
        menu.addSeparator()
        act_hide = menu.addAction(f"Hide ({len(selected_names)})")

        all_bad = all(name in self._bad_channels for name in selected_names)
        act_bad = menu.addAction("Unmark as bad" if all_bad else "Mark as bad")

        chosen = menu.exec_(QtGui.QCursor.pos())
        if chosen == act_open_panel:
            self.requestOpenComputationPanel.emit(selected_abs)
        elif chosen == act_open_annos:
            self.requestOpenAnnotationsPanel.emit()
        elif chosen == act_hide:
            self._hide_channels(selected_names)
        elif chosen == act_bad:
            self._set_bad_channels(selected_names, bad=not all_bad)

        return True

    def _scene_pos_hits_annotation(self, scene_pos) -> bool:
        for item in self.scene().items(scene_pos):
            if isinstance(item, (AnnotationRect, _AnnotationROI)):
                return True
        return False


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

    # ---------------- Channel naming & display ----------------
    def get_channel_names(self) -> list[str]:
        return list(self._display_names if self._display_names else self._channel_names)
    
    def get_display_channel_names(self) -> list[str]:
        return list(self._display_names)

    def get_raw_channel_names(self) -> list[str]:
        return list(self._channel_names)

    def _display_name_for_abs(self, abs_idx: int) -> str:
        names = self.get_channel_names()
        if 0 <= int(abs_idx) < len(names):
            return str(names[int(abs_idx)])
        return str(abs_idx)

    # ---------------- Channel selection & Positioning----------------

    def set_selected_abs(self, selected_abs: list[int], *, anchor: int | None = None, emit: bool = True):
        self._selected_abs_set = set(int(i) for i in selected_abs)
        if anchor is not None:
            self._selection_anchor_abs = int(anchor)

        self.highlight_selected_channels()
        if emit and hasattr(self, "selectionChanged"):
            self.selectionChanged.emit(sorted(self._selected_abs_set))

    def highlight_selected_channels(self):
        """Highlight selected channels (thicker trace + yellow label)."""
        if self._visible_abs.size == 0:
            return

        n = int(min(len(self._curves), int(self._visible_abs.size)))
        for row in range(n):
            c = self._curves[row]
            abs_idx = int(self._visible_abs[row])
            raw_name = self._channel_names[abs_idx]

            is_bad = raw_name in self._bad_channels
            is_selected = abs_idx in self._selected_abs_set

            group = self.get_channel_group(raw_name)
            width = 3 if is_selected else 1

            if is_bad:
                color = "r"
            elif group == "micro":
                color = self._micro_trace_color
            else:
                color = self._macro_trace_color

            c.setPen(pg.mkPen(color, width=width))

        n_lbl = int(min(len(self._labels), int(self._visible_abs.size)))
        for row in range(n_lbl):
            abs_idx = int(self._visible_abs[row])
            ch_name = self._channel_names[abs_idx]
            group = self.get_channel_group(ch_name)

            if abs_idx in self._selected_abs_set:
                self._labels[row].setColor((255, 255, 0))
            else:
                base_color = (
                    self._micro_label_color
                    if group == "micro"
                    else self._macro_label_color
                )
                self._labels[row].setColor(base_color)

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

    def center_channel_on(self, abs_idx: int) -> None:
        """
        Scroll channel window so abs_idx is roughly centered vertically.
        abs_idx is the index in the displayed channel list (same indexing your annotations use).
        """
        if self._raw is None or self._picks.size == 0:
            return

        all_vis = self._all_visible_abs_indices()
        if abs_idx not in all_vis:
            return  # hidden or invalid

        pos = all_vis.index(int(abs_idx))
        n_vis = int(min(self._chan_range, len(all_vis)))
        new_start = pos - (n_vis // 2)
        self.set_channel_start(new_start)

    def _abs_channel_from_y(self, y: float) -> int | None:
        n_vis = len(getattr(self, "_last_visible_abs", []))
        if n_vis <= 0:
            return None

        plot_row = int(y // float(self._spacing))
        if plot_row < 0 or plot_row >= n_vis:
            return None

        data_row = (n_vis - 1 - plot_row)
        return int(self._last_visible_abs[data_row])

    def _row_center_y_for_abs(self, abs_idx: int) -> float | None:
        visible_abs = list(getattr(self, "_last_visible_abs", []))
        if abs_idx not in visible_abs:
            return None
        n_vis = len(visible_abs)
        data_row = visible_abs.index(abs_idx)
        plot_row = (n_vis - 1 - data_row)
        return float(plot_row) * float(self._spacing)

 # ---------------- Hiden / Bad Channel----------------
    def _hide_channel(self, ch_name: str) -> None:
        self._hidden_channels.add(ch_name)

        # Remove hidden channel from selection (selection stored as abs indices)
        try:
            abs_idx = self._channel_names.index(ch_name)
        except ValueError:
            abs_idx = None

        if abs_idx is not None:
            self._selected_abs_set.discard(abs_idx)

        self._clamp_ch_start()
        self.render()

    def _hide_channels(self, ch_names: list[str]):
        if not ch_names:
            return
        self._hidden_channels.update(ch_names)
        self._clamp_ch_start()
        self.render()
        self.hiddenChannelsChanged.emit()

    def unhide_channel(self, ch_name: str):
        self._hidden_channels.discard(ch_name)
        self._clamp_ch_start()
        self.render()
        self.hiddenChannelsChanged.emit()

    def unhide_all_channels(self):
        self._hidden_channels.clear()
        self._clamp_ch_start()
        self.render()
        self.hiddenChannelsChanged.emit()

    def _toggle_bad_channel(self, ch_name: str):
        if ch_name in self._bad_channels:
            self._bad_channels.remove(ch_name)
        else:
            self._bad_channels.add(ch_name)
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
        self.badChannelsChanged.emit()

  
      # ---------------- Visible window calculation ----------------
   
    def set_hidden_channels(self, hidden: set[str]) -> None:
        """
        Replace hidden-channel state and rerender.
        """
        self._hidden_channels = set(hidden)
        self.hiddenChannelsChanged.emit()
        self.render()

    def set_bad_channels(self, bad: set[str]) -> None:
        """
        Replace bad-channel state and rerender.
        """
        self._bad_channels = set(bad)
        self.badChannelsChanged.emit()
        self.render()
        
    def get_hidden_channels(self) -> list[str]:
        return sorted(self._hidden_channels)

    def get_bad_channels(self) -> list[str]:
        return sorted(self._bad_channels)

 # ---------------- Referencing / Montage----------------
    def set_monopolar_mode(self) -> None:
        self._reference_mode = "monopolar"
        self._display_names = list(self._channel_names)
        self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))
        self._clamp_ch_start()
        self.render()

    def set_average_mode(self) -> None:
        self._reference_mode = "average"
        self._display_names = list(self._channel_names)
        self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))
        self._clamp_ch_start()
        self.render()

    def set_median_mode(self) -> None:
        self._reference_mode = "median"
        self._display_names = list(self._channel_names)
        self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))
        self._clamp_ch_start()
        self.render()

    def set_common_reference_mode(self, ref_name: str) -> None:
        if ref_name not in self._channel_names:
            return

        self._reference_mode = "common"
        self._common_ref_name = str(ref_name)
        self._display_names = list(self._channel_names)
        self._monopolar_abs_to_pick_idx = list(range(len(self._channel_names)))
        self._clamp_ch_start()
        self.render()

    def set_bipolar_mode(self, montage: BipolarMontage) -> None:
        self._reference_mode = "bipolar"
        self._bipolar_montage = montage
        self._bipolar_pairs = list(montage.pairs)
        self._display_names = [pair.name for pair in self._bipolar_pairs]
        self._clear_bipolar_cache()
        self._clamp_ch_start()
        self.render()

    def _clear_bipolar_cache(self) -> None:
        self._cached_bipolar_data_key = None
        self._cached_bipolar_data = None

    def get_bipolar_montage(self):
        return self._bipolar_montage

    def _get_or_compute_bipolar_data(self) -> np.ndarray | None:
        if self._raw is None or self._picks.size == 0:
            return None
        if self._bipolar_montage is None or not self._bipolar_montage.pairs:
            return None

        key = (
            id(self._raw),
            tuple(int(x) for x in self._picks),
            tuple((pair.ch1, pair.ch2, pair.name, pair.origin) for pair in self._bipolar_montage.pairs),
        )

        if self._cached_bipolar_data_key == key and self._cached_bipolar_data is not None:
            return self._cached_bipolar_data

        raw = self._raw
        picks = self._picks

        name_to_pick_idx = {
            self._channel_names[i]: int(picks[i])
            for i in range(len(self._channel_names))
        }

        rows: list[np.ndarray] = []

        for pair in self._bipolar_montage.pairs:
            raw_idx_1 = name_to_pick_idx.get(pair.ch1)
            raw_idx_2 = name_to_pick_idx.get(pair.ch2)
            if raw_idx_1 is None or raw_idx_2 is None:
                continue

            d1, _ = raw[[int(raw_idx_1)], :]
            d2, _ = raw[[int(raw_idx_2)], :]

            d1_arr = np.asarray(d1, dtype=float)
            d2_arr = np.asarray(d2, dtype=float)

            if d1_arr.ndim != 2 or d2_arr.ndim != 2 or d1_arr.shape[0] == 0 or d2_arr.shape[0] == 0:
                continue

            rows.append(d1_arr[0, :] - d2_arr[0, :])

        if not rows:
            return None

        full_data = np.vstack(rows).astype(float, copy=False)
        self._cached_bipolar_data_key = key
        self._cached_bipolar_data = full_data
        return full_data

    def get_channel_segment(
        self,
        abs_idx: int,
        start_s: float,
        stop_s: float,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        if self._raw is None or self._picks.size == 0:
            return None
        if not (0 <= int(abs_idx) < len(self.get_channel_names())):
            return None

        raw = self._raw
        fs = float(self._fs)
        t0 = float(min(start_s, stop_s))
        t1 = float(max(start_s, stop_s))
        if t1 <= t0:
            return None

        start_samp = max(0, min(int(np.floor(t0 * fs)), raw.n_times - 1))
        stop_samp = max(start_samp + 1, min(int(np.ceil(t1 * fs)), raw.n_times))

        times = np.asarray(raw.times[start_samp:stop_samp], dtype=float)
        if times.size == 0:
            return None

        if self._reference_mode == "monopolar":
            raw_pick = int(self._picks[int(abs_idx)])
            data_v, _ = raw[[raw_pick], start_samp:stop_samp]
            signal_v = np.asarray(data_v, dtype=float)[0]

        elif self._reference_mode in ("average", "median"):
            usable_abs = self._all_nonbad_abs_indices()
            if not usable_abs:
                return None
            ref_picks = self._picks[np.asarray(usable_abs, dtype=int)]
            ref_data, _ = raw[ref_picks, start_samp:stop_samp]
            ref_data = np.asarray(ref_data, dtype=float)
            bad_mask = self._build_bad_segment_mask_for_abs(usable_abs, times)
            if bad_mask.shape == ref_data.shape:
                ref_data = ref_data.copy()
                ref_data[bad_mask] = np.nan

            if self._reference_mode == "average":
                ref = np.nanmean(ref_data, axis=0)
            else:
                ref = np.nanmedian(ref_data, axis=0)
            ref = np.where(np.isnan(ref), 0.0, ref)

            raw_pick = int(self._picks[int(abs_idx)])
            data_v, _ = raw[[raw_pick], start_samp:stop_samp]
            signal_v = np.asarray(data_v, dtype=float)[0] - ref

        elif self._reference_mode == "common":
            if not self._common_ref_name or self._common_ref_name not in self._channel_names:
                return None
            raw_pick = int(self._picks[int(abs_idx)])
            ref_abs = self._channel_names.index(self._common_ref_name)
            ref_pick = int(self._picks[ref_abs])
            data_v, _ = raw[[raw_pick], start_samp:stop_samp]
            ref_v, _ = raw[[ref_pick], start_samp:stop_samp]
            signal_v = np.asarray(data_v, dtype=float)[0] - np.asarray(ref_v, dtype=float)[0]

        elif self._reference_mode == "bipolar":
            full_data = self._get_or_compute_bipolar_data()
            if full_data is None or not (0 <= int(abs_idx) < full_data.shape[0]):
                return None
            signal_v = np.asarray(full_data[int(abs_idx), start_samp:stop_samp], dtype=float)

        else:
            return None

        return signal_v * 1e6, times

 # ---------------- Cursor & navigation ----------------

    def set_time_start(self, t_start: float):
        """Move the visible time window start (seconds)."""
        if self._raw is None:
            return
        self._t_start = float(t_start)
        self._clamp_time_start()
        self.render()
        self.timeWindowChanged.emit(self._t_start)

    def set_cursor_x(self, x: float):
        self._cursor_x = float(x)
        if self._cursor_line is not None:
            self._cursor_line.blockSignals(True)
            self._cursor_line.setPos(self._cursor_x)
            self._cursor_line.blockSignals(False)

    def _on_cursor_moved(self):
        if self._cursor_line is None:
            return
        self._cursor_x = float(np.asarray(self._cursor_line.value()).item())
        self.cursorMoved.emit(self._cursor_x)

    # ---------------- Annotation ----------------

    def start_annotation_mode(self, *, kind: str, note: str = "", scope: str = SCOPE_CLICKED) -> None:
        if self._raw is None:
            return
        self.stop_scalogram_selection_mode()
        self._annotation_mode = True
        self._pending_kind = str(kind)
        self._pending_note = str(note or "")
        self._pending_scope = str(scope)

    def stop_annotation_mode(self) -> None:
        self._annotation_mode = False
        self._pending_kind = None
        self._pending_note = ""
        self._anno_drag_active = False
        self._anno_drag_start_t = None
        self._anno_drag_y = None
        if self._anno_preview is not None:
            try:
                self.signal_plot.removeItem(self._anno_preview)
            except Exception:
                pass
            self._anno_preview = None

    def get_annotations(self) -> list[Annotation]:
        return list(self._annotations)

    def get_annotation_by_id(self, anno_id: str) -> Annotation | None:
        for a in self._annotations:
            if a.id == anno_id:
                return a
        return None

    def _create_annotation(self, *, t0: float, t1: float, y: float) -> None:
        """Create a new annotation from a mouse drag."""
        kind = self._pending_kind or "Other"
        note = self._pending_note
        scope = self._pending_scope

        clicked_abs = self._abs_channel_from_y(y)

        # Decide which channels the annotation applies to
        if scope == "Global (all channels)":
            targets: list[int | None] = [None]

        elif scope == "Selected channels" and self._selected_abs_set:
            targets = [int(x) for x in sorted(self._selected_abs_set)]

        else:  # clicked channel
            if clicked_abs is None:
                return
            targets = [int(clicked_abs)]

        for ch in targets:
            self._annotations.append(
                Annotation(
                    id=new_id(),
                    kind=kind,
                    t_start=float(t0),
                    t_end=float(t1),
                    abs_channel=ch,   # None means global
                    note=str(note or ""),
                )
            )
        self.annotationsChanged.emit()
   
    def _create_annotation_from_drag(self, *, t0: float, t1: float, y: float) -> None:
        kind = self._pending_kind or "Other"
        note = self._pending_note
        scope = self._pending_scope

        clicked_abs = self._abs_channel_from_y(y)

       # --- decide which channels to annotate ---
        # Global handled separately (no targets list needed)
        if scope == SCOPE_GLOBAL:
            anno_id = new_id()
            a = Annotation(
                id=anno_id,
                kind=kind,
                t_start=float(t0),
                t_end=float(t1),
                abs_channel=None,          # None means global
                note=str(note or ""),
            )
            self._annotations.append(a)
            self.annotationsChanged.emit()
            return

        # Non-global targets: only ints
        targets: list[int] = []

        if scope == SCOPE_SELECTED:
            if self._selected_abs_set:
                targets = [int(x) for x in sorted(self._selected_abs_set)]
            elif clicked_abs is not None:
                targets = [int(clicked_abs)]
        else:  # SCOPE_CLICKED
            if clicked_abs is not None:
                targets = [int(clicked_abs)]

        for abs_ch in targets:
            anno_id = new_id()
            a = Annotation(
                id=anno_id,
                kind=kind,
                t_start=float(t0),
                t_end=float(t1),
                abs_channel=abs_ch,
                note=str(note or ""),
            )
            self._annotations.append(a)

        self.annotationsChanged.emit()
    
    def update_annotation(
        self,
        anno_id: str,
        *,
        kind: str,
        note: str,
        t_start: float | None = None,
        t_end: float | None = None,
    ) -> None:
        # update data
        for i, a in enumerate(self._annotations):
            if a.id == anno_id:
                new_t_start = float(a.t_start if t_start is None else t_start)
                new_t_end = float(a.t_end if t_end is None else t_end)
                if new_t_end < new_t_start:
                    new_t_start, new_t_end = new_t_end, new_t_start
                self._annotations[i] = Annotation(
                    id=a.id,
                    kind=str(kind),
                    t_start=new_t_start,
                    t_end=new_t_end,
                    abs_channel=a.abs_channel,
                    note=str(note or ""),
                )
                break

        # update visuals
        self._apply_annotation_style(anno_id)
        self.annotationsChanged.emit()

    def delete_annotation(self, anno_id: str) -> None:
        self._annotations = [a for a in self._annotations if a.id != anno_id]
        self._remove_annotation_items(anno_id)
        self.annotationsChanged.emit()

    def jump_to_annotation(self, anno_id: str) -> None:
        a = self.get_annotation_by_id(anno_id)
        if a is None:
            return

        # center time
        center = 0.5 * (float(a.t_start) + float(a.t_end))
        new_t0 = center - 0.5 * float(self._time_range)
        self.set_time_start(new_t0)

        # center channel if not global
        if a.abs_channel is not None:
            self.center_channel_on(int(a.abs_channel))

    def set_annotations(self, annos: list[Annotation]) -> None:
        """
        Replace all current annotations with the provided list.
        Used when restoring a project file.
        """
        self._annotations = list(annos)
        self.annotationsChanged.emit()
        self.render()

    def set_annotations_from_dicts(self, annos: list[dict]) -> None:
        """
        Restore annotations from a list of plain dicts loaded from a project file.
        """
        restored: list[Annotation] = []
        for d in annos:
            restored.append(
                Annotation(
                    id=str(d.get("id", "")),
                    kind=str(d.get("kind", "Other")),
                    t_start=float(d.get("t_start", 0.0)),
                    t_end=float(d.get("t_end", 0.0)),
                    abs_channel=(None if d.get("abs_channel", None) is None else int(d["abs_channel"])),
                    note=str(d.get("note", "")),
                )
            )

        self.set_annotations(restored)

    def _update_preview_roi(self, *, t0: float, t1: float, y: float) -> None:
        kind = self._pending_kind or "Other"
        rgb = ANNOTATION_STYLES.get(kind, (0, 200, 0))
        x0, x1 = (t0, t1) if t0 <= t1 else (t1, t0)

        abs_idx = self._abs_channel_from_y(y)
        if abs_idx is None:
            return
        yc = self._row_center_y_for_abs(abs_idx)
        if yc is None:
            return

        h = 0.90 * float(self._spacing)
        y0 = yc - h / 2.0

        if self._anno_preview is None:
            self._anno_preview = pg.RectROI([x0, y0], [max(1e-6, x1 - x0), h], pen=None, movable=False)
            self._anno_preview.setZValue(-5)
            self.signal_plot.addItem(self._anno_preview)

        self._anno_preview.setPos([x0, y0])
        self._anno_preview.setSize([max(1e-6, x1 - x0), h])
        self._anno_preview.setBrush(pg.mkBrush(rgb[0], rgb[1], rgb[2], 60)) # type: ignore

    def _add_annotation_items(self, a: Annotation) -> None:
        rgb = ANNOTATION_STYLES.get(a.kind, (0, 200, 0))
        h = 0.90 * float(self._spacing)

        if a.abs_channel is None:
            # Global: span full visible height
            n_vis = len(getattr(self, "_last_visible_abs", []))
            if n_vis <= 0:
                return
            y0 = -h / 2.0
            height = float(n_vis) * float(self._spacing)
        else:
            yc = self._row_center_y_for_abs(int(a.abs_channel))
            if yc is None:
                # channel not visible right now; we still keep the annotation data
                return
            y0 = yc - h / 2.0
            height = h

        roi = _AnnotationROI(
            viewer=self,
            anno_id=a.id,
            pos=[float(a.t_start), float(y0)],
            size=[max(1e-6, float(a.t_end - a.t_start)), float(height)],
            brush=pg.mkBrush(rgb[0], rgb[1], rgb[2], 60),
        )
        roi.setZValue(-5)

        # Constrain vertical movement: we only allow time edits
        roi.sigRegionChanged.connect(lambda: self._keep_roi_y_fixed(a.id))
        roi.sigRegionChangeFinished.connect(lambda: self._commit_roi_to_annotation(a.id))

        self.signal_plot.addItem(roi)
        self._anno_rois[a.id] = roi

        # Note label (displayed next to annotation)
        label_txt = a.note if a.note else ""
        txt_item = pg.TextItem(text=label_txt, color=(255, 255, 255), anchor=(0, 1))
        txt_item.setZValue(-4)
        self.signal_plot.addItem(txt_item)
        self._anno_labels[a.id] = txt_item

        self._reposition_annotation_label(a.id)

    def _keep_roi_y_fixed(self, anno_id: str) -> None:
        """During drag/resize, keep ROI pinned to the correct channel row."""
        a = self.get_annotation_by_id(anno_id)
        roi = self._anno_rois.get(anno_id)
        if a is None or roi is None:
            return

        if a.abs_channel is None:
            return  # global can span

        yc = self._row_center_y_for_abs(int(a.abs_channel))
        if yc is None:
            return
        h = 0.90 * float(self._spacing)
        y0 = yc - h / 2.0

        pos = roi.pos()
        roi.setPos([pos.x(), y0])

    def _commit_roi_to_annotation(self, anno_id: str) -> None:
        """After user finishes resizing/moving ROI, write new t_start/t_end back to data."""
        a = self.get_annotation_by_id(anno_id)
        roi = self._anno_rois.get(anno_id)
        if a is None or roi is None:
            return

        pos = roi.pos()
        size = roi.size()
        t_start = float(pos.x())
        t_end = float(pos.x() + size[0])

        # Update stored annotation
        for i, ann in enumerate(self._annotations):
            if ann.id == anno_id:
                self._annotations[i] = Annotation(
                    id=ann.id,
                    kind=ann.kind,
                    t_start=t_start,
                    t_end=t_end,
                    abs_channel=ann.abs_channel,
                    note=ann.note,
                )
                break

        self._reposition_annotation_label(anno_id)
        self.annotationsChanged.emit()

    def _reposition_annotation_label(self, anno_id: str) -> None:
        a = self.get_annotation_by_id(anno_id)
        roi = self._anno_rois.get(anno_id)
        txt = self._anno_labels.get(anno_id)
        if a is None or roi is None or txt is None:
            return

        if not a.note:
            txt.setText("")
            return

        pos = roi.pos()
        # Place label near left edge of the region
        txt.setPos(pos.x(), pos.y())

    def _apply_annotation_style(self, anno_id: str) -> None:
        a = self.get_annotation_by_id(anno_id)
        roi = self._anno_rois.get(anno_id)
        txt = self._anno_labels.get(anno_id)
        if a is None:
            return

        rgb = ANNOTATION_STYLES.get(a.kind, (0, 200, 0))
        if roi is not None:
            roi.setBrush(pg.mkBrush(rgb[0], rgb[1], rgb[2], 60)) # type: ignore

        if txt is not None:
            txt.setText(a.note or "")

        self._reposition_annotation_label(anno_id)

    def _remove_annotation_items(self, anno_id: str) -> None:
        roi = self._anno_rois.pop(anno_id, None)
        if roi is not None:
            try:
                self.signal_plot.removeItem(roi)
            except Exception:
                pass

        txt = self._anno_labels.pop(anno_id, None)
        if txt is not None:
            try:
                self.signal_plot.removeItem(txt)
            except Exception:
                pass

    def _clear_annotation_items(self) -> None:
        """Remove currently drawn annotation graphics (rectangles + text)."""
        for it in getattr(self, "_annotation_items", []):
            try:
                self.signal_plot.removeItem(it)
            except Exception:
                pass
        self._annotation_items = []

    def _build_bad_segment_mask_for_abs(
        self,
        visible_abs: list[int],
        times: np.ndarray,
    ) -> np.ndarray:
        """
        Return a boolean mask of shape (n_channels, n_times) where True means:
        exclude this sample from the average/median reference pool.

        Rules:
        - global "Bad segment" annotations mask all channels over that interval
        - channel-specific "Bad segment" annotations mask only that channel
        - only applies to channels present in visible_abs / ref_abs passed in
        """
        n_ch = len(visible_abs)
        n_t = int(times.size)

        mask = np.zeros((n_ch, n_t), dtype=bool)
        if n_ch == 0 or n_t == 0:
            return mask

        abs_to_row = {int(abs_idx): row for row, abs_idx in enumerate(visible_abs)}
        for a in self._annotations:
            if str(a.kind) != "Bad segment":
                continue

            t0 = float(min(a.t_start, a.t_end))
            t1 = float(max(a.t_start, a.t_end))

            # interval -> sample mask on the provided times vector
            time_mask = (times >= t0) & (times <= t1)
            if not np.any(time_mask):
                continue

            if a.abs_channel is None:
                # global bad segment
                mask[:, time_mask] = True
            else:
                row = abs_to_row.get(int(a.abs_channel))
                if row is not None:
                    mask[row, time_mask] = True

        return mask

    # ---------------- Zoom window ----------------
    def _current_view_state(self) -> dict[str, float | int]:
        return {
            "t_start": float(self._t_start),
            "time_range": float(self._time_range),
            "ch_start": int(self._ch_start),
            "chan_range": int(self._chan_range),
        }

    def _apply_view_state(self, state: dict[str, float | int]) -> None:
        self._t_start = float(state["t_start"])
        self._time_range = float(state["time_range"])
        self._ch_start = int(state["ch_start"])
        self._chan_range = int(state["chan_range"])

        self._clamp_time_start()
        self._clamp_ch_start()
        self.render()
        self.timeWindowChanged.emit(self._t_start)
        self.channelWindowChanged.emit(self._ch_start)

    def start_zoom_selection_mode(self) -> None:
        if self._raw is None or self._picks.size == 0:
            return

        # cancel annotation mode if active
        if self._annotation_mode:
            self.stop_annotation_mode()
        self.stop_scalogram_selection_mode()

        self._zoom_mode = True
        self._zoom_drag_active = False
        self._zoom_drag_start_t = None
        self._zoom_drag_start_y = None

        # Capture the base view only once, at the start of a zoom session
        if self._zoom_base_view is None:
            self._zoom_base_view = self._current_view_state()
            self._zoom_history = []
            self.zoomStateChanged.emit(True)

        self.setCursor(Qt.CursorShape.CrossCursor)

    def stop_zoom_selection_mode(self) -> None:
        self._zoom_mode = False
        self._zoom_drag_active = False
        self._zoom_drag_start_t = None
        self._zoom_drag_start_y = None

        if self._zoom_preview is not None:
            try:
                self.signal_plot.removeItem(self._zoom_preview)
            except Exception:
                pass
            self._zoom_preview = None

        self.setCursor(Qt.CursorShape.ArrowCursor)

    def start_scalogram_selection_mode(self) -> None:
        if self._raw is None or self._picks.size == 0:
            return

        if self._annotation_mode:
            self.stop_annotation_mode()
        self.stop_zoom_selection_mode()

        self._scalogram_mode = True
        self._scalogram_drag_active = False
        self._scalogram_drag_start_t = None
        self._scalogram_drag_abs = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.scalogramModeChanged.emit(True)

    def stop_scalogram_selection_mode(self) -> None:
        was_active = self._scalogram_mode
        self._scalogram_mode = False
        self._scalogram_drag_active = False
        self._scalogram_drag_start_t = None
        self._scalogram_drag_abs = None

        if self._scalogram_preview is not None:
            try:
                self.signal_plot.removeItem(self._scalogram_preview)
            except Exception:
                pass
            self._scalogram_preview = None

        self.setCursor(Qt.CursorShape.ArrowCursor)
        if was_active:
            self.scalogramModeChanged.emit(False)

    def _update_scalogram_preview(self, *, abs_idx: int, t0: float, t1: float) -> None:
        y_center = self._row_center_y_for_abs(abs_idx)
        if y_center is None:
            return

        x0, x1 = (t0, t1) if t0 <= t1 else (t1, t0)
        height = 0.8 * float(self._spacing)
        y0 = y_center - height / 2.0

        if self._scalogram_preview is None:
            rect = QtWidgets.QGraphicsRectItem()
            rect.setPen(pg.mkPen((255, 190, 80), width=1.5))
            rect.setBrush(pg.mkBrush(255, 190, 80, 60))
            rect.setZValue(25)
            self.signal_plot.addItem(rect)
            self._scalogram_preview = rect

        self._scalogram_preview.setRect(x0, y0, max(1e-6, x1 - x0), height)

    def reset_zoom_to_base(self) -> None:
        if self._zoom_base_view is None:
            return

        self._apply_view_state(self._zoom_base_view)
        self._zoom_history.clear()
        self._zoom_base_view = None
        self.stop_zoom_selection_mode()
        self.zoomStateChanged.emit(False)

    def zoom_back_one_step(self) -> None:
        if not self._zoom_history:
            return

        state = self._zoom_history.pop()
        self._apply_view_state(state)

        if not self._zoom_history and self._zoom_base_view is None:
            self.zoomStateChanged.emit(False)

    def _update_zoom_preview(self, *, t0: float, t1: float, y0: float, y1: float) -> None:
        x0, x1 = (t0, t1) if t0 <= t1 else (t1, t0)
        yy0, yy1 = (y0, y1) if y0 <= y1 else (y1, y0)

        if self._zoom_preview is None:
            rect = QtWidgets.QGraphicsRectItem()
            rect.setPen(pg.mkPen((100, 200, 255), width=1))
            rect.setBrush(pg.mkBrush(100, 200, 255, 40))
            rect.setZValue(20)
            self.signal_plot.addItem(rect)
            self._zoom_preview = rect

        self._zoom_preview.setRect(x0, yy0, max(1e-6, x1 - x0), max(1e-6, yy1 - yy0))

    def _apply_zoom_selection(self, *, t0: float, t1: float, y0: float, y1: float) -> None:
        if self._raw is None or self._picks.size == 0:
            return

        if abs(t1 - t0) < 1e-6 or abs(y1 - y0) < 1e-6:
            return

        # Time range from selection
        t_min, t_max = (t0, t1) if t0 <= t1 else (t1, t0)
        new_time_range = max(0.1, float(t_max - t_min))

        # Channel range from selection (current visible channels only)
        visible_abs = list(getattr(self, "_last_visible_abs", []))
        if not visible_abs:
            return

        n_vis = len(visible_abs)
        plot_row_a = int(min(y0, y1) // float(self._spacing))
        plot_row_b = int(max(y0, y1) // float(self._spacing))

        plot_row_a = max(0, min(plot_row_a, n_vis - 1))
        plot_row_b = max(0, min(plot_row_b, n_vis - 1))

        data_row_top = n_vis - 1 - plot_row_b
        data_row_bottom = n_vis - 1 - plot_row_a

        data_row_top = max(0, min(data_row_top, n_vis - 1))
        data_row_bottom = max(0, min(data_row_bottom, n_vis - 1))

        sel_visible_abs = visible_abs[data_row_top : data_row_bottom + 1]
        if not sel_visible_abs:
            return

        all_vis = self._all_visible_abs_indices()
        if not all_vis:
            return

        first_abs = sel_visible_abs[0]
        last_abs = sel_visible_abs[-1]

        if first_abs not in all_vis or last_abs not in all_vis:
            return

        new_ch_start = all_vis.index(first_abs)
        new_chan_range = max(1, len(sel_visible_abs))

        # save current view before zooming
        self._zoom_history.append(self._current_view_state())

        self._t_start = float(t_min)
        self._time_range = float(new_time_range)
        self._ch_start = int(new_ch_start)
        self._chan_range = int(new_chan_range)

        self._clamp_time_start()
        self._clamp_ch_start()
        self.render()
        self.timeWindowChanged.emit(self._t_start)
        self.channelWindowChanged.emit(self._ch_start)
    
    # ---------------- Basic Interactions ----------------

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
            # Cancel annotation mode
            if ev.type() == QEvent.Type.KeyPress and getattr(self, "_annotation_mode", False):
                if ev.key() == Qt.Key.Key_Escape:
                    self.stop_annotation_mode()
                    ev.accept()
                    return True

            # Wheel behavior
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

            # --- Right-click context menu: swallow the whole RMB gesture ---
            if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.RightButton:
                scene_pos = self.mapToScene(ev.position().toPoint())

                if self._scene_pos_hits_annotation(scene_pos):
                    return super().eventFilter(obj, ev)

                if self._sig_vb.sceneBoundingRect().contains(scene_pos):
                    self._context_menu_active = True

                    if self._show_context_menu_for_scene_pos(scene_pos):
                        ev.accept()
                        return True

                    ev.accept()
                    return True

            if ev.type() == QEvent.Type.MouseMove and getattr(self, "_context_menu_active", False):
                ev.accept()
                return True

            if ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.RightButton:
                if getattr(self, "_context_menu_active", False):
                    self._context_menu_active = False
                    ev.accept()
                    return True

            # --- Zoom selection mode ---
            if getattr(self, "_zoom_mode", False):
                if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                    self.stop_zoom_selection_mode()
                    ev.accept()
                    return True

                if ev.type() in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonRelease,
                ):
                    scene_pos = self.mapToScene(ev.position().toPoint())
                    if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
                        return super().eventFilter(obj, ev)

                    p = self._sig_vb.mapSceneToView(scene_pos)
                    t = float(p.x())
                    y = float(p.y())

                    if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                        self._zoom_drag_active = True
                        self._zoom_drag_start_t = t
                        self._zoom_drag_start_y = y
                        self._update_zoom_preview(t0=t, t1=t, y0=y, y1=y)
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseMove and self._zoom_drag_active:
                        t0 = float(self._zoom_drag_start_t if self._zoom_drag_start_t is not None else t)
                        y0 = float(self._zoom_drag_start_y if self._zoom_drag_start_y is not None else y)
                        self._update_zoom_preview(t0=t0, t1=t, y0=y0, y1=y)
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                        if not self._zoom_drag_active:
                            return True

                        t0 = float(self._zoom_drag_start_t if self._zoom_drag_start_t is not None else t)
                        y0 = float(self._zoom_drag_start_y if self._zoom_drag_start_y is not None else y)

                        self._zoom_drag_active = False
                        self._zoom_drag_start_t = None
                        self._zoom_drag_start_y = None

                        self._apply_zoom_selection(t0=t0, t1=t, y0=y0, y1=y)
                        self.stop_zoom_selection_mode()
                        ev.accept()
                        return True

            # --- Scalogram selection mode ---
            if getattr(self, "_scalogram_mode", False):
                if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                    self.stop_scalogram_selection_mode()
                    ev.accept()
                    return True

                if ev.type() in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonRelease,
                ):
                    scene_pos = self.mapToScene(ev.position().toPoint())

                    if ev.type() == QEvent.Type.MouseButtonPress:
                        if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
                            return super().eventFilter(obj, ev)
                    elif not self._scalogram_drag_active:
                        if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
                            return super().eventFilter(obj, ev)

                    p = self._sig_vb.mapSceneToView(scene_pos)
                    t = float(p.x())
                    y = float(p.y())

                    if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                        abs_idx = self._abs_channel_from_y(y)
                        if abs_idx is None:
                            ev.accept()
                            return True
                        self._select_single_channel_abs(abs_idx)
                        self._scalogram_drag_active = True
                        self._scalogram_drag_start_t = t
                        self._scalogram_drag_abs = abs_idx
                        self._update_scalogram_preview(abs_idx=abs_idx, t0=t, t1=t)
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseMove and self._scalogram_drag_active:
                        abs_idx = self._scalogram_drag_abs
                        t0 = float(self._scalogram_drag_start_t if self._scalogram_drag_start_t is not None else t)
                        if abs_idx is not None:
                            self._update_scalogram_preview(abs_idx=abs_idx, t0=t0, t1=t)
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                        if not self._scalogram_drag_active:
                            return True

                        abs_idx = self._scalogram_drag_abs
                        t0 = float(self._scalogram_drag_start_t if self._scalogram_drag_start_t is not None else t)
                        t_min, t_max = (t0, t) if t0 <= t else (t, t0)

                        self._scalogram_drag_active = False
                        self._scalogram_drag_start_t = None
                        self._scalogram_drag_abs = None

                        min_duration = max(self._min_scalogram_duration_s, 2.0 / max(self._fs, 1.0))
                        if abs(t_max - t_min) >= min_duration and abs_idx is not None:
                            self.scalogramRequested.emit(int(abs_idx), float(t_min), float(t_max))
                            self.stop_scalogram_selection_mode()
                        elif abs_idx is not None:
                            self._update_scalogram_preview(abs_idx=int(abs_idx), t0=t_min, t1=t_min)
                        ev.accept()
                        return True

            # --- Annotation click/drag ---
            if getattr(self, "_annotation_mode", False) and getattr(self, "_pending_kind", None) is not None:
                if ev.type() in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonRelease,
                ):
                    scene_pos = self.mapToScene(ev.position().toPoint())

                    if ev.type() == QEvent.Type.MouseButtonPress:
                        if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
                            return super().eventFilter(obj, ev)
                    elif not self._anno_drag_active:
                        if not self._sig_vb.sceneBoundingRect().contains(scene_pos):
                            return super().eventFilter(obj, ev)

                    p = self._sig_vb.mapSceneToView(scene_pos)
                    t = float(p.x())
                    y = float(p.y())

                    if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                        self._anno_drag_active = True
                        self._anno_drag_start_t = t
                        self._anno_drag_start_y = y
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseMove and self._anno_drag_active:
                        t0 = float(self._anno_drag_start_t if self._anno_drag_start_t is not None else t)
                        y0 = float(self._anno_drag_start_y if self._anno_drag_start_y is not None else y)
                        self._update_preview_roi(t0=t0, t1=t, y=y0)
                        ev.accept()
                        return True

                    if ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                        if not self._anno_drag_active:
                            return True

                        t0 = float(self._anno_drag_start_t if self._anno_drag_start_t is not None else t)
                        t1 = float(t)
                        y0 = float(self._anno_drag_start_y if self._anno_drag_start_y is not None else y)

                        self._anno_drag_active = False
                        self._anno_drag_start_t = None
                        self._anno_drag_start_y = None

                        if t1 < t0:
                            t0, t1 = t1, t0

                        if abs(t1 - t0) < 1e-6:
                            dt = 0.10
                            t0 = t0 - dt / 2.0
                            t1 = t0 + dt

                        self._create_annotation_from_drag(t0=t0, t1=t1, y=y0)
                        self.stop_annotation_mode()
                        self.render()

                        ev.accept()
                        return True

        return super().eventFilter(obj, ev)

    def _on_mouse_clicked(self, event):
        """Left click selects channel."""
        if self._raw is None or self._visible_abs.size == 0:
            return
        if getattr(self, "_scalogram_mode", False) or getattr(self, "_scalogram_drag_active", False):
            return

        if event.double():
            if event.button() == Qt.MouseButton.LeftButton:
                self.zoom_back_one_step()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.scenePos()
        in_label = self._label_vb.sceneBoundingRect().contains(pos)
        in_signal = self._sig_vb.sceneBoundingRect().contains(pos)

        vb = self._label_vb if in_label else self._sig_vb
        data_point = vb.mapSceneToView(pos)
        y = float(data_point.y())

        n_vis = len(self._visible_abs)
        centers = (np.arange(n_vis)[::-1]) * self._spacing
        idx_vis = int(np.argmin(np.abs(centers - y)))
        idx_vis = max(0, min(idx_vis, n_vis - 1))

        idx_abs = int(self._visible_abs[idx_vis])

        mods = QtWidgets.QApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        all_vis = self._all_visible_abs_indices()

        if not ctrl and not shift:
            self._selected_abs_set = {idx_abs}
            self._selection_anchor_abs = idx_abs

        elif ctrl and not shift:
            if idx_abs in self._selected_abs_set:
                self._selected_abs_set.remove(idx_abs)
            else:
                self._selected_abs_set.add(idx_abs)

        else:
            if self._selection_anchor_abs is None:
                self._selection_anchor_abs = idx_abs

            if (idx_abs in all_vis) and (self._selection_anchor_abs in all_vis):
                i1 = all_vis.index(self._selection_anchor_abs)
                i2 = all_vis.index(idx_abs)
                lo, hi = (i1, i2) if i1 <= i2 else (i2, i1)
                range_set = set(all_vis[lo:hi + 1])

                if ctrl:
                    self._selected_abs_set |= range_set
                else:
                    self._selected_abs_set = range_set

        self.channelClicked.emit(idx_abs)
        self.selectionChanged.emit(sorted(self._selected_abs_set))
        self.highlight_selected_channels()
        
class _AnnotationROI(pg.RectROI):
    def __init__(self, *, viewer: MultiChannelViewer, anno_id: str, pos, size, brush):
        super().__init__(pos, size, pen=None, movable=True)
        self._viewer = viewer
        self._anno_id = anno_id
        self.setBrush(brush) # type: ignore

    def contextMenuEvent(self, ev):
        menu = QtWidgets.QMenu()
        act_edit = menu.addAction("Edit...")
        act_del = menu.addAction("Delete")
        chosen = menu.exec_(ev.screenPos().toPoint())

        if chosen == act_del:
            self._viewer.delete_annotation(self._anno_id)
        elif chosen == act_edit:
            self._viewer.requestEditAnnotation.emit(self._anno_id)

        ev.accept()
