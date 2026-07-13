"""
Python translation of matlab2/compute_gamma.m.

This implementation follows the matlab2 gamma-decision logic and uses a local
generalized Morse CWT helper to mirror MATLAB's default `cwtfilterbank` setup:
frequency limits 30-100 Hz and 40 voices per octave.
"""

from __future__ import annotations

import numpy as np

try:
    from .build_gamma_masks import build_gamma_masks
    from .select_max_gamma_candidate import select_max_gamma_candidate
except ImportError:  # Allows direct script-style use from this folder.
    from build_gamma_masks import build_gamma_masks
    from select_max_gamma_candidate import select_max_gamma_candidate


MATLAB_MORSE_NORM_PER_HZ_AT_2000HZ = 0.055219397200513345
MATLAB_MORSE_NORM_REFERENCE_FS = 2000.0
_WAVELET_CACHE: dict[tuple[int, float, float, float, int, float, float], tuple[np.ndarray, np.ndarray]] = {}


def compute_gamma(
    segment: np.ndarray,
    fs: float,
    p1: float,
    n2: float,
    invalid_mask: np.ndarray | None = None,
    min_clean_baseline_ms: float = 250.0,
    precomputed_transform: dict[str, np.ndarray] | None = None,
    *,
    return_details: bool = False,
):
    """
    Detect preceding gamma activity in a spike segment.

    Parameters
    ----------
    segment:
        Gamma-filtered spike segment. MATLAB passes a 2 s segment and applies
        the CWT to ``segment(1:end-1)``.
    fs:
        Sampling frequency in Hz.
    p1:
        Spike onset sample in MATLAB-style 1-based coordinates.
    n2:
        Spike end sample in MATLAB-style 1-based coordinates.
    invalid_mask:
        Optional boolean mask over ``segment[:-1]``. Masked coefficients are
        excluded from baseline and candidate search, matching matlab2.
    min_clean_baseline_ms:
        Minimum unmasked baseline duration required for evaluation.
    precomputed_transform:
        Optional deterministic fixture with ``gamma_sig``, ``tf_freqs``,
        ``gamma_mean``, and ``gamma_baseline_sd``. Used by regression tests.

    Returns
    -------
    np.ndarray
        ``[maximum_gamma_power, gamma_frequency, gamma_duration_ms]``. If
        ``return_details`` is True, returns ``(output, details)``.
    """

    segment = np.asarray(segment, dtype=float).ravel()
    if segment.size < 2:
        output = np.array([0.0, 0.0, 0.0])
        details = _initial_details(min_clean_baseline_ms)
        return (output, details) if return_details else output

    f_gamma = segment[:-1]
    masks = build_gamma_masks(f_gamma.size, fs, p1, n2, invalid_mask, min_clean_baseline_ms)
    details = _initial_details(min_clean_baseline_ms)
    details.update(
        {
            "evaluable": masks["evaluable"],
            "masked_samples": masks["masked_samples"],
            "clean_baseline_samples": masks["clean_baseline_samples"],
            "clean_baseline_duration_ms": masks["clean_baseline_duration_ms"],
        }
    )
    if not masks["evaluable"]:
        output = np.array([np.nan, np.nan, np.nan], dtype=float)
        details["exclusion_reason"] = "insufficient_clean_baseline"
        return (output, details) if return_details else output

    if precomputed_transform is None:
        tf, tf_freqs = _morse_cwt(f_gamma, fs, (30.0, 100.0), voices_per_octave=40)
        gamma_sig = np.abs(tf)
        gamma_baseline = gamma_sig[:, masks["baseline_mask"]]
        gamma_mean = np.mean(gamma_baseline, axis=1)
        gamma_baseline_sd = np.std(gamma_baseline, axis=1, ddof=1)
    else:
        required = ["gamma_sig", "tf_freqs", "gamma_mean", "gamma_baseline_sd"]
        missing = [name for name in required if name not in precomputed_transform]
        if missing:
            raise ValueError(f"precomputed_transform is missing {missing}")
        gamma_sig = np.asarray(precomputed_transform["gamma_sig"], dtype=float)
        tf_freqs = np.asarray(precomputed_transform["tf_freqs"], dtype=float).ravel()
        gamma_mean = np.asarray(precomputed_transform["gamma_mean"], dtype=float).ravel()
        gamma_baseline_sd = np.asarray(precomputed_transform["gamma_baseline_sd"], dtype=float).ravel()
        if (
            gamma_sig.shape[1] != f_gamma.size
            or gamma_sig.shape[0] != tf_freqs.size
            or gamma_mean.size != tf_freqs.size
            or gamma_baseline_sd.size != tf_freqs.size
        ):
            raise ValueError("precomputed_transform dimensions do not match signal or frequencies")

    pow_thresh = 2 * gamma_baseline_sd
    gamma_thresh = gamma_mean + pow_thresh

    pre_indices = np.flatnonzero(masks["search_mask"])
    if pre_indices.size:
        denom = gamma_baseline_sd.copy()
        denom[denom == 0] = np.nan
        gamma_z = (gamma_sig - gamma_mean[:, None]) / denom[:, None]
        pre_z = gamma_z[:, pre_indices[0] : pre_indices[-1] + 1]
        local_valid = masks["search_mask"][pre_indices[0] : pre_indices[-1] + 1]
        pre_z[:, ~local_valid] = np.nan
        if not np.all(np.isnan(pre_z)):
            linear = int(np.nanargmax(pre_z))
            best_freq_idx, best_time_idx = np.unravel_index(linear, pre_z.shape)
            best_sample0 = pre_indices[0] + best_time_idx
            best_sample = best_sample0 + 1
            details["best_pre_sd_from_baseline"] = float(pre_z[best_freq_idx, best_time_idx])
            details["best_pre_power"] = float(gamma_sig[best_freq_idx, best_sample0])
            details["best_pre_frequency_hz"] = float(tf_freqs[best_freq_idx])
            details["best_pre_onset_rel_ms"] = float(1000.0 * (best_sample - p1) / fs)
            details["best_pre_offset_rel_ms"] = details["best_pre_onset_rel_ms"]
            details["best_pre_duration_ms"] = float(1000.0 / fs)
            details["best_pre_ends_within_190ms"] = bool(p1 - best_sample <= 0.19 * fs)

    gamma_2sd = gamma_sig > gamma_thresh[:, None]
    gamma_2sd[:, ~masks["search_mask"]] = False

    dur_thresh = 3 * np.ceil((1.0 / tf_freqs) * fs)
    segments: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []

    for i_freq in range(len(tf_freqs)):
        pass_row = np.r_[False, gamma_2sd[i_freq, :], False].astype(int)
        segs = np.flatnonzero(np.diff(pass_row) != 0) + 1
        if segs.size == 0:
            continue
        pairs = segs.reshape(-1, 2)
        seglen = pairs[:, 1] - pairs[:, 0]

        for pair, length in zip(pairs, seglen):
            event_onset = int(pair[0] + 1)  # MATLAB 1-based
            event_offset = int(pair[1])
            event_duration_ms = float(1000.0 * length / fs)
            event_power = float(np.mean(gamma_sig[i_freq, pair[0] : pair[1]]))
            baseline_sd = float(gamma_baseline_sd[i_freq])
            event_sd = float((event_power - gamma_mean[i_freq]) / baseline_sd) if baseline_sd > 0 else np.nan
            event_onset_rel_ms = float(1000.0 * (event_onset - p1) / fs)
            event_offset_rel_ms = float(1000.0 * (event_offset - p1) / fs)
            ends_within_190ms = bool(p1 - event_offset <= 0.19 * fs)
            dur_thresh_pass = bool(length >= dur_thresh[i_freq])
            passes_gamma_rules = bool(ends_within_190ms and dur_thresh_pass)
            row = {
                "frequency_index": i_freq + 1,
                "frequency_hz": float(tf_freqs[i_freq]),
                "onset_sample": event_onset,
                "offset_sample": event_offset,
                "onset_rel_ms": event_onset_rel_ms,
                "offset_rel_ms": event_offset_rel_ms,
                "gamma_power": event_power,
                "baseline_mean": float(gamma_mean[i_freq]),
                "baseline_sd": baseline_sd,
                "sd_from_baseline": event_sd,
                "duration_ms": event_duration_ms,
                "ends_within_190ms": ends_within_190ms,
                "passes_3cycles": dur_thresh_pass,
                "passes_gamma_rules": passes_gamma_rules,
            }
            segments.append(row)
            if passes_gamma_rules:
                candidates.append(row.copy())
            else:
                gamma_2sd[i_freq, pair[0] : pair[1]] = False

    details["segments"] = segments
    details["candidates"] = candidates

    selected = select_max_gamma_candidate([candidate["gamma_power"] for candidate in candidates])
    if selected is None:
        output = np.array([0.0, 0.0, 0.0], dtype=float)
        return (output, details) if return_details else output

    chosen = candidates[selected]
    output = np.array(
        [chosen["gamma_power"], chosen["frequency_hz"], chosen["duration_ms"]],
        dtype=float,
    )
    details.update(
        {
            "has_gamma": True,
            "onset_sample": chosen["onset_sample"],
            "offset_sample": chosen["offset_sample"],
            "onset_rel_ms": chosen["onset_rel_ms"],
            "offset_rel_ms": chosen["offset_rel_ms"],
            "sd_from_baseline": chosen["sd_from_baseline"],
            "baseline_mean": chosen["baseline_mean"],
            "baseline_sd": chosen["baseline_sd"],
        }
    )
    return (output, details) if return_details else output


