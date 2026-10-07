import json
import tempfile
import unittest
from pathlib import Path

from ml.download_era5 import iter_jobs, request_for, storm_boxes


def point(time, lng, lat=12.0):
    return {"time": time, "lng": lng, "lat": lat}


class Era5RequestTests(unittest.TestCase):
    def test_antimeridian_track_uses_two_narrow_boxes(self):
        boxes = storm_boxes(
            [point("2025-08-01T00:00:00", 170), point("2025-08-01T06:00:00", -175)],
            margin=5,
        )
        self.assertEqual(boxes, [(17.0, -180.0, 7.0, -170.0), (17.0, 165.0, 7.0, 175.0)])

    def test_jobs_use_month_local_track_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "year").mkdir()
            (root / "typhoon").mkdir()
            (root / "year" / "2025.json").write_text(
                json.dumps([{"tfbh": "202501"}]), encoding="utf-8"
            )
            record = {
                "tfbh": "202501",
                "name": "TEST",
                "points": [
                    point("2025-01-01T00:00:00", 120),
                    point("2025-02-01T06:00:00", 140),
                ],
            }
            (root / "typhoon" / "202501.json").write_text(
                json.dumps([record]), encoding="utf-8"
            )
            jobs = list(iter_jobs(root, [2025], margin=1))

        self.assertEqual([job[3] for job in jobs], ["_202501", "_202502"])
        self.assertEqual(jobs[0][2], (13.0, 119.0, 11.0, 121.0))
        self.assertEqual(jobs[1][2], (13.0, 139.0, 11.0, 141.0))

    def test_request_requires_synoptic_time(self):
        with self.assertRaisesRegex(ValueError, "00/06/12/18 UTC"):
            request_for([point("2025-08-01T03:00:00", 130)], (20, 120, 10, 140))


if __name__ == "__main__":
    unittest.main()
