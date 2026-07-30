from __future__ import annotations

from pathlib import Path
from typing import Any
import sys
import types

import numpy as np


PYHFO_ARTIFACT_MODEL = "pyhfo_artifact_pruning"
PYHFO_SPIKE_MODEL = "pyhfo_spike_pruning"
DEFAULT_PYHFO_CHECKPOINT_DIR = Path(__file__).resolve().parents[2] / "checkpoints" / "pyhfo_legacy_binary"
DEFAULT_PYHFO_ARTIFACT_CHECKPOINT = DEFAULT_PYHFO_CHECKPOINT_DIR / "model_a.tar"
DEFAULT_PYHFO_SPIKE_CHECKPOINT = DEFAULT_PYHFO_CHECKPOINT_DIR / "model_s.tar"


def classify_pyhfo_pybrain_candidate_pool(
    data_uv: np.ndarray,
    fs: float,
    channel_names: list[str] | np.ndarray,
    candidates,
    *,
    checkpoint_paths: dict[str, str] | None,
    device: str = "cpu",
    feature_freq_max_hz: float | None = None,
) -> dict[str, np.ndarray | list[str] | str]:
    """
    Run the original pyHFO artifact/spike-HFO cascade on candidate events.

    This path mirrors pyHFO's GUI classifier flow: extract 2-second waveforms,
    build 224x224 time-frequency/amplitude feature tensors, crop with the
    checkpoint preprocessing settings, run artifact keep/not-keep first, then
    run spike-HFO only for kept candidates.
    """
    if not candidates:
        return {"status": "no_events"}

    paths = checkpoint_paths or {}
    artifact_path = str(paths.get(PYHFO_ARTIFACT_MODEL, "") or DEFAULT_PYHFO_ARTIFACT_CHECKPOINT)
    spike_path = str(paths.get(PYHFO_SPIKE_MODEL, "") or DEFAULT_PYHFO_SPIKE_CHECKPOINT)
    if not artifact_path or not spike_path:
        return {"status": "checkpoint_not_configured"}
    if not Path(artifact_path).exists() or not Path(spike_path).exists():
        return {"status": "checkpoint_missing"}

    try:
        import torch
        from . import model as pyhfo_model
        from .features import compute_biomarker_feature_pyhfo, extract_waveforms_pyhfo
        from .model import PreProcessing
    except ImportError as exc:
        return {"status": f"dependency_missing: {exc}"}

    _install_pyhfo_pickle_aliases(pyhfo_model)

    matrix = np.asarray(data_uv, dtype=float)
    starts = np.asarray([int(getattr(candidate, "start_sample")) for candidate in candidates], dtype=float)
    ends = np.asarray([int(getattr(candidate, "end_sample")) for candidate in candidates], dtype=float)
    event_channels = np.asarray([str(getattr(candidate, "channel")) for candidate in candidates], dtype=object)
    channel_names_arr = np.asarray([str(name) for name in channel_names], dtype=object)
    sample_freq = float(fs)
    time_range = [0, 1000]
    win_size = 224
    freq_range = [10, int(float(feature_freq_max_hz if feature_freq_max_hz is not None else sample_freq // 2))]

    waveforms = extract_waveforms_pyhfo(
        matrix,
        starts,
        ends,
        event_channels,
        channel_names_arr,
        sample_freq,
        time_range,
    )
    features = np.zeros((len(candidates), 2, win_size, win_size), dtype=float)
    for idx, candidate in enumerate(candidates):
        _channel, _start, _end, tf_img, amp_img, _raw_spectrum = compute_biomarker_feature_pyhfo(
            start=int(getattr(candidate, "start_sample")),
            end=int(getattr(candidate, "end_sample")),
            channel_name=str(getattr(candidate, "channel")),
            data=waveforms[idx],
            sample_rate=sample_freq,
            win_size=win_size,
            ps_MinFreqHz=freq_range[0],
            ps_MaxFreqHz=freq_range[1],
            time_window_ms=500,
        )
        features[idx, 0] = tf_img
        features[idx, 1] = amp_img

    artifact_ckpt = torch.load(artifact_path, map_location="cpu", weights_only=False)
    spike_ckpt = torch.load(spike_path, map_location="cpu", weights_only=False)
    artifact_model = artifact_ckpt["model"]
    spike_model = spike_ckpt["model"]
    artifact_model.channel_selection = True
    artifact_model.in_channels = 1
    artifact_model = artifact_model.to(device).float()
    spike_model = spike_model.to(device).float()
    artifact_model.eval()
    spike_model.eval()

    artifact_features = _pyhfo_preprocess_features(
        PreProcessing,
        dict(artifact_ckpt["preprocessing"]),
        features,
        sample_freq=sample_freq,
        freq_range=freq_range,
        time_range=time_range,
    )
    keep_score = _pyhfo_model_scores(artifact_model, artifact_features, device=device, batch_size=32)
    keep_binary = (keep_score > 0.5).astype(int)

    spike_score = np.zeros(len(candidates), dtype=float)
    spike_binary = np.zeros(len(candidates), dtype=int) - 1
    keep_index = np.where(keep_binary > 0)[0]
    if keep_index.size:
        spike_features = _pyhfo_preprocess_features(
            PreProcessing,
            dict(spike_ckpt["preprocessing"]),
            features,
            sample_freq=sample_freq,
            freq_range=freq_range,
            time_range=time_range,
        )[keep_index]
        kept_spike_score = _pyhfo_model_scores(spike_model, spike_features, device=device, batch_size=32)
        spike_score[keep_index] = kept_spike_score
        spike_binary[keep_index] = (kept_spike_score > 0.5).astype(int)

    labels = [_label_from_pyhfo_binary(int(keep), int(spike)) for keep, spike in zip(keep_binary, spike_binary)]
    artifact_score = 1.0 - keep_score
    hfo_score = keep_score * (1.0 - spike_score)

    return {
        "status": "ok",
        "artifact_score": artifact_score,
        "spike_score": spike_score,
        "hfo_score": hfo_score,
        "pyhfo_keep_score": keep_score,
        "pyhfo_keep_binary": keep_binary,
        "pyhfo_spike_binary": spike_binary,
        "classification_label": labels,
        "implementation": "pyhfo_pybrain",
        "classifier_feature_freq_range_hz": [10.0, float(freq_range[1])],
    }


def classify_pyhfo_omni_legacy_batch(
    waveforms_uv: np.ndarray,
    *,
    checkpoint_paths: dict[str, str] | None,
    device: str = "cpu",
) -> dict[str, np.ndarray | str]:
    """
    Run PyHFO artifact/spike classifiers when checkpoints are configured.

    Omni's PyHFO wrapper expects checkpoints containing a serialized `model` and
    `preprocessing` dictionary. The GUI integration passes in-memory waveforms
    instead of Omni feature NPZ files.
    """
    paths = checkpoint_paths or {}
    artifact_path = str(paths.get(PYHFO_ARTIFACT_MODEL, "") or DEFAULT_PYHFO_ARTIFACT_CHECKPOINT)
    spike_path = str(paths.get(PYHFO_SPIKE_MODEL, "") or DEFAULT_PYHFO_SPIKE_CHECKPOINT)
    if not artifact_path or not spike_path:
        return {"status": "checkpoint_not_configured"}
    if not Path(artifact_path).exists() or not Path(spike_path).exists():
        return {"status": "checkpoint_missing"}

    try:
        import torch
        from .features import generate_feature_from_df_gpu_batch, normalize_img
        from . import model as pyhfo_model
        from .model import PreProcessing
    except ImportError as exc:
        return {"status": f"dependency_missing: {exc}"}

    _install_pyhfo_pickle_aliases(pyhfo_model)

    feature_param: dict[str, Any] = {
        "image_size": 224,
        "freq_min_hz": 10,
        "freq_max_hz": 500,
        "resample": 1000,
        "raw_waveform_length": 2000,
        "model_additional_parameter": {
            PYHFO_ARTIFACT_MODEL: {"n_feature": 1, "time_window_ms": 1000},
            PYHFO_SPIKE_MODEL: {"n_feature": 2, "time_window_ms": 1000},
        },
    }

    model_paths = {
        PYHFO_ARTIFACT_MODEL: artifact_path,
        PYHFO_SPIKE_MODEL: spike_path,
    }
    models = {}
    preprocessings = {}
    for model_name, model_path in model_paths.items():
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        models[model_name] = ckpt["model"].to(device).float()
        models[model_name].eval()
        preprocessing_dict = dict(ckpt["preprocessing"])
        preprocessing_dict["fs"] = feature_param["resample"]
        preprocessing = PreProcessing.from_dict(preprocessing_dict)
        preprocessing.disable_random_shift()
        preprocessings[model_name] = preprocessing

    matrix = np.asarray(waveforms_uv, dtype=float)
    outputs: dict[str, list[np.ndarray]] = {
        PYHFO_ARTIFACT_MODEL: [],
        PYHFO_SPIKE_MODEL: [],
    }
    batch_size = 32
    with torch.no_grad():
        for start in range(0, int(matrix.shape[0]), batch_size):
            batch = matrix[start:start + batch_size]
            for model_name, model in models.items():
                features = generate_feature_from_df_gpu_batch(batch, feature_param, model_name, device=device)
                features = normalize_img(features)
                features = preprocessings[model_name](features)
                outputs[model_name].append(model(features).detach().cpu().numpy())

    hfo_keep_score = _flatten_batches(outputs[PYHFO_ARTIFACT_MODEL])
    raw_spike_score = _flatten_batches(outputs[PYHFO_SPIKE_MODEL])
    artifact_score = 1.0 - hfo_keep_score
    keep_binary = (hfo_keep_score > 0.5).astype(int)
    spike_binary = np.where(keep_binary > 0, (raw_spike_score > 0.5).astype(int), -1)
    spike_score = np.where(keep_binary > 0, raw_spike_score, 0.0)
    real_hfo_score = hfo_keep_score * (1.0 - spike_score)
    labels = [_label_from_pyhfo_binary(int(keep), int(spike)) for keep, spike in zip(keep_binary, spike_binary)]

    return {
        "status": "ok",
        "artifact_score": artifact_score,
        "spike_score": spike_score,
        "hfo_score": real_hfo_score,
        "pyhfo_keep_score": hfo_keep_score,
        "pyhfo_spike_raw_score": raw_spike_score,
        "pyhfo_keep_binary": keep_binary,
        "pyhfo_spike_binary": spike_binary,
        "classification_label": labels,
        "implementation": "pyhfo_omni_legacy",
        "classifier_feature_freq_range_hz": [10.0, 500.0],
    }


def _flatten_batches(batches: list[np.ndarray]) -> np.ndarray:
    if not batches:
        return np.asarray([], dtype=float)
    values = np.concatenate([np.asarray(batch, dtype=float).reshape(int(batch.shape[0]), -1) for batch in batches], axis=0)
    return values[:, 0] if values.ndim == 2 and values.shape[1] else values.reshape(-1)


def _pyhfo_preprocess_features(
    preprocessing_cls,
    preprocessing_dict: dict,
    features: np.ndarray,
    *,
    sample_freq: float,
    freq_range: list[float] | list[int],
    time_range: list[float] | list[int],
):
    preprocessing = preprocessing_cls(
        preprocessing_dict["image_size"],
        sample_freq,
        list(freq_range),
        max(time_range),
        preprocessing_dict["selected_window_size_ms"],
        preprocessing_dict["selected_freq_range_hz"],
        0,
    )
    return preprocessing(np.asarray(features, dtype=float))


def _pyhfo_model_scores(model, features: np.ndarray, *, device: str, batch_size: int) -> np.ndarray:
    import torch

    model.eval()
    x = torch.from_numpy(np.asarray(features, dtype=float)).float()
    outputs = []
    with torch.no_grad():
        for start in range(0, int(x.shape[0]), int(batch_size)):
            batch = x[start:start + int(batch_size)].to(device)
            for channel_idx in range(int(batch.shape[1])):
                batch[:, channel_idx, :, :] = _normalize_pyhfo_channel(batch[:, channel_idx, :, :])
            outputs.append(model(batch).detach().cpu().numpy())
    if not outputs:
        return np.asarray([], dtype=float)
    return np.concatenate([np.asarray(output, dtype=float).reshape(int(output.shape[0]), -1) for output in outputs], axis=0)[:, 0]


def _normalize_pyhfo_channel(channel_tensor):
    import torch

    reshaped = channel_tensor.reshape(channel_tensor.shape[0], -1)
    a_min = torch.min(reshaped, -1)[0].unsqueeze(1)
    a_max = torch.max(reshaped, -1)[0].unsqueeze(1)
    normalized = 255.0 * (reshaped - a_min) / torch.clamp(a_max - a_min, min=1e-12)
    return normalized.reshape_as(channel_tensor)


def _label_from_pyhfo_binary(artifact_keep: int, spike: int) -> str:
    if int(artifact_keep) != 1:
        return "artifact"
    if int(spike) == 1:
        return "spike-HFO"
    return "HFO"


def _install_pyhfo_pickle_aliases(pyhfo_model_module) -> None:
    src_module = sys.modules.get("src")
    if src_module is None:
        src_module = types.ModuleType("src")
        sys.modules["src"] = src_module
    setattr(src_module, "model", pyhfo_model_module)
    sys.modules["src.model"] = pyhfo_model_module
