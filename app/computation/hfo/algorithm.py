from __future__ import annotations

from fractions import Fraction
from typing import Any

import numpy as np
from scipy import signal

from app.computation.hfo.classification.ehfo import classify_ehfo
from app.computation.hfo.classification.pyhfo_omni_legacy import classify_pyhfo_omni_legacy
from app.computation.hfo.classification.pyhfo_pybrain import classify_pyhfo_pybrain
from app.computation.hfo.detectors.omni_hfo_detector import (
    DEFAULT_CANDIDATE_DETECTORS,
    OMNI_1000HZ_UPPER_FREQ_HZ,
    OMNI_EVENT_WINDOW_MS,
    OMNI_TARGET_FS_HZ,
    detect_candidates_from_array,
    extract_event_waveforms,
)
from app.computation.hfo.preprocessing.omni import prepare_hfo_input_from_array
from app.computation.hfo.preprocessing.pybrain import (
    apply_pybrain_bandpass,
    prepare_pybrain_hfo_input_from_array,
)
from app.computation.hfo.types import HFOChannelResult, HFOComputationResult, HFOEventResult


HFO_CLASSIFIER_PYHFO_OMNI_LEGACY = "pyhfo_omni_legacy"
HFO_CLASSIFIER_PYHFO_PYBRAIN = "pyhfo_pybrain"
HFO_CLASSIFIER_EHFO = "eHFO"

OMNI_IEEG_REPO_URL = "https://github.com/Omni-iEEG/Omni-iEEG/tree/master/omni_ieeg"
PYHFO_PYBRAIN_REPO_URL = "https://github.com/roychowdhuryresearch/pyHFO/tree/pyBrain"
PYHFO_REPO_URL = "https://github.com/roychowdhuryresearch/pyHFO"


