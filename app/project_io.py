from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from typing import Any

PROJECT_VERSION = 4
PROJECT_FORMAT = "ieeg-review-project"


def _serialize_bipolar_montage_if_edited(montage) -> dict[str, Any] | None:
    if montage is None or not getattr(montage, "pairs", None):
        return None

    has_manual_edit = any(getattr(pair, "origin", "") == "manual" for pair in montage.pairs)
    if not has_manual_edit:
        return None

    return {
        "pairs": [
            {
                "name": pair.name,
                "ch1": pair.ch1,
                "ch2": pair.ch2,
                "origin": pair.origin,
            }
            for pair in montage.pairs
        ],
        "unparsed_channels": list(getattr(montage, "unparsed_channels", [])),
        "non_consecutive_channels": list(getattr(montage, "non_consecutive_channels", [])),
        "bad_channel_skips": list(getattr(montage, "bad_channel_skips", [])),
    }

def build_project_dict(main_window) -> dict[str, Any]:
    viewer = main_window.viewer

    annotations = [asdict(a) for a in viewer.get_annotations()]
    bipolar_montage = getattr(main_window, "_saved_bipolar_montage", None)
    filter_profiles = getattr(main_window, "filter_profiles", None)
    channel_groups = getattr(main_window, "channel_groups", {})

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
            "hidden_channels": viewer.get_hidden_channels(),
            "bad_channels": viewer.get_bad_channels(),
            "bipolar_montage": _serialize_bipolar_montage_if_edited(bipolar_montage),
            "channel_groups": channel_groups,
        },
        "preprocessing": {
            "filters": _serialize_filter_profiles(filter_profiles),
        },
    }

def save_project(path: Path, main_window) -> None:
    payload = build_project_dict(main_window)
    path = Path(path)

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def load_project(path: Path) -> dict[str, Any]:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Project file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid project JSON: {path}") from e

    if not isinstance(payload, dict):
        raise ValueError("Project file must contain a JSON object")

    fmt = payload.get("format")
    if fmt != PROJECT_FORMAT:
        raise ValueError(f"Unsupported project format: {fmt!r}")

    version_raw = payload.get("version")
    if not isinstance(version_raw, int):
        raise ValueError(f"Invalid project version: {version_raw!r}")
    if version_raw not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported project version: {version_raw!r}")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise ValueError("Project file missing 'source' section")

    raw_file = source.get("raw_file")
    if not isinstance(raw_file, str) or not raw_file.strip():
        raise ValueError("Project does not contain a valid source.raw_file")

    return payload

def _serialize_filter_settings(filters) -> dict[str, Any]:
    return {
        "highpass_hz": filters.highpass_hz,
        "lowpass_hz": filters.lowpass_hz,
        "notch_mode": filters.notch_mode,
    }

def _serialize_filter_profiles(profiles) -> dict[str, Any]:
    return {
        "macro": _serialize_filter_settings(profiles.macro),
        "micro": _serialize_filter_settings(profiles.micro),
    }