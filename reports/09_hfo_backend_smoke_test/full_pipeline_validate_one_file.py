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

from app.computation.hfo.algorithm import compute_hfo_for_gui


DEFAULT_EDF = Path(
    r"D:\omni dataset complement\updated_dataset\bids"
    r"\sub-zurich14\ses-01\ieeg"
    r"\sub-zurich14_ses-01_run-01_ieeg.edf"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full HFO detector + classifier backend validation on one EDF.")
    parser.add_argument("--edf", type=Path, default=DEFAULT_EDF)
    parser.add_argument("--detectors", default="mni", help="Comma-separated candidate detectors.")
    parser.add_argument("--low-freq-hz", type=float, default=80.0)
    parser.add_argument("--high-freq-hz", type=float, default=300.0)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/09_hfo_backend_smoke_test/full_pipeline_zurich14"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw = mne.io.read_raw_edf(args.edf, preload=False, verbose="ERROR")
    fs = float(raw.info["sfreq"])
    data_uv = np.asarray(raw.get_data(), dtype=float) * 1e6
    channel_names = [str(name) for name in raw.ch_names]
    raw.close()

    active_detectors = [name.strip() for name in str(args.detectors).split(",") if name.strip()]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = compute_hfo_for_gui(
                data=data_uv,
                fs=fs,
                channel_names=channel_names,
                data_start_s=0.0,
                analysis_window_s=(0.0, data_uv.shape[1] / fs),
                detector_version="pyhfo_omni_legacy",
                active_candidate_detectors=active_detectors,
                band_label="Ripple",
                low_freq_hz=float(args.low_freq_hz),
                high_freq_hz=float(args.high_freq_hz),
                threshold_sigma=5.0,
                min_duration_ms=6.0,
                merge_gap_ms=10.0,
                min_cycles=6.0,
                notch_modes_by_channel={},
                checkpoint_paths={},
                device="cpu",
            )

    rows = [
        {
            "channel": event.channel,
            "detector": event.detector,
            "start_sample": event.start_sample,
            "end_sample": event.end_sample,
            "start_time_s": event.start_time_s,
            "end_time_s": event.end_time_s,
            "duration_ms": event.duration_ms,
            "is_boundary": event.is_boundary,
            "artifact_score": event.artifact_score,
            "hfo_score": event.hfo_score,
            "spike_score": event.spike_score,
            "classification_label": event.classification_label,
        }
        for event in result.events
    ]
    events_df = pd.DataFrame(rows)
    events_path = args.output_dir / "full_pipeline_events.csv"
    events_df.to_csv(events_path, index=False)

    label_counts = events_df["classification_label"].value_counts(dropna=False).to_dict() if not events_df.empty else {}
    detector_counts = events_df["detector"].value_counts(dropna=False).to_dict() if not events_df.empty else {}
    missing_scores = int(
        events_df[["artifact_score", "hfo_score", "spike_score"]].isna().any(axis=1).sum()
    ) if not events_df.empty else 0
    boundary_events = int(events_df["is_boundary"].sum()) if not events_df.empty else 0

    print(f"edf: {args.edf}")
    print(f"input shape: {tuple(data_uv.shape)}")
    print(f"input fs: {fs:g} Hz")
    print(f"active detectors: {active_detectors}")
    print(f"classification status: {result.metadata.get('classification_status')}")
    print(f"total events: {len(result.events)}")
    print(f"detector counts: {detector_counts}")
    print(f"class counts: {label_counts}")
    print(f"missing score rows: {missing_scores}")
    print(f"boundary events: {boundary_events}")
    print(f"candidate labels: {int(label_counts.get('candidate', 0))}")
    print(f"events csv: {events_path}")
    print(
        "first 10: "
        + str(
            [
                (
                    event.channel,
                    event.detector,
                    event.start_sample,
                    event.end_sample,
                    event.classification_label,
                    round(float(event.artifact_score or 0.0), 4),
                    round(float(event.hfo_score or 0.0), 4),
                    round(float(event.spike_score or 0.0), 4),
                )
                for event in result.events[:10]
            ]
        )
    )


if __name__ == "__main__":
    main()
