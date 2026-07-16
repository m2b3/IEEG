"""GUI-facing gamma spike detector wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

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
from app.preprocessing.filtering import NOTCH_50_HARM, NOTCH_60_HARM, NOTCH_OFF


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


def _butter_ba(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
    b, a = cast(Any, signal.butter(*args, **kwargs))
    return np.asarray(b, dtype=float), np.asarray(a, dtype=float)


def compute_gamma_spike_for_gui(
    *,
    data: np.ndarray,
    fs: float,
    channel_names: list[str],
    data_start_s: float,
    analysis_window_s: tuple[float, float],
    settings: str | DetectorSettings | None = None,
    filter_context_seconds: float = 30.0,
    notch_modes_by_channel: dict[str, str] | None = None,
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

    notch_modes = _clean_notch_modes(channel_names, notch_modes_by_channel)
    active_notch_modes = sorted({mode for mode in notch_modes.values() if mode != NOTCH_OFF})

    detector_data = np.ascontiguousarray(matrix.T)
    detector_settings = (
        _detector_settings_for_notch_modes(active_notch_modes)
        if settings is None
        else settings
    )
    out, _discharges, _d_decim, _envelope, _background, _envelope_pdf = (
        spike_detector_hilbert_v25(detector_data, float(fs), settings=detector_settings)
    )
    out = _keep_analysis_window_events(
        out,
        fs=float(fs),
        data_start_s=float(data_start_s),
        analysis_window_s=(float(start_s), float(stop_s)),
    )
    per_channel_samples, qc = postprocessing(
        out,
        float(fs),
        len(channel_names),
        return_qc=True,
    )

    b_boundary, a_boundary = _butter_ba(4, [10.0, 60.0], btype="bandpass", fs=float(fs))
    b_gamma, a_gamma = _butter_ba(4, [30.0, 100.0], btype="bandpass", fs=float(fs))
    detector_hum_hz = _detector_hum_frequency_for_modes(active_notch_modes)

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
                b_boundary=b_boundary,
                a_boundary=a_boundary,
                notch_mode=str(notch_modes.get(str(name), NOTCH_OFF)),
                b_gamma=b_gamma,
                a_gamma=a_gamma,
                filter_context_seconds=float(filter_context_seconds),
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
            "filter_context_seconds": float(filter_context_seconds),
            "notch_filter": bool(active_notch_modes),
            "notch_modes": active_notch_modes,
            "detector_hum_filter_hz": detector_hum_hz,
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
    b_boundary: np.ndarray,
    a_boundary: np.ndarray,
    notch_mode: str,
    b_gamma: np.ndarray,
    a_gamma: np.ndarray,
    filter_context_seconds: float,
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

    # Match the reference segmented pipeline: store the unrounded spike onset
    # and offset, round only for reading, then add boundary offsets back to the
    # unrounded onset. This keeps gamma-window timing/output comparable.
    spike_onset = float(spike_sample1 - 75e-3 * fs)
    spike_offset = float(spike_sample1 + 225e-3 * fs)
    boundary_start1 = _matlab_round_scalar(spike_onset)
    boundary_stop1 = _matlab_round_scalar(spike_offset)
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
        boundary_signal, boundary_extended_start1 = _filtered_window(
            raw_signal,
            needed_start_sample1=boundary_start1,
            needed_stop_sample1=boundary_stop1,
            fs=fs,
            b=b_boundary,
            a=a_boundary,
            filter_context_seconds=filter_context_seconds,
        )
        boundary_local_start0 = boundary_start1 - boundary_extended_start1
        boundary_local_stop0 = boundary_stop1 - boundary_extended_start1 + 1
        p1_boundary, n1_boundary, n2_boundary = compute_spike_boundary(
            boundary_signal[boundary_local_start0:boundary_local_stop0],
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

    p1_abs1 = spike_onset + p1_boundary
    n1_abs1 = spike_onset + n1_boundary
    n2_abs1 = spike_onset + n2_boundary
    gamma_window = _matlab_colon(n1_abs1 - fs, n1_abs1 + fs)
    if gamma_window.size == 0:
        gamma_start1 = 1
        gamma_stop1 = 0
    else:
        rounded_gamma_window = _matlab_round_array(gamma_window).astype(int)
        gamma_start1 = int(rounded_gamma_window[0])
        gamma_stop1 = int(rounded_gamma_window[-1])
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
        gamma_signal, gamma_extended_start1 = _gamma_filtered_window(
            raw_signal,
            needed_start_sample1=gamma_start1,
            needed_stop_sample1=gamma_stop1,
            fs=fs,
            notch_mode=notch_mode,
            b_gamma=b_gamma,
            a_gamma=a_gamma,
            filter_context_seconds=filter_context_seconds,
        )
        gamma_local_start0 = gamma_start1 - gamma_extended_start1
        gamma_local_stop0 = gamma_stop1 - gamma_extended_start1 + 1
        gamma, details = compute_gamma(
            gamma_signal[gamma_local_start0:gamma_local_stop0],
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


def _keep_analysis_window_events(
    out: DetectorOutput,
    *,
    fs: float,
    data_start_s: float,
    analysis_window_s: tuple[float, float],
) -> DetectorOutput:
    start_s, stop_s = analysis_window_s
    times_s = float(data_start_s) + np.asarray(out.pos, dtype=float).ravel()
    samples1 = _matlab_round_array(times_s * float(fs)).astype(int)
    start_sample1 = _matlab_round_scalar(float(start_s) * float(fs))
    stop_sample1 = _matlab_round_scalar(float(stop_s) * float(fs))
    keep = (samples1 >= start_sample1) & (samples1 <= stop_sample1)
    return DetectorOutput(
        pos=np.asarray(out.pos, dtype=float).ravel()[keep],
        dur=np.asarray(out.dur, dtype=float).ravel()[keep],
        chan=np.asarray(out.chan, dtype=int).ravel()[keep],
        con=np.asarray(out.con, dtype=float).ravel()[keep],
        weight=np.asarray(out.weight, dtype=float).ravel()[keep],
        pdf=np.asarray(out.pdf, dtype=float).ravel()[keep],
    )


def _filtered_window(
    values: np.ndarray,
    *,
    needed_start_sample1: int,
    needed_stop_sample1: int,
    fs: float,
    b: np.ndarray,
    a: np.ndarray,
    filter_context_seconds: float,
) -> tuple[np.ndarray, int]:
    context = int(round(max(0.0, float(filter_context_seconds)) * float(fs)))
    extended_start_sample1 = max(1, int(needed_start_sample1) - context)
    extended_stop_sample1 = min(values.size, int(needed_stop_sample1) + context)
    segment = values[extended_start_sample1 - 1 : extended_stop_sample1]
    return signal.filtfilt(b, a, segment), extended_start_sample1


def _gamma_filtered_window(
    values: np.ndarray,
    *,
    needed_start_sample1: int,
    needed_stop_sample1: int,
    fs: float,
    notch_mode: str,
    b_gamma: np.ndarray,
    a_gamma: np.ndarray,
    filter_context_seconds: float,
) -> tuple[np.ndarray, int]:
    context = int(round(max(0.0, float(filter_context_seconds)) * float(fs)))
    extended_start_sample1 = max(1, int(needed_start_sample1) - context)
    extended_stop_sample1 = min(values.size, int(needed_stop_sample1) + context)
    segment = values[extended_start_sample1 - 1 : extended_stop_sample1]
    notched = _apply_notch_mode(segment, fs, notch_mode)
    return signal.filtfilt(b_gamma, a_gamma, notched), extended_start_sample1


def _bandpass_filter(values: np.ndarray, fs: float, low_hz: float, high_hz: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 8:
        return arr
    nyquist = 0.5 * float(fs)
    low = max(1e-6, float(low_hz))
    high = min(float(high_hz), nyquist - 1e-6)
    if low >= high:
        return arr
    b, a = _butter_ba(4, [low, high], btype="bandpass", fs=float(fs))
    return signal.filtfilt(b, a, arr)


def _gamma_filter(values: np.ndarray, fs: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 8:
        return arr
    notched = _apply_notch_mode(arr, fs, NOTCH_60_HARM)
    return _bandpass_filter(notched, fs, 30.0, 100.0)


def _clean_notch_modes(
    channel_names: list[str],
    notch_modes_by_channel: dict[str, str] | None,
) -> dict[str, str]:
    modes_by_channel = notch_modes_by_channel or {}
    cleaned: dict[str, str] = {}
    valid_modes = {NOTCH_OFF, NOTCH_50_HARM, NOTCH_60_HARM}
    for channel_name in channel_names:
        mode = str(modes_by_channel.get(str(channel_name), NOTCH_OFF))
        cleaned[str(channel_name)] = mode if mode in valid_modes else NOTCH_OFF
    return cleaned


def _detector_settings_for_notch_modes(active_notch_modes: list[str]) -> str:
    hum_hz = _detector_hum_frequency_for_modes(active_notch_modes)
    hum_token = f"{hum_hz:g}" if hum_hz is not None else "1000000000"
    return f"-bl 10 -bh 60 -h {hum_token} -k1 3.65 -dec 200"


def _detector_hum_frequency_for_modes(active_notch_modes: list[str]) -> float | None:
    if NOTCH_60_HARM in active_notch_modes:
        return 60.0
    if NOTCH_50_HARM in active_notch_modes:
        return 50.0
    return None


def _apply_notch_mode(values: np.ndarray, fs: float, notch_mode: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 8 or notch_mode == NOTCH_OFF:
        return arr
    if notch_mode == NOTCH_50_HARM:
        freqs = _harmonic_freqs(50.0, fs)
    elif notch_mode == NOTCH_60_HARM:
        freqs = _harmonic_freqs(60.0, fs)
    else:
        return arr
    filtered = np.asarray(arr, dtype=float)
    for freq in freqs:
        b, a = signal.iirnotch(w0=float(freq), Q=30.0, fs=float(fs))
        filtered = signal.filtfilt(b, a, filtered)
    return np.asarray(filtered, dtype=float)


def _harmonic_freqs(base_hz: float, fs: float) -> np.ndarray:
    nyquist = 0.5 * float(fs)
    freqs = np.arange(float(base_hz), nyquist, float(base_hz), dtype=float)
    return freqs[freqs > 0.0]


def _matlab_round_scalar(value: float) -> int:
    return int(np.sign(value) * np.floor(abs(float(value)) + 0.5))


def _matlab_round_array(value: np.ndarray) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    return np.sign(arr) * np.floor(np.abs(arr) + 0.5)


def _matlab_colon(start: float, stop: float) -> np.ndarray:
    if start > stop:
        return np.array([], dtype=float)
    count = int(np.floor(stop - start)) + 1
    return start + np.arange(count, dtype=float)


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
