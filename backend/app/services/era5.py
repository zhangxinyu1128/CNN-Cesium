"""Read track-aligned ERA5 wind samples used by the first fusion dashboard."""

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def _time_key(value: Any) -> str:
    return str(value or "").replace("Z", "")[:19]


def _wind_summary(value: Dict[str, Any]) -> Dict[str, float]:
    u = float(value.get("u", 0.0))
    v = float(value.get("v", 0.0))
    speed = math.hypot(u, v)
    direction = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
    return {
        "u": u,
        "v": v,
        "speed_ms": speed,
        "direction_deg": direction,
    }


class Era5Repository:
    """Index the small track-aligned JSONL products, not the raw GRIB files."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._rows: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None

    def _load(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if self._rows is not None:
            return self._rows

        rows: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        sources = [
            self.root / "track_points_2025.jsonl",
            self.root / "historical_500_850_refresh_20261002" / "point_winds_500_850.jsonl",
        ]
        for source in sources:
            if not source.is_file():
                continue
            with source.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    typhoon_id = str(row.get("typhoon_id") or "")
                    time = _time_key(row.get("time"))
                    if not typhoon_id or not time:
                        continue
                    # Prefer the 2025 product because it has the complete 200-850 hPa profile.
                    existing = rows[typhoon_id].get(time)
                    if existing is None or len(row.get("wind", {})) > len(existing.get("wind", {})):
                        rows[typhoon_id][time] = row

        self._rows = rows
        return rows

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        wind = row.get("wind") or {}
        levels: Dict[str, Dict[str, float]] = {}
        for level, values in wind.items():
            if not isinstance(values, dict):
                continue
            try:
                levels[str(level)] = _wind_summary(values)
            except (TypeError, ValueError):
                continue

        shear = None
        if "500" in levels and "850" in levels:
            shear = math.hypot(
                levels["500"]["u"] - levels["850"]["u"],
                levels["500"]["v"] - levels["850"]["v"],
            )
        return {
            "time": _time_key(row.get("time")),
            "lng": row.get("lng"),
            "lat": row.get("lat"),
            "era5_time_utc": _time_key(row.get("era5_time_utc") or row.get("time")),
            "era5_age_hours": row.get("era5_age_hours", 0.0),
            "levels": levels,
            "shear_500_850_ms": shear,
        }

    def get_typhoon(self, typhoon_id: str) -> Dict[str, Any]:
        rows = self._load().get(str(typhoon_id), {})
        points = [self._normalize_row(rows[key]) for key in sorted(rows)]
        levels = sorted(
            {int(level) for point in points for level in point["levels"]},
            reverse=True,
        )
        return {
            "typhoon_id": str(typhoon_id),
            "status": "ready" if points else "unavailable",
            "source": "ERA5 pressure-level wind samples aligned to the track",
            "levels_hpa": levels,
            "matched_points": len(points),
            "points": points,
        }
