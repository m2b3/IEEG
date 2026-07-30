from __future__ import annotations

import json
from pathlib import Path

import numpy as np


EHFO_ARTIFACT_MODEL = "ehfo_artifact"
EHFO_SPIKE_MODEL = "ehfo_spike"
EHFO_HFO_MODEL = "ehfo_ehfo"

_CHECKPOINT_DIR = Path(__file__).resolve().parents[2] / "checkpoints" / "ehfo"
_DEFAULT_CHECKPOINTS = {
    EHFO_ARTIFACT_MODEL: _CHECKPOINT_DIR / "artifacts.pth",
    EHFO_SPIKE_MODEL: _CHECKPOINT_DIR / "spikes.pth",
    EHFO_HFO_MODEL: _CHECKPOINT_DIR / "eHFOs.pth",
}


def classify_ehfo(
    waveforms_uv: np.ndarray,
    *,
    checkpoint_paths: dict[str, str] | None,
    device: str = "cpu",
) -> dict[str, np.ndarray | str]:
    paths = checkpoint_paths or {}
    required = {
        EHFO_ARTIFACT_MODEL: Path(paths.get(EHFO_ARTIFACT_MODEL) or _DEFAULT_CHECKPOINTS[EHFO_ARTIFACT_MODEL]),
        EHFO_SPIKE_MODEL: Path(paths.get(EHFO_SPIKE_MODEL) or _DEFAULT_CHECKPOINTS[EHFO_SPIKE_MODEL]),
        EHFO_HFO_MODEL: Path(paths.get(EHFO_HFO_MODEL) or _DEFAULT_CHECKPOINTS[EHFO_HFO_MODEL]),
    }
    if all(value.is_file() and value.suffix.lower() in {".tar", ".pth", ".pt"} for value in required.values()):
        return _classify_omni_tar(waveforms_uv, checkpoint_paths=required, device=device)
    if any(not _checkpoint_files(value) for value in required.values()):
        return {"status": "checkpoint_missing"}

    try:
        import torch
        from safetensors.torch import load_file
        from .features import EHFOPreProcessing, generate_feature_from_df_gpu_batch, normalize_img_ehfo
        from .model import NeuralCNNForImageClassification, ResnetConfig
    except ImportError as exc:
        return {"status": f"dependency_missing: {exc}"}

    models = {}
    preprocessors = {}
    thresholds = {}
    for model_name, model_dir in required.items():
        config_path, spec_path, weights_path = _checkpoint_files(model_dir)
        config = ResnetConfig.from_dict(_read_json(config_path))
        spec = _read_json(spec_path)
        model = NeuralCNNForImageClassification(config).to(device)
        model.load_state_dict(load_file(str(weights_path), device=str(device)))
        model.eval()
        models[model_name] = model
        preprocessors[model_name] = EHFOPreProcessing.from_spec(spec)
        thresholds[model_name] = float(((spec.get("output") or {}).get("threshold", 0.5)))

    feature_param = {
        "n_jobs": 1,
        "n_feature": 1,
        "resample": 1000,
        "raw_waveform_length": 2000,
    }
    matrix = np.asarray(waveforms_uv, dtype=float)
    outputs: dict[str, list[np.ndarray]] = {
        EHFO_ARTIFACT_MODEL: [],
        EHFO_SPIKE_MODEL: [],
        EHFO_HFO_MODEL: [],
    }
    predictions: dict[str, list[np.ndarray]] = {
        EHFO_ARTIFACT_MODEL: [],
        EHFO_SPIKE_MODEL: [],
        EHFO_HFO_MODEL: [],
    }
    batch_size = 128
    with torch.no_grad():
        for start in range(0, int(matrix.shape[0]), batch_size):
            batch = matrix[start:start + batch_size]
            spectrum_np, amplitude_np = generate_feature_from_df_gpu_batch(
                batch,
                feature_param,
                n_jobs=1,
            )
            feature_np = np.stack([spectrum_np, amplitude_np], axis=1).astype(np.float32)
            artifact_features = preprocessors[EHFO_ARTIFACT_MODEL].process(
                feature_np,
                feature_sample_freq=float(feature_param["resample"]),
            )
            spike_features = preprocessors[EHFO_SPIKE_MODEL].process(
                feature_np,
                feature_sample_freq=float(feature_param["resample"]),
            )
            ehfo_features = preprocessors[EHFO_HFO_MODEL].process(
                feature_np,
                feature_sample_freq=float(feature_param["resample"]),
            )
            ehfo_spectrum = ehfo_features[:, 0:1, :, :]
            ehfo_features = np.repeat(ehfo_spectrum, repeats=3, axis=1)
            for model_name, feature_batch in (
                (EHFO_ARTIFACT_MODEL, artifact_features),
                (EHFO_SPIKE_MODEL, spike_features),
                (EHFO_HFO_MODEL, ehfo_features),
            ):
                tensor = torch.from_numpy(feature_batch).float().to(device)
                tensor = _normalize_channels(tensor, normalize_img_ehfo)
                scores = models[model_name](tensor).detach().cpu().numpy()
                outputs[model_name].append(scores)
                predictions[model_name].append((scores.reshape(-1) > thresholds[model_name]).astype(int))

    keep_scores = _flatten_batches(outputs[EHFO_ARTIFACT_MODEL])
    spike_scores = _flatten_batches(outputs[EHFO_SPIKE_MODEL])
    ehfo_scores = _flatten_batches(outputs[EHFO_HFO_MODEL])
    keep_predictions = _flatten_batches(predictions[EHFO_ARTIFACT_MODEL]).astype(int)
    spike_predictions = _flatten_batches(predictions[EHFO_SPIKE_MODEL]).astype(int)
    ehfo_predictions = _flatten_batches(predictions[EHFO_HFO_MODEL]).astype(int)

    return {
        "status": "ok",
        "pyhfo_keep_score": keep_scores,
        "artifact_score": 1.0 - keep_scores,
        "spike_score": spike_scores,
        "hfo_score": ehfo_scores,
        "ehfo_score": ehfo_scores,
        "artifact_prediction": keep_predictions,
        "spike_prediction": spike_predictions,
        "ehfo_prediction": ehfo_predictions,
        "classification_label": _labels(keep_predictions, spike_predictions, ehfo_predictions),
        "thresholds": {
            "artifact_keep": thresholds[EHFO_ARTIFACT_MODEL],
            "spike_hfo": thresholds[EHFO_SPIKE_MODEL],
            "ehfo": thresholds[EHFO_HFO_MODEL],
        },
    }


