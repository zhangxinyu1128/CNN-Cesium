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
LOCAL_FEATURE_NAMES = [
    "east_km", "north_km", "speed", "power", "acceleration_ms_per_hour",
    "turn_rate_rad_per_hour", "heading_sin", "heading_cos",
]
LOCAL_TARGET_FEATURES = ["east_km", "north_km", "speed"]


class ModelService:
    """Load one checkpoint at startup and perform validated single-window inference."""

    def __init__(
        self,
        checkpoint_path: Path,
        model_version: Optional[str] = None,
        uncertainty_path: Optional[Path] = None,
        joint_uncertainty_path: Optional[Path] = None,
    ) -> None:
        self.checkpoint_path = checkpoint_path.resolve()
        self.model_version = model_version
        self._model = None
        self._torch = None
        self._normalizer = None
        self._device = None
        self._model_type = None
        self._coordinate_system = "geographic_v1"
        self._input_steps = INPUT_STEPS
        self._reason = None
        self._checkpoint_sha256 = None
        self._uncertainty = None
        self._uncertainty_path = uncertainty_path
        self._joint_uncertainty = None
        self._joint_uncertainty_path = joint_uncertainty_path
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
            coordinate_system = architecture.get(
                "coordinate_system", normalizer.get("coordinate_system", "geographic_v1")
            )
            if coordinate_system not in ("geographic_v1", "local_motion_v1"):
                raise ValueError("checkpoint coordinate system is not supported")
            input_steps = int(architecture.get("input_steps", INPUT_STEPS))
            if not 1 <= input_steps <= 24:
                raise ValueError("checkpoint input_steps must be between 1 and 24")
            feature_names = LOCAL_FEATURE_NAMES if coordinate_system == "local_motion_v1" else FEATURE_NAMES
            target_names = LOCAL_TARGET_FEATURES if coordinate_system == "local_motion_v1" else TARGET_FEATURES
            input_features = len(feature_names)
            model_type = architecture.get("type") or (
                "residual" if "residual" in model_version else "track"
            )
            if architecture and (
                input_steps != architecture.get("input_steps", INPUT_STEPS)
                or architecture.get("input_features", input_features) != input_features
                or architecture.get("target_steps", FORECAST_STEPS) != FORECAST_STEPS
                or architecture.get("target_features", len(TARGET_FEATURES)) != len(TARGET_FEATURES)
            ):
                raise ValueError("checkpoint architecture does not match the API contract")
            if model_type == "track":
                if input_steps != INPUT_STEPS:
                    raise ValueError("track CNN checkpoints require four input steps")
                from ml.models.track_cnn import TrackCNN

                model = TrackCNN(
                    input_features=input_features,
                    horizons=FORECAST_STEPS,
                    dropout=architecture.get("dropout", 0.3),
                )
            elif model_type == "residual":
                from ml.models.residual_cnn import ResidualTrackCNN

                model = ResidualTrackCNN(
                    input_features=input_features,
                    horizons=FORECAST_STEPS,
                    dropout=architecture.get("dropout", 0.3),
                )
            elif model_type == "multiscale_residual":
                if coordinate_system != "local_motion_v1":
                    raise ValueError("multi-scale residual checkpoints require local-motion coordinates")
                from ml.models.multiscale_residual_cnn import MultiScaleResidualCNN

                model = MultiScaleResidualCNN(
                    input_features=input_features,
                    horizons=FORECAST_STEPS,
                    hidden_channels=architecture.get("hidden_channels", 48),
                    dropout=architecture.get("dropout", 0.15),
                    quantile=architecture.get("quantile", False),
                )
            else:
                raise ValueError("checkpoint model type must be track, residual, or multiscale_residual")
            if normalizer.get("feature_names") != feature_names:
                raise ValueError("checkpoint feature names do not match the API contract")
            if len(normalizer.get("mean", [])) != input_features or len(normalizer.get("std", [])) != input_features:
                raise ValueError("checkpoint normalizer feature means and scales do not match its feature schema")
            if any(not math.isfinite(float(value)) for value in normalizer["mean"] + normalizer["std"]):
                raise ValueError("checkpoint normalizer contains non-finite values")
            if any(float(value) <= 0 for value in normalizer["std"]):
                raise ValueError("checkpoint normalizer scales must be positive")
            if coordinate_system == "local_motion_v1":
                if normalizer.get("target_names") != target_names:
                    raise ValueError("checkpoint target names do not match the local-motion API contract")
                if len(normalizer.get("target_mean", [])) != len(target_names) or len(normalizer.get("target_std", [])) != len(target_names):
                    raise ValueError("checkpoint must contain local-motion target means and scales")
                if any(not math.isfinite(float(value)) for value in normalizer["target_mean"] + normalizer["target_std"]):
                    raise ValueError("checkpoint local-motion target normalizer contains non-finite values")
                if any(float(value) <= 0 for value in normalizer["target_std"]):
                    raise ValueError("checkpoint local-motion target scales must be positive")

            model.load_state_dict(checkpoint["model_state_dict"], strict=True)
            model.to(device)
            model.eval()

            self._torch = torch
            self._model = model
            self._normalizer = normalizer
            self._device = device
            self._model_type = model_type
            self._coordinate_system = coordinate_system
            self._input_steps = input_steps
            self.model_version = self.model_version or model_version
            self._checkpoint_sha256 = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
            if self._uncertainty_path and self._uncertainty_path.is_file():
                uncertainty = json.loads(self._uncertainty_path.read_text(encoding="utf-8"))
                radii = uncertainty.get("calibration", {}).get("location_radius_km", [])
                ellipses = uncertainty.get("calibration", {}).get("location_ellipse_90", [])
                valid_radii = (
                    isinstance(radii, list)
                    and len(radii) == FORECAST_STEPS
                    and all(math.isfinite(float(radius)) and float(radius) > 0 for radius in radii)
                )
                valid_ellipses = (
                    not ellipses
                    or (
                        isinstance(ellipses, list)
                        and len(ellipses) == FORECAST_STEPS
                        and all(
                            isinstance(item, dict)
                            and all(
                                key in item
                                and math.isfinite(float(item[key])) and float(item[key]) > 0
                                for key in ("semi_major_axis_km", "semi_minor_axis_km")
                            )
                            and "bearing_deg" in item
                            and math.isfinite(float(item["bearing_deg"]))
                            for item in ellipses
                        )
                    )
                )
                if (
                    uncertainty.get("checkpoint_sha256") == self._checkpoint_sha256
                    and valid_radii
                    and valid_ellipses
                ):
                    self._uncertainty = uncertainty
            if self._joint_uncertainty_path and self._joint_uncertainty_path.is_file():
                joint = json.loads(self._joint_uncertainty_path.read_text(encoding="utf-8"))
                widths = joint.get("joint_location_speed", {}).get("calibration_by_horizon", [])
                valid_widths = (
                    isinstance(widths, list)
                    and len(widths) == FORECAST_STEPS
                    and all(
                        isinstance(item, dict)
                        and math.isfinite(float(item.get("speed_half_width_native", 0)))
                        and float(item.get("speed_half_width_native", 0)) > 0
                        for item in widths
                    )
                )
                if joint.get("checkpoint_sha256") == self._checkpoint_sha256 and valid_widths:
                    self._joint_uncertainty = joint
            logger.info("Loaded %s from %s on %s", self.model_version, self.checkpoint_path, device)
        except Exception as error:
            self._reason = "model load failed: {}: {}".format(type(error).__name__, error)
            logger.exception("Could not load model checkpoint %s", self.checkpoint_path)

    def predict(self, history: Sequence[Dict[str, Any]], horizons_hours: Sequence[int]) -> Dict[str, Any]:
        if not self.ready:
            raise RuntimeError("model is not ready")
        if len(history) != self._input_steps + 1:
            raise ValueError(
                "history must contain {} consecutive 6-hour observations".format(self._input_steps + 1)
            )

        means = self._normalizer["mean"]
        scales = self._normalizer["std"]
        features: List[List[float]] = []
        if self._coordinate_system == "local_motion_v1":
            if history[0].get("speed") is None or any(
                point.get("speed") is None or point.get("power") is None for point in history[1:]
            ):
                raise ValueError("context speed and input-observation speed and power are required for local motion")
            from ml.features.local_motion import encode_track

            rows = encode_track(history, STEP_HOURS)
            features = [row["features"] for row in rows]
            if len(features) != self._input_steps:
                raise ValueError("history could not be encoded to the checkpoint input length")
            if any(not math.isfinite(value) for row in features for value in row):
                raise ValueError("history features must be finite numbers")
        else:
            for index in range(1, len(history)):
                previous = history[index - 1]
                point = history[index]
                required = (point.get("speed"), point.get("power"))
                if any(value is None for value in required):
                    raise ValueError("speed and power are required for each of the last four history points")
                longitude = float(point["lng"]) % 360.0
                previous_longitude = float(previous["lng"]) % 360.0
                delta_longitude = (longitude - previous_longitude + 180.0) % 360.0 - 180.0
                row = [longitude, float(point["lat"]), float(point["speed"]), float(point["power"]), delta_longitude, float(point["lat"]) - float(previous["lat"])]
                if any(not math.isfinite(value) for value in row):
                    raise ValueError("history features must be finite numbers")
                features.append(row)
        features = [[(value - means[column]) / scales[column] for column, value in enumerate(row)] for row in features]

        tensor = self._torch.tensor([features], dtype=self._torch.float32, device=self._device)
        quantile_values = None
        with self._torch.no_grad():
            output = self._model(tensor)
            if self._model_type == "multiscale_residual":
                target_means = self._torch.tensor(
                    self._normalizer["target_mean"], dtype=tensor.dtype, device=self._device
                )
                target_scales = self._torch.tensor(
                    self._normalizer["target_std"], dtype=tensor.dtype, device=self._device
                )
                feature_means = self._torch.tensor(means, dtype=tensor.dtype, device=self._device)
                feature_scales = self._torch.tensor(scales, dtype=tensor.dtype, device=self._device)
                last = tensor[:, -1, :] * feature_scales + feature_means
                steps = self._torch.arange(
                    1, FORECAST_STEPS + 1, dtype=tensor.dtype, device=self._device
                ).view(1, -1)
                prior_raw = self._torch.stack(
                    (
                        steps * last[:, None, 0],
                        steps * last[:, None, 1],
                        last[:, None, 2].expand(-1, FORECAST_STEPS),
                    ),
                    dim=-1,
                )
                prior = (prior_raw - target_means) / target_scales
                normalized_track = output["track"] + prior[..., :2]
                normalized_wind = output["wind"] + prior[..., 2:3]
                local_values = self._torch.cat((normalized_track, normalized_wind), dim=-1)[0]
                local_values = (
                    local_values.cpu().numpy() * self._normalizer["target_std"]
                    + self._normalizer["target_mean"]
                )
                track, wind = local_values[:, :2], local_values[:, 2]
                if output.get("quantiles") is not None:
                    normalized_quantiles = output["quantiles"] + prior[..., None, :2]
                    normalized_quantiles, _ = normalized_quantiles.sort(dim=2)
                    quantile_values = (
                        normalized_quantiles[0].cpu().numpy() * self._normalizer["target_std"][:2]
                        + self._normalizer["target_mean"][:2]
                    )
            elif self._model_type == "residual":
                means_tensor = self._torch.tensor(means, dtype=tensor.dtype, device=self._device)
                scales_tensor = self._torch.tensor(scales, dtype=tensor.dtype, device=self._device)
                last = tensor[:, -1, :] * scales_tensor + means_tensor
                steps = self._torch.arange(1, FORECAST_STEPS + 1, dtype=tensor.dtype, device=self._device).view(1, -1)
                prior = self._torch.zeros((1, FORECAST_STEPS, 3), dtype=tensor.dtype, device=self._device)
                residual = self._torch.cat((output["track"], output["wind"]), dim=-1)
                if self._coordinate_system == "local_motion_v1":
                    target_means = self._torch.tensor(self._normalizer["target_mean"], dtype=tensor.dtype, device=self._device)
                    target_scales = self._torch.tensor(self._normalizer["target_std"], dtype=tensor.dtype, device=self._device)
                    prior[..., 0] = steps * last[:, None, 0]
                    prior[..., 1] = steps * last[:, None, 1]
                    prior[..., 2] = last[:, None, 2]
                    prior = (prior - target_means) / target_scales
                    output_values = prior + residual
                    local_values = output_values[0].cpu().numpy() * self._normalizer["target_std"] + self._normalizer["target_mean"]
                    track, wind = local_values[:, :2], local_values[:, 2]
                else:
                    prior[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
                    prior[..., 1] = last[:, None, 1] + steps * last[:, None, 5]
                    prior[..., 2] = last[:, None, 2]
                    output_values = (prior - means_tensor[:3]) / scales_tensor[:3] + residual
                    track, wind = output_values[0, :, :2].cpu().numpy(), output_values[0, :, 2].cpu().numpy()
            else:
                track = output["track"][0].detach().cpu().numpy()
                wind = output["wind"][0, :, 0].detach().cpu().numpy()
                if self._coordinate_system == "local_motion_v1":
                    local_values = (
                        self._torch.cat((output["track"], output["wind"]), dim=-1)[0].cpu().numpy()
                        * self._normalizer["target_std"]
                        + self._normalizer["target_mean"]
                    )
                    track, wind = local_values[:, :2], local_values[:, 2]
        target_indexes = (0, 1, 2)
        predictions = []
        for index in range(FORECAST_STEPS):
            if self._coordinate_system == "local_motion_v1":
                from ml.features.local_motion import destination_lonlat

                longitude, latitude = destination_lonlat(history[-1], float(track[index, 0]), float(track[index, 1]))
                speed = float(wind[index])
            else:
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
                    "p10": self._local_quantile_point(history[-1], quantile_values, index, 0),
                    "p90": self._local_quantile_point(history[-1], quantile_values, index, 2),
                    "location_radius_90_km": (
                        self._uncertainty["calibration"]["location_radius_km"][index]
                        if self._uncertainty else None
                    ),
                    "uncertainty_region": self._region_for_horizon(index),
                    "speed_interval_source": self._speed_interval_for_horizon(index, speed),
                }
            )

        requested = set(horizons_hours)
        return {
            "model_version": self.model_version,
            "model_type": self._model_type,
            "coordinate_system": self._coordinate_system,
            "input_steps": self._input_steps,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "input_window": self._input_steps,
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
            "region_geometry": (
                "conformal_ellipse"
                if calibration.get("location_ellipse_90")
                else "isotropic_circle"
            ),
            "region_note": "基于历史台风分组校准的二维位置区域，不是实时预报保证或灾害概率区。",
            "joint_region_geometry": (
                "location_ellipse_and_source_speed_interval"
                if self._joint_uncertainty else None
            ),
            "joint_region_note": (
                "位置椭圆与源数据风速字段区间的历史联合校准结果；风速单位沿用源字段，不是灾害概率或风圈。"
                if self._joint_uncertainty else None
            ),
        }

    def _speed_interval_for_horizon(self, index: int, speed: float) -> Optional[Dict[str, Any]]:
        if not self._joint_uncertainty:
            return None
        calibrations = self._joint_uncertainty.get("joint_location_speed", {}).get("calibration_by_horizon", [])
        if index >= len(calibrations):
            return None
        half_width = float(calibrations[index].get("speed_half_width_native", 0.0))
        if not math.isfinite(half_width) or half_width <= 0:
            return None
        return {
            "lower": max(0.0, float(speed) - half_width),
            "upper": float(speed) + half_width,
            "unit": "source_native",
            "interpretation": "历史联合校准风速字段区间，不是灾害概率或业务风圈。",
        }

    def _region_for_horizon(self, index: int) -> Optional[Dict[str, Any]]:
        if not self._uncertainty:
            return None
        calibration = self._uncertainty.get("calibration", {})
        radii = calibration.get("location_radius_km", [])
        if index >= len(radii):
            return None
        radius = float(radii[index])
        if not math.isfinite(radius) or radius <= 0:
            return None
        ellipses = calibration.get("location_ellipse_90", [])
        ellipse = ellipses[index] if index < len(ellipses) else None
        return {
            "geometry": ellipse.get("geometry", "conformal_ellipse") if ellipse else "isotropic_circle",
            "coverage": float(calibration.get("target_coverage", 0.9)),
            "semi_major_axis_km": float(ellipse["semi_major_axis_km"]) if ellipse else radius,
            "semi_minor_axis_km": float(ellipse["semi_minor_axis_km"]) if ellipse else radius,
            "bearing_deg": float(ellipse.get("bearing_deg", 0.0)) if ellipse else 0.0,
            "area_km2": float(ellipse["area_km2"]) if ellipse else math.pi * radius * radius,
            "interpretation": "历史台风分组校准的二维位置区域，不是实时预报保证或灾害概率区。",
        }

    @staticmethod
    def _local_quantile_point(
        origin: Dict[str, Any], values: Optional[Any], horizon_index: int, quantile_index: int
    ) -> Optional[Dict[str, float]]:
        if values is None:
            return None
        from ml.features.local_motion import destination_lonlat

        longitude, latitude = destination_lonlat(
            origin,
            float(values[horizon_index, quantile_index, 0]),
            float(values[horizon_index, quantile_index, 1]),
        )
        return {"lng": longitude, "lat": latitude}

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
            "coordinate_system": self._coordinate_system if self.ready else None,
            "input_steps": self._input_steps if self.ready else None,
            "history_points": self._input_steps + 1 if self.ready else None,
            "device": str(self._device) if self._device is not None else None,
            "checkpoint_sha256": self._checkpoint_sha256,
            "reason": self._reason,
        }
