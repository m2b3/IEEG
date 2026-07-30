"""Shared pyHFO legacy binary feature generation."""

from __future__ import annotations

import math

import numpy as np
import torch
from skimage.transform import resize


def generate_feature_from_df_gpu_batch(hfo_waveforms, feature_param, model_name, device="cpu"):
    win_size = int(feature_param["image_size"])
    ps_min_freq_hz = float(feature_param["freq_min_hz"])
    ps_max_freq_hz = float(feature_param["freq_max_hz"])
    sampling_rate = float(feature_param["resample"])
    hfo_waveforms_tensor = torch.tensor(hfo_waveforms).float().to(device)
    expected_samples = int(feature_param["raw_waveform_length"] * sampling_rate / 1000)
    if int(hfo_waveforms_tensor.shape[1]) != expected_samples:
        raise ValueError(f"PyHFO expects {expected_samples} waveform samples.")

    total_samples = int(hfo_waveforms_tensor.shape[1])
    time_window_length = int(
        feature_param["model_additional_parameter"][model_name]["time_window_ms"]
        / 1000
        * sampling_rate
    )
    middle_idx = total_samples // 2
    start_idx = middle_idx - time_window_length // 2
    end_idx = start_idx + time_window_length
    hfo_waveforms_tensor = hfo_waveforms_tensor[:, start_idx:end_idx]

    time_freq_tensor, amp_tensor = hfo_feature_batch(
        hfo_waveforms_tensor,
        sampling_rate,
        win_size,
        ps_min_freq_hz,
        ps_max_freq_hz,
        resize_img=True,
        device=device,
    )

    n_feature = int(feature_param["model_additional_parameter"][model_name]["n_feature"])
    if n_feature == 1:
        return time_freq_tensor[:, None, :, :]
    if n_feature == 2:
        return torch.cat((time_freq_tensor[:, None, :, :], amp_tensor[:, None, :, :]), dim=1)
    raise ValueError(f"Invalid number of PyHFO features: {n_feature}")


def hfo_feature_batch(hfo_waveforms, sample_rate, win_size, ps_min_freq_hz, ps_max_freq_hz, *, resize_img=True, device="cpu"):
    spec_tensor = compute_spectrum_batch(
        hfo_waveforms,
        ps_SampleRate=float(sample_rate),
        ps_FreqSeg=int(win_size),
        ps_MinFreqHz=float(ps_min_freq_hz),
        ps_MaxFreqHz=float(ps_max_freq_hz),
        device=device,
    )
    amp_tensor = hfo_waveforms.unsqueeze(1).expand(-1, int(win_size), -1)
    if resize_img:
        spec_tensor = torch.nn.functional.interpolate(
            spec_tensor.unsqueeze(1),
            size=(int(win_size), int(win_size)),
            mode="bilinear",
            align_corners=False,
        ).squeeze(1)
        resized_amps = [
            torch.from_numpy(resize(np.asarray(amp_tensor[i].detach().cpu()), (int(win_size), int(win_size)))).float()
            for i in range(int(amp_tensor.shape[0]))
        ]
        amp_tensor = torch.stack(resized_amps).to(hfo_waveforms.device)
    return spec_tensor, amp_tensor


