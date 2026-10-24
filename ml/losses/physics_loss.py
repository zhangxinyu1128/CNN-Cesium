"""Pinball and kinematic regularization losses for local-motion forecasts."""

from typing import Dict, Optional, Sequence

import torch
from torch import Tensor
from torch.nn import functional as F


def pinball_loss(predictions: Tensor, target: Tensor, quantiles: Sequence[float]) -> Tensor:
    """Mean pinball loss for predictions shaped (B, H, Q, C)."""
    if predictions.dim() != 4 or predictions.size(2) != len(quantiles):
        raise ValueError("quantile predictions must have shape (batch, horizon, quantile, channel)")
    q = torch.as_tensor(quantiles, dtype=predictions.dtype, device=predictions.device).view(1, 1, -1, 1)
    error = target.unsqueeze(2) - predictions
    return torch.maximum(q * error, (q - 1.0) * error).mean()


def physics_constrained_loss(
    predictions: Dict[str, Optional[Tensor]],
    target: Tensor,
    target_mean: Tensor,
    target_std: Tensor,
    quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    velocity_weight: float = 0.05,
    acceleration_weight: float = 0.002,
    turn_weight: float = 0.002,
) -> Dict[str, Tensor]:
    """Combine point/quantile fit with velocity, acceleration and turn penalties.

    The first two target channels are east/north displacement in km relative to
    the last observed point. Physics terms use denormalized values and fixed km
    scales so their weights are interpretable and stable across data normalizers.
    """
    predicted_track = predictions["track"]
    predicted_wind = predictions["wind"]
    if predicted_track is None or predicted_wind is None:
        raise ValueError("track and wind predictions are required")

    if predictions.get("quantiles") is None:
        location_fit = F.smooth_l1_loss(predicted_track, target[..., :2])
    else:
        location_fit = pinball_loss(predictions["quantiles"], target[..., :2], quantiles)
    wind_fit = F.mse_loss(predicted_wind, target[..., 2:3])

    mean = target_mean.to(device=target.device, dtype=target.dtype)
    std = target_std.to(device=target.device, dtype=target.dtype)
    predicted_km = predicted_track * std[:2] + mean[:2]
    actual_km = target[..., :2] * std[:2] + mean[:2]
    pred_velocity = torch.diff(predicted_km, dim=1)
    true_velocity = torch.diff(actual_km, dim=1)
    velocity = F.smooth_l1_loss(pred_velocity / 100.0, true_velocity / 100.0)

    if pred_velocity.size(1) > 1:
        pred_acceleration = torch.diff(pred_velocity, dim=1)
        acceleration = (pred_acceleration / 100.0).square().mean()
        pred_angle = torch.atan2(pred_velocity[..., 0], pred_velocity[..., 1])
        angle_delta = torch.atan2(
            torch.sin(torch.diff(pred_angle, dim=1)),
            torch.cos(torch.diff(pred_angle, dim=1)),
        )
        turn = angle_delta.square().mean()
    else:
        acceleration = predicted_track.new_zeros(())
        turn = predicted_track.new_zeros(())

    total = (
        location_fit
        + 0.25 * wind_fit
        + velocity_weight * velocity
        + acceleration_weight * acceleration
        + turn_weight * turn
    )
    return {
        "loss": total,
        "location": location_fit.detach(),
        "wind": wind_fit.detach(),
        "velocity": velocity.detach(),
        "acceleration": acceleration.detach(),
        "turn": turn.detach(),
    }
