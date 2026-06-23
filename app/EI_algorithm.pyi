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
    onset_sec_in_ictal_window: float

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
    metadata: dict[str, Any] | None = None,
) -> EIComputationResult: ...
