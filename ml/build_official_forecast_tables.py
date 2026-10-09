"""Build official-forecast comparison tables from JTWC ATCF archives.

Inputs (raw, unmodified):
  official_forecast/JTWC/best_track/2017-2024/bwp*.dat   JTWC b-deck (BEST)
  official_forecast/JTWC/forecast/2025_fst/WP*.zip      JTWC f-deck (.fst)
  official_forecast/IBTrACS/ibtracs.WP.list.v04r01.csv   truth for 2025 storms

Outputs:
  official_forecast/forecast_points.csv
  official_forecast/best_track_points.csv
  official_forecast/paired_errors.csv
  official_forecast/build_report.json

Rules follow the official task doc: leads 6/12/18/24/30/36 only, truth matched on
storm id + valid time, linear interpolation allowed only when the bracketing
truth points are <= 9 hours apart, great-circle error in km. Forecast positions
are never interpolated.
"""

from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "official_forecast"
JTWC_BDECK = RAW_ROOT / "JTWC" / "best_track" / "2017-2024"
JTWC_FST = RAW_ROOT / "JTWC" / "forecast" / "2025_fst"
IBTRACS = RAW_ROOT / "IBTrACS" / "ibtracs.WP.list.v04r01.csv"

KEEP_LEADS = (6, 12, 18, 24, 30, 36)
MAX_TRUTH_GAP_H = 9.0
EARTH_RADIUS_KM = 6371.0
STORM_RE = re.compile(r"^bwp(\d{2})(\d{4})", re.IGNORECASE)


def parse_coord(raw):
    raw = raw.strip().upper()
    if not raw:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)([NSEW])$", raw)
    if not m:
        return None
    value = float(m.group(1)) / 10.0
    if m.group(2) in ("S", "W"):
        value = -value
    return value


def parse_atcf_time(raw):
    """Parse an ATCF time stamp.

    The f-deck stamps in this archive are 10 digits (YYYYMMDDHH) with no minute
    field at all. strptime("%Y%m%d%H%M") would backtrack and silently read
    "2025061106" as 00:06, so the width is checked explicitly here.
    """
    raw = raw.strip()
    if not raw.isdigit():
        return None
    if len(raw) == 12:
        if raw[10:] != "00":
            return None
        raw = raw[:10]
    elif len(raw) != 10:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def storm_id(basin, number, year):
    return "%s%02d%d" % (basin.strip().upper(), int(number), year)


def split_line(line):
    return [part.strip() for part in line.split(",")]


def load_best_track_decks():
    rows = []
    for path in sorted(JTWC_BDECK.glob("bwp*.dat")):
        m = STORM_RE.match(path.name)
        if not m:
            continue
        year = int(m.group(2))
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = split_line(line)
            if len(parts) < 8 or parts[4].upper() != "BEST":
                continue
            time = parse_atcf_time(parts[2])
            lat = parse_coord(parts[6])
            lon = parse_coord(parts[7])
            if time is None or lat is None or lon is None:
                continue
            rows.append(
                {
                    "storm_id": storm_id(parts[0], parts[1], year),
                    "time": time,
                    "lat": lat,
                    "lon": lon,
                    "source": "JTWC b-deck",
                    "year": year,
                }
            )
    return rows