def compute_hfo_for_gui(
    *,
    data: np.ndarray,
    fs: float,
    channel_names: list[str],
    data_start_s: float,
    analysis_window_s: tuple[float, float],
    detector_version: str,
    active_candidate_detectors: list[str] | tuple[str, ...] | None,
    band_label: str,
    low_freq_hz: float,
    high_freq_hz: float,
    threshold_sigma: float = 5.0,
    min_duration_ms: float = 6.0,
    max_duration_ms: float | None = 500.0,
    boundary_padding_s: float = 1.0,
    merge_gap_ms: float = 10.0,
    min_cycles: float = 6.0,
    detector_parameters: dict | None = None,
    notch_modes_by_channel: dict[str, str] | None = None,
    selected_channel_names: list[str] | tuple[str, ...] | None = None,
    bad_channels: list[str] | tuple[str, ...] | set[str] | None = None,
    reference_mode: str = "none",
    bipolar_pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
    data_is_interval_sliced: bool = True,
    checkpoint_paths: dict[str, str] | None = None,
    device: str = "cpu",
    metadata: dict | None = None,
) -> HFOComputationResult:
    classifier_name = str(detector_version)
    use_pybrain_pipeline = classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN
    preparation = (
        prepare_pybrain_hfo_input_from_array
        if use_pybrain_pipeline
        else prepare_hfo_input_from_array
    )
    prepared = preparation(
        data_uv=np.asarray(data, dtype=float),
        fs=float(fs),
        channel_names=[str(name) for name in channel_names],
        data_start_s=float(data_start_s),
        analysis_window_s=analysis_window_s,
        data_is_interval_sliced=bool(data_is_interval_sliced),
        selected_channels=selected_channel_names,
        bad_channels=bad_channels,
        reference_mode=str(reference_mode),
        bipolar_pairs=bipolar_pairs,
        notch_modes_by_channel=notch_modes_by_channel,
    )
    matrix = np.asarray(prepared.data_uv, dtype=float)
    channel_names = list(prepared.channel_names)
    fs = float(prepared.fs)
    data_start_s = float(prepared.data_start_s)
    analysis_window_s = prepared.analysis_window_s
    if not use_pybrain_pipeline and fs < OMNI_TARGET_FS_HZ:
        raise ValueError("HFO detection requires recordings sampled at 1000 Hz or higher.")

    start_s, stop_s = map(float, analysis_window_s)
    active = tuple(active_candidate_detectors or DEFAULT_CANDIDATE_DETECTORS)
    if not active:
        raise ValueError("Select at least one HFO candidate detector.")

    processing_order = list(prepared.processing_order)
    if use_pybrain_pipeline:
        nyquist = float(fs) / 2.0
        effective_high_freq_hz = min(float(high_freq_hz), nyquist * 0.99)
        if float(low_freq_hz) >= effective_high_freq_hz:
            raise ValueError(
                "pyhfo_pybrain low frequency must stay below the native "
                f"Nyquist limit ({nyquist:g} Hz)."
            )
        detection_data = apply_pybrain_bandpass(
            matrix,
            float(fs),
            pass_band_hz=float(low_freq_hz),
            stop_band_hz=float(effective_high_freq_hz),
        )
        detection_fs = float(fs)
        sample_scale = 1.0
        processing_order.append("pyBrain Chebyshev-II HFO bandpass")
        processing_order.append("preserve native sampling")
        classifier_feature_freq_max_hz = min(500.0, nyquist)
        detector_assume_filtered = True
        detector_mni_seed = 0
    else:
        detection_data, detection_fs = _resample_to_omni_target(matrix, float(fs))
        sample_scale = float(detection_fs) / float(fs)
        processing_order.append(
            "resampling to 1000 Hz"
            if float(detection_fs) != float(fs)
            else "keep native 1000 Hz sampling"
        )
        effective_high_freq_hz = min(float(high_freq_hz), OMNI_1000HZ_UPPER_FREQ_HZ)
        if float(low_freq_hz) >= effective_high_freq_hz:
            raise ValueError(
                "HFO low frequency must stay below the effective detection Nyquist limit "
                f"after 1000 Hz resampling ({effective_high_freq_hz:g} Hz)."
            )
        classifier_feature_freq_max_hz = 500.0
        detector_assume_filtered = False
        detector_mni_seed = None
    processing_order.append("candidate detection")
    processing_order.append("candidate waveform extraction")
    processing_order.append("classification")

    raw_candidates = detect_candidates_from_array(
        detection_data,
        float(detection_fs),
        [str(name) for name in channel_names],
        active_detectors=active,
        low_freq_hz=float(low_freq_hz),
        high_freq_hz=float(effective_high_freq_hz),
        threshold_sigma=float(threshold_sigma),
        min_duration_ms=float(min_duration_ms),
        merge_gap_ms=float(merge_gap_ms),
        min_cycles=float(min_cycles),
        mni_seed=detector_mni_seed,
        detector_parameters=detector_parameters,
        assume_filtered=detector_assume_filtered,
    )
    if max_duration_ms is None:
        max_duration_s: float | None = None
    else:
        max_duration_s = max(0.0, float(max_duration_ms)) / 1000.0
    if max_duration_s is not None and max_duration_s > 0.0:
        candidates = [
            candidate
            for candidate in raw_candidates
            if (
                float(candidate.end_sample) - float(candidate.start_sample)
            ) / float(detection_fs) <= max_duration_s
        ]
    else:
        candidates = list(raw_candidates)
    duration_excluded_count = int(len(raw_candidates) - len(candidates))
    duration_filtered_candidates = list(candidates)
    boundary_padding_samples = int(round(max(0.0, float(boundary_padding_s)) * float(detection_fs)))
    if boundary_padding_samples > 0:
        n_detection_samples = int(detection_data.shape[1])
        lower_bound = int(boundary_padding_samples)
        upper_bound = max(lower_bound, n_detection_samples - int(boundary_padding_samples))
        candidates = [
            candidate
            for candidate in duration_filtered_candidates
            if int(candidate.start_sample) >= lower_bound and int(candidate.end_sample) <= upper_bound
        ]
    boundary_excluded_count = int(len(duration_filtered_candidates) - len(candidates))
    window_samples = int(round(OMNI_EVENT_WINDOW_MS / 1000.0 * float(detection_fs)))
    waveforms, real_starts, real_ends, is_boundaries = extract_event_waveforms(
        detection_data,
        candidates,
        [str(name) for name in channel_names],
        window_samples=window_samples,
    )
    classification = _classify(
        detector_version=str(detector_version),
        waveforms_uv=waveforms,
        data_uv=matrix if use_pybrain_pipeline else detection_data,
        fs=float(detection_fs),
        channel_names=[str(name) for name in channel_names],
        candidates=candidates,
        checkpoint_paths=checkpoint_paths,
        device=str(device),
        feature_freq_max_hz=float(classifier_feature_freq_max_hz),
    )
    _validate_classification_output(
        detector_version=str(detector_version),
        classification=classification,
        candidate_count=len(candidates),
    )

    events: list[HFOEventResult] = []
    artifact_scores = _score_array(classification.get("artifact_score"))
    spike_scores = _score_array(classification.get("spike_score"))
    hfo_scores = _score_array(classification.get("hfo_score"))
    real_hfo_scores = _score_array(classification.get("pyhfo_keep_score"))
    classification_labels = _label_array(classification.get("classification_label"))
    for idx, candidate in enumerate(candidates):
        start_time_s = float(data_start_s) + float(candidate.start_sample) / float(detection_fs)
        end_time_s = float(data_start_s) + float(candidate.end_sample) / float(detection_fs)
        peak_time_s = 0.5 * (start_time_s + end_time_s)
        artifact_score = _score_at(artifact_scores, idx)
        spike_score = _score_at(spike_scores, idx)
        hfo_score = _score_at(hfo_scores, idx)
        real_hfo_probability = _score_at(real_hfo_scores, idx)
        if real_hfo_probability is None and artifact_score is not None:
            real_hfo_probability = 1.0 - float(artifact_score)
        artifact_probability = (
            1.0 - float(real_hfo_probability)
            if real_hfo_probability is not None
            else artifact_score
        )
        spike_hfo_probability = spike_score
        classification_label = (
            classification_labels[idx]
            if classification_labels is not None and idx < len(classification_labels)
            else _classification_label(artifact_score, spike_score, hfo_score)
        )
        boundary_warning = bool(is_boundaries[idx] if idx < len(is_boundaries) else False)
        events.append(
            HFOEventResult(
                event_id=f"hfo_{idx + 1:06d}",
                channel=str(candidate.channel),
                detector=str(candidate.detector),
                start_sample=int(round(float(candidate.start_sample) / sample_scale)),
                end_sample=int(round(float(candidate.end_sample) / sample_scale)),
                start_time_s=start_time_s,
                end_time_s=end_time_s,
                peak_time_s=peak_time_s,
                duration_ms=max(0.0, (end_time_s - start_time_s) * 1000.0),
                band_label=str(band_label),
                low_freq_hz=float(low_freq_hz),
                high_freq_hz=float(high_freq_hz),
                waveform=waveforms[idx] if idx < int(waveforms.shape[0]) else None,
                real_start_sample=real_starts[idx] if idx < len(real_starts) else None,
                real_end_sample=real_ends[idx] if idx < len(real_ends) else None,
                is_boundary=boundary_warning,
                boundary_warning=boundary_warning,
                real_hfo_probability=real_hfo_probability,
                artifact_probability=artifact_probability,
                spike_hfo_probability=spike_hfo_probability,
                final_model_class=classification_label,
                manual_class=None,
                manual_review_status="unreviewed",
                artifact_score=artifact_probability,
                spike_score=spike_hfo_probability,
                hfo_score=hfo_score,
                classification_label=classification_label,
            )
        )

    channel_results: list[HFOChannelResult] = []
    for channel_name in channel_names:
        channel_events = [event for event in events if str(event.channel) == str(channel_name)]
        channel_results.append(
            HFOChannelResult(
                channel=str(channel_name),
                event_count=len(channel_events),
                events=channel_events,
            )
        )

    result_metadata = dict(metadata or {})
    active_notch_modes = sorted({mode for mode in prepared.notch_modes_by_channel.values() if mode != "Off"})
    classification_status = str(classification.get("status", "unknown"))
    classification_label_counts = _classification_label_counts(events)
    classification_probability_counts = {
        "real_hfo_probability": sum(event.real_hfo_probability is not None for event in events),
        "artifact_probability": sum(event.artifact_probability is not None for event in events),
        "spike_hfo_probability": sum(event.spike_hfo_probability is not None for event in events),
    }
    result_metadata.update(
        {
            "algorithm": "HFO",
            "detector_version": str(detector_version),
            "algorithm_details": _hfo_algorithm_details(str(detector_version)),
            "source_repositories": _hfo_source_repositories(str(detector_version)),
            "active_candidate_detectors": list(map(str, active)),
            "analysis_window_s": [float(start_s), float(stop_s)],
            "data_start_s": float(data_start_s),
            "input_fs": float(fs),
            "detection_fs": float(detection_fs),
            "resampled_to_hz": float(detection_fs) if float(detection_fs) != float(fs) else None,
            "input_units": "microvolts",
            "n_channels": len(channel_names),
            "n_samples": int(matrix.shape[1]),
            "total_events": len(events),
            "band_label": str(band_label),
            "low_freq_hz": float(low_freq_hz),
            "high_freq_hz": float(high_freq_hz),
            "effective_high_freq_hz": float(effective_high_freq_hz),
            "threshold_sigma": float(threshold_sigma),
            "min_duration_ms": float(min_duration_ms),
            "max_duration_ms": (
                float(max_duration_ms)
                if max_duration_ms is not None and float(max_duration_ms) > 0.0
                else None
            ),
            "raw_candidate_events": int(len(raw_candidates)),
            "duration_excluded_events": int(duration_excluded_count),
            "boundary_padding_s": float(max(0.0, float(boundary_padding_s))),
            "boundary_excluded_events": int(boundary_excluded_count),
            "merge_gap_ms": float(merge_gap_ms),
            "min_cycles": float(min_cycles),
            "detector_parameters": detector_parameters or {},
            "mni_seed": detector_mni_seed,
            "event_window_ms": float(OMNI_EVENT_WINDOW_MS),
            "notch_behavior": "GUI notch modes",
            "notch_filter": bool(active_notch_modes),
            "notch_modes": active_notch_modes,
            "notch_modes_by_channel": dict(prepared.notch_modes_by_channel),
            "input_boundary": str(prepared.input_boundary),
            "selected_channels": list(prepared.selected_channels),
            "bad_channels_excluded": list(prepared.bad_channels),
            "reference_mode": str(prepared.reference_mode),
            "bipolar_pairs": [list(pair) for pair in prepared.bipolar_pairs],
            "processing_order": processing_order,
            "preprocessing_log": list(prepared.preprocessing_log),
            "classification_status": classification_status,
            "classifier_implementation": classification.get("implementation"),
            "classifier_origin": _hfo_classifier_origin(str(detector_version)),
            "classifier_feature_freq_range_hz": classification.get("classifier_feature_freq_range_hz"),
            "classification_label_counts": classification_label_counts,
            "classification_probability_counts": classification_probability_counts,
        }
    )
    return HFOComputationResult(channels=channel_results, events=events, metadata=result_metadata)


