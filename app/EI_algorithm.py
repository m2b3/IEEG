r"""
2-EI_algorithm.py

Batch EI computation using your existing segment CSV.

This script DOES NOT use the GUI and DOES NOT require iEEG.org login.
By default, EDF recordings are resampled to 1000 Hz before EI window
extraction to match the Omni-iEEG benchmarking workflow.
It reads:
    expert_annotations/baseline_ictal_segments_clean.csv

Required CSV columns:
    edf_full_path OR edf_relative_path
    baseline_start_sec
    baseline_end_sec
    ictal_start_sec
    ictal_end_sec

Optional CSV columns:
    usable_for_EI
    subject
    run
    qc_note

Install in your VS Code .venv:
    pip install numpy scipy pandas mne

Run on Windows:
    python scripts\2-EI_algorithm.py ^
        --data_root r"C:\Users\F15\Desktop\EI_Benchmarking\data_bids" ^
        --segments_csv "C:\Users\F15\Desktop\EI_Benchmarking\expert_annotations\baseline_ictal_segments_clean.csv" ^
        --output_dir "C:\Users\F15\Desktop\EI_Benchmarking\results\2-EI_Results"

Run on Mac/Linux:
    python scripts/2-EI_algorithm.py \
        --data_root ./data_bids \
        --segments_csv ./expert_annotations/baseline_ictal_segments_clean.csv \
        --output_dir ./results/2-EI_Results
"""

from __future__ import annotations

import argparse
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import cast
import numpy as np
from numpy.typing import NDArray
from scipy.signal import convolve2d
from scipy import signal

try:
    import pandas as pd
except ImportError:  # GUI EI computation does not require pandas.
    pd = None

__all__ = [
    "EIChannelResult",
    "EIComputationResult",
    "compute_ei_for_gui",
    "validate_gui_ei_timing",
]

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
    metadata: dict


