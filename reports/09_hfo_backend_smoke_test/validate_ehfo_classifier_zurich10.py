from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
from scipy import signal

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.computation.hfo.classification.ehfo.classifier import (
    EHFO_ARTIFACT_MODEL,
    EHFO_HFO_MODEL,
    EHFO_SPIKE_MODEL,
)
from app.computation.hfo.classification.ehfo.features import (
    generate_omni_ehfo_feature_batch,
    normalize_img_ehfo,
)
from app.computation.hfo.classification.ehfo.model import NeuralCNN
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
    "reports/09_hfo_backend_smoke_test/ehfo_classifier_validation_zurich10"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate our eHFO classifier against official Omni code on Zurich10 candidates."
    )
    parser.add_argument("--edf", type=Path, default=DEFAULT_EDF)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--omni-repo", type=Path, default=DEFAULT_OMNI_REPO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="0 means all candidates.")
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_time = time.perf_counter()
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

    if abs(fs - 1000.0) < 1e-9:
        data_1000 = data_uv
    else:
        ratio = 1000.0 / fs
        from fractions import Fraction

        frac = Fraction(ratio).limit_denominator(1000)
        data_1000 = signal.resample_poly(data_uv, frac.numerator, frac.denominator, axis=1)

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

    feature_param = {
        "n_jobs": 1,
        "n_feature": 1,
        "resample": 1000,
        "raw_waveform_length": 2000,
    }
    omni_path = Path(args.omni_repo).resolve()
    if str(omni_path) not in sys.path:
        sys.path.insert(0, str(omni_path))
    omni_features = importlib.import_module("omni_ieeg.event_model.ehfo_classification.features")
    omni_model = importlib.import_module("omni_ieeg.event_model.ehfo_classification.model")

    own_spec, own_spike_img, own_intensity = generate_omni_ehfo_feature_batch(
        waveforms,
        feature_param,
        n_jobs=1,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref_spec, ref_spike_img, ref_intensity = omni_features.generate_feature_from_df_gpu_batch(
            waveforms,
            feature_param,
            device=str(args.device),
            n_jobs=1,
        )

    feature_diffs = {
        "spectrum_max_abs_diff": _max_abs_diff(own_spec, ref_spec),
        "spike_image_max_abs_diff": _max_abs_diff(own_spike_img, ref_spike_img),
        "intensity_image_max_abs_diff": _max_abs_diff(own_intensity, ref_intensity),
    }

    own_result = _classify_precomputed_features(
        model_module=sys.modules[NeuralCNN.__module__],
        feature_module=sys.modules[normalize_img_ehfo.__module__],
        spectrum_np=own_spec,
        spike_np=own_spike_img,
        intensity_np=own_intensity,
        device=str(args.device),
    )
    ref_result = _classify_reference_precomputed_features(
        omni_model=omni_model,
        omni_features=omni_features,
        spectrum_np=ref_spec,
        spike_np=ref_spike_img,
        intensity_np=ref_intensity,
        device=str(args.device),
    )

    comparison = candidate_df.reset_index(drop=True).copy()
    comparison["real_start_1000_sample"] = real_starts
    comparison["real_end_1000_sample"] = real_ends
    comparison["boundary_waveform"] = boundaries
    for key in ("artifact_score", "spike_score", "ehfo_score"):
        comparison[f"ours_{key}"] = own_result[key]
        comparison[f"omni_{key}"] = ref_result[key]
        comparison[f"{key}_abs_diff"] = np.abs(
            np.asarray(own_result[key], dtype=float) - np.asarray(ref_result[key], dtype=float)
        )
    comparison["ours_label"] = own_result["classification_label"]
    comparison["omni_label"] = ref_result["classification_label"]
    comparison["same_label"] = comparison["ours_label"].astype(str) == comparison["omni_label"].astype(str)
    comparison.to_csv(args.output_dir / "ehfo_zurich10_classifier_comparison.csv", index=False)

    summary: dict[str, Any] = {
        "edf": str(args.edf),
        "candidate_csv": str(args.candidates),
        "omni_repo": str(args.omni_repo),
        "n_candidates": int(len(candidate_df)),
        "input_fs_hz": fs,
        "effective_fs_hz": 1000.0,
        "waveform_shape": list(waveforms.shape),
        "boundary_waveforms": int(np.sum(boundaries)),
        "feature_diffs": feature_diffs,
        "max_score_abs_diff": {
            key: float(comparison[f"{key}_abs_diff"].max())
            for key in ("artifact_score", "spike_score", "ehfo_score")
        },
        "label_agreement": int(comparison["same_label"].sum()),
        "label_total": int(len(comparison)),
        "ours_label_counts": _counts(comparison["ours_label"]),
        "omni_label_counts": _counts(comparison["omni_label"]),
        "runtime_s": time.perf_counter() - start_time,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_summary_txt(args.output_dir / "summary.txt", summary)

    print(f"edf: {args.edf}")
    print(f"candidate csv: {args.candidates}")
    print(f"n candidates: {summary['n_candidates']}")
    print(f"waveform shape: {summary['waveform_shape']}")
    print(f"feature diffs: {summary['feature_diffs']}")
    print(f"max score abs diff: {summary['max_score_abs_diff']}")
    print(f"label agreement: {summary['label_agreement']}/{summary['label_total']}")
    print(f"ours counts: {summary['ours_label_counts']}")
    print(f"omni counts: {summary['omni_label_counts']}")
    print(f"output: {args.output_dir}")


def _classify_precomputed_features(
    *,
    model_module: Any,
    feature_module: Any,
    spectrum_np: np.ndarray,
    spike_np: np.ndarray,
    intensity_np: np.ndarray,
    device: str,
) -> dict[str, Any]:
    import torch

    checkpoint_dir = REPO_ROOT / "app" / "computation" / "hfo" / "checkpoints" / "ehfo"
    paths = {
        EHFO_ARTIFACT_MODEL: checkpoint_dir / "artifacts.pth",
        EHFO_SPIKE_MODEL: checkpoint_dir / "spikes.pth",
        EHFO_HFO_MODEL: checkpoint_dir / "eHFOs.pth",
    }
    models = {}
    for name, path in paths.items():
        model = model_module.NeuralCNN(num_classes=2).to(device)
        checkpoint = torch.load(str(path), map_location=device)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        models[name] = model

    keep_batches: list[np.ndarray] = []
    spike_batches: list[np.ndarray] = []
    ehfo_batches: list[np.ndarray] = []
    batch_size = 64
    with torch.no_grad():
        for start in range(0, int(spectrum_np.shape[0]), batch_size):
            stop = start + batch_size
            spectrum = torch.from_numpy(spectrum_np[start:stop]).float().to(device)
            spike = torch.from_numpy(spike_np[start:stop]).float().to(device)
            intensity_tensor = torch.from_numpy(intensity_np[start:stop]).float().to(device)
            spectrum_norm = feature_module.normalize_img_ehfo(spectrum)
            intensity_norm = feature_module.normalize_img_ehfo(intensity_tensor)
            inputs_a = torch.stack([spectrum_norm, spectrum_norm, spectrum_norm], dim=1).to(device).float()
            inputs_s = torch.stack([spectrum_norm, spike, intensity_norm], dim=1).to(device).float()
            keep_batches.append(models[EHFO_ARTIFACT_MODEL](inputs_a).detach().cpu().numpy().reshape(-1))
            spike_batches.append(models[EHFO_SPIKE_MODEL](inputs_s).detach().cpu().numpy().reshape(-1))
            ehfo_batches.append(models[EHFO_HFO_MODEL](inputs_s).detach().cpu().numpy().reshape(-1))
    keep = np.concatenate(keep_batches)
    spike_score = np.concatenate(spike_batches)
    ehfo_score = np.concatenate(ehfo_batches)

    keep_pred = (keep > 0.5).astype(int)
    spike_pred = (spike_score > 0.5).astype(int)
    ehfo_pred = (ehfo_score > 0.5).astype(int)
    return {
        "artifact_score": 1.0 - keep,
        "spike_score": spike_score,
        "ehfo_score": ehfo_score,
        "classification_label": _labels(keep_pred, spike_pred, ehfo_pred),
    }


def _classify_reference_precomputed_features(
    *,
    omni_model: Any,
    omni_features: Any,
    spectrum_np: np.ndarray,
    spike_np: np.ndarray,
    intensity_np: np.ndarray,
    device: str,
) -> dict[str, Any]:
    import torch
    import torchvision.models as tv_models

    original_resnet18 = omni_model.models.resnet18
    omni_model.models.resnet18 = lambda *args, **kwargs: original_resnet18(weights=None)
    try:
        checkpoint_dir = REPO_ROOT / "app" / "computation" / "hfo" / "checkpoints" / "ehfo"
        paths = {
            EHFO_ARTIFACT_MODEL: checkpoint_dir / "artifacts.pth",
            EHFO_SPIKE_MODEL: checkpoint_dir / "spikes.pth",
            EHFO_HFO_MODEL: checkpoint_dir / "eHFOs.pth",
        }
        models = {}
        for name, path in paths.items():
            model = omni_model.NeuralCNN(num_classes=2).to(device)
            checkpoint = torch.load(str(path), map_location=device)
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            models[name] = model

        keep_batches: list[np.ndarray] = []
        spike_batches: list[np.ndarray] = []
        ehfo_batches: list[np.ndarray] = []
        batch_size = 64
        with torch.no_grad():
            for start in range(0, int(spectrum_np.shape[0]), batch_size):
                stop = start + batch_size
                spectrum = torch.from_numpy(spectrum_np[start:stop]).float().to(device)
                spike = torch.from_numpy(spike_np[start:stop]).float().to(device)
                intensity_tensor = torch.from_numpy(intensity_np[start:stop]).float().to(device)
                spectrum_norm = omni_features.normalize_img_ehfo(spectrum)
                intensity_norm = omni_features.normalize_img_ehfo(intensity_tensor)
                inputs_a = torch.stack([spectrum_norm, spectrum_norm, spectrum_norm], dim=1).to(device).float()
                inputs_s = torch.stack([spectrum_norm, spike, intensity_norm], dim=1).to(device).float()
                keep_batches.append(models[EHFO_ARTIFACT_MODEL](inputs_a).detach().cpu().numpy().reshape(-1))
                spike_batches.append(models[EHFO_SPIKE_MODEL](inputs_s).detach().cpu().numpy().reshape(-1))
                ehfo_batches.append(models[EHFO_HFO_MODEL](inputs_s).detach().cpu().numpy().reshape(-1))
        keep = np.concatenate(keep_batches)
        spike_score = np.concatenate(spike_batches)
        ehfo_score = np.concatenate(ehfo_batches)
    finally:
        omni_model.models.resnet18 = original_resnet18

    keep_pred = (keep > 0.5).astype(int)
    spike_pred = (spike_score > 0.5).astype(int)
    ehfo_pred = (ehfo_score > 0.5).astype(int)
    return {
        "artifact_score": 1.0 - keep,
        "spike_score": spike_score,
        "ehfo_score": ehfo_score,
        "classification_label": _labels(keep_pred, spike_pred, ehfo_pred),
    }


def _labels(keep_predictions: np.ndarray, spike_predictions: np.ndarray, ehfo_predictions: np.ndarray) -> list[str]:
    labels: list[str] = []
    for keep, spike, ehfo in zip(keep_predictions, spike_predictions, ehfo_predictions):
        if int(keep) == 0:
            labels.append("artifact")
        elif int(spike) == 1 and int(ehfo) == 1:
            labels.append("spike-eHFO")
        elif int(spike) == 1:
            labels.append("spike-HFO")
        elif int(ehfo) == 1:
            labels.append("eHFO")
        else:
            labels.append("HFO")
    return labels


def _max_abs_diff(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(left, dtype=float) - np.asarray(right, dtype=float))))


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
        f"Feature diffs: {summary['feature_diffs']}",
        f"Max score abs diff: {summary['max_score_abs_diff']}",
        f"Label agreement: {summary['label_agreement']}/{summary['label_total']}",
        f"Our label counts: {summary['ours_label_counts']}",
        f"Omni label counts: {summary['omni_label_counts']}",
        f"Runtime s: {summary['runtime_s']:.3f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
