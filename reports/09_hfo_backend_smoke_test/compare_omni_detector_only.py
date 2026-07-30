from __future__ import annotations

import argparse
import contextlib
import csv
import io
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import mne
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.computation.hfo.detectors import (
    DETECTOR_HILBERT,
    DETECTOR_MNI,
    DETECTOR_STE,
    OMNI_1000HZ_UPPER_FREQ_HZ,
    detect_candidates_from_array,
)


OUTPUT_DIR = REPO_ROOT / "reports" / "09_hfo_backend_smoke_test" / "detector_only_compare"
DETECTORS = (DETECTOR_STE, DETECTOR_MNI, DETECTOR_HILBERT)


@dataclass(frozen=True)
class Event:
    channel: str
    detector: str
    start: int
    end: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare our HFO detector wrapper against Omni's legacy detector parameters only."
    )
    parser.add_argument("--edf", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--tolerance-samples", type=int, default=0)
    parser.add_argument("--detector", action="append", choices=list(DETECTORS), default=[])
    parser.add_argument("--mni-seed", type=int, default=0)
    parser.add_argument(
        "--notch-60",
        action="store_true",
        help="Apply Omni's 60 Hz notch before resampling. This is only for comparison.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    active_detectors = tuple(args.detector or DETECTORS)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for idx, edf_path in enumerate(args.edf, start=1):
        print(f"[{idx}/{len(args.edf)}] {edf_path.name}", flush=True)
        data_uv, fs, channels = _load_like_omni(edf_path, notch_60=bool(args.notch_60))
        print(f"  input shape={data_uv.shape}, fs={fs:g}, detectors={','.join(active_detectors)}", flush=True)

        started = time.perf_counter()
        reference = _run_reference_omni_legacy(data_uv, fs, channels, active_detectors, mni_seed=args.mni_seed)
        ours = _run_our_wrapper(data_uv, fs, channels, active_detectors, mni_seed=args.mni_seed)
        elapsed_s = time.perf_counter() - started

        for detector in active_detectors:
            ref_subset = [event for event in reference if event.detector == detector]
            our_subset = [event for event in ours if event.detector == detector]
            strict = _match_events(our_subset, ref_subset, tolerance_samples=int(args.tolerance_samples))
            overlap = _match_events_by_overlap(our_subset, ref_subset)
            row = {
                "edf": str(edf_path),
                "detector": detector,
                "reference_count": len(ref_subset),
                "our_count": len(our_subset),
                "strict_matched_count": len(strict),
                "overlap_matched_count": len(overlap),
                "strict_reference_recall": _fraction(len(strict), len(ref_subset)),
                "strict_our_precision": _fraction(len(strict), len(our_subset)),
                "overlap_reference_recall": _fraction(len(overlap), len(ref_subset)),
                "overlap_our_precision": _fraction(len(overlap), len(our_subset)),
                "elapsed_s": round(elapsed_s, 3),
            }
            rows.append(row)
            print(
                "  "
                f"{detector}: reference={len(ref_subset)}, ours={len(our_subset)}, "
                f"strict={len(strict)}, overlap={len(overlap)}",
                flush=True,
            )

    output_path = args.output_dir / "summary.csv"
    _write_csv(output_path, rows)
    print(f"summary: {output_path}")


def _load_like_omni(edf_path: Path, *, notch_60: bool) -> tuple[np.ndarray, float, list[str]]:
    raw = mne.io.read_raw_edf(edf_path, preload=True, verbose="ERROR")
    if notch_60:
        raw = raw.notch_filter(60, n_jobs=1, notch_widths=2, verbose=False)
    if abs(float(raw.info["sfreq"]) - 1000.0) > 1e-9:
        raw = raw.copy().resample(1000.0, n_jobs=1, verbose=False)
    channels = [str(name) for name in raw.info["ch_names"]]
    data_uv = np.asarray(raw.get_data(), dtype=float) * 1e6
    return data_uv, float(raw.info["sfreq"]), channels


def _run_reference_omni_legacy(
    data_uv: np.ndarray,
    fs: float,
    channels: list[str],
    active_detectors: tuple[str, ...],
    mni_seed: int | None,
) -> list[Event]:
    from HFODetector import hil, mni, ste

    channel_array = np.asarray(channels, dtype=object)
    filter_freq = [80, int(OMNI_1000HZ_UPPER_FREQ_HZ)]
    events: list[Event] = []
    detector_specs = []
    if DETECTOR_STE in active_detectors:
        detector_specs.append(
            (
                DETECTOR_STE,
                ste.STEDetector(
                    sample_freq=float(fs),
                    filter_freq=filter_freq,
                    rms_window=3e-3,
                    min_window=6e-3,
                    min_gap=10e-3,
                    epoch_len=600,
                    min_osc=6,
                    rms_thres=5,
                    peak_thres=3,
                    n_jobs=1,
                    front_num=1,
                ),
            )
        )
    if DETECTOR_MNI in active_detectors:
        detector_specs.append(
            (
                DETECTOR_MNI,
                mni.MNIDetector(
                    float(fs),
                    filter_freq=filter_freq,
                    epoch_time=10,
                    epo_CHF=60,
                    per_CHF=95 / 100,
                    min_win=10e-3,
                    min_gap=10e-3,
                    thrd_perc=99.9999 / 100,
                    base_seg=125e-3,
                    base_shift=0.5,
                    base_thrd=0.67,
                    base_min=5,
                    n_jobs=1,
                    front_num=1,
                    seed=mni_seed,
                ),
            )
        )
    if DETECTOR_HILBERT in active_detectors:
        detector_specs.append(
            (
                DETECTOR_HILBERT,
                hil.HILDetector(
                    sample_freq=float(fs),
                    filter_freq=filter_freq,
                    sd_thres=5,
                    min_window=10e-3,
                    epoch_len=3600,
                    n_jobs=1,
                    front_num=1,
                ),
            )
        )
    for detector_name, detector in detector_specs:
        events.extend(_run_detector(detector, data_uv, channel_array, detector_name))
    return sorted(events, key=lambda event: (event.channel.casefold(), event.start, event.end, event.detector))


def _run_our_wrapper(
    data_uv: np.ndarray,
    fs: float,
    channels: list[str],
    active_detectors: tuple[str, ...],
    mni_seed: int | None,
) -> list[Event]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            candidates = detect_candidates_from_array(
                data_uv,
                float(fs),
                channels,
                active_detectors=active_detectors,
                low_freq_hz=80.0,
                high_freq_hz=float(OMNI_1000HZ_UPPER_FREQ_HZ),
                threshold_sigma=5.0,
                min_duration_ms=6.0,
                merge_gap_ms=10.0,
                min_cycles=6.0,
                mni_seed=mni_seed,
            )
    return [
        Event(
            channel=str(candidate.channel),
            detector=str(candidate.detector),
            start=int(candidate.start_sample),
            end=int(candidate.end_sample),
        )
        for candidate in candidates
    ]


def _run_detector(detector, data_uv: np.ndarray, channels: np.ndarray, detector_name: str) -> list[Event]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            channel_names, start_end_by_channel = detector.detect_multi_channels(data_uv, channels)
    events: list[Event] = []
    for channel_name, start_end in zip(channel_names, start_end_by_channel):
        arr = np.asarray(start_end, dtype=float)
        if arr.size == 0:
            continue
        arr = np.atleast_2d(arr)
        for start, end in arr[:, :2]:
            events.append(
                Event(
                    channel=str(channel_name),
                    detector=str(detector_name),
                    start=int(round(float(start))),
                    end=int(round(float(end))),
                )
            )
    return events


def _match_events(detected: list[Event], reference: list[Event], *, tolerance_samples: int) -> list[tuple[int, int]]:
    reference_by_key: dict[tuple[str, str], list[tuple[int, Event]]] = defaultdict(list)
    for idx, event in enumerate(reference):
        reference_by_key[(event.channel, event.detector)].append((idx, event))
    used_reference: set[int] = set()
    matches: list[tuple[int, int]] = []
    for our_idx, event in enumerate(detected):
        possible = []
        for ref_idx, ref in reference_by_key.get((event.channel, event.detector), []):
            if ref_idx in used_reference:
                continue
            start_diff = abs(event.start - ref.start)
            end_diff = abs(event.end - ref.end)
            if start_diff <= tolerance_samples and end_diff <= tolerance_samples:
                possible.append((start_diff + end_diff, ref_idx))
        if possible:
            _score, ref_idx = min(possible)
            used_reference.add(ref_idx)
            matches.append((our_idx, ref_idx))
    return matches


def _match_events_by_overlap(detected: list[Event], reference: list[Event]) -> list[tuple[int, int]]:
    reference_by_key: dict[tuple[str, str], list[tuple[int, Event]]] = defaultdict(list)
    for idx, event in enumerate(reference):
        reference_by_key[(event.channel, event.detector)].append((idx, event))
    used_reference: set[int] = set()
    matches: list[tuple[int, int]] = []
    for our_idx, event in enumerate(detected):
        possible = []
        for ref_idx, ref in reference_by_key.get((event.channel, event.detector), []):
            if ref_idx in used_reference:
                continue
            overlap = min(event.end, ref.end) - max(event.start, ref.start)
            if overlap <= 0:
                continue
            union = max(event.end, ref.end) - min(event.start, ref.start)
            iou = float(overlap) / float(union) if union > 0 else 0.0
            possible.append((-iou, ref_idx))
        if possible:
            _score, ref_idx = min(possible)
            used_reference.add(ref_idx)
            matches.append((our_idx, ref_idx))
    return matches


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
