"""Filter ERA5 GRIB messages to synoptic times surrounding CMA track points."""

import argparse
import glob
import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Counter as CounterType, Set, Tuple

from eccodes import codes_get, codes_grib_new_from_file, codes_release, codes_write


SYNOPTIC_HOURS = {0, 6, 12, 18}


def target_times(project_root: Path, years: Set[int]) -> Tuple[Set[datetime], CounterType, CounterType]:
    selected = set()
    observations: Counter = Counter()
    pattern = project_root / "data" / "typhoon" / "*.json"
    for path in glob.glob(str(pattern)):
        try:
            year = int(Path(path).name[:4])
        except ValueError:
            continue
        if year not in years:
            continue
        with open(path, encoding="utf-8") as handle:
            storms = json.load(handle)
        for storm in storms:
            for point in storm.get("points", []):
                value = point.get("time")
                if not value:
                    continue
                dt = datetime.fromisoformat(value)
                base = dt.replace(hour=(dt.hour // 6) * 6, minute=0, second=0, microsecond=0)
                choices = {base} if dt.hour in SYNOPTIC_HOURS and dt == base else {base, base + timedelta(hours=6)}
                selected.update(choices)
                observations[year] += 1
    return selected, observations, Counter()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--years", nargs="+", type=int, required=True)
    args = parser.parse_args()

    years = set(args.years)
    project_root = Path(__file__).resolve().parents[1]
    wanted, observations, _ = target_times(project_root, years)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    total = 0
    kept = 0
    kept_times = set()
    with args.input.open("rb") as source, temporary.open("wb") as destination:
        while True:
            message = codes_grib_new_from_file(source)
            if message is None:
                break
            try:
                total += 1
                date = int(codes_get(message, "dataDate"))
                time = int(codes_get(message, "dataTime"))
                current = datetime.strptime(f"{date:08d}{time:04d}", "%Y%m%d%H%M")
                if current in wanted:
                    codes_write(message, destination)
                    kept += 1
                    kept_times.add(current)
            finally:
                codes_release(message)
    os.replace(temporary, args.output)
    compressed = args.output.with_suffix(args.output.suffix + ".gz")
    with args.output.open("rb") as source, compressed.open("wb") as destination:
        import gzip

        with gzip.GzipFile(fileobj=destination, mode="wb", compresslevel=9) as zipped:
            shutil.copyfileobj(source, zipped, 1024 * 1024)
    metadata = {
        "selection_method": "For each CMA best-track observation, retain the surrounding 6-hour ERA5 synoptic times; retain one time when the observation is already synoptic.",
        "source_file": str(args.input),
        "output_file": str(args.output),
        "years": sorted(years),
        "track_observations": dict(sorted(observations.items())),
        "matched_track_times": len(kept_times),
        "source_messages": total,
        "output_messages": kept,
        "output_raw_grib_bytes": args.output.stat().st_size,
        "output_compressed_bytes": compressed.stat().st_size,
        "output_decompressed_sha256": sha256(args.output),
        "output_compressed_sha256": sha256(compressed),
        "date_min_utc": min(kept_times).date().isoformat() if kept_times else None,
        "date_max_utc": max(kept_times).date().isoformat() if kept_times else None,
        "variables": ["u", "v"],
        "pressure_levels_hPa": [500, 850],
        "time_utc": ["00:00", "06:00", "12:00", "18:00"],
        "geographical_area": {"north": 45.0, "south": 0.0, "west": 100.0, "east": 180.0},
        "grid": {"type": "regular_ll", "Ni": 321, "Nj": 181, "resolution_degrees": 0.25},
    }
    metadata_path = args.output.with_suffix(args.output.suffix + ".metadata.json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
