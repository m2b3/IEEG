from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import torch
from numpy import linalg as LA
from skimage.transform import resize


def normalize_img_ehfo(a):
    batch_num = int(a.shape[0])
    h = int(a.shape[1])
    w = int(a.shape[2])
    reshaped = a.reshape(batch_num, -1)
    a_min = torch.min(reshaped, -1)[0].unsqueeze(1)
    a_max = torch.max(reshaped, -1)[0].unsqueeze(1)
    denom = torch.clamp(a_max - a_min, min=1e-12)
    c = 255.0 * (reshaped - a_min) / denom
    return c.reshape(batch_num, h, w)


@dataclass
class EHFOPreProcessing:
    image_size: int
    freq_range_hz: list[float]
    time_range_ms: list[float]
    fs: float
    selected_window_size_ms: float
    selected_freq_range_hz: list[float]
    random_shift_ms: float

    @staticmethod
    def from_spec(spec: dict) -> "EHFOPreProcessing":
        preprocessing = dict(spec.get("preprocessing", {}) or {})
        return EHFOPreProcessing(
            image_size=int(preprocessing.get("image_size", 224)),
            freq_range_hz=[float(v) for v in preprocessing.get("freq_range_hz", [10, 500])],
            time_range_ms=[float(v) for v in preprocessing.get("time_range_ms", [0, 1000])],
            fs=float(preprocessing.get("fs", 2000)),
            selected_window_size_ms=float(preprocessing.get("selected_window_size_ms", 500)),
            selected_freq_range_hz=[float(v) for v in preprocessing.get("selected_freq_range_hz", [10, 500])],
            random_shift_ms=0.0,
        )

    def process(self, features: np.ndarray, *, feature_sample_freq: float) -> np.ndarray:
        data = np.asarray(features, dtype=np.float32)
        freq_low, freq_high = map(float, self.freq_range_hz)
        crop_low, crop_high = map(float, self.selected_freq_range_hz)
        event_length_ms = max(float(v) for v in self.time_range_ms)
        if feature_sample_freq > 0:
            event_length_ms = max(event_length_ms, 1000.0 * float(data.shape[-1]) / float(feature_sample_freq))

        crop_half_width = float(self.selected_window_size_ms) / float(event_length_ms) * float(self.image_size)
        freq_index_low = self.image_size - self.image_size / (freq_high - freq_low) * (crop_low - freq_low)
        freq_index_high = self.image_size - self.image_size / (freq_high - freq_low) * (crop_high - freq_low)
        freq_idx = np.array([freq_index_high, freq_index_low]).astype(int)
        time_offsets = np.array([-crop_half_width, crop_half_width]).astype(int)
        time_idx = self.image_size // 2 + time_offsets
        if (
            freq_idx[0] < 0
            or freq_idx[1] > self.image_size
            or time_idx[0] < 0
            or time_idx[1] > self.image_size
            or freq_idx[0] >= freq_idx[1]
            or time_idx[0] >= time_idx[1]
        ):
            raise ValueError("eHFO preprocessing crop is outside the feature image.")
        return data[:, :, freq_idx[0]:freq_idx[1], time_idx[0]:time_idx[1]]


