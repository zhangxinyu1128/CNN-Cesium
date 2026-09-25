"""Resample best-track data and build storm-disjoint CNN windows."""

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ml.data_audit import PROJECT_ROOT, SPLIT_RANGES, audit, number, parse_time, track_year


FEATURES = ("lng", "lat", "speed", "power", "delta_lng", "delta_lat")
TARGETS = ("lng", "lat", "speed")
INPUT_STEPS = 4
TARGET_STEPS = 6


def linear_value(
    values: Sequence[Tuple[datetime, float]],
    target_time: datetime,
    cyclic_longitude: bool = False,
    maximum_gap_hours: Optional[float] = None,
) -> Optional[float]:
    for index, (left_time, left_value) in enumerate(values):
        if target_time == left_time:
            return left_value
        if index + 1 >= len(values):
            continue
        right_time, right_value = values[index + 1]
        if left_time < target_time <= right_time:
            duration = (right_time - left_time).total_seconds()
            if duration <= 0:
                return None
            if maximum_gap_hours is not None and duration > maximum_gap_hours * 3600:
                return None
            ratio = (target_time - left_time).total_seconds() / duration
            delta = right_value - left_value
            if cyclic_longitude:
                delta = (delta + 180.0) % 360.0 - 180.0
            value = left_value + ratio * delta
            return value % 360.0 if cyclic_longitude else value
    return None


def split_points(points: List[Dict[str, Any]], gap_hours: float) -> List[List[Dict[str, Any]]]:
    segments: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    max_gap = timedelta(hours=gap_hours)
    for point in points:
        if current and point["time"] - current[-1]["time"] > max_gap:
            if len(current) >= 2:
                segments.append(current)
            current = []
        current.append(point)
    if len(current) >= 2:
        segments.append(current)
    return segments