def load_channel_qc_module():
    """Load the numbered channel QC helper despite the hyphenated filename."""
    channel_qc_path = Path(__file__).with_name("1-channel_quality_control.py")
    if not channel_qc_path.exists():
        raise FileNotFoundError(f"Channel QC helper not found: {channel_qc_path}")

    spec = importlib.util.spec_from_file_location("ieeg_ei_channel_qc", channel_qc_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import channel QC helper: {channel_qc_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_channel_qc = None


def _get_channel_qc_module():
    global _channel_qc
    if _channel_qc is None:
        _channel_qc = load_channel_qc_module()
    return _channel_qc


# -------------------------
# EI core from scripts code
# -------------------------

def compute_hfer(
    target_data: Array,
    base_data: Array,
    fs: float,
) -> tuple[Array, Array]:
    target_arr = np.asarray(target_data, dtype=np.float64)
    base_arr = np.asarray(base_data, dtype=np.float64)

    target_sq = cast(Array, target_arr ** 2)
    base_sq = cast(Array, base_arr ** 2)

    window = max(1, int(fs / 2.0))
    energy_kernel = np.ones((1, window), dtype=np.float64)
    target_energy = cast(Array, convolve2d(target_sq, energy_kernel, mode="same"))
    base_energy = cast(Array, convolve2d(base_sq, energy_kernel, mode="same"))

    base_energy_ref = cast(Array, np.sum(base_energy, axis=1) / float(base_energy.shape[1]))
    base_energy_ref = cast(
        Array,
        np.where(base_energy_ref <= 0, np.finfo(np.float64).eps, base_energy_ref),
    )

    target_de_matrix = cast(
        Array,
        base_energy_ref[:, np.newaxis]
        * np.ones((1, target_energy.shape[1]), dtype=np.float64),
    )
    base_de_matrix = cast(
        Array,
        base_energy_ref[:, np.newaxis]
        * np.ones((1, base_energy.shape[1]), dtype=np.float64),
    )

    norm_target_energy = cast(Array, target_energy / target_de_matrix)
    norm_base_energy = cast(Array, base_energy / base_de_matrix)

    return norm_target_energy, norm_base_energy


def determine_threshold_onset(target: Array, base: Array) -> Array:
    sigma = np.std(base, axis=1, ddof=1)
    channel_max_base = np.max(base, axis=1)
    thresh_value = channel_max_base + 10 * sigma

    onset_location = np.zeros(shape=(target.shape[0],))

    for channel_idx in range(target.shape[0]):
        logic_vec = target[channel_idx, :] > thresh_value[channel_idx]
        if np.sum(logic_vec) == 0:
            onset_location[channel_idx] = len(logic_vec)
        else:
            onset_location[channel_idx] = np.where(logic_vec != 0)[0][0]

    return cast(Array, onset_location)


def compute_ei_index(target: Array, base: Array, fs: float) -> tuple[Array, Array, Array]:
    target_hfer, base_hfer = compute_hfer(target, base, fs)

    channel_onset = determine_threshold_onset(target_hfer, base_hfer)
    target_len = int(target_hfer.shape[1])
    if target_len <= 0:
        raise ValueError("Ictal window is empty after preprocessing.")

    seizure_location = int(np.min(channel_onset))
    seizure_location = max(0, min(seizure_location, target_len - 1))

    hfer_window = max(1, int(round(0.25 * fs)))
    hfer_stop = min(target_len, seizure_location + hfer_window)
    actual_window = max(1, hfer_stop - seizure_location)
    hfer = np.sum(target_hfer[:, seizure_location:hfer_stop], axis=1) / float(actual_window)

    time_rank_tmp = np.argsort(channel_onset)
    onset_rank = np.argsort(time_rank_tmp) + 1
    onset_rank = 1.0 / onset_rank.astype(np.float32)

    ei = np.sqrt(hfer * onset_rank)

    ei[np.isnan(ei)] = 0
    ei[np.isinf(ei)] = 0

    if np.max(ei) > 0:
        ei = ei / np.max(ei)

    return cast(Array, ei), channel_onset, target_hfer


def bandpass_hf(data: Array, fs: float) -> Array:
    """
    High-frequency filtering from original code.
    Input/output: channels x time.
    """
    nyquist = fs / 2.0

    if nyquist <= 71:
        raise ValueError(f"Sampling rate too low for 70 Hz high-frequency bandpass: fs={fs}")

    high = min(140, nyquist - 1)
    ba_coefficients = cast(
        tuple[Array, Array],
        signal.butter(
            N=4,
            Wn=[70.0, float(high)],
            btype="bandpass",
            analog=False,
            output="ba",
            fs=float(fs),
        ),
    )
    b_raw, a_raw = ba_coefficients
    b = cast(Array, np.asarray(b_raw, dtype=np.float64))
    a = cast(Array, np.asarray(a_raw, dtype=np.float64))
    filtered = signal.filtfilt(b, a, np.asarray(data, dtype=np.float64), axis=1)
    return cast(Array, np.asarray(filtered, dtype=np.float64))


def compute_ei_from_windows(data: np.ndarray, fs: float,
                            baseline_samples: tuple[int, int],
                            ictal_samples: tuple[int, int]) -> tuple[Array, Array, Array]:
    """
    data shape must be channels x time.
    """
    data_filt = bandpass_hf(cast(Array, np.asarray(data, dtype=np.float64)), fs)

    base = data_filt[:, baseline_samples[0]:baseline_samples[1]]
    target = data_filt[:, ictal_samples[0]:ictal_samples[1]]

    return compute_ei_index(target, base, fs)


def validate_gui_ei_timing(
    *,
    seizure_onset_s: float,
    seizure_offset_s: float,
    baseline_window_s: tuple[float, float],
    ictal_window_s: tuple[float, float],
    recording_duration_s: float | None = None,
) -> None:
    seizure_onset_s = float(seizure_onset_s)
    seizure_offset_s = float(seizure_offset_s)
    baseline_start_s, baseline_end_s = map(float, baseline_window_s)
    ictal_start_s, ictal_end_s = map(float, ictal_window_s)

    if seizure_offset_s <= seizure_onset_s:
        raise ValueError("Seizure offset must be after seizure onset.")
    if seizure_offset_s - seizure_onset_s <= 20.0:
        raise ValueError("Seizure duration must be more than 20 seconds for REI.")
    if baseline_end_s <= baseline_start_s:
        raise ValueError("Baseline end must be after baseline start.")
    if ictal_end_s <= ictal_start_s:
        raise ValueError("Ictal end must be after ictal start.")
    if baseline_end_s > seizure_onset_s:
        raise ValueError("Baseline window must end at or before seizure onset.")
    if ictal_start_s > seizure_onset_s:
        raise ValueError("Ictal window must start at or before seizure onset.")
    if ictal_end_s > seizure_offset_s:
        raise ValueError("Ictal window must end at or before seizure offset.")
    if recording_duration_s is not None:
        if baseline_start_s < 0.0:
            raise ValueError("Baseline start is before the beginning of the recording.")
        if ictal_end_s > float(recording_duration_s):
            raise ValueError("Ictal end is beyond the end of the recording.")


def sec_to_sample_clamped(sec: float, fs: float, n_samples: int) -> int:
    sample = int(round(float(sec) * float(fs)))
    return max(0, min(sample, int(n_samples)))


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
    metadata: dict | None = None,
) -> EIComputationResult:
    """
    GUI-facing REI computation.

    Parameters
    ----------
    data
        Channels x time data from the current GUI montage. The REI normalization is
        scale-invariant for rankings, so volts/uV are both acceptable as long as
        baseline and ictal windows use the same units.
    fs
        Sampling frequency in Hz.
    channel_names
        Display channel names matching data rows.
    data_start_s
        Absolute recording time represented by data[:, 0].
    seizure_onset_s / seizure_offset_s
        Manual GUI seizure timing in seconds.
    baseline_window_s / ictal_window_s
        Absolute recording-time windows in seconds.
    """
    data = np.asarray(data, dtype=float)
    if data.ndim != 2:
        raise ValueError("REI data must be a 2D channels x time array.")
    if data.shape[0] != len(channel_names):
        raise ValueError("REI channel name count does not match data rows.")
    if data.shape[0] == 0:
        raise ValueError("No channels available for REI computation.")

    n_samples = int(data.shape[1])
    validate_gui_ei_timing(
        seizure_onset_s=float(seizure_onset_s),
        seizure_offset_s=float(seizure_offset_s),
        baseline_window_s=baseline_window_s,
        ictal_window_s=ictal_window_s,
        recording_duration_s=None,
    )

    baseline_start_s, baseline_end_s = map(float, baseline_window_s)
    ictal_start_s, ictal_end_s = map(float, ictal_window_s)
    data_start_s = float(data_start_s)

    b0 = sec_to_sample_clamped(baseline_start_s - data_start_s, fs, n_samples)
    b1 = sec_to_sample_clamped(baseline_end_s - data_start_s, fs, n_samples)
    i0 = sec_to_sample_clamped(ictal_start_s - data_start_s, fs, n_samples)
    i1 = sec_to_sample_clamped(ictal_end_s - data_start_s, fs, n_samples)

    if not (0 <= b0 < b1 <= n_samples):
        raise ValueError(f"Invalid baseline sample window: {(b0, b1)}")
    if not (0 <= i0 < i1 <= n_samples):
        raise ValueError(f"Invalid ictal sample window: {(i0, i1)}")

    bad_channels = {str(ch) for ch in (bad_channels or set())}
    keep = [idx for idx, name in enumerate(channel_names) if str(name) not in bad_channels]
    if not keep:
        raise ValueError("All selected channels are marked bad; no REI channels remain.")

    kept_names = [str(channel_names[idx]) for idx in keep]
    kept_data = data[np.asarray(keep, dtype=int), :]

    ei, channel_onset_samples, target_hfer = compute_ei_from_windows(
        data=kept_data,
        fs=float(fs),
        baseline_samples=(int(b0), int(b1)),
        ictal_samples=(int(i0), int(i1)),
    )

    ranks = np.asarray(np.asarray(ei).argsort()[::-1].argsort() + 1, dtype=int)
    groups = channel_groups or {}
    onset_time_shift_s = float(ictal_start_s) - float(seizure_onset_s)
    rows = [
        EIChannelResult(
            channel=name,
            group=str(groups.get(name, "macro")),
            ei=float(ei[idx]),
            rank=int(ranks[idx]),
            onset_sample_in_ictal_window=int(channel_onset_samples[idx]),
            onset_sec_from_seizure_onset=(
                float(channel_onset_samples[idx]) / float(fs) + onset_time_shift_s
            ),
        )
        for idx, name in enumerate(kept_names)
    ]
    rows.sort(key=lambda row: row.ei, reverse=True)

    heatmap_times = np.arange(target_hfer.shape[1], dtype=float) / float(fs)
    result_metadata = dict(metadata or {})
    result_metadata.update({
        "bad_channels_excluded": True,
        "excluded_bad_channels": sorted(bad_channels.intersection(set(channel_names))),
        "n_channels_input": int(len(channel_names)),
        "n_channels_computed": int(len(kept_names)),
        "fs": float(fs),
        "baseline_samples": [int(b0), int(b1)],
        "ictal_samples": [int(i0), int(i1)],
    })
    if "seizure_onset_s" not in result_metadata:
        result_metadata["seizure_onset_s"] = float(seizure_onset_s)
    if "seizure_offset_s" not in result_metadata:
        result_metadata["seizure_offset_s"] = float(seizure_offset_s)
    if "baseline_window_s" not in result_metadata:
        result_metadata["baseline_window_s"] = [float(baseline_start_s), float(baseline_end_s)]
    if "ictal_window_s" not in result_metadata:
        result_metadata["ictal_window_s"] = [float(ictal_start_s), float(ictal_end_s)]
    result_metadata["data_start_s"] = data_start_s
    result_metadata.setdefault("threshold_sigma", 10.0)
    result_metadata.setdefault("energy_window_sec", 0.5)
    result_metadata.setdefault("hfer_window_sec", 0.25)

    return EIComputationResult(
        channels=rows,
        heatmap=np.asarray(target_hfer, dtype=float),
        heatmap_times=heatmap_times,
        heatmap_channels=kept_names,
        metadata=result_metadata,
    )


# -------------------------
# File loading
# -------------------------

def load_edf(file_path: Path, target_fs: float | None = 1000.0):
    import mne
    raw = mne.io.read_raw_edf(file_path, preload=True, verbose=False)
    native_fs = float(raw.info["sfreq"])

    if target_fs is not None and target_fs > 0 and not np.isclose(native_fs, target_fs):
        raw.resample(float(target_fs), npad="auto", verbose=False)

    data = raw.get_data()  # channels x time, in volts
    fs = float(raw.info["sfreq"])
    ch_names = list(raw.ch_names)
    return data, fs, ch_names, native_fs


def sec_to_sample(sec: float, fs: float, n_samples: int):
    sample = int(round(float(sec) * fs))
    return max(0, min(sample, n_samples))


def safe_bool(value):
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def resolve_edf_path(row, data_root: Path):
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    """
    Prefer edf_full_path if present and exists.
    Otherwise fall back to data_root / edf_relative_path if available.
    """
    edf_full = row.get("edf_full_path", "")
    if not pd.isna(edf_full) and str(edf_full).strip():
        edf_full_path = Path(str(edf_full))
        if edf_full_path.exists():
            return edf_full_path

    edf_rel = row.get("edf_relative_path", "")
    if pd.isna(edf_rel) or not str(edf_rel).strip():
        raise ValueError("No edf_full_path or edf_relative_path found for this row")

    return data_root / Path(str(edf_rel))


def get_row_subject(row) -> str:
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    if "subject" in row.index and pd.notna(row["subject"]):
        return str(row["subject"])

    for key in ("edf_relative_path", "edf_full_path"):
        if key in row.index and pd.notna(row[key]):
            subject = next(
                (part for part in Path(str(row[key])).parts if str(part).startswith("sub-")),
                "",
            )
            if subject:
                return subject

    return ""


def load_ei_patient_exclusions(path: Path) -> dict[str, str]:
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    if not path.exists():
        return {}

    exclusions = pd.read_csv(path)
    required = {"patient_id", "reason"}
    missing = required - set(exclusions.columns)
    if missing:
        raise ValueError(f"EI exclusion file missing columns: {sorted(missing)}")

    exclusions = exclusions[["patient_id", "reason"]].dropna(subset=["patient_id"]).copy()
    exclusions["patient_id"] = exclusions["patient_id"].astype(str)
    exclusions["reason"] = exclusions["reason"].fillna("").astype(str)
    return dict(zip(exclusions["patient_id"], exclusions["reason"]))


def row_is_explicitly_valid(row) -> tuple[bool, str]:
    if "usable_for_EI" in row.index:
        if safe_bool(row["usable_for_EI"]):
            return True, ""
        return False, f"usable_for_EI is false | {row.get('qc_note', '')}"

    segment_status = str(row.get("segment_status", "")).strip().lower()
    if segment_status:
        if segment_status == "ok":
            return True, ""
        return False, f"segment_status is {segment_status} | {row.get('qc_note', '')}"

    return False, "row is not explicitly valid for EI"


def row_has_consistent_annotations(row) -> tuple[bool, str]:
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    duration = pd.to_numeric(row.get("duration_sec", np.nan), errors="coerce")
    onset = pd.to_numeric(row.get("seizure_onset_relative_sec", np.nan), errors="coerce")
    offset = pd.to_numeric(row.get("seizure_offset_relative_sec", np.nan), errors="coerce")
    seizure_duration = pd.to_numeric(row.get("seizure_duration_sec", np.nan), errors="coerce")

    problems = []
    if pd.notna(duration):
        if pd.notna(onset) and onset > duration:
            problems.append("onset exceeds EDF duration")
        if pd.notna(offset) and offset > duration:
            problems.append("offset exceeds EDF duration")
        if pd.notna(seizure_duration) and seizure_duration > duration:
            problems.append("seizure duration exceeds EDF duration")

    if pd.notna(onset) and pd.notna(offset) and offset <= onset:
        problems.append("offset before or equal onset")

    if problems:
        return False, "; ".join(problems)

    return True, ""


def build_channel_keep_mask(
    ch_names: list[str],
    edf_path: Path,
    exclude_regex: str | None = None,
):
    return _get_channel_qc_module().build_channel_keep_mask(
        ch_names=ch_names,
        edf_path=edf_path,
        exclude_regex=exclude_regex,
        exclude_channels_missing_from_metadata=True,
    )


def compute_row(
    row,
    row_index: int,
    data_root: Path,
    output_dir: Path,
    exclude_regex: str | None = None,
    target_fs: float | None = 1000.0,
    excluded_patients: dict[str, str] | None = None,
):
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    subject = get_row_subject(row)
    excluded_patients = excluded_patients or {}
    if subject in excluded_patients:
        return {
            "row": row_index,
            "status": "skipped",
            "reason": f"patient excluded from EI computation | {excluded_patients[subject]}",
            "output": "",
            "edf": str(row.get("edf_full_path", row.get("edf_relative_path", ""))),
            "subject": subject,
        }

    is_valid, invalid_reason = row_is_explicitly_valid(row)
    if not is_valid:
        return {
            "row": row_index,
            "status": "skipped",
            "reason": invalid_reason,
            "output": "",
        }

    has_consistent_annotations, annotation_reason = row_has_consistent_annotations(row)
    if not has_consistent_annotations:
        return {
            "row": row_index,
            "status": "skipped",
            "reason": annotation_reason,
            "output": "",
        }

    edf_path = resolve_edf_path(row, data_root)

    if not edf_path.exists():
        return {
            "row": row_index,
            "status": "error",
            "reason": f"Missing EDF: {edf_path}",
            "output": "",
        }

    data, fs, ch_names, native_fs = load_edf(edf_path, target_fs=target_fs)
    keep_mask, exclusion_reasons, channel_tsv = build_channel_keep_mask(
        ch_names=ch_names,
        edf_path=edf_path,
        exclude_regex=exclude_regex,
    )

    excluded_channel_count = int((~keep_mask).sum())
    if excluded_channel_count:
        data = data[keep_mask, :]
        ch_names = [ch_name for ch_name, keep in zip(ch_names, keep_mask) if keep]

    n_samples = data.shape[1]

    baseline_samples = (
        sec_to_sample(row["baseline_start_sec"], fs, n_samples),
        sec_to_sample(row["baseline_end_sec"], fs, n_samples),
    )
    ictal_samples = (
        sec_to_sample(row["ictal_start_sec"], fs, n_samples),
        sec_to_sample(row["ictal_end_sec"], fs, n_samples),
    )

    if baseline_samples[1] <= baseline_samples[0]:
        raise ValueError(f"Invalid baseline window: {baseline_samples}")
    if ictal_samples[1] <= ictal_samples[0]:
        raise ValueError(f"Invalid ictal window: {ictal_samples}")

    ei, channel_onset_samples, _ = compute_ei_from_windows(
        data=data,
        fs=fs,
        baseline_samples=baseline_samples,
        ictal_samples=ictal_samples,
    )

    base_name = edf_path.stem
    subject = str(row.get("subject", "unknown"))
    run = str(row.get("run", row_index + 1))

    seizure_output_dir = output_dir / base_name
    seizure_output_dir.mkdir(parents=True, exist_ok=True)

    out_csv = seizure_output_dir / f"{base_name}_EI.csv"

    out = pd.DataFrame({
        "channel": ch_names,
        "EI": ei,
        "EI_rank_desc": pd.Series(ei).rank(ascending=False, method="min").astype(int),
        "channel_onset_sample_in_ictal_window": channel_onset_samples.astype(int),
        "channel_onset_sec_in_ictal_window": channel_onset_samples / fs,
        "subject": subject,
        "run": run,
        "edf_file": str(edf_path),
        "native_fs": native_fs,
        "fs": fs,
        "resampled_to_fs": fs if not np.isclose(native_fs, fs) else "",
        "baseline_start_sec": row["baseline_start_sec"],
        "baseline_end_sec": row["baseline_end_sec"],
        "ictal_start_sec": row["ictal_start_sec"],
        "ictal_end_sec": row["ictal_end_sec"],
        "excluded_channel_count": excluded_channel_count,
    }).sort_values("EI", ascending=False)

    out.to_csv(out_csv, index=False)

    return {
        "row": row_index,
        "status": "ok",
        "reason": "",
        "output": str(out_csv),
        "edf": str(edf_path),
        "n_channels": len(ch_names),
        "excluded_channel_count": excluded_channel_count,
        "channel_metadata_file": "" if channel_tsv is None else str(channel_tsv),
        "channel_exclusion_reason": " | ".join(exclusion_reasons),
        "native_fs": native_fs,
        "fs": fs,
        "resampled_to_fs": fs if not np.isclose(native_fs, fs) else "",
    }


def main():
    if pd is None:
        raise ImportError("pandas is required for batch EI CSV processing.")
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    default_data_root = project_root / "data_bids"
    default_segments_csv = project_root / "expert_annotations" / "baseline_ictal_segments_clean.csv"
    default_output_dir = project_root / "results" / "2-EI_Results"

    parser = argparse.ArgumentParser()
    parser.add_argument("--data_root", default=str(default_data_root))
    parser.add_argument("--segments_csv", default=str(default_segments_csv))
    parser.add_argument("--output_dir", default=str(default_output_dir))
    parser.add_argument(
        "--exclude_channel_regex",
        default=None,
        help="Optional regex applied to channel names after metadata-based exclusion.",
    )
    parser.add_argument(
        "--target_fs",
        type=float,
        default=1000.0,
        help=(
            "Target sampling frequency in Hz for EI computation. "
            "Use 0 or a negative value to disable resampling."
        ),
    )
    parser.add_argument(
        "--ei_patient_exclusions",
        default=str(script_path.parent / "post_ei_patient_exclusions.csv"),
        help="CSV with patient_id and reason columns for patients excluded from EI computation.",
    )
    parser.add_argument(
        "--start_row",
        type=int,
        default=0,
        help="First segment CSV row to process. Useful for long chunked reruns.",
    )
    parser.add_argument(
        "--end_row",
        type=int,
        default=None,
        help="Last segment CSV row to process, inclusive. Defaults to the final row.",
    )
    args = parser.parse_args()

    data_root = Path(args.data_root)
    segments_csv = Path(args.segments_csv)
    output_dir = Path(args.output_dir)
    ei_patient_exclusions = Path(args.ei_patient_exclusions)

    if not data_root.exists():
        raise FileNotFoundError(f"data_root not found: {data_root}")
    if not segments_csv.exists():
        raise FileNotFoundError(f"segments_csv not found: {segments_csv}")

    output_dir.mkdir(parents=True, exist_ok=True)
    target_fs = args.target_fs if args.target_fs and args.target_fs > 0 else None
    excluded_patients = load_ei_patient_exclusions(ei_patient_exclusions)
    if excluded_patients:
        print(
            "EI patient exclusions loaded: "
            + ", ".join(f"{patient_id}" for patient_id in sorted(excluded_patients))
        )

    T = pd.read_csv(segments_csv)

    required_cols = {
        "baseline_start_sec",
        "baseline_end_sec",
        "ictal_start_sec",
        "ictal_end_sec",
    }
    missing = required_cols - set(T.columns)
    if missing:
        raise ValueError(f"Missing required CSV columns: {missing}")

    log_rows = []
    processed_ok = 0
    skipped_rows = 0
    error_rows = 0
    excluded_channels_total = 0

    start_row = max(int(args.start_row), 0)
    end_row = len(T) - 1 if args.end_row is None else min(int(args.end_row), len(T) - 1)
    if start_row > end_row:
        raise ValueError(f"Invalid row range: start_row={start_row}, end_row={end_row}")

    log_path = output_dir / "EI_batch_log.csv"
    if start_row != 0 or end_row != len(T) - 1:
        log_path = output_dir / f"EI_batch_log_rows_{start_row:03d}_{end_row:03d}.csv"

    for i, row in T.iloc[start_row : end_row + 1].iterrows():
        try:
            result = compute_row(
                row,
                i,
                data_root,
                output_dir,
                exclude_regex=args.exclude_channel_regex,
                target_fs=target_fs,
                excluded_patients=excluded_patients,
            )
            log_rows.append(result)

            if result["status"] == "ok":
                processed_ok += 1
                excluded_channels_total += int(result.get("excluded_channel_count", 0))
                print(f"[OK] row {i} -> {result['output']}")
            elif result["status"] == "skipped":
                skipped_rows += 1
                print(f"[SKIP] row {i} | {result['reason']}")
            else:
                error_rows += 1
                print(f"[ERROR] row {i} | {result['reason']}")

        except Exception as e:
            msg = str(e)
            print(f"[FAILED] row {i} | {msg}")
            error_rows += 1
            log_rows.append({
                "row": i,
                "status": "error",
                "reason": msg,
                "output": "",
            })

    log = pd.DataFrame(log_rows)
    log.to_csv(log_path, index=False)

    print("\nDone.")
    print("Summary:")
    print(f"  total rows in segment CSV: {len(T)}")
    print(f"  row range processed: {start_row}-{end_row}")
    print(f"  processed: {processed_ok}")
    print(f"  skipped: {skipped_rows}")
    print(f"  errors: {error_rows}")
    print(f"  excluded channels total: {excluded_channels_total}")
    print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    main()
