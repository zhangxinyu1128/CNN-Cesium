"""Tests for the shared-holdout trajectory model comparison summary."""

import unittest

from backend.app.main import _experiment_summary


class ExperimentSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = _experiment_summary()
        cls.trajectory = cls.summary["trajectory_comparison"]

    def test_innovation_models_share_a_frozen_holdout(self):
        self.assertEqual(self.trajectory["status"], "ready")
        self.assertEqual(self.trajectory["sample_count"], 914)
        self.assertEqual(self.trajectory["storm_count"], 73)
        self.assertEqual(
            {model["key"] for model in self.trajectory["models"]},
            {
                "track_cnn_baseline",
                "track_cnn_residual",
                "multiscale_local_motion",
                "innovation_physics_multiscale",
            },
        )

    def test_innovation_reports_seed_spread_and_bootstrap_evidence(self):
        innovation = next(
            model
            for model in self.trajectory["models"]
            if model["key"] == "innovation_physics_multiscale"
        )
        at_24_hours = next(row for row in innovation["by_horizon"] if row["lead_hours"] == 24)
        self.assertEqual(innovation["seed_count"], 3)
        self.assertAlmostEqual(at_24_hours["path_mae_km"], 184.0, places=0)
        self.assertGreater(at_24_hours["path_mae_km_std"], 0)

        statistics = self.trajectory["statistics"]
        self.assertEqual(statistics["resamples"], 20000)
        self.assertTrue(all(
            point["statistically_lower_at_95pct"]
            for point in statistics["innovation_vs_track_cnn_baseline"]
        ))
        six_hour_residual = next(
            point
            for point in statistics["innovation_vs_track_cnn_residual"]
            if point["lead_hours"] == 6
        )
        self.assertGreater(six_hour_residual["paired_storm_bootstrap_95ci_km"][0], 0)

    def test_era5_ablation_and_trajectory_holdout_stay_separate(self):
        self.assertEqual(self.summary["test_samples"], 1661)
        self.assertEqual(self.trajectory["sample_count"], 914)
        self.assertNotIn(
            "innovation_physics_multiscale",
            {model["key"] for model in self.summary["models"]},
        )

    def test_online_calibration_radii_remain_available_without_test_evaluation(self):
        uncertainty = self.summary["uncertainty"]
        self.assertEqual(uncertainty["status"], "ready")
        self.assertEqual(uncertainty["model_key"], "innovation_physics_multiscale")
        self.assertEqual(uncertainty["calibration_storms"], 22)
        self.assertEqual(
            [row["lead_hours"] for row in uncertainty["by_horizon"]],
            [6, 12, 18, 24, 30, 36],
        )
        at_24_hours = next(
            row for row in uncertainty["by_horizon"] if row["lead_hours"] == 24
        )
        self.assertAlmostEqual(at_24_hours["location_radius_90_km"], 653.9486, places=3)
        self.assertNotIn("location_coverage_90", at_24_hours)
        self.assertIsNone(uncertainty["evaluation_storms"])


if __name__ == "__main__":
    unittest.main()
