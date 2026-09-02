# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""HFO candidate detector wrappers."""

from app.computation.hfo.detectors.omni_hfo_detector import (
    DEFAULT_CANDIDATE_DETECTORS,
    DETECTOR_HILBERT,
    DETECTOR_MNI,
    DETECTOR_STE,
    OMNI_1000HZ_UPPER_FREQ_HZ,
    OMNI_EVENT_WINDOW_MS,
    OMNI_TARGET_FS_HZ,
    HFOCandidate,
    centered_window_bounds,
    detect_candidates_from_array,
    extract_event_waveforms,
)

__all__ = [
    "DEFAULT_CANDIDATE_DETECTORS",
    "DETECTOR_HILBERT",
    "DETECTOR_MNI",
    "DETECTOR_STE",
    "HFOCandidate",
    "OMNI_1000HZ_UPPER_FREQ_HZ",
    "OMNI_EVENT_WINDOW_MS",
    "OMNI_TARGET_FS_HZ",
    "centered_window_bounds",
    "detect_candidates_from_array",
    "extract_event_waveforms",
]
