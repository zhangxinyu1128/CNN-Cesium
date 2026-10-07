"""Inspect and verify the metadata of a local ERA5 GRIB file without loading its grid."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VARIABLES = {"u", "v"}
EXPECTED_LEVELS = {200, 300, 500, 700, 850}
EXPECTED_TIMES = {0, 600, 1200, 1800}
EXPECTED_GRID = (45.0, 0.0, 100.0, 180.0, 0.25, 0.25)


def inspect(path: Path) -> Dict[str, Any]:
    try:
        import eccodes
    except ImportError as error:
        raise RuntimeError("Install ml/requirements-era5.txt before inspecting GRIB files") from error

    variables: Set[str] = set()
    levels: Set[int] = set()
    times: Set[int] = set()
    dates: Set[int] = set()
    grids: Set[Tuple[Any, ...]] = set()
    message_count = 0
    with path.open("rb") as handle:
        while True:
            message = eccodes.codes_grib_new_from_file(handle)
            if message is None:
                break
            try:
                message_count += 1
                variables.add(str(eccodes.codes_get(message, "shortName")))
                levels.add(int(eccodes.codes_get(message, "level")))
                times.add(int(eccodes.codes_get(message, "dataTime")))
                dates.add(int(eccodes.codes_get(message, "dataDate")))
                grids.add(
                    (
                        str(eccodes.codes_get(message, "gridType")),
                        float(eccodes.codes_get(message, "latitudeOfFirstGridPointInDegrees")),
                        float(eccodes.codes_get(message, "latitudeOfLastGridPointInDegrees")),
                        float(eccodes.codes_get(message, "longitudeOfFirstGridPointInDegrees")),
                        float(eccodes.codes_get(message, "longitudeOfLastGridPointInDegrees")),
                        float(eccodes.codes_get(message, "iDirectionIncrementInDegrees")),
                        float(eccodes.codes_get(message, "jDirectionIncrementInDegrees")),
                        int(eccodes.codes_get(message, "Ni")),
                        int(eccodes.codes_get(message, "Nj")),
                    )
                )
            finally:
                eccodes.codes_release(message)

    grid_rows = [
        {
            "grid_type": row[0],
            "north": row[1],
            "south": row[2],
            "west": row[3],
            "east": row[4],
            "dx": row[5],
            "dy": row[6],
            "Ni": row[7],
            "Nj": row[8],
        }
        for row in sorted(grids, key=str)
    ]
    return {
        "file": str(path.resolve()),
        "format": "GRIB",
        "messages": message_count,
        "variables": sorted(variables),
        "pressure_levels_hPa": sorted(levels),
        "time_utc": [f"{value // 100:02d}:{value % 100:02d}" for value in sorted(times)],
        "date_min_utc": datetime.strptime(str(min(dates)), "%Y%m%d").date().isoformat(),
        "date_max_utc": datetime.strptime(str(max(dates)), "%Y%m%d").date().isoformat(),
        "grids": grid_rows,
    }


def verify(metadata: Dict[str, Any]) -> None:
    if set(metadata["variables"]) != EXPECTED_VARIABLES:
        raise ValueError(f"unexpected variables: {metadata['variables']}")
    if set(metadata["pressure_levels_hPa"]) != EXPECTED_LEVELS:
        raise ValueError(f"unexpected pressure levels: {metadata['pressure_levels_hPa']}")
    actual_times = {int(value[:2]) * 100 + int(value[3:]) for value in metadata["time_utc"]}
    if actual_times != EXPECTED_TIMES:
        raise ValueError(f"unexpected UTC times: {metadata['time_utc']}")
    if metadata["date_min_utc"] != "2025-01-01" or metadata["date_max_utc"] != "2025-12-31":
        raise ValueError("GRIB date range is not the selected 2025 range")
    if len(metadata["grids"]) != 1:
        raise ValueError(f"expected one regular grid, found {len(metadata['grids'])}")
    grid = metadata["grids"][0]
    actual = tuple(grid[key] for key in ("north", "south", "west", "east", "dx", "dy"))
    if actual != EXPECTED_GRID:
        raise ValueError(f"unexpected grid area/resolution: {actual}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=PROJECT_ROOT / "ERA5data" / "data.grib")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "ERA5data" / "metadata.json")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    metadata = inspect(args.path)
    if args.verify:
        verify(metadata)
    args.output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if args.verify:
        print("ERA5 metadata verification: PASS")


if __name__ == "__main__":
    main()