def resample_segment(
    points: List[Dict[str, Any]], interval_hours: int, counters: Counter
) -> List[Dict[str, Any]]:
    fields = ("lng", "lat", "speed", "power")
    values: Dict[str, List[Tuple[datetime, float]]] = {
        field: [
            (point["time"], point[field])
            for point in points
            if point.get(field) is not None
        ]
        for field in fields
    }
    start = points[0]["time"]
    end = points[-1]["time"]
    step = timedelta(hours=interval_hours)
    count = int((end - start).total_seconds() // step.total_seconds()) + 1
    result: List[Dict[str, Any]] = []
    for index in range(count):
        timestamp = start + index * step
        row: Dict[str, Any] = {"time": timestamp}
        row["lng"] = linear_value(values["lng"], timestamp, True, interval_hours * 1.5)
        row["lat"] = linear_value(values["lat"], timestamp, maximum_gap_hours=interval_hours * 1.5)
        row["speed"] = linear_value(values["speed"], timestamp, maximum_gap_hours=interval_hours * 1.5)
        row["power"] = linear_value(values["power"], timestamp, maximum_gap_hours=interval_hours * 1.5)
        if any(row[field] is None for field in fields):
            counters["unusable_resampled_points"] += 1
            row["usable"] = False
        else:
            row["usable"] = True
            row["lng"] %= 360.0
            counters["resampled_points"] += 1
        result.append(row)
    return result


def feature_rows(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for previous, point in zip(points, points[1:]):
        delta_lng = (point["lng"] - previous["lng"] + 180.0) % 360.0 - 180.0
        rows.append(
            {
                "time": point["time"],
                "features": [
                    point["lng"],
                    point["lat"],
                    point["speed"],
                    point["power"],
                    delta_lng,
                    point["lat"] - previous["lat"],
                ],
                "target": [point["lng"], point["lat"], point["speed"]],
            }
        )
    return rows


def load_track(path: Path, counters: Counter) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            records = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        counters["invalid_tracks"] += 1
        return None
    if not isinstance(records, list):
        counters["invalid_tracks"] += 1
        return None
    record = next((item for item in records if isinstance(item, dict)), None)
    if record is None:
        counters["empty_tracks"] += 1
        return None

    identifier = str(record.get("tfbh") or path.stem)
    points: List[Dict[str, Any]] = []
    for source in record.get("points", []):
        if not isinstance(source, dict):
            counters["invalid_points"] += 1
            continue
        timestamp = parse_time(source.get("time"))
        lng = number(source.get("lng"))
        lat = number(source.get("lat"))
        if timestamp is None or lng is None or lat is None or not -180 <= lng <= 360 or not -90 <= lat <= 90:
            counters["invalid_points"] += 1
            continue
        speed = number(source.get("speed"))
        power = number(source.get("power"))
        points.append(
            {
                "time": timestamp,
                "lng": lng % 360.0,
                "lat": lat,
                "speed": speed,
                "power": power,
            }
        )

    points.sort(key=lambda item: item["time"])
    unique_points: List[Dict[str, Any]] = []
    for point in points:
        if unique_points and point["time"] == unique_points[-1]["time"]:
            counters["duplicate_points"] += 1
            unique_points[-1] = point
        else:
            unique_points.append(point)
    return identifier, unique_points


def build_samples(
    data_root: Path,
    gap_hours: float,
    interval_hours: int,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[List[float]]], Counter, Dict[str, set]]:
    split_samples: Dict[str, List[Dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    train_features: List[List[float]] = []
    counters: Counter = Counter()
    counters["windows"] = Counter()
    storms_with_samples: Dict[str, set] = {name: set() for name in split_samples}

    for path in sorted((data_root / "typhoon").glob("*.json")):
        loaded = load_track(path, counters)
        if loaded is None:
            continue
        identifier, points = loaded
        year = track_year(identifier)
        split = next(
            (name for name, (start, end) in SPLIT_RANGES.items() if year is not None and start <= year <= end),
            None,
        )
        if split is None:
            counters["outside_split_years"] += 1
            continue
        counters["valid_tracks"] += 1

        for segment in split_points(points, gap_hours):
            counters["source_segments"] += 1
            sampled = resample_segment(segment, interval_hours, counters)
            block: List[Dict[str, Any]] = []
            for point in sampled:
                if not point["usable"]:
                    if block:
                        rows = feature_rows(block)
                        if split == "train":
                            train_features.extend(row["features"] for row in rows)
                        counters["usable_segments"] += 1
                        _append_segment_samples(identifier, split, rows, split_samples, storms_with_samples, counters)
                        block = []
                    continue
                block.append(point)
            if block:
                rows = feature_rows(block)
                if split == "train":
                    train_features.extend(row["features"] for row in rows)
                counters["usable_segments"] += 1
                _append_segment_samples(identifier, split, rows, split_samples, storms_with_samples, counters)

    return split_samples, {"train": train_features}, counters, storms_with_samples


def _append_segment_samples(
    identifier: str,
    split: str,
    rows: List[Dict[str, Any]],
    split_samples: Dict[str, List[Dict[str, Any]]],
    storms_with_samples: Dict[str, set],
    counters: Counter,
) -> None:
    max_start = len(rows) - INPUT_STEPS - TARGET_STEPS
    if max_start < 1:
        counters["segments_too_short"] += 1
        return
    for start in range(1, max_start + 1):
        history = rows[start : start + INPUT_STEPS]
        future = rows[start + INPUT_STEPS : start + INPUT_STEPS + TARGET_STEPS]
        split_samples[split].append(
            {
                "typhoon_id": identifier,
                "history_times": [item["time"].isoformat() for item in history],
                "target_times": [item["time"].isoformat() for item in future],
                "x_raw": [item["features"] for item in history],
                "y_raw": [item["target"] for item in future],
            }
        )
        storms_with_samples[split].add(identifier)
        counters["windows"][split] += 1


def fit_scaler(rows: List[List[float]]) -> Dict[str, List[float]]:
    if not rows:
        raise ValueError("no training feature rows were produced")
    columns = list(zip(*rows))
    means = [math.fsum(column) / len(column) for column in columns]
    scales = []
    for column, mean in zip(columns, means):
        variance = math.fsum((value - mean) ** 2 for value in column) / len(column)
        scale = math.sqrt(variance)
        scales.append(scale if scale > 1e-8 else 1.0)
    return {"mean": means, "std": scales}


def normalize_samples(
    samples: List[Dict[str, Any]], scaler: Dict[str, List[float]]
) -> List[Dict[str, Any]]:
    feature_means = scaler["mean"]
    feature_stds = scaler["std"]
    target_indexes = (0, 1, 2)
    result = []
    for sample in samples:
        x = [
            [(value - feature_means[index]) / feature_stds[index] for index, value in enumerate(row)]
            for row in sample.pop("x_raw")
        ]
        y_raw = sample.pop("y_raw")
        y = [
            [
                (value - feature_means[index]) / feature_stds[index]
                for index, value in zip(target_indexes, row)
            ]
            for row in y_raw
        ]
        result.append({**sample, "x": x, "y": y, "target_raw": y_raw})
    return result


def validate_samples(
    split_samples: Dict[str, List[Dict[str, Any]]], interval_hours: int
) -> None:
    owners: Dict[str, str] = {}
    expected_step = timedelta(hours=interval_hours)
    for split, samples in split_samples.items():
        for sample in samples:
            identifier = sample["typhoon_id"]
            previous_owner = owners.setdefault(identifier, split)
            if previous_owner != split:
                raise ValueError("a typhoon appears in more than one data split: " + identifier)
            if len(sample["x_raw"]) != INPUT_STEPS or any(
                len(row) != len(FEATURES) for row in sample["x_raw"]
            ):
                raise ValueError("invalid model input shape for " + identifier)
            if len(sample["y_raw"]) != TARGET_STEPS or any(
                len(row) != len(TARGETS) for row in sample["y_raw"]
            ):
                raise ValueError("invalid model target shape for " + identifier)
            times = [
                datetime.fromisoformat(value)
                for value in sample["history_times"] + sample["target_times"]
            ]
            if any(right - left != expected_step for left, right in zip(times, times[1:])):
                raise ValueError("non-uniform six-hour sample window for " + identifier)


def write_jsonl(path: Path, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temporary.replace(path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"file": path.name, "samples": len(records), "bytes": path.stat().st_size, "sha256": file_hash}


def prepare(
    data_root: Path,
    output_root: Path,
    interval_hours: int,
    max_gap_hours: float,
    seed: int,
) -> Dict[str, Any]:
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    if max_gap_hours < interval_hours or max_gap_hours > 24:
        raise ValueError("max_gap_hours must be between interval_hours and 24")

    manifest = audit(data_root, seed)
    split_samples, training_rows, counters, storms = build_samples(
        data_root, max_gap_hours, interval_hours
    )
    validate_samples(split_samples, interval_hours)
    scaler = fit_scaler(training_rows["train"])
    output_root.mkdir(parents=True, exist_ok=True)
    outputs = []
    for split, samples in split_samples.items():
        normalized = normalize_samples(samples, scaler)
        for sample in normalized:
            values = [value for row in sample["x"] + sample["y"] for value in row]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("normalized sample contains a non-finite value")
        outputs.append(write_jsonl(output_root / (split + ".jsonl"), normalized))

    manifest["preprocessing"] = {
        "input_steps": INPUT_STEPS,
        "input_features": list(FEATURES),
        "target_steps": TARGET_STEPS,
        "target_features": list(TARGETS),
        "target_interval_hours": interval_hours,
        "maximum_interpolation_gap_hours": max_gap_hours,
        "interpolation": "linear for speed, power and latitude; shortest-arc linear for longitude",
        "normalization": "population mean and standard deviation fit on unique resampled training points only",
        "empty_and_invalid_tracks": counters["empty_tracks"] + counters["invalid_tracks"],
        "invalid_points_skipped": counters["invalid_points"],
        "duplicate_points_removed": counters["duplicate_points"],
        "source_segments": counters["source_segments"],
        "usable_segments": counters["usable_segments"],
        "segments_too_short": counters["segments_too_short"],
        "unusable_resampled_points": counters["unusable_resampled_points"],
        "resampled_points": counters["resampled_points"],
        "valid_tracks": counters["valid_tracks"],
        "normalizer": {
            "feature_names": list(FEATURES),
            "mean": scaler["mean"],
            "std": scaler["std"],
            "target_feature_indexes": [0, 1, 2],
        },
        "split_track_counts": {name: len(ids) for name, ids in storms.items()},
        "split_sample_counts": {name: len(samples) for name, samples in split_samples.items()},
        "sample_files": outputs,
        "source_track_splits": {
            name: list(years) for name, years in SPLIT_RANGES.items()
        },
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest["preprocessing_policy"]["time_resampling"] = (
        "linear interpolation to a six-hour cadence within segments; "
        "segments split when a source gap exceeds the configured limit"
    )
    manifest_path = output_root / "manifest.json"
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "processed")
    parser.add_argument("--interval-hours", type=int, default=6)
    parser.add_argument("--max-gap-hours", type=float, default=9.0)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    manifest = prepare(
        args.data_root,
        args.output_root,
        args.interval_hours,
        args.max_gap_hours,
        args.seed,
    )
    print(json.dumps(manifest["preprocessing"], ensure_ascii=False, indent=2))
    print("Manifest:", (args.output_root / "manifest.json").resolve())


if __name__ == "__main__":
    main()
