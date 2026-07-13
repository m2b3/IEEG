"""Gamma spike detector package."""

from app.computation.gamma_spike.original_algorithm.compute_gamma import compute_gamma
from app.computation.gamma_spike.original_algorithm.compute_spike_boundary import (
    compute_spike_boundary,
)
from app.computation.gamma_spike.original_algorithm.postprocessing import postprocessing
from app.computation.gamma_spike.original_algorithm.spike_detector_hilbert_v25 import (
    DetectorOutput,
    DetectorSettings,
    Discharges,
    parse_settings,
    spike_detector_hilbert_v25,
)
from app.computation.gamma_spike.wire_algorithm import (
    GammaSpikeChannelResult,
    GammaSpikeComputationResult,
    GammaSpikeEventResult,
    compute_gamma_spike_for_gui,
)

__all__ = [
    "DetectorOutput",
    "DetectorSettings",
    "Discharges",
    "GammaSpikeChannelResult",
    "GammaSpikeComputationResult",
    "GammaSpikeEventResult",
    "compute_gamma",
    "compute_gamma_spike_for_gui",
    "compute_spike_boundary",
    "parse_settings",
    "postprocessing",
    "spike_detector_hilbert_v25",
]