def load_ibtracs_truth(years):
    """Agency truth rows from IBTrACS for storms with no local b-deck.

    Preference order is JTWC, then CMA. The report records which one was
    actually used so the truth source is never ambiguous.
    """
    rows = []
    with IBTRACS.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
        next(csv.reader(handle))
        col = {name: i for i, name in enumerate(header)}
        lat_col = col["CMA_LAT"]
        lon_col = col["CMA_LON"]
        # ATCF id (e.g. WP012025) is what the f-deck forecast files use, while
        # IBTrACS NUMBER is the WMO id (e.g. 33); they only match via this field.
        atcf_col = col["USA_ATCF_ID"]
        iso = col["ISO_TIME"]
        season = col["SEASON"]
        basin = col["BASIN"]
        number = col["NUMBER"]
        for record in csv.reader(handle):
            if len(record) <= lon_col:
                continue
            year = int(record[season])
            if year not in years or record[basin].strip().upper() != "WP":
                continue
            lat = record[lat_col].strip()
            lon = record[lon_col].strip()
            if not lat or not lon:
                continue
            try:
                time = datetime.strptime(
                    record[iso].strip(), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            atcf_id = record[atcf_col].strip().upper()
            if re.match(r"^WP\d{6}$", atcf_id):
                sid = atcf_id
            else:
                sid = storm_id("WP", record[number], year)
            rows.append(
                {
                    "storm_id": sid,
                    "time": time,
                    "lat": float(lat),
                    "lon": float(lon),
                    "source": "IBTrACS v04r01 (CMA)",
                    "year": year,
                }
            )
    return rows


def load_jtwc_forecasts():
    rows = []
    seen = set()
    lead_hist = defaultdict(int)
    for zip_path in sorted(JTWC_FST.glob("*.zip")):
        m = STORM_RE.match("bwp" + zip_path.stem[2:])
        if not m:
            continue
        year = int(m.group(2))
        with zipfile.ZipFile(zip_path) as archive:
            for entry in archive.namelist():
                if not entry.lower().endswith(".fst"):
                    continue
                text = archive.read(entry).decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    parts = split_line(line)
                    if len(parts) < 8:
                        continue
                    if parts[4].strip().upper() != "JTWC":
                        continue
                    issue = parse_atcf_time(parts[2])
                    if issue is None or issue.hour % 6 != 0:
                        continue
                    try:
                        lead = int(parts[5])
                    except ValueError:
                        continue
                    lat = parse_coord(parts[6])
                    lon = parse_coord(parts[7])
                    if lat is None or lon is None:
                        continue
                    lead_hist[lead] += 1
                    if lead not in KEEP_LEADS:
                        continue
                    sid = storm_id("WP", parts[1], year)
                    key = (sid, issue, lead)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "storm_id": sid,
                            "agency": "JTWC",
                            "issue_time": issue,
                            "lead_hours": lead,
                            "valid_time": issue + timedelta(hours=lead),
                            "forecast_lat": lat,
                            "forecast_lon": lon,
                        }
                    )
    return rows, dict(sorted(lead_hist.items()))


