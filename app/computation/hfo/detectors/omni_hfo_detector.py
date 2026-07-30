from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


DETECTOR_STE = "ste"
DETECTOR_MNI = "mni"
DETECTOR_HILBERT = "hilbert"
DEFAULT_CANDIDATE_DETECTORS = (DETECTOR_STE, DETECTOR_MNI, DETECTOR_HILBERT)
OMNI_TARGET_FS_HZ = 1000.0
OMNI_EVENT_WINDOW_MS = 2000.0
OMNI_1000HZ_UPPER_FREQ_HZ = 300.0


@dataclass
class HFOCandidate:
    channel: str
    detector: str
    start_sample: int
    end_sample: int


def detect_candidates_from_array(
    data_uv: np.ndarray,
    fs: float,
    channel_names: list[str],
    *,
    active_detectors: Iterable[str],
    low_freq_hz: float,
    high_freq_hz: float,
    threshold_sigma: float = 5.0,
    min_duration_ms: float = 6.0,
    merge_gap_ms: float = 10.0,
    min_cycles: float = 6.0,
    mni_seed: int | None = None,
    detector_parameters: dict | None = None,
    assume_filtered: bool = False,
) -> list[HFOCandidate]:
    """
    Run Omni's legacy candidate detectors on an in-memory channel x sample matrix.

    This intentionally replaces Omni's EDF traversal/loading layer. It preserves
    the STE/MNI/HIL parameterization while letting the GUI provide data, units,
    selected channels, timing, notch behavior, and resampling.
    """
    try:
        from HFODetector import hil, mni, ste
    except ImportError as exc:
        raise RuntimeError(
            "HFO candidate detection requires the optional HFODetector package. "
            "Install it before running Omni-style HFO detection."
        ) from exc

    matrix = np.asarray(data_uv, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("HFO candidate data must be a 2D channel x sample array.")
    if matrix.shape[0] != len(channel_names):
        raise ValueError("Channel name count does not match HFO candidate data.")
    if float(fs) <= 0.0:
        raise ValueError("Sampling frequency must be positive.")

    channels = np.asarray([str(name) for name in channel_names], dtype=object)
    active = {str(name).strip().lower() for name in active_detectors}
    candidates: list[HFOCandidate] = []
    filter_freq = [float(low_freq_hz), float(high_freq_hz)]
    threshold_sigma = float(threshold_sigma)
    min_duration_s = max(0.0, float(min_duration_ms)) / 1000.0
    mni_hilbert_min_duration_s = max(min_duration_s, 10e-3)
    merge_gap_s = max(0.0, float(merge_gap_ms)) / 1000.0
    min_cycles_int = max(0, int(round(float(min_cycles))))
    params = detector_parameters if isinstance(detector_parameters, dict) else {}
    ste_params = params.get(DETECTOR_STE, {}) if isinstance(params.get(DETECTOR_STE, {}), dict) else {}
    mni_params = params.get(DETECTOR_MNI, {}) if isinstance(params.get(DETECTOR_MNI, {}), dict) else {}
    hil_params = params.get(DETECTOR_HILBERT, {}) if isinstance(params.get(DETECTOR_HILBERT, {}), dict) else {}

    if DETECTOR_STE in active:
        ste_detector = ste.STEDetector(
            sample_freq=float(fs),
            filter_freq=filter_freq,
            rms_window=_float_param(ste_params, "rms_window_s", 3e-3),
            min_window=_float_param(ste_params, "min_window_s", min_duration_s),
            min_gap=_float_param(ste_params, "min_gap_s", merge_gap_s),
            epoch_len=_int_param(ste_params, "epoch_len", 600),
            min_osc=_int_param(ste_params, "min_osc", min_cycles_int),
            rms_thres=_float_param(ste_params, "rms_thres", threshold_sigma),
            peak_thres=_float_param(ste_params, "peak_thres", 3),
            n_jobs=_int_param(ste_params, "n_jobs", 1),
            front_num=1,
        )
        candidates.extend(_run_detector(ste_detector, matrix, channels, DETECTOR_STE, assume_filtered=assume_filtered))

    if DETECTOR_MNI in active:
        mni_filter_freq = [
            int(round(float(low_freq_hz))),
            int(round(float(high_freq_hz))),
        ]
        mni_detector = mni.MNIDetector(
            float(fs),
            filter_freq=mni_filter_freq,
            epoch_time=_float_param(mni_params, "epoch_time_s", 10),
            epo_CHF=_float_param(mni_params, "epo_chf_hz", 60),
            per_CHF=_float_param(mni_params, "per_chf", 95 / 100),
            min_win=_float_param(mni_params, "min_win_s", mni_hilbert_min_duration_s),
            min_gap=_float_param(mni_params, "min_gap_s", merge_gap_s),
            thrd_perc=_float_param(mni_params, "threshold_percentile", 99.9999 / 100),
            base_seg=_float_param(mni_params, "base_seg_s", 125e-3),
            base_shift=_float_param(mni_params, "base_shift_s", 0.5),
            base_thrd=_float_param(mni_params, "base_threshold", 0.67),
            base_min=_int_param(mni_params, "base_min", 5),
            n_jobs=_int_param(mni_params, "n_jobs", 1),
            front_num=1,
            seed=mni_seed,
        )
        candidates.extend(_run_detector(mni_detector, matrix, channels, DETECTOR_MNI, assume_filtered=assume_filtered))

    if DETECTOR_HILBERT in active:
        hil_detector = hil.HILDetector(
            sample_freq=float(fs),
            filter_freq=filter_freq,
            sd_thres=_float_param(hil_params, "sd_threshold", threshold_sigma),
            min_window=_float_param(hil_params, "min_window_s", mni_hilbert_min_duration_s),
            epoch_len=_float_param(hil_params, "epoch_len_s", 3600),
            n_jobs=_int_param(hil_params, "n_jobs", 1),
            front_num=1,
        )
        candidates.extend(_run_detector(hil_detector, matrix, channels, DETECTOR_HILBERT, assume_filtered=assume_filtered))

    candidates.sort(key=lambda event: (event.channel.casefold(), event.start_sample, event.end_sample, event.detector))
    return candidates


def _float_param(params: dict, key: str, default: float) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return value if np.isfinite(value) else float(default)


def _int_param(params: dict, key: str, default: int) -> int:
    try:
        return int(round(float(params.get(key, default))))
    except (TypeError, ValueError):
        return int(default)


def extract_event_waveforms(
    data_uv: np.ndarray,
    candidates: list[HFOCandidate],
    channel_names: list[str],
    *,
    window_samples: int,
) -> tuple[np.ndarray, list[int], list[int], list[bool]]:
    matrix = np.asarray(data_uv, dtype=float)
    name_to_idx = {str(name): idx for idx, name in enumerate(channel_names)}
    waveforms = np.zeros((len(candidates), int(window_samples)), dtype=float)
    real_starts: list[int] = []
    real_ends: list[int] = []
    is_boundaries: list[bool] = []

    for event_idx, candidate in enumerate(candidates):
        channel_idx = name_to_idx.get(str(candidate.channel))
        if channel_idx is None:
            real_starts.append(0)
            real_ends.append(0)
            is_boundaries.append(True)
            continue
        real_start, real_end, is_boundary = centered_window_bounds(
            int(candidate.start_sample),
            int(candidate.end_sample),
            int(matrix.shape[1]),
            int(window_samples),
        )
        segment = matrix[int(channel_idx), int(real_start):int(real_end)]
        if segment.size:
            waveforms[event_idx, : min(segment.size, int(window_samples))] = segment[: int(window_samples)]
        real_starts.append(int(real_start))
        real_ends.append(int(real_end))
        is_boundaries.append(bool(is_boundary))

    return waveforms, real_starts, real_ends, is_boundaries


def centered_window_bounds(start: int, end: int, length: int, window_samples: int) -> tuple[int, int, bool]:
    window_samples = max(1, int(window_samples))
    length = max(0, int(length))
    if length <= window_samples:
        return 0, length, True
    if int(start) < window_samples:
        return 0, window_samples, True
    if int(end) > length - window_samples:
        return length - window_samples, length, True
    center = int(0.5 * (int(start) + int(end)))
    half = window_samples // 2
    real_start = center - half
    real_end = real_start + window_samples
    return int(real_start), int(real_end), False


def _run_detector(
    detector,
    data_uv: np.ndarray,
    channels: np.ndarray,
    detector_name: str,
    *,
    assume_filtered: bool = False,
) -> list[HFOCandidate]:
    channel_names, start_end_by_channel = detector.detect_multi_channels(
        data_uv,
        channels,
        filtered=bool(assume_filtered),
    )
    events: list[HFOCandidate] = []
    for channel_name, start_end in zip(channel_names, start_end_by_channel):
        arr = np.asarray(start_end, dtype=float)
        if arr.size == 0:
            continue
        arr = np.atleast_2d(arr)
        for start, end in arr[:, :2]:
            events.append(
                HFOCandidate(
                    channel=str(channel_name),
                    detector=str(detector_name),
                    start_sample=int(round(float(start))),
                    end_sample=int(round(float(end))),
                )
            )
    return events
