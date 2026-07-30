from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HFOEventResult:
    event_id: str
    channel: str
    detector: str
    start_sample: int
    end_sample: int
    start_time_s: float
    end_time_s: float
    peak_time_s: float
    duration_ms: float
    band_label: str
    low_freq_hz: float
    high_freq_hz: float
    waveform: object | None = None
    real_start_sample: int | None = None
    real_end_sample: int | None = None
    is_boundary: bool = False
    boundary_warning: bool = False
    real_hfo_probability: float | None = None
    artifact_probability: float | None = None
    spike_hfo_probability: float | None = None
    final_model_class: str | None = None
    manual_class: str | None = None
    manual_review_status: str = "unreviewed"
    artifact_score: float | None = None
    spike_score: float | None = None
    hfo_score: float | None = None
    classification_label: str | None = None
    error: str | None = None


@dataclass
class HFOChannelResult:
    channel: str
    event_count: int
    events: list[HFOEventResult]


@dataclass
class HFOComputationResult:
    channels: list[HFOChannelResult]
    events: list[HFOEventResult]
    metadata: dict
