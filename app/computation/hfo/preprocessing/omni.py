"""Omni-compatible HFO input preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from app.computation.rei.algorithm import apply_notch_by_channel


@dataclass
class HFOInput:
    data_uv: np.ndarray
    fs: float
    channel_names: list[str]
    data_start_s: float
    analysis_window_s: tuple[float, float]
    notch_modes_by_channel: dict[str, str] = field(default_factory=dict)
    bad_channels: list[str] = field(default_factory=list)
    selected_channels: list[str] = field(default_factory=list)
    reference_mode: str = "none"
    bipolar_pairs: list[tuple[str, str]] = field(default_factory=list)
    source_file_path: str | None = None
    input_boundary: str = "unknown"
    processing_order: list[str] = field(default_factory=list)
    preprocessing_log: list[str] = field(default_factory=list)


def prepare_hfo_input_from_file(
    file_path: str | Path,
    *,
    analysis_window_s: tuple[float, float],
    selected_channels: Iterable[str] | None = None,
    bad_channels: Iterable[str] | None = None,
    reference_mode: str = "none",
    bipolar_pairs: Sequence[tuple[str, str]] | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
) -> HFOInput:
    try:
        import mne
    except ImportError as exc:
        raise RuntimeError("HFO file loading requires mne.") from exc

    path = Path(file_path)
    suffix = path.suffix.casefold()
    if suffix == ".edf":
        raw = mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
    elif suffix == ".fif":
        raw = mne.io.read_raw_fif(path, preload=False, verbose="ERROR")
    else:
        raw = mne.io.read_raw(path, preload=False, verbose="ERROR")
    try:
        fs = float(raw.info["sfreq"])
        start_s, stop_s = map(float, analysis_window_s)
        start_sample = max(0, int(round(start_s * fs)))
        stop_sample = min(int(raw.n_times), int(round(stop_s * fs)))
        if stop_sample <= start_sample:
            raise ValueError("HFO analysis interval contains no samples.")
        channel_names = [str(name) for name in raw.ch_names]
        data_uv = np.asarray(raw.get_data(start=start_sample, stop=stop_sample), dtype=float) * 1e6
    finally:
        raw.close()

    prepared = prepare_hfo_input_from_array(
        data_uv=data_uv,
        fs=fs,
        channel_names=channel_names,
        data_start_s=start_s,
        analysis_window_s=analysis_window_s,
        data_is_interval_sliced=True,
        selected_channels=selected_channels,
        bad_channels=bad_channels,
        reference_mode=reference_mode,
        bipolar_pairs=bipolar_pairs,
        notch_modes_by_channel=notch_modes_by_channel,
    )
    prepared.source_file_path = str(path)
    prepared.input_boundary = "backend file loader read raw file segment and prepared the HFO signal array"
    prepared.preprocessing_log.insert(0, f"loaded file: {path}")
    return prepared


def prepare_hfo_input_from_array(
    *,
    data_uv: np.ndarray,
    fs: float,
    channel_names: list[str] | tuple[str, ...],
    data_start_s: float,
    analysis_window_s: tuple[float, float],
    data_is_interval_sliced: bool = True,
    selected_channels: Iterable[str] | None = None,
    bad_channels: Iterable[str] | None = None,
    reference_mode: str = "none",
    bipolar_pairs: Sequence[tuple[str, str]] | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
) -> HFOInput:
    matrix = np.asarray(data_uv, dtype=float)
    names = [str(name) for name in channel_names]
    if matrix.ndim != 2:
        raise ValueError("HFO data must be a 2D channel x sample array.")
    if matrix.shape[0] != len(names):
        raise ValueError("Channel name count does not match HFO data rows.")
    if matrix.shape[0] == 0 or matrix.shape[1] < 2:
        raise ValueError("Not enough data for HFO detection.")
    if float(fs) <= 0:
        raise ValueError("Sampling frequency must be positive.")

    log: list[str] = ["input units: microvolts"]
    processing_order: list[str] = ["validate prepared microvolt signal array"]
    start_s, stop_s = map(float, analysis_window_s)
    if stop_s <= start_s:
        raise ValueError("HFO analysis end must be after analysis start.")

    if not data_is_interval_sliced:
        rel_start = max(0, int(round((start_s - float(data_start_s)) * float(fs))))
        rel_stop = min(int(matrix.shape[1]), int(round((stop_s - float(data_start_s)) * float(fs))))
        if rel_stop <= rel_start:
            raise ValueError("HFO analysis interval contains no samples in the provided data.")
        matrix = matrix[:, rel_start:rel_stop]
        data_start_s = start_s
        processing_order.append("interval slicing")
        log.append(f"sliced analysis interval: {start_s:g}-{stop_s:g} s")
    else:
        processing_order.append("accept caller-sliced analysis interval")
        log.append("analysis interval already sliced by caller")

    matrix, names = _select_channels(matrix, names, selected_channels, log)
    processing_order.append("channel selection")
    matrix, names, excluded_bad = _exclude_bad_channels(matrix, names, bad_channels, log)
    processing_order.append("bad-channel exclusion")
    matrix, names = _apply_reference_or_bipolar(
        matrix,
        names,
        reference_mode=str(reference_mode or "none"),
        bipolar_pairs=list(bipolar_pairs or []),
        log=log,
    )
    reference_step = (
        "no backend reference/montage transform"
        if str(reference_mode or "none").strip().lower() in {"", "none", "original"}
        else "backend reference/montage transform"
    )
    processing_order.append(reference_step)
    notch_modes = {
        str(name): str((notch_modes_by_channel or {}).get(str(name), "Off"))
        for name in names
    }
    notched = apply_notch_by_channel(matrix, float(fs), [notch_modes[name] for name in names])
    processing_order.append("GUI notch filtering")
    active_notches = sorted({mode for mode in notch_modes.values() if mode != "Off"})
    log.append(f"notch modes: {active_notches if active_notches else ['Off']}")

    return HFOInput(
        data_uv=np.asarray(notched, dtype=float),
        fs=float(fs),
        channel_names=names,
        data_start_s=float(data_start_s),
        analysis_window_s=(start_s, stop_s),
        notch_modes_by_channel=notch_modes,
        bad_channels=excluded_bad,
        selected_channels=[str(name) for name in selected_channels] if selected_channels is not None else list(names),
        reference_mode=str(reference_mode or "none"),
        bipolar_pairs=[(str(a), str(b)) for a, b in (bipolar_pairs or [])],
        input_boundary=(
            "GUI/file layer provides selected, referenced/montaged, microvolt signal array"
            if data_is_interval_sliced
            else "backend received a wider microvolt signal array and sliced it"
        ),
        processing_order=processing_order,
        preprocessing_log=log,
    )


def _select_channels(
    matrix: np.ndarray,
    names: list[str],
    selected_channels: Iterable[str] | None,
    log: list[str],
) -> tuple[np.ndarray, list[str]]:
    if selected_channels is None:
        log.append(f"selected channels: all ({len(names)})")
        return matrix, names
    selected = [str(name) for name in selected_channels]
    name_to_idx = {name: idx for idx, name in enumerate(names)}
    missing = [name for name in selected if name not in name_to_idx]
    if missing:
        raise ValueError(f"Selected HFO channels are missing from data: {missing}")
    indices = [name_to_idx[name] for name in selected]
    log.append(f"selected channels: {len(indices)}")
    return matrix[indices], selected


def _exclude_bad_channels(
    matrix: np.ndarray,
    names: list[str],
    bad_channels: Iterable[str] | None,
    log: list[str],
) -> tuple[np.ndarray, list[str], list[str]]:
    bad = {str(name) for name in (bad_channels or [])}
    if not bad:
        log.append("bad-channel exclusion: none")
        return matrix, names, []
    keep = [idx for idx, name in enumerate(names) if name not in bad]
    excluded = [name for name in names if name in bad]
    if not keep:
        raise ValueError("All selected HFO channels were marked bad.")
    log.append(f"bad-channel exclusion: {len(excluded)}")
    return matrix[keep], [names[idx] for idx in keep], excluded


def _apply_reference_or_bipolar(
    matrix: np.ndarray,
    names: list[str],
    *,
    reference_mode: str,
    bipolar_pairs: list[tuple[str, str]],
    log: list[str],
) -> tuple[np.ndarray, list[str]]:
    mode = reference_mode.strip().lower()
    if mode in {"", "none", "original"}:
        log.append("reference/montage: none")
        return matrix, names
    if mode in {"average", "common_average"}:
        if matrix.shape[0] < 2:
            raise ValueError("Average reference requires at least two HFO channels.")
        log.append("reference/montage: average")
        return matrix - np.mean(matrix, axis=0, keepdims=True), names
    if mode == "bipolar":
        if not bipolar_pairs:
            raise ValueError("Bipolar HFO montage requires channel pairs.")
        name_to_idx = {name: idx for idx, name in enumerate(names)}
        rows: list[np.ndarray] = []
        pair_names: list[str] = []
        missing: list[str] = []
        for a, b in bipolar_pairs:
            left = str(a)
            right = str(b)
            if left not in name_to_idx or right not in name_to_idx:
                missing.extend([name for name in (left, right) if name not in name_to_idx])
                continue
            rows.append(matrix[name_to_idx[left]] - matrix[name_to_idx[right]])
            pair_names.append(f"{left}-{right}")
        if missing:
            raise ValueError(f"Bipolar HFO montage channels are missing from data: {sorted(set(missing))}")
        if not rows:
            raise ValueError("Bipolar HFO montage produced no channels.")
        log.append(f"reference/montage: bipolar ({len(rows)} pairs)")
        return np.asarray(rows, dtype=float), pair_names
    raise ValueError(f"Unsupported HFO reference/montage mode: {reference_mode}")
