from __future__ import annotations

import argparse
from pathlib import Path
import time

from .segmented_pipeline import (
    DEFAULT_SETTINGS,
    boundary_header,
    gamma_header,
    qc_header,
    run_segmented_recording,
    step1_header,
    step2_header,
    summary_header,
    write_rows,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the segmented Python2 spike-gamma pipeline on one recording.")
    parser.add_argument("recording", type=Path, help="EDF/FIF file, or an .ieeg metadata file pointing to one.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Folder where CSV outputs will be written.")
    parser.add_argument("--chunk-minutes", type=float, default=10.0)
    parser.add_argument("--context-seconds", type=float, default=10.0)
    parser.add_argument("--filter-context-seconds", type=float, default=30.0)
    parser.add_argument("--settings", default=DEFAULT_SETTINGS)
    parser.add_argument(
        "--max-spikes-for-boundary-gamma",
        type=int,
        default=None,
        help="Limit boundary/gamma rows for validation. Omit for full processing.",
    )
    args = parser.parse_args()

    start = time.perf_counter()
    result = run_segmented_recording(
        args.recording,
        settings=args.settings,
        chunk_minutes=args.chunk_minutes,
        context_seconds=args.context_seconds,
        filter_context_seconds=args.filter_context_seconds,
        max_spikes_for_boundary_gamma=args.max_spikes_for_boundary_gamma,
    )
    elapsed = time.perf_counter() - start

    write_outputs(args.output_dir, result)
    print(f"saved_output_dir={args.output_dir}")
    print(f"runtime_seconds={elapsed:.3f}")
    print(f"raw_detections={result['summary'][5]}")
    print(f"postprocessed_detections={result['summary'][6]}")


def write_outputs(output_dir: Path, result: dict[str, object]) -> None:
    write_rows(output_dir / "step1_detections.csv", step1_header(), result["step1"])
    write_rows(output_dir / "step2_postprocessing_by_channel.csv", step2_header(), result["step2"])
    write_rows(output_dir / "step2_qc_summary.csv", qc_header(), result["qc"])
    write_rows(output_dir / "step3_boundaries.csv", boundary_header(), result["step3"])
    write_rows(output_dir / "step4_gamma.csv", gamma_header(), result["step4"])
    write_rows(output_dir / "summary.csv", summary_header(), [result["summary"]])


if __name__ == "__main__":
    main()
