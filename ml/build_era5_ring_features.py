"""Extract pre-observation ERA5 annular steering-flow features for track windows."""

import argparse
import json
import math
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ml.align_era5_tracks import (
    VARIABLES,
    LEVELS,
    bilinear_value,
    collect_track_points,
    cycle_key,
    discover_gribs,
    era5_years,
)
from ml.data_audit import PROJECT_ROOT

EARTH_RADIUS_KM = 6371.0088
RING_RADII_KM = (350.0, 550.0, 750.0)
BEARING_COUNT = 8


def destination(lng: float, lat: float, distance_km: float, bearing_rad: float) -> Tuple[float, float]:
    lat1, lon1 = math.radians(lat), math.radians(lng)
    angular = distance_km / EARTH_RADIUS_KM
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing_rad)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing_rad) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lon2) % 360.0, math.degrees(lat2)


def annular_mean(
    message: Any,
    values: np.ndarray,
    lng: float,
    lat: float,
    eccodes: Any,
) -> Tuple[float, float, int, int]:
    samples: List[Tuple[float, float, float]] = []
    expected = len(RING_RADII_KM) * BEARING_COUNT
    for radius in RING_RADII_KM:
        for bearing_index in range(BEARING_COUNT):
            bearing = 2.0 * math.pi * bearing_index / BEARING_COUNT
            sample_lng, sample_lat = destination(lng, lat, radius, bearing)
            value = bilinear_value(message, values, sample_lng, sample_lat, eccodes)
            if value is not None:
                samples.append((value, radius, bearing))
    if not samples:
        return float("nan"), float("nan"), 0, expected
    # Radius weighting approximates area weighting across the sampled annulus.
    weights = np.asarray([sample[1] for sample in samples], dtype=np.float64)
    u = np.asarray([sample[0] for sample in samples], dtype=np.float64)
    return float(np.average(u, weights=weights)), float(len(samples) / expected), len(samples), expected


