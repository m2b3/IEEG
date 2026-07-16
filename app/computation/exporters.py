from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.computation.gamma_spike.wire_algorithm import GammaSpikeComputationResult
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
            if _gamma_event_is_gamma(event.gamma_power, event.gamma_duration_ms)
        ]
        total_spikes = int(channel_result.spike_count)
        gamma_spikes = len(gamma_events)
        powers = np.asarray(
            [float(event.gamma_power) for event in gamma_events],
            dtype=float,
        )
        durations = np.asarray(
            [float(event.gamma_duration_ms) for event in gamma_events],
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
                    "sample": _finite_float(event.sample),
                    "time_s": _finite_float(event.time_s),
                    "is_gamma": _gamma_event_is_gamma(
                        event.gamma_power,
                        event.gamma_duration_ms,
                    ),
                    "boundary_p1_sample": _finite_float(event.boundary_p1_sample),
                    "boundary_n1_sample": _finite_float(event.boundary_n1_sample),
                    "boundary_n2_sample": _finite_float(event.boundary_n2_sample),
                    "boundary_p1_time_s": _sample_to_time(
                        event.boundary_p1_sample,
                        fs,
                        data_start_s,
                    ),
                    "boundary_n1_time_s": _sample_to_time(
                        event.boundary_n1_sample,
                        fs,
                        data_start_s,
                    ),
                    "boundary_n2_time_s": _sample_to_time(
                        event.boundary_n2_sample,
                        fs,
                        data_start_s,
                    ),
                    "gamma_power": _finite_float(event.gamma_power),
                    "gamma_frequency_hz": _finite_float(event.gamma_frequency_hz),
                    "gamma_duration_ms": _finite_float(event.gamma_duration_ms),
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
            "boundary_p1_sample",
            "boundary_n1_sample",
            "boundary_n2_sample",
            "boundary_p1_time_s",
            "boundary_n1_time_s",
            "boundary_n2_time_s",
            "gamma_power",
            "gamma_frequency_hz",
            "gamma_duration_ms",
            "error",
        ],
        event_rows,
    )

    metadata_path = output_dir / "gamma_metadata.json"
    _write_json(metadata_path, _compact_gamma_metadata(metadata, summary_rows))

    readme_path = output_dir / "README.txt"
    _write_readme(readme_path, _gamma_readme_text())

    return [summary_path, events_path, metadata_path, readme_path]


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
        "filter_context_seconds": _finite_float(metadata.get("filter_context_seconds")),
        "notch_filter": bool(metadata.get("notch_filter", False)),
        "notch_modes": _json_safe(metadata.get("notch_modes", [])),
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

- gamma_metadata.json
  Records the analyzed file, analysis window, sampling frequency, notch
  filter setting, and spike counts.

Notes:
- Spikes are exported only inside the selected analysis window.
- Gamma measurements use the notch setting selected before running the algorithm.
- No gamma heatmap figures are exported.
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


def _sample_to_time(sample: Any, fs: float, data_start_s: float) -> float | None:
    sample_value = _finite_float(sample)
    if sample_value is None or fs <= 0.0:
        return None
    return float(data_start_s) + sample_value / float(fs)


def _gamma_event_is_gamma(power: Any, duration_ms: Any) -> bool:
    power_value = _finite_float(power)
    duration_value = _finite_float(duration_ms)
    if power_value is None or duration_value is None:
        return False
    return bool(power_value > 0.0 or duration_value > 0.0)
