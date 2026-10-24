import math
import unittest
from datetime import datetime, timedelta

from ml.features.local_motion import destination_lonlat, displacement_km, encode_track
from ml.prepare_dataset import feature_rows, normalize_samples, _append_segment_samples
from collections import Counter


class LocalMotionTests(unittest.TestCase):
    def test_dateline_crossing_uses_shortest_eastward_path(self):
        east, north = displacement_km(
            {"lng": 359.0, "lat": 0.0}, {"lng": 1.0, "lat": 0.0}
        )
        self.assertAlmostEqual(east, 222.4, delta=1.0)
        self.assertAlmostEqual(north, 0.0, delta=0.01)

    def test_local_displacement_round_trips_to_geographic_coordinates(self):
        origin = {"lng": 179.5, "lat": 24.0}
        point = {"lng": 181.1, "lat": 25.2}
        east, north = displacement_km(origin, point)
        longitude, latitude = destination_lonlat(origin, east, north)
        self.assertAlmostEqual(longitude, point["lng"] % 360.0, places=6)
        self.assertAlmostEqual(latitude, point["lat"], places=6)

    def test_encoder_emits_motion_intensity_acceleration_and_heading(self):
        start = datetime(2025, 8, 1)
        points = [
            {"time": start + timedelta(hours=6 * index), "lng": 120 + 0.5 * index,
             "lat": 15 + 0.2 * index, "speed": 20 + 2 * index, "power": 3}
            for index in range(4)
        ]
        encoded = encode_track(points)
        self.assertEqual(len(encoded), 3)
        self.assertTrue(all(len(row["features"]) == 8 for row in encoded))
        self.assertAlmostEqual(encoded[0]["features"][4], 2 / 6)
        self.assertAlmostEqual(
            math.hypot(encoded[0]["features"][6], encoded[0]["features"][7]), 1.0
        )

    def test_dataset_windows_use_last_observation_as_local_origin(self):
        start = datetime(2025, 8, 1)
        points = [
            {"time": start + timedelta(hours=6 * index), "lng": 140 + 0.4 * index,
             "lat": 12 + 0.1 * index, "speed": 18 + index, "power": 2}
            for index in range(12)
        ]
        rows = feature_rows(points)
        samples = {"train": [], "validation": [], "test": []}
        storms = {"train": set(), "validation": set(), "test": set()}
        counters = Counter()
        counters["windows"] = Counter()
        _append_segment_samples("2025001", "train", rows, samples, storms, counters)
        self.assertTrue(samples["train"])
        raw = samples["train"][0]
        self.assertEqual(raw["origin"], rows[4]["position"])
        self.assertEqual(len(raw["x_raw"][0]), 8)
        self.assertEqual(raw["x_raw"][0][5], 0.0)
        self.assertEqual(len(raw["y_raw"][0]), 3)
        scaler = {"mean": [0.0] * 8, "std": [1.0] * 8}
        target_scaler = {"mean": [0.0, 0.0, 20.0], "std": [100.0, 100.0, 5.0]}
        normalized = normalize_samples([raw.copy()], scaler, target_scaler)[0]
        self.assertEqual(len(normalized["target_raw"][0]), 3)
        self.assertEqual(len(normalized["y"][0]), 3)


if __name__ == "__main__":
    unittest.main()
