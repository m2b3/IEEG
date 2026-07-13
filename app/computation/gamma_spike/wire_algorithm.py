"""GUI-facing gamma spike detector wrapper."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from app.computation.gamma_spike.original_algorithm.segmented_pipeline import DEFAULT_SETTINGS
from app.computation.gamma_spike.original_algorithm.compute_gamma import compute_gamma
from app.computation.gamma_spike.original_algorithm.compute_spike_boundary import (
    compute_spike_boundary,
)
from app.computation.gamma_spike.original_algorithm.postprocessing import postprocessing
from app.computation.gamma_spike.original_algorithm.spike_detector_hilbert_v25 import (
    DetectorOutput,
    DetectorSettings,
    spike_detector_hilbert_v25,
)


@dataclass
class GammaSpikeEventResult:
    sample: float
    time_s: float
    boundary_p1_sample: float | None
    boundary_n1_sample: float | None
    boundary_n2_sample: float | None
    gamma_power: float | None
    gamma_frequency_hz: float | None
    gamma_duration_ms: float | None
    error: str | None = None


@dataclass
class GammaSpikeChannelResult:
    channel: str
    spike_count: int
    spike_samples: np.ndarray
    spike_times_s: np.ndarray
    events: list[GammaSpikeEventResult]


@dataclass
class GammaSpikeComputationResult:
    channels: list[GammaSpikeChannelResult]
    detector_output: DetectorOutput
    metadata: dict


def compute_gamma_spike_for_gui(
    *,
    data: np.ndarray,
    fs: float,
    channel_names: list[str],
    data_start_s: float,
    analysis_window_s: tuple[float, float],
    settings: str | DetectorSettings | None = None,
) -> GammaSpikeComputationResult:
    """
    Run the gamma spike detector from GUI-selected data.

    The GUI works with channel x sample arrays in microvolts, while the
    detector expects sample x channel arrays. This wrapper keeps that conversion
    and metadata bookkeeping out of the computation panel.
    """
    start_s, stop_s = analysis_window_s
    if stop_s <= start_s:
        raise ValueError("Gamma analysis end must be after analysis start.")
    if fs <= 0:
        raise ValueError("Sampling frequency must be positive.")

    matrix = np.asarray(data, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("Gamma spike data must be a 2D channel x sample array.")
    if matrix.shape[0] != len(channel_names):
        raise ValueError("Channel name count does not match gamma spike data.")
    if matrix.shape[0] == 0 or matrix.shape[1] < 2:
        raise ValueError("Not enough data for gamma spike detection.")

    detector_data = np.ascontiguousarray(matrix.T)
    detector_settings = DEFAULT_SETTINGS if settings is None else settings
    out, _discharges, _d_decim, _envelope, _background, _envelope_pdf = (
        spike_detector_hilbert_v25(detector_data, float(fs), settings=detector_settings)
    )
    per_channel_samples, qc = postprocessing(
        out,
        float(fs),
        len(channel_names),
        return_qc=True,
    )

    channels: list[GammaSpikeChannelResult] = []
    gamma_success_count = 0
    boundary_success_count = 0
    for channel_index, (name, spike_samples1) in enumerate(zip(channel_names, per_channel_samples)):
        samples1 = np.asarray(spike_samples1, dtype=float)
        samples0 = samples1 - 1.0
        times_s = data_start_s + samples0 / float(fs)
        events: list[GammaSpikeEventResult] = []
        for sample1, sample0, time_s in zip(samples1, samples0, times_s):
            event = _compute_spike_gamma_event(
                signal=matrix[channel_index, :],
                fs=float(fs),
                spike_sample1=float(sample1),
                gui_sample0=float(sample0),
                spike_time_s=float(time_s),
            )
            if event.boundary_p1_sample is not None:
                boundary_success_count += 1
            if event.gamma_power is not None:
                gamma_success_count += 1
            events.append(event)

        channels.append(
            GammaSpikeChannelResult(
                channel=str(name),
                spike_count=int(samples0.size),
                spike_samples=samples0,
                spike_times_s=times_s,
                events=events,
            )
        )

    return GammaSpikeComputationResult(
        channels=channels,
        detector_output=out,
        metadata={
            "data_start_s": float(data_start_s),
            "analysis_window_s": [float(start_s), float(stop_s)],
            "fs": float(fs),
            "n_channels": len(channel_names),
            "n_samples": int(matrix.shape[1]),
            "total_spikes": int(sum(channel.spike_count for channel in channels)),
            "boundary_success_count": int(boundary_success_count),
            "gamma_success_count": int(gamma_success_count),
            "detector_settings": (
                detector_settings if isinstance(detector_settings, str) else "DetectorSettings"
            ),
            "postprocessing_qc": _json_safe_qc(qc),
        },
    )


def _compute_spike_gamma_event(
    *,
    signal: np.ndarray,
    fs: float,
    spike_sample1: float,
    gui_sample0: float,
    spike_time_s: float,
) -> GammaSpikeEventResult:
    raw_signal = np.asarray(signal, dtype=float).ravel()
    n_samples = raw_signal.size
    spike_index1 = _matlab_round_scalar(spike_sample1)
    if spike_index1 < 1 or spike_index1 > n_samples:
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=None,
            boundary_n1_sample=None,
            boundary_n2_sample=None,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error="spike sample is outside the analysis window",
        )

    boundary_signal = _bandpass_filter(raw_signal, fs, 10.0, 60.0)
    boundary_start1 = _matlab_round_scalar(spike_sample1 - 0.075 * fs)
    boundary_stop1 = _matlab_round_scalar(spike_sample1 + 0.225 * fs)
    if boundary_start1 < 1 or boundary_stop1 > n_samples or boundary_stop1 <= boundary_start1:
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=None,
            boundary_n1_sample=None,
            boundary_n2_sample=None,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error="not enough data around spike for boundary detection",
        )

    try:
        p1_boundary, n1_boundary, n2_boundary = compute_spike_boundary(
            boundary_signal[boundary_start1 - 1 : boundary_stop1],
            fs,
        )
    except Exception as exc:
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=None,
            boundary_n1_sample=None,
            boundary_n2_sample=None,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=f"boundary detection failed: {exc}",
        )

    p1_abs1 = boundary_start1 + p1_boundary - 1.0
    n1_abs1 = boundary_start1 + n1_boundary - 1.0
    n2_abs1 = boundary_start1 + n2_boundary - 1.0
    gamma_start1 = _matlab_round_scalar(n1_abs1 - fs)
    gamma_stop1 = _matlab_round_scalar(n1_abs1 + fs)
    if gamma_start1 < 1 or gamma_stop1 > n_samples or gamma_stop1 <= gamma_start1:
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=p1_abs1 - 1.0,
            boundary_n1_sample=n1_abs1 - 1.0,
            boundary_n2_sample=n2_abs1 - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error="not enough data around spike for gamma analysis",
        )

    p1_gamma = p1_abs1 - (n1_abs1 - fs)
    n2_gamma = n2_abs1 - (n1_abs1 - fs)
    try:
        gamma_signal = _gamma_filter(raw_signal, fs)
        gamma, details = compute_gamma(
            gamma_signal[gamma_start1 - 1 : gamma_stop1],
            fs,
            p1_gamma,
            n2_gamma,
            return_details=True,
        )
    except Exception as exc:
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=p1_abs1 - 1.0,
            boundary_n1_sample=n1_abs1 - 1.0,
            boundary_n2_sample=n2_abs1 - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=f"gamma analysis failed: {exc}",
        )

    if not np.all(np.isfinite(gamma)):
        reason = str(details.get("exclusion_reason") or "gamma analysis not evaluable")
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=p1_abs1 - 1.0,
            boundary_n1_sample=n1_abs1 - 1.0,
            boundary_n2_sample=n2_abs1 - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=reason,
        )

    return GammaSpikeEventResult(
        sample=gui_sample0,
        time_s=spike_time_s,
        boundary_p1_sample=p1_abs1 - 1.0,
        boundary_n1_sample=n1_abs1 - 1.0,
        boundary_n2_sample=n2_abs1 - 1.0,
        gamma_power=float(gamma[0]),
        gamma_frequency_hz=float(gamma[1]),
        gamma_duration_ms=float(gamma[2]),
        error=None,
    )


def _bandpass_filter(values: np.ndarray, fs: float, low_hz: float, high_hz: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 8:
        return arr
    nyquist = 0.5 * float(fs)
    low = max(1e-6, float(low_hz))
    high = min(float(high_hz), nyquist - 1e-6)
    if low >= high:
        return arr
    b, a = signal.butter(4, [low, high], btype="bandpass", fs=float(fs))
    return signal.filtfilt(b, a, arr)


def _gamma_filter(values: np.ndarray, fs: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 8:
        return arr
    b_notch, a_notch = _notch_filter_coefficients(float(fs))
    notched = signal.filtfilt(b_notch, a_notch, arr)
    return _bandpass_filter(notched, fs, 30.0, 100.0)


def _notch_filter_coefficients(fs: float) -> tuple[np.ndarray, np.ndarray]:
    freq = 60.0
    r = 0.985
    b = np.array([1.0, -2.0 * np.cos(2.0 * np.pi * freq / fs), 1.0])
    a = np.array([1.0, -2.0 * r * np.cos(2.0 * np.pi * freq / fs), r * r])
    return b, a


def _matlab_round_scalar(value: float) -> int:
    return int(np.sign(value) * np.floor(abs(float(value)) + 0.5))


def _json_safe_qc(qc: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in qc.items():
        if isinstance(value, np.ndarray):
            safe[str(key)] = value.tolist()
        elif isinstance(value, list):
            safe[str(key)] = [
                item.tolist() if isinstance(item, np.ndarray) else item
                for item in value
            ]
        else:
            safe[str(key)] = value
    return safe


__all__ = [
    "GammaSpikeChannelResult",
    "GammaSpikeComputationResult",
    "GammaSpikeEventResult",
    "compute_gamma_spike_for_gui",
]