def _resample_to_omni_target(data: np.ndarray, fs: float) -> tuple[np.ndarray, float]:
    if abs(float(fs) - OMNI_TARGET_FS_HZ) < 1e-9:
        return np.asarray(data, dtype=float), OMNI_TARGET_FS_HZ
    ratio = Fraction(OMNI_TARGET_FS_HZ / float(fs)).limit_denominator(1000)
    resampled = signal.resample_poly(np.asarray(data, dtype=float), ratio.numerator, ratio.denominator, axis=1)
    return np.asarray(resampled, dtype=float), OMNI_TARGET_FS_HZ


def _classify(
    *,
    detector_version: str,
    waveforms_uv: np.ndarray,
    data_uv: np.ndarray,
    fs: float,
    channel_names: list[str],
    candidates: list,
    checkpoint_paths: dict[str, str] | None,
    device: str,
    feature_freq_max_hz: float | None = None,
) -> dict[str, Any]:
    if waveforms_uv.size == 0:
        return {"status": "no_events"}
    classifier_name = str(detector_version)
    if classifier_name == HFO_CLASSIFIER_EHFO:
        return classify_ehfo(waveforms_uv, checkpoint_paths=checkpoint_paths, device=device)
    if classifier_name == HFO_CLASSIFIER_PYHFO_OMNI_LEGACY:
        return classify_pyhfo_omni_legacy(
            waveforms_uv,
            checkpoint_paths=checkpoint_paths,
            device=device,
        )
    if classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN:
        return classify_pyhfo_pybrain(
            data_uv,
            fs,
            channel_names,
            candidates,
            checkpoint_paths=checkpoint_paths,
            device=device,
            feature_freq_max_hz=feature_freq_max_hz,
        )
    return {"status": "unsupported_detector_version"}


