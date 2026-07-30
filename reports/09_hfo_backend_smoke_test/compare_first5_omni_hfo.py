from __future__ import annotations

import argparse
import contextlib
import csv
import io
import sys
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import mne
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.computation.hfo.algorithm import _resample_to_omni_target
from app.computation.hfo.detectors import (
    OMNI_1000HZ_UPPER_FREQ_HZ,
    detect_candidates_from_array,
)


DATASET_ROOT = Path(r"D:\omni dataset complement\updated_dataset")
OUTPUT_DIR = REPO_ROOT / "reports" / "09_hfo_backend_smoke_test" / "first5_compare"
DETECTOR_TO_EXPERT = {"ste": "ste", "mni": "mni", "hilbert": "hil"}
EXPERT_TO_DETECTOR = {value: key for key, value in DETECTOR_TO_EXPERT.items()}


@dataclass(frozen=True)
class Event:
    channel: str
    detector: str
    start: int
    end: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare our HFO candidate detector output to Omni expert_hfo CSVs for the first N EDFs."
    )
    parser.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--edf",
        action="append",
        type=Path,
        default=[],
        help="Explicit EDF path to compare. Can be passed multiple times.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--tolerance-samples", type=int, default=5)
    parser.add_argument("--merge-gap-ms", type=float, default=10.0)
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="Compare only expert rows with artifact=1, used by this export as accepted/non-artifact.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--all-channels",
        action="store_true",
        help="Run on every EDF channel instead of only channels present in the expert CSV.",
    )
    parser.add_argument(
        "--all-detectors",
        action="store_true",
        help="Run all candidate detectors instead of only detectors present in the expert CSV.",
    )
    parser.add_argument(
        "--omni-preprocess",
        action="store_true",
        help="Apply Omni's EDF preprocessing: preload, 60 Hz notch, then MNE resample to 1000 Hz.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bids_root = args.dataset_root / "bids"
    expert_root = args.dataset_root / "derivatives" / "expert_hfo"
    edf_paths = [Path(path) for path in args.edf] if args.edf else sorted(bids_root.rglob("*.edf"))[: max(1, int(args.limit))]
    if not edf_paths:
        raise FileNotFoundError(f"No EDF files found under {bids_root}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, object]] = []
    unmatched_rows: list[dict[str, object]] = []

    for file_idx, edf_path in enumerate(edf_paths, start=1):
        print(f"[{file_idx}/{len(edf_paths)}] {edf_path.name}", flush=True)
        expert_path = _expert_path_for_edf(expert_root, edf_path)
        if expert_path is None:
            print("  no expert_hfo CSV found", flush=True)
            continue

        expert = _read_expert_events(
            expert_path,
            accepted_only=bool(args.accepted_only),
            target_fs=1000.0,
        )
        expert_channels = sorted({event.channel for event in expert})
        expert_detectors = sorted({event.detector for event in expert})
        detectors = (
            ["ste", "mni", "hilbert"]
            if bool(args.all_detectors)
            else [EXPERT_TO_DETECTOR.get(detector, detector) for detector in expert_detectors]
        )
        started = time.perf_counter()
        fs, shape, detected = _run_our_detection(
            edf_path,
            channels=None if bool(args.all_channels) else expert_channels,
            active_detectors=detectors,
            omni_preprocess=bool(args.omni_preprocess),
            merge_gap_ms=float(args.merge_gap_ms),
        )
        elapsed_s = time.perf_counter() - started

        per_detector = sorted(set([event.detector for event in detected] + [event.detector for event in expert]))
        print(f"  input shape={shape}, fs={fs:g}, expert={len(expert)}, ours={len(detected)}, elapsed={elapsed_s:.1f}s")
        for detector in per_detector:
            expert_subset = [event for event in expert if event.detector == detector]
            detected_subset = [event for event in detected if event.detector == detector]
            matches = _match_events(
                detected_subset,
                expert_subset,
                tolerance_samples=int(args.tolerance_samples),
            )
            overlap_matches = _match_events_by_overlap(detected_subset, expert_subset)
            matched_ours = {our_idx for our_idx, _expert_idx in matches}
            matched_expert = {_expert_idx for _our_idx, _expert_idx in matches}
            summary_rows.append(
                {
                    "edf": str(edf_path),
                    "expert_csv": str(expert_path),
                    "detector": detector,
                    "input_shape": str(shape),
                    "fs": float(fs),
                    "expert_count": len(expert_subset),
                    "our_count": len(detected_subset),
                    "strict_matched_count": len(matches),
                    "strict_our_match_fraction": _fraction(len(matches), len(detected_subset)),
                    "strict_expert_recall_fraction": _fraction(len(matches), len(expert_subset)),
                    "overlap_matched_count": len(overlap_matches),
                    "overlap_our_match_fraction": _fraction(len(overlap_matches), len(detected_subset)),
                    "overlap_expert_recall_fraction": _fraction(len(overlap_matches), len(expert_subset)),
                    "unmatched_our_count": len(detected_subset) - len(matched_ours),
                    "unmatched_expert_count": len(expert_subset) - len(matched_expert),
                    "elapsed_s": round(elapsed_s, 3),
                }
            )
            print(
                "  "
                f"{detector}: expert={len(expert_subset)}, ours={len(detected_subset)}, "
                f"strict={len(matches)}, overlap={len(overlap_matches)}",
                flush=True,
            )
            unmatched_rows.extend(
                _sample_unmatched_rows(
                    edf_path=edf_path,
                    detector=detector,
                    detected=detected_subset,
                    expert=expert_subset,
                    matched_ours=matched_ours,
                    matched_expert=matched_expert,
                )
            )

    summary_path = args.output_dir / "summary.csv"
    unmatched_path = args.output_dir / "unmatched_samples.csv"
    _write_csv(summary_path, summary_rows)
    _write_csv(unmatched_path, unmatched_rows)
    print(f"summary: {summary_path}")
    print(f"unmatched samples: {unmatched_path}")


def _run_our_detection(
    edf_path: Path,
    *,
    channels: list[str] | None,
    active_detectors: list[str],
    omni_preprocess: bool,
    merge_gap_ms: float,
) -> tuple[float, tuple[int, int], list[Event]]:
    raw = mne.io.read_raw_edf(edf_path, preload=bool(omni_preprocess), verbose="ERROR")
    if omni_preprocess:
        raw = raw.notch_filter(60, n_jobs=1, notch_widths=2, verbose=False)
        if abs(float(raw.info["sfreq"]) - 1000.0) > 1e-9:
            raw = raw.copy().resample(1000.0, n_jobs=1, verbose=False)
    original_fs = float(raw.info["sfreq"])
    if channels is None:
        picks = list(range(len(raw.ch_names)))
    else:
        name_to_idx = {str(name): idx for idx, name in enumerate(raw.ch_names)}
        picks = [name_to_idx[name] for name in channels if name in name_to_idx]
        missing = sorted(set(channels) - set(name_to_idx))
        if missing:
            print(f"  warning: {len(missing)} expert channels not found in EDF: {missing[:5]}", flush=True)
    channel_names = [str(raw.ch_names[idx]) for idx in picks]
    data_uv = np.asarray(raw.get_data(picks=picks), dtype=float) * 1e6
    if omni_preprocess:
        detection_data_uv, detection_fs = data_uv, original_fs
    else:
        detection_data_uv, detection_fs = _resample_to_omni_target(data_uv, original_fs)
    effective_high = min(500.0, OMNI_1000HZ_UPPER_FREQ_HZ)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            candidates = detect_candidates_from_array(
                detection_data_uv,
                float(detection_fs),
                channel_names,
                active_detectors=active_detectors,
                low_freq_hz=80.0,
                high_freq_hz=float(effective_high),
                threshold_sigma=5.0,
                min_duration_ms=6.0,
                merge_gap_ms=float(merge_gap_ms),
                min_cycles=6.0,
            )
    events = [
        Event(
            channel=str(candidate.channel),
            detector=DETECTOR_TO_EXPERT.get(str(candidate.detector), str(candidate.detector)),
            start=int(candidate.start_sample),
            end=int(candidate.end_sample),
        )
        for candidate in candidates
    ]
    return float(detection_fs), tuple(data_uv.shape), events


def _read_expert_events(path: Path, *, accepted_only: bool, target_fs: float) -> list[Event]:
    df = pd.read_csv(path)
    if accepted_only and "artifact" in df.columns:
        df = df[df["artifact"].astype(str) == "1"]
    events: list[Event] = []
    for row in df.itertuples(index=False):
        event_fs = float(getattr(row, "freq", target_fs) or target_fs)
        sample_scale = float(target_fs) / event_fs
        events.append(
            Event(
                channel=str(getattr(row, "name")),
                detector=str(getattr(row, "detector")),
                start=int(round(float(getattr(row, "start")) * sample_scale)),
                end=int(round(float(getattr(row, "end")) * sample_scale)),
            )
        )
    return events


def _expert_path_for_edf(expert_root: Path, edf_path: Path) -> Path | None:
    subject = next((part for part in edf_path.parts if part.startswith("sub-")), None)
    if subject is None:
        return None
    stem = edf_path.stem
    candidates = sorted((expert_root / subject).rglob(f"{stem}_expert_hfo_events.csv"))
    if candidates:
        return candidates[0]
    candidates = sorted((expert_root / subject).rglob("*_expert_hfo_events.csv"))
    return candidates[0] if len(candidates) == 1 else None


def _match_events(
    detected: list[Event],
    expert: list[Event],
    *,
    tolerance_samples: int,
) -> list[tuple[int, int]]:
    expert_by_key: dict[tuple[str, str], list[tuple[int, Event]]] = defaultdict(list)
    for idx, event in enumerate(expert):
        expert_by_key[(event.channel, event.detector)].append((idx, event))

    used_expert: set[int] = set()
    matches: list[tuple[int, int]] = []
    for our_idx, event in enumerate(detected):
        possible = []
        for expert_idx, ref in expert_by_key.get((event.channel, event.detector), []):
            if expert_idx in used_expert:
                continue
            start_diff = abs(event.start - ref.start)
            end_diff = abs(event.end - ref.end)
            if start_diff <= tolerance_samples and end_diff <= tolerance_samples:
                possible.append((start_diff + end_diff, expert_idx))
        if possible:
            _score, expert_idx = min(possible)
            used_expert.add(expert_idx)
            matches.append((our_idx, expert_idx))
    return matches


def _match_events_by_overlap(detected: list[Event], expert: list[Event]) -> list[tuple[int, int]]:
    expert_by_key: dict[tuple[str, str], list[tuple[int, Event]]] = defaultdict(list)
    for idx, event in enumerate(expert):
        expert_by_key[(event.channel, event.detector)].append((idx, event))

    used_expert: set[int] = set()
    matches: list[tuple[int, int]] = []
    for our_idx, event in enumerate(detected):
        possible = []
        for expert_idx, ref in expert_by_key.get((event.channel, event.detector), []):
            if expert_idx in used_expert:
                continue
            overlap = min(event.end, ref.end) - max(event.start, ref.start)
            if overlap <= 0:
                continue
            union = max(event.end, ref.end) - min(event.start, ref.start)
            iou = float(overlap) / float(union) if union > 0 else 0.0
            possible.append((-iou, expert_idx))
        if possible:
            _score, expert_idx = min(possible)
            used_expert.add(expert_idx)
            matches.append((our_idx, expert_idx))
    return matches


def _sample_unmatched_rows(
    *,
    edf_path: Path,
    detector: str,
    detected: list[Event],
    expert: list[Event],
    matched_ours: set[int],
    matched_expert: set[int],
    limit: int = 10,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for idx, event in enumerate(detected):
        if idx in matched_ours:
            continue
        rows.append(_event_row(edf_path, detector, "ours_unmatched", event))
        if len([row for row in rows if row["kind"] == "ours_unmatched"]) >= limit:
            break
    for idx, event in enumerate(expert):
        if idx in matched_expert:
            continue
        rows.append(_event_row(edf_path, detector, "expert_unmatched", event))
        if len([row for row in rows if row["kind"] == "expert_unmatched"]) >= limit:
            break
    return rows


def _event_row(edf_path: Path, detector: str, kind: str, event: Event) -> dict[str, object]:
    return {
        "edf": str(edf_path),
        "detector": detector,
        "kind": kind,
        "channel": event.channel,
        "start": event.start,
        "end": event.end,
        "duration_samples": event.end - event.start,
    }


def _fraction(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
