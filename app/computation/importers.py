# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from app.computation.gamma_spike.original_algorithm.spike_detector_hilbert_v25 import (
    DetectorOutput,
)
from app.computation.gamma_spike.wire_algorithm import (
    GammaSpikeChannelResult,
    GammaSpikeComputationResult,
    GammaSpikeEventResult,
)
from app.computation.hfo.types import (
    HFOChannelResult,
    HFOComputationResult,
    HFOEventResult,
)
from app.computation.rei.algorithm import EIChannelResult, EIComputationResult


ImportedAlgorithm = Literal["ei", "gamma_spike", "hfo"]


@dataclass
class ImportedComputationResult:
    algorithm: ImportedAlgorithm
    result: EIComputationResult | GammaSpikeComputationResult | HFOComputationResult
    source_dir: Path


def import_computation_result(input_dir: Path) -> ImportedComputationResult:
    input_dir = Path(input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Import folder does not exist: {input_dir}")

    if (input_dir / "hfo_events.csv").exists() and (input_dir / "hfo_metadata.json").exists():
        return ImportedComputationResult("hfo", _import_hfo_result(input_dir), input_dir)
    if (
        (input_dir / "gamma_spike_events.csv").exists()
        and (input_dir / "gamma_metadata.json").exists()
    ):
        return ImportedComputationResult(
            "gamma_spike",
            _import_gamma_spike_result(input_dir),
            input_dir,
        )
    if (input_dir / "rei_summary.csv").exists() and (input_dir / "rei_metadata.json").exists():
        return ImportedComputationResult("ei", _import_ei_result(input_dir), input_dir)

    raise ValueError(
        "Unsupported result folder. Expected one of: "
        "hfo_events.csv + hfo_metadata.json, "
        "gamma_spike_events.csv + gamma_metadata.json, "
        "or rei_summary.csv + rei_metadata.json."
    )


def _import_hfo_result(input_dir: Path) -> HFOComputationResult:
    metadata = _read_json(input_dir / "hfo_metadata.json")
    rows = _read_csv(input_dir / "hfo_events.csv")
    if not rows:
        raise ValueError("hfo_events.csv is empty.")

    default_low = _float_or_none(metadata.get("low_freq_hz")) or 80.0
    default_high = _float_or_none(metadata.get("high_freq_hz")) or 300.0
    default_band = str(metadata.get("band_label") or f"{default_low:g}-{default_high:g} Hz")

    events: list[HFOEventResult] = []
    by_channel: dict[str, list[HFOEventResult]] = {}
    for event_number, row in enumerate(rows, start=1):
        channel = _required_text(row, "channel", event_number)
        start_time_s = _float_or_default(row.get("start_time_s"), 0.0)
        end_time_s = _float_or_default(row.get("end_time_s"), start_time_s)
        start_sample = _int_or_default(row.get("start_sample"), 0)
        end_sample = _int_or_default(row.get("end_sample"), start_sample)
        duration_ms = _float_or_none(row.get("duration_ms"))
        if duration_ms is None:
            duration_ms = max(0.0, (end_time_s - start_time_s) * 1000.0)
        peak_time_s = _float_or_none(row.get("peak_time_s"))
        if peak_time_s is None:
            peak_time_s = start_time_s + max(0.0, end_time_s - start_time_s) / 2.0
        manual_review_status = str(row.get("manual_review_status") or "unreviewed")
        manual_class = _normalize_imported_hfo_class(row.get("manual_class"))
        if manual_class is None and manual_review_status.strip().lower() in {"reviewed", "deleted"}:
            manual_class = _normalize_imported_hfo_class(row.get("official_class"))

        manual_class = _none_if_blank(row.get("manual_class"))
        review_status = str(row.get("manual_review_status") or "unreviewed")
        if manual_class is None and review_status.strip().lower() in {"reviewed", "deleted"}:
            manual_class = _none_if_blank(row.get("official_class"))
        event = HFOEventResult(
            event_id=str(row.get("event_id") or f"hfo_{event_number:06d}"),
            channel=channel,
            detector=str(row.get("detector") or ""),
            start_sample=start_sample,
            end_sample=end_sample,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            peak_time_s=peak_time_s,
            duration_ms=duration_ms,
            band_label=str(row.get("band_label") or default_band),
            low_freq_hz=_float_or_default(row.get("low_freq_hz"), default_low),
            high_freq_hz=_float_or_default(row.get("high_freq_hz"), default_high),
            boundary_warning=_bool_value(row.get("boundary_warning")),
            is_boundary=_bool_value(row.get("boundary_warning")),
            real_hfo_probability=_float_or_none(row.get("real_hfo_probability")),
            artifact_probability=_float_or_none(row.get("artifact_probability")),
            spike_hfo_probability=_float_or_none(row.get("spike_hfo_probability")),
            final_model_class=_none_if_blank(row.get("final_model_class")),
            manual_class=manual_class,
            manual_review_status=manual_review_status,
            artifact_score=_float_or_none(row.get("legacy_artifact_score")),
            spike_score=_float_or_none(row.get("legacy_spike_score")),
            hfo_score=_float_or_none(row.get("legacy_hfo_score")),
            classification_label=_none_if_blank(row.get("legacy_classification_label")),
            error=_none_if_blank(row.get("error")),
        )
        events.append(event)
        by_channel.setdefault(channel, []).append(event)

    ordered_channels = _ordered_channels(metadata, by_channel.keys())
    channels = [
        HFOChannelResult(
            channel=channel,
            event_count=len(by_channel.get(channel, [])),
            events=list(by_channel.get(channel, [])),
        )
        for channel in ordered_channels
    ]
    return HFOComputationResult(channels=channels, events=events, metadata=_import_metadata(metadata, input_dir))


def _import_gamma_spike_result(input_dir: Path) -> GammaSpikeComputationResult:
    metadata = _read_json(input_dir / "gamma_metadata.json")
    event_rows = _read_csv(input_dir / "gamma_spike_events.csv")
    summary_rows = _read_optional_csv(input_dir / "gamma_channel_summary.csv")
    if not event_rows and not summary_rows:
        raise ValueError("Gamma export has no events or channel summary rows.")

    fs = (
        _float_or_none(metadata.get("fs"))
        or _float_or_none(metadata.get("sampling_frequency_hz"))
        or 1.0
    )
    data_start_s = _float_or_none(metadata.get("data_start_s")) or 0.0
    summary_count_by_channel = {
        str(row.get("channel") or ""): _int_or_default(row.get("total_spikes"), 0)
        for row in summary_rows
        if row.get("channel")
    }

    events_by_channel: dict[str, list[GammaSpikeEventResult]] = {}
    all_samples: list[float] = []
    all_durations: list[float] = []
    all_channels: list[int] = []
    channel_index: dict[str, int] = {}
    for row_idx, row in enumerate(event_rows, start=1):
        channel = _required_text(row, "channel", row_idx)
        sample = _sample1_to_sample0(row.get("sample"))
        if sample is None:
            time_s = _float_or_default(row.get("time_s"), data_start_s)
            sample = max(0.0, (time_s - data_start_s) * fs)
        else:
            time_s = _float_or_default(row.get("time_s"), data_start_s + sample / fs)

        event = GammaSpikeEventResult(
            sample=float(sample),
            time_s=float(time_s),
            boundary_p1_sample=_sample1_to_sample0(row.get("boundary_p1_sample")),
            boundary_n1_sample=_sample1_to_sample0(row.get("boundary_n1_sample")),
            boundary_n2_sample=_sample1_to_sample0(row.get("boundary_n2_sample")),
            gamma_power=_float_or_none(row.get("gamma_power")),
            gamma_frequency_hz=_float_or_none(row.get("gamma_frequency_hz")),
            gamma_duration_ms=_float_or_none(row.get("gamma_duration_ms")),
            error=_none_if_blank(row.get("error")),
            manual_class=_none_if_blank(row.get("manual_class")),
            manual_review_status=str(row.get("manual_review_status") or "unreviewed"),
        )
        events_by_channel.setdefault(channel, []).append(event)
        if channel not in channel_index:
            channel_index[channel] = len(channel_index)
        all_samples.append(float(sample))
        all_durations.append(float(event.gamma_duration_ms or 0.0))
        all_channels.append(int(channel_index[channel]))

    ordered_channels = _ordered_channels(metadata, list(summary_count_by_channel) or events_by_channel.keys())
    for channel in events_by_channel:
        if channel not in ordered_channels:
            ordered_channels.append(channel)

    channels = []
    for channel in ordered_channels:
        events = list(events_by_channel.get(channel, []))
        samples = np.asarray([event.sample for event in events], dtype=float)
        times = np.asarray([event.time_s for event in events], dtype=float)
        spike_count = summary_count_by_channel.get(channel, len(events))
        channels.append(
            GammaSpikeChannelResult(
                channel=channel,
                spike_count=int(spike_count),
                spike_samples=samples,
                spike_times_s=times,
                events=events,
            )
        )

    detector_output = DetectorOutput(
        pos=np.asarray(all_samples, dtype=float),
        dur=np.asarray(all_durations, dtype=float),
        chan=np.asarray(all_channels, dtype=float),
        con=np.zeros(len(all_samples), dtype=float),
        weight=np.zeros(len(all_samples), dtype=float),
        pdf=np.zeros(len(all_samples), dtype=float),
    )
    return GammaSpikeComputationResult(
        channels=channels,
        detector_output=detector_output,
        metadata=_import_metadata(metadata, input_dir),
    )


def _import_ei_result(input_dir: Path) -> EIComputationResult:
    metadata = _read_json(input_dir / "rei_metadata.json")
    rows = _read_csv(input_dir / "rei_summary.csv")
    if not rows:
        raise ValueError("rei_summary.csv is empty.")

    fs = _float_or_none(metadata.get("sampling_frequency_hz")) or _float_or_none(metadata.get("fs")) or 1.0
    channels = [
        EIChannelResult(
            channel=_required_text(row, "channel", row_idx),
            group=str(row.get("group") or ""),
            ei=_float_or_default(row.get("ei_score"), 0.0),
            rank=_int_or_default(row.get("rank"), row_idx),
            onset_sample_in_ictal_window=int(
                round(_float_or_default(row.get("onset_sec_from_seizure_onset"), 0.0) * fs)
            ),
            onset_sec_from_seizure_onset=_float_or_default(
                row.get("onset_sec_from_seizure_onset"),
                0.0,
            ),
        )
        for row_idx, row in enumerate(rows, start=1)
    ]
    heatmap, heatmap_times, heatmap_channels = _read_ei_heatmap(input_dir / "rei_heatmap.csv")
    return EIComputationResult(
        channels=channels,
        heatmap=heatmap,
        heatmap_times=heatmap_times,
        heatmap_channels=heatmap_channels,
        metadata=_import_metadata(metadata, input_dir),
    )


def _read_ei_heatmap(path: Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if not path.exists():
        return np.empty((0, 0), dtype=float), np.asarray([], dtype=float), []
    rows = _read_csv(path)
    if not rows:
        return np.empty((0, 0), dtype=float), np.asarray([], dtype=float), []
    fieldnames = [name for name in rows[0].keys() if name != "channel"]
    times = np.asarray([_parse_time_header(name) for name in fieldnames], dtype=float)
    channels: list[str] = []
    values: list[list[float]] = []
    for row in rows:
        channels.append(str(row.get("channel") or ""))
        values.append([_float_or_default(row.get(name), np.nan) for name in fieldnames])
    return np.asarray(values, dtype=float), times, channels


def _parse_time_header(name: str) -> float:
    text = str(name).strip()
    if text.endswith("s"):
        text = text[:-1]
    return _float_or_default(text, 0.0)


def _ordered_channels(metadata: dict[str, Any], fallback: Any) -> list[str]:
    candidates = (
        metadata.get("selected_channels")
        or metadata.get("channel_names")
        or metadata.get("channels")
        or []
    )
    ordered = [str(channel) for channel in candidates if str(channel)]
    for channel in fallback:
        channel_text = str(channel)
        if channel_text and channel_text not in ordered:
            ordered.append(channel_text)
    return ordered


def _import_metadata(metadata: dict[str, Any], input_dir: Path) -> dict[str, Any]:
    imported = dict(metadata)
    imported["imported"] = True
    imported["imported_from"] = str(input_dir)
    imported["import_format"] = "native_export_folder_v1"
    return imported


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing metadata file: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV file: {path.name}")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_optional_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return _read_csv(path)


def _required_text(row: dict[str, Any], field: str, row_number: int) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"Missing required field '{field}' in row {row_number}.")
    return value


def _none_if_blank(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_imported_hfo_class(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower().replace("_", "-")
    if lowered in {"deleted", "excluded"}:
        return "deleted"
    if "artifact" in lowered:
        return "artifact"
    if lowered in {"spike-ehfo", "spike ehfo", "spkehfo", "spk-ehfo", "spk ehfo"}:
        return "spike-eHFO"
    if lowered in {"ehfo", "e-hfo"}:
        return "eHFO"
    if lowered in {"spike-hfo", "spkhfo", "spk-hfo", "spk hfo", "spike hfo"}:
        return "spike-HFO"
    if lowered in {"hfo", "real hfo", "real-hfo"}:
        return "HFO"
    if lowered in {"non-spike hfo", "non-spkhfo", "non-spk hfo"}:
        return "non-spike HFO"
    if lowered in {"unclassified", "candidate", "unknown", "not-classified"}:
        return "unclassified"
    return text


def _float_or_none(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _float_or_default(value: Any, default: float) -> float:
    numeric = _float_or_none(value)
    return float(default) if numeric is None else float(numeric)


def _int_or_default(value: Any, default: int) -> int:
    numeric = _float_or_none(value)
    if numeric is None:
        return int(default)
    return int(round(float(numeric)))


def _sample1_to_sample0(value: Any) -> float | None:
    numeric = _float_or_none(value)
    if numeric is None:
        return None
    return max(0.0, float(numeric) - 1.0)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}
