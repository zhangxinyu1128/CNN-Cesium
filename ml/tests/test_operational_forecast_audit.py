import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from ml.audit_operational_forecasts import audit


class OperationalForecastAuditTests(unittest.TestCase):
    def test_counts_only_forecasts_with_history_and_best_track_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "typhoon").mkdir()
            (root / "processed").mkdir()
            origin = datetime(2024, 1, 2)
            points = []
            for offset in range(-24, 7, 6):
                timestamp = origin + timedelta(hours=offset)
                points.append(
                    {
                        "time": timestamp.isoformat(),
                        "lng": 120.0 + offset / 100,
                        "lat": 20.0 + offset / 100,
                        "speed": 20.0,
                        "power": 10.0,
                        "forecast": [],
                    }
                )
            points[4]["forecast"] = [
                {
                    "sets": "fixture-provider",
                    "points": [
                        {
                            "time": (origin + timedelta(hours=6)).isoformat(),
                            "lng": 120.1,
                            "lat": 20.1,
                        }
                    ],
                }
            ]
            (root / "typhoon" / "202401.json").write_text(
                json.dumps([{"tfbh": "202401", "points": points}]), encoding="utf-8"
            )
            (root / "processed" / "manifest.json").write_text(
                json.dumps({"source": {"fingerprint_sha256": "fixture"}}), encoding="utf-8"
            )
            split_file = root / "storm_splits.json"
            split_file.write_text(
                json.dumps({"assignments": {"202401": "test"}}), encoding="utf-8"
            )

            result = audit(root, split_file)

        self.assertEqual(result["counts"]["test_forecast_origins"], 1)
        self.assertEqual(result["counts"]["test_forecast_positions"], 1)
        self.assertEqual(result["counts"]["test_origins_with_model_history"], 1)
        self.assertEqual(result["counts"]["comparable_positions"], 1)
        self.assertEqual(result["comparable_by_lead_hours"], {"6": 1})


if __name__ == "__main__":
    unittest.main()
