"""Align historical ERA5 500/850 hPa winds to CMA track windows.

Only real GRIB files are read. Temporary downloads are ignored. Track points
are interpolated linearly between bracketing six-hour cycles and bilinearly on
the ERA5 grid; this script never edits the frozen source splits.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import json
import multiprocessing
import re
import tempfile
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from ml.data_audit import PROJECT_ROOT, number


LEVELS = (500, 850)
VARIABLES = ("u", "v")
FIELD_NAMES = ("u500", "v500", "u850", "v850")
SYNOPTIC_HOURS = 6


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def brackets(timestamp: datetime) -> Tuple[datetime, datetime, float]:
    base = timestamp.replace(
        hour=(timestamp.hour // SYNOPTIC_HOURS) * SYNOPTIC_HOURS,
        minute=0,
        second=0,
        microsecond=0,
    )
    return base, base, 0.0


def collect_track_points(track_root: Path) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for path in sorted(track_root.glob("*.json")):
        records = read_json(path)
        for record in records if isinstance(records, list) else []:
            storm_id = str(record.get("tfbh") or path.stem)
            for point in record.get("points") or []:
                try:
                    timestamp = parse_datetime(point["time"])
                except (KeyError, TypeError, ValueError):
                    continue
                lng, lat = number(point.get("lng")), number(point.get("lat"))
                if lng is None or lat is None:
                    continue
                lower, upper, fraction = brackets(timestamp)
                points.append(
                    {
                        "typhoon_id": storm_id,
                        "time": str(point["time"]),
                        "timestamp": timestamp,
                        "year": timestamp.year,
                        "lng": float(lng) % 360.0,
                        "lat": float(lat),
                        "lower": lower,
                        "upper": upper,
                        "fraction": fraction,
                    }
                )
    return points


def sidecar_paths(path: Path) -> List[Path]:
    candidates = [Path(str(path) + ".metadata.json")]
    if path.name.lower().endswith(".grib.gz.tmp"):
        for sidecar in path.parent.glob("*.metadata.json"):
            try:
                metadata = read_json(sidecar)
            except (OSError, ValueError):
                continue
            if int(metadata.get("output_raw_grib_bytes", -1)) == path.stat().st_size:
                candidates.append(sidecar)
    if path.name.lower().startswith("data_track_times"):
        candidates.extend((path.parent / "metadata_track_times.json", path.parents[1] / "metadata_track_times.json"))
    if path.name.lower() in {"data.grib", "data.grib.gz"}:
        candidates.append(path.parent / "metadata.json")
    return [candidate for candidate in candidates if candidate.is_file()]


def era5_years(path: Path) -> Set[int]:
    years = {int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", path.name)}
    for sidecar in sidecar_paths(path):
        try:
            metadata = read_json(sidecar)
        except (OSError, ValueError):
            continue
        for value in metadata.get("source_available_year_blocks", []):
            for year in re.findall(r"(?:19|20)\d{2}", str(value)):
                years.add(int(year))
        for field in ("year", "track_year_range", "date_min_utc", "date_max_utc", "output_date_min_utc", "output_date_max_utc"):
            value = metadata.get(field)
            if value is not None:
                years.update(int(year) for year in re.findall(r"(?:19|20)\d{2}", str(value)))
    return years


def discover_gribs(root: Path) -> List[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(".grib") or name.endswith(".grib.gz"):
            files.append(path)
            continue
        if not name.endswith(".grib.gz.tmp"):
            continue
        metadata = next(
            (
                read_json(sidecar)
                for sidecar in sidecar_paths(path)
                if int(read_json(sidecar).get("output_raw_grib_bytes", -1)) == path.stat().st_size
            ),
            None,
        )
        if metadata is None or path.stat().st_size != int(metadata.get("output_raw_grib_bytes", -1)):
            continue
        with path.open("rb") as handle:
            if handle.read(4) != b"GRIB":
                continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != metadata.get("output_decompressed_sha256"):
            continue
        print("verified staged GRIB by size and SHA-256:", path.name, flush=True)
        files.append(path)
    return sorted(files)


def bilinear_value(message: Any, values: np.ndarray, lng: float, lat: float, eccodes: Any) -> Optional[float]:
    ni = int(eccodes.codes_get(message, "Ni"))
    nj = int(eccodes.codes_get(message, "Nj"))
    if int(eccodes.codes_get(message, "jPointsAreConsecutive")):
        return None
    first_lng = float(eccodes.codes_get(message, "longitudeOfFirstGridPointInDegrees")) % 360.0
    last_lng = float(eccodes.codes_get(message, "longitudeOfLastGridPointInDegrees")) % 360.0
    first_lat = float(eccodes.codes_get(message, "latitudeOfFirstGridPointInDegrees"))
    last_lat = float(eccodes.codes_get(message, "latitudeOfLastGridPointInDegrees"))
    delta_lng = (last_lng - first_lng + 540.0) % 360.0 - 180.0
    if abs(delta_lng) < 1e-9 and ni > 1:
        return None
    grid_delta_lng = delta_lng / max(ni - 1, 1)
    grid_delta_lat = (last_lat - first_lat) / max(nj - 1, 1)
    if grid_delta_lng == 0 or grid_delta_lat == 0:
        return None
    point_delta_lng = (lng - first_lng + 540.0) % 360.0 - 180.0
    x = point_delta_lng / grid_delta_lng
    y = (lat - first_lat) / grid_delta_lat
    tolerance = 1e-6
    if x < -tolerance or x > ni - 1 + tolerance or y < -tolerance or y > nj - 1 + tolerance:
        return None
    x, y = min(max(x, 0.0), ni - 1.0), min(max(y, 0.0), nj - 1.0)
    x0, y0 = int(np.floor(x)), int(np.floor(y))
    x1, y1 = min(x0 + 1, ni - 1), min(y0 + 1, nj - 1)
    wx, wy = x - x0, y - y0
    grid = values.reshape(nj, ni)
    corners = (grid[y0, x0], grid[y0, x1], grid[y1, x0], grid[y1, x1])
    if not all(np.isfinite(value) and abs(float(value)) < 1e10 for value in corners):
        return None
    return float(
        (1.0 - wy) * ((1.0 - wx) * corners[0] + wx * corners[1])
        + wy * ((1.0 - wx) * corners[2] + wx * corners[3])
    )


def cycle_key(timestamp: datetime) -> Tuple[str, int]:
    return timestamp.strftime("%Y%m%d"), timestamp.hour * 100


def scan_grib_file(
    path: str,
    eligible_cycles: Dict[Tuple[str, int], List[int]],
    coordinates: Dict[int, Tuple[float, float]],
) -> Tuple[Dict[int, Dict[str, float]], int, int]:
    try:
        import eccodes
    except ImportError as error:
        raise RuntimeError("Install ml/requirements-era5.txt before reading GRIB files") from error
    sampled: Dict[int, Dict[str, float]] = defaultdict(dict)
    message_count = 0
    matched_messages = 0
    with Path(path).open("rb") as handle:
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
                indices = eligible_cycles.get(key)
                if not indices:
                    continue
                values = np.asarray(eccodes.codes_get_values(message), dtype=np.float64)
                field = variable + str(level)
                for index in indices:
                    lng, lat = coordinates[index]
                    result = bilinear_value(message, values, lng, lat, eccodes)
                    if result is not None:
                        sampled[index][key[0] + str(key[1]) + field] = result
                matched_messages += 1
            finally:
                eccodes.codes_release(message)
    return dict(sampled), message_count, matched_messages


def sample_files(points: List[Dict[str, Any]], files: List[Path]) -> Tuple[Dict[int, Dict[str, float]], List[Dict[str, Any]]]:
    requests: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    for index, point in enumerate(points):
        requests[cycle_key(point["lower"])].append(index)
        if point["upper"] != point["lower"]:
            requests[cycle_key(point["upper"])].append(index)

    sampled: Dict[int, Dict[str, float]] = defaultdict(dict)
    file_audit = []
    for path in files:
        years = era5_years(path)
        eligible_cycles = {}
        for key, indices in requests.items():
            eligible = [index for index in indices if not years or points[index]["year"] in years]
            if eligible:
                eligible_cycles[key] = eligible
        if not eligible_cycles:
            continue
        coordinates = {
            index: (points[index]["lng"], points[index]["lat"])
            for indices in eligible_cycles.values()
            for index in indices
        }
        if path.name.lower().endswith(".gz"):
            print("decompressing", path.name, flush=True)
            with tempfile.TemporaryDirectory(prefix="era5_grib_") as temp_dir:
                scan_path = Path(temp_dir) / "input.grib"
                with gzip.open(str(path), "rb") as compressed, scan_path.open("wb") as expanded:
                    shutil.copyfileobj(compressed, expanded, length=8 * 1024 * 1024)
                with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
                    result, message_count, matched_messages = pool.submit(
                        scan_grib_file, str(scan_path), eligible_cycles, coordinates
                    ).result()
        else:
            result, message_count, matched_messages = scan_grib_file(str(path), eligible_cycles, coordinates)
        for index, fields in result.items():
            sampled[index].update(fields)
        file_audit.append(
            {
                "file": str(path.resolve()),
                "bytes": path.stat().st_size,
                "years_from_name_or_metadata": sorted(years),
                "messages_scanned": message_count,
                "matched_wind_messages": matched_messages,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.stat().st_size < 100_000_000 else None,
            }
        )
        print("scanned", path.name, "messages=", message_count, "matched=", matched_messages, flush=True)
    return sampled, file_audit


def interpolate_point(point: Dict[str, Any], samples: Dict[str, float], index: int) -> Optional[Dict[str, Any]]:
    lower_key = point["lower"].strftime("%Y%m%d") + str(point["lower"].hour * 100)
    upper_key = point["upper"].strftime("%Y%m%d") + str(point["upper"].hour * 100)
    values = {}
    for level in LEVELS:
        for variable in VARIABLES:
            field = variable + str(level)
            lower_value = samples.get(lower_key + field)
            upper_value = samples.get(upper_key + field)
            if lower_value is None or upper_value is None:
                return None
            values[field] = lower_value + point["fraction"] * (upper_value - lower_value)
    return {
        "typhoon_id": point["typhoon_id"],
        "time": point["time"],
        "lng": point["lng"],
        "lat": point["lat"],
        "era5_time_utc": point["lower"].isoformat(),
        "era5_age_hours": (point["timestamp"] - point["lower"]).total_seconds() / 3600.0,
        "wind": {
            "500": {"u": values["u500"], "v": values["v500"]},
            "850": {"u": values["u850"], "v": values["v850"]},
        },
    }


def write_paired_windows(data_root: Path, point_winds: Dict[Tuple[str, str], Dict[str, Any]], output_root: Path) -> Dict[str, Any]:
    summaries = {}
    for split in ("train", "validation", "test"):
        source = data_root / "processed" / (split + ".jsonl")
        output = output_root / "windows" / (split + ".jsonl")
        paired = []
        source_count = 0
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            source_count += 1
            record = json.loads(line)
            feature_rows = []
            for index, timestamp in enumerate(record["history_times"]):
                weather = point_winds.get((str(record["typhoon_id"]), str(timestamp)))
                if weather is None:
                    break
                wind = weather["wind"]
                feature_rows.append(
                    list(record["x"][index])
                    + [
                        float(wind["500"]["u"]),
                        float(wind["500"]["v"]),
                        float(wind["850"]["u"]),
                        float(wind["850"]["v"]),
                        float(weather["era5_age_hours"]),
                    ]
                )
            if len(feature_rows) == len(record["history_times"]):
                paired.append({**record, "x": feature_rows})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in paired), encoding="utf-8")
        summaries[split] = {
            "source_windows": source_count,
            "paired_windows": len(paired),
            "coverage_fraction": len(paired) / source_count if source_count else 0.0,
            "storms": len({str(row["typhoon_id"]) for row in paired}),
            "output": str(output.resolve()),
        }
    return summaries


def run(grib_root: Path, data_root: Path, output_root: Path) -> Dict[str, Any]:
    points = collect_track_points(data_root / "typhoon")
    files = discover_gribs(grib_root)
    sampled, file_audit = sample_files(points, files)
    point_winds: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_year: Dict[str, Dict[str, int]] = defaultdict(lambda: {"track_points": 0, "paired_points": 0})
    point_rows = []
    for index, point in enumerate(points):
        year_summary = by_year[str(point["year"])]
        year_summary["track_points"] += 1
        row = interpolate_point(point, sampled.get(index, {}), index)
        if row is not None:
            point_rows.append(row)
            point_winds[(row["typhoon_id"], row["time"])] = row
            year_summary["paired_points"] += 1
    output_root.mkdir(parents=True, exist_ok=True)
    point_path = output_root / "point_winds_500_850.jsonl"
    point_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in point_rows), encoding="utf-8")
    split_summaries = write_paired_windows(data_root, point_winds, output_root)
    result = {
        "levels_hPa": list(LEVELS),
        "variables": list(VARIABLES),
        "features": list(FIELD_NAMES) + ["era5_age_hours"],
        "interpolation": {
            "time": "most recent 6-hour UTC ERA5 cycle at or before the observation; age is included as a feature",
            "space": "bilinear on each GRIB regular latitude-longitude grid",
            "future_weather_leakage": "avoided; no ERA5 cycle later than the track observation is used",
            "coverage_rule": "all four 500/850 hPa u/v fields required at the preceding cycle",
        },
        "track_points": len(points),
        "paired_points": len(point_rows),
        "paired_point_fraction": len(point_rows) / len(points) if points else 0.0,
        "by_track_year": dict(sorted(by_year.items())),
        "paired_windows_by_frozen_split": split_summaries,
        "point_output": str(point_path.resolve()),
        "files": file_audit,
        "note": "Year availability reflects GRIB files physically present and readable during this run; temporary .tmp files are excluded.",
    }
    (output_root / "alignment_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("track_points", "paired_points", "paired_point_fraction", "paired_windows_by_frozen_split")}, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grib-root", type=Path, default=PROJECT_ROOT / "ERA5data")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850")
    args = parser.parse_args()
    run(args.grib_root, args.data_root, args.output_root)


if __name__ == "__main__":
    main()
