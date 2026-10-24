import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import torch

from backend.app.services.model import ModelService
from ml.features.local_motion import destination_lonlat, encode_track
from ml.models.multiscale_residual_cnn import MultiScaleResidualCNN
from ml.models.residual_cnn import ResidualTrackCNN


class LocalMotionPredictionTests(unittest.TestCase):
    def test_local_motion_checkpoint_returns_geographic_forecasts(self):
        feature_names = [
            "east_km", "north_km", "speed", "power", "acceleration_ms_per_hour",
            "turn_rate_rad_per_hour", "heading_sin", "heading_cos",
        ]
        target_names = ["east_km", "north_km", "speed"]
        normalizer = {
            "coordinate_system": "local_motion_v1",
            "feature_names": feature_names,
            "mean": [0.0] * 8,
            "std": [1.0] * 8,
            "target_names": target_names,
            "target_mean": [0.0, 0.0, 20.0],
            "target_std": [100.0, 100.0, 10.0],
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "local-motion.pth"
            model = ResidualTrackCNN(input_features=8, horizons=6, dropout=0.0)
            for parameter in model.parameters():
                torch.nn.init.zeros_(parameter)
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "model_version": "local-motion-test",
                    "architecture": {
                        "type": "residual", "input_steps": 4, "input_features": 8,
                        "target_steps": 6, "target_features": 3, "dropout": 0.0,
                        "coordinate_system": "local_motion_v1",
                    },
                    "normalizer": normalizer,
                },
                checkpoint_path,
            )
            service = ModelService(checkpoint_path)
            self.assertTrue(service.ready, service.status())
            self.assertEqual(service.status()["coordinate_system"], "local_motion_v1")
            start = datetime(2025, 8, 1)
            history = [
                {"time": (start + timedelta(hours=6 * index)).isoformat(),
                 "lng": (359.0 + 0.5 * index) % 360.0, "lat": 18.0 + 0.2 * index,
                 "speed": 20.0, "power": 3.0}
                for index in range(5)
            ]
            prediction = service.predict(history, [6])
            self.assertEqual(prediction["model_version"], "local-motion-test")
            self.assertEqual(prediction["coordinate_system"], "local_motion_v1")
            self.assertEqual(len(prediction["predictions"]), 1)
            rows = encode_track(history)
            expected = destination_lonlat(
                history[-1], rows[-1]["features"][0], rows[-1]["features"][1]
            )
            actual = prediction["predictions"][0]
            self.assertAlmostEqual(actual["lng"], expected[0], places=5)
            self.assertAlmostEqual(actual["lat"], expected[1], places=5)

    def test_multiscale_checkpoint_accepts_thirteen_observations(self):
        feature_names = [
            "east_km", "north_km", "speed", "power", "acceleration_ms_per_hour",
            "turn_rate_rad_per_hour", "heading_sin", "heading_cos",
        ]
        normalizer = {
            "coordinate_system": "local_motion_v1",
            "feature_names": feature_names,
            "mean": [0.0] * 8,
            "std": [1.0] * 8,
            "target_names": ["east_km", "north_km", "speed"],
            "target_mean": [0.0, 0.0, 20.0],
            "target_std": [100.0, 100.0, 10.0],
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "multiscale-local-motion.pth"
            model = MultiScaleResidualCNN(input_features=8, horizons=6, dropout=0.0)
            for parameter in model.parameters():
                torch.nn.init.zeros_(parameter)
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "model_version": "multiscale-local-motion-test",
                    "architecture": {
                        "type": "multiscale_residual", "variant": "physics",
                        "input_steps": 12, "input_features": 8,
                        "target_steps": 6, "target_features": 3, "dropout": 0.0,
                        "coordinate_system": "local_motion_v1", "quantile": False,
                    },
                    "normalizer": normalizer,
                },
                checkpoint_path,
            )
            service = ModelService(checkpoint_path)
            self.assertTrue(service.ready, service.status())
            self.assertEqual(service.status()["input_steps"], 12)
            self.assertEqual(service.status()["history_points"], 13)
            start = datetime(2025, 8, 1)
            history = [
                {
                    "time": (start + timedelta(hours=6 * index)).isoformat(),
                    "lng": 128.0 + 0.5 * index,
                    "lat": 18.0 + 0.2 * index,
                    "speed": 20.0,
                    "power": 3.0,
                }
                for index in range(13)
            ]
            prediction = service.predict(history, [6, 12])
            self.assertEqual(prediction["input_window"], 12)
            self.assertEqual(prediction["coordinate_system"], "local_motion_v1")
            self.assertEqual([item["lead_hours"] for item in prediction["predictions"]], [6, 12])
            for item in prediction["predictions"]:
                self.assertTrue(0.0 <= item["lng"] < 360.0)
                self.assertTrue(-90.0 <= item["lat"] <= 90.0)
                self.assertIsNone(item["p10"])
                self.assertIsNone(item["p90"])
            missing_context_speed = [dict(point) for point in history]
            missing_context_speed[0]["speed"] = None
            with self.assertRaisesRegex(ValueError, "context speed"):
                service.predict(missing_context_speed, [6])


if __name__ == "__main__":
    unittest.main()
