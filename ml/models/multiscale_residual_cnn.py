"""Multi-scale causal residual CNN for 6-hourly local-motion windows."""

from typing import Dict, Optional

import torch
from torch import Tensor, nn
from torch.nn import functional as F


class CausalResidualBlock(nn.Module):
    def __init__(self, channels: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.left_padding = 2 * dilation
        self.conv1 = nn.Conv1d(channels, channels, kernel_size=3, dilation=dilation)
        self.conv2 = nn.Conv1d(channels, channels, kernel_size=3, dilation=dilation)
        self.norm1 = nn.BatchNorm1d(channels)
        self.norm2 = nn.BatchNorm1d(channels)
        self.dropout = nn.Dropout(dropout)

    def forward(self, values: Tensor) -> Tensor:
        hidden = self.conv1(F.pad(values, (self.left_padding, 0)))
        hidden = self.dropout(F.gelu(self.norm1(hidden)))
        hidden = self.conv2(F.pad(hidden, (self.left_padding, 0)))
        hidden = self.dropout(self.norm2(hidden))
        return F.gelu(values + hidden)


class MultiScaleResidualCNN(nn.Module):
    """Residual motion predictor with temporal attention and optional P10/P50/P90 heads.

    Inputs are (batch, history_steps, 8) local-motion features. The heads predict
    residuals over a constant-velocity prior; `track` is the median/point head.
    """

    quantiles = (0.1, 0.5, 0.9)

    def __init__(
        self,
        input_features: int = 8,
        horizons: int = 6,
        hidden_channels: int = 48,
        dropout: float = 0.15,
        quantile: bool = False,
    ) -> None:
        super().__init__()
        self.input_features = input_features
        self.horizons = horizons
        self.quantile = quantile
        self.stem = nn.Conv1d(input_features, hidden_channels, kernel_size=1)
        self.scales = nn.ModuleList(
            CausalResidualBlock(hidden_channels, dilation, dropout)
            for dilation in (1, 2, 4)
        )
        self.scale_fusion = nn.Conv1d(hidden_channels * 3, hidden_channels, kernel_size=1)
        self.attention = nn.Conv1d(hidden_channels, 1, kernel_size=1)
        track_heads = 3 if quantile else 1
        self.track_head = nn.Linear(hidden_channels, horizons * track_heads * 2)
        self.wind_head = nn.Linear(hidden_channels, horizons)

    def forward(self, features: Tensor) -> Dict[str, Optional[Tensor]]:
        if features.dim() != 3 or features.size(-1) != self.input_features:
            raise ValueError(
                "expected input shape (batch, history_steps, {}), got {}".format(
                    self.input_features, tuple(features.shape)
                )
            )
        encoded = self.stem(features.transpose(1, 2))
        multiscale = torch.cat([branch(encoded) for branch in self.scales], dim=1)
        fused = self.scale_fusion(multiscale)
        weights = torch.softmax(self.attention(fused), dim=-1)
        pooled = torch.sum(fused * weights, dim=-1)
        track = self.track_head(pooled)
        wind = self.wind_head(pooled).unsqueeze(-1)
        if self.quantile:
            track = track.view(-1, self.horizons, 3, 2)
            median = track[:, :, 1, :]
            quantiles = track
        else:
            median = track.view(-1, self.horizons, 2)
            quantiles = None
        return {"track": median, "quantiles": quantiles, "wind": wind}