def _validate_classification_output(
    *,
    detector_version: str,
    classification: dict[str, Any],
    candidate_count: int,
) -> None:
    if int(candidate_count) <= 0:
        return

    classifier_name = str(detector_version)
    if not (
        classifier_name == HFO_CLASSIFIER_PYHFO_OMNI_LEGACY
        or classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN
        or classifier_name == HFO_CLASSIFIER_EHFO
    ):
        return

    status = str(classification.get("status", "unknown"))
    if status != "ok":
        raise RuntimeError(
            f"{classifier_name} classification failed "
            f"(status={status}, candidates={int(candidate_count)})."
        )

    labels = _label_array(classification.get("classification_label"))
    keep_scores = _score_array(classification.get("pyhfo_keep_score"))
    artifact_scores = _score_array(classification.get("artifact_score"))
    spike_scores = _score_array(classification.get("spike_score"))
    hfo_scores = _score_array(classification.get("hfo_score"))
    missing: list[str] = []
    if labels is None or len(labels) < int(candidate_count):
        missing.append("classification_label")
    if keep_scores is None or int(keep_scores.size) < int(candidate_count):
        missing.append("pyhfo_keep_score")
    if artifact_scores is None or int(artifact_scores.size) < int(candidate_count):
        missing.append("artifact_score")
    if spike_scores is None or int(spike_scores.size) < int(candidate_count):
        missing.append("spike_score")
    if classifier_name == HFO_CLASSIFIER_EHFO and (
        hfo_scores is None or int(hfo_scores.size) < int(candidate_count)
    ):
        missing.append("hfo_score")
    if missing:
        raise RuntimeError(
            f"{classifier_name} classification returned incomplete output "
            f"({', '.join(missing)}; candidates={int(candidate_count)})."
        )


