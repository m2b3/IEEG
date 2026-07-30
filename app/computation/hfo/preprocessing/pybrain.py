"""pyHFO/pyBrain-compatible HFO input preparation.

This module deliberately preserves the native sampling frequency. The original
pyHFO/pyBrain GUI performs detection and feature construction at the EDF
sampling rate rather than forcing the Omni 1000 Hz processing rate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from scipy.signal import cheb2ord, cheby2, sosfilt, zpk2sos

from app.computation.hfo.preprocessing.omni import (
    HFOInput,
    prepare_hfo_input_from_array,
    prepare_hfo_input_from_file,
)


def prepare_pybrain_hfo_input_from_array(
    *,
    data_uv,
    fs: float,
    channel_names: list[str] | tuple[str, ...],
    data_start_s: float,
    analysis_window_s: tuple[float, float],
    data_is_interval_sliced: bool = True,
    selected_channels: Iterable[str] | None = None,
    bad_channels: Iterable[str] | None = None,
    reference_mode: str = "none",
    bipolar_pairs: Sequence[tuple[str, str]] | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
) -> HFOInput:
    prepared = prepare_hfo_input_from_array(
        data_uv=data_uv,
        fs=float(fs),
        channel_names=channel_names,
        data_start_s=float(data_start_s),
        analysis_window_s=analysis_window_s,
        data_is_interval_sliced=bool(data_is_interval_sliced),
        selected_channels=selected_channels,
        bad_channels=bad_channels,
        reference_mode=reference_mode,
        bipolar_pairs=bipolar_pairs,
        notch_modes_by_channel=notch_modes_by_channel,
    )
    prepared.input_boundary = (
        "GUI/file layer provides selected, referenced/montaged, microvolt "
        "signal array; pyBrain preprocessing preserves native sampling"
    )
    prepared.processing_order.append("preserve native sampling for pyBrain path")
    prepared.preprocessing_log.append(f"pyBrain native sampling: {float(fs):g} Hz")
    return prepared


def prepare_pybrain_hfo_input_from_file(
    file_path: str | Path,
    *,
    analysis_window_s: tuple[float, float],
    selected_channels: Iterable[str] | None = None,
    bad_channels: Iterable[str] | None = None,
    reference_mode: str = "none",
    bipolar_pairs: Sequence[tuple[str, str]] | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
) -> HFOInput:
    prepared = prepare_hfo_input_from_file(
        file_path,
        analysis_window_s=analysis_window_s,
        selected_channels=selected_channels,
        bad_channels=bad_channels,
        reference_mode=reference_mode,
        bipolar_pairs=bipolar_pairs,
        notch_modes_by_channel=notch_modes_by_channel,
    )
    prepared.input_boundary = (
        "backend file loader read raw file segment and prepared the HFO signal "
        "array; pyBrain preprocessing preserves native sampling"
    )
    prepared.processing_order.append("preserve native sampling for pyBrain path")
    prepared.preprocessing_log.append(f"pyBrain native sampling: {float(prepared.fs):g} Hz")
    return prepared


def apply_pybrain_bandpass(
    data_uv,
    fs: float,
    *,
    pass_band_hz: float,
    stop_band_hz: float,
    ripple_db: float = 0.5,
    attenuation_db: float = 93.0,
    transition_space_hz: float = 0.5,
):
    """Apply pyBrain's Chebyshev-II HFO bandpass preprocessing."""
    matrix = np.asarray(data_uv, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("pyBrain HFO bandpass expects a channel x sample array.")
    sos = construct_pybrain_filter(
        pass_band_hz=float(pass_band_hz),
        stop_band_hz=float(stop_band_hz),
        ripple_db=float(ripple_db),
        attenuation_db=float(attenuation_db),
        transition_space_hz=float(transition_space_hz),
        fs=float(fs),
    )
    rows = []
    for row in matrix:
        filtered = sosfilt(sos, np.asarray(row, dtype=float))
        filtered = sosfilt(sos, np.flipud(filtered))
        rows.append(np.flipud(filtered))
    return np.asarray(rows, dtype=float)


def construct_pybrain_filter(
    *,
    pass_band_hz: float,
    stop_band_hz: float,
    ripple_db: float,
    attenuation_db: float,
    transition_space_hz: float,
    fs: float,
):
    if float(stop_band_hz) < 1.0 or float(attenuation_db) < 1.0:
        raise ValueError("Invalid pyBrain HFO filter stop band or attenuation.")
    nyquist = float(fs) / 2.0
    if float(pass_band_hz) <= 0.0 or float(pass_band_hz) >= nyquist:
        raise ValueError("Invalid pyBrain HFO filter pass band.")
    high = min(float(stop_band_hz), nyquist * 0.99)
    if high <= float(pass_band_hz):
        raise ValueError("pyBrain HFO filter high band must be above low band.")

    low_stop = _transition_edge(float(pass_band_hz), -float(transition_space_hz))
    high_stop = _transition_edge(high, float(transition_space_hz))
    high_stop = min(high_stop, nyquist * 0.999)
    if low_stop <= 0.0:
        low_stop = max(1e-6, float(pass_band_hz) * 0.5)
    if high_stop <= high:
        high_stop = min(nyquist * 0.999, high + max(1e-6, high * 0.001))

    order, stop = cheb2ord(
        [float(pass_band_hz) / nyquist, high / nyquist],
        [low_stop / nyquist, high_stop / nyquist],
        float(ripple_db),
        float(attenuation_db),
    )
    z, p, k = cheby2(order, float(attenuation_db), stop, btype="bandpass", analog=0, output="zpk")
    return zpk2sos(z, p, k)


def _transition_edge(value: float, delta: float) -> float:
    scale = 0
    probe = float(value)
    while 0 < probe < 1:
        probe *= 10
        scale += 1
    return float(value) + (float(delta) * 10 ** (-1 * scale))
