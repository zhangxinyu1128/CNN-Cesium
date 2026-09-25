"""One-dimensional CNN for multi-horizon track and intensity prediction."""

from typing import Dict

import torch
from torch import nn


class TrackCNN(nn.Module):
    """Four-step, six-feature input with separate track and wind heads."""

    def __init__(self, input_features: int = 6, horizons: int = 6, dropout: float = 0.3) -> None:
        super().__init__()
        self.horizons = horizons
        self.feature_extractor = nn.Sequential(
            nn.Conv1d(input_features, 32, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
        )
        self.shared = nn.Sequential(
            nn.Linear(64, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.track_head = nn.Linear(64, horizons * 2)
        self.wind_head = nn.Linear(64, horizons)
        self.task_log_variances = nn.Parameter(torch.zeros(2))

    def forward(self, features: torch.Tensor) -> Dict[str, torch.Tensor]:
        encoded = self.feature_extractor(features.transpose(1, 2))
        shared = self.shared(encoded)
        track = self.track_head(shared).view(-1, self.horizons, 2)
        wind = self.wind_head(shared).unsqueeze(-1)
        return {"track": track, "wind": wind}

def multi_task_loss(
    model: TrackCNN,
    predictions: Dict[str, torch.Tensor],
    targets: torch.Tensor,
) -> torch.Tensor:
    track_loss = nn.functional.mse_loss(predictions["track"], targets[..., :2])
    wind_loss = nn.functional.mse_loss(predictions["wind"], targets[..., 2:3])
    log_variances = model.task_log_variances
    return (
        torch.exp(-log_variances[0]) * track_loss
        + log_variances[0]
        + torch.exp(-log_variances[1]) * wind_loss
        + log_variances[1]
    )
