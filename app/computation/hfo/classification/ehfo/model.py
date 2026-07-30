from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torchvision.models as models


@dataclass(frozen=True)
class ResnetConfig:
    input_channels: int = 3
    num_classes: int = 1
    channel_selection: bool = True
    hidden_size: int = 64
    kernel_size: int = 7
    stride: int = 2
    padding: int = 3
    freeze: bool = False

    @staticmethod
    def from_dict(data: dict) -> "ResnetConfig":
        return ResnetConfig(
            input_channels=int(data.get("input_channels", 3)),
            num_classes=int(data.get("num_classes", 1)),
            channel_selection=bool(data.get("channel_selection", True)),
            hidden_size=int(data.get("hidden_size", 64)),
            kernel_size=int(data.get("kernel_size", 7)),
            stride=int(data.get("stride", 2)),
            padding=int(data.get("padding", 3)),
            freeze=bool(data.get("freeze", False)),
        )


class NeuralCNNForImageClassification(torch.nn.Module):
    """PyHFO 2.0 Hugging Face NeuralCNN architecture for local safetensors."""

    def __init__(self, config: ResnetConfig):
        super().__init__()
        self.input_channels = int(config.input_channels)
        self.outputs = int(config.num_classes)
        self.channel_selection = bool(config.channel_selection)
        self.hidden_size = int(config.hidden_size)
        self.kernel_size = int(config.kernel_size)
        self.stride = int(config.stride)
        self.padding = int(config.padding)

        self.cnn = models.resnet18(weights=None)
        if self.input_channels != 3:
            self.cnn.conv1 = nn.Conv2d(
                self.input_channels,
                self.hidden_size,
                self.kernel_size,
                self.stride,
                self.padding,
                bias=False,
            )
        self.cnn.fc = nn.Sequential(nn.Linear(512, self.hidden_size // 2))
        for param in self.cnn.fc.parameters():
            param.requires_grad = not bool(config.freeze)

        # Present in upstream state dict; forward intentionally mirrors pyHFO 2.0.
        self.bn0 = nn.BatchNorm1d(self.hidden_size // 2)
        self.relu0 = nn.LeakyReLU()
        self.fc = nn.Linear(self.hidden_size // 2, self.hidden_size // 2)
        self.bn = nn.BatchNorm1d(self.hidden_size // 2)
        self.relu = nn.LeakyReLU()
        self.fc1 = nn.Linear(self.hidden_size // 2, self.hidden_size // 4)
        self.bn1 = nn.BatchNorm1d(self.hidden_size // 4)
        self.relu1 = nn.LeakyReLU()
        self.fc_out = nn.Linear(self.hidden_size // 4, self.outputs)
        self.final_ac = nn.Sigmoid()

    def forward(self, input_features):
        input_features = input_features[:, 0:self.input_channels, :, :]
        batch = self.cnn(input_features)
        batch = self.bn(self.relu(self.fc(batch)))
        batch = self.bn1(self.relu1(self.fc1(batch)))
        return self.final_ac(self.fc_out(batch))


class NeuralCNN(torch.nn.Module):
    def __init__(self, num_classes=2, num_extra_features=0, dropout_p=0, freeze_layers=False):
        super().__init__()
        self.cnn = models.resnet18(weights=None)
        if freeze_layers:
            for param in self.cnn.parameters():
                param.requires_grad = False
        self.cnn.fc = nn.Sequential(nn.Linear(512, 32))
        self.bn0 = nn.BatchNorm1d(32)
        self.relu0 = nn.LeakyReLU()
        self.fc = nn.Linear(32 + num_extra_features, 32)
        self.bn = nn.BatchNorm1d(32)
        self.relu = nn.LeakyReLU()
        self.fc1 = nn.Linear(32, 16)
        self.bn1 = nn.BatchNorm1d(16)
        self.relu1 = nn.LeakyReLU()
        self.dropout = nn.Dropout(dropout_p)
        if num_classes < 3:
            self.fc_out = nn.Linear(16, 1)
            self.final_ac = nn.Sigmoid()
        else:
            self.fc_out = nn.Linear(16, num_classes)
            self.final_ac = nn.Softmax(dim=-1)

    def forward(self, x, additional_feature=None):
        batch = self.cnn(x)
        if additional_feature is not None:
            batch = torch.cat((batch, additional_feature), 1)
        batch = self.dropout(self.bn(self.relu(self.fc(batch))))
        batch = self.dropout(self.bn1(self.relu1(self.fc1(batch))))
        return self.final_ac(self.fc_out(batch))
