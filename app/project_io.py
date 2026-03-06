from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from typing import Any

PROJECT_VERSION = 1
PROJECT_FORMAT = "ieeg-review-project"


def build_project_dict(main_window) -> dict[str, Any]:
    viewer = main_window.viewer

    annotations = [asdict(a) for a in viewer.get_annotations()]

    project_name = None
    if getattr(main_window, "project_path", None) is not None:
        project_name = Path(main_window.project_path).stem
    elif getattr(main_window, "loaded_file", None) is not None:
        project_name = Path(main_window.loaded_file).stem
    else:
        project_name = "untitled"

    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "project_name": project_name,
        "source": {
            "raw_file": str(main_window.loaded_file) if main_window.loaded_file else None,
        },
        "review": {
            "annotations": annotations,
            "hidden_channels": sorted(list(getattr(viewer, "_hidden_channels", set()))),
            "bad_channels": sorted(list(getattr(viewer, "_bad_channels", set()))),
        },
    }

def save_project(path: Path, main_window) -> None:
    payload = build_project_dict(main_window)
    path = Path(path)

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def load_project(path: Path) -> dict[str, Any]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("format") != PROJECT_FORMAT:
        raise ValueError(f"Unsupported project format: {payload.get('format')!r}")

    if int(payload.get("version", 0)) != PROJECT_VERSION:
        raise ValueError(f"Unsupported project version: {payload.get('version')!r}")

    return payload