def _flatten_batches(batches: list[np.ndarray]) -> np.ndarray:
    if not batches:
        return np.asarray([], dtype=float)
    values = np.concatenate([np.asarray(batch, dtype=float).reshape(int(batch.shape[0]), -1) for batch in batches], axis=0)
    return values[:, 0] if values.ndim == 2 and values.shape[1] else values.reshape(-1)


def _classify_omni_tar(
    waveforms_uv: np.ndarray,
    *,
    checkpoint_paths: dict[str, Path],
    device: str,
) -> dict[str, np.ndarray | str]:
    try:
        import torch
        from .features import generate_omni_ehfo_feature_batch, normalize_img_ehfo
        from .model import NeuralCNN
    except ImportError as exc:
        return {"status": f"dependency_missing: {exc}"}

    models = {}
    for model_name, model_path in checkpoint_paths.items():
        try:
            checkpoint = torch.load(str(model_path), map_location=device)
        except Exception as exc:
            return {"status": f"checkpoint_load_failed: {model_name}: {exc}"}
        state_dict = checkpoint.get("state_dict") if isinstance(checkpoint, dict) else None
        if state_dict is None:
            return {"status": f"checkpoint_not_omni_state_dict: {model_name}"}
        model = NeuralCNN(num_classes=2).to(device)
        model.load_state_dict(state_dict)
        model.eval()
        models[model_name] = model

    feature_param = {
        "n_jobs": 1,
        "n_feature": 1,
        "resample": 1000,
        "raw_waveform_length": 2000,
    }
    matrix = np.asarray(waveforms_uv, dtype=float)
    outputs: dict[str, list[np.ndarray]] = {
        EHFO_ARTIFACT_MODEL: [],
        EHFO_SPIKE_MODEL: [],
        EHFO_HFO_MODEL: [],
    }
    batch_size = 128
    with torch.no_grad():
        for start in range(0, int(matrix.shape[0]), batch_size):
            batch = matrix[start:start + batch_size]
            spectrum_np, spike_np, intensity_np = generate_omni_ehfo_feature_batch(
                batch,
                feature_param,
                n_jobs=1,
            )
            spectrum = torch.from_numpy(spectrum_np).float().to(device)
            spike = torch.from_numpy(spike_np).float().to(device)
            intensity = torch.from_numpy(intensity_np).float().to(device)
            spectrum_norm = normalize_img_ehfo(spectrum)
            intensity_norm = normalize_img_ehfo(intensity)
            inputs_a = torch.stack([spectrum_norm, spectrum_norm, spectrum_norm], dim=1).to(device).float()
            inputs_s = torch.stack([spectrum_norm, spike, intensity_norm], dim=1).to(device).float()
            outputs[EHFO_ARTIFACT_MODEL].append(models[EHFO_ARTIFACT_MODEL](inputs_a).detach().cpu().numpy())
            outputs[EHFO_SPIKE_MODEL].append(models[EHFO_SPIKE_MODEL](inputs_s).detach().cpu().numpy())
            outputs[EHFO_HFO_MODEL].append(models[EHFO_HFO_MODEL](inputs_s).detach().cpu().numpy())

    keep_scores = _flatten_batches(outputs[EHFO_ARTIFACT_MODEL])
    spike_scores = _flatten_batches(outputs[EHFO_SPIKE_MODEL])
    ehfo_scores = _flatten_batches(outputs[EHFO_HFO_MODEL])
    keep_predictions = (keep_scores > 0.5).astype(int)
    spike_predictions = (spike_scores > 0.5).astype(int)
    ehfo_predictions = (ehfo_scores > 0.5).astype(int)
    return {
        "status": "ok",
        "pyhfo_keep_score": keep_scores,
        "artifact_score": 1.0 - keep_scores,
        "spike_score": spike_scores,
        "hfo_score": ehfo_scores,
        "ehfo_score": ehfo_scores,
        "artifact_prediction": keep_predictions,
        "spike_prediction": spike_predictions,
        "ehfo_prediction": ehfo_predictions,
        "classification_label": _labels(keep_predictions, spike_predictions, ehfo_predictions),
        "thresholds": {
            "artifact_keep": 0.5,
            "spike_hfo": 0.5,
            "ehfo": 0.5,
        },
        "implementation": "omni_legacy_state_dict",
    }


def _checkpoint_files(path: Path) -> tuple[Path, Path, Path] | None:
    model_dir = Path(path)
    if model_dir.is_file():
        model_dir = model_dir.parent
    config_path = model_dir / "config.json"
    spec_path = model_dir / "model.spec.json"
    weights_path = model_dir / "model.safetensors"
    if not (config_path.exists() and spec_path.exists() and weights_path.exists()):
        return None
    return config_path, spec_path, weights_path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_channels(tensor, normalizer):
    normalized = tensor.clone()
    for channel_idx in range(int(normalized.shape[1])):
        normalized[:, channel_idx, :, :] = normalizer(normalized[:, channel_idx, :, :])
    return normalized


def _labels(keep_predictions: np.ndarray, spike_predictions: np.ndarray, ehfo_predictions: np.ndarray) -> list[str]:
    labels: list[str] = []
    for keep, spike, ehfo in zip(keep_predictions, spike_predictions, ehfo_predictions):
        if int(keep) != 1:
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
