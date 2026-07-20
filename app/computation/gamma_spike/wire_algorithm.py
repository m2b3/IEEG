"""GUI-facing gamma spike detector wrapper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from typing import Any, cast

import numpy as np
from scipy import signal

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

MATLAB2_COMPAT_SETTINGS = "-bl 10 -bh 60 -h 60 -k1 3.65 -dec 200"
MATLAB2_COMPAT_NOTCH_FREQ_HZ = 60.0
MATLAB2_COMPAT_NOTCH_R = 0.985


GammaSpikeDataLoader = Callable[[float, float], tuple[np.ndarray, float, list[str]]]
GammaSpikeIndexedDataLoader = Callable[
    [list[int], float, float],
    tuple[np.ndarray, float, list[str]],
]
GammaSpikeProgressCallback = Callable[[str], None]


class GammaSpikeCancelled(RuntimeError):
    """Raised when a running gamma spike computation is cancelled by the user."""


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


def compute_gamma_spike_segmented_for_gui(
    *,
    data_loader: GammaSpikeDataLoader,
    analysis_window_s: tuple[float, float],
    recording_duration_s: float | None,
    settings: str | DetectorSettings | None = None,
    chunk_minutes: float = 10.0,
    chunk_context_seconds: float = 10.0,
    filter_context_seconds: float = 30.0,
    notch_modes_by_channel: dict[str, str] | None = None,
    indexed_data_loader: GammaSpikeIndexedDataLoader | None = None,
    progress_callback: GammaSpikeProgressCallback | None = None,
    matlab2_compat: bool = True,
) -> GammaSpikeComputationResult:
    """
    Run gamma spike detection in fixed chunks while returning one merged result.

    The detector is run on 10-minute central windows with context on both sides.
    Only events from each central window are kept, then all events are merged and
    postprocessed once. This keeps long recordings memory-friendly without
    changing the algorithmic path between short and long selections.
    """
    start_s, stop_s = (float(analysis_window_s[0]), float(analysis_window_s[1]))
    if stop_s <= start_s:
        raise ValueError("Gamma analysis end must be after analysis start.")
    if recording_duration_s is not None:
        stop_s = min(stop_s, float(recording_duration_s))
    if stop_s <= start_s:
        raise ValueError("Gamma analysis window is outside the recording.")

    chunk_duration_s = max(1.0, float(chunk_minutes) * 60.0)
    chunk_context_s = max(0.0, float(chunk_context_seconds))
    filter_context_s = max(0.0, float(filter_context_seconds))

    if matlab2_compat:
        fs, channel_names, notch_modes, detector_settings, detector_hum_hz, detector_events = (
            _detect_gamma_spikes_segmented_matlab2_compat(
                data_loader=data_loader,
                analysis_window_s=(start_s, stop_s),
                recording_duration_s=recording_duration_s,
                settings=MATLAB2_COMPAT_SETTINGS,
                chunk_duration_s=chunk_duration_s,
                chunk_context_s=chunk_context_s,
                notch_modes_by_channel=notch_modes_by_channel,
                progress_callback=progress_callback,
            )
        )
    else:
        fs, channel_names, notch_modes, detector_settings, detector_hum_hz, detector_events = (
            _detect_gamma_spikes_channel_batched(
                data_loader=data_loader,
                indexed_data_loader=indexed_data_loader,
                analysis_window_s=(start_s, stop_s),
                recording_duration_s=recording_duration_s,
                settings=settings,
                filter_context_seconds=filter_context_s,
                notch_modes_by_channel=notch_modes_by_channel,
                progress_callback=progress_callback,
            )
        )
    chunk_count = int(math.ceil((stop_s - start_s) / chunk_duration_s))

    detector_out = _detector_output_from_events(detector_events)
    per_channel_samples, qc = postprocessing(
        detector_out,
        float(fs),
        len(channel_names),
        return_qc=True,
    )

    b_boundary, a_boundary = _butter_ba(4, [10.0, 60.0], btype="bandpass", fs=float(fs))
    b_gamma, a_gamma = _butter_ba(4, [30.0, 100.0], btype="bandpass", fs=float(fs))
    local_radius_s = filter_context_s + 1.5

    samples1_by_channel = [
        np.asarray(spike_samples1, dtype=float)
        for spike_samples1 in per_channel_samples
    ]
    samples0_by_channel = [samples1 - 1.0 for samples1 in samples1_by_channel]
    times_by_channel = [samples0 / float(fs) for samples0 in samples0_by_channel]
    events_by_channel: list[list[GammaSpikeEventResult]] = [
        [] for _ in channel_names
    ]

    gamma_success_count = 0
    boundary_success_count = 0
    total_postprocessed_spikes = int(sum(samples1.size for samples1 in samples1_by_channel))
    if total_postprocessed_spikes and matlab2_compat:
        events_by_channel, boundary_success_count, gamma_success_count = (
            _compute_matlab2_compat_events_by_channel(
                data_loader=data_loader,
                indexed_data_loader=indexed_data_loader,
                fs=float(fs),
                channel_names=channel_names,
                samples1_by_channel=samples1_by_channel,
                recording_duration_s=recording_duration_s,
                notch_modes=notch_modes,
                progress_callback=progress_callback,
            )
        )
    elif total_postprocessed_spikes:
        central_start_s = start_s
        detail_chunk_index = 0
        while central_start_s < stop_s:
            detail_chunk_index += 1
            central_stop_s = min(stop_s, central_start_s + chunk_duration_s)
            is_final_chunk = central_stop_s >= stop_s
            local_start_s = max(0.0, central_start_s - local_radius_s)
            local_stop_s = central_stop_s + local_radius_s
            if recording_duration_s is not None:
                local_stop_s = min(float(recording_duration_s), local_stop_s)
            local_start_sample0 = int(np.floor(local_start_s * float(fs)))

            local_matrix_all: np.ndarray | None = None
            if indexed_data_loader is None:
                local_data, local_fs, local_names = data_loader(local_start_s, local_stop_s)
                if abs(float(local_fs) - float(fs)) > 1e-6:
                    raise ValueError("Sampling frequency changed during gamma measurement.")
                if list(map(str, local_names)) != channel_names:
                    raise ValueError("Channel list changed during gamma measurement.")
                local_matrix_all = _validated_gamma_matrix(local_data, local_names)

            for channel_index, name in enumerate(channel_names):
                times_s = times_by_channel[channel_index]
                if times_s.size == 0:
                    continue
                if is_final_chunk:
                    in_chunk = (times_s >= central_start_s) & (times_s <= central_stop_s)
                else:
                    in_chunk = (times_s >= central_start_s) & (times_s < central_stop_s)
                spike_indices = np.flatnonzero(in_chunk)
                if not spike_indices.size:
                    continue

                if progress_callback is not None:
                    progress_callback(
                        "Gamma spike details: "
                        f"chunk {detail_chunk_index}/{chunk_count}, "
                        f"channel {channel_index + 1}/{len(channel_names)}"
                    )

                if indexed_data_loader is None:
                    if local_matrix_all is None:
                        raise ValueError("Gamma measurement data is not available.")
                    local_signal = local_matrix_all[channel_index, :]
                else:
                    local_data, local_fs, local_names = indexed_data_loader(
                        [channel_index],
                        local_start_s,
                        local_stop_s,
                    )
                    if abs(float(local_fs) - float(fs)) > 1e-6:
                        raise ValueError("Sampling frequency changed during gamma measurement.")
                    if list(map(str, local_names)) != [str(name)]:
                        raise ValueError("Channel list changed during gamma measurement.")
                    local_matrix = _validated_gamma_matrix(local_data, local_names)
                    local_signal = local_matrix[0, :]

                samples1 = samples1_by_channel[channel_index]
                samples0 = samples0_by_channel[channel_index]
                for spike_index in spike_indices:
                    sample1 = float(samples1[int(spike_index)])
                    sample0 = float(samples0[int(spike_index)])
                    time_s = float(times_s[int(spike_index)])
                    local_sample1 = sample1 - float(local_start_sample0)
                    event = _compute_spike_gamma_event(
                        signal=local_signal,
                        fs=float(fs),
                        spike_sample1=local_sample1,
                        gui_sample0=sample0,
                        spike_time_s=time_s,
                        b_boundary=b_boundary,
                        a_boundary=a_boundary,
                        notch_mode=str(notch_modes.get(str(name), NOTCH_OFF)),
                        b_gamma=b_gamma,
                        a_gamma=a_gamma,
                        filter_context_seconds=filter_context_s,
                    )
                    event = _offset_event_samples(event, float(local_start_sample0))
                    if event.boundary_p1_sample is not None:
                        boundary_success_count += 1
                    if event.gamma_power is not None:
                        gamma_success_count += 1
                    events_by_channel[channel_index].append(event)

            central_start_s = central_stop_s

    channels: list[GammaSpikeChannelResult] = []
    for channel_index, name in enumerate(channel_names):
        samples0 = samples0_by_channel[channel_index]
        times_s = times_by_channel[channel_index]
        channels.append(
            GammaSpikeChannelResult(
                channel=str(name),
                spike_count=int(samples0.size),
                spike_samples=samples0,
                spike_times_s=times_s,
                events=events_by_channel[channel_index],
            )
        )

    active_notch_modes = sorted({mode for mode in notch_modes.values() if mode != NOTCH_OFF})
    return GammaSpikeComputationResult(
        channels=channels,
        detector_output=detector_out,
        metadata={
            "data_start_s": 0.0,
            "analysis_window_s": [float(start_s), float(stop_s)],
            "fs": float(fs),
            "n_channels": len(channel_names),
            "n_samples": int(round((stop_s - start_s) * float(fs))),
            "processing_mode": (
                "segmented_detector_full_channel_details"
                if matlab2_compat
                else "channel_batched_full_window_detector"
            ),
            "detector_window_s": [float(start_s), float(stop_s)],
            "detector_extra_context_seconds": (
                float(chunk_context_s) if matlab2_compat else 0.0
            ),
            "detail_chunk_minutes": float(chunk_minutes),
            "detail_chunk_context_seconds": float(chunk_context_s),
            "chunk_minutes": float(chunk_minutes),
            "chunk_context_seconds": float(chunk_context_s),
            "n_chunks": int(chunk_count),
            "filter_context_seconds": float(filter_context_s),
            "notch_filter": bool(active_notch_modes),
            "notch_modes": active_notch_modes,
            "gamma_notch_behavior": "selected_line_frequency_harmonics",
            "notch_frequency_hz": None,
            "notch_r": None,
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

    if not bool(np.any(np.asarray(gamma, dtype=float) != 0.0)):
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=p1_abs1 - 1.0,
            boundary_n1_sample=n1_abs1 - 1.0,
            boundary_n2_sample=n2_abs1 - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=None,
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


def _detect_gamma_spikes_channel_batched(
    *,
    data_loader: GammaSpikeDataLoader,
    indexed_data_loader: GammaSpikeIndexedDataLoader | None,
    analysis_window_s: tuple[float, float],
    recording_duration_s: float | None,
    settings: str | DetectorSettings | None,
    filter_context_seconds: float,
    notch_modes_by_channel: dict[str, str] | None,
    progress_callback: GammaSpikeProgressCallback | None,
) -> tuple[
    float,
    list[str],
    dict[str, str],
    str | DetectorSettings,
    float | None,
    list[tuple[float, float, int, float, float, float]],
]:
    start_s, stop_s = analysis_window_s
    # Match the original non-segmented detector behavior: detection sees exactly
    # the requested analysis window. Extra context is used later for boundary
    # and gamma measurements, not for detector threshold estimation.
    padded_start_s = float(start_s)
    padded_stop_s = float(stop_s)

    probe_stop_s = min(padded_stop_s, padded_start_s + 1.0)
    probe_data, fs, channel_names = data_loader(padded_start_s, probe_stop_s)
    _validated_gamma_matrix(probe_data, channel_names)
    fs = float(fs)
    if fs <= 0.0:
        raise ValueError("Sampling frequency must be positive.")
    channel_names = list(map(str, channel_names))
    notch_modes = _clean_notch_modes(channel_names, notch_modes_by_channel)
    active_notch_modes = sorted({mode for mode in notch_modes.values() if mode != NOTCH_OFF})
    detector_settings: str | DetectorSettings = (
        _detector_settings_for_notch_modes(active_notch_modes)
        if settings is None
        else settings
    )
    detector_hum_hz = _detector_hum_frequency_for_modes(active_notch_modes)

    detector_events: list[tuple[float, float, int, float, float, float]] = []
    for channel_index, expected_name in enumerate(channel_names):
        if progress_callback is not None:
            progress_callback(
                "Gamma spike detection: "
                f"channel {channel_index + 1}/{len(channel_names)}"
            )
        if indexed_data_loader is None:
            channel_data, channel_fs, names = data_loader(padded_start_s, padded_stop_s)
            matrix = _validated_gamma_matrix(channel_data, names)
            signal_matrix = matrix[channel_index : channel_index + 1, :]
            names = [str(names[channel_index])]
        else:
            channel_data, channel_fs, names = indexed_data_loader(
                [channel_index],
                padded_start_s,
                padded_stop_s,
            )
            signal_matrix = _validated_gamma_matrix(channel_data, names)

        if abs(float(channel_fs) - fs) > 1e-6:
            raise ValueError("Sampling frequency changed during gamma detection.")
        if not names or str(names[0]) != str(expected_name):
            raise ValueError("Channel list changed during gamma detection.")

        out, _discharges, _d_decim, _envelope, _background, _envelope_pdf = (
            spike_detector_hilbert_v25(
                np.ascontiguousarray(signal_matrix.T),
                fs,
                settings=detector_settings,
            )
        )
        out = _keep_analysis_window_events(
            out,
            fs=fs,
            data_start_s=padded_start_s,
            analysis_window_s=(float(start_s), float(stop_s)),
        )
        for pos, dur, chan, con, weight, pdf_value in zip(
            out.pos,
            out.dur,
            out.chan,
            out.con,
            out.weight,
            out.pdf,
        ):
            detector_events.append(
                (
                    padded_start_s + float(pos),
                    float(dur),
                    channel_index + 1,
                    float(con),
                    float(weight),
                    float(pdf_value),
                )
            )

    return (
        fs,
        channel_names,
        notch_modes,
        detector_settings,
        detector_hum_hz,
        detector_events,
    )


def _detect_gamma_spikes_segmented_matlab2_compat(
    *,
    data_loader: GammaSpikeDataLoader,
    analysis_window_s: tuple[float, float],
    recording_duration_s: float | None,
    settings: str | DetectorSettings,
    chunk_duration_s: float,
    chunk_context_s: float,
    notch_modes_by_channel: dict[str, str] | None,
    progress_callback: GammaSpikeProgressCallback | None,
) -> tuple[
    float,
    list[str],
    dict[str, str],
    str | DetectorSettings,
    float | None,
    list[tuple[float, float, int, float, float, float]],
]:
    start_s, stop_s = analysis_window_s
    probe_stop_s = min(float(stop_s), float(start_s) + 1.0)
    probe_data, fs, channel_names = data_loader(float(start_s), probe_stop_s)
    _validated_gamma_matrix(probe_data, channel_names)
    fs = float(fs)
    if fs <= 0.0:
        raise ValueError("Sampling frequency must be positive.")
    channel_names = list(map(str, channel_names))

    start_sample0 = max(0, int(np.floor(float(start_s) * fs)))
    stop_sample0 = int(np.ceil(float(stop_s) * fs))
    if recording_duration_s is not None:
        stop_sample0 = min(stop_sample0, int(round(float(recording_duration_s) * fs)))
    if stop_sample0 <= start_sample0:
        raise ValueError("Gamma analysis window is outside the recording.")

    chunk_samples = max(1, int(round(float(chunk_duration_s) * fs)))
    context_samples = max(0, int(round(float(chunk_context_s) * fs)))
    recording_stop_sample0 = (
        int(round(float(recording_duration_s) * fs))
        if recording_duration_s is not None
        else stop_sample0
    )

    detector_events: list[tuple[float, float, int, float, float, float]] = []
    central_start0 = start_sample0
    chunk_index = 0
    chunk_count = int(math.ceil((stop_sample0 - start_sample0) / chunk_samples))
    while central_start0 < stop_sample0:
        chunk_index += 1
        central_stop0 = min(stop_sample0, central_start0 + chunk_samples)
        extended_start0 = max(0, central_start0 - context_samples)
        extended_stop0 = min(recording_stop_sample0, central_stop0 + context_samples)
        if extended_stop0 <= extended_start0:
            break

        if progress_callback is not None:
            progress_callback(
                "Gamma spike detection: "
                f"chunk {chunk_index}/{chunk_count}"
            )

        extended_start_s = extended_start0 / fs
        extended_stop_s = extended_stop0 / fs
        chunk_data, chunk_fs, chunk_names = data_loader(extended_start_s, extended_stop_s)
        if abs(float(chunk_fs) - fs) > 1e-6:
            raise ValueError("Sampling frequency changed during gamma detection.")
        if list(map(str, chunk_names)) != channel_names:
            raise ValueError("Channel list changed during gamma detection.")
        matrix = _validated_gamma_matrix(chunk_data, chunk_names)
        out, _discharges, _d_decim, _envelope, _background, _envelope_pdf = (
            spike_detector_hilbert_v25(
                np.ascontiguousarray(matrix.T),
                fs,
                settings=settings,
            )
        )

        for pos, dur, chan, con, weight, pdf_value in zip(
            out.pos,
            out.dur,
            out.chan,
            out.con,
            out.weight,
            out.pdf,
        ):
            absolute_pos_s = float(extended_start_s) + float(pos)
            global_sample = _matlab_round_scalar(absolute_pos_s * fs)
            if global_sample < central_start0 + 1 or global_sample > central_stop0:
                continue
            detector_events.append(
                (
                    absolute_pos_s,
                    float(dur),
                    int(chan),
                    float(con),
                    float(weight),
                    float(pdf_value),
                )
            )

        central_start0 = central_stop0

    notch_modes = _clean_notch_modes(channel_names, notch_modes_by_channel)
    return (
        fs,
        channel_names,
        notch_modes,
        settings,
        MATLAB2_COMPAT_NOTCH_FREQ_HZ,
        detector_events,
    )


def _compute_matlab2_compat_events_by_channel(
    *,
    data_loader: GammaSpikeDataLoader,
    indexed_data_loader: GammaSpikeIndexedDataLoader | None,
    fs: float,
    channel_names: list[str],
    samples1_by_channel: list[np.ndarray],
    recording_duration_s: float | None,
    notch_modes: dict[str, str],
    progress_callback: GammaSpikeProgressCallback | None,
) -> tuple[list[list[GammaSpikeEventResult]], int, int]:
    if recording_duration_s is None:
        max_sample1 = max(
            (
                float(np.nanmax(samples))
                for samples in samples1_by_channel
                if np.asarray(samples).size
            ),
            default=0.0,
        )
        stop_s = max(1.0 / float(fs), (max_sample1 + float(fs) + 1.0) / float(fs))
    else:
        stop_s = float(recording_duration_s)

    b_boundary, a_boundary = _butter_ba(4, [10.0, 60.0], btype="bandpass", fs=float(fs))
    b_gamma, a_gamma = _butter_ba(4, [30.0, 100.0], btype="bandpass", fs=float(fs))

    events_by_channel: list[list[GammaSpikeEventResult]] = [
        [] for _ in channel_names
    ]
    boundary_success_count = 0
    gamma_success_count = 0

    for channel_index, name in enumerate(channel_names):
        samples1 = np.asarray(samples1_by_channel[channel_index], dtype=float)
        if samples1.size == 0:
            continue

        if progress_callback is not None:
            progress_callback(
                "Gamma spike details: "
                f"channel {channel_index + 1}/{len(channel_names)}"
            )

        if indexed_data_loader is None:
            data, channel_fs, names = data_loader(0.0, stop_s)
            matrix = _validated_gamma_matrix(data, names)
            if channel_index >= matrix.shape[0]:
                raise ValueError("Channel list changed during gamma spike details.")
            if list(map(str, names)) != channel_names:
                raise ValueError("Channel list changed during gamma spike details.")
            raw_signal = matrix[channel_index, :]
        else:
            data, channel_fs, names = indexed_data_loader([channel_index], 0.0, stop_s)
            matrix = _validated_gamma_matrix(data, names)
            if list(map(str, names)) != [str(name)]:
                raise ValueError("Channel list changed during gamma spike details.")
            raw_signal = matrix[0, :]
        if abs(float(channel_fs) - float(fs)) > 1e-6:
            raise ValueError("Sampling frequency changed during gamma spike details.")

        signal_notched = _apply_notch_mode(
            np.asarray(raw_signal, dtype=float),
            float(fs),
            str(notch_modes.get(str(name), NOTCH_OFF)),
        )
        signal_boundary = signal.filtfilt(b_boundary, a_boundary, signal_notched)
        signal_gamma = signal.filtfilt(b_gamma, a_gamma, signal_notched)

        n_samples = int(signal_boundary.size)
        for sample1 in samples1:
            sample0 = float(sample1) - 1.0
            time_s = sample0 / float(fs)
            event = _compute_spike_gamma_event_matlab2_compat(
                signal_boundary=signal_boundary,
                signal_gamma=signal_gamma,
                fs=float(fs),
                spike_sample1=float(sample1),
                gui_sample0=sample0,
                spike_time_s=time_s,
                n_samples=n_samples,
            )
            if event.boundary_p1_sample is not None:
                boundary_success_count += 1
            if event.gamma_power is not None:
                gamma_success_count += 1
            events_by_channel[channel_index].append(event)

    return events_by_channel, boundary_success_count, gamma_success_count


def _compute_spike_gamma_event_matlab2_compat(
    *,
    signal_boundary: np.ndarray,
    signal_gamma: np.ndarray,
    fs: float,
    spike_sample1: float,
    gui_sample0: float,
    spike_time_s: float,
    n_samples: int,
) -> GammaSpikeEventResult:
    spike_onset = float(spike_sample1 - 75e-3 * fs)
    spike_offset = float(spike_sample1 + 225e-3 * fs)
    start_index = _matlab_round_scalar(spike_onset)
    stop_index = _matlab_round_scalar(spike_offset)
    if start_index < 1 or stop_index > n_samples or stop_index <= start_index:
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
            signal_boundary[start_index - 1 : stop_index],
            fs,
        )
        if np.size(p1_boundary) == 0 or np.size(n1_boundary) == 0 or np.size(n2_boundary) == 0:
            raise ValueError("empty boundary")
        p1_boundary = float(np.ravel(p1_boundary)[0])
        n1_boundary = float(np.ravel(n1_boundary)[0])
        n2_boundary = float(np.ravel(n2_boundary)[0])
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
    gamma_window = _matlab_round_array(_matlab_colon(n1_abs1 - fs, n1_abs1 + fs)).astype(int)
    if gamma_window.size == 0 or gamma_window[0] < 1 or gamma_window[-1] > n_samples:
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
        gamma, details = compute_gamma(
            signal_gamma[int(gamma_window[0]) - 1 : int(gamma_window[-1])],
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

    if not bool(np.any(np.asarray(gamma, dtype=float) != 0.0)):
        return GammaSpikeEventResult(
            sample=gui_sample0,
            time_s=spike_time_s,
            boundary_p1_sample=p1_abs1 - 1.0,
            boundary_n1_sample=n1_abs1 - 1.0,
            boundary_n2_sample=n2_abs1 - 1.0,
            gamma_power=None,
            gamma_frequency_hz=None,
            gamma_duration_ms=None,
            error=None,
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


def _offset_event_samples(
    event: GammaSpikeEventResult,
    sample_offset0: float,
) -> GammaSpikeEventResult:
    if sample_offset0 == 0.0:
        return event
    return GammaSpikeEventResult(
        sample=event.sample,
        time_s=event.time_s,
        boundary_p1_sample=(
            None
            if event.boundary_p1_sample is None
            else float(event.boundary_p1_sample) + sample_offset0
        ),
        boundary_n1_sample=(
            None
            if event.boundary_n1_sample is None
            else float(event.boundary_n1_sample) + sample_offset0
        ),
        boundary_n2_sample=(
            None
            if event.boundary_n2_sample is None
            else float(event.boundary_n2_sample) + sample_offset0
        ),
        gamma_power=event.gamma_power,
        gamma_frequency_hz=event.gamma_frequency_hz,
        gamma_duration_ms=event.gamma_duration_ms,
        error=event.error,
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


def _validated_gamma_matrix(data: np.ndarray, channel_names: list[str]) -> np.ndarray:
    matrix = np.asarray(data, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("Gamma spike data must be a 2D channel x sample array.")
    if matrix.shape[0] != len(channel_names):
        raise ValueError("Channel name count does not match gamma spike data.")
    if matrix.shape[0] == 0 or matrix.shape[1] < 2:
        raise ValueError("Not enough data for gamma spike detection.")
    return matrix


def _central_detector_events(
    out: DetectorOutput,
    *,
    data_start_s: float,
    central_start_s: float,
    central_stop_s: float,
    include_stop: bool,
) -> list[tuple[float, float, int, float, float, float]]:
    positions = np.asarray(out.pos, dtype=float).ravel()
    durations = np.asarray(out.dur, dtype=float).ravel()
    channels = np.asarray(out.chan, dtype=int).ravel()
    connectivity = np.asarray(out.con, dtype=float).ravel()
    weights = np.asarray(out.weight, dtype=float).ravel()
    pdf = np.asarray(out.pdf, dtype=float).ravel()

    events: list[tuple[float, float, int, float, float, float]] = []
    for pos, dur, chan, con, weight, pdf_value in zip(
        positions,
        durations,
        channels,
        connectivity,
        weights,
        pdf,
    ):
        absolute_pos_s = float(data_start_s) + float(pos)
        if absolute_pos_s < float(central_start_s):
            continue
        if include_stop:
            if absolute_pos_s > float(central_stop_s):
                continue
        elif absolute_pos_s >= float(central_stop_s):
            continue
        events.append(
            (
                absolute_pos_s,
                float(dur),
                int(chan),
                float(con),
                float(weight),
                float(pdf_value),
            )
        )
    return events


def _detector_output_from_events(
    events: list[tuple[float, float, int, float, float, float]],
) -> DetectorOutput:
    if events:
        events = sorted(events, key=lambda item: (item[0], item[2]))
    return DetectorOutput(
        pos=np.asarray([event[0] for event in events], dtype=float),
        dur=np.asarray([event[1] for event in events], dtype=float),
        chan=np.asarray([event[2] for event in events], dtype=int),
        con=np.asarray([event[3] for event in events], dtype=float),
        weight=np.asarray([event[4] for event in events], dtype=float),
        pdf=np.asarray([event[5] for event in events], dtype=float),
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


def _matlab2_notch_coefficients(fs: float) -> tuple[np.ndarray, np.ndarray]:
    theta = 2.0 * np.pi * MATLAB2_COMPAT_NOTCH_FREQ_HZ / float(fs)
    r = MATLAB2_COMPAT_NOTCH_R
    b = np.array([1.0, -2.0 * np.cos(theta), 1.0], dtype=float)
    a = np.array([1.0, -2.0 * r * np.cos(theta), r * r], dtype=float)
    return b, a


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
    "GammaSpikeCancelled",
    "compute_gamma_spike_for_gui",
    "compute_gamma_spike_segmented_for_gui",
]
