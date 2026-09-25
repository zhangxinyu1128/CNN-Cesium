"""Load and serve the trained multi-horizon track CNN."""

import hashlib
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

    def __init__(self, checkpoint_path: Path, model_version: Optional[str] = None) -> None:
        self.checkpoint_path = checkpoint_path.resolve()
        self.model_version = model_version
        self._model = None
        self._torch = None
        self._normalizer = None
        self._device = None
        self._reason = None
        self._checkpoint_sha256 = None
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

            from ml.models.track_cnn import TrackCNN

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
            architecture = checkpoint["architecture"]
            normalizer = checkpoint["normalizer"]
            if architecture.get("input_steps") != INPUT_STEPS:
                raise ValueError("checkpoint input_steps must be 4")
            if architecture.get("input_features") != INPUT_FEATURES:
                raise ValueError("checkpoint input_features must be 6")
            if architecture.get("target_steps") != FORECAST_STEPS:
                raise ValueError("checkpoint target_steps must be 6")
            if architecture.get("target_features") != len(TARGET_FEATURES):
                raise ValueError("checkpoint must predict longitude, latitude, and wind speed")
            if normalizer.get("feature_names") != FEATURE_NAMES:
                raise ValueError("checkpoint feature names do not match the API contract")
            if len(normalizer.get("mean", [])) != INPUT_FEATURES or len(normalizer.get("std", [])) != INPUT_FEATURES:
                raise ValueError("checkpoint normalizer must contain six feature means and scales")
            if any(not math.isfinite(float(value)) for value in normalizer["mean"] + normalizer["std"]):
                raise ValueError("checkpoint normalizer contains non-finite values")
            if any(float(value) <= 0 for value in normalizer["std"]):
                raise ValueError("checkpoint normalizer scales must be positive")

            model = TrackCNN(
                input_features=architecture["input_features"],
                horizons=architecture["target_steps"],
                dropout=architecture["dropout"],
            )
            model.load_state_dict(checkpoint["model_state_dict"], strict=True)
            model.to(device)
            model.eval()

            self._torch = torch
            self._model = model
            self._normalizer = normalizer
            self._device = device
            self.model_version = self.model_version or checkpoint["model_version"]
            self._checkpoint_sha256 = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
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
                }
            )

        requested = set(horizons_hours)
        return {
            "model_version": self.model_version,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "input_window": INPUT_STEPS,
            "horizons_hours": list(horizons_hours),
            "predictions": [item for item in predictions if item["lead_hours"] in requested],
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
            "device": str(self._device) if self._device is not None else None,
            "checkpoint_sha256": self._checkpoint_sha256,
            "reason": self._reason,
        }
