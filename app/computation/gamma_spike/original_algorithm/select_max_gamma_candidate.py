from __future__ import annotations

import numpy as np


def select_max_gamma_candidate(candidate_powers: np.ndarray) -> int | None:
    powers = np.asarray(candidate_powers, dtype=float).ravel()
    if powers.size == 0:
        return None
    valid = np.isfinite(powers)
    if not np.any(valid):
        return None
    valid_indices = np.flatnonzero(valid)
    relative_index = int(np.argmax(powers[valid]))
    return int(valid_indices[relative_index])


__all__ = ["select_max_gamma_candidate"]
