"""Shared pyHFO legacy binary model/preprocessing classes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torchvision.models as models
from torchvision.models import ResNet18_Weights


class NeuralCNN(torch.nn.Module):
    def __init__(self, in_channels, outputs, freeze=False, channel_selection=True):
        super().__init__()
        self.in_channels = in_channels
        self.outputs = outputs
        self.channel_selection = channel_selection
        self.cnn = models.resnet18(weights=ResNet18_Weights.DEFAULT)
        self.cnn.conv1 = nn.Conv2d(self.in_channels, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.cnn.fc = nn.Sequential(nn.Linear(512, 32))
        for param in self.cnn.fc.parameters():
            param.requires_grad = not freeze
        self.bn0 = nn.BatchNorm1d(32)
        self.relu0 = nn.LeakyReLU()
        self.fc = nn.Linear(32, 32)
        self.bn = nn.BatchNorm1d(32)
        self.relu = nn.LeakyReLU()
        self.fc1 = nn.Linear(32, 16)
        self.bn1 = nn.BatchNorm1d(16)
        self.relu1 = nn.LeakyReLU()
        self.fc_out = nn.Linear(16, self.outputs)
        self.final_ac = nn.Sigmoid()

    def forward(self, x):
        if self.in_channels == 1:
            x = x[:, 0:1, :, :]
        batch = self.cnn(x)
        batch = self.bn(self.relu(self.fc(batch)))
        batch = self.bn1(self.relu1(self.fc1(batch)))
        return self.final_ac(self.fc_out(batch))


class PreProcessing:
    def __init__(
        self,
        image_size,
        fs,
        freq_range_hz,
        event_length,
        selected_window_size_ms,
        selected_freq_range_hz,
        random_shift_ms,
    ):
        self.image_size = image_size
        self.freq_range = freq_range_hz
        self.fs = fs
        self.event_length = event_length
        self.crop_time = selected_window_size_ms
        self.crop_freq = selected_freq_range_hz
        self.random_shift_time = random_shift_ms
        self.initialize()

    def initialize(self):
        self.freq_range_low = self.freq_range[0]
        self.freq_range_high = self.freq_range[1]
        self.crop_range_index = self.crop_time / self.event_length * self.image_size
        self.crop_freq_low = self.crop_freq[0]
        self.crop_freq_high = self.crop_freq[1]
        self.calculate_crop_index()
        self.random_shift_index = int(self.random_shift_time * (self.image_size / self.event_length))
        self.random_shift = self.random_shift_time != 0

    @staticmethod
    def from_dict(d):
        data_meta = pd.DataFrame(
            {
                "image_size": d["image_size"],
                "freq_min_hz": d["freq_range_hz"][0],
                "freq_max_hz": d["freq_range_hz"][1],
                "resample": d["fs"],
                "time_window_ms": d["time_range_ms"][1],
            },
            index=[0],
        )
        return PreProcessing.from_df_args(data_meta, d)

    @staticmethod
    def from_df_args(data_meta, args):
        if len(data_meta) != 1:
            raise AssertionError("Data meta should be a single row")
        return PreProcessing(
            data_meta["image_size"].values[0],
            data_meta["resample"].values[0],
            [data_meta["freq_min_hz"].values[0], data_meta["freq_max_hz"].values[0]],
            data_meta["time_window_ms"].values[0],
            args["selected_window_size_ms"],
            args["selected_freq_range_hz"],
            args["random_shift_ms"],
        )

    def calculate_crop_index(self):
        self.crop_freq_index_low = self.image_size - self.image_size / (
            self.freq_range_high - self.freq_range_low
        ) * (self.crop_freq_low - self.freq_range_low)
        self.crop_freq_index_high = self.image_size - self.image_size / (
            self.freq_range_high - self.freq_range_low
        ) * (self.crop_freq_high - self.freq_range_low)
        self.crop_freq_index = np.array([self.crop_freq_index_high, self.crop_freq_index_low]).astype(int)
        self.crop_time_index = np.array([-self.crop_range_index, self.crop_range_index]).astype(int)
        self.crop_time_index_r = self.image_size // 2 + self.crop_time_index
        self.crop_index_w = np.abs(self.crop_time_index_r[0] - self.crop_time_index_r[1])
        self.crop_index_h = np.abs(self.crop_freq_index[0] - self.crop_freq_index[1])

    def disable_random_shift(self):
        self.random_shift = False

    def _cropping(self, data):
        time_crop_index = self.crop_time_index_r.copy()
        if self.random_shift:
            shift = np.random.randint(-self.random_shift_index, self.random_shift_index)
            time_crop_index += shift
        self.crop_freq_index[0] = min(max(0, self.crop_freq_index[0]), self.image_size)
        self.crop_freq_index[1] = min(max(0, self.crop_freq_index[1]), self.image_size)
        time_crop_index[0] = min(max(0, time_crop_index[0]), self.image_size)
        time_crop_index[1] = min(max(0, time_crop_index[1]), self.image_size)
        return data[:, :, self.crop_freq_index[0]:self.crop_freq_index[1], time_crop_index[0]:time_crop_index[1]]

    def __call__(self, data):
        return self._cropping(data)