def _initial_details(min_clean_baseline_ms: float) -> dict[str, object]:
    return {
        "evaluable": True,
        "exclusion_reason": "",
        "masked_samples": 0,
        "clean_baseline_samples": 0,
        "clean_baseline_duration_ms": 0.0,
        "min_clean_baseline_ms": float(min_clean_baseline_ms),
        "has_gamma": False,
        "onset_sample": np.nan,
        "offset_sample": np.nan,
        "onset_rel_ms": np.nan,
        "offset_rel_ms": np.nan,
        "sd_from_baseline": np.nan,
        "baseline_mean": np.nan,
        "baseline_sd": np.nan,
        "best_pre_sd_from_baseline": np.nan,
        "best_pre_power": np.nan,
        "best_pre_frequency_hz": np.nan,
        "best_pre_onset_rel_ms": np.nan,
        "best_pre_offset_rel_ms": np.nan,
        "best_pre_duration_ms": np.nan,
        "best_pre_ends_within_190ms": False,
        "best_pre_passes_3cycles": False,
        "best_pre_passes_gamma_rules": False,
        "segments": [],
        "candidates": [],
    }


def _morse_cwt(
    signal: np.ndarray,
    fs: float,
    frequency_limits: tuple[float, float],
    voices_per_octave: int,
    gamma: float = 3.0,
    time_bandwidth: float = 60.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute a generalized Morse CWT using FFT-domain wavelets."""

    signal = np.asarray(signal, dtype=float).ravel()
    n = signal.size
    signal_fft = np.fft.fft(signal)
    freqs, wavelets = _wavelet_bank(
        n,
        fs,
        frequency_limits[0],
        frequency_limits[1],
        voices_per_octave,
        gamma,
        time_bandwidth,
    )
    coeffs = np.empty((freqs.size, n), dtype=complex)

    for idx, wavelet_fft in enumerate(wavelets):
        coeffs[idx, :] = np.fft.ifft(signal_fft * np.conj(wavelet_fft))

    return coeffs, freqs


def _matlab_colon(start: float, stop: float) -> np.ndarray:
    """Return values produced by MATLAB's default ``start:stop`` expression."""

    if start > stop:
        return np.array([], dtype=float)
    count = int(np.floor(stop - start)) + 1
    return start + np.arange(count, dtype=float)


def _validated_matlab_indices(values: np.ndarray, upper: int) -> np.ndarray:
    """Validate MATLAB-style 1-based indices and return them as integers."""

    if values.size == 0:
        return np.array([], dtype=int)
    rounded = np.round(values)
    valid = (
        np.isfinite(values)
        & np.isclose(values, rounded, rtol=0.0, atol=1e-9)
        & (rounded >= 1)
        & (rounded <= upper)
    )
    if not np.all(valid):
        raise IndexError(
            "Index in position 2 is invalid. Array indices must be positive integers or logical values."
        )
    return rounded.astype(int)


def _wavelet_bank(
    n: int,
    fs: float,
    low: float,
    high: float,
    voices_per_octave: int,
    gamma: float,
    time_bandwidth: float,
) -> tuple[np.ndarray, np.ndarray]:
    key = (n, float(fs), float(low), float(high), int(voices_per_octave), float(gamma), float(time_bandwidth))
    if key in _WAVELET_CACHE:
        return _WAVELET_CACHE[key]

    freqs = _frequency_grid(low, high, voices_per_octave)
    beta = time_bandwidth / gamma
    peak_rad = (beta / gamma) ** (1.0 / gamma)
    omega = 2.0 * np.pi * np.fft.fftfreq(n)
    positive = omega > 0
    wavelets = np.zeros((freqs.size, n), dtype=complex)

    for idx, freq in enumerate(freqs):
        scale = peak_rad / (2.0 * np.pi * freq / fs)
        scaled_omega = scale * omega
        wavelet_fft = np.zeros(n, dtype=complex)
        wavelet_fft[positive] = (
            scaled_omega[positive] ** beta * np.exp(-(scaled_omega[positive] ** gamma))
        )
        norm = np.sqrt(np.sum(np.abs(wavelet_fft) ** 2))
        if norm > 0:
            wavelet_fft /= norm
        matlab_fs_norm = np.sqrt(MATLAB_MORSE_NORM_REFERENCE_FS / fs)
        wavelets[idx, :] = wavelet_fft * np.sqrt(scale) * freq * MATLAB_MORSE_NORM_PER_HZ_AT_2000HZ * matlab_fs_norm

    _WAVELET_CACHE[key] = (freqs, wavelets)
    return _WAVELET_CACHE[key]


def _frequency_grid(low: float, high: float, voices_per_octave: int) -> np.ndarray:
    n_steps = int(np.floor(np.log2(high / low) * voices_per_octave))
    freqs = high / (2.0 ** (np.arange(n_steps + 1) / voices_per_octave))
    return freqs[freqs >= low]


__all__ = ["compute_gamma"]
