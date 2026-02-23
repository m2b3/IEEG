import numpy as np # for fake data 
import pyqtgraph as pg
from PySide6.QtCore import Signal, Qt
from typing import cast
from pyqtgraph.GraphicsScene.GraphicsScene import GraphicsScene


class MultiChannelViewer(pg.PlotWidget):
    channelClicked = Signal(int)  # emits channel index (0-based)

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        # Store references (avoids Pylance warnings and repeated lookups)
        self.plot_item = self.getPlotItem()
        assert self.plot_item is not None  
        self.view_box = self.plot_item.getViewBox()

        self._curves: list[pg.PlotDataItem] = []
        self._spacing = 200.0

        self._data: np.ndarray | None = None          # (n_channels, n_samples)
        self._fs: float = 1.0
        self._channel_names: list[str] = []
        self._t_start: float = 0.0
        self._time_range: float = 3.0
        self._chan_range: int = 32
        self._gain: float = 1.0

        # Listen to clicks on the plot (cast only for type-checker)
        scene = cast(GraphicsScene, self.scene())
        scene.sigMouseClicked.connect(self._on_mouse_clicked)

        self.hideAxis('left')
        self.hideAxis('bottom')
        self.setMouseEnabled(x=False, y=False)


    def set_signals(self, data: np.ndarray, fs: float, channel_names: list[str]):
        """Load signals into the viewer and render the current window."""
        if data.ndim != 2:
            raise ValueError(f"Expected data shape (n_channels, n_samples), got {data.shape}")

        self._data = data
        self._fs = float(fs)
        self._channel_names = channel_names

        # reset view state
        self._t_start = 0.0
        self._time_range = min(self._time_range, data.shape[1] / self._fs)

        self.render()

    def set_view_params(self, time_range: float | None = None, chan_range: int | None = None, gain: float | None = None):
        if time_range is not None:
            self._time_range = float(time_range)
        if chan_range is not None:
            self._chan_range = int(chan_range)
        if gain is not None:
            self._gain = float(gain)
        self.render()

    def render(self):
        """Redraw based on current state: t_start, time_range, chan_range, gain."""
        if self._data is None:
            return

        data = self._data
        n_channels, n_samples = data.shape

        # time window indices
        i0 = int(self._t_start * self._fs)
        i1 = int((self._t_start + self._time_range) * self._fs)
        i0 = max(0, min(i0, n_samples - 1))
        i1 = max(i0 + 1, min(i1, n_samples))

        seg = data[:, i0:i1]  # (n_channels, window_samples)
        win_len = seg.shape[1]
        t = np.arange(win_len) / self._fs + (i0 / self._fs)

        # choose how many channels to show (top N for now)
        n_vis = min(self._chan_range, n_channels)

        # basic decimation for speed if window is huge
        max_points = 3000
        step = max(1, win_len // max_points)
        t_ds = t[::step]
        seg_ds = seg[:n_vis, ::step]

        self.clear()
        self._curves = []

        for i in range(n_vis):
            y = (seg_ds[i] * self._gain) + i * self._spacing
            curve = self.plot(t_ds, y)
            self._curves.append(curve)

        self.setXRange(float(t_ds[0]), float(t_ds[-1]))
    

    def highlight_channel(self, index: int):
        if not (0 <= index < len(self._curves)):
            return

        for c in self._curves:
            c.setPen(pg.mkPen(width=1))
        self._curves[index].setPen(pg.mkPen(width=3))


    def _on_mouse_clicked(self, event):
        if not self._curves:
            return

        # Only react to LEFT click
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Convert from scene coords -> data coords
        pos = event.scenePos()
        data_point = self.view_box.mapSceneToView(pos)
        y = data_point.y()

        # robust nearest channel index
        centers = np.arange(len(self._curves)) * self._spacing
        idx = int(np.argmin(np.abs(centers - y)))
        self.channelClicked.emit(idx)
