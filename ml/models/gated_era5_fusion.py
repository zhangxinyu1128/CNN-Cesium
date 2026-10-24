"""Optional trajectory—ERA5 feature fusion with a safe missing-data fallback."""

from typing import Optional

import torch
from torch import Tensor, nn


class GatedERA5Fusion(nn.Module):
    """Fuse local-motion and ERA5 features while degrading to track-only input.

    ``era5_mask`` is 1 for a time step with a valid, time-aligned environment
    sample and 0 otherwise. The gate is multiplied by this mask, so missing or
    rejected ERA5 rows cannot influence the output. Setting ``era5_mask=None``
    is equivalent to an unavailable environment branch.
    """

    def __init__(self, track_features: int = 8, era5_features: int = 4, hidden: int = 32) -> None:
        super().__init__()
        self.track_encoder = nn.Sequential(
            nn.Linear(track_features, hidden), nn.GELU(), nn.LayerNorm(hidden)
        )
        self.era5_encoder = nn.Sequential(
            nn.Linear(era5_features, hidden), nn.GELU(), nn.LayerNorm(hidden)
        )
        self.gate = nn.Sequential(
            nn.Linear(hidden * 2, hidden), nn.GELU(), nn.Linear(hidden, hidden), nn.Sigmoid()
        )
        self.output = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.LayerNorm(hidden))

    def forward(self, track_features: Tensor, era5_features: Tensor, era5_mask: Optional[Tensor] = None) -> Tensor:
        if track_features.shape[:-1] != era5_features.shape[:-1]:
            raise ValueError("track and ERA5 features must share batch and time dimensions")
        track = self.track_encoder(track_features)
        environment = self.era5_encoder(era5_features)
        gate = self.gate(torch.cat((track, environment), dim=-1))
        if era5_mask is None:
            valid = torch.zeros_like(gate[..., :1])
        else:
            valid = era5_mask.to(dtype=gate.dtype, device=gate.device)
            while valid.dim() < gate.dim():
                valid = valid.unsqueeze(-1)
            if valid.shape[:-1] != gate.shape[:-1]:
                raise ValueError("ERA5 mask must match batch and time dimensions")
        fused = track + gate * valid * environment
        return self.output(fused)
