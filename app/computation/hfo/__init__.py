"""HFO computation package."""

from app.computation.hfo.algorithm import compute_hfo_for_gui
from app.computation.hfo.detectors.omni_hfo_detector import DEFAULT_CANDIDATE_DETECTORS
from app.computation.hfo.input_preparation import HFOInput, prepare_hfo_input_from_array, prepare_hfo_input_from_file
from app.computation.hfo.types import HFOChannelResult, HFOComputationResult, HFOEventResult

__all__ = [
    "DEFAULT_CANDIDATE_DETECTORS",
    "HFOInput",
    "HFOChannelResult",
    "HFOComputationResult",
    "HFOEventResult",
    "compute_hfo_for_gui",
    "prepare_hfo_input_from_array",
    "prepare_hfo_input_from_file",
]
