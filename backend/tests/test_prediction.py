"""Integration tests for the checkpoint-backed multi-horizon predictor."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch
from pydantic import ValidationError

from backend.app.schemas import PredictionRequest
from backend.app.services.model import ModelService
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
        cls.service = ModelService(cls.checkpoint_path)
        if not cls.service.ready:
            raise RuntimeError("trained checkpoint could not be loaded: " + str(cls.service.status()))

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


if __name__ == "__main__":
    unittest.main()
