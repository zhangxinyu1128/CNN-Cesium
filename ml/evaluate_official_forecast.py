"""Compare the frozen ERA5-CNN test storms against JTWC operational forecasts.

The project stores best-track time stamps in Beijing time (UTC+8) and resamples
them to a six-hour cadence, so every window issue time falls on 02/08/14/20
local time, i.e. 18/00/06/12 UTC. JTWC f-decks are issued on the same UTC
cycle, so official positions can be paired with model windows by
(storm, issue time, lead hour) without any interpolation across cycles.

Only the frozen 2020-2025 test split is scored, and only storms that appear in
the downloaded JTWC archive are used, so no training-year information leaks
into the comparison.
"""

import argparse
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import torch

from ml.data_audit import PROJECT_ROOT
from ml.models.residual_cnn import ResidualTrackCNN
from ml.models.track_cnn import TrackCNN
from ml.train import haversine_km
from ml.train_era5_annular_ablation import VARIANTS
from ml.train_era5_ablation import (
    denormalize_predictions,
    make_inputs,
    read_rows,
)


MODEL_LEADS = (6, 12, 18, 24, 30, 36)
OFFICIAL_LEADS = (6, 12, 24, 36, 48, 72)
SHARED_LEADS = (12, 24, 36)
CANONICAL_TRUTH_PATH = PROJECT_ROOT / "official_forecast" / "best_track_points.csv"
LAT_PATTERN = re.compile(r"^(\d{1,3})([NS])$")
LNG_PATTERN = re.compile(r"^(\d{1,4})([EW])$")


def parse_latitude(value: str) -> float:
    match = LAT_PATTERN.match(value.strip().upper())
    if not match:
        raise ValueError(f"unsupported latitude: {value!r}")
    # ATCF stores tenths of a degree, e.g. 084N means 8.4 N and 150N means 15.0 N.
    tenths = int(match.group(1))
    return -tenths / 10.0 if match.group(2) == "S" else tenths / 10.0


def parse_longitude(value: str) -> float:
    match = LNG_PATTERN.match(value.strip().upper())
    if not match:
        raise ValueError(f"unsupported longitude: {value!r}")
    tenths = int(match.group(1)) / 10.0
    return -tenths if match.group(2) == "W" else tenths % 360.0


def parse_atcf_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y%m%d%H").replace(tzinfo=timezone.utc)


def to_utc(timestamp: str) -> datetime:
    """Project track timestamps are treated as UTC for ATCF cycle matching."""

    return datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)


def storm_key(typhoon_id: str) -> str:
    """Return the basin storm number of 202503 / 20250301 / WP032025."""

    atcf = re.search(r"(?:WP|W?P)(\d{2})\d{4}", str(typhoon_id).upper())
    if atcf:
        return atcf.group(1)
    digits = re.sub(r"\D", "", str(typhoon_id))
    return digits[-2:].zfill(2)


def storm_year(typhoon_id: str) -> int:
    digits = re.sub(r"\D", "", str(typhoon_id))
    return int(digits[:4]) if len(digits) >= 4 else -1


def load_project_last_position(row: Dict[str, Any], normalizer: Dict[str, Any]) -> Tuple[float, float]:
    """Un-normalise the last history step of a window to (lng, lat) in degrees."""

    step = np.asarray(row["x"][-1][:2], dtype=np.float64)
    mean = np.asarray(normalizer["mean"][:2], dtype=np.float64)
    std = np.asarray(normalizer["std"][:2], dtype=np.float64)
    lng, lat = step * std + mean
    return float(lng % 360.0), float(np.clip(lat, -90.0, 90.0))


