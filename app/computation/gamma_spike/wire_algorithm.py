"""GUI-facing gamma spike detector wrapper."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

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
    out, _discharges, _d_decim, _envelope, _background, _envelope_pdf = (
        spike_detector_hilbert_v25(detector_data, float(fs), settings=settings)
    )
    per_channel_samples = postprocessing(out, float(fs), len(channel_names))

    channels: list[GammaSpikeChannelResult] = []
    gamma_success_count = 0
    boundary_success_count = 0
    for channel_index, (name, spike_samples) in enumerate(zip(channel_names, per_channel_samples)):
        samples = np.asarray(spike_samples, dtype=float)
        times_s = data_start_s + samples / float(fs)
        events: list[GammaSpikeEventResult] = []
        for sample, time_s in zip(samples, times_s):
            event = _compute_spike_gamma_event(
                signal=matrix[channel_index, :],
                fs=float(fs),
                spike_sample=float(sample),
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
                spike_count=int(samples.size),
                spike_samples=samples,
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
        },
    )


def _compute_spike_gamma_event(
    *,
    signal: np.ndarray,
    fs: float,
    spike_sample: float,
    spike_time_s: float,
) -> GammaSpikeEventResult:
    signal = np.asarray(signal, dtype=float).ravel()
    spike_index = int(round(spike_sample))
    if spike_index < 0 or spike_index >= signal.size:
        return GammaSpikeEventResult(
            sample=spike_sample,
            time_s=spike_time_s,
            boundary_p1_sample=None,
            boundary_n1_sample=None,
            boundary_n2_sample=None,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error="spike sample is outside the analysis window",
        )

    boundary_pre = int(round(0.075 * fs))
    boundary_len = max(2, int(round(0.300 * fs)))
    boundary_start = spike_index - boundary_pre
    boundary_stop = boundary_start + boundary_len
    if boundary_start < 0 or boundary_stop > signal.size:
        return GammaSpikeEventResult(
            sample=spike_sample,
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
            signal[boundary_start:boundary_stop],
            fs,
        )
    except Exception as exc:
        return GammaSpikeEventResult(
            sample=spike_sample,
            time_s=spike_time_s,
            boundary_p1_sample=None,
            boundary_n1_sample=None,
            boundary_n2_sample=None,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=f"boundary detection failed: {exc}",
        )

    gamma_pre = int(round(fs))
    gamma_len = max(2, int(round(2.0 * fs)))
    gamma_start = spike_index - gamma_pre
    gamma_stop = gamma_start + gamma_len
    if gamma_start < 0 or gamma_stop > signal.size:
        return GammaSpikeEventResult(
            sample=spike_sample,
            time_s=spike_time_s,
            boundary_p1_sample=boundary_start + p1_boundary - 1.0,
            boundary_n1_sample=boundary_start + n1_boundary - 1.0,
            boundary_n2_sample=boundary_start + n2_boundary - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error="not enough data around spike for gamma analysis",
        )

    p1_gamma = (boundary_start - gamma_start) + p1_boundary
    n2_gamma = (boundary_start - gamma_start) + n2_boundary
    try:
        gamma = compute_gamma(
            signal[gamma_start:gamma_stop],
            fs,
            p1_gamma,
            n2_gamma,
            strict_matlab_indexing=False,
        )
    except Exception as exc:
        return GammaSpikeEventResult(
            sample=spike_sample,
            time_s=spike_time_s,
            boundary_p1_sample=boundary_start + p1_boundary - 1.0,
            boundary_n1_sample=boundary_start + n1_boundary - 1.0,
            boundary_n2_sample=boundary_start + n2_boundary - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=f"gamma analysis failed: {exc}",
        )

    return GammaSpikeEventResult(
        sample=spike_sample,
        time_s=spike_time_s,
        boundary_p1_sample=boundary_start + p1_boundary - 1.0,
        boundary_n1_sample=boundary_start + n1_boundary - 1.0,
        boundary_n2_sample=boundary_start + n2_boundary - 1.0,
        gamma_power=float(gamma[0]),
        gamma_frequency_hz=float(gamma[1]),
        gamma_duration_ms=float(gamma[2]),
        error=None,
    )


__all__ = [
    "GammaSpikeChannelResult",
    "GammaSpikeComputationResult",
    "GammaSpikeEventResult",
    "compute_gamma_spike_for_gui",
]
