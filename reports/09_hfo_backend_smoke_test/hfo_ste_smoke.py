from __future__ import annotations

import argparse
import contextlib
import io
import sys
import warnings
from dataclasses import asdict
from pathlib import Path

import mne
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.computation.hfo.algorithm import _resample_to_omni_target
from app.computation.hfo.detectors.omni_hfo_detector import (
    DETECTOR_HILBERT,
    DETECTOR_MNI,
    DETECTOR_STE,
    OMNI_1000HZ_UPPER_FREQ_HZ,
    OMNI_EVENT_WINDOW_MS,
    detect_candidates_from_array,
    centered_window_bounds,
)


DEFAULT_RECORDING = Path(
    r"D:\omni dataset complement\updated_dataset\bids"
    r"\sub-hupHUP130\ses-01\ieeg"
    r"\sub-hupHUP130_ses-01_task-interictal_run-02_ieeg.edf"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone HFO backend smoke test. Loads a short real recording "
            "segment and runs only one candidate detector."
        )
    )
    parser.add_argument("--recording", type=Path, default=DEFAULT_RECORDING)
    parser.add_argument(
        "--detector",
        choices=[DETECTOR_STE, DETECTOR_MNI, DETECTOR_HILBERT],
        default=DETECTOR_STE,
    )
    parser.add_argument("--start-s", type=float, default=0.0)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--max-channels", type=int, default=35)
    parser.add_argument("--low-freq-hz", type=float, default=80.0)
    parser.add_argument("--high-freq-hz", type=float, default=500.0)
    parser.add_argument("--threshold-sigma", type=float, default=5.0)
    parser.add_argument("--min-duration-ms", type=float, default=6.0)
    parser.add_argument("--merge-gap-ms", type=float, default=10.0)
    parser.add_argument("--min-cycles", type=float, default=6.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.recording.exists():
        raise FileNotFoundError(f"Recording not found: {args.recording}")

    raw = _read_raw(args.recording)
    original_fs = float(raw.info["sfreq"])
    start_sample = max(0, int(round(float(args.start_s) * original_fs)))
    stop_sample = min(
        int(raw.n_times),
        start_sample + max(2, int(round(float(args.duration_s) * original_fs))),
    )
    picks = _ieeg_picks(raw, int(args.max_channels))
    channel_names = [str(raw.ch_names[idx]) for idx in picks]

    data_v = raw.get_data(picks=picks, start=start_sample, stop=stop_sample)
    data_uv = np.asarray(data_v, dtype=float) * 1e6
    detection_data_uv, effective_fs = _resample_to_omni_target(data_uv, original_fs)
    effective_high = min(float(args.high_freq_hz), OMNI_1000HZ_UPPER_FREQ_HZ)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            candidates = detect_candidates_from_array(
                detection_data_uv,
                float(effective_fs),
                channel_names,
                active_detectors=[str(args.detector)],
                low_freq_hz=float(args.low_freq_hz),
                high_freq_hz=float(effective_high),
                threshold_sigma=float(args.threshold_sigma),
                min_duration_ms=float(args.min_duration_ms),
                merge_gap_ms=float(args.merge_gap_ms),
                min_cycles=float(args.min_cycles),
            )

    events = [_event_dict(event, float(effective_fs), float(args.start_s)) for event in candidates]
    durations_ms = [
        round((event.end_sample - event.start_sample) / float(effective_fs) * 1000.0, 3)
        for event in candidates
    ]
    any_boundary = any(
        centered_window_bounds(
            event.start_sample,
            event.end_sample,
            int(detection_data_uv.shape[1]),
            int(round(OMNI_EVENT_WINDOW_MS / 1000.0 * float(effective_fs))),
        )[2]
        for event in candidates
    )

    print(f"input shape: {tuple(data_uv.shape)}")
    print("signal units: microvolts (MNE data converted from volts)")
    print(f"original sampling frequency: {original_fs:g} Hz")
    print(f"effective sampling frequency: {float(effective_fs):g} Hz")
    print(f"number of detected candidates: {len(candidates)}")
    print(f"first 10 events: {events[:10]}")
    print(f"event durations: {durations_ms}")
    print(f"any event close to signal boundary: {any_boundary}")


def _read_raw(path: Path):
    suffix = path.suffix.casefold()
    if suffix == ".edf":
        return mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
    if suffix == ".fif":
        return mne.io.read_raw_fif(path, preload=False, verbose="ERROR")
    return mne.io.read_raw(path, preload=False, verbose="ERROR")


def _ieeg_picks(raw, max_channels: int) -> list[int]:
    preferred = ["seeg", "ecog", "dbs", "eeg"]
    picks: list[int] = []
    for channel_type in preferred:
        picks.extend(
            int(idx)
            for idx in mne.pick_types(
                raw.info,
                meg=False,
                eeg=channel_type == "eeg",
                seeg=channel_type == "seeg",
                ecog=channel_type == "ecog",
                dbs=channel_type == "dbs",
                exclude="bads",
            )
        )
        if picks:
            break
    if not picks:
        picks = list(range(len(raw.ch_names)))
    return picks[: max(1, int(max_channels))]


def _event_dict(event, fs: float, segment_start_s: float) -> dict[str, object]:
    row = asdict(event)
    row["start_time_s"] = round(float(segment_start_s) + event.start_sample / fs, 6)
    row["end_time_s"] = round(float(segment_start_s) + event.end_sample / fs, 6)
    row["duration_ms"] = round((event.end_sample - event.start_sample) / fs * 1000.0, 3)
    return row


if __name__ == "__main__":
    main()