def great_circle_km(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def build_truth_index(rows):
    index = defaultdict(list)
    for row in rows:
        index[row["storm_id"]].append((row["time"], row["lat"], row["lon"]))
    for sid in index:
        index[sid].sort(key=lambda item: item[0])
    return index


def interpolate_truth(points, when):
    if not points:
        return None
    before = None
    after = None
    for point in points:
        if point[0] <= when:
            before = point
        elif after is None:
            after = point
            break
    if before is not None and before[0] == when:
        return before[1], before[2], 0.0
    if after is not None and after[0] == when:
        return after[1], after[2], 0.0
    if before is None or after is None:
        return None
    gap = (after[0] - before[0]).total_seconds() / 3600.0
    if gap > MAX_TRUTH_GAP_H:
        return None
    ratio = (when - before[0]).total_seconds() / (after[0] - before[0]).total_seconds()
    lat = before[1] + (after[1] - before[1]) * ratio
    lon = before[2] + (after[2] - before[2]) * ratio
    return lat, lon, gap


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    forecasts, lead_hist = load_jtwc_forecasts()
    forecast_years = set()
    for row in forecasts:
        forecast_years.add(row["valid_time"].year)
        forecast_years.add(row["issue_time"].year)

    truth_rows = load_best_track_decks()
    covered = set(row["storm_id"] for row in truth_rows)
    forecast_storms = set(row["storm_id"] for row in forecasts)
    missing = sorted(s for s in forecast_storms if s not in covered)
    extra_truth = load_ibtracs_truth(forecast_years)
    truth_rows.extend(extra_truth)

    truth_sorted = sorted(truth_rows, key=lambda r: (r["storm_id"], r["time"]))
    write_csv(
        RAW_ROOT / "best_track_points.csv",
        ["storm_id", "year", "time_utc", "lat", "lon", "source"],
        [
            {
                "storm_id": r["storm_id"],
                "year": r["year"],
                "time_utc": iso(r["time"]),
                "lat": "%.2f" % r["lat"],
                "lon": "%.2f" % r["lon"],
                "source": r["source"],
            }
            for r in truth_sorted
        ],
    )

    forecasts_sorted = sorted(
        forecasts, key=lambda r: (r["storm_id"], r["issue_time"], r["lead_hours"])
    )
    write_csv(
        RAW_ROOT / "forecast_points.csv",
        [
            "storm_id",
            "agency",
            "issue_time_utc",
            "lead_hours",
            "valid_time_utc",
            "forecast_lat",
            "forecast_lon",
        ],
        [
            {
                "storm_id": r["storm_id"],
                "agency": r["agency"],
                "issue_time_utc": iso(r["issue_time"]),
                "lead_hours": r["lead_hours"],
                "valid_time_utc": iso(r["valid_time"]),
                "forecast_lat": "%.2f" % r["forecast_lat"],
                "forecast_lon": "%.2f" % r["forecast_lon"],
            }
            for r in forecasts_sorted
        ],
    )

    index = build_truth_index(truth_rows)
    paired = []
    for row in forecasts_sorted:
        match = interpolate_truth(index.get(row["storm_id"], []), row["valid_time"])
        if match is None:
            continue
        truth_lat, truth_lon, gap = match
        err = great_circle_km(
            row["forecast_lat"], row["forecast_lon"], truth_lat, truth_lon
        )
        paired.append(
            {
                "storm_id": row["storm_id"],
                "agency": row["agency"],
                "issue_time_utc": iso(row["issue_time"]),
                "lead_hours": row["lead_hours"],
                "valid_time_utc": iso(row["valid_time"]),
                "forecast_lat": "%.2f" % row["forecast_lat"],
                "forecast_lon": "%.2f" % row["forecast_lon"],
                "truth_lat": "%.2f" % truth_lat,
                "truth_lon": "%.2f" % truth_lon,
                "truth_gap_h": "%.1f" % gap,
                "error_km": "%.1f" % err,
            }
        )

    write_csv(
        RAW_ROOT / "paired_errors.csv",
        [
            "storm_id",
            "agency",
            "issue_time_utc",
            "lead_hours",
            "valid_time_utc",
            "forecast_lat",
            "forecast_lon",
            "truth_lat",
            "truth_lon",
            "truth_gap_h",
            "error_km",
        ],
        paired,
    )

    per_lead = defaultdict(list)
    for row in paired:
        per_lead[int(row["lead_hours"])].append(float(row["error_km"]))
    summary = {
        "generated_at": iso(datetime.now(timezone.utc)),
        "rules": {
            "kept_leads": list(KEEP_LEADS),
            "max_truth_gap_hours": MAX_TRUTH_GAP_H,
            "forecast_positions_interpolated": False,
        },
        "truth_rows": len(truth_rows),
        "truth_deck_rows": sum(
            1 for r in truth_rows if r["source"].startswith("JTWC")
        ),
        "ibtracs_jtwc_rows": len(extra_truth),
        "storm_years": sorted(forecast_years),
        "forecast_storms": len(forecast_storms),
        "storm_ids_without_bdeck": missing,
        "forecast_points_kept": len(forecasts_sorted),
        "forecast_points_dropped_no_truth": len(forecasts_sorted) - len(paired),
        "lead_histogram_raw_fst": lead_hist,
        "paired_by_lead": {
            str(lead): {
                "n": len(vals),
                "mean_km": round(sum(vals) / len(vals), 1) if vals else None,
                "max_km": round(max(vals), 1) if vals else None,
            }
            for lead, vals in sorted(per_lead.items())
        },
        "mean_error_km_all_leads": (
            round(sum(float(r["error_km"]) for r in paired) / len(paired), 1)
            if paired
            else None
        ),
    }
    (RAW_ROOT / "build_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