def generate_feature_from_df_gpu_batch(hfo_waveforms, feature_param, device="cpu", n_jobs=1):
    del device, n_jobs
    sampling_rate = int(feature_param["resample"])
    expected_samples = int(feature_param["raw_waveform_length"] * sampling_rate / 1000)
    hfo_waveforms = np.asarray(hfo_waveforms, dtype=float)
    if int(hfo_waveforms.shape[1]) != expected_samples:
        raise ValueError(f"eHFO expects {expected_samples} waveform samples.")

    total_samples = int(hfo_waveforms.shape[1])
    middle_idx = total_samples // 2
    if total_samples < 2 * sampling_rate:
        raise ValueError("Waveform length must be at least 2 seconds for eHFO classification.")
    start_05s = int(middle_idx - sampling_rate / 2)
    end_05s = int(middle_idx + sampling_rate / 2)
    data_slice_features = np.asarray(hfo_waveforms[:, start_05s:end_05s], dtype=float)

    spectrum_imgs = []
    amplitude_imgs = []
    left_index = int(0.5 * sampling_rate)
    right_index = int(0.5 * sampling_rate)
    for waveform, feature_slice in zip(hfo_waveforms, data_slice_features):
        raw_spectrum = compute_spectrum_ehfo(waveform, ps_SampleRate=sampling_rate, ps_FreqSeg=224)
        middle = int(len(waveform) // 2)
        selected_spectrum = raw_spectrum[:, middle - left_index:middle + right_index]
        spectrum_imgs.append(resize(selected_spectrum, (224, 224)))
        amplitude_imgs.append(construct_amplitude_coding_ehfo(feature_slice))
    return (
        np.stack(spectrum_imgs, axis=0),
        np.stack(amplitude_imgs, axis=0),
    )


def generate_omni_ehfo_feature_batch(hfo_waveforms, feature_param, device="cpu", n_jobs=1):
    del device, n_jobs
    sampling_rate = int(feature_param["resample"])
    expected_samples = int(feature_param["raw_waveform_length"] * sampling_rate / 1000)
    hfo_waveforms = np.asarray(hfo_waveforms, dtype=float)
    if int(hfo_waveforms.shape[1]) != expected_samples:
        raise ValueError(f"Omni eHFO expects {expected_samples} waveform samples.")

    total_samples = int(hfo_waveforms.shape[1])
    middle_idx = total_samples // 2
    if total_samples < 2 * sampling_rate:
        raise ValueError("Waveform length must be at least 2 seconds for Omni eHFO classification.")
    start_1s, end_1s = middle_idx - sampling_rate, middle_idx + sampling_rate
    start_05s = int(middle_idx - sampling_rate / 2)
    end_05s = int(middle_idx + sampling_rate / 2)
    data_slice_spectrum = np.asarray(hfo_waveforms[:, start_1s:end_1s], dtype=float)
    data_slice_features = np.asarray(hfo_waveforms[:, start_05s:end_05s], dtype=float)

    spectrum_imgs = []
    spike_imgs = []
    intensity_imgs = []
    for spectrum_slice, feature_slice in zip(data_slice_spectrum, data_slice_features):
        spectrum_imgs.append(compute_spectrum_omni_ehfo(spectrum_slice, ps_SampleRate=sampling_rate))
        spike_image, intensity_image = construct_features_ehfo(feature_slice)
        spike_imgs.append(spike_image)
        intensity_imgs.append(intensity_image)
    return (
        np.stack(spectrum_imgs, axis=0),
        np.stack(spike_imgs, axis=0),
        np.stack(intensity_imgs, axis=0),
    )


def create_extended_sig_ehfo(waveform):
    sig = np.asarray(waveform, dtype=float)
    s_len = len(sig)
    s_halflen = int(np.ceil(s_len / 2)) + 1
    start_win = sig[:s_halflen] - sig[0]
    end_win = sig[s_len - s_halflen - 1:] - sig[-1]
    start_win = -start_win[::-1] + sig[0]
    end_win = -end_win[::-1] + sig[-1]
    final_sig = np.concatenate((start_win[:-1], sig, end_win[1:]))
    if len(final_sig) % 2 == 0:
        final_sig = final_sig[:-1]
    return final_sig


def compute_spectrum_ehfo(org_sig, ps_SampleRate=1000, ps_FreqSeg=512, ps_MinFreqHz=10, ps_MaxFreqHz=500):
    final_sig = torch.from_numpy(create_extended_sig_ehfo(org_sig))
    ps_stdev_cycles = 3
    s_len = len(final_sig)
    s_half_len = math.floor(s_len / 2) + 1
    v_w_axis = torch.linspace(0, 2 * np.pi, s_len)[:-1] * float(ps_SampleRate)
    v_w_axis_half = v_w_axis[:s_half_len].repeat(int(ps_FreqSeg), 1)
    v_freq_axis = torch.linspace(float(ps_MaxFreqHz), float(ps_MinFreqHz), steps=int(ps_FreqSeg))
    v_win_fft = torch.zeros(int(ps_FreqSeg), s_len)
    s_stdev_sec = (1 / v_freq_axis) * ps_stdev_cycles
    v_win_fft[:, :s_half_len] = torch.exp(
        -0.5
        * torch.pow(v_w_axis_half - (2 * torch.pi * v_freq_axis.view(-1, 1)), 2)
        * (s_stdev_sec**2).view(-1, 1)
    )
    v_win_fft = v_win_fft * np.sqrt(s_len) / torch.norm(v_win_fft, dim=-1).view(-1, 1)
    input_signal_fft = torch.fft.fft(final_sig)
    result = torch.fft.ifft(input_signal_fft.view(1, -1) * v_win_fft) / torch.sqrt(s_stdev_sec).view(-1, 1)
    start_idx = int(len(org_sig) // 2)
    end_idx = int(len(org_sig) // 2 + len(org_sig))
    return np.abs(result[:, start_idx:end_idx].numpy())


def compute_spectrum_omni_ehfo(org_sig, ps_SampleRate=1000, ps_FreqSeg=512, ps_MinFreqHz=10, ps_MaxFreqHz=500):
    final_sig = create_extended_sig_ehfo(org_sig)
    s_len = len(final_sig)
    s_half_len = math.floor(s_len / 2) + 1
    v_w_axis = np.linspace(0, 2 * np.pi, s_len, endpoint=False) * float(ps_SampleRate)
    v_w_axis_half = v_w_axis[:s_half_len]
    v_freq_axis = np.linspace(float(ps_MinFreqHz), float(ps_MaxFreqHz), num=int(ps_FreqSeg))[::-1]
    input_signal_fft = np.fft.fft(final_sig)
    stdev_cycles = 3
    gabor = np.zeros((int(ps_FreqSeg), s_len), dtype=complex)
    for idx, freq in enumerate(v_freq_axis):
        win_fft = np.zeros(s_len)
        stdev_sec = (1 / freq) * stdev_cycles
        win_fft[:s_half_len] = np.exp(
            -0.5 * np.power(v_w_axis_half - (2 * np.pi * freq), 2) * (stdev_sec**2)
        )
        win_fft = win_fft * np.sqrt(s_len) / LA.norm(win_fft, 2)
        gabor[idx, :] = np.fft.ifft(input_signal_fft * win_fft) / np.sqrt(stdev_sec)
    center_idx = len(final_sig) // 2
    half_win_samples = int(ps_SampleRate) // 2
    return resize(np.abs(gabor[:, center_idx - half_win_samples:center_idx + half_win_samples]), (224, 224))


def construct_amplitude_coding_ehfo(raw_signal):
    values = np.asarray(raw_signal, dtype=float)
    signal_length = len(values)
    index = np.arange(signal_length)
    canvas = np.zeros((int(signal_length), int(signal_length)))
    canvas[index, :] = values
    return resize(canvas, (224, 224))


def normalized(a, max_=2000 - 11):
    values = np.asarray(a, dtype=float)
    ptp = np.ptp(values)
    if ptp <= 0:
        return np.full(values.shape, 5, dtype=int)
    return (max_ * (values - np.min(values)) / ptp).astype(int) + 5


def construct_features_ehfo(raw_signal):
    values = np.asarray(raw_signal, dtype=float)
    signal_length = len(values)
    canvas_width = 2000
    time_idx = np.arange(signal_length)

    spike_canvas = np.zeros((signal_length, canvas_width), dtype=float)
    spike_col_idx = normalized(values)
    for offset in range(3):
        spike_canvas[time_idx, np.clip(spike_col_idx - offset, 0, canvas_width - 1)] = 256
        spike_canvas[time_idx, np.clip(spike_col_idx + offset, 0, canvas_width - 1)] = 256
    spike_image = resize(spike_canvas, (224, 224))

    intensity_canvas = np.zeros((signal_length, canvas_width), dtype=float)
    intensity_canvas[time_idx, :] = values[:, np.newaxis]
    intensity_image = resize(intensity_canvas, (224, 224))
    return spike_image, intensity_image
