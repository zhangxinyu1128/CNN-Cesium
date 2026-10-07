"""Sample a local ERA5 GRIB at 2025 best-track points.

This is a provenance-preserving pilot. It creates point features only where the
track timestamp matches a synoptic ERA5 cycle and the point is inside the
downloaded grid. It does not alter the frozen model windows or claim a fusion
model has been trained.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from ml.data_audit import PROJECT_ROOT, number


LEVELS = (200, 300, 500, 700, 850)
VARIABLES = ("u", "v")
SYNOPTIC = {0, 600, 1200, 1800}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_time(value: Any) -> Tuple[str, int]:
    timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    return timestamp.strftime("%Y%m%d"), timestamp.hour * 100


def collect_points(data_root: Path, year: int) -> Tuple[Dict[Tuple[str, int], List[Dict[str, Any]]], int]:
    requested: Dict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    total = 0
    for path in sorted((data_root / "typhoon").glob(f"{year}*.json")):
        records = read_json(path)
        for record in records if isinstance(records, list) else []:
            storm_id = str(record.get("tfbh") or path.stem)
            for point in record.get("points") or []:
                try:
                    date, data_time = parse_time(point["time"])
                except (KeyError, TypeError, ValueError):
                    continue
                if data_time not in SYNOPTIC:
                    continue
                lng = number(point.get("lng"))
                lat = number(point.get("lat"))
                if lng is None or lat is None:
                    continue
                total += 1
                requested[(date, data_time)].append(
                    {"typhoon_id": storm_id, "time": point["time"], "lng": float(lng) % 360.0, "lat": float(lat)}
                )
    return requested, total


def sample_grib(grib_path: Path, requested: Dict[Tuple[str, int], List[Dict[str, Any]]]) -> Dict[Tuple[str, str, int], Dict[str, float]]:
    try:
        import eccodes
    except ImportError as error:
        raise RuntimeError("Install ml/requirements-era5.txt before sampling GRIB files") from error

    results: Dict[Tuple[str, str, int], Dict[str, float]] = {}
    with grib_path.open("rb") as handle:
        while True:
            message = eccodes.codes_grib_new_from_file(handle)
            if message is None:
                break
            try:
                variable = str(eccodes.codes_get(message, "shortName"))
                level = int(eccodes.codes_get(message, "level"))
                date = str(eccodes.codes_get(message, "dataDate"))
                data_time = int(eccodes.codes_get(message, "dataTime"))
                points = requested.get((date, data_time), [])
                if variable not in VARIABLES or level not in LEVELS or not points:
                    continue
                north = float(eccodes.codes_get(message, "latitudeOfFirstGridPointInDegrees"))
                south = float(eccodes.codes_get(message, "latitudeOfLastGridPointInDegrees"))
                west = float(eccodes.codes_get(message, "longitudeOfFirstGridPointInDegrees")) % 360.0
                east = float(eccodes.codes_get(message, "longitudeOfLastGridPointInDegrees")) % 360.0
                dx = float(eccodes.codes_get(message, "iDirectionIncrementInDegrees"))
                dy = float(eccodes.codes_get(message, "jDirectionIncrementInDegrees"))
                ni = int(eccodes.codes_get(message, "Ni"))
                nj = int(eccodes.codes_get(message, "Nj"))
                values = np.asarray(eccodes.codes_get_values(message), dtype=np.float64).reshape(nj, ni)
                for point in points:
                    if not south <= point["lat"] <= north or not west <= point["lng"] <= east:
                        continue
                    column = int(round((point["lng"] - west) / dx))
                    row = int(round((north - point["lat"]) / dy))
                    if not (0 <= column < ni and 0 <= row < nj):
                        continue
                    key = (point["typhoon_id"], point["time"], level)
                    results.setdefault(key, {})[variable] = float(values[row, column])
            finally:
                eccodes.codes_release(message)
    return results


def run(data_root: Path, grib_path: Path, output: Path, year: int) -> Dict[str, Any]:
    requested, requested_count = collect_points(data_root, year)
    sampled = sample_grib(grib_path, requested)
    rows = []
    complete = 0
    for points in requested.values():
        for point in points:
            levels = {}
            for level in LEVELS:
                values = sampled.get((point["typhoon_id"], point["time"], level), {})
                if set(values) == set(VARIABLES):
                    levels[str(level)] = values
            if len(levels) == len(LEVELS):
                complete += 1
            rows.append({**point, "wind": levels})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return {
        "source_grib": str(grib_path.resolve()),
        "source_grib_sha256": hashlib.sha256(grib_path.read_bytes()).hexdigest(),
        "year": year,
        "requested_synoptic_track_points": requested_count,
        "sampled_rows": len(rows),
        "complete_5_level_uv_rows": complete,
        "coverage_fraction": complete / requested_count if requested_count else 0.0,
        "levels_hPa": list(LEVELS),
        "variables": list(VARIABLES),
        "output": str(output.resolve()),
        "sampling": "nearest 0.25 degree grid point; exact UTC date and 00/06/12/18 cycle; no extrapolation",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--grib", type=Path, default=PROJECT_ROOT / "ERA5data" / "data.grib")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "track_points_2025.jsonl")
    parser.add_argument("--summary", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "sampling_summary_2025.json")
    args = parser.parse_args()
    summary = run(args.data_root, args.grib, args.output, args.year)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
