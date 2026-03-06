from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from typing import Any

from app.annotations import Annotation


SESSION_VERSION = 1

def default_session_path_for(data_file: Path) -> Path:
    """
    patient01.edf -> patient01.haly.session.json
    """
    return data_file.with_suffix(".haly.session.json")


def build_session_dict(main_window) -> dict[str, Any]:
    """
    Collect the current state of the UI to save.
    """
    viewer = main_window.viewer

    annotations = []
    for a in viewer.get_annotations():
        annotations.append(asdict(a))

    return {
        "version": SESSION_VERSION,
        "data_file": str(main_window.loaded_file) if main_window.loaded_file else None,
        "annotations": annotations,
        "hidden_channels": sorted(list(getattr(viewer, "_hidden_channels", set()))),
        "bad_channels": sorted(list(getattr(viewer, "_bad_channels", set()))),
    }


def save_session(path: Path, main_window) -> None:
    """
    Write the session JSON file.
    """
    payload = build_session_dict(main_window)

    path = Path(path)

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def load_session(path: Path) -> dict[str, Any]:
    """
    Read a session JSON file.
    """
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
    


def apply_session(main_window, payload: dict[str, Any], *, source_path: Path | None = None) -> None:
    """
    Apply a session payload to the current loaded viewer.
    Assumes an EEG file is already loaded in main_window.
    """
    if int(payload.get("version", 0)) != SESSION_VERSION:
        raise ValueError(f"Unsupported session version: {payload.get('version')}")

    # Optional safety check: ensure session matches the currently loaded EEG
    data_file_in_session = payload.get("data_file")
    if main_window.loaded_file is not None and data_file_in_session:
        if str(main_window.loaded_file) != str(data_file_in_session):
            # You can change this to a warning dialog if you prefer
            raise ValueError(
                "Session file does not match the currently loaded EEG file.\n"
                f"Loaded EEG: {main_window.loaded_file}\n"
                f"Session EEG: {data_file_in_session}"
            )

    # Rebuild annotations
    annos: list[Annotation] = []
    for d in payload.get("annotations", []):
        annos.append(
            Annotation(
                id=str(d.get("id", "")),
                kind=str(d.get("kind", "Other")),
                t_start=float(d.get("t_start", 0.0)),
                t_end=float(d.get("t_end", 0.0)),
                abs_channel=(None if d.get("abs_channel", None) is None else int(d["abs_channel"])),
                note=str(d.get("note", "")),
            )
        )

    hidden = set(payload.get("hidden_channels", []) or [])
    bad = set(payload.get("bad_channels", []) or [])

    # Apply to viewer (we’ll add these methods in plot.py in step 2)
    main_window.viewer.set_annotations(annos)
    main_window.viewer.set_hidden_channels(hidden)
    main_window.viewer.set_bad_channels(bad)

    # After applying, consider it clean and “bound” to the session file
    if source_path is not None:
        main_window.session_path = Path(source_path)
    main_window.session_dirty = False
    main_window._update_window_title()