def extract(
    grib_root: Path,
    data_root: Path,
    center_root: Path,
    output_root: Path,
    max_points: Optional[int] = None,
    max_files: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        import eccodes
    except ImportError as error:
        raise RuntimeError("Install ml/requirements-era5.txt before reading GRIB files") from error

    points = collect_track_points(data_root / "typhoon")
    if max_points is not None:
        points = points[:max_points]
    by_cycle: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    for index, point in enumerate(points):
        by_cycle[cycle_key(point["lower"])].append(index)

    fields: Dict[int, Dict[str, float]] = defaultdict(dict)
    audits = []
    grib_files = discover_gribs(grib_root)
    if max_files is not None:
        grib_files = grib_files[:max_files]
    for path in grib_files:
        years = era5_years(path)
        relevant_cycles = {
            key: indices
            for key, indices in by_cycle.items()
            if not years or any(points[index]["year"] in years for index in indices)
        }
        if not relevant_cycles:
            continue
        # Verified .grib.gz.tmp files are staged raw GRIBs; only real .gz files
        # need decompression. This also avoids Windows file-lock cleanup issues.
        compressed = path.name.lower().endswith(".gz")
        if compressed:
            import gzip
            temp_dir = output_root / "_tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            handle_fd, temp_name = tempfile.mkstemp(prefix="ring_", suffix=".grib", dir=str(temp_dir))
            os.close(handle_fd)
            scan_path = Path(temp_name)
            with gzip.open(path, "rb") as source, scan_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=16 * 1024 * 1024)
            scan_handle = scan_path.open("rb")
        else:
            scan_handle = path.open("rb")

        message_count = matched_count = 0
        with scan_handle as handle:
            while True:
                message = eccodes.codes_grib_new_from_file(handle)
                if message is None:
                    break
                message_count += 1
                try:
                    variable = str(eccodes.codes_get(message, "shortName"))
                    level = int(eccodes.codes_get(message, "level"))
                    if variable not in VARIABLES or level not in LEVELS:
                        continue
                    key = (str(eccodes.codes_get(message, "dataDate")), int(eccodes.codes_get(message, "dataTime")))
                    indices = relevant_cycles.get(key)
                    if not indices:
                        continue
                    grid = np.asarray(eccodes.codes_get_values(message), dtype=np.float64)
                    field_name = f"{variable}{level}"
                    for index in indices:
                        point = points[index]
                        mean, coverage, _, _ = annular_mean(
                            message, grid, point["lng"], point["lat"], eccodes
                        )
                        if math.isfinite(mean):
                            fields[index][field_name] = mean
                            fields[index][field_name + "_coverage"] = coverage
                    matched_count += 1
                finally:
                    eccodes.codes_release(message)
        if compressed:
            scan_handle.close()
            try:
                os.remove(scan_path)
            except OSError as error:
                print(f"warning: could not remove {scan_path}: {error}", flush=True)
        audits.append({"file": str(path), "messages_scanned": message_count, "matched_fields": matched_count})
        print(f"scanned {path.name}: messages={message_count} matched={matched_count}", flush=True)

    point_features: Dict[Tuple[str, str], Dict[str, Any]] = {}
    coverage = {name: 0 for name in ("u500", "v500", "u850", "v850")}
    for index, point in enumerate(points):
        values = fields.get(index, {})
        feature_names = ("u500", "v500", "u850", "v850")
        if all(name in values for name in feature_names):
            point_features[(point["typhoon_id"], point["time"])] = {
                name: values[name] for name in feature_names
            }
            for name in feature_names:
                coverage[name] += 1

    split_summaries = {}
    for split in ("train", "validation", "test"):
        source_path = data_root / "processed" / f"{split}.jsonl"
        output_path = output_root / "windows" / f"{split}.jsonl"
        rows = []
        for line in source_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            ring_steps = []
            for timestamp in record["history_times"]:
                row = point_features.get((str(record["typhoon_id"]), str(timestamp)))
                if row is None:
                    break
                ring_steps.append([row[name] for name in ("u500", "v500", "u850", "v850")])
            if len(ring_steps) != len(record["history_times"]):
                continue
            rows.append({**record, "ring": ring_steps})
        # Restrict all variants to the existing center-point complete windows.
        center_path = center_root / "windows" / f"{split}.jsonl"
        center_index = {
            (str(item["typhoon_id"]), tuple(item["history_times"])): item
            for item in (
                json.loads(line)
                for line in center_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }
        common = []
        for item in rows:
            key = (str(item["typhoon_id"]), tuple(item["history_times"]))
            center = center_index.get(key)
            if center is None:
                continue
            merged_x = []
            for source_x, ring_values in zip(center["x"], item["ring"]):
                merged_x.append(list(source_x) + ring_values)
            common.append({**center, "x": merged_x})
        output_path = output_root / "ring_windows" / f"{split}.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in common),
            encoding="utf-8",
        )
        split_summaries[split] = {
            "center_point_windows": len(center_index),
            "ring_complete_common_windows": len(common),
            "coverage_fraction": len(common) / len(center_index) if center_index else 0.0,
            "storms": len({str(item["typhoon_id"]) for item in common}),
            "output": str(output_path.resolve()),
        }

    result = {
        "experiment": "era5_annular_steering_flow",
        "levels_hPa": [500, 850],
        "variables": ["u", "v"],
        "annulus_km": [min(RING_RADII_KM), max(RING_RADII_KM)],
        "quadrature": {
            "radii_km": list(RING_RADII_KM),
            "equally_spaced_bearings": BEARING_COUNT,
            "interpolation": "bilinear on native regular lat/lon grid",
            "aggregation": "radius-weighted mean over valid annular samples",
        },
        "time_policy": "latest ERA5 6-hour UTC cycle at or before track observation; no future fields",
        "point_coverage": len(point_features) / len(points) if points else 0,
        "field_coverage": coverage,
        "paired_windows_by_frozen_split": split_summaries,
        "files": audits,
        "limits": {"max_points": max_points, "max_files": max_files},
        "note": "Environmental flow proxy from ERA5 winds; not a dynamical steering-flow diagnostic with vortex removal.",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "ring_alignment_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(split_summaries, ensure_ascii=False, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grib-root", type=Path, default=PROJECT_ROOT / "ERA5data")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--center-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850_refresh_20261002",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow",
    )
    parser.add_argument("--max-points", type=int, default=None, help="Limit track points for a smoke test")
    parser.add_argument("--max-files", type=int, default=None, help="Limit discovered GRIB files for a smoke test")
    args = parser.parse_args()
    extract(
        args.grib_root,
        args.data_root,
        args.center_root,
        args.output_root,
        max_points=args.max_points,
        max_files=args.max_files,
    )


if __name__ == "__main__":
    main()