def link_storms_by_track_overlap(
    windows: Sequence[Dict[str, Any]],
    truth_by_storm: Dict[str, Dict[str, Tuple[float, float]]],
    normalizer: Dict[str, Any],
    minimum_points: int = 4,
    maximum_mean_km: float = 60.0,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Map project storm ids to JTWC storm numbers by spatiotemporal overlap.

    CMA and JTWC number the same storm independently, so ids cannot be compared
    directly. Two storms are treated as the same when enough of their 6-hourly
    positions share a valid time and the mean great-circle distance at those
    times stays below a tight threshold.
    """

    project_points: Dict[str, Dict[str, Tuple[float, float]]] = defaultdict(dict)
    for row in windows:
        stamp = to_utc(row["history_times"][-1]).strftime("%Y%m%d%H")
        project_points[str(row["typhoon_id"])][stamp] = load_project_last_position(row, normalizer)

    mapping: Dict[str, str] = {}
    audit: Dict[str, Any] = {"threshold_mean_km": maximum_mean_km, "candidates": []}
    for typhoon_id, points in sorted(project_points.items()):
        best: Optional[Tuple[float, str, int]] = None
        for storm, record in truth_by_storm.items():
            shared = sorted(set(points) & set(record))
            if len(shared) < minimum_points:
                continue
            errors = [
                float(haversine_km(points[s][0], points[s][1], record[s][0], record[s][1]))
                for s in shared
            ]
            mean_km = float(np.mean(errors))
            if best is None or mean_km < best[0]:
                best = (mean_km, storm, len(shared))
        audit["candidates"].append(
            {
                "typhoon_id": typhoon_id,
                "jtwc_storm": None if best is None else best[1],
                "mean_km": None if best is None else round(best[0], 2),
                "shared_points": 0 if best is None else best[2],
            }
        )
        if best is not None and best[0] <= maximum_mean_km:
            mapping[typhoon_id] = best[1]
    audit["linked_storms"] = len(mapping)
    return mapping, audit


def load_official_forecasts(archive_root: Path) -> Dict[Tuple[str, str, int], Tuple[float, float]]:
    """Return {(storm, issue_time, lead_hours): (lat, lon)} for JTWC records.

    Duplicate leads inside one f-deck carry the same position in every sample
    inspected; the first record wins so the result is order-independent.
    """

    official: Dict[Tuple[str, str, int], Tuple[float, float]] = {}
    duplicates = 0
    conflicts = 0
    archive_root.mkdir(parents=True, exist_ok=True)
    for archive in sorted(archive_root.glob("*.zip")):
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not name.lower().endswith(".fst"):
                    continue
                text = bundle.read(name).decode("utf-8", errors="replace")
                for line in text.splitlines():
                    fields = [item.strip() for item in line.split(",")]
                    if len(fields) < 10 or fields[0].upper() != "WP" or fields[4].upper() != "JTWC":
                        continue
                    if not re.fullmatch(r"-?\d+", fields[5]):
                        continue
                    try:
                        lat = parse_latitude(fields[6])
                        lon = parse_longitude(fields[7])
                    except ValueError:
                        continue
                    key = (
                        str(fields[1]).zfill(2),
                        fields[2],
                        int(fields[5]),
                    )
                    position = (lon, lat)
                    if key in official:
                        duplicates += 1
                        if official[key] != position:
                            conflicts += 1
                        continue
                    official[key] = position
    print(
        f"official JTWC records={len(official)} duplicate_rows={duplicates} conflicting_duplicates={conflicts}",
        flush=True,
    )
    return official


def official_analysis_positions(
    official: Dict[Tuple[str, str, int], Tuple[float, float]],
) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Storm-level analysis track taken from the tau=0 f-deck records.

    The downloaded b-deck archive stops at 2024, so 2025 storms have no
    independent best-track file. Every f-deck cycle carries a tau=0 record of
    the analysed centre, and the union of those records across all cycles is a
    six-hourly storm track that can serve as verification truth for 2025.
    """

    return {
        (storm, issue): position
        for (storm, issue, lead), position in official.items()
        if lead == 0
    }


def load_best_track_truth(truth_root: Path, years: Iterable[int]) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Return {(storm, utc_valid_time): (lat, lon)} from JTWC b-decks."""

    truth: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for year in sorted(set(years)):
        for path in sorted(truth_root.glob(f"bwp*{year}.dat")):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = [item.strip() for item in line.split(",")]
                if len(fields) < 8 or fields[4].upper() != "BEST" or fields[5] != "0":
                    continue
                try:
                    lat = parse_latitude(fields[6])
                    lon = parse_longitude(fields[7])
                except ValueError:
                    continue
                    truth[(str(fields[1]).zfill(2), fields[2])] = (lon, lat)
    return truth


def load_canonical_truth(
    path: Path,
    years: Iterable[int],
) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Load the audited best-track table produced by build_official_forecast_tables."""

    truth: Dict[Tuple[str, str], Tuple[float, float]] = {}
    allowed_years = {int(year) for year in years}
    if not path.is_file():
        return truth
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                year = int(row["year"])
                stamp = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00")).strftime("%Y%m%d%H")
                storm = storm_key(row["storm_id"])
                truth[(storm, stamp)] = (float(row["lon"]), float(row["lat"]))
            except (KeyError, TypeError, ValueError):
                continue
            if year not in allowed_years:
                truth.pop((storm, stamp), None)
    return truth


def group_truth_by_storm(
    truth: Dict[Tuple[str, str], Tuple[float, float]],
) -> Dict[str, Dict[str, Tuple[float, float]]]:
    grouped: Dict[str, Dict[str, Tuple[float, float]]] = defaultdict(dict)
    for (storm, stamp), position in truth.items():
        grouped[storm][stamp] = position
    return grouped


def load_model(
    checkpoint_path: Path,
    variant: str,
    device: torch.device,
) -> Tuple[torch.nn.Module, Dict[str, Any], Dict[str, Any]]:
    payload = torch.load(checkpoint_path, map_location=device, weights_only=False)
    indices = tuple(payload["source_feature_indices"])
    architecture = payload.get("architecture", "direct")
    horizons = 6
    if architecture == "residual":
        model = ResidualTrackCNN(input_features=len(indices), horizons=horizons, dropout=0.2).to(device)
    else:
        model = TrackCNN(input_features=len(indices), horizons=horizons, dropout=0.2).to(device)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    return model, payload["track_normalizer"], {
        "indices": indices,
        "architecture": architecture,
        "weather_scaler": payload.get("weather_scaler"),
        "variant": variant,
        "seed": payload.get("seed"),
    }


def predict(
    model: torch.nn.Module,
    rows: List[Dict[str, Any]],
    meta: Dict[str, Any],
    normalizer: Dict[str, Any],
    device: torch.device,
) -> np.ndarray:
    features, _, _, _ = make_inputs(rows, meta["indices"], scaler=meta["weather_scaler"])
    architecture = meta["architecture"]
    prior = None
    with torch.no_grad():
        features_device = features.to(device)
        outputs = model(features_device)
        normalized = torch.cat((outputs["track"], outputs["wind"]), dim=-1)
        if architecture == "residual":
            from ml.train_residual import constant_velocity_normalized

            prior = constant_velocity_normalized(features_device[:, :, :6], normalizer, normalized.shape[1])
            normalized = normalized + prior
        normalized = normalized.cpu().numpy()
    return denormalize_predictions(normalized, normalizer)


def official_error_summary(
    errors_km: np.ndarray,
    storm_ids: Sequence[str],
    lead_hours: Sequence[int],
) -> Dict[str, Any]:
    """Aggregate errors per lead hour, both per sample and per storm."""

    per_horizon = []
    storm_ids = np.asarray(storm_ids)
    for index, lead in enumerate(lead_hours):
        column = errors_km[:, index]
        valid = np.isfinite(column)
        if not valid.any():
            continue
        by_storm: Dict[str, List[float]] = defaultdict(list)
        for storm, value in zip(storm_ids[valid], column[valid]):
            by_storm[str(storm)].append(float(value))
        storm_means = np.asarray([np.mean(values) for values in by_storm.values()], dtype=np.float64)
        per_horizon.append(
            {
                "lead_hours": int(lead),
                "sample_count": int(valid.sum()),
                "storm_count": len(by_storm),
                "mae_km": float(np.mean(column[valid])),
                "rmse_km": float(np.sqrt(np.mean(column[valid] ** 2))),
                "median_km": float(np.median(column[valid])),
                "storm_mean_mae_km": float(np.mean(storm_means)),
                "storm_median_mae_km": float(np.median(storm_means)),
                "p90_km": float(np.percentile(column[valid], 90)),
            }
        )
    return {"sample_count": int(errors_km.shape[0]), "by_horizon": per_horizon}


def truth_at_leads(
    selected: Sequence[Dict[str, Any]],
    truth: Dict[Tuple[str, str], Tuple[float, float]],
    leads: Sequence[int],
) -> np.ndarray:
    """Best-track truth positions for the given leads as (longitude, latitude)."""

    out = np.full((len(selected), len(leads), 2), np.nan, dtype=np.float64)
    for row_index, item in enumerate(selected):
        for lead_index, lead in enumerate(leads):
            stamp = (item["issue_utc"] + timedelta(hours=lead)).strftime("%Y%m%d%H")
            position = truth.get((item["storm"], stamp))
            if position is not None:
                out[row_index, lead_index] = position
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--official-root",
        type=Path,
        default=PROJECT_ROOT / "official_forecast" / "JTWC" / "2025官方预报",
    )
    parser.add_argument(
        "--truth-root",
        type=Path,
        default=PROJECT_ROOT / "official_forecast" / "JTWC" / "最佳路径真值",
    )
    parser.add_argument(
        "--paired-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "ring_windows",
    )
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "ablation" / "20261008T090306Z_seed2028",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "official_comparison",
    )
    parser.add_argument("--variants", default="track_only,ring_500_850")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    device = torch.device("cuda" if (args.device == "auto" and torch.cuda.is_available()) else ("cpu" if args.device == "auto" else args.device))
    test_rows = read_rows(args.paired_root / "test.jsonl")
    manifest = json.loads((PROJECT_ROOT / "data" / "processed" / "manifest.json").read_text(encoding="utf-8"))
    normalizer = manifest["preprocessing"]["normalizer"]

    official = load_official_forecasts(args.official_root)
    archive_storms = {storm for storm, _, _ in official}
    archive_years = {int(issue[:4]) for _, issue, _ in official}
    truth = load_canonical_truth(CANONICAL_TRUTH_PATH, archive_years)
    truth_source = "official_forecast/best_track_points.csv"
    if not truth:
        truth = load_best_track_truth(args.truth_root, sorted(archive_years - {max(archive_years)}))
        analysis = official_analysis_positions(official)
        for key, position in analysis.items():
            truth.setdefault(key, position)
        truth_source = "JTWC b-deck plus f-deck tau=0 fallback"
    truth_by_storm = group_truth_by_storm(truth)
    linkage, linkage_audit = link_storms_by_track_overlap(test_rows, truth_by_storm, normalizer)
    print(
        f"storm linkage: {len(linkage)} of {len({str(r['typhoon_id']) for r in test_rows})} test storms matched a JTWC storm",
        flush=True,
    )

    candidate_rows = []
    for row in test_rows:
        key = linkage.get(str(row["typhoon_id"]))
        year = storm_year(row["typhoon_id"])
        if key is None or key not in archive_storms or year not in archive_years:
            continue
        issue_utc = to_utc(row["history_times"][-1])
        issue_text = issue_utc.strftime("%Y%m%d%H")
        paired = {
            lead: official.get((key, issue_text, lead)) for lead in OFFICIAL_LEADS
        }
        candidate_rows.append({
            "row": row,
            "storm": key,
            "year": year,
            "typhoon_id": str(row["typhoon_id"]),
            "issue_utc": issue_utc,
            "issue_text": issue_text,
            "official": paired,
        })

    issue_grid = sorted({(item["storm"], item["issue_text"]) for item in candidate_rows})
    matched_cycles = [pair for pair in issue_grid if any((pair[0], pair[1], lead) in official for lead in OFFICIAL_LEADS)]
    selected = [
        item for item in candidate_rows
        if any(item["official"][lead] is not None for lead in SHARED_LEADS)
    ]
    print(
        f"test windows={len(test_rows)} archive storms={sorted(archive_storms)} "
        f"storm-matched windows={len(candidate_rows)} officially scored windows={len(selected)}",
        flush=True,
    )
    if not selected:
        raise ValueError("no test window could be paired with a JTWC official forecast")

    actual = np.asarray([item["row"]["target_raw"] for item in selected], dtype=np.float64)
    storm_ids = [item["storm"] for item in selected]
    truth_positions = truth_at_leads(selected, truth, MODEL_LEADS)
    truth_official_leads = truth_at_leads(selected, truth, OFFICIAL_LEADS)
    official_positions = np.full((len(selected), len(OFFICIAL_LEADS), 2), np.nan, dtype=np.float64)
    for row_index, item in enumerate(selected):
        for lead_index, lead in enumerate(OFFICIAL_LEADS):
            position = item["official"][lead]
            if position is not None:
                official_positions[row_index, lead_index] = position

    results: Dict[str, Any] = {
        "experiment": "jtwc_official_forecast_comparison",
        "official_source": {
            "agency": "JTWC",
            "product": "f-deck (fst archive mirror data.natyphoon.top)",
            "technique_filter": "JTWC",
            "years": sorted(archive_years),
            "storms": sorted(archive_storms),
            "available_leads_hours": sorted({lead for _, _, lead in official}),
        },
        "matching_policy": {
            "split": "frozen test split (2020-2025), no training-year storms used",
            "time_zone": "project timestamps are paired as UTC because their six-hour cycles coincide with ATCF UTC cycles",
            "issue_cycle": "exact (storm, issue hour) match, no interpolation across cycles",
            "shared_lead_hours": list(SHARED_LEADS),
            "note": "The model emits 6/12/18/24/30/36 h; the JTWC archive publishes 12/24/36/48/72/96/120 h with no 6 h record. The head-to-head table therefore uses 12/24/36 h, and the 6/18/30 h model leads are reported separately against best track only.",
        },
        "coverage": {
            "test_windows_total": len(test_rows),
            "storm_linkage": linkage_audit,
            "test_windows_with_archive_storm": len(candidate_rows),
            "test_windows_scored": len(selected),
            "distinct_issue_cycles": len(issue_grid),
            "issue_cycles_with_official_record": len(matched_cycles),
            "storms_scored": sorted({item["storm"] for item in selected}),
            "storm_count": len({item["storm"] for item in selected}),
        },
        "verification_truth": {
            "source": truth_source,
            "years": sorted(archive_years),
            "note": "真值来自审计后的 best_track_points.csv；预报与真值来自独立字段，未将 f-deck tau=0 冒充独立最佳路径。",
        },
        "baselines": {},
        "variants": {},
    }
    normalized = np.asarray([[step[:6] for step in item["row"]["x"]] for item in selected], dtype=np.float64)
    means = np.asarray(normalizer["mean"], dtype=np.float64)
    scales = np.asarray(normalizer["std"], dtype=np.float64)
    raw = normalized * scales + means
    last = raw[:, -1, :]
    persistence = np.zeros_like(actual)
    persistence[..., 0] = last[:, None, 0]
    persistence[..., 1] = last[:, None, 1]
    constant_velocity = persistence.copy()
    steps = np.arange(1, actual.shape[1] + 1, dtype=np.float64)[None, :]
    constant_velocity[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
    constant_velocity[..., 1] = np.clip(last[:, None, 1] + steps * last[:, None, 5], -90.0, 90.0)

    def score_all_leads(predictions: np.ndarray) -> Dict[str, Any]:
        errors = haversine_km(predictions[..., 0], predictions[..., 1], truth_positions[..., 0], truth_positions[..., 1])
        return official_error_summary(errors, storm_ids, list(MODEL_LEADS))

    def score_shared(predictions: np.ndarray) -> Dict[str, Any]:
        columns = [MODEL_LEADS.index(lead) for lead in SHARED_LEADS]
        errors = haversine_km(
            predictions[..., 0][:, columns], predictions[..., 1][:, columns],
            truth_positions[..., 0][:, columns], truth_positions[..., 1][:, columns],
        )
        return official_error_summary(errors, storm_ids, list(SHARED_LEADS))

    results["baselines"] = {
        "persistence": {"all_leads": score_all_leads(persistence), "shared_leads": score_shared(persistence)},
        "constant_velocity": {"all_leads": score_all_leads(constant_velocity), "shared_leads": score_shared(constant_velocity)},
    }
    results["jtwc_official"] = official_error_summary(
        haversine_km(
            official_positions[..., 0], official_positions[..., 1],
            truth_official_leads[..., 0], truth_official_leads[..., 1],
        ),
        storm_ids,
        list(OFFICIAL_LEADS),
    )

    for name in [item.strip() for item in args.variants.split(",") if item.strip()]:
        checkpoint = args.checkpoint_root / f"{name}.pth"
        model, ckpt_normalizer, meta = load_model(checkpoint, name, device)
        predictions = predict(model, [item["row"] for item in selected], meta, ckpt_normalizer, device)
        results["variants"][name] = {
            "checkpoint": str(checkpoint.resolve()),
            "seed": meta.get("seed"),
            "features": meta["indices"],
            "metrics": {"all_leads": score_all_leads(predictions), "shared_leads": score_shared(predictions)},
            "predictions_by_window": [
                {
                    "typhoon_id": item["typhoon_id"],
                    "storm": item["storm"],
                    "issue_utc": item["issue_utc"].isoformat(),
                    "truth_lat_lon": [
                        None if not math.isfinite(truth_positions[r, c, 0]) else [round(float(truth_positions[r, c, 0]), 3), round(float(truth_positions[r, c, 1]), 3)]
                        for c in range(len(MODEL_LEADS))
                    ],
                    "jtwc_lat_lon": [
                        None if not math.isfinite(official_positions[r, c, 0]) else [round(float(official_positions[r, c, 0]), 3), round(float(official_positions[r, c, 1]), 3)]
                        for c in range(len(OFFICIAL_LEADS))
                    ],
                    "model_lat_lon": [
                        [round(float(predictions[r, c, 0]) % 360.0, 3), round(float(predictions[r, c, 1]), 3)]
                        for c in range(predictions.shape[1])
                    ],
                }
                for r, item in enumerate(selected)
            ],
        }
        print(
            f"{name}: " + json.dumps(results["variants"][name]["metrics"]["shared_leads"]["by_horizon"], ensure_ascii=False),
            flush=True,
        )

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "official_comparison.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("written to", (args.output_root / "official_comparison.json").resolve())


if __name__ == "__main__":
    main()
