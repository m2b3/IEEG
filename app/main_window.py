from PySide6.QtWidgets import (
    QMainWindow, QMessageBox, QStatusBar, QWidget,
    QHBoxLayout, QVBoxLayout, QListWidget, QFrame,
    QToolBar, QLabel, QSpinBox, QDoubleSpinBox, QFileDialog
)
from PySide6.QtCore import Qt

from app.menus import build_menubar
from app.plot import MultiChannelViewer
from app.console_viewer import ConsoleWindow
from pathlib import Path
import numpy as np



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Halyzia — UI Shell")
        self.resize(1400, 800)

        # ---- Menu bar (top) ----
        build_menubar(self)
        
        # ---- Toolbar controls (time / channel range / amplitude) ----
        self._build_toolbar()

        # ---- Central Container ----
        central = QWidget()
        self.setCentralWidget(central)

        # Vertical layout: [top area (channels+plot)] + [bottom timeline]
        main_v = QVBoxLayout(central)  
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        # Top area container
        top = QWidget()
        top_h = QHBoxLayout(top)
        top_h.setContentsMargins(0, 0, 0, 0)
        top_h.setSpacing(0)

        # LEFT: channels (fixed width)
        self.channel_list = QListWidget()
        self.channel_list.setFixedWidth(260)

        self.channel_names = []
        self.channel_list.addItems(self.channel_names)
        top_h.addWidget(self.channel_list)

        # CENTER: multi-channel viewer
        self.viewer = MultiChannelViewer()
        top_h.addWidget(self.viewer, 1)

        # BOTTOM: timeline/cursor bar placeholder
        self.timeline = QFrame()
        self.timeline.setFixedHeight(70)      # adjust to match Halyzia
        self.timeline.setFrameShape(QFrame.Shape.StyledPanel)

        self.channel_list.hide()
        self.timeline.hide()

        # assemble
        main_v.addWidget(top, 1)              # stretch=1 => grows
        main_v.addWidget(self.timeline, 0)

       # Click on signal -> select label; label -> highlight signal
        self.viewer.channelClicked.connect(self.channel_list.setCurrentRow)
        self.channel_list.currentRowChanged.connect(self.viewer.highlight_channel)
        if self.channel_list.count() > 0:
            self.channel_list.setCurrentRow(0)


        # ---- Status bar ----
        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

        # ---- Console viewer (separate window) ----
        self.console = ConsoleWindow()
        self.console.show()
        self.console.log("Console ready.")


    def _build_toolbar(self):
        tb = QToolBar("Controls")
        tb.setMovable(False)  # fixed like Halyzia-style tools
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # Time range (seconds)
        tb.addWidget(QLabel("Time Range (s):"))
        self.time_range = QDoubleSpinBox()
        self.time_range.setRange(0.5, 60.0)
        self.time_range.setSingleStep(0.5)
        self.time_range.setValue(3.0)
        tb.addWidget(self.time_range)

        tb.addSeparator()

        # Channel range (how many visible)
        tb.addWidget(QLabel("Channels range:"))
        self.chan_range = QSpinBox()
        self.chan_range.setRange(1, 256)
        self.chan_range.setValue(32)
        tb.addWidget(self.chan_range)

        tb.addSeparator()

        # Amplitude / gain (placeholder)
        tb.addWidget(QLabel("Amplitude (uV):+-"))
        self.gain = QDoubleSpinBox()
        self.gain.setRange(0.1, 20.0)
        self.gain.setSingleStep(0.1)
        self.gain.setValue(1.0)
        tb.addWidget(self.gain)

        self.time_range.valueChanged.connect(lambda v: self.viewer.set_view_params(time_range=v))
        self.chan_range.valueChanged.connect(lambda v: self.viewer.set_view_params(chan_range=v))
        self.gain.valueChanged.connect(lambda v: self.viewer.set_view_params(gain=v))


    def on_open(self):
        # 1) Open a file dialog
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open signal file",
            "",
            "NumPy arrays (*.npy *.npz);;All files (*)"
        )

        if not path:  # user cancelled
            if hasattr(self, "console"):
                self.console.log("Open cancelled.")
            return

        # 2) Log it
        print(f"Open: {path}")
        if hasattr(self, "console"):
            self.console.log(f"Open: {path}")
        
        try:
                p = Path(path)
                if p.suffix.lower() == ".npy":
                    data = np.load(p)
                elif p.suffix.lower() == ".npz":
                    npz = np.load(p)
                    data = npz["data"] if "data" in npz else npz[list(npz.keys())[0]]
                else:
                    raise ValueError("Unsupported file type for now (use .npy or .npz)")

                if data.ndim != 2:
                    raise ValueError(f"Expected 2D array (n_channels, n_samples), got {data.shape}")

                n_channels = data.shape[0]
                channel_names = [f"CH{i:03d}" for i in range(n_channels)]
                fs = 500.0  # for now; later read from file (EDF/MNE)

                # update UI
                self.channel_list.clear()
                self.channel_list.addItems(channel_names)

                self.viewer.set_signals(data, fs, channel_names)

                self.channel_list.show()
                self.timeline.show()

                self.time_range.setEnabled(True)
                self.chan_range.setEnabled(True)
                self.gain.setEnabled(True)

                if self.channel_list.count() > 0:
                    self.channel_list.setCurrentRow(0)

                if hasattr(self, "console"):
                    self.console.log(f"Loaded: {p.name} shape={data.shape} fs={fs}")

        except Exception as e:
            if hasattr(self, "console"):
                self.console.log(f"Open failed: {e}")
            QMessageBox.critical(self, "Open failed", str(e))





