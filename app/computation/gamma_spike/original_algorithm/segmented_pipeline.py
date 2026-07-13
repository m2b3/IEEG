from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, cast, overload

import mne
import numpy as np
from scipy import signal

from .compute_gamma import compute_gamma
from .compute_spike_boundary import compute_spike_boundary
from .postprocessing import postprocessing
from .spike_detector_hilbert_v25 import DetectorOutput, spike_detector_hilbert_v25


DEFAULT_SETTINGS = "-bl 10 -bh 60 -h 60 -k1 3.65 -dec 200"


CsvRow = list[Any]
Rows = list[CsvRow]
SummaryRow = CsvRow


@dataclass(frozen=True)
class SegmentedPipelineResult:
    step1: Rows
    step2: Rows
    qc: Rows
    step3: Rows
    step4: Rows
    summary: SummaryRow


@dataclass(frozen=True)
class EventRecord:
    time_sec: float
    sample: int
    channel: int
    channel_name: str
    condition: float
    weight: float
    pdf: float
    dur: float


def _row_float(row: Sequence[Any], index: int) -> float:
    return float(row[index])


def _row_int(row: Sequence[Any], index: int) -> int:
    return int(row[index])


def _row_str(row: Sequence[Any], index: int) -> str:
    return str(row[index])


