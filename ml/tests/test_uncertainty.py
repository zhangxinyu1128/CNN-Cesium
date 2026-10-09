import unittest

import numpy as np

from ml.evaluate_uncertainty import conformal_group_quantile, storm_coverage
from ml.uncertainty_regions import calibrate_location_ellipse, ellipse_metrics, location_energy_score


class ClusteredConformalTests(unittest.TestCase):
    def test_quantile_uses_per_storm_maximum_and_finite_sample_rank(self):
        scores = np.asarray([1.0, 100.0, 2.0, 3.0])
        groups = ["storm-a", "storm-a", "storm-b", "storm-c"]
        self.assertEqual(conformal_group_quantile(scores, groups, 0.75), 100.0)

    def test_coverage_requires_every_window_in_a_storm_to_be_covered(self):
        covered = np.asarray([True, False, True, True])
        groups = ["storm-a", "storm-a", "storm-b", "storm-c"]
        self.assertAlmostEqual(storm_coverage(covered, groups), 2.0 / 3.0)

    def test_quantile_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            conformal_group_quantile(np.asarray([]), [], 0.9)
        with self.assertRaises(ValueError):
            conformal_group_quantile(np.asarray([1.0]), ["storm-a", "storm-b"], 0.9)

    def test_location_ellipse_uses_directional_storm_group_calibration(self):
        center = np.zeros((6, 2), dtype=np.float64)
        actual = np.asarray([
            [0.1, 0.0], [-0.1, 0.0], [0.2, 0.01], [-0.2, -0.01], [0.05, 0.02], [-0.05, -0.02]
        ])
        groups = ["a", "a", "b", "b", "c", "c"]
        calibration, _ = calibrate_location_ellipse(center, actual, groups, 0.75)
        metrics = ellipse_metrics(center, actual, groups, calibration)
        self.assertEqual(calibration["geometry"], "conformal_ellipse")
        self.assertGreater(calibration["semi_major_axis_km"], calibration["semi_minor_axis_km"])
        self.assertGreaterEqual(metrics["location_coverage_90"], 0.75)
        self.assertGreater(calibration["area_km2"], 0)

    def test_location_energy_score_is_finite_and_zero_for_exact_ensemble(self):
        draws = np.zeros((3, 5, 2), dtype=np.float64)
        actual = np.zeros((3, 2), dtype=np.float64)
        self.assertAlmostEqual(location_energy_score(draws, actual), 0.0)


if __name__ == "__main__":
    unittest.main()