def _score_array(value: Any) -> np.ndarray | None:
    if value is None or isinstance(value, str):
        return None
    arr = np.asarray(value, dtype=float).reshape(-1)
    return arr if arr.size else None


def _score_at(values: np.ndarray | None, idx: int) -> float | None:
    if values is None or idx >= values.size:
        return None
    score = float(values[int(idx)])
    return score if np.isfinite(score) else None


def _label_array(value: Any) -> list[str] | None:
    if value is None or isinstance(value, str):
        return None
    return [str(item) for item in value]


def _classification_label_counts(events: list[HFOEventResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        label = str(event.final_model_class or event.classification_label or "unclassified").strip()
        if not label:
            label = "unclassified"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _hfo_source_repositories(detector_version: str) -> list[dict[str, str]]:
    classifier_name = str(detector_version)
    repositories = [
        {
            "name": "Omni-iEEG",
            "url": OMNI_IEEG_REPO_URL,
            "used_for": "Omni HFO/eHFO event-model code, Omni-compatible preprocessing route, and eHFO reference validation.",
        },
        {
            "name": "pyHFO",
            "url": PYHFO_REPO_URL,
            "used_for": "Original pyHFO detector and classifier family context.",
        },
    ]
    if classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN:
        repositories.append(
            {
                "name": "pyHFO pyBrain branch",
                "url": PYHFO_PYBRAIN_REPO_URL,
                "used_for": "Default pyBrain-compatible preprocessing, detector/filter behavior, and binary pyHFO classifier validation target.",
            }
        )
    return repositories


def _hfo_classifier_origin(detector_version: str) -> dict[str, Any]:
    classifier_name = str(detector_version)
    if classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN:
        return {
            "name": "pyHFO pyBrain binary classifier",
            "reference_repository": PYHFO_PYBRAIN_REPO_URL,
            "models": ["model_a.tar", "model_s.tar"],
            "classes": ["artifact", "HFO", "spike-HFO"],
        }
    if classifier_name == HFO_CLASSIFIER_PYHFO_OMNI_LEGACY:
        return {
            "name": "Omni legacy pyHFO binary classifier",
            "reference_repository": OMNI_IEEG_REPO_URL,
            "models": ["model_a.tar", "model_s.tar"],
            "classes": ["artifact", "HFO", "spike-HFO"],
        }
    if classifier_name == HFO_CLASSIFIER_EHFO:
        return {
            "name": "Omni eHFO three-model classifier",
            "reference_repository": OMNI_IEEG_REPO_URL,
            "models": ["artifacts.pth", "spikes.pth", "eHFOs.pth"],
            "classes": ["artifact", "HFO", "spike-HFO", "eHFO", "spike-eHFO"],
        }
    return {
        "name": classifier_name,
        "reference_repository": "",
        "models": [],
        "classes": [],
    }


def _hfo_algorithm_details(detector_version: str) -> dict[str, Any]:
    classifier_name = str(detector_version)
    common = {
        "pipeline": [
            "GUI/file layer prepares selected channel x sample signal in microvolts",
            "HFO backend excludes bad channels",
            "HFO backend applies the GUI-selected notch once",
            "selected route preprocessing",
            "candidate detection with STE, MNI, and/or Hilbert",
            "2-second waveform extraction around candidate events",
            "selected classifier inference",
            "event-level labels, probabilities, review fields, summaries, and export",
        ],
        "candidate_detector_origin": {
            "name": "Omni/pyHFO-style STE, MNI, and Hilbert candidate detectors",
            "reference_repositories": [OMNI_IEEG_REPO_URL, PYHFO_REPO_URL],
            "role": "Candidate detectors find suspicious high-frequency events; they do not decide the final model class.",
        },
        "manual_review_rule": {
            "final_model_class": "Immutable classifier proposition.",
            "manual_class": "Reviewer correction, if any.",
            "official_class": "manual_class after review; otherwise final_model_class.",
        },
    }
    if classifier_name == HFO_CLASSIFIER_PYHFO_PYBRAIN:
        common.update(
            {
                "selected_route": "pyhfo_pybrain",
                "route_origin": "Original pyHFO/pyBrain GUI-compatible route.",
                "reference_repository": PYHFO_PYBRAIN_REPO_URL,
                "preprocessing": "Preserves native EDF sampling, applies pyBrain-style Chebyshev-II HFO bandpass before candidate detection, and reconstructs classifier features from the native signal plus candidate coordinates.",
                "default_detector_band_hz": [80.0, 500.0],
                "classifier": "Gated binary pyHFO classifier: Model A accepts/rejects real HFO; Model S classifies accepted events as spike-HFO or HFO.",
                "checkpoint_directory": "app/computation/hfo/checkpoints/pyhfo_legacy_binary",
            }
        )
    elif classifier_name == HFO_CLASSIFIER_PYHFO_OMNI_LEGACY:
        common.update(
            {
                "selected_route": "pyhfo_omni_legacy",
                "route_origin": "Omni legacy pyHFO event-model route.",
                "reference_repository": OMNI_IEEG_REPO_URL,
                "preprocessing": "Rejects input below 1000 Hz, resamples higher-rate input to 1000 Hz, and uses the Omni-compatible 80-300 Hz detection route.",
                "default_detector_band_hz": [80.0, 300.0],
                "classifier": "Gated binary pyHFO classifier: Model A accepts/rejects real HFO; Model S classifies accepted events as spike-HFO or HFO.",
                "checkpoint_directory": "app/computation/hfo/checkpoints/pyhfo_legacy_binary",
            }
        )
    elif classifier_name == HFO_CLASSIFIER_EHFO:
        common.update(
            {
                "selected_route": "eHFO",
                "route_origin": "Omni eHFO event-model route.",
                "reference_repository": OMNI_IEEG_REPO_URL,
                "preprocessing": "Uses the Omni-compatible route: reject input below 1000 Hz, resample higher-rate input to 1000 Hz, extract 2-second waveforms, and build Omni eHFO image features.",
                "default_detector_band_hz": [80.0, 300.0],
                "classifier": "Three binary neural-network outputs are combined: artifact/keep, spike association, and eHFO positivity.",
                "class_mapping": {
                    "not_kept": "artifact",
                    "kept_no_spike_no_ehfo": "HFO",
                    "kept_spike_no_ehfo": "spike-HFO",
                    "kept_no_spike_ehfo": "eHFO",
                    "kept_spike_ehfo": "spike-eHFO",
                },
                "checkpoint_directory": "app/computation/hfo/checkpoints/ehfo",
            }
        )
    else:
        common.update(
            {
                "selected_route": classifier_name,
                "route_origin": "Unsupported or unknown HFO classifier route.",
                "reference_repository": "",
            }
        )
    return common


def _classification_label(
    artifact_score: float | None,
    spike_score: float | None,
    hfo_score: float | None,
) -> str | None:
    if artifact_score is None and spike_score is None and hfo_score is None:
        return None
    if artifact_score is not None and artifact_score >= 0.5:
        return "artifact"
    if spike_score is not None and spike_score >= 0.5:
        return "spike-HFO"
    if hfo_score is not None and hfo_score >= 0.5:
        return "HFO"
    return "candidate"
