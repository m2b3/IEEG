from __future__ import annotations

import argparse
import contextlib
import io
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import mne
import numpy as np
import pandas as pd
from scipy import signal


REPO_ROOT = Path(__file__).resolve().parents[2]
PYHFO_ROOT = REPO_ROOT / ".tmp_pyHFO_pyBrain"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PYHFO_ROOT) not in sys.path:
    sys.path.insert(0, str(PYHFO_ROOT))

from app.computation.hfo.classification.pyhfo_pybrain import classify_pyhfo_pybrain
from app.computation.hfo.detectors.omni_hfo_detector import HFOCandidate, extract_event_waveforms


DEFAULT_EDF = Path(
    r"D:\omni dataset complement\updated_dataset\bids"
    r"\sub-zurich15\ses-01\ieeg"
    r"\sub-zurich15_ses-01_run-02_ieeg.edf"
)


@dataclass
class PyHFOCandidatePool:
    data_uv: np.ndarray
    filtered_uv: np.ndarray
    fs: float
    channel_names: np.ndarray
    candidates: list[HFOCandidate]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate our PyHFO classifier wrapper on a pyHFO-style detection "
            "candidate pool from one EDF."
        )
    )
    parser.add_argument("--edf", type=Path, default=DEFAULT_EDF)
    parser.add_argument("--detector", choices=["mni", "ste", "hilbert"], default="mni")
    parser.add_argument("--low-freq-hz", type=float, default=80.0)
    parser.add_argument("--high-freq-hz", type=float, default=300.0)
    parser.add_argument("--max-candidates", type=int, default=0, help="Cap candidate pool after detection; 0 keeps all.")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/09_hfo_backend_smoke_test/pyhfo_pool_classifier_zurich15"))
    parser.add_argument("--save-feature-pool", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pool = build_pyhfo_candidate_pool(
        args.edf,
        detector_name=str(args.detector),
        low_freq_hz=float(args.low_freq_hz),
        high_freq_hz=float(args.high_freq_hz),
    )
    original_candidate_count = len(pool.candidates)
    if int(args.max_candidates) > 0 and len(pool.candidates) > int(args.max_candidates):
        pool.candidates = pool.candidates[: int(args.max_candidates)]
    pyhfo_df = classify_pool_with_pyhfo(pool, args.output_dir, save_feature_pool=bool(args.save_feature_pool))
    ours_df = classify_pool_with_our_backend(pool)
    comparison = pyhfo_df.merge(
        ours_df,
        on=["channel", "start_sample", "end_sample"],
        how="inner",
        suffixes=("_pyhfo", "_ours"),
    )
    comparison["same_label"] = comparison["label_pyhfo"] == comparison["label_ours"]

    candidates_path = args.output_dir / "pyhfo_detection_candidates.csv"
    pyhfo_path = args.output_dir / "pyhfo_classifier_labels.csv"
    ours_path = args.output_dir / "our_classifier_labels.csv"
    comparison_path = args.output_dir / "classifier_comparison.csv"

    pd.DataFrame(
        [
            {
                "channel": c.channel,
                "detector": c.detector,
                "start_sample": c.start_sample,
                "end_sample": c.end_sample,
                "start_seconds": c.start_sample / pool.fs,
                "end_seconds": c.end_sample / pool.fs,
            }
            for c in pool.candidates
        ]
    ).to_csv(candidates_path, index=False)
    pyhfo_df.to_csv(pyhfo_path, index=False)
    ours_df.to_csv(ours_path, index=False)
    comparison.to_csv(comparison_path, index=False)

    print(f"edf: {args.edf}")
    print(f"detector: {args.detector}")
    print(f"input shape: {tuple(pool.data_uv.shape)}")
    print(f"sampling frequency: {pool.fs:g} Hz")
    print(f"original pyHFO candidate count: {original_candidate_count}")
    print(f"pyHFO candidate count: {len(pool.candidates)}")
    print(f"pyHFO classifier counts: {pyhfo_df['label'].value_counts().to_dict()}")
    print(f"our classifier counts: {ours_df['label'].value_counts().to_dict()}")
    print(f"matched rows: {len(comparison)}")
    if len(comparison):
        print(f"same label count: {int(comparison['same_label'].sum())}")
        print(f"same label rate: {float(comparison['same_label'].mean()):.3f}")
    print(f"candidates csv: {candidates_path}")
    print(f"comparison csv: {comparison_path}")


def build_pyhfo_candidate_pool(
    edf_path: Path,
    *,
    detector_name: str,
    low_freq_hz: float,
    high_freq_hz: float,
) -> PyHFOCandidatePool:
    from HFODetector import hil, mni, ste
    from src.utils.utils_filter import construct_filter, filter_data
    from src.utils.utils_io import sort_channel

    raw = mne.io.read_raw_edf(edf_path, preload=False, verbose="ERROR")
    fs = float(raw.info["sfreq"])
    data_uv = raw.get_data() * 1e6
    raw_channel_names = np.asarray(raw.info["ch_names"], dtype=object)
    raw.close()
    indices, channel_names = sort_channel(raw_channel_names)
    data_uv = np.asarray(data_uv[indices], dtype=float)

    sos = construct_filter(
        int(round(low_freq_hz)),
        int(round(high_freq_hz)),
        0.5,
        93,
        0.5,
        int(round(fs)),
    )
    filtered = np.asarray([filter_data(row, sos) for row in data_uv], dtype=float)

    if detector_name == "ste":
        detector = ste.STEDetector(
            sample_freq=fs,
            filter_freq=[low_freq_hz, high_freq_hz],
            rms_window=3e-3,
            min_window=6e-3,
            min_gap=10e-3,
            epoch_len=600,
            min_osc=6,
            rms_thres=5,
            peak_thres=3,
            n_jobs=1,
            front_num=1,
        )
    elif detector_name == "mni":
        detector = mni.MNIDetector(
            sample_freq=fs,
            filter_freq=[int(round(low_freq_hz)), int(round(high_freq_hz))],
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
            seed=0,
        )
    else:
        detector = hil.HILDetector(
            sample_freq=fs,
            filter_freq=[low_freq_hz, high_freq_hz],
            sd_thres=5,
            min_window=10e-3,
            epoch_len=3600,
            n_jobs=1,
            front_num=1,
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            event_channel_names, hfos = detector.detect_multi_channels(filtered, channel_names, filtered=True)

    candidates: list[HFOCandidate] = []
    for channel_name, intervals in zip(event_channel_names, hfos):
        arr = np.asarray(intervals, dtype=float)
        if arr.size == 0:
            continue
        arr = np.atleast_2d(arr)
        for start, end in arr[:, :2]:
            if int(round(float(end))) - int(round(float(start))) >= int(round(fs)):
                continue
            candidates.append(
                HFOCandidate(
                    channel=str(channel_name),
                    detector=str(detector_name),
                    start_sample=int(round(float(start))),
                    end_sample=int(round(float(end))),
                )
            )
    candidates.sort(key=lambda item: (item.channel.casefold(), item.start_sample, item.end_sample))
    return PyHFOCandidatePool(data_uv=data_uv, filtered_uv=filtered, fs=fs, channel_names=channel_names, candidates=candidates)


def classify_pool_with_pyhfo(pool: PyHFOCandidatePool, output_dir: Path, *, save_feature_pool: bool = False) -> pd.DataFrame:
    import torch
    from src.hfo_feature import HFO_Feature
    from src.model import PreProcessing
    from src.utils.utils_feature import compute_biomarker_feature, extract_waveforms
    from src.utils.utils_inference import inference, load_ckpt

    if not pool.candidates:
        return pd.DataFrame(columns=["channel", "start_sample", "end_sample", "pyhfo_keep_score", "artifact_pred", "spike_pred", "label"])

    starts = np.asarray([c.start_sample for c in pool.candidates], dtype=float)
    ends = np.asarray([c.end_sample for c in pool.candidates], dtype=float)
    event_channels = np.asarray([c.channel for c in pool.candidates], dtype=object)
    time_range = [0, 1000]
    win_size = 224
    freq_range = [10, int(pool.fs // 4)]
    waveforms = extract_waveforms(pool.data_uv, starts, ends, event_channels, pool.channel_names, pool.fs, time_range)

    features = np.zeros((len(pool.candidates), 2, win_size, win_size), dtype=float)
    for idx, candidate in enumerate(pool.candidates):
        _channel, _start, _end, tf_img, amp_img, _raw_spectrum = compute_biomarker_feature(
            start=candidate.start_sample,
            end=candidate.end_sample,
            channel_name=candidate.channel,
            data=waveforms[idx],
            sample_rate=pool.fs,
            win_size=win_size,
            ps_MinFreqHz=freq_range[0],
            ps_MaxFreqHz=freq_range[1],
            time_window_ms=500,
        )
        features[idx, 0] = tf_img
        features[idx, 1] = amp_img

    feature_obj = HFO_Feature(
        event_channels,
        np.asarray([starts, ends]).T,
        features,
        HFO_type=pool.candidates[0].detector,
        sample_freq=pool.fs,
        freq_range=freq_range,
        time_range=time_range,
        feature_size=win_size,
    )

    device = "cpu"
    artifact_param, artifact_model = load_ckpt(torch.load, str(REPO_ROOT / "app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_a.tar"))
    spike_param, spike_model = load_ckpt(torch.load, str(REPO_ROOT / "app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_s.tar"))
    artifact_model.channel_selection = True
    artifact_model.in_channels = 1
    artifact_model = artifact_model.to(device).float()
    spike_model = spike_model.to(device).float()
    preprocessing_artifact = PreProcessing.from_param(artifact_param)
    preprocessing_spike = PreProcessing.from_param(spike_param)

    artifact_features = preprocessing_artifact.process_biomarker_feature(feature_obj)
    artifact_keep = np.atleast_1d(
        inference(artifact_model, artifact_features, device, batch_size=32, threshold=0.5)
    ).astype(int)
    feature_obj.update_artifact_pred(artifact_keep)

    spike_pred = np.zeros(features.shape[0], dtype=int) - 1
    keep_index = np.where(feature_obj.artifact_predictions > 0)[0]
    if keep_index.size:
        spike_features = preprocessing_spike.process_biomarker_feature(feature_obj)[keep_index]
        spike_pred[keep_index] = np.atleast_1d(
            inference(spike_model, spike_features, device, batch_size=32, threshold=0.5)
        ).astype(int)
    feature_obj.update_spike_pred(spike_pred)

    if save_feature_pool:
        np.savez_compressed(
            output_dir / "pyhfo_feature_pool.npz",
            features=features,
            starts=starts,
            ends=ends,
            channels=event_channels,
            artifact_keep=artifact_keep,
            spike_pred=spike_pred,
        )

    return pd.DataFrame(
        {
            "channel": event_channels,
            "start_sample": starts.astype(int),
            "end_sample": ends.astype(int),
            "artifact_pred": artifact_keep,
            "spike_pred": spike_pred,
            "label": [_label_from_pyhfo_binary(int(a), int(s)) for a, s in zip(artifact_keep, spike_pred)],
        }
    )


def classify_pool_with_our_backend(pool: PyHFOCandidatePool) -> pd.DataFrame:
    if not pool.candidates:
        return pd.DataFrame(columns=["channel", "start_sample", "end_sample", "artifact_score", "hfo_score", "spike_score", "label"])

    result = classify_pyhfo_pybrain(
        pool.data_uv,
        float(pool.fs),
        [str(name) for name in pool.channel_names],
        pool.candidates,
        checkpoint_paths={},
        device="cpu",
        feature_freq_max_hz=int(pool.fs // 4),
    )
    artifact = np.asarray(result["artifact_score"], dtype=float)
    hfo = np.asarray(result["hfo_score"], dtype=float)
    spike = np.asarray(result["spike_score"], dtype=float)
    labels = [str(label) for label in result["classification_label"]]
    return pd.DataFrame(
        {
            "channel": [c.channel for c in pool.candidates],
            "start_sample": [c.start_sample for c in pool.candidates],
            "end_sample": [c.end_sample for c in pool.candidates],
            "artifact_score": artifact,
            "hfo_score": hfo,
            "spike_score": spike,
            "label": labels,
        }
    )


def _resample(data: np.ndarray, original_fs: float, target_fs: float) -> np.ndarray:
    if abs(float(original_fs) - float(target_fs)) < 1e-9:
        return np.asarray(data, dtype=float)
    from fractions import Fraction

    ratio = Fraction(float(target_fs) / float(original_fs)).limit_denominator(1000)
    return signal.resample_poly(np.asarray(data, dtype=float), ratio.numerator, ratio.denominator, axis=1)


def _label_from_pyhfo_binary(artifact_keep: int, spike: int) -> str:
    if int(artifact_keep) != 1:
        return "artifact"
    if int(spike) == 1:
        return "spike-HFO"
    return "HFO"


def _label_from_scores(artifact_score: float, hfo_score: float, spike_score: float) -> str:
    if artifact_score >= 0.5:
        return "artifact"
    if spike_score >= 0.5:
        return "spike-HFO"
    if hfo_score >= 0.5:
        return "HFO"
    return "candidate"


if __name__ == "__main__":
    main()
