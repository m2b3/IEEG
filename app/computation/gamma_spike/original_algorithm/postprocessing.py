"""
Python translation of matlab2/postprocessing.m.

The matlab2 version keeps the historical burst-filtered ``out_ch`` output and
adds a QC structure exposing common-mode and burst-removal masks.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Literal, Mapping, Sequence, overload

import numpy as np

from .spike_detector_hilbert_v25 import DetectorOutput


@overload
def postprocessing(
    out: DetectorOutput | Mapping[str, Sequence[float]],
    fs: float,
    num_chans: int,
    *,
    return_qc: Literal[False] = False,
) -> list[np.ndarray]:
    ...


@overload
def postprocessing(
    out: DetectorOutput | Mapping[str, Sequence[float]],
    fs: float,
    num_chans: int,
    *,
    return_qc: Literal[True],
) -> tuple[list[np.ndarray], dict[str, object]]:
    ...


def postprocessing(
    out: DetectorOutput | Mapping[str, Sequence[float]],
    fs: float,
    num_chans: int,
    *,
    return_qc: bool = False,
) -> list[np.ndarray] | tuple[list[np.ndarray], dict[str, object]]:
    out_fields = _out_fields(out)
    positions_sec = np.asarray(out_fields[0], dtype=float).ravel()
    channels = np.asarray(out_fields[2], dtype=float).ravel()

    coincidence_tolerance_sec = 0.010
    burst_tolerance_sec = 0.300
    out_ch = [np.asarray([], dtype=float) for _ in range(num_chans)]
    context_by_channel = [np.asarray([], dtype=float) for _ in range(num_chans)]
    removed_common_by_channel = [np.asarray([], dtype=float) for _ in range(num_chans)]
    removed_burst_by_channel = [np.asarray([], dtype=float) for _ in range(num_chans)]

    if positions_sec.size == 0:
        qc = _make_qc(
            np.asarray([], dtype=float),
            np.asarray([], dtype=float),
            np.zeros(0, dtype=bool),
            np.zeros(0, dtype=bool),
            np.zeros(0, dtype=bool),
            out_ch,
            context_by_channel,
            removed_common_by_channel,
            removed_burst_by_channel,
            coincidence_tolerance_sec,
            burst_tolerance_sec,
        )
        return (out_ch, qc) if return_qc else out_ch

    if positions_sec.size != channels.size:
        raise ValueError("out.pos and out.chan must have the same length")
    if np.any((channels < 1) | (channels > num_chans) | (channels != np.round(channels))):
        raise ValueError("detector channel indices must be integers in 1:num_chans")

    samples = _matlab_round(positions_sec * fs).astype(float)
    n_input = samples.size
    order = np.argsort(positions_sec, kind="mergesort")
    sorted_pos = positions_sec[order]
    sorted_channels = channels[order]
    common_sorted = np.zeros(n_input, dtype=bool)
    channel_threshold = int(np.ceil(num_chans / 2))

    cluster_start = 0
    while cluster_start < n_input:
        cluster_end = cluster_start
        anchor = sorted_pos[cluster_start]
        while cluster_end + 1 < n_input and sorted_pos[cluster_end + 1] - anchor <= coincidence_tolerance_sec:
            cluster_end += 1
        members = np.arange(cluster_start, cluster_end + 1)
        if np.unique(sorted_channels[members]).size >= channel_threshold:
            common_sorted[members] = True
        cluster_start = cluster_end + 1

    common_mode_mask = np.zeros(n_input, dtype=bool)
    common_mode_mask[order] = common_sorted

    burst_removal_mask = np.zeros(n_input, dtype=bool)
    burst_tolerance_samples = burst_tolerance_sec * fs
    for channel_index in range(1, num_chans + 1):
        channel_event_indices = np.flatnonzero((channels == channel_index) & ~common_mode_mask)
        local_order = np.argsort(samples[channel_event_indices], kind="mergesort")
        sorted_indices = channel_event_indices[local_order]
        sorted_samples = samples[sorted_indices]
        if sorted_samples.size > 1:
            close_pairs = np.diff(sorted_samples) < burst_tolerance_samples
            local_burst = np.zeros(sorted_samples.shape, dtype=bool)
            local_burst[:-1] |= close_pairs
            local_burst[1:] |= close_pairs
            burst_removal_mask[sorted_indices[local_burst]] = True

    retained_mask = ~common_mode_mask & ~burst_removal_mask

    for channel_index in range(1, num_chans + 1):
        channel_mask = channels == channel_index
        out_ch[channel_index - 1] = np.sort(samples[channel_mask & retained_mask])
        context_by_channel[channel_index - 1] = np.sort(samples[channel_mask & ~common_mode_mask])
        removed_common_by_channel[channel_index - 1] = np.sort(samples[channel_mask & common_mode_mask])
        removed_burst_by_channel[channel_index - 1] = np.sort(samples[channel_mask & burst_removal_mask])

    qc = _make_qc(
        samples,
        channels,
        common_mode_mask,
        burst_removal_mask,
        retained_mask,
        out_ch,
        context_by_channel,
        removed_common_by_channel,
        removed_burst_by_channel,
        coincidence_tolerance_sec,
        burst_tolerance_sec,
    )
    return (out_ch, qc) if return_qc else out_ch


def _out_fields(out: DetectorOutput | Mapping[str, Sequence[float]]) -> list[np.ndarray]:
    names = ["pos", "dur", "chan", "con", "weight", "pdf"]
    if is_dataclass(out):
        available = {field.name for field in fields(out)}
        missing = [name for name in ["pos", "chan"] if name not in available]
        if missing:
            raise ValueError(f"missing output fields: {missing}")
        return [np.asarray(getattr(out, name)).copy() if name in available else np.asarray([]) for name in names]

    missing = [name for name in ["pos", "chan"] if name not in out]
    if missing:
        raise ValueError(f"missing output fields: {missing}")
    return [np.asarray(out[name]).copy() if name in out else np.asarray([]) for name in names]


def _make_qc(
    samples: np.ndarray,
    channels: np.ndarray,
    common_mask: np.ndarray,
    burst_mask: np.ndarray,
    retained_mask: np.ndarray,
    output_by_channel: list[np.ndarray],
    context_by_channel: list[np.ndarray],
    common_by_channel: list[np.ndarray],
    burst_by_channel: list[np.ndarray],
    coincidence_tolerance_sec: float,
    burst_tolerance_sec: float,
) -> dict[str, object]:
    return {
        "input_detections": int(samples.size),
        "common_mode_removals": int(np.sum(common_mask)),
        "burst_removals": int(np.sum(burst_mask)),
        "non_common_mode_detections": int(np.sum(~common_mask)),
        "output_detections": int(np.sum(retained_mask)),
        "coincidence_tolerance_sec": coincidence_tolerance_sec,
        "burst_tolerance_sec": burst_tolerance_sec,
        "input_samples": samples,
        "input_channels": channels,
        "common_mode_mask": common_mask,
        "burst_removal_mask": burst_mask,
        "retained_mask": retained_mask,
        "output_detections_by_channel": output_by_channel,
        "context_detections_by_channel": context_by_channel,
        "removed_common_mode_by_channel": common_by_channel,
        "removed_burst_by_channel": burst_by_channel,
    }


def _matlab_round(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


__all__ = ["postprocessing"]
