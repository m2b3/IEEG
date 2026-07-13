"""
Python translation of Matlab_version/compute_gamma.m.

This implementation follows the MATLAB gamma-decision logic and uses a local
generalized Morse CWT helper to mirror MATLAB's default `cwtfilterbank` setup:
frequency limits 30-100 Hz and 40 voices per octave.
"""

from __future__ import annotations

import numpy as np


MATLAB_MORSE_NORM_PER_HZ_AT_2000HZ = 0.055219397200513345
MATLAB_MORSE_NORM_REFERENCE_FS = 2000.0
_WAVELET_CACHE: dict[tuple[int, float, float, float, int, float, float], tuple[np.ndarray, np.ndarray]] = {}


def compute_gamma(
    segment: np.ndarray,
    fs: float,
    p1: float,
    n2: float,
    *,
    strict_matlab_indexing: bool = True,
) -> np.ndarray:
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
    strict_matlab_indexing:
        When True, reject the same invalid/decimal column indices that MATLAB
        rejects. When False, out-of-range ranges are clipped to valid Python
        array bounds.

    Returns
    -------
    np.ndarray
        ``[maximum_gamma_power, gamma_frequency, gamma_duration_ms]``. If no
        gamma activity is detected, returns ``[0, 0, 0]``.
    """

    segment = np.asarray(segment, dtype=float).ravel()
    if segment.size < 2:
        return np.array([0.0, 0.0, 0.0])

    f_gamma = segment[:-1]
    tf, tf_freqs = _morse_cwt(f_gamma, fs, (30.0, 100.0), voices_per_octave=40)
    gamma_sig = np.abs(tf)

    n_times = gamma_sig.shape[1]
    keep = np.ones(n_times, dtype=bool)
    delete_ranges = [
        (1.0, p1 - fs / 2 - 1.0),
        (np.floor(p1), np.ceil(n2)),
        (n2 + fs / 2 + 1.0, float(n_times)),
    ]
    for start, stop in delete_ranges:
        values = _matlab_colon(start, stop)
        if values.size == 0:
            continue
        if strict_matlab_indexing:
            indices = _validated_matlab_indices(values, n_times)
            keep[indices - 1] = False
        else:
            indices = np.trunc(values).astype(int)
            indices = indices[(indices >= 1) & (indices <= n_times)]
            keep[indices - 1] = False

    gamma_baseline = gamma_sig[:, keep]
    if gamma_baseline.shape[1] == 0:
        return np.array([0.0, 0.0, 0.0])

    gamma_mean = np.mean(gamma_baseline, axis=1)
    pow_thresh = 2 * np.std(gamma_baseline, axis=1, ddof=1)
    gamma_thresh = gamma_mean + pow_thresh

    gamma_2sd = gamma_sig > gamma_thresh[:, None]

    zero_ranges = [(1.0, np.ceil(p1 - fs / 2)), (p1, float(n_times))]
    for start, stop in zero_ranges:
        values = _matlab_colon(start, stop)
        if values.size == 0:
            continue
        if strict_matlab_indexing:
            indices = _validated_matlab_indices(values, n_times)
            gamma_2sd[:, indices - 1] = False
        else:
            indices = np.trunc(values).astype(int)
            indices = indices[(indices >= 1) & (indices <= n_times)]
            gamma_2sd[:, indices - 1] = False

    dur_thresh = 3 * np.ceil((1.0 / tf_freqs) * fs)
    gamma_dur = np.full(tf_freqs.shape, np.nan, dtype=float)
    gamma_pow = np.full(tf_freqs.shape, np.nan, dtype=float)

    for i_freq in range(len(tf_freqs)):
        pass_row = np.r_[False, gamma_2sd[i_freq, :], False].astype(int)
        segs = np.flatnonzero(np.diff(pass_row) != 0) + 1
        if segs.size == 0:
            continue
        pairs = segs.reshape(-1, 2)
        seglen = pairs[:, 1] - pairs[:, 0]

        for pair, length in zip(pairs, seglen):
            if p1 - pair[1] <= 0.19 * fs:
                if length >= dur_thresh[i_freq]:
                    gamma_dur[i_freq] = length
                    gamma_pow[i_freq] = np.mean(gamma_sig[i_freq, pair[0] : pair[1]])
                else:
                    gamma_2sd[i_freq, pair[0] : pair[1]] = False
                    gamma_dur[i_freq] = np.nan
                    gamma_pow[i_freq] = np.nan
            else:
                gamma_2sd[i_freq, pair[0] : pair[1]] = False

    if np.all(np.isnan(gamma_pow)):
        return np.array([0.0, 0.0, 0.0])

    gamma_f = int(np.nanargmax(gamma_pow))
    return np.array(
        [
            gamma_pow[gamma_f],
            tf_freqs[gamma_f],
            1000.0 * gamma_dur[gamma_f] / fs,
        ],
        dtype=float,
    )


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
