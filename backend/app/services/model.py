"""Load and serve the trained multi-horizon track CNN."""

import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


logger = logging.getLogger(__name__)


INPUT_STEPS = 4
INPUT_FEATURES = 6
FORECAST_STEPS = 6
STEP_HOURS = 6
FEATURE_NAMES = ["lng", "lat", "speed", "power", "delta_lng", "delta_lat"]
TARGET_FEATURES = ["lng", "lat", "speed"]


class ModelService:
    """Load one checkpoint at startup and perform validated single-window inference."""

    def __init__(
        self,
        checkpoint_path: Path,
        model_version: Optional[str] = None,
        uncertainty_path: Optional[Path] = None,
    ) -> None:
        self.checkpoint_path = checkpoint_path.resolve()
        self.model_version = model_version
        self._model = None
        self._torch = None
        self._normalizer = None
        self._device = None
        self._model_type = None
        self._reason = None
        self._checkpoint_sha256 = None
        self._uncertainty = None
        self._uncertainty_path = uncertainty_path
        self._load()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def _load(self) -> None:
        if not self.checkpoint_path.is_file():
            self._reason = "checkpoint file does not exist"
            return
        if self.checkpoint_path.stat().st_size == 0:
            self._reason = "checkpoint file is empty"
            return

        try:
            import torch

            requested_device = os.getenv("TC_DEVICE", "auto").lower()
            if requested_device == "auto":
                device_name = "cuda" if torch.cuda.is_available() else "cpu"
            elif requested_device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("TC_DEVICE=cuda but CUDA is unavailable")
            elif requested_device in ("cpu", "cuda"):
                device_name = requested_device
            else:
                raise ValueError("TC_DEVICE must be auto, cpu, or cuda")
            device = torch.device(device_name)
            checkpoint = torch.load(self.checkpoint_path, map_location=device, weights_only=False)
            normalizer = checkpoint["normalizer"]
            model_version = checkpoint.get("model_version", "")
            architecture = checkpoint.get("architecture", {})
            model_type = architecture.get("type") or (
                "residual" if "residual" in model_version else "track"
            )
            if architecture and (
                architecture.get("input_steps", INPUT_STEPS) != INPUT_STEPS
                or architecture.get("input_features", INPUT_FEATURES) != INPUT_FEATURES
                or architecture.get("target_steps", FORECAST_STEPS) != FORECAST_STEPS
                or architecture.get("target_features", len(TARGET_FEATURES)) != len(TARGET_FEATURES)
            ):
                raise ValueError("checkpoint architecture does not match the API contract")
            if model_type == "track":
                from ml.models.track_cnn import TrackCNN

                model = TrackCNN(
                    input_features=INPUT_FEATURES,
                    horizons=FORECAST_STEPS,
                    dropout=architecture.get("dropout", 0.3),
                )
            elif model_type == "residual":
                from ml.models.residual_cnn import ResidualTrackCNN

                model = ResidualTrackCNN(
                    input_features=INPUT_FEATURES,
                    horizons=FORECAST_STEPS,
                    dropout=architecture.get("dropout", 0.3),
                )
            else:
                raise ValueError("checkpoint model type must be track or residual")
            if normalizer.get("feature_names") != FEATURE_NAMES:
                raise ValueError("checkpoint feature names do not match the API contract")
            if len(normalizer.get("mean", [])) != INPUT_FEATURES or len(normalizer.get("std", [])) != INPUT_FEATURES:
                raise ValueError("checkpoint normalizer must contain six feature means and scales")
            if any(not math.isfinite(float(value)) for value in normalizer["mean"] + normalizer["std"]):
                raise ValueError("checkpoint normalizer contains non-finite values")
            if any(float(value) <= 0 for value in normalizer["std"]):
                raise ValueError("checkpoint normalizer scales must be positive")

            model.load_state_dict(checkpoint["model_state_dict"], strict=True)
            model.to(device)
            model.eval()

            self._torch = torch
            self._model = model
            self._normalizer = normalizer
            self._device = device
            self._model_type = model_type
            self.model_version = self.model_version or model_version
            self._checkpoint_sha256 = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
            if self._uncertainty_path and self._uncertainty_path.is_file():
                uncertainty = json.loads(self._uncertainty_path.read_text(encoding="utf-8"))
                radii = uncertainty.get("calibration", {}).get("location_radius_km", [])
                valid_radii = (
                    isinstance(radii, list)
                    and len(radii) == FORECAST_STEPS
                    and all(math.isfinite(float(radius)) and float(radius) > 0 for radius in radii)
                )
                if uncertainty.get("checkpoint_sha256") == self._checkpoint_sha256 and valid_radii:
                    self._uncertainty = uncertainty
            logger.info("Loaded %s from %s on %s", self.model_version, self.checkpoint_path, device)
        except Exception as error:
            self._reason = "model load failed: {}: {}".format(type(error).__name__, error)
            logger.exception("Could not load model checkpoint %s", self.checkpoint_path)

    def predict(self, history: Sequence[Dict[str, Any]], horizons_hours: Sequence[int]) -> Dict[str, Any]:
        if not self.ready:
            raise RuntimeError("model is not ready")
        if len(history) != INPUT_STEPS + 1:
            raise ValueError("history must contain five consecutive 6-hour observations")

        means = self._normalizer["mean"]
        scales = self._normalizer["std"]
        features: List[List[float]] = []
        for index in range(1, len(history)):
            previous = history[index - 1]
            point = history[index]
            required = (point.get("speed"), point.get("power"))
            if any(value is None for value in required):
                raise ValueError("speed and power are required for each of the last four history points")
            longitude = float(point["lng"]) % 360.0
            previous_longitude = float(previous["lng"]) % 360.0
            delta_longitude = (longitude - previous_longitude + 180.0) % 360.0 - 180.0
            row = [
                longitude,
                float(point["lat"]),
                float(point["speed"]),
                float(point["power"]),
                delta_longitude,
                float(point["lat"]) - float(previous["lat"]),
            ]
            if any(not math.isfinite(value) for value in row):
                raise ValueError("history features must be finite numbers")
            features.append([(value - means[column]) / scales[column] for column, value in enumerate(row)])

        tensor = self._torch.tensor([features], dtype=self._torch.float32, device=self._device)
        with self._torch.no_grad():
            output = self._model(tensor)
            if self._model_type == "residual":
                means_tensor = self._torch.tensor(means, dtype=tensor.dtype, device=self._device)
                scales_tensor = self._torch.tensor(scales, dtype=tensor.dtype, device=self._device)
                last = tensor[:, -1, :] * scales_tensor + means_tensor
                steps = self._torch.arange(1, FORECAST_STEPS + 1, dtype=tensor.dtype, device=self._device).view(1, -1)
                prior = self._torch.zeros((1, FORECAST_STEPS, 3), dtype=tensor.dtype, device=self._device)
                prior[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
                prior[..., 1] = last[:, None, 1] + steps * last[:, None, 5]
                prior[..., 2] = last[:, None, 2]
                residual = self._torch.cat((output["track"], output["wind"]), dim=-1)
                output_values = (prior - means_tensor[:3]) / scales_tensor[:3] + residual
                track, wind = output_values[0, :, :2].cpu().numpy(), output_values[0, :, 2].cpu().numpy()
            else:
                track = output["track"][0].detach().cpu().numpy()
                wind = output["wind"][0, :, 0].detach().cpu().numpy()
        target_indexes = (0, 1, 2)
        predictions = []
        for index in range(FORECAST_STEPS):
            longitude = (float(track[index, 0]) * scales[target_indexes[0]] + means[target_indexes[0]]) % 360.0
            latitude = float(track[index, 1]) * scales[target_indexes[1]] + means[target_indexes[1]]
            speed = float(wind[index]) * scales[target_indexes[2]] + means[target_indexes[2]]
            if not all(math.isfinite(value) for value in (longitude, latitude, speed)):
                raise RuntimeError("model produced a non-finite prediction")
            predictions.append(
                {
                    "lead_hours": STEP_HOURS * (index + 1),
                    "lng": longitude,
                    "lat": min(90.0, max(-90.0, latitude)),
                    "speed_ms": max(0.0, speed),
                    "p05": None,
                    "p95": None,
                    "location_radius_90_km": (
                        self._uncertainty["calibration"]["location_radius_km"][index]
                        if self._uncertainty else None
                    ),
                }
            )

        requested = set(horizons_hours)
        return {
            "model_version": self.model_version,
            "model_type": self._model_type,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "input_window": INPUT_STEPS,
            "horizons_hours": list(horizons_hours),
            "predictions": [item for item in predictions if item["lead_hours"] in requested],
            "uncertainty": self._uncertainty_summary(),
        }

    def _uncertainty_summary(self) -> Dict[str, Any]:
        if not self._uncertainty:
            return {"status": "unavailable"}
        calibration = self._uncertainty.get("calibration", {})
        return {
            "status": "historical_calibration",
            "target_coverage": calibration.get("target_coverage"),
            "method": calibration.get("method"),
            "calibration_storms": calibration.get("storm_count"),
            "interpretation": self._uncertainty.get("interpretation"),
        }

    def status(self) -> Dict[str, Any]:
        exists = self.checkpoint_path.exists()
        size_bytes = self.checkpoint_path.stat().st_size if exists else 0
        return {
            "status": "ready" if self.ready else "unavailable",
            "path": str(self.checkpoint_path),
            "exists": exists,
            "size_bytes": size_bytes,
            "model_version": self.model_version,
            "model_type": self._model_type,
            "device": str(self._device) if self._device is not None else None,
            "checkpoint_sha256": self._checkpoint_sha256,
            "reason": self._reason,
        }