def _butter_ba(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
    b, a = cast(Any, signal.butter(*args, **kwargs))
    return np.asarray(b, dtype=float), np.asarray(a, dtype=float)


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    central_start_sample0: int
    central_stop_sample0: int
    extended_start_sample0: int
    extended_stop_sample0: int


def run_segmented_recording(
    edf_path: Path,
    *,
    settings: str = DEFAULT_SETTINGS,
    chunk_minutes: float = 10.0,
    context_seconds: float = 10.0,
    filter_context_seconds: float = 5.0,
    max_spikes_for_boundary_gamma: int | None = 25,
) -> SegmentedPipelineResult:
    """Run the Python2 spike-gamma pipeline using chunked EDF reads."""

    signal_path = resolve_signal_path(edf_path)
    raw = read_raw_signal(signal_path, preload=False)
    fs = float(raw.info["sfreq"])
    n_samples = int(raw.n_times)
    channel_names = list(raw.ch_names)
    n_channels = len(channel_names)

    chunk_samples = int(round(chunk_minutes * 60.0 * fs))
    context_samples = int(round(context_seconds * fs))
    if chunk_samples <= 0:
        raise ValueError("chunk_minutes must produce at least one sample")

    detector_rows: Rows = []
    event_records: list[EventRecord] = []

    for spec in make_chunk_specs(n_samples, chunk_samples, context_samples):
        data = read_data(raw, spec.extended_start_sample0, spec.extended_stop_sample0)
        out, *_ = spike_detector_hilbert_v25(data, fs, settings)
        chunk_rows, chunk_records = keep_central_events(spec, out, fs, channel_names)
        detector_rows.extend(chunk_rows)
        event_records.extend(chunk_records)

    event_records = sort_and_renumber_events(event_records)
    detector_rows = event_rows_from_records(event_records, channel_names)
    detector_out = detector_output_from_records(event_records)

    out_pp, qc = postprocessing(detector_out, fs, n_channels, return_qc=True)
    step2_rows = make_step2_rows(out_pp, channel_names)
    qc_rows = make_qc_rows(qc)

    boundary_rows = compute_boundaries(
        raw,
        fs,
        channel_names,
        out_pp,
        filter_context_seconds=filter_context_seconds,
        max_rows=max_spikes_for_boundary_gamma,
    )
    gamma_rows = compute_gamma_rows(
        raw,
        fs,
        channel_names,
        boundary_rows,
        filter_context_seconds=filter_context_seconds,
    )

    summary: SummaryRow = [
        str(edf_path),
        str(signal_path),
        fs,
        n_samples,
        n_channels,
        len(detector_rows),
        qc["output_detections"],
        len(boundary_rows),
        len(gamma_rows),
        max_spikes_for_boundary_gamma if max_spikes_for_boundary_gamma is not None else "",
        chunk_minutes,
        context_seconds,
        filter_context_seconds,
        "ok",
        "",
    ]

    return SegmentedPipelineResult(
        step1=detector_rows,
        step2=step2_rows,
        qc=qc_rows,
        step3=boundary_rows,
        step4=gamma_rows,
        summary=summary,
    )


def make_chunk_specs(n_samples: int, chunk_samples: int, context_samples: int) -> Iterable[ChunkSpec]:
    chunk_index = 1
    central_start = 0
    while central_start < n_samples:
        central_stop = min(n_samples, central_start + chunk_samples)
        extended_start = max(0, central_start - context_samples)
        extended_stop = min(n_samples, central_stop + context_samples)
        yield ChunkSpec(chunk_index, central_start, central_stop, extended_start, extended_stop)
        central_start = central_stop
        chunk_index += 1


def read_data(raw, start_sample0: int, stop_sample0: int) -> np.ndarray:
    return raw.get_data(start=start_sample0, stop=stop_sample0).T * 1e6


def resolve_signal_path(path: Path) -> Path:
    if path.suffix.lower() != ".ieeg":
        return path

    metadata = json.loads(path.read_text(encoding="utf-8"))
    raw_file = metadata.get("source", {}).get("raw_file")
    if raw_file:
        return Path(raw_file)

    raw_file_relative = metadata.get("source", {}).get("raw_file_relative")
    if raw_file_relative:
        return path.parent / raw_file_relative

    raise ValueError(f"Could not find source.raw_file in {path}")


def read_raw_signal(path: Path, *, preload: bool):
    suffix = path.suffix.lower()
    if suffix == ".edf":
        return mne.io.read_raw_edf(path, preload=preload, verbose="ERROR")
    if suffix == ".fif":
        return mne.io.read_raw_fif(path, preload=preload, verbose="ERROR")
    return mne.io.read_raw(path, preload=preload, verbose="ERROR")


def keep_central_events(
    spec: ChunkSpec,
    out: DetectorOutput,
    fs: float,
    channel_names: list[str],
) -> tuple[Rows, list[EventRecord]]:
    records: list[EventRecord] = []
    for pos, dur, channel, condition, weight, pdf in zip(out.pos, out.dur, out.chan, out.con, out.weight, out.pdf):
        global_pos = float(pos + spec.extended_start_sample0 / fs)
        global_sample = int(matlab_round(global_pos * fs))
        if global_sample < spec.central_start_sample0 + 1 or global_sample > spec.central_stop_sample0:
            continue
        ch = int(channel)
        records.append(
            EventRecord(
                time_sec=global_pos,
                sample=global_sample,
                channel=ch,
                channel_name=channel_names[ch - 1] if 1 <= ch <= len(channel_names) else "",
                condition=float(condition),
                weight=float(weight),
                pdf=float(pdf),
                dur=float(dur),
            )
        )
    return event_rows_from_records(records, channel_names), records


def sort_and_renumber_events(records: list[EventRecord]) -> list[EventRecord]:
    return sorted(records, key=lambda row: (row.time_sec, row.channel))


def event_rows_from_records(records: list[EventRecord], channel_names: list[str]) -> Rows:
    rows = []
    for detection_index, record in enumerate(records, start=1):
        channel = record.channel
        channel_name = record.channel_name or channel_names[channel - 1]
        rows.append(
            [
                detection_index,
                record.time_sec,
                record.sample,
                channel,
                channel_name,
                record.condition,
                record.weight,
                record.pdf,
            ]
        )
    return rows


def detector_output_from_records(records: list[EventRecord]) -> DetectorOutput:
    return DetectorOutput(
        pos=np.asarray([record.time_sec for record in records], dtype=float),
        dur=np.asarray([record.dur for record in records], dtype=float),
        chan=np.asarray([record.channel for record in records], dtype=int),
        con=np.asarray([record.condition for record in records], dtype=float),
        weight=np.asarray([record.weight for record in records], dtype=float),
        pdf=np.asarray([record.pdf for record in records], dtype=float),
    )


def make_step2_rows(out_pp: list[np.ndarray], channel_names: list[str]) -> Rows:
    rows = []
    for channel_index, detections in enumerate(out_pp, start=1):
        rows.append(
            [
                channel_index,
                channel_names[channel_index - 1],
                int(len(detections)),
                " ".join(str(int(x)) for x in detections),
            ]
        )
    return rows


def make_qc_rows(qc: dict[str, object]) -> Rows:
    return [
        ["input_detections", qc["input_detections"]],
        ["common_mode_removals", qc["common_mode_removals"]],
        ["burst_removals", qc["burst_removals"]],
        ["non_common_mode_detections", qc["non_common_mode_detections"]],
        ["output_detections", qc["output_detections"]],
        ["coincidence_tolerance_sec", qc["coincidence_tolerance_sec"]],
        ["burst_tolerance_sec", qc["burst_tolerance_sec"]],
    ]


def compute_boundaries(
    raw,
    fs: float,
    channel_names: list[str],
    out_pp: list[np.ndarray],
    *,
    filter_context_seconds: float,
    max_rows: int | None,
) -> Rows:
    b_bp, a_bp = _butter_ba(4, [10.0, 60.0], btype="bandpass", fs=fs)
    rows: Rows = []
    row_id = 0
    n_samples = int(raw.n_times)

    for channel_index, detections in enumerate(out_pp, start=1):
        if max_rows is not None and len(rows) >= max_rows:
            break
        for spike_index, spike_location in enumerate(detections, start=1):
            if max_rows is not None and len(rows) >= max_rows:
                break
            row_id += 1
            spike_onset = float(spike_location - 75e-3 * fs)
            spike_offset = float(spike_location + 225e-3 * fs)
            status = "ok"
            error = ""
            p1 = n1 = n2 = np.nan
            try:
                start_index = int(matlab_round(spike_onset))
                stop_index = int(matlab_round(spike_offset))
                if start_index < 1 or stop_index > n_samples:
                    status = "skipped_edge"
                else:
                    signal_bp, extended_start_sample1 = read_filtered_channel_window(
                        raw,
                        channel_index,
                        start_index,
                        stop_index,
                        fs,
                        b_bp,
                        a_bp,
                        filter_context_seconds,
                    )
                    local_start0 = start_index - extended_start_sample1
                    local_stop0 = stop_index - extended_start_sample1 + 1
                    segment = signal_bp[local_start0:local_stop0]
                    p1, n1, n2 = compute_spike_boundary(segment, fs)
            except Exception as exc:
                status = "error"
                error = repr(exc)
            rows.append(
                [
                    row_id,
                    channel_index,
                    channel_names[channel_index - 1],
                    spike_index,
                    float(spike_location),
                    spike_onset,
                    spike_offset,
                    p1,
                    n1,
                    n2,
                    status,
                    error,
                ]
            )
    return rows


def compute_gamma_rows(
    raw,
    fs: float,
    channel_names: list[str],
    boundary_rows: Rows,
    *,
    filter_context_seconds: float,
) -> Rows:
    b_notch, a_notch = notch_filter_coefficients(fs)
    b_gamma, a_gamma = _butter_ba(4, [30.0, 100.0], btype="bandpass", fs=fs)
    n_samples = int(raw.n_times)
    rows = []

    for row in boundary_rows:
        row_id = _row_int(row, 0)
        channel = _row_int(row, 1)
        spike_index = _row_int(row, 3)
        sample = _row_float(row, 4)
        status = _row_str(row, 10)
        error = ""
        output = np.array([0.0, 0.0, 0.0])

        if status == "ok":
            try:
                spike_onset = _row_float(row, 5)
                n1_abs = spike_onset + _row_float(row, 8)
                p1_abs = spike_onset + _row_float(row, 7)
                n2_abs = spike_onset + _row_float(row, 9)
                ref = n1_abs - fs
                p1_gamma = p1_abs - ref
                n2_gamma = n2_abs - ref
                window = matlab_round(matlab_colon(n1_abs - fs, n1_abs + fs)).astype(int)
                if window[0] < 1 or window[-1] > n_samples:
                    status = "skipped_edge"
                else:
                    gamma_filtered, extended_start_sample1 = read_gamma_filtered_channel_window(
                        raw,
                        channel,
                        int(window[0]),
                        int(window[-1]),
                        fs,
                        b_notch,
                        a_notch,
                        b_gamma,
                        a_gamma,
                        filter_context_seconds,
                    )
                    local_start0 = int(window[0]) - extended_start_sample1
                    local_stop0 = int(window[-1]) - extended_start_sample1 + 1
                    segment = gamma_filtered[local_start0:local_stop0]
                    output = compute_gamma(segment, fs, p1_gamma, n2_gamma)
            except Exception as exc:
                status = "error"
                error = repr(exc)

        rows.append(
            [
                row_id,
                channel,
                channel_names[channel - 1],
                spike_index,
                sample,
                float(output[0]),
                float(output[1]),
                float(output[2]),
                int(np.any(output != 0)),
                status,
                error,
            ]
        )
    return rows


def read_filtered_channel_window(
    raw,
    channel_index: int,
    needed_start_sample1: int,
    needed_stop_sample1: int,
    fs: float,
    b: np.ndarray,
    a: np.ndarray,
    filter_context_seconds: float,
) -> tuple[np.ndarray, int]:
    context = int(round(filter_context_seconds * fs))
    extended_start_sample1 = max(1, needed_start_sample1 - context)
    extended_stop_sample1 = min(int(raw.n_times), needed_stop_sample1 + context)
    values = read_channel(raw, channel_index, extended_start_sample1, extended_stop_sample1)
    return signal.filtfilt(b, a, values), extended_start_sample1


def read_gamma_filtered_channel_window(
    raw,
    channel_index: int,
    needed_start_sample1: int,
    needed_stop_sample1: int,
    fs: float,
    b_notch: np.ndarray,
    a_notch: np.ndarray,
    b_gamma: np.ndarray,
    a_gamma: np.ndarray,
    filter_context_seconds: float,
) -> tuple[np.ndarray, int]:
    context = int(round(filter_context_seconds * fs))
    extended_start_sample1 = max(1, needed_start_sample1 - context)
    extended_stop_sample1 = min(int(raw.n_times), needed_stop_sample1 + context)
    values = read_channel(raw, channel_index, extended_start_sample1, extended_stop_sample1)
    notched = signal.filtfilt(b_notch, a_notch, values)
    return signal.filtfilt(b_gamma, a_gamma, notched), extended_start_sample1


def read_channel(raw, channel_index: int, start_sample1: int, stop_sample1: int) -> np.ndarray:
    data = raw.get_data(
        picks=[channel_index - 1],
        start=start_sample1 - 1,
        stop=stop_sample1,
    )[0]
    return data * 1e6


def notch_filter_coefficients(fs: float) -> tuple[np.ndarray, np.ndarray]:
    freq = 60.0
    r = 0.985
    b = np.array([1.0, -2.0 * np.cos(2.0 * np.pi * freq / fs), 1.0])
    a = np.array([1.0, -2.0 * r * np.cos(2.0 * np.pi * freq / fs), r * r])
    return b, a


@overload
def matlab_round(value: float) -> float:
    ...


@overload
def matlab_round(value: np.ndarray) -> np.ndarray:
    ...


def matlab_round(value: np.ndarray | float) -> np.ndarray | float:
    arr = np.asarray(value, dtype=float)
    rounded = np.sign(arr) * np.floor(np.abs(arr) + 0.5)
    if np.isscalar(value):
        return float(rounded)
    return rounded


def matlab_colon(start: float, stop: float) -> np.ndarray:
    if start > stop:
        return np.array([], dtype=float)
    count = int(np.floor(stop - start)) + 1
    return start + np.arange(count, dtype=float)


def write_rows(path: Path, header: list[str], rows: Rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def step1_header() -> list[str]:
    return ["detection_index", "time_sec", "sample", "channel", "channel_name", "condition", "weight", "pdf"]


def step2_header() -> list[str]:
    return ["channel", "channel_name", "retained_count", "retained_samples"]


def qc_header() -> list[str]:
    return ["metric", "value"]


def boundary_header() -> list[str]:
    return ["row_id", "channel", "channel_name", "spike_index", "sample", "spike_onset", "spike_offset", "p1", "n1", "n2", "status", "error"]


def gamma_header() -> list[str]:
    return ["row_id", "channel", "channel_name", "spike_index", "sample", "gamma_power", "gamma_frequency", "gamma_duration_ms", "gamma_detected", "status", "error"]


def summary_header() -> list[str]:
    return [
        "edf_path",
        "signal_path",
        "fs",
        "n_samples",
        "n_channels",
        "raw_detection_count",
        "postprocessed_detection_count",
        "boundary_rows",
        "gamma_rows",
        "max_spikes_for_boundary_gamma",
        "chunk_minutes",
        "context_seconds",
        "filter_context_seconds",
        "status",
        "error",
    ]


__all__ = [
    "Rows",
    "EventRecord",
    "SegmentedPipelineResult",
    "SummaryRow",
    "run_segmented_recording",
    "write_rows",
    "step1_header",
    "step2_header",
    "qc_header",
    "boundary_header",
    "gamma_header",
    "summary_header",
]
