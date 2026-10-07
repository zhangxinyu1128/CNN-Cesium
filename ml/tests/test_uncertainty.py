import unittest

import numpy as np

from ml.evaluate_uncertainty import conformal_group_quantile, storm_coverage


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


if __name__ == "__main__":
    unittest.main()
