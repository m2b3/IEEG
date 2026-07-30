from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
from scipy import signal

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.computation.hfo.classification.pyhfo_omni_legacy import classify_pyhfo_omni_legacy
from app.computation.hfo.classification._pyhfo_binary_common.classifier import (
    PYHFO_ARTIFACT_MODEL,
    PYHFO_SPIKE_MODEL,
)
from app.computation.hfo.detectors.omni_hfo_detector import HFOCandidate, extract_event_waveforms

DEFAULT_EDF = Path(
    r"D:\omni dataset complement\updated_dataset\bids"
    r"\sub-zurich10\ses-01\ieeg"
    r"\sub-zurich10_ses-01_run-01_ieeg.edf"
)
DEFAULT_CANDIDATES = Path(
    "reports/09_hfo_backend_smoke_test"
    "/full_pipeline_zurich10_gui_venv_after_torch"
    "/full_pipeline_events.csv"
)
DEFAULT_OMNI_REPO = Path(".tmp_omni_ieeg_validation")
DEFAULT_OUTPUT_DIR = Path(
    "reports/09_hfo_backend_smoke_test/omni_legacy_pyhfo_validation_zurich10"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate our legacy pyHFO classifier against Omni legacy pyHFO inference."
    )
    parser.add_argument("--edf", type=Path, default=DEFAULT_EDF)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--omni-repo", type=Path, default=DEFAULT_OMNI_REPO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="0 means all candidates.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--freq-max-hz", type=float, default=500.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    candidate_df = pd.read_csv(args.candidates)
    if int(args.limit) > 0:
        candidate_df = candidate_df.head(int(args.limit)).copy()
    if candidate_df.empty:
        raise ValueError("Candidate CSV has no events.")

    raw = mne.io.read_raw_edf(args.edf, preload=False, verbose="ERROR")
    fs = float(raw.info["sfreq"])
    channels = [str(ch) for ch in raw.ch_names]
    data_uv = np.asarray(raw.get_data(), dtype=float) * 1e6
    raw.close()

    data_1000 = _resample_to_1000(data_uv, fs)
    candidates = [
        HFOCandidate(
            channel=str(row.channel),
            detector=str(row.detector),
            start_sample=int(round(float(row.start_time_s) * 1000.0)),
            end_sample=int(round(float(row.end_time_s) * 1000.0)),
        )
        for row in candidate_df.itertuples(index=False)
    ]
    waveforms, real_starts, real_ends, boundaries = extract_event_waveforms(
        np.asarray(data_1000, dtype=float),
        candidates,
        channels,
        window_samples=2000,
    )

    our = classify_pyhfo_omni_legacy(
        waveforms,
        checkpoint_paths=None,
        device=str(args.device),
    )
    if str(our.get("status")) != "ok":
        raise RuntimeError(f"Our pyHFO classification failed: {our.get('status')}")

    omni = _run_omni_legacy_pyhfo(
        waveforms=waveforms,
        real_starts=real_starts,
        real_ends=real_ends,
        boundaries=boundaries,
        candidate_df=candidate_df,
        omni_repo=Path(args.omni_repo),
        output_dir=Path(args.output_dir),
        device=str(args.device),
        freq_max_hz=float(args.freq_max_hz),
    )

    comparison = candidate_df.reset_index(drop=True).copy()
    comparison["real_start_1000_sample"] = real_starts
    comparison["real_end_1000_sample"] = real_ends
    comparison["boundary_waveform"] = boundaries
    comparison["ours_keep_score"] = np.asarray(our["pyhfo_keep_score"], dtype=float)
    comparison["omni_keep_score"] = np.asarray(omni["keep_score"], dtype=float)
    comparison["keep_score_abs_diff"] = np.abs(comparison["ours_keep_score"] - comparison["omni_keep_score"])
    comparison["ours_artifact_score"] = np.asarray(our["artifact_score"], dtype=float)
    comparison["omni_artifact_score"] = np.asarray(omni["artifact_score"], dtype=float)
    comparison["artifact_score_abs_diff"] = np.abs(
        comparison["ours_artifact_score"] - comparison["omni_artifact_score"]
    )
    comparison["ours_spike_score"] = np.asarray(our["spike_score"], dtype=float)
    comparison["omni_spike_score"] = np.asarray(omni["spike_score"], dtype=float)
    comparison["spike_score_abs_diff"] = np.abs(comparison["ours_spike_score"] - comparison["omni_spike_score"])
    comparison["ours_hfo_score"] = np.asarray(our["hfo_score"], dtype=float)
    comparison["omni_hfo_score"] = np.asarray(omni["hfo_score"], dtype=float)
    comparison["hfo_score_abs_diff"] = np.abs(comparison["ours_hfo_score"] - comparison["omni_hfo_score"])
    comparison["ours_label"] = list(our["classification_label"])
    comparison["omni_label"] = list(omni["classification_label"])
    comparison["same_label"] = comparison["ours_label"].astype(str) == comparison["omni_label"].astype(str)
    comparison.to_csv(args.output_dir / "legacy_pyhfo_zurich10_omni_comparison.csv", index=False)

    summary: dict[str, Any] = {
        "edf": str(args.edf),
        "candidate_csv": str(args.candidates),
        "omni_repo": str(args.omni_repo),
        "n_candidates": int(len(candidate_df)),
        "input_fs_hz": fs,
        "effective_fs_hz": 1000.0,
        "waveform_shape": list(waveforms.shape),
        "boundary_waveforms": int(np.sum(boundaries)),
        "freq_max_hz": float(args.freq_max_hz),
        "max_score_abs_diff": {
            "keep_score": float(comparison["keep_score_abs_diff"].max()),
            "artifact_score": float(comparison["artifact_score_abs_diff"].max()),
            "spike_score": float(comparison["spike_score_abs_diff"].max()),
            "hfo_score": float(comparison["hfo_score_abs_diff"].max()),
        },
        "label_agreement": int(comparison["same_label"].sum()),
        "label_total": int(len(comparison)),
        "ours_label_counts": _counts(comparison["ours_label"]),
        "omni_label_counts": _counts(comparison["omni_label"]),
        "runtime_s": time.perf_counter() - started,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_summary_txt(args.output_dir / "summary.txt", summary)

    print(f"edf: {args.edf}")
    print(f"candidate csv: {args.candidates}")
    print(f"n candidates: {summary['n_candidates']}")
    print(f"waveform shape: {summary['waveform_shape']}")
    print(f"max score abs diff: {summary['max_score_abs_diff']}")
    print(f"label agreement: {summary['label_agreement']}/{summary['label_total']}")
    print(f"ours counts: {summary['ours_label_counts']}")
    print(f"omni counts: {summary['omni_label_counts']}")
    print(f"output: {args.output_dir}")


def _resample_to_1000(data: np.ndarray, fs: float) -> np.ndarray:
    if abs(float(fs) - 1000.0) < 1e-9:
        return np.asarray(data, dtype=float)
    from fractions import Fraction

    ratio = Fraction(1000.0 / float(fs)).limit_denominator(1000)
    return np.asarray(signal.resample_poly(np.asarray(data, dtype=float), ratio.numerator, ratio.denominator, axis=1))


def _run_omni_legacy_pyhfo(
    *,
    waveforms: np.ndarray,
    real_starts: list[int],
    real_ends: list[int],
    boundaries: list[bool],
    candidate_df: pd.DataFrame,
    omni_repo: Path,
    output_dir: Path,
    device: str,
    freq_max_hz: float,
) -> dict[str, Any]:
    omni_root = omni_repo.resolve()
    if str(omni_root) not in sys.path:
        sys.path.insert(0, str(omni_root))

    pyhfo_pkg = importlib.import_module("omni_ieeg.event_model.pyhfo_classification")
    sys.modules["src"] = pyhfo_pkg
    pyhfo_script = importlib.import_module("omni_ieeg.event_model.legacy_model_inference.pyhfo_classification")

    feature_param = dict(pyhfo_script.feature_param)
    feature_param["model_additional_parameter"] = {
        PYHFO_ARTIFACT_MODEL: {"n_feature": 1, "time_window_ms": 1000},
        PYHFO_SPIKE_MODEL: {"n_feature": 2, "time_window_ms": 1000},
    }
    feature_param["n_jobs"] = 1
    feature_param["n_feature"] = 1
    feature_param["resample"] = 1000
    feature_param["raw_waveform_length"] = 2000
    feature_param["freq_min_hz"] = 10
    feature_param["freq_max_hz"] = float(freq_max_hz)
    feature_param["image_size"] = 224

    checkpoint_dir = REPO_ROOT / "app" / "computation" / "hfo" / "checkpoints" / "pyhfo_legacy_binary"
    models: dict[str, Any] = {}
    preprocessings: dict[str, Any] = {}
    for model_name, path in {
        PYHFO_ARTIFACT_MODEL: checkpoint_dir / "model_a.tar",
        PYHFO_SPIKE_MODEL: checkpoint_dir / "model_s.tar",
    }.items():
        ckpt = _torch_load_with_src_alias(path, device)
        models[model_name] = ckpt["model"].to(device).float()
        models[model_name].eval()
        preprocessing_dict = dict(ckpt["preprocessing"])
        preprocessing_dict["fs"] = feature_param["resample"]
        preprocessing = pyhfo_script.PreProcessing.from_dict(preprocessing_dict)
        preprocessing.disable_random_shift()
        preprocessings[model_name] = preprocessing

    feature_npz = output_dir / "zurich10_legacy_pyhfo_features_for_omni.npz"
    np.savez(
        feature_npz,
        start=np.asarray(candidate_df["start_time_s"], dtype=float) * 1000.0,
        end=np.asarray(candidate_df["end_time_s"], dtype=float) * 1000.0,
        real_start=np.asarray(real_starts, dtype=int),
        real_end=np.asarray(real_ends, dtype=int),
        is_boundary=np.asarray(boundaries, dtype=bool),
        name=np.asarray(candidate_df["channel"].astype(str), dtype=object),
        hfo_waveforms=np.asarray(waveforms, dtype=float),
        detector=np.asarray(candidate_df["detector"].astype(str), dtype=object),
        participant=np.asarray(["sub-zurich10"] * len(candidate_df), dtype=object),
        session=np.asarray(["ses-01"] * len(candidate_df), dtype=object),
        file_name=np.asarray(["sub-zurich10_ses-01_run-01_ieeg.edf"] * len(candidate_df), dtype=object),
    )

    out_df = pyhfo_script.inference_one_patient(str(feature_npz), models, device, feature_param, preprocessings)
    keep_score = np.asarray(out_df[PYHFO_ARTIFACT_MODEL].apply(_scalar_from_cell), dtype=float)
    raw_spike_score = np.asarray(out_df[PYHFO_SPIKE_MODEL].apply(_scalar_from_cell), dtype=float)
    keep_binary = (keep_score > 0.5).astype(int)
    spike_score = np.zeros(len(keep_score), dtype=float)
    spike_binary = np.zeros(len(keep_score), dtype=int) - 1
    keep_idx = np.where(keep_binary > 0)[0]
    spike_score[keep_idx] = raw_spike_score[keep_idx]
    spike_binary[keep_idx] = (raw_spike_score[keep_idx] > 0.5).astype(int)
    artifact_score = 1.0 - keep_score
    hfo_score = keep_score * (1.0 - spike_score)
    return {
        "keep_score": keep_score,
        "artifact_score": artifact_score,
        "spike_score": spike_score,
        "hfo_score": hfo_score,
        "classification_label": [_label_from_binary(k, s) for k, s in zip(keep_binary, spike_binary)],
    }


def _torch_load_with_src_alias(path: Path, device: str) -> dict[str, Any]:
    import torch

    try:
        return torch.load(str(path), map_location="cpu", weights_only=False)
    except ModuleNotFoundError:
        import app.computation.hfo.classification._pyhfo_binary_common.model as model_module

        src_module = types.ModuleType("src")
        src_module.model = model_module
        sys.modules.setdefault("src", src_module)
        sys.modules.setdefault("src.model", model_module)
        return torch.load(str(path), map_location="cpu", weights_only=False)


def _scalar_from_cell(value: Any) -> float:
    arr = np.asarray(value, dtype=float).reshape(-1)
    return float(arr[0]) if arr.size else float("nan")


def _label_from_binary(keep: int, spike: int) -> str:
    if int(keep) <= 0:
        return "artifact"
    if int(spike) > 0:
        return "spike-HFO"
    return "HFO"


def _counts(values: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in values.value_counts(dropna=False).to_dict().items()}


def _write_summary_txt(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        f"EDF: {summary['edf']}",
        f"Candidate CSV: {summary['candidate_csv']}",
        f"Candidates: {summary['n_candidates']}",
        f"Input fs Hz: {summary['input_fs_hz']}",
        f"Effective fs Hz: {summary['effective_fs_hz']}",
        f"Waveform shape: {summary['waveform_shape']}",
        f"Boundary waveforms: {summary['boundary_waveforms']}",
        f"Feature max frequency Hz: {summary['freq_max_hz']}",
        f"Max score abs diff: {summary['max_score_abs_diff']}",
        f"Label agreement: {summary['label_agreement']}/{summary['label_total']}",
        f"Our label counts: {summary['ours_label_counts']}",
        f"Omni label counts: {summary['omni_label_counts']}",
        f"Runtime s: {summary['runtime_s']:.3f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