def compute_spectrum_batch(org_sigs, ps_SampleRate=2000, ps_FreqSeg=512, ps_MinFreqHz=10, ps_MaxFreqHz=500, device="cpu"):
    org_sigs_device = org_sigs.device if org_sigs.is_cuda else torch.device(device)
    org_sigs = org_sigs.to(org_sigs_device)
    _batch_size, sig_len = org_sigs.shape
    ii, jj = int(sig_len // 2), int(sig_len // 2 + sig_len)

    extend_sigs = create_extended_sig_batch(org_sigs)
    ps_stdev_cycles = 3
    s_len = int(extend_sigs.shape[1])
    s_half_len = math.floor(s_len / 2) + 1

    v_w_axis = (torch.linspace(0, 2 * np.pi, s_len, device=org_sigs_device)[:-1] * float(ps_SampleRate)).float()
    v_w_axis_half = v_w_axis[:s_half_len].repeat(int(ps_FreqSeg), 1)
    v_freq_axis = torch.linspace(float(ps_MaxFreqHz), float(ps_MinFreqHz), steps=int(ps_FreqSeg), device=org_sigs_device).float()

    v_win_fft = torch.zeros(int(ps_FreqSeg), s_len, device=org_sigs_device).float()
    s_stdev_sec = (1 / v_freq_axis) * ps_stdev_cycles
    v_win_fft[:, :s_half_len] = torch.exp(
        -0.5
        * (v_w_axis_half - (2 * torch.pi * v_freq_axis.view(-1, 1))) ** 2
        * (s_stdev_sec**2).view(-1, 1)
    )
    v_win_fft = v_win_fft * math.sqrt(s_len) / torch.norm(v_win_fft, dim=-1).view(-1, 1)
    v_input_signal_fft = torch.fft.fft(extend_sigs, dim=1)
    res = torch.fft.ifft(v_input_signal_fft.unsqueeze(1) * v_win_fft.unsqueeze(0), dim=2)[:, :, ii:jj]
    res = res / torch.sqrt(s_stdev_sec).view(1, -1, 1)
    return res.abs()


def create_extended_sig_batch(sigs):
    _batch_size, s_len = sigs.shape
    s_halflen = int(np.ceil(s_len / 2)) + 1
    start_win = sigs[:, :s_halflen] - sigs[:, [0]]
    end_win = sigs[:, s_len - s_halflen - 1:] - sigs[:, [-1]]
    start_win = -start_win.flip(dims=[1]) + sigs[:, [0]]
    end_win = -end_win.flip(dims=[1]) + sigs[:, [-1]]
    final_sigs = torch.cat((start_win[:, :-1], sigs, end_win[:, 1:]), dim=1)
    if final_sigs.shape[1] % 2 == 0:
        final_sigs = final_sigs[:, :-1]
    return final_sigs


def normalize_img(a):
    batch_num = int(a.shape[0])
    c = int(a.shape[1])
    h = int(a.shape[2])
    w = int(a.shape[3])
    reshaped = a.reshape(batch_num * c, -1)
    a_min = torch.min(reshaped, -1)[0].unsqueeze(1)
    a_max = torch.max(reshaped, -1)[0].unsqueeze(1)
    denom = torch.clamp(a_max - a_min, min=1e-12)
    normalized = 255.0 * (reshaped - a_min) / denom
    return normalized.reshape(batch_num, c, h, w)


def extract_waveforms_pyhfo(data, starts, ends, channel_names, unique_channel_names, sampling_rate, time_range):
    def calculate_boundary(start, end, length, win_len=2000):
        if start < win_len:
            return 0, int(win_len * 2)
        if end > length - win_len:
            return int(length - win_len * 2), int(length)
        return int(0.5 * (start + end) - win_len), int(0.5 * (start + end) + win_len)

    def extract_data(channel_data, start, end, win_len):
        channel_data = np.squeeze(channel_data)
        real_start, real_end = calculate_boundary(start, end, len(channel_data), win_len=win_len)
        return channel_data[real_start:real_end]

    win_len = int(float(sampling_rate) * float(time_range[1]) / 1000)
    biomarker_waveforms = np.zeros((len(starts), win_len * 2), dtype=float)
    unique_channel_names = np.asarray(unique_channel_names, dtype=object)
    for idx in range(len(starts)):
        channel_index = np.where(unique_channel_names == channel_names[idx])[0]
        if channel_index.size == 0:
            continue
        waveform = extract_data(data[channel_index], starts[idx], ends[idx], win_len)
        biomarker_waveforms[idx, : min(len(waveform), win_len * 2)] = waveform[: win_len * 2]
    return biomarker_waveforms


def compute_biomarker_feature_pyhfo(start, end, channel_name, data, sample_rate, win_size, ps_MinFreqHz, ps_MaxFreqHz, time_window_ms):
    spectrum_img = compute_spectrum_pyhfo(
        data,
        ps_SampleRate=sample_rate,
        ps_FreqSeg=win_size,
        ps_MinFreqHz=ps_MinFreqHz,
        ps_MaxFreqHz=ps_MaxFreqHz,
    )
    left_index = int((time_window_ms / 1000) * sample_rate)
    right_index = int((time_window_ms / 1000) * sample_rate)
    middle_index = len(data) // 2
    selected_data = data[middle_index - left_index:middle_index + right_index]
    select_amplitude_coding_plot = construct_coding_pyhfo(selected_data, length=left_index * 2)
    select_spectrum_img = spectrum_img[:, middle_index - left_index:middle_index + right_index]
    time_frequency_img = resize(select_spectrum_img, (win_size, win_size))
    amplitude_coding_plot = resize(select_amplitude_coding_plot, (win_size, win_size))
    return channel_name, start, end, time_frequency_img, amplitude_coding_plot, spectrum_img


def compute_spectrum_pyhfo(org_sig, ps_SampleRate=2000, ps_FreqSeg=512, ps_MinFreqHz=10, ps_MaxFreqHz=500):
    def create_extended_sig(sig):
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

    extend_sig = torch.from_numpy(np.asarray(create_extended_sig(org_sig), dtype=float))
    ps_stdev_cycles = 3
    s_len = len(extend_sig)
    s_half_len = math.floor(s_len / 2) + 1
    v_w_axis = torch.linspace(0, 2 * np.pi, s_len)[:-1] * ps_SampleRate
    v_w_axis_half = v_w_axis[:s_half_len].repeat(ps_FreqSeg, 1)
    v_freq_axis = torch.linspace(ps_MaxFreqHz, ps_MinFreqHz, steps=ps_FreqSeg)
    v_win_fft = torch.zeros(ps_FreqSeg, s_len)
    s_stdev_sec = (1 / v_freq_axis) * ps_stdev_cycles
    v_win_fft[:, :s_half_len] = torch.exp(
        -0.5
        * torch.pow(v_w_axis_half - (2 * torch.pi * v_freq_axis.view(-1, 1)), 2)
        * (s_stdev_sec**2).view(-1, 1)
    )
    v_win_fft = v_win_fft * np.sqrt(s_len) / torch.norm(v_win_fft, dim=-1).view(-1, 1)
    v_input_signal_fft = torch.fft.fft(extend_sig)
    res = torch.fft.ifft(v_input_signal_fft.view(1, -1) * v_win_fft) / torch.sqrt(s_stdev_sec).view(-1, 1)
    ii, jj = int(len(org_sig) // 2), int(len(org_sig) // 2 + len(org_sig))
    return np.abs(res[:, ii:jj].numpy())


def construct_coding_pyhfo(raw_signal, length=2000):
    index = np.arange(len(raw_signal))
    intensity_image = np.zeros((int(length), int(length)))
    intensity_image[index, :] = raw_signal
    return intensity_image
