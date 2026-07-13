"""
Python translation of Matlab_version/compute_spike_boundary.m.

Returns MATLAB-style 1-based sample indices: p1, n1, n2.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks


def compute_spike_boundary(spike_ref: np.ndarray, fs: float) -> tuple[float, float, float]:
    """Detect spike onset (p1), peak (n1), and end (n2)."""
    spike_ref = np.asarray(spike_ref, dtype=float).ravel()
    if spike_ref.size == 0:
        raise ValueError("spike_ref must not be empty")

    num_samples = fs * 0.3
    spike_index = _matlab_round(num_samples / 4) + 1
    downsample_ratio = 2 * _matlab_round(fs / 200)

    start_check = max(1, int(spike_index - downsample_ratio))
    stop_check = min(spike_ref.size, int(spike_index + downsample_ratio - 1))
    if stop_check < start_check:
        raise ValueError("invalid spike peak search window")

    peak_window = spike_ref[start_check - 1 : stop_check]
    ind = int(np.argmax(np.abs(peak_window)) + start_check)

    prom = 22
    high_prom = 0
    local_left: int | None = None
    local_right: int | None = None
    found = False

    while not found:
        prom -= 2

        if spike_ref[ind - 1] > 0:
            local_extrema = _islocalmin(spike_ref, prom)
        else:
            local_extrema = _islocalmax(spike_ref, prom)

        local_left = _last_true_1based(local_extrema[: ind - 1])
        right_offset = _first_true_1based(local_extrema[ind:])
        local_right = ind + right_offset if right_offset is not None else None

        found = local_left is not None and local_right is not None
        if found:
            width_sec = (local_right - local_left) / fs
            if width_sec > 0.07 or width_sec < 0.02:
                found = False

            if (local_right - ind) / fs < 0.01 or (ind - local_left) / fs < 0.01:
                found = False
                high_prom += 1
                if high_prom == 1:
                    prom = 32
                elif high_prom == 2:
                    prom = 42
                elif high_prom == 3:
                    prom = 52

        if prom == 0:
            break

    if local_left is None or local_right is None:
        raise RuntimeError("could not estimate p1 and p2 spike boundaries")

    p1 = float(local_left)
    n1 = float(ind)
    p2 = float(local_right)

    prom = 2
    n2: float | None = None
    while n2 is None or (n2 - p2) / fs < 0.01 or (n2 - p1) / fs > 0.15:
        n2 = None
        prom += 2

        if spike_ref[ind - 1] < 0:
            local_extrema = _islocalmin(spike_ref, prom)
        else:
            local_extrema = _islocalmax(spike_ref, prom)

        p2_index = int(p2)
        offset = _first_true_1based(local_extrema[p2_index:])
        if offset is not None:
            # MATLAB code uses ind + min(find(local_min(p2+1:end)==1)).
            n2 = float(ind + offset)

        if prom == 50:
            break

    if n2 is None:
        n2 = p1 + fs * 0.15

    return p1, n1, float(n2)


def _islocalmax(values: np.ndarray, prominence: float) -> np.ndarray:
    marker = np.zeros(values.shape, dtype=bool)
    peaks, _ = find_peaks(values, prominence=prominence)
    marker[peaks] = True
    return marker


def _islocalmin(values: np.ndarray, prominence: float) -> np.ndarray:
    marker = np.zeros(values.shape, dtype=bool)
    peaks, _ = find_peaks(-values, prominence=prominence)
    marker[peaks] = True
    return marker


def _last_true_1based(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return None
    return int(idx[-1] + 1)


def _first_true_1based(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return None
    return int(idx[0] + 1)


def _matlab_round(value: float) -> int:
    return int(np.sign(value) * np.floor(abs(value) + 0.5))


__all__ = ["compute_spike_boundary"]
