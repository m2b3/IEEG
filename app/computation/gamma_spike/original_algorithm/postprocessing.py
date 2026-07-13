"""
Python translation of Matlab_version/postprocessing.m.

The function keeps the MATLAB behavior intentionally close because this module
is part of the step-by-step validation path. It returns per-channel spike
locations after the artifact/co-detection and burst-spacing cleanup.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Mapping, Sequence

import numpy as np

from .spike_detector_hilbert_v25 import DetectorOutput


def postprocessing(
    out: DetectorOutput | Mapping[str, Sequence[float]],
    fs: float,
    num_chans: int,
) -> list[np.ndarray]:
    """
    Post-process Janca detector detections.

    Parameters
    ----------
    out:
        Janca detector output with MATLAB-like fields: pos, dur, chan, con,
        weight, pdf. ``pos`` is in seconds and ``chan`` is 1-based.
    fs:
        Sampling frequency in Hz.
    num_chans:
        Number of channels in the montage/data.

    Returns
    -------
    list[np.ndarray]
        One list entry per channel. Each array contains retained spike sample
        positions for that channel, matching MATLAB's ``out_ch`` cells.
    """

    out_fields = _out_fields(out)
    pos = out_fields[0]
    chan = out_fields[2].astype(int)
    threshold_sec = 0.300

    if pos.size > 1:
        out_fields = _remove_codetections_matlab_style(out_fields, num_chans)
        pos = out_fields[0]
        chan = out_fields[2].astype(int)

        burst_thresh = threshold_sec * fs
        out_ch: list[np.ndarray] = []
        for channel in range(1, num_chans + 1):
            chan_ind = np.flatnonzero(chan == channel)
            spike_pos = _matlab_round(pos[chan_ind] * fs).astype(float)

            if spike_pos.size > 1:
                inter_lr = np.diff(spike_pos)
                inter_lr = np.r_[-(spike_pos[0] - spike_pos[1]), inter_lr]
                too_close_lr = inter_lr < burst_thresh

                inter_rl = -np.diff(spike_pos[::-1])
                inter_rl = np.r_[-(spike_pos[-2] - spike_pos[-1]), inter_rl]
                too_close_rl = (inter_rl < burst_thresh)[::-1]

                spike_pos = spike_pos[~(too_close_lr | too_close_rl)]

            out_ch.append(spike_pos)
        return out_ch

    out_ch = [np.asarray([], dtype=float) for _ in range(num_chans)]
    if chan.size:
        channel_index = int(chan[0]) - 1
        if 0 <= channel_index < num_chans:
            # MATLAB's single-detection branch stores out.pos directly, not
            # round(out.pos * fs). Preserve that behavior for parity.
            out_ch[channel_index] = np.asarray([pos[0]], dtype=float)
    return out_ch


def _out_fields(out: DetectorOutput | Mapping[str, Sequence[float]]) -> list[np.ndarray]:
    names = ["pos", "dur", "chan", "con", "weight", "pdf"]
    if is_dataclass(out):
        available = {field.name for field in fields(out)}
        missing = [name for name in names if name not in available]
        if missing:
            raise ValueError(f"missing output fields: {missing}")
        return [np.asarray(getattr(out, name)).copy() for name in names]

    missing = [name for name in names if name not in out]
    if missing:
        raise ValueError(f"missing output fields: {missing}")
    return [np.asarray(out[name]).copy() for name in names]


def _remove_codetections_matlab_style(out_fields: list[np.ndarray], num_chans: int) -> list[np.ndarray]:
    pos = out_fields[0]
    unique_pos = np.unique(pos)
    if unique_pos.size < 2:
        return out_fields

    counts, _ = np.histogram(pos, bins=unique_pos)
    spike_bins_to_remove = np.flatnonzero(counts > np.ceil(num_chans / 2)) + 1
    if spike_bins_to_remove.size == 0:
        return out_fields

    keep = np.ones(pos.shape, dtype=bool)
    for spike_bin in spike_bins_to_remove:
        keep &= pos != spike_bin
    return [values[keep] for values in out_fields]


def _matlab_round(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


__all__ = ["postprocessing"]
