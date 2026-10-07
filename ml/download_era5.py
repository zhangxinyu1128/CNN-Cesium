"""Download track-centered ERA5 pressure-level fields for selected CMA years."""

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET = "reanalysis-era5-pressure-levels"
PRESSURE_LEVELS = ["200", "300", "500", "700", "850"]
VARIABLES = [
    "u_component_of_wind",
    "v_component_of_wind",
]
SYNOPTIC_HOURS = {"00", "06", "12", "18"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)


def storm_boxes(points: Sequence[Dict], margin: float) -> List[Tuple[float, float, float, float]]:
    latitudes = [float(point["lat"]) for point in points]
    north = min(90.0, max(latitudes) + margin)
    south = max(-90.0, min(latitudes) - margin)

    longitudes = [float(point["lng"]) % 360.0 for point in points]
    if any(lon > 180.0 for lon in longitudes) and any(lon <= 180.0 for lon in longitudes):
        west_side = [lon - 360.0 for lon in longitudes if lon > 180.0]
        east_side = [lon for lon in longitudes if lon <= 180.0]
        boxes = [
            (
                north,
                max(-180.0, min(west_side) - margin),
                south,
                min(180.0, max(west_side) + margin),
            ),
            (north, max(-180.0, min(east_side) - margin), south, min(180.0, max(east_side) + margin)),
        ]
        return [box for box in boxes if box[1] <= box[3]]

    normalized = [lon - 360.0 if lon > 180.0 else lon for lon in longitudes]
    return [(north, max(-180.0, min(normalized) - margin), south, min(180.0, max(normalized) + margin))]


def request_for(points: Sequence[Dict], box: Tuple[float, float, float, float]) -> Dict:
    dates = sorted({point["time"][:10] for point in points})
    years = sorted({date[:4] for date in dates})
    months = sorted({date[5:7] for date in dates})
    days = sorted({date[8:10] for date in dates})
    times = sorted({point["time"][11:13] + ":00" for point in points if point["time"][11:13] in SYNOPTIC_HOURS})
    if not years or not months or not days or not times:
        raise ValueError("ERA5 request requires dated track points at 00/06/12/18 UTC")
    return {
        "product_type": ["reanalysis"],
        "variable": VARIABLES,
        "pressure_level": PRESSURE_LEVELS,
        "year": years,
        "month": months,
        "day": days,
        "time": times,
        "area": [round(value, 2) for value in box],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def iter_jobs(data_root: Path, years: Iterable[int], margin: float):
    for year in years:
        index_path = data_root / "year" / (str(year) + ".json")
        if not index_path.exists():
            raise FileNotFoundError("Missing annual index: " + str(index_path))
        for item in read_json(index_path):
            identifier = str(item["tfbh"])
            track_path = data_root / "typhoon" / (identifier + ".json")
            if not track_path.exists():
                raise FileNotFoundError("Missing track: " + str(track_path))
            record = read_json(track_path)[0]
            points = record.get("points") or []
            points = [point for point in points if point.get("time") and point.get("lat") is not None and point.get("lng") is not None]
            if not points:
                continue
            months = sorted({point["time"][:7] for point in points})
            for month in months:
                month_points = [point for point in points if point["time"].startswith(month)]
                boxes = storm_boxes(month_points, margin)
                for part, box in enumerate(boxes, start=1):
                    suffix = "_part" + str(part) if len(boxes) > 1 else ""
                    yield record, month_points, box, "_" + month.replace("-", "") + suffix


def download(data_root: Path, output_root: Path, years: Sequence[int], margin: float, limit: int = 0):
    try:
        import cdsapi
    except ImportError as error:
        raise RuntimeError("Install cdsapi in the active Python environment: python -m pip install cdsapi") from error

    output_root.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client()
    completed = []
    for index, (record, points, box, suffix) in enumerate(iter_jobs(data_root, years, margin), start=1):
        if limit and index > limit:
            break
        identifier = safe_name(str(record["tfbh"]))
        target = output_root / f"{identifier}{suffix}.nc"
        metadata_path = target.with_suffix(".request.json")
        request = request_for(points, box)
        metadata = {
            "source": "CMA best-track + Copernicus ERA5",
            "dataset": DATASET,
            "storm_id": record["tfbh"],
            "storm_name": record.get("name"),
            "track_time_range": [min(point["time"] for point in points), max(point["time"] for point in points)],
            "spatial_margin_degrees": margin,
            "pressure_levels_hPa": PRESSURE_LEVELS,
            "variables": VARIABLES,
            "wind_unit": "m s-1",
            "request": request,
        }
        if target.exists() and target.stat().st_size > 0:
            completed.append({"file": target.name, "status": "already_present"})
            continue
        client.retrieve(DATASET, request, str(target))
        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("CDS returned without producing a non-empty file: " + str(target))
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        completed.append({"file": target.name, "bytes": target.stat().st_size, "status": "downloaded"})
        print(json.dumps(completed[-1], ensure_ascii=False))
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "era5" / "pressure_levels")
    parser.add_argument("--year", type=int, action="append", dest="years", help="Repeat to select multiple years; default: 2025")
    parser.add_argument("--margin", type=float, default=10.0, help="Spatial padding around each storm track in degrees")
    parser.add_argument("--limit", type=int, default=0, help="Download only the first N storms as a pilot")
    args = parser.parse_args()
    years = args.years or [2025]
    results = download(args.data_root, args.output_root, years, args.margin, args.limit)
    print(json.dumps({"jobs": len(results), "output": str(args.output_root.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
