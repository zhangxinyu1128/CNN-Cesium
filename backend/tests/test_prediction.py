"""Integration tests for the checkpoint-backed multi-horizon predictor."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch
from pydantic import ValidationError

from backend.app.schemas import PredictionRequest, PredictionResponse
from backend.app.services.model import ModelService
from ml.models.residual_cnn import ResidualTrackCNN
from ml.models.track_cnn import TrackCNN


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PredictionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_root = PROJECT_ROOT / "data" / "processed"
        cls.manifest = json.loads((cls.data_root / "manifest.json").read_text(encoding="utf-8"))
        with (cls.data_root / "test.jsonl").open("r", encoding="utf-8") as handle:
            cls.sample = json.loads(handle.readline())
        cls.checkpoint_path = PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth"
        cls.residual_checkpoint_path = PROJECT_ROOT / "artifacts" / "residual" / "checkpoints" / "track_cnn_residual.pth"
        cls.uncertainty_path = PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_20261008.json"
        cls.joint_uncertainty_path = PROJECT_ROOT / "artifacts" / "reports" / "stage4_extensions_20261008.json"
        cls.service = ModelService(
            cls.checkpoint_path,
            uncertainty_path=cls.uncertainty_path,
            joint_uncertainty_path=cls.joint_uncertainty_path,
        )
        cls.residual_service = ModelService(cls.residual_checkpoint_path)
        if not cls.service.ready:
            raise RuntimeError("trained checkpoint could not be loaded: " + str(cls.service.status()))
        if not cls.residual_service.ready:
            raise RuntimeError("residual checkpoint could not be loaded: " + str(cls.residual_service.status()))

    def test_default_model_remains_track_cnn(self):
        self.assertEqual(self.service.status()["model_type"], "track")

    def _history(self):
        scaler = self.manifest["preprocessing"]["normalizer"]
        means = np.asarray(scaler["mean"], dtype=np.float64)
        scales = np.asarray(scaler["std"], dtype=np.float64)
        features = np.asarray(self.sample["x"], dtype=np.float64) * scales + means
        times = [datetime.fromisoformat(value) for value in self.sample["history_times"]]
        first = features[0]
        history = [
            {
                "time": (times[0] - timedelta(hours=6)).isoformat(),
                "lng": float((first[0] - first[4]) % 360.0),
                "lat": float(first[1] - first[5]),
                "speed": float(first[2]),
                "power": float(first[3]),
            }
        ]
        for index, timestamp in enumerate(times):
            row = features[index]
            history.append(
                {
                    "time": timestamp.isoformat(),
                    "lng": float(row[0]),
                    "lat": float(row[1]),
                    "speed": float(row[2]),
                    "power": float(row[3]),
                }
            )
        return history

    def test_api_preprocessing_matches_saved_test_window(self):
        result = self.service.predict(self._history(), [6, 12, 18, 24, 30, 36])
        checkpoint = torch.load(self.checkpoint_path, map_location=self.service._device, weights_only=False)
        model = TrackCNN(input_features=6, horizons=6, dropout=0.3).to(self.service._device)
        model.load_state_dict(checkpoint["model_state_dict"])
        model.eval()
        with torch.no_grad():
            output = model(
                torch.tensor([self.sample["x"]], dtype=torch.float32, device=self.service._device)
            )

        scaler = self.manifest["preprocessing"]["normalizer"]
        means = np.asarray(scaler["mean"], dtype=np.float64)
        scales = np.asarray(scaler["std"], dtype=np.float64)
        expected_track = output["track"][0].cpu().numpy() * scales[:2] + means[:2]
        expected_wind = output["wind"][0, :, 0].cpu().numpy() * scales[2] + means[2]
        expected_track[:, 0] %= 360.0
        expected_track[:, 1] = np.clip(expected_track[:, 1], -90.0, 90.0)
        expected_wind = np.maximum(expected_wind, 0.0)

        self.assertEqual(result["horizons_hours"], [6, 12, 18, 24, 30, 36])
        self.assertEqual(len(result["predictions"]), 6)
        for index, prediction in enumerate(result["predictions"]):
            self.assertAlmostEqual(prediction["lng"], expected_track[index, 0], delta=1e-4)
            self.assertAlmostEqual(prediction["lat"], expected_track[index, 1], delta=1e-4)
            self.assertAlmostEqual(prediction["speed_ms"], expected_wind[index], delta=1e-4)
            self.assertIsNone(prediction["p05"])
            self.assertIsNone(prediction["p95"])
        prediction = result["predictions"][0]
        self.assertGreater(prediction["location_radius_90_km"], 0)
        self.assertEqual(prediction["uncertainty_region"]["geometry"], "conformal_ellipse")
        self.assertAlmostEqual(
            prediction["uncertainty_region"]["semi_major_axis_km"],
            560.932804339698,
        )
        self.assertGreater(prediction["uncertainty_region"]["semi_major_axis_km"], prediction["uncertainty_region"]["semi_minor_axis_km"])
        self.assertGreater(prediction["uncertainty_region"]["area_km2"], 0)
        self.assertTrue(0 <= prediction["uncertainty_region"]["bearing_deg"] < 180)
        self.assertEqual(prediction["speed_interval_source"]["unit"], "source_native")
        self.assertLess(prediction["speed_interval_source"]["lower"], prediction["speed_interval_source"]["upper"])
        self.assertEqual(result["uncertainty"]["status"], "historical_calibration")
        validated = PredictionResponse(**result)
        self.assertAlmostEqual(validated.predictions[0].location_radius_90_km, 483.9615881788603)
        self.assertEqual(validated.uncertainty.region_geometry, "conformal_ellipse")
        self.assertEqual(validated.uncertainty.joint_region_geometry, "location_ellipse_and_source_speed_interval")
        self.assertIn("不是实时预报保证", validated.uncertainty.region_note)

    def test_uncertainty_is_not_reused_for_a_different_checkpoint(self):
        service = ModelService(self.residual_checkpoint_path, uncertainty_path=self.uncertainty_path)
        self.assertTrue(service.ready)
        result = service.predict(self._history(), [6])
        self.assertEqual(result["uncertainty"]["status"], "unavailable")
        self.assertIsNone(result["predictions"][0]["location_radius_90_km"])

    def test_request_rejects_wrong_window_spacing_and_horizons(self):
        history = self._history()
        base = {"history": history}
        with self.assertRaises(ValidationError):
            PredictionRequest(**{**base, "history": history[:-1]})

        irregular = [dict(point) for point in history]
        final_time = datetime.fromisoformat(irregular[-1]["time"]) + timedelta(hours=1)
        irregular[-1]["time"] = final_time.isoformat()
        with self.assertRaises(ValidationError):
            PredictionRequest(**{**base, "history": irregular})

        with self.assertRaises(ValidationError):
            PredictionRequest(**{**base, "horizons_hours": [24, 48, 72, 96, 120, 144]})

    def test_can_return_supported_horizon_subset(self):
        result = self.service.predict(self._history(), [12, 24, 36])
        self.assertEqual(result["horizons_hours"], [12, 24, 36])
        self.assertEqual([item["lead_hours"] for item in result["predictions"]], [12, 24, 36])

    def test_residual_checkpoint_inference_matches_prior_plus_residual(self):
        service = self.residual_service
        result = service.predict(self._history(), [6, 12, 18, 24, 30, 36])
        checkpoint = torch.load(self.residual_checkpoint_path, map_location=service._device, weights_only=False)
        model = ResidualTrackCNN(input_features=6, horizons=6, dropout=0.3).to(service._device)
        model.load_state_dict(checkpoint["model_state_dict"])
        model.eval()
        features = torch.tensor([self.sample["x"]], dtype=torch.float32, device=service._device)
        scaler = self.manifest["preprocessing"]["normalizer"]
        means = torch.tensor(scaler["mean"], dtype=features.dtype, device=service._device)
        scales = torch.tensor(scaler["std"], dtype=features.dtype, device=service._device)
        with torch.no_grad():
            output = model(features)
            last = features[:, -1, :] * scales + means
            steps = torch.arange(1, 7, dtype=features.dtype, device=service._device).view(1, -1)
            prior = torch.zeros((1, 6, 3), dtype=features.dtype, device=service._device)
            prior[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
            prior[..., 1] = last[:, None, 1] + steps * last[:, None, 5]
            prior[..., 2] = last[:, None, 2]
            residual = torch.cat((output["track"], output["wind"]), dim=-1)
            expected = ((prior - means[:3]) / scales[:3] + residual)[0].cpu().numpy()
        expected = expected * np.asarray(scaler["std"][:3]) + np.asarray(scaler["mean"][:3])
        expected[:, 0] %= 360.0
        expected[:, 1] = np.clip(expected[:, 1], -90.0, 90.0)
        expected[:, 2] = np.maximum(expected[:, 2], 0.0)
        self.assertEqual(result["model_version"], "track-cnn-residual-v1")
        for index, prediction in enumerate(result["predictions"]):
            np.testing.assert_allclose(
                [prediction["lng"], prediction["lat"], prediction["speed_ms"]],
                expected[index],
                atol=1e-4,
            )


if __name__ == "__main__":
    unittest.main()
