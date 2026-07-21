from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass
class EIChannelResult:
    channel: str
    group: str
    ei: float
    rank: int
    onset_sample_in_ictal_window: int
    onset_sec_from_seizure_onset: float


@dataclass
class EIComputationResult:
    channels: list[EIChannelResult]
    heatmap: Array
    heatmap_times: Array
    heatmap_channels: list[str]
    metadata: dict[str, Any]


def compute_hfer(
    target_data: Array,
    base_data: Array,
    fs: float,
) -> tuple[Array, Array]: ...


def bandpass_hf(
    data: Array,
    fs: float,
    *,
    low_freq: float = 60.0,
    high_freq: float = 140.0,
) -> Array: ...


def apply_notch_by_channel(
    data: np.ndarray,
    fs: float,
    notch_modes: list[str] | None,
) -> Array: ...


def compute_ei_from_windows(
    data: np.ndarray,
    fs: float,
    baseline_samples: tuple[int, int],
    ictal_samples: tuple[int, int],
    *,
    low_freq: float = 60.0,
    high_freq: float = 140.0,
) -> tuple[Array, Array, Array]: ...


def validate_gui_ei_timing(
    *,
    seizure_onset_s: float,
    seizure_offset_s: float,
    baseline_window_s: tuple[float, float],
    ictal_window_s: tuple[float, float],
    recording_duration_s: float | None = None,
) -> None: ...


def compute_ei_for_gui(
    data: np.ndarray,
    fs: float,
    channel_names: list[str],
    *,
    data_start_s: float,
    seizure_onset_s: float,
    seizure_offset_s: float,
    baseline_window_s: tuple[float, float],
    ictal_window_s: tuple[float, float],
    channel_groups: dict[str, str] | None = None,
    bad_channels: set[str] | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
    low_freq: float = 60.0,
    high_freq: float = 140.0,
    metadata: dict[str, Any] | None = None,
) -> EIComputationResult: ...
