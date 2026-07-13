from __future__ import annotations

import numpy as np


def build_gamma_masks(
    signal_length: int,
    fs: float,
    p1: float,
    n2: float,
    invalid_mask: np.ndarray | None = None,
    min_clean_baseline_ms: float = 250.0,
) -> dict[str, object]:
    if invalid_mask is None:
        invalid = np.zeros(signal_length, dtype=bool)
    else:
        invalid = np.asarray(invalid_mask, dtype=bool).ravel()
        if invalid.size != signal_length:
            raise ValueError(
                f"invalid_mask length {invalid.size} does not match gamma signal length {signal_length}"
            )

    pre_start = max(1, int(np.ceil(p1 - fs / 2.0)) + 1)
    pre_stop = min(signal_length, int(np.floor(p1)) - 1)
    post_start = max(1, int(np.ceil(n2)) + 1)
    post_stop = min(signal_length, int(np.floor(n2 + fs / 2.0)))

    pre_mask = np.zeros(signal_length, dtype=bool)
    post_mask = np.zeros(signal_length, dtype=bool)
    if pre_stop >= pre_start:
        pre_mask[pre_start - 1 : pre_stop] = True
    if post_stop >= post_start:
        post_mask[post_start - 1 : post_stop] = True

    baseline_unmasked = pre_mask | post_mask
    baseline_mask = baseline_unmasked & ~invalid
    search_mask = pre_mask & ~invalid
    clean_baseline_duration_ms = 1000.0 * np.sum(baseline_mask) / fs

    return {
        "invalid_mask": invalid,
        "baseline_mask": baseline_mask,
        "search_mask": search_mask,
        "pre_mask": pre_mask,
        "post_mask": post_mask,
        "masked_samples": int(np.sum(invalid & (baseline_unmasked | pre_mask))),
        "clean_baseline_samples": int(np.sum(baseline_mask)),
        "clean_baseline_duration_ms": float(clean_baseline_duration_ms),
        "min_clean_baseline_ms": float(min_clean_baseline_ms),
        "evaluable": bool(clean_baseline_duration_ms >= min_clean_baseline_ms and np.any(search_mask)),
    }


__all__ = ["build_gamma_masks"]
