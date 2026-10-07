import unittest

import numpy as np

from ml.evaluate_storm_bootstrap import (
    assign_power_tertiles,
    bootstrap_mean_ci,
    storm_average,
    storm_power_tertiles,
)


class StormBootstrapTests(unittest.TestCase):
    def test_storm_average_gives_each_storm_one_row(self):
        errors = np.asarray([[1.0, 3.0], [3.0, 5.0], [10.0, 20.0]])
        ids, averages = storm_average(errors, ["a", "a", "b"])
        self.assertEqual(ids, ["a", "b"])
        np.testing.assert_allclose(averages, [[2.0, 4.0], [10.0, 20.0]])

    def test_bootstrap_ci_is_deterministic_and_positive_for_positive_pairs(self):
        values = np.asarray([1.0, 2.0, 3.0, 4.0])
        first = bootstrap_mean_ci(values, iterations=2000, seed=17)
        second = bootstrap_mean_ci(values, iterations=2000, seed=17)
        self.assertEqual(first, second)
        self.assertGreater(first[1], 0.0)
        self.assertAlmostEqual(first[0], 2.5)

    def test_bootstrap_rejects_single_cluster(self):
        with self.assertRaises(ValueError):
            bootstrap_mean_ci(np.asarray([1.0]), iterations=100, seed=1)

    def test_power_tertiles_use_storm_medians(self):
        power = np.asarray([1.0, 3.0, 2.0, 4.0, 9.0, 11.0, 10.0, 12.0, 18.0])
        ids = ["a", "a", "b", "b", "c", "c", "d", "d", "e"]
        thresholds = storm_power_tertiles(power, ids)
        self.assertAlmostEqual(thresholds[0], 5.3333333333)
        self.assertAlmostEqual(thresholds[1], 10.6666666667)
        np.testing.assert_array_equal(
            assign_power_tertiles(np.asarray([2.0, 7.0, 11.0]), thresholds),
            np.asarray([0, 1, 2]),
        )


if __name__ == "__main__":
    unittest.main()
