from __future__ import annotations

import argparse
import contextlib
import io
import sys
import warnings
from pathlib import Path

import mne
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app.computation.hfo.algorithm import compute_hfo_for_gui
from validate_pyhfo_pool_classifier import build_pyhfo_candidate_pool, classify_pool_with_pyhfo


DEFAULT_EDF = Path(
    r"D:\omni dataset complement\updated_dataset\bids"
    r"\sub-zurich15\ses-01\ieeg"
    r"\sub-zurich15_ses-01_run-02_ieeg.edf"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare original pyHFO full pipeline against our full HFO pipeline.")
    parser.add_argument("--edf", type=Path, default=DEFAULT_EDF)
    parser.add_argument("--detector", choices=["mni", "ste", "hilbert"], default="mni")
    parser.add_argument("--low-freq-hz", type=float, default=80.0)
    parser.add_argument("--high-freq-hz", type=float, default=500.0)
    parser.add_argument("--match-tolerance-ms", type=float, default=5.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/09_hfo_backend_smoke_test/full_compare_pyhfo_vs_ours_zurich15_mni"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pyhfo_pool = build_pyhfo_candidate_pool(
        args.edf,
        detector_name=str(args.detector),
        low_freq_hz=float(args.low_freq_hz),
        high_freq_hz=float(args.high_freq_hz),
    )
    pyhfo_df = classify_pool_with_pyhfo(pyhfo_pool, args.output_dir, save_feature_pool=False)
    pyhfo_df = pyhfo_df.rename(columns={"label": "label_pyhfo"})
    pyhfo_df["detector"] = str(args.detector)
    pyhfo_df["start_time_s"] = pyhfo_df["start_sample"].astype(float) / float(pyhfo_pool.fs)
    pyhfo_df["end_time_s"] = pyhfo_df["end_sample"].astype(float) / float(pyhfo_pool.fs)

    raw = mne.io.read_raw_edf(args.edf, preload=False, verbose="ERROR")
    fs = float(raw.info["sfreq"])
    data_uv = np.asarray(raw.get_data(), dtype=float) * 1e6
    channel_names = [str(name) for name in raw.ch_names]
    raw.close()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ours = compute_hfo_for_gui(
                data=data_uv,
                fs=fs,
                channel_names=channel_names,
                data_start_s=0.0,
                analysis_window_s=(0.0, data_uv.shape[1] / fs),
                detector_version="pyhfo_pybrain",
                active_candidate_detectors=[str(args.detector)],
                band_label="Ripple",
                low_freq_hz=float(args.low_freq_hz),
                high_freq_hz=float(args.high_freq_hz),
                threshold_sigma=5.0,
                min_duration_ms=6.0,
                max_duration_ms=1000.0,
                boundary_padding_s=0.0,
                merge_gap_ms=10.0,
                min_cycles=6.0,
                notch_modes_by_channel={},
                checkpoint_paths={},
                device="cpu",
            )

    ours_df = pd.DataFrame(
        [
            {
                "channel": event.channel,
                "detector": event.detector,
                "start_sample": event.start_sample,
                "end_sample": event.end_sample,
                "start_time_s": event.start_time_s,
                "end_time_s": event.end_time_s,
                "artifact_score": event.artifact_score,
                "hfo_score": event.hfo_score,
                "spike_score": event.spike_score,
                "label_ours": event.classification_label,
                "is_boundary": event.is_boundary,
            }
            for event in ours.events
        ]
    )

    matches_df = match_events(
        pyhfo_df,
        ours_df,
        tolerance_s=float(args.match_tolerance_ms) / 1000.0,
    )

    pyhfo_path = args.output_dir / "pyhfo_full_pipeline_events.csv"
    ours_path = args.output_dir / "our_full_pipeline_events.csv"
    matches_path = args.output_dir / "matched_event_comparison.csv"
    pyhfo_df.to_csv(pyhfo_path, index=False)
    ours_df.to_csv(ours_path, index=False)
    matches_df.to_csv(matches_path, index=False)

    matched = int(matches_df["matched"].sum()) if not matches_df.empty else 0
    same_labels = int((matches_df["matched"] & matches_df["same_label"]).sum()) if not matches_df.empty else 0
    print(f"edf: {args.edf}")
    print(f"detector: {args.detector}")
    print(f"pyHFO events: {len(pyhfo_df)}")
    print(f"our events: {len(ours_df)}")
    print(f"pyHFO label counts: {pyhfo_df['label_pyhfo'].value_counts(dropna=False).to_dict() if not pyhfo_df.empty else {}}")
    print(f"our label counts: {ours_df['label_ours'].value_counts(dropna=False).to_dict() if not ours_df.empty else {}}")
    print(f"matched pyHFO events: {matched}/{len(pyhfo_df)}")
    print(f"label agreement on matched events: {same_labels}/{matched if matched else 0}")
    print(f"match tolerance: {args.match_tolerance_ms:g} ms")
    print(f"pyHFO csv: {pyhfo_path}")
    print(f"ours csv: {ours_path}")
    print(f"matched comparison csv: {matches_path}")


def match_events(pyhfo_df: pd.DataFrame, ours_df: pd.DataFrame, *, tolerance_s: float) -> pd.DataFrame:
    used_ours: set[int] = set()
    rows: list[dict] = []
    for py_idx, py_row in pyhfo_df.iterrows():
        channel_matches = ours_df[
            (ours_df["channel"].astype(str) == str(py_row["channel"]))
            & (~ours_df.index.isin(used_ours))
        ].copy()
        if channel_matches.empty:
            rows.append(_unmatched_row(py_idx, py_row))
            continue
        channel_matches["start_delta_s"] = (channel_matches["start_time_s"].astype(float) - float(py_row["start_time_s"])).abs()
        channel_matches["end_delta_s"] = (channel_matches["end_time_s"].astype(float) - float(py_row["end_time_s"])).abs()
        channel_matches["delta_sum_s"] = channel_matches["start_delta_s"] + channel_matches["end_delta_s"]
        within = channel_matches[
            (channel_matches["start_delta_s"] <= tolerance_s)
            & (channel_matches["end_delta_s"] <= tolerance_s)
        ]
        if within.empty:
            rows.append(_unmatched_row(py_idx, py_row))
            continue
        best_idx = int(within.sort_values("delta_sum_s").index[0])
        used_ours.add(best_idx)
        ours_row = ours_df.loc[best_idx]
        rows.append(
            {
                "pyhfo_index": int(py_idx),
                "ours_index": int(best_idx),
                "matched": True,
                "channel": py_row["channel"],
                "pyhfo_start_time_s": py_row["start_time_s"],
                "pyhfo_end_time_s": py_row["end_time_s"],
                "ours_start_time_s": ours_row["start_time_s"],
                "ours_end_time_s": ours_row["end_time_s"],
                "start_delta_ms": abs(float(ours_row["start_time_s"]) - float(py_row["start_time_s"])) * 1000.0,
                "end_delta_ms": abs(float(ours_row["end_time_s"]) - float(py_row["end_time_s"])) * 1000.0,
                "label_pyhfo": py_row["label_pyhfo"],
                "label_ours": ours_row["label_ours"],
                "same_label": str(py_row["label_pyhfo"]) == str(ours_row["label_ours"]),
            }
        )
    return pd.DataFrame(rows)


def _unmatched_row(py_idx: int, py_row: pd.Series) -> dict:
    return {
        "pyhfo_index": int(py_idx),
        "ours_index": None,
        "matched": False,
        "channel": py_row["channel"],
        "pyhfo_start_time_s": py_row["start_time_s"],
        "pyhfo_end_time_s": py_row["end_time_s"],
        "ours_start_time_s": None,
        "ours_end_time_s": None,
        "start_delta_ms": None,
        "end_delta_ms": None,
        "label_pyhfo": py_row["label_pyhfo"],
        "label_ours": None,
        "same_label": False,
    }


if __name__ == "__main__":
    main()
