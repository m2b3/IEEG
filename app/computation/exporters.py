# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.computation.gamma_spike.wire_algorithm import GammaSpikeComputationResult
from app.computation.hfo.types import HFOComputationResult
from app.computation.rei.algorithm import EIComputationResult


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    return value


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_safe(data), indent=2),
        encoding="utf-8",
    )


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_safe(row.get(key)) for key in fieldnames})


def _csv_safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return ""
    return value


def export_ei_result(output_dir: Path, result: EIComputationResult) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    peak_hfer_by_channel = _ei_peak_hfer_by_channel(result)

    summary_rows: list[dict[str, Any]] = []
    for row in result.channels:
        onset_from_seizure_s = _finite_float(row.onset_sec_from_seizure_onset)
        summary_rows.append(
            {
                "channel": row.channel,
                "group": row.group,
                "ei_score": float(row.ei),
                "rank": int(row.rank),
                "onset_sec_from_seizure_onset": onset_from_seizure_s,
                "peak_hfer_activity": peak_hfer_by_channel.get(str(row.channel)),
            }
        )

    summary_path = output_dir / "rei_summary.csv"
    _write_rows(
        summary_path,
        [
            "channel",
            "group",
            "ei_score",
            "rank",
            "onset_sec_from_seizure_onset",
            "peak_hfer_activity",
        ],
        summary_rows,
    )

    heatmap_path = output_dir / "rei_heatmap.csv"
    _write_ei_heatmap_csv(heatmap_path, result)

    heatmap_figure_path = output_dir / "rei_heatmap.png"
    _write_ei_heatmap_figure(heatmap_figure_path, result)

    metadata_path = output_dir / "rei_metadata.json"
    _write_json(metadata_path, _compact_ei_metadata(metadata))

    readme_path = output_dir / "README.txt"
    _write_readme(readme_path, _rei_readme_text())

    return [summary_path, heatmap_path, heatmap_figure_path, metadata_path, readme_path]


def _ei_peak_hfer_by_channel(result: EIComputationResult) -> dict[str, float]:
    heatmap = np.asarray(result.heatmap, dtype=float)
    channel_names = [str(channel) for channel in (result.heatmap_channels or [])]
    if heatmap.ndim != 2 or not heatmap.size or not channel_names:
        return {}
    peaks: dict[str, float] = {}
    for row_idx, channel_name in enumerate(channel_names[: int(heatmap.shape[0])]):
        row = np.asarray(heatmap[row_idx], dtype=float)
        finite_values = row[np.isfinite(row)]
        if finite_values.size:
            peaks[channel_name] = float(np.max(finite_values))
    return peaks


def _compact_ei_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "algorithm": metadata.get("algorithm", "Recruitment Energy Index"),
        "source_file_name": metadata.get("source_file_name"),
        "source_file_path": metadata.get("source_file_path"),
        "montage_used": metadata.get("montage_used"),
        "recommended_montage": metadata.get("recommended_montage"),
        "seizure_onset_s": _finite_float(metadata.get("seizure_onset_s")),
        "seizure_offset_s": _finite_float(metadata.get("seizure_offset_s")),
        "baseline_window_s": _json_safe(metadata.get("baseline_window_s")),
        "ictal_window_s": _json_safe(metadata.get("ictal_window_s")),
        "sampling_frequency_hz": _finite_float(metadata.get("fs")),
        "n_channels_input": int(metadata.get("n_channels_input", 0) or 0),
        "n_channels_computed": int(metadata.get("n_channels_computed", 0) or 0),
        "bad_channels_excluded": bool(metadata.get("bad_channels_excluded", False)),
        "excluded_bad_channels": _json_safe(
            metadata.get("excluded_bad_channels", [])
        ),
        "display_filter_used_for_computation": bool(
            metadata.get(
                "display_filter_used_for_computation",
                metadata.get("uses_display_filter", False),
            )
        ),
        "analysis_filter": _json_safe(metadata.get("analysis_filter")),
        "notch_filter": bool(metadata.get("notch_filter", False)),
        "notch_modes": _json_safe(metadata.get("notch_modes", [])),
        "threshold_sigma": _finite_float(metadata.get("threshold_sigma")),
        "energy_window_sec": _finite_float(metadata.get("energy_window_sec")),
        "hfer_window_sec": _finite_float(metadata.get("hfer_window_sec")),
        "baseline_samples": _json_safe(metadata.get("baseline_samples")),
        "ictal_samples": _json_safe(metadata.get("ictal_samples")),
        "output_files": [
            "rei_summary.csv",
            "rei_heatmap.csv",
            "rei_heatmap.png",
            "rei_metadata.json",
            "README.txt",
        ],
    }
    return compact


