# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify that the iEEG Tool environment can import and construct the GUI."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path


REQUIRED_MODULES = (
    "HFODetector",
    "matplotlib",
    "mne",
    "numpy",
    "pandas",
    "pyqtgraph",
    "PySide6",
    "safetensors",
    "scipy",
    "skimage",
    "torch",
    "torchvision",
)

CHECKPOINTS = (
    Path("app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_a.tar"),
    Path("app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_s.tar"),
    Path("app/computation/hfo/checkpoints/ehfo/artifacts.pth"),
    Path("app/computation/hfo/checkpoints/ehfo/spikes.pth"),
    Path("app/computation/hfo/checkpoints/ehfo/eHFOs.pth"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-window",
        action="store_true",
        help="check imports and assets without constructing the Qt main window",
    )
    return parser.parse_args()


def check_imports() -> None:
    for module_name in REQUIRED_MODULES:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", "installed")
        print(f"{module_name}: {version}")

    from HFODetector import hil, mni, ste  # noqa: F401
    from app.computation import gamma_spike, hfo, rei  # noqa: F401


def check_assets() -> None:
    missing = [str(path) for path in CHECKPOINTS if not path.is_file()]
    empty = [str(path) for path in CHECKPOINTS if path.is_file() and path.stat().st_size == 0]
    if missing or empty:
        details = ", ".join(missing + empty)
        raise RuntimeError(f"missing or empty HFO checkpoint: {details}")
    print(f"HFO checkpoints: {len(CHECKPOINTS)} present")


def check_checkpoint_loading() -> None:
    import torch

    from app.computation.hfo.classification._pyhfo_binary_common import model
    from app.computation.hfo.classification._pyhfo_binary_common.classifier import (
        _install_pyhfo_pickle_aliases,
    )

    _install_pyhfo_pickle_aliases(model)
    for checkpoint in CHECKPOINTS:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected checkpoint format: {checkpoint}")
    print(f"HFO checkpoints: {len(CHECKPOINTS)} loaded successfully")


def check_window() -> None:
    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    application.processEvents()
    window.close()
    application.processEvents()
    print("Qt main window: constructed successfully")


def main() -> None:
    args = parse_args()
    check_imports()
    check_assets()
    check_checkpoint_loading()
    if not args.skip_window:
        check_window()
    print("Environment check passed")


if __name__ == "__main__":
    main()
