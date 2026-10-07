import unittest
from datetime import datetime
from types import SimpleNamespace

import numpy as np

from ml.align_era5_tracks import bilinear_value, brackets, interpolate_point, parse_datetime


class AlignEra5TrackTests(unittest.TestCase):
    def test_uses_latest_cycle_before_observation(self):
        lower, upper, fraction = brackets(datetime(2025, 7, 1, 5, 30))
        self.assertEqual(lower, datetime(2025, 7, 1, 0))
        self.assertEqual(upper, lower)
        self.assertEqual(fraction, 0.0)

    def test_exact_cycle_has_zero_age(self):
        lower, upper, fraction = brackets(datetime(2025, 7, 1, 6))
        self.assertEqual((lower, upper, fraction), (datetime(2025, 7, 1, 6), datetime(2025, 7, 1, 6), 0.0))

    def test_timezone_is_normalized_to_utc(self):
        self.assertEqual(parse_datetime("2025-07-01T09:00:00+09:00"), datetime(2025, 7, 1, 0))

    def test_bilinear_grid_sample(self):
        keys = {
            "Ni": 2,
            "Nj": 2,
            "jPointsAreConsecutive": 0,
            "longitudeOfFirstGridPointInDegrees": 100.0,
            "longitudeOfLastGridPointInDegrees": 101.0,
            "latitudeOfFirstGridPointInDegrees": 20.0,
            "latitudeOfLastGridPointInDegrees": 19.0,
        }
        fake_eccodes = SimpleNamespace(codes_get=lambda _message, key: keys[key])
        result = bilinear_value(object(), np.asarray([0.0, 1.0, 2.0, 3.0]), 100.5, 19.5, fake_eccodes)
        self.assertAlmostEqual(result, 1.5)

    def test_observation_wind_requires_all_four_fields(self):
        point = {
            "typhoon_id": "202501",
            "time": "2025-07-01T03:00:00",
            "lng": 120.0,
            "lat": 20.0,
            "timestamp": datetime(2025, 7, 1, 3),
            "lower": datetime(2025, 7, 1, 0),
            "upper": datetime(2025, 7, 1, 0),
            "fraction": 0.0,
        }
        key = "202507010"
        samples = {key + field: value for field, value in (("u500", 1.0), ("v500", 2.0), ("u850", 3.0), ("v850", 4.0))}
        row = interpolate_point(point, samples, 0)
        self.assertEqual(row["wind"]["500"], {"u": 1.0, "v": 2.0})
        self.assertEqual(row["wind"]["850"], {"u": 3.0, "v": 4.0})
        self.assertEqual(row["era5_age_hours"], 3.0)
        self.assertIsNone(interpolate_point(point, {key + "u500": 1.0}, 0))


if __name__ == "__main__":
    unittest.main()