def _write_ei_heatmap_csv(path: Path, result: EIComputationResult) -> None:
    heatmap = np.asarray(result.heatmap, dtype=float)
    times = np.asarray(result.heatmap_times, dtype=float).reshape(-1)
    channels = [str(channel) for channel in (result.heatmap_channels or [])]
    n_rows = int(heatmap.shape[0]) if heatmap.ndim == 2 else 0
    n_cols = int(heatmap.shape[1]) if heatmap.ndim == 2 else 0
    usable_rows = min(n_rows, len(channels))
    usable_cols = min(n_cols, int(times.size))

    fieldnames = ["channel"] + [f"{times[idx]:.6f}s" for idx in range(usable_cols)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row_idx in range(usable_rows):
            values = [
                _csv_safe(float(heatmap[row_idx, col_idx]))
                for col_idx in range(usable_cols)
            ]
            writer.writerow([channels[row_idx], *values])


def _write_ei_heatmap_figure(path: Path, result: EIComputationResult) -> None:
    plt = _pyplot()
    heatmap = np.asarray(result.heatmap, dtype=float)
    times = np.asarray(result.heatmap_times, dtype=float).reshape(-1)
    channels = [str(channel) for channel in (result.heatmap_channels or [])]
    if heatmap.ndim != 2 or not heatmap.size or not channels:
        return
    n_rows = min(int(heatmap.shape[0]), len(channels))
    heatmap = heatmap[:n_rows, :]
    channels = channels[:n_rows]
    times = _ei_heatmap_times_from_metadata(times, result.metadata)

    log_heatmap = np.log10(np.maximum(heatmap, 1e-6))
    width = max(7.0, min(18.0, 0.025 * max(1, log_heatmap.shape[1])))
    height = max(4.5, min(18.0, 0.22 * n_rows + 1.8))
    fig, ax = plt.subplots(figsize=(width, height), constrained_layout=True)
    extent = _image_extent(times, n_rows)
    image = ax.imshow(
        log_heatmap,
        aspect="auto",
        interpolation="nearest",
        origin="upper",
        extent=extent,
        cmap="viridis",
    )
    ax.axvline(0.0, color="white", linestyle="--", linewidth=1.0, alpha=0.85)
    ax.set_title("REI heatmap")
    ax.set_xlabel("Time from seizure onset (s)")
    ax.set_ylabel("Channel")
    ax.set_yticks(np.arange(n_rows, dtype=float))
    ax.set_yticklabels(channels, fontsize=8)
    fig.colorbar(image, ax=ax, label="log10 HFER activity")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _ei_heatmap_times_from_metadata(times: np.ndarray, metadata: dict | None) -> np.ndarray:
    adjusted = np.asarray(times, dtype=float).reshape(-1)
    if adjusted.size == 0 or not isinstance(metadata, dict):
        return adjusted
    seizure_onset = _finite_float(metadata.get("seizure_onset_s"))
    ictal_window = metadata.get("ictal_window_s")
    if (
        seizure_onset is not None
        and isinstance(ictal_window, list)
        and len(ictal_window) >= 2
    ):
        ictal_start = _finite_float(ictal_window[0])
        if ictal_start is not None:
            adjusted = adjusted + (ictal_start - seizure_onset)
    return adjusted


def _image_extent(times: np.ndarray, n_rows: int) -> list[float]:
    if times.size >= 2:
        dt = float(np.median(np.diff(times)))
        x0 = float(times[0]) - 0.5 * dt
        x1 = float(times[-1]) + 0.5 * dt
    elif times.size == 1:
        x0 = float(times[0]) - 0.5
        x1 = float(times[0]) + 0.5
    else:
        x0, x1 = 0.0, 1.0
    return [x0, x1, float(n_rows) - 0.5, -0.5]


def export_gamma_spike_result(
    output_dir: Path,
    result: GammaSpikeComputationResult,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    fs = _finite_float(metadata.get("fs")) or 0.0
    data_start_s = _finite_float(metadata.get("data_start_s")) or 0.0

    for channel_result in result.channels:
        gamma_events = [
            event
            for event in channel_result.events
            if _gamma_event_official_is_gamma(event)
        ]
        total_spikes = int(channel_result.spike_count)
        gamma_spikes = len(gamma_events)
        powers = np.asarray(
            [
                float(event.gamma_power)
                for event in gamma_events
                if event.gamma_power is not None
            ],
            dtype=float,
        )
        durations = np.asarray(
            [
                float(event.gamma_duration_ms)
                for event in gamma_events
                if event.gamma_duration_ms is not None
            ],
            dtype=float,
        )
        finite_powers = powers[np.isfinite(powers)]
        finite_durations = durations[np.isfinite(durations)]
        summary_rows.append(
            {
                "channel": channel_result.channel,
                "total_spikes": total_spikes,
                "gamma_spikes": gamma_spikes,
                "non_gamma_spikes": max(0, total_spikes - gamma_spikes),
                "spike_gamma_rate": (
                    float(gamma_spikes) / float(total_spikes)
                    if total_spikes > 0
                    else 0.0
                ),
                "mean_gamma_power": (
                    float(np.mean(finite_powers)) if finite_powers.size else None
                ),
                "mean_gamma_duration_ms": (
                    float(np.mean(finite_durations)) if finite_durations.size else None
                ),
            }
        )

        for event_number, event in enumerate(channel_result.events, start=1):
            event_rows.append(
                {
                    "channel": channel_result.channel,
                    "event_number": int(event_number),
                    "sample": _sample0_to_export_sample1(event.sample),
                    "time_s": _sample0_to_export_time(
                        event.sample,
                        fs,
                        data_start_s,
                    ),
                    "is_gamma": _gamma_event_is_gamma(
                        event.gamma_power,
                        event.gamma_duration_ms,
                    ),
                    "model_class": (
                        "gamma"
                        if _gamma_event_is_gamma(event.gamma_power, event.gamma_duration_ms)
                        else "non-gamma"
                    ),
                    "official_class": _gamma_event_official_class(event),
                    "boundary_p1_sample": _sample0_to_export_sample1(
                        event.boundary_p1_sample
                    ),
                    "boundary_n1_sample": _sample0_to_export_sample1(
                        event.boundary_n1_sample
                    ),
                    "boundary_n2_sample": _sample0_to_export_sample1(
                        event.boundary_n2_sample
                    ),
                    "boundary_p1_time_s": _sample0_to_export_time(
                        event.boundary_p1_sample,
                        fs,
                        data_start_s,
                    ),
                    "boundary_n1_time_s": _sample0_to_export_time(
                        event.boundary_n1_sample,
                        fs,
                        data_start_s,
                    ),
                    "boundary_n2_time_s": _sample0_to_export_time(
                        event.boundary_n2_sample,
                        fs,
                        data_start_s,
                    ),
                    "gamma_power": _finite_float(event.gamma_power),
                    "gamma_frequency_hz": _finite_float(event.gamma_frequency_hz),
                    "gamma_duration_ms": _finite_float(event.gamma_duration_ms),
                    "manual_class": getattr(event, "manual_class", None),
                    "manual_review_status": getattr(event, "manual_review_status", "unreviewed"),
                    "error": event.error,
                }
            )

    summary_path = output_dir / "gamma_channel_summary.csv"
    _write_rows(
        summary_path,
        [
            "channel",
            "total_spikes",
            "gamma_spikes",
            "non_gamma_spikes",
            "spike_gamma_rate",
            "mean_gamma_power",
            "mean_gamma_duration_ms",
        ],
        summary_rows,
    )

    events_path = output_dir / "gamma_spike_events.csv"
    _write_rows(
        events_path,
        [
            "channel",
            "event_number",
            "sample",
            "time_s",
            "is_gamma",
            "model_class",
            "official_class",
            "boundary_p1_sample",
            "boundary_n1_sample",
            "boundary_n2_sample",
            "boundary_p1_time_s",
            "boundary_n1_time_s",
            "boundary_n2_time_s",
            "gamma_power",
            "gamma_frequency_hz",
            "gamma_duration_ms",
            "manual_class",
            "manual_review_status",
            "error",
        ],
        event_rows,
    )

    metadata_path = output_dir / "gamma_metadata.json"
    _write_json(metadata_path, _compact_gamma_metadata(metadata, summary_rows))

    readme_path = output_dir / "README.txt"
    _write_readme(readme_path, _gamma_readme_text())

    return [summary_path, events_path, metadata_path, readme_path]


def export_hfo_result(output_dir: Path, result: HFOComputationResult) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    include_ehfo = str(metadata.get("detector_version", "") or "").strip().lower() == "ehfo"
    analysis_duration_min = _hfo_analysis_duration_min(metadata)

    summary_rows: list[dict[str, Any]] = []
    for channel_result in result.channels:
        channel_events = list(channel_result.events)
        active_channel_events = [
            event for event in channel_events
            if _hfo_event_class(event, include_ehfo=include_ehfo) != "deleted"
        ]
        candidate_count = len(active_channel_events)
        deleted_count = len(channel_events) - candidate_count
        event_classes = [
            _hfo_event_class(event, include_ehfo=include_ehfo)
            for event in active_channel_events
        ]
        artifact_count = sum(1 for label in event_classes if label == "artifact")
        spike_count = sum(1 for label in event_classes if label == "spike-HFO")
        non_spike_count = sum(1 for label in event_classes if label == "HFO")
        ehfo_count = sum(1 for label in event_classes if label == "eHFO")
        spike_ehfo_count = sum(1 for label in event_classes if label == "spike-eHFO")
        accepted_count = non_spike_count + spike_count + ehfo_count + spike_ehfo_count
        boundary_count = sum(1 for event in active_channel_events if bool(getattr(event, "boundary_warning", event.is_boundary)))
        summary_row = {
            "channel": channel_result.channel,
            "candidate_count": candidate_count,
            "accepted_hfo_count": accepted_count,
            "non_spike_hfo_count": non_spike_count,
            "spike_hfo_count": spike_count,
            "artifact_count": artifact_count,
            "candidate_rate_per_min": _rate_per_min(candidate_count, analysis_duration_min),
            "accepted_hfo_rate_per_min": _rate_per_min(accepted_count, analysis_duration_min),
            "non_spike_hfo_rate_per_min": _rate_per_min(non_spike_count, analysis_duration_min),
            "spike_hfo_rate_per_min": _rate_per_min(spike_count, analysis_duration_min),
            "artifact_percentage": (
                100.0 * float(artifact_count) / float(candidate_count)
                if candidate_count > 0
                else 0.0
            ),
            "deleted_event_count": deleted_count,
            "boundary_event_count": boundary_count,
        }
        if include_ehfo:
            summary_row.update(
                {
                    "ehfo_count": ehfo_count,
                    "spike_ehfo_count": spike_ehfo_count,
                    "ehfo_rate_per_min": _rate_per_min(ehfo_count, analysis_duration_min),
                    "spike_ehfo_rate_per_min": _rate_per_min(spike_ehfo_count, analysis_duration_min),
                }
            )
        summary_rows.append(summary_row)

    event_rows: list[dict[str, Any]] = []
    for event_number, event in enumerate(result.events, start=1):
        event_rows.append(
            {
                "event_id": str(getattr(event, "event_id", "") or f"hfo_{event_number:06d}"),
                "channel": event.channel,
                "start_time_s": _finite_float(event.start_time_s),
                "end_time_s": _finite_float(event.end_time_s),
                "duration_ms": _finite_float(event.duration_ms),
                "detector": event.detector,
                "boundary_warning": bool(getattr(event, "boundary_warning", event.is_boundary)),
                "real_hfo_probability": _finite_float(getattr(event, "real_hfo_probability", None)),
                "artifact_probability": _finite_float(getattr(event, "artifact_probability", event.artifact_score)),
                "spike_hfo_probability": _finite_float(getattr(event, "spike_hfo_probability", event.spike_score)),
                "final_model_class": getattr(event, "final_model_class", None) or event.classification_label,
                "manual_class": getattr(event, "manual_class", None),
                "official_class": _hfo_event_class(event, include_ehfo=include_ehfo),
                "manual_review_status": getattr(event, "manual_review_status", "unreviewed"),
                "start_sample": int(event.start_sample),
                "end_sample": int(event.end_sample),
                "peak_time_s": _finite_float(event.peak_time_s),
                "band_label": event.band_label,
                "low_freq_hz": _finite_float(event.low_freq_hz),
                "high_freq_hz": _finite_float(event.high_freq_hz),
                "effective_high_freq_hz": _finite_float(metadata.get("effective_high_freq_hz")),
                "input_sampling_frequency_hz": _finite_float(metadata.get("input_fs")),
                "detection_sampling_frequency_hz": _finite_float(metadata.get("detection_fs")),
                "legacy_artifact_score": _finite_float(event.artifact_score),
                "legacy_spike_score": _finite_float(event.spike_score),
                "legacy_hfo_score": _finite_float(event.hfo_score),
                "legacy_classification_label": event.classification_label,
                "error": event.error,
            }
        )

    summary_path = output_dir / "hfo_channel_summary.csv"
    summary_fields = [
        "channel",
        "candidate_count",
        "accepted_hfo_count",
        "non_spike_hfo_count",
        "spike_hfo_count",
    ]
    if include_ehfo:
        summary_fields.extend(["ehfo_count", "spike_ehfo_count"])
    summary_fields.extend(
        [
            "artifact_count",
            "candidate_rate_per_min",
            "accepted_hfo_rate_per_min",
            "non_spike_hfo_rate_per_min",
            "spike_hfo_rate_per_min",
        ]
    )
    if include_ehfo:
        summary_fields.extend(["ehfo_rate_per_min", "spike_ehfo_rate_per_min"])
    summary_fields.extend(
        ["artifact_percentage", "deleted_event_count", "boundary_event_count"]
    )
    _write_rows(
        summary_path,
        summary_fields,
        summary_rows,
    )

    events_path = output_dir / "hfo_events.csv"
    _write_rows(
        events_path,
        [
            "event_id",
            "channel",
            "start_time_s",
            "end_time_s",
            "duration_ms",
            "detector",
            "boundary_warning",
            "real_hfo_probability",
            "artifact_probability",
            "spike_hfo_probability",
            "final_model_class",
            "manual_class",
            "official_class",
            "manual_review_status",
            "start_sample",
            "end_sample",
            "peak_time_s",
            "band_label",
            "low_freq_hz",
            "high_freq_hz",
            "effective_high_freq_hz",
            "input_sampling_frequency_hz",
            "detection_sampling_frequency_hz",
            "legacy_artifact_score",
            "legacy_spike_score",
            "legacy_hfo_score",
            "legacy_classification_label",
            "error",
        ],
        event_rows,
    )

    metadata_path = output_dir / "hfo_metadata.json"
    _write_json(metadata_path, _compact_hfo_metadata(metadata))

    readme_path = output_dir / "README.txt"
    _write_readme(
        readme_path,
        _hfo_readme_text(
            str(metadata.get("detector_version", "") or ""),
            include_ehfo=include_ehfo,
        ),
    )

    return [summary_path, events_path, metadata_path, readme_path]


def _hfo_analysis_duration_min(metadata: dict[str, Any]) -> float:
    window_s = metadata.get("analysis_window_s")
    if isinstance(window_s, (list, tuple)) and len(window_s) >= 2:
        start = _finite_float(window_s[0])
        stop = _finite_float(window_s[1])
        if start is not None and stop is not None and stop > start:
            return (float(stop) - float(start)) / 60.0
    return 0.0


def _rate_per_min(count: int, duration_min: float) -> float:
    return float(count) / float(duration_min) if float(duration_min) > 0.0 else 0.0


def _hfo_event_class(event: Any, *, include_ehfo: bool = True) -> str:
    label = str(
        getattr(event, "manual_class", None)
        or getattr(event, "final_model_class", None)
        or getattr(event, "classification_label", "")
        or ""
    ).strip().lower().replace("_", "-")
    if label in {"deleted", "excluded"}:
        return "deleted"
    if "artifact" in label:
        return "artifact"
    if label in {"spike-ehfo", "spkehfo", "spk-ehfo", "spk ehfo", "spike ehfo"}:
        return "spike-eHFO" if include_ehfo else "spike-HFO"
    if label in {"ehfo", "e-hfo"}:
        return "eHFO" if include_ehfo else "HFO"
    if label in {"spike-hfo", "spkhfo", "spk-hfo", "spk hfo", "spike hfo"}:
        return "spike-HFO"
    if label in {"hfo", "real hfo", "real-hfo", "non-spike hfo", "non-spkhfo", "non-spk hfo"}:
        return "HFO"
    return "candidate"


def _compact_hfo_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "algorithm": metadata.get("algorithm", "HFO"),
        "source_file_name": metadata.get("source_file_name"),
        "source_file_path": metadata.get("source_file_path"),
        "analysis_window_s": _json_safe(metadata.get("analysis_window_s")),
        "input_sampling_frequency_hz": _finite_float(metadata.get("input_fs")),
        "detection_sampling_frequency_hz": _finite_float(metadata.get("detection_fs")),
        "resampled_to_hz": _finite_float(metadata.get("resampled_to_hz")),
        "input_units": metadata.get("input_units"),
        "detector_version": metadata.get("detector_version"),
        "algorithm_details": _json_safe(metadata.get("algorithm_details", {})),
        "source_repositories": _json_safe(metadata.get("source_repositories", [])),
        "active_candidate_detectors": _json_safe(
            metadata.get("active_candidate_detectors", [])
        ),
        "band_label": metadata.get("band_label"),
        "low_freq_hz": _finite_float(metadata.get("low_freq_hz")),
        "high_freq_hz": _finite_float(metadata.get("high_freq_hz")),
        "effective_high_freq_hz": _finite_float(metadata.get("effective_high_freq_hz")),
        "threshold_sigma": _finite_float(metadata.get("threshold_sigma")),
        "min_duration_ms": _finite_float(metadata.get("min_duration_ms")),
        "max_duration_ms": _finite_float(metadata.get("max_duration_ms")),
        "raw_candidate_events": int(metadata.get("raw_candidate_events", 0) or 0),
        "duration_excluded_events": int(metadata.get("duration_excluded_events", 0) or 0),
        "boundary_padding_s": _finite_float(metadata.get("boundary_padding_s")),
        "boundary_excluded_events": int(metadata.get("boundary_excluded_events", 0) or 0),
        "merge_gap_ms": _finite_float(metadata.get("merge_gap_ms")),
        "min_cycles": _finite_float(metadata.get("min_cycles")),
        "detector_parameters": _json_safe(metadata.get("detector_parameters", {})),
        "event_window_ms": _finite_float(metadata.get("event_window_ms")),
        "notch_filter": bool(metadata.get("notch_filter", False)),
        "notch_modes": _json_safe(metadata.get("notch_modes", [])),
        "input_boundary": metadata.get("input_boundary"),
        "processing_order": _json_safe(metadata.get("processing_order", [])),
        "selected_channels": _json_safe(metadata.get("selected_channels", [])),
        "bad_channels_excluded": _json_safe(metadata.get("bad_channels_excluded", [])),
        "reference_mode": metadata.get("reference_mode"),
        "bipolar_pairs": _json_safe(metadata.get("bipolar_pairs", [])),
        "preprocessing_log": _json_safe(metadata.get("preprocessing_log", [])),
        "classification_status": metadata.get("classification_status"),
        "classifier_implementation": metadata.get("classifier_implementation"),
        "classifier_origin": _json_safe(metadata.get("classifier_origin", {})),
        "classifier_feature_freq_range_hz": _json_safe(
            metadata.get("classifier_feature_freq_range_hz")
        ),
        "classification_label_counts": _json_safe(metadata.get("classification_label_counts", {})),
        "official_label_counts": _json_safe(metadata.get("official_label_counts", {})),
        "manual_reviewed_events": int(metadata.get("manual_reviewed_events", 0) or 0),
        "manual_deleted_events": int(metadata.get("manual_deleted_events", 0) or 0),
        "total_events": int(metadata.get("total_events", 0) or 0),
        "n_channels": int(metadata.get("n_channels", 0) or 0),
        "n_samples": int(metadata.get("n_samples", 0) or 0),
        "output_files": [
            "hfo_channel_summary.csv",
            "hfo_events.csv",
            "hfo_metadata.json",
            "README.txt",
        ],
    }


def _compact_gamma_metadata(
    metadata: dict[str, Any],
    summary_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    fs = _finite_float(metadata.get("fs"))
    n_samples = int(metadata.get("n_samples", 0) or 0)
    return {
        "algorithm": "gamma_spike",
        "source_file_name": metadata.get("source_file_name"),
        "source_file_path": metadata.get("source_file_path"),
        "analysis_window_s": _json_safe(metadata.get("analysis_window_s")),
        "sampling_frequency_hz": fs,
        "processing_mode": metadata.get("processing_mode"),
        "detector_window_s": _json_safe(
            metadata.get("detector_window_s", metadata.get("analysis_window_s"))
        ),
        "detector_extra_context_seconds": _finite_float(
            metadata.get("detector_extra_context_seconds", 0.0)
        ),
        "detail_chunk_minutes": _finite_float(
            metadata.get("detail_chunk_minutes", metadata.get("chunk_minutes"))
        ),
        "detail_chunk_context_seconds": _finite_float(
            metadata.get(
                "detail_chunk_context_seconds",
                metadata.get("chunk_context_seconds"),
            )
        ),
        "boundary_gamma_filter_context_seconds": _finite_float(
            metadata.get("filter_context_seconds")
        ),
        "sample_indexing": (
            "1-based samples in exported gamma_spike_events.csv; "
            "time columns are derived from those exported samples"
        ),
        "n_chunks": int(metadata.get("n_chunks", 0) or 0),
        "notch_filter": bool(metadata.get("notch_filter", False)),
        "notch_modes": _json_safe(metadata.get("notch_modes", [])),
        "gamma_notch_behavior": metadata.get("gamma_notch_behavior"),
        "notch_frequency_hz": _finite_float(metadata.get("notch_frequency_hz")),
        "notch_r": _finite_float(metadata.get("notch_r")),
        "total_spikes": int(metadata.get("total_spikes", 0) or 0),
        "boundary_success_count": int(
            metadata.get("boundary_success_count", 0) or 0
        ),
        "gamma_positive_spikes": int(
            sum(int(row.get("gamma_spikes", 0) or 0) for row in summary_rows)
        ),
        "gamma_success_count": int(metadata.get("gamma_success_count", 0) or 0),
        "n_channels": int(metadata.get("n_channels", len(summary_rows)) or 0),
        "n_samples": n_samples,
        "performance_assessment": _json_safe(
            metadata.get("performance_assessment")
        ),
        "output_files": [
            "gamma_channel_summary.csv",
            "gamma_spike_events.csv",
            "gamma_metadata.json",
            "README.txt",
        ],
    }


def _write_readme(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _rei_readme_text() -> str:
    return """REI Export

Files:
- rei_summary.csv
  One row per channel. Includes channel group, REI score, rank,
  recruitment delay relative to seizure onset, and peak HFER activity.

- rei_heatmap.csv
  Numeric HFER heatmap values per channel over time.

- rei_heatmap.png
  Figure version of the REI heatmap.

- rei_metadata.json
  Records the analyzed file, montage, seizure timing, baseline and ictal
  windows, filters, channel counts, and REI parameters.

Notes:
- REI computes from its analysis data, not from the visual display filter.
- Bad channels are excluded before REI computation.
- Time values are in seconds.
"""


def _gamma_readme_text() -> str:
    return """Gamma Spike Export

Files:
- gamma_channel_summary.csv
  One row per channel. Includes total spikes, gamma-positive spikes,
  non-gamma spikes, gamma rate, and mean gamma measurements.

- gamma_spike_events.csv
  One row per detected spike. Includes channel, event time, spike
  boundaries, gamma power, gamma frequency, gamma duration, and errors.
  Sample columns use 1-based indexing, and time columns are derived from those
  exported samples, to match the original Python2 output.

- gamma_metadata.json
  Records the analyzed file, analysis window, sampling frequency, segmented
  processing settings, notch filter setting, and spike counts.

Notes:
- Spikes are exported only inside the selected analysis window.
- The detector window is exactly the selected analysis window; extra context is
  used only for boundary and gamma detail measurements.
- Gamma measurements use the notch setting selected before running the algorithm.
- No gamma heatmap figures are exported.
- Time values are in seconds.
"""


def _hfo_readme_text(
    classifier_name: str = "",
    *,
    include_ehfo: bool = False,
) -> str:
    classifier_key = str(classifier_name or "").strip().lower()
    class_summary = ""
    if include_ehfo:
        class_summary = ", eHFO counts, and spike-eHFO counts"
    if classifier_key == "ehfo":
        route_notes = (
            "- The eHFO route requires at least 1000 Hz, resamples higher-rate "
            "recordings to 1000 Hz, and uses the validated 80-300 Hz detector band.\n"
            "- eHFO and spike-eHFO classes are produced only by this route."
        )
    elif classifier_key == "pyhfo_omni_legacy":
        route_notes = (
            "- The pyhfo_omni_legacy route requires at least 1000 Hz, resamples "
            "higher-rate recordings to 1000 Hz, and uses the validated 80-300 Hz detector band."
        )
    else:
        route_notes = (
            "- The pyhfo_pybrain route preserves native sampling and uses its "
            "validated 80-500 Hz detector and filter route."
        )
    return f"""HFO Export

Files:
- hfo_channel_summary.csv
  One row per channel with candidate counts, accepted HFO counts, artifact
  counts, spike-HFO counts{class_summary}, rates per minute, artifact percentage, and boundary
  event counts. Manually deleted events are excluded from active counts and
  reported separately.

- hfo_events.csv
  One row per detected event. Includes channel, candidate detector, event
  timing, boundary warning, pyHFO probabilities, final model class,
  manual review fields, selected band, and sampling-frequency details.

  Key event columns:
  - event_id: stable event identifier from the run.
  - channel: reviewed channel or bipolar derivation.
  - start_time_s, end_time_s, duration_ms: event timing.
  - detector: candidate detector that produced the event.
  - boundary_warning: true when the classifier waveform window touches or
    approaches the available signal boundary.
  - real_hfo_probability: pyHFO Model A accepted-HFO probability.
  - artifact_probability: complement of real_hfo_probability when available.
  - spike_hfo_probability: pyHFO Model S spike-association probability.
  - final_model_class: classifier proposition.
  - manual_class: official reviewer class after manual correction; empty until
    reviewed. "deleted" means manually excluded from active review/results.
  - official_class: derived active class used for display, summaries, and
    active exported counts. Equals manual_class when reviewed, otherwise the
    classifier proposition.
  - manual_review_status: unreviewed, reviewed, or deleted.

- hfo_metadata.json
  Records the analyzed file, manual analysis window, sampling frequencies,
  route-specific resampling behavior, GUI notch settings, candidate detectors,
  classifier status, model label counts, reviewed official label counts, input
  boundary, processing order, selected HFO algorithm route, classifier origin,
  checkpoint family, class mapping, and source GitHub repositories.

Notes:
{route_notes}
- HFO uses the notch setting selected in the main GUI.
- Candidates longer than max_duration_ms are excluded before waveform extraction
  and classification; excluded counts are recorded in metadata.
- Candidates inside boundary_padding_s from the selected analysis-window start or
  end are excluded before waveform extraction and classification. Use 0 s for
  exact legacy comparison runs.
- The pyHFO Model A positive score is exported as
  real_hfo_probability; artifact_probability is its complement.
- final_model_class is the immutable classifier proposition. manual_class is the
  reviewer correction. official_class is derived from those two fields and
  should not be used to overwrite the classifier proposition on import.
- Time values are in seconds.
"""


def _pyplot():
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _sample0_to_export_time(sample: Any, fs: float, data_start_s: float) -> float | None:
    sample_value = _finite_float(sample)
    if sample_value is None or fs <= 0.0:
        return None
    return float(data_start_s) + (sample_value + 1.0) / float(fs)


def _sample0_to_export_sample1(sample: Any) -> float | None:
    sample_value = _finite_float(sample)
    if sample_value is None:
        return None
    return float(sample_value) + 1.0


def _gamma_event_is_gamma(power: Any, duration_ms: Any) -> bool:
    power_value = _finite_float(power)
    duration_value = _finite_float(duration_ms)
    if power_value is None or duration_value is None:
        return False
    return bool(power_value > 0.0 or duration_value > 0.0)


def _normalize_gamma_class(label: Any) -> str:
    text = str(label or "").strip().lower().replace("_", "-")
    if text in {"gamma", "gamma spike", "gamma-spike"}:
        return "gamma"
    if text in {"non-gamma", "nongamma", "non gamma", "regular", "regular spike"}:
        return "non-gamma"
    if text in {"unclassified", "candidate", "unknown", "not-classified"}:
        return "unclassified"
    return "unclassified" if not text else str(label)


def _gamma_event_official_class(event: Any) -> str:
    manual_class = getattr(event, "manual_class", None)
    if manual_class:
        return _normalize_gamma_class(manual_class)
    return (
        "gamma"
        if _gamma_event_is_gamma(
            getattr(event, "gamma_power", None),
            getattr(event, "gamma_duration_ms", None),
        )
        else "non-gamma"
    )


def _gamma_event_official_is_gamma(event: Any) -> bool:
    return _gamma_event_official_class(event) == "gamma"
