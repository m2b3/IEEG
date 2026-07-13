from __future__ import annotations

import numpy as np


def classify_neighbor_context(
    spike_locations: np.ndarray,
    target_index: int,
    p1_abs: float,
    n2_abs: float,
    fs: float,
    window_ms: float = 500.0,
) -> dict[str, object]:
    locations = np.asarray(spike_locations, dtype=float).ravel()
    other_indices = np.setdiff1d(np.arange(1, locations.size + 1), np.asarray([target_index]), assume_unique=False)
    other_peaks = locations[other_indices - 1]

    preceding = other_peaks[other_peaks < p1_abs]
    following = other_peaks[other_peaks > n2_abs]
    preceding_latency_ms = np.nan if preceding.size == 0 else 1000.0 * (p1_abs - np.max(preceding)) / fs
    following_latency_ms = np.nan if following.size == 0 else 1000.0 * (np.min(following) - n2_abs) / fs

    context_start = p1_abs - window_ms * fs / 1000.0
    context_end = n2_abs + window_ms * fs / 1000.0
    neighbor_mask = (locations[other_indices - 1] >= context_start) & (locations[other_indices - 1] <= context_end)
    neighbor_indices = other_indices[neighbor_mask]

    return {
        "neighbor_indices": neighbor_indices,
        "neighbor_count": int(neighbor_indices.size),
        "preceding_latency_ms": float(preceding_latency_ms),
        "following_latency_ms": float(following_latency_ms),
        "primary_evaluable": bool(neighbor_indices.size == 0),
    }


__all__ = ["classify_neighbor_context"]
