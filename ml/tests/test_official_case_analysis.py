import unittest

from ml.analyze_official_cases import bearing_deg, haversine_km, wall_clock_key


class OfficialCaseAnalysisTests(unittest.TestCase):
    def test_wall_clock_key_preserves_cycle_hour(self):
        self.assertEqual(wall_clock_key("2025-06-12T02:00:00+08:00"), "2025061202")
        self.assertEqual(wall_clock_key("2025-06-12T02:00:00"), "2025061202")

    def test_distance_and_bearing_are_geographic(self):
        self.assertAlmostEqual(haversine_km((0.0, 0.0), (1.0, 0.0)), 111.2, delta=0.5)
        self.assertAlmostEqual(bearing_deg((0.0, 0.0), (1.0, 0.0)), 90.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
