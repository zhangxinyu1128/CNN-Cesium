"""1D CNN 多任务基线模型,与 IMPLEMENTATION_PLAN 第 3 阶段一致。

输入:  (B, T=4, C=6)
    - T = INPUT_STEPS = 4 个 6 小时步长
    - C = 6 个特征 [lng, lat, speed, power, delta_lng, delta_lat]
    - 已用 manifest.preprocessing.normalizer 做过零均值单位方差归一化
输出: dict 包含
    - track:     (B, 6, 2)  未来 6 个 6 小时步长的 [lng, lat],归一化空间
    - intensity: (B, 6, 1)  未来 6 个 6 小时步长的 [speed],归一化空间

对应 24/48/72/96/120/144 小时六个预测时效,输出后由 infer.py 用
normalizer.mean/std[target_feature_indexes] 反归一化,再回填经度 mod 360。

设计要点:
- 两层 Conv1d + ReLU + Dropout,匹配策划书的基线网络结构
- 共享编码器 + 双解码头 (track / intensity),即"多任务输出"
- 直接一次输出 6 步 (direct multi-step),避免滚动预测的误差累积
- 模型小 (约 5 万参数),与样本量 (1,903 条轨迹) 匹配,避免过拟合
"""
from typing import Dict

import torch
from torch import Tensor, nn


class TyphoonCNN(nn.Module):
    """两层 Conv1D + 共享编码器 + 双任务头。

    形状约定:
        x:        (B, T=4, C=6)
        track:    (B, H=6, 2)
        intensity:(B, H=6, 1)
    """

    def __init__(
        self,
        in_channels: int = 6,
        hidden_channels: int = 64,
        dropout: float = 0.1,
        input_steps: int = 4,
        horizon: int = 6,
    ) -> None:
        super().__init__()
        self.horizon = horizon
        self.input_steps = input_steps
        self.in_channels = in_channels

        self.encoder = nn.Sequential(
            nn.Conv1d(in_channels, hidden_channels, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Conv1d(hidden_channels, hidden_channels, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
        )
        flat_dim = hidden_channels * input_steps
        self.head_track = nn.Linear(flat_dim, horizon * 2)      # lng, lat
        self.head_intensity = nn.Linear(flat_dim, horizon * 1)  # speed

    def forward(self, x: Tensor) -> Dict[str, Tensor]:
        if x.dim() != 3 or x.size(1) != self.input_steps or x.size(2) != self.in_channels:
            raise ValueError(
                f"expected x of shape (B, {self.input_steps}, {self.in_channels}), got {tuple(x.shape)}"
            )
        # (B, T, C) -> (B, C, T)  Conv1d 期望 (B, channels, length)
        h = self.encoder(x.transpose(1, 2))
        h = h.flatten(1)                                          # (B, hidden * T)
        track = self.head_track(h).view(-1, self.horizon, 2)
        intensity = self.head_intensity(h).view(-1, self.horizon, 1)
        return {"track": track, "intensity": intensity}

    @torch.no_grad()
    def predict_normalized(self, x: Tensor) -> Tensor:
        """合并双头输出为 (B, 6, 3) 的归一化预测张量,便于与 y 对齐。

        用于训练时的指标计算与推理时的反归一化。
        """
        out = self.forward(x)
        return torch.cat([out["track"], out["intensity"]], dim=-1)


def multi_task_loss(
    outputs: Dict[str, Tensor],
    target: Tensor,
    track_weight: float = 1.0,
    intensity_weight: float = 0.5,
) -> Dict[str, Tensor]:
    """联合损失: track MSE + intensity MSE。

    target: (B, 6, 3) 归一化后的 [lng, lat, speed]。
    权重建议在小预算实验里扫描 (1.0, 0.5) 与 (1.0, 1.0),记录到训练日志。
    """
    if target.dim() != 3 or target.size(-1) != 3:
        raise ValueError(f"target must have shape (B, H, 3), got {tuple(target.shape)}")
    track_target = target[..., :2]
    intensity_target = target[..., 2:3]
    loss_track = torch.nn.functional.mse_loss(outputs["track"], track_target)
    loss_intensity = torch.nn.functional.mse_loss(outputs["intensity"], intensity_target)
    total = track_weight * loss_track + intensity_weight * loss_intensity
    return {
        "loss": total,
        "track": loss_track.detach(),
        "intensity": loss_intensity.detach(),
    }
