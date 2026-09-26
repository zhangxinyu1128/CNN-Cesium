"""Convert CMA annual best-track TXT files into the project's JSON schema."""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


HEADER_RE = re.compile(
    r"^66666\s+(\d{4})\s+(\d+)\s+\d{4}\s+(\d{4})\s+\d+\s+(\d+)\s+(.+?)\s+(\d{8})\s*$"
)
POINT_RE = re.compile(
    r"^(\d{10})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)


def category(intensity: int) -> Tuple[str, int]:
    labels = {
        1: ("热带低压(TD)", 6),
        2: ("热带风暴(TS)", 8),
        3: ("强热带风暴(STS)", 10),
        4: ("台风(TY)", 12),
        5: ("强台风(STY)", 14),
        6: ("超强台风(Super TY)", 16),
    }
    return labels.get(intensity, ("未分级", max(0, intensity)))


def parse_file(path: Path) -> Tuple[List[Dict], int]:
    year = int(path.stem[2:6])
    records: List[Dict] = []
    current: Optional[Dict] = None
    sequence = 0
    expected_points = 0

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.rstrip()
        header = HEADER_RE.match(line)
        if header:
            if current is not None:
                records.append(current)
            sequence += 1
            source_id, count, storm_id, intensity, name, _ = header.groups()
            storm_number = storm_id[-2:]
            tfbh = f"{year}{storm_number}" if storm_id != "0000" else f"{year}{sequence:02d}01"
            display_name = name.strip() if name.strip() != "(nameless)" else "热带低压"
            current = {
                "tfbh": tfbh,
                "ident": tfbh if storm_id != "0000" else "TD",
                "name": display_name,
                "ename": display_name,
                "is_current": 0,
                "begin_time": None,
                "end_time": None,
                "land": [],
                "points": [],
                "_expected_points": int(count),
                "_header_intensity": int(intensity),
                "_source_id": source_id,
            }
            expected_points += int(count)
            continue

        point = POINT_RE.match(line)
        if not point or current is None:
            continue
        timestamp, intensity, lat10, lng10, pressure, speed = point.groups()
        dt = datetime.strptime(timestamp, "%Y%m%d%H")
        label, power = category(int(intensity))
        current["points"].append(
            {
                "time": dt.isoformat(),
                "lng": int(lng10) / 10,
                "lat": int(lat10) / 10,
                "strong": label,
                "power": power,
                "speed": int(speed),
                "move_dir": None,
                "move_speed": None,
                "pressure": int(pressure),
                "radius7": None,
                "radius10": None,
                "radius12": None,
                "radius7_quad": {"ne": None, "se": None, "sw": None, "nw": None},
                "radius10_quad": {"ne": None, "se": None, "sw": None, "nw": None},
                "radius12_quad": {"ne": None, "se": None, "sw": None, "nw": None},
                "remark": "",
                "forecast": None,
            }
        )

    if current is not None:
        records.append(current)

    for record in records:
        points = record.pop("points")
        expected = record.pop("_expected_points")
        record.pop("_header_intensity")
        record.pop("_source_id")
        if len(points) != expected:
            raise ValueError(
                f"{path.name}: {record['tfbh']} declares {expected} points, parsed {len(points)}"
            )
        if points:
            record["begin_time"] = points[0]["time"]
            record["end_time"] = points[-1]["time"]
        record["points"] = points
    return records, expected_points


def convert(source: Path, data_root: Path, year: Optional[int] = None) -> Dict:
    files = [source / f"CH{year}BST.txt"] if year else sorted(source.glob("CH????BST.txt"))
    total_storms = 0
    total_points = 0
    converted_years = []
    for path in files:
        if not path.exists():
            raise FileNotFoundError(path)
        records, expected_points = parse_file(path)
        target_year = int(path.stem[2:6])
        year_root = data_root / "year"
        track_root = data_root / "typhoon"
        year_root.mkdir(parents=True, exist_ok=True)
        track_root.mkdir(parents=True, exist_ok=True)
        metadata = []
        for record in records:
            metadata.append({key: value for key, value in record.items() if key != "points"})
            (track_root / f"{record['tfbh']}.json").write_text(
                json.dumps([record], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            total_points += len(record["points"])
        (year_root / f"{target_year}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        total_storms += len(records)
        converted_years.append({"year": target_year, "storms": len(records), "points": sum(len(r["points"]) for r in records)})
    return {"years": converted_years, "storms": total_storms, "points": total_points}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--year", type=int)
    args = parser.parse_args()
    print(json.dumps(convert(args.source, args.data_root, args.year), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
