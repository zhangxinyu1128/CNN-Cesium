"""Persistence 基线: 用最后一步的位移线性外推。

按 README 建议作为强基线对照,与 CNN 在相同测试集上比较 Skill Score。
该基线无参数,也不训练,只依赖归一化后的 delta_lng / delta_lat。
"""
from typing import Dict

import torch
from torch import Tensor


class PersistenceBaseline:
    """无参数基线,无需训练。

    输入:  (B, 4, 6) 归一化后的 [lng, lat, speed, power, delta_lng, delta_lat]
    输出:  dict(track=(B, 6, 2), intensity=(B, 6, 1)),归一化空间
    """

    def __init__(self, horizon: int = 6) -> None:
        self.horizon = horizon

    def __call__(self, x: Tensor) -> Dict[str, Tensor]:
        if x.dim() != 3 or x.size(1) < 2 or x.size(2) < 6:
            raise ValueError(
                f"expected x of shape (B, >=2, 6), got {tuple(x.shape)}"
            )
        last = x[:, -1, :]                                  # (B, 6)
        delta_lng = last[:, 4:5]                            # (B, 1)
        delta_lat = last[:, 5:6]                            # (B, 1)
        steps = torch.arange(
            1, self.horizon + 1, device=x.device, dtype=x.dtype
        ).view(1, self.horizon, 1)                         # (1, 6, 1)
        pred_lng = last[:, 0:1].unsqueeze(1) + steps * delta_lng.unsqueeze(1)
        pred_lat = last[:, 1:2].unsqueeze(1) + steps * delta_lat.unsqueeze(1)
        pred_speed = last[:, 2:3].unsqueeze(1).expand(-1, self.horizon, -1)
        return {
            "track": torch.cat([pred_lng, pred_lat], dim=-1),     # (B, 6, 2)
            "intensity": pred_speed,                              # (B, 6, 1)
        }

    @torch.no_grad()
    def predict_normalized(self, x: Tensor) -> Tensor:
        out = self(x)
        return torch.cat([out["track"], out["intensity"]], dim=-1)
