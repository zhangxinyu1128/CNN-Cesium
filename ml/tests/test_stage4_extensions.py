import unittest

import numpy as np

from ml.evaluate_stage4_extensions import intensity_group, joint_location_speed_evaluation


class Stage4ExtensionTests(unittest.TestCase):
    def test_intensity_groups_follow_source_power_categories(self):
        self.assertEqual(intensity_group(6.0), "TD_TS")
        self.assertEqual(intensity_group(8.0), "TD_TS")
        self.assertEqual(intensity_group(10.0), "STS_TY")
        self.assertEqual(intensity_group(12.0), "STS_TY")
        self.assertEqual(intensity_group(14.0), "STY_PLUS")
        self.assertEqual(intensity_group(float("nan")), "unknown")

    def test_joint_calibration_contains_position_and_source_speed(self):
        validation_center = np.zeros((12, 2, 3), dtype=np.float64)
        validation_actual = validation_center.copy()
        validation_actual[:, :, 0] = np.linspace(-0.8, 0.8, 12)[:, None]
        validation_actual[:, :, 1] = np.linspace(-0.4, 0.4, 12)[:, None]
        validation_actual[:, :, 2] = np.linspace(-3.0, 3.0, 12)[:, None]
        test_center = np.zeros((3, 2, 3), dtype=np.float64)
        test_actual = test_center.copy()
        test_actual[:, :, 0] = np.asarray([-0.2, 0.0, 0.2])[:, None]
        test_actual[:, :, 2] = np.asarray([-1.0, 0.0, 1.0])[:, None]
        validation_ids = [f"storm-{index:02d}" for index in range(12)]
        test_ids = ["test-a", "test-b", "test-c"]

        result = joint_location_speed_evaluation(
            validation_center,
            validation_actual,
            validation_ids,
            test_center,
            test_actual,
            test_ids,
        )

        first = result["evaluation"][0]
        self.assertGreaterEqual(first["joint_window_coverage"], 0.0)
        self.assertGreater(first["speed_half_width_native"], 0.0)
        self.assertEqual(len(result["calibration_by_horizon"]), 2)


if __name__ == "__main__":
    unittest.main()
