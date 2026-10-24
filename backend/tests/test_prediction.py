"""Integration tests for checkpoint-backed prediction without dataset artifacts."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch
from pydantic import ValidationError

from backend.app.schemas import PredictionRequest, PredictionResponse
from backend.app.services.model import ModelService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def sample_history(count=5):
    """Return deterministic six-hour observations independent of training data."""
    start = datetime(2025, 8, 12, 0, 0)
    positions = [(128.0, 16.0), (128.7, 16.3), (129.5, 16.8), (130.4, 17.4), (131.4, 18.1)]
    while len(positions) < count:
        longitude, latitude = positions[-1]
        positions.append((longitude + 1.1, latitude + 0.7))
    return [
        {
            "time": (start + timedelta(hours=6 * index)).isoformat(),
            "lng": lng,
            "lat": lat,
            "speed": 22.0 + index * 1.5,
            "power": 8.0 + index * 0.2,
        }
        for index, (lng, lat) in enumerate(positions[:count])
    ]


def encoded_input(service, history):
    """Encode requests from the schema declared by the loaded checkpoint."""
    if service._coordinate_system == "local_motion_v1":
        from ml.features.local_motion import encode_track

        raw = [row["features"] for row in encode_track(history, 6.0)]
    else:
        raw = []
        for previous, point in zip(history, history[1:]):
            delta_lng = (point["lng"] - previous["lng"] + 180.0) % 360.0 - 180.0
            raw.append([
                point["lng"] % 360.0,
                point["lat"],
                point["speed"],
                point["power"],
                delta_lng,
                point["lat"] - previous["lat"],
            ])
    means = np.asarray(service._normalizer["mean"], dtype=np.float64)
    scales = np.asarray(service._normalizer["std"], dtype=np.float64)
    return np.asarray([[(value - means[i]) / scales[i] for i, value in enumerate(row)] for row in raw], dtype=np.float32)


class PredictionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkpoint_path = PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth"
        cls.residual_checkpoint_path = PROJECT_ROOT / "artifacts" / "residual" / "checkpoints" / "track_cnn_residual.pth"
        cls.multiscale_checkpoint_path = PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_physics_multiscale.pth"
        cls.uncertainty_path = PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_20261008.json"
        cls.multiscale_uncertainty_path = PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_multiscale_physics_2028.json"
        cls.joint_uncertainty_path = PROJECT_ROOT / "artifacts" / "reports" / "stage4_extensions_20261008.json"
        cls.service = ModelService(
            cls.checkpoint_path,
            uncertainty_path=cls.uncertainty_path,
            joint_uncertainty_path=cls.joint_uncertainty_path,
        )
        cls.residual_service = ModelService(cls.residual_checkpoint_path)
        cls.multiscale_service = ModelService(
            cls.multiscale_checkpoint_path,
            uncertainty_path=cls.multiscale_uncertainty_path,
        )
        if not cls.service.ready:
            raise RuntimeError("trained checkpoint could not be loaded: " + str(cls.service.status()))
        if not cls.residual_service.ready:
            raise RuntimeError("residual checkpoint could not be loaded: " + str(cls.residual_service.status()))
        if not cls.multiscale_service.ready:
            raise RuntimeError("multi-scale checkpoint could not be loaded: " + str(cls.multiscale_service.status()))

    def test_default_model_remains_track_cnn(self):
        self.assertEqual(self.service.status()["model_type"], "track")

    def test_multiscale_local_motion_checkpoint_serves_thirteen_point_requests(self):
        request = PredictionRequest(history=sample_history(13))
        result = self.multiscale_service.predict(
            [point.dict() for point in request.history], request.horizons_hours
        )
        response = PredictionResponse.parse_obj({**result, "typhoon_id": "test"})
        self.assertEqual(response.model_type, "multiscale_residual")
        self.assertEqual(response.coordinate_system, "local_motion_v1")
        self.assertEqual(response.input_window, 12)
        self.assertEqual(len(response.predictions), 6)
        self.assertEqual(response.uncertainty.status, "historical_calibration")
        self.assertAlmostEqual(response.predictions[0].location_radius_90_km, 153.36158137343907)

    def test_api_prediction_matches_loaded_checkpoint(self):
        history = sample_history()
        result = self.service.predict(history, [6, 12, 18, 24, 30, 36])
        tensor = torch.as_tensor(
            np.asarray([encoded_input(self.service, history)]),
            dtype=torch.float32,
            device=self.service._device,
        )
        with torch.no_grad():
            output = self.service._model(tensor)
        means = np.asarray(self.service._normalizer["mean"], dtype=np.float64)
        scales = np.asarray(self.service._normalizer["std"], dtype=np.float64)
        expected_track = output["track"][0].cpu().numpy() * scales[:2] + means[:2]
        expected_wind = output["wind"][0, :, 0].cpu().numpy() * scales[2] + means[2]
        expected_track[:, 0] %= 360.0
        expected_track[:, 1] = np.clip(expected_track[:, 1], -90.0, 90.0)
        expected_wind = np.maximum(expected_wind, 0.0)

        self.assertEqual(result["model_version"], self.service.status()["model_version"])
        self.assertEqual(result["horizons_hours"], [6, 12, 18, 24, 30, 36])
        self.assertEqual(len(result["predictions"]), 6)
        for index, prediction in enumerate(result["predictions"]):
            np.testing.assert_allclose(
                [prediction["lng"], prediction["lat"], prediction["speed_ms"]],
                [*expected_track[index], expected_wind[index]],
                atol=1e-4,
            )
            self.assertIsNone(prediction["p05"])
            self.assertIsNone(prediction["p95"])
        self.assertIn(result["uncertainty"]["status"], {"historical_calibration", "unavailable"})
        if result["uncertainty"]["status"] == "historical_calibration":
            self.assertGreater(result["predictions"][0]["location_radius_90_km"], 0)
            self.assertIsNotNone(result["predictions"][0]["uncertainty_region"])
        PredictionResponse(**result)

    def test_uncertainty_is_not_reused_for_a_different_checkpoint(self):
        service = ModelService(self.residual_checkpoint_path, uncertainty_path=self.uncertainty_path)
        self.assertTrue(service.ready)
        result = service.predict(sample_history(), [6])
        self.assertEqual(result["uncertainty"]["status"], "unavailable")
        self.assertIsNone(result["predictions"][0]["location_radius_90_km"])

    def test_request_rejects_wrong_window_spacing_and_horizons(self):
        history = sample_history()
        with self.assertRaises(ValidationError):
            PredictionRequest(history=history[:-1])

        irregular = [dict(point) for point in history]
        irregular[-1]["time"] = (datetime.fromisoformat(irregular[-1]["time"]) + timedelta(hours=1)).isoformat()
        with self.assertRaises(ValidationError):
            PredictionRequest(history=irregular)

        with self.assertRaises(ValidationError):
            PredictionRequest(history=history, horizons_hours=[24, 48, 72, 96, 120, 144])

    def test_can_return_supported_horizon_subset(self):
        result = self.service.predict(sample_history(), [12, 24, 36])
        self.assertEqual(result["horizons_hours"], [12, 24, 36])
        self.assertEqual([item["lead_hours"] for item in result["predictions"]], [12, 24, 36])

    def test_residual_checkpoint_returns_finite_predictions(self):
        result = self.residual_service.predict(sample_history(), [6, 12, 18, 24, 30, 36])
        self.assertEqual(result["model_version"], "track-cnn-residual-v1")
        self.assertEqual(result["model_type"], "residual")
        for prediction in result["predictions"]:
            self.assertTrue(all(np.isfinite(prediction[key]) for key in ("lng", "lat", "speed_ms")))


if __name__ == "__main__":
    unittest.main()
