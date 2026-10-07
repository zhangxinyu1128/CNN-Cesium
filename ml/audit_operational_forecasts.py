"""Audit whether embedded forecast points can be fairly verified on held-out storms."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ml.data_audit import PROJECT_ROOT, number
from ml.prepare_dataset import linear_value


SUPPORTED_LEADS = {6, 12, 18, 24, 30, 36}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def make_series(points: List[Dict[str, Any]]) -> Dict[str, List[Tuple[datetime, float]]]:
    series: Dict[str, List[Tuple[datetime, float]]] = {key: [] for key in ("lng", "lat", "speed", "power")}
    for point in points:
        timestamp = parse_datetime(point.get("time"))
        if timestamp is None:
            continue
        for key in series:
            value = number(point.get(key))
            if value is not None:
                series[key].append((timestamp, value % 360.0 if key == "lng" else value))
    for values in series.values():
        values.sort(key=lambda item: item[0])
    return series


def interpolate(series: Dict[str, List[Tuple[datetime, float]]], key: str, timestamp: datetime) -> Optional[float]:
    return linear_value(
        series[key], timestamp, cyclic_longitude=(key == "lng"), maximum_gap_hours=9.0
    )


def history_available(series: Dict[str, List[Tuple[datetime, float]]], origin: datetime) -> bool:
    for hours_before in (24, 18, 12, 6, 0):
        timestamp = origin - timedelta(hours=hours_before)
        if any(interpolate(series, key, timestamp) is None for key in ("lng", "lat", "speed", "power")):
            return False
    return True


def audit(data_root: Path, split_file: Path) -> Dict[str, Any]:
    split_payload = read_json(split_file)
    assignments = split_payload["assignments"]
    test_ids = {storm_id for storm_id, split in assignments.items() if split == "test"}
    all_origins = 0
    all_forecast_points = 0
    test_origins = 0
    test_forecast_points = 0
    test_history_ready_origins = 0
    supported_lead_points = 0
    truth_covered_points = 0
    comparable_points = 0
    by_lead: Counter = Counter()
    by_source: Counter = Counter()
    all_sources: Counter = Counter()

    for path in sorted((data_root / "typhoon").glob("*.json")):
        try:
            records = read_json(path)
        except (OSError, ValueError):
            continue
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            storm_id = str(record.get("tfbh") or path.stem)
            points = record.get("points") or []
            series = make_series(points)
            for origin_point in points:
                forecasts = origin_point.get("forecast") or []
                if not forecasts:
                    continue
                all_origins += 1
                origin = parse_datetime(origin_point.get("time"))
                nested = [
                    (str(source.get("sets") or "unknown"), target)
                    for source in forecasts if isinstance(source, dict)
                    for target in (source.get("points") or []) if isinstance(target, dict)
                ]
                all_forecast_points += len(nested)
                all_sources.update(source_name for source_name, _ in nested)
                if storm_id not in test_ids:
                    continue
                test_origins += 1
                test_forecast_points += len(nested)
                if origin is None:
                    continue
                has_history = history_available(series, origin)
                if has_history:
                    test_history_ready_origins += 1
                for source_name, target in nested:
                    target_time = parse_datetime(target.get("time"))
                    if target_time is None:
                        continue
                    lead = (target_time - origin).total_seconds() / 3600.0
                    if lead not in SUPPORTED_LEADS:
                        continue
                    supported_lead_points += 1
                    truth = [interpolate(series, key, target_time) for key in ("lng", "lat")]
                    if any(value is None for value in truth):
                        continue
                    truth_covered_points += 1
                    if has_history:
                        comparable_points += 1
                        by_lead[str(int(lead))] += 1
                        by_source[source_name] += 1

    return {
        "dataset_fingerprint_sha256": read_json(data_root / "processed" / "manifest.json")["source"]["fingerprint_sha256"],
        "split_file_sha256": hashlib.sha256(split_file.read_bytes()).hexdigest(),
        "counts": {
            "all_forecast_origins": all_origins,
            "all_forecast_positions": all_forecast_points,
            "test_forecast_origins": test_origins,
            "test_forecast_positions": test_forecast_points,
            "test_origins_with_model_history": test_history_ready_origins,
            "test_positions_with_supported_lead": supported_lead_points,
            "test_positions_with_best_track_verification": truth_covered_points,
            "comparable_positions": comparable_points,
        },
        "comparable_by_lead_hours": dict(sorted(by_lead.items(), key=lambda item: int(item[0]))),
        "comparable_by_source_label": dict(sorted(by_source.items())),
        "all_forecast_positions_by_source_label": dict(sorted(all_sources.items())),
        "comparison_policy": {
            "test_storms_only": True,
            "history_times": "origin minus 24, 18, 12, 6, and 0 hours; linear interpolation only across gaps <= 9h",
            "verification": "forecast valid time must have interpolated best-track longitude and latitude across a gap <= 9h",
            "supported_model_leads_hours": sorted(SUPPORTED_LEADS),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--split-file", type=Path, default=PROJECT_ROOT / "data" / "splits" / "storm_splits.json")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = audit(args.data_root, args.split_file)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
