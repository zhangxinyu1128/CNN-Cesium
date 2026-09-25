"""Audit the raw typhoon JSON dataset and write a reproducible manifest."""

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("time", "lng", "lat", "speed", "power", "pressure", "move_dir", "move_speed", "radius7", "radius10")
GRID_EXTENSIONS = {".nc", ".grib", ".grb", ".grb2"}
SPLIT_RANGES = {
    "train": (1945, 2016),
    "validation": (2017, 2019),
    "test": (2020, 2024),
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def update_file_hash(path: Path, root: Path, digest: Any) -> None:
    file_hash = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            file_hash.update(chunk)
    digest.update(path.relative_to(root).as_posix().encode("utf-8"))
    digest.update(file_hash.digest())


def track_year(identifier: str) -> Optional[int]:
    match = re.match(r"^(\d{4})", identifier)
    return int(match.group(1)) if match else None


def split_counts(identifiers: Iterable[str]) -> Dict[str, int]:
    counts = {name: 0 for name in SPLIT_RANGES}
    counts["outside_range"] = 0
    for identifier in identifiers:
        year = track_year(identifier)
        matched = False
        if year is not None:
            for name, (start, end) in SPLIT_RANGES.items():
                if start <= year <= end:
                    counts[name] += 1
                    matched = True
                    break
        if not matched:
            counts["outside_range"] += 1
    return counts


def audit(data_root: Path, seed: int) -> Dict[str, Any]:
    data_root = data_root.resolve()
    year_root = data_root / "year"
    typhoon_root = data_root / "typhoon"
    year_files = sorted(year_root.glob("*.json")) if year_root.exists() else []
    track_files = sorted(typhoon_root.glob("*.json")) if typhoon_root.exists() else []

    year_ids: List[str] = []
    year_errors: List[Dict[str, str]] = []
    for path in year_files:
        try:
            records = read_json(path)
            if not isinstance(records, list):
                raise ValueError("year index must be a JSON array")
            year_ids.extend(
                str(item.get("tfbh"))
                for item in records
                if isinstance(item, dict) and item.get("tfbh") is not None
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            year_errors.append({"file": path.name, "error": str(error)})

    digest = hashlib.sha256()
    field_missing: Counter = Counter()
    intervals: Counter = Counter()
    invalid_coordinates = 0
    duplicate_timestamps = 0
    non_monotonic_steps = 0
    point_count = 0
    valid_tracks: List[str] = []
    invalid_files: List[Dict[str, str]] = []
    duplicate_ids: List[str] = []
    filename_id_mismatches: List[Dict[str, str]] = []
    seen_ids = set()
    total_bytes = 0
    for path in year_files:
        total_bytes += path.stat().st_size
        update_file_hash(path, data_root, digest)

    for path in track_files:
        total_bytes += path.stat().st_size
        update_file_hash(path, data_root, digest)
        try:
            records = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            invalid_files.append({"file": path.name, "reason": str(error)})
            continue
        if not isinstance(records, list):
            invalid_files.append({"file": path.name, "reason": "top-level JSON value is not an array"})
            continue
        record = next((item for item in records if isinstance(item, dict)), None)
        if record is None:
            invalid_files.append({"file": path.name, "reason": "no track object"})
            continue

        identifier = str(record.get("tfbh") or path.stem)
        if identifier in seen_ids:
            duplicate_ids.append(identifier)
        seen_ids.add(identifier)
        if identifier != path.stem:
            filename_id_mismatches.append({"file": path.name, "tfbh": identifier})
        valid_tracks.append(path.stem)
        points = record.get("points")
        if not isinstance(points, list):
            invalid_files.append({"file": path.name, "reason": "points is not an array"})
            continue

        previous_time: Optional[datetime] = None
        track_times = set()
        for point in points:
            if not isinstance(point, dict):
                invalid_files.append({"file": path.name, "reason": "point is not an object"})
                continue
            point_count += 1
            for field in FIELDS:
                if point.get(field) is None:
                    field_missing[field] += 1

            lng = number(point.get("lng"))
            lat = number(point.get("lat"))
            if lng is None or lat is None or not -180 <= lng <= 360 or not -90 <= lat <= 90:
                invalid_coordinates += 1

            current_time = parse_time(point.get("time"))
            if current_time is None:
                invalid_files.append({"file": path.name, "reason": "invalid or missing point time"})
                continue
            if current_time in track_times:
                duplicate_timestamps += 1
            track_times.add(current_time)
            if previous_time is not None:
                hours = (current_time - previous_time).total_seconds() / 3600
                if hours <= 0:
                    non_monotonic_steps += 1
                else:
                    intervals[round(hours, 4)] += 1
            previous_time = current_time

    year_id_set = set(year_ids)
    track_id_set = {path.stem for path in track_files}
    missing_track_files = sorted(year_id_set - track_id_set)
    unindexed_tracks = sorted(track_id_set - year_id_set)

    random_ids = valid_tracks[:]
    random.Random(seed).shuffle(random_ids)
    train_end = int(len(random_ids) * 0.7)
    validation_end = train_end + int(len(random_ids) * 0.15)
    random_split = {
        "train": train_end,
        "validation": validation_end - train_end,
        "test": len(random_ids) - validation_end,
    }

    grid_files = [
        path for path in data_root.rglob("*")
        if path.is_file() and path.suffix.lower() in GRID_EXTENSIONS
    ]
    year_numbers = sorted(
        int(path.stem) for path in year_files if re.fullmatch(r"\d{4}", path.stem)
    )
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "root": str(data_root),
            "fingerprint_sha256": digest.hexdigest(),
            "bytes": total_bytes,
            "components": ["annual typhoon indexes", "best-track JSON files"],
            "gridded_meteorology_files": len(grid_files),
        },
        "coverage": {
            "year_files": len(year_files),
            "year_min": min(year_numbers) if year_numbers else None,
            "year_max": max(year_numbers) if year_numbers else None,
            "annual_index_rows": len(year_ids),
            "typhoon_files": len(track_files),
            "valid_tracks": len(valid_tracks),
            "point_count": point_count,
            "invalid_coordinate_points": invalid_coordinates,
            "duplicate_timestamps": duplicate_timestamps,
            "non_monotonic_steps": non_monotonic_steps,
        },
        "field_missing": {
            field: {
                "count": field_missing[field],
                "fraction": round(field_missing[field] / point_count, 8) if point_count else None,
            }
            for field in FIELDS
        },
        "interval_hours": {
            str(hours): count for hours, count in sorted(intervals.items())
        },
        "index_consistency": {
            "index_ids_without_track_file": missing_track_files,
            "track_files_without_index_row": unindexed_tracks,
            "duplicate_track_ids": duplicate_ids,
            "filename_id_mismatches": filename_id_mismatches,
        },
        "errors": {
            "invalid_track_files": invalid_files,
            "invalid_year_files": year_errors,
        },
        "splits": {
            "time_holdout_year_ranges": {
                key: list(value) for key, value in SPLIT_RANGES.items()
            },
            "time_holdout_track_counts": split_counts(valid_tracks),
            "random_track_split": {
                "seed": seed,
                "fractions": {"train": 0.7, "validation": 0.15, "test": 0.15},
                "counts": random_split,
            },
        },
        "preprocessing_policy": {
            "input_steps": 4,
            "input_features": ["lng", "lat", "speed", "power", "delta_lng", "delta_lat"],
            "target_steps": 6,
            "target_interval_hours": 6,
            "normalization": "fit on training storms only",
            "time_resampling": "not applied by this audit command",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "processed" / "manifest.json")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    manifest = audit(args.data_root, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["coverage"], ensure_ascii=False, indent=2))
    print("Manifest:", args.output.resolve())


if __name__ == "__main__":
    main()
