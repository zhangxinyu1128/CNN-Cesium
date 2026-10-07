"""Explore paired residual-model errors by recent track-turn category."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import torch

from ml.bootstrap_era5_ablation import predict
from ml.data_audit import PROJECT_ROOT
from ml.train import haversine_km
from ml.train_era5_ablation import read_rows


def segment(lon1: float, lat1: float, lon2: float, lat2: float) -> Tuple[float, float]:
    radians = np.pi / 180.0
    dlon = ((lon2 - lon1 + 180.0) % 360.0 - 180.0) * radians
    lat1r, lat2r = lat1 * radians, lat2 * radians
    y = np.sin(dlon) * np.cos(lat2r)
    x = np.cos(lat1r) * np.sin(lat2r) - np.sin(lat1r) * np.cos(lat2r) * np.cos(dlon)
    bearing = (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0
    distance = float(haversine_km(np.asarray(lon2), np.asarray(lat2), np.asarray(lon1), np.asarray(lat1)))
    return float(bearing), distance


def classify_turns(rows: List[Dict[str, Any]], normalizer: Dict[str, Any]) -> List[str]:
    means = np.asarray(normalizer["mean"], dtype=np.float64)
    scales = np.asarray(normalizer["std"], dtype=np.float64)
    groups = []
    for record in rows:
        history = np.asarray([step[:6] for step in record["x"]], dtype=np.float64)
        raw = history * scales + means
        first_bearing, first_distance = segment(*raw[-3, :2], *raw[-2, :2])
        second_bearing, second_distance = segment(*raw[-2, :2], *raw[-1, :2])
        if min(first_distance, second_distance) < 50.0:
            groups.append("weak_motion")
            continue
        turn = abs((second_bearing - first_bearing + 180.0) % 360.0 - 180.0)
        if turn >= 30.0:
            groups.append("turning_ge_30deg")
        elif turn <= 15.0:
            groups.append("steady_le_15deg")
        else:
            groups.append("moderate_15_30deg")
    return groups


def run(paired_root: Path, ablation_root: Path, output_path: Path, iterations: int, seed: int) -> Dict[str, Any]:
    rows = read_rows(paired_root / "test.jsonl")
    actual = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    storm_ids = np.asarray([str(row["typhoon_id"]) for row in rows])
    metrics_files = []
    for path in sorted(ablation_root.glob("*/metrics.json")):
        metrics = json.loads(path.read_text(encoding="utf-8"))
        if metrics.get("architecture") == "residual" and {"track_only", "fusion_500_850"}.issubset(metrics.get("variants", {})):
            metrics_files.append((path.parent, metrics))
    if len(metrics_files) != 3:
        raise ValueError("expected exactly three residual seeds; found " + str(len(metrics_files)))

    results = []
    for run_dir, metrics in metrics_files:
        baseline_checkpoint = torch.load(run_dir / "track_only.pth", map_location="cpu", weights_only=False)
        groups = classify_turns(rows, baseline_checkpoint["track_normalizer"])
        predictions = {
            name: predict(run_dir, name, rows, torch.device("cpu"))
            for name in ("track_only", "fusion_500_850")
        }
        errors = {
            name: haversine_km(value[..., 0], value[..., 1], actual[..., 0], actual[..., 1])
            for name, value in predictions.items()
        }
        rng = np.random.default_rng(seed + int(metrics["seed"]))
        group_results = {}
        for group in ("turning_ge_30deg", "steady_le_15deg", "moderate_15_30deg", "weak_motion"):
            mask = np.asarray(groups) == group
            storms = sorted(set(storm_ids[mask].tolist()))
            if not storms:
                group_results[group] = {"windows": 0, "storms": 0, "by_horizon": []}
                continue
            storm_errors = {
                name: np.asarray([value[mask & (storm_ids == storm)].mean(axis=0) for storm in storms])
                for name, value in errors.items()
            }
            draws = rng.integers(0, len(storms), size=(iterations, len(storms)))
            differences = storm_errors["track_only"] - storm_errors["fusion_500_850"]
            sampled = differences[draws].mean(axis=1)
            horizon_rows = []
            for index in range(actual.shape[1]):
                delta = float(differences[:, index].mean())
                interval = np.quantile(sampled[:, index], [0.025, 0.975])
                horizon_rows.append(
                    {
                        "lead_hours": 6 * (index + 1),
                        "track_only_storm_weighted_mae_km": float(storm_errors["track_only"][:, index].mean()),
                        "fusion_storm_weighted_mae_km": float(storm_errors["fusion_500_850"][:, index].mean()),
                        "fusion_improvement_km": delta,
                        "improvement_95ci_km": [float(interval[0]), float(interval[1])],
                    }
                )
            group_results[group] = {"windows": int(mask.sum()), "storms": len(storms), "by_horizon": horizon_rows}
        results.append({"seed": metrics["seed"], "run_id": metrics["run_id"], "groups": group_results})

    output = {
        "experiment": "recent_turn_case_analysis_500_850_residual",
        "turn_definition": "course change over the latest two input segments; turning >=30 degrees, steady <=15 degrees; each segment must be >=50 km",
        "test_windows": len(rows),
        "iterations": iterations,
        "bootstrap_unit": "typhoon within each event group",
        "runs": results,
        "limitations": [
            "Exploratory case strata; windows overlap and confidence intervals resample storms, not windows.",
            "Nearshore analysis requires a verified coastline vector dataset, which is not present locally.",
            "Rapid-intensification analysis is deferred until the source wind-speed unit is verified.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "windows")
    parser.add_argument("--ablation-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "ablation")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "turn_case_analysis.json")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=2030)
    args = parser.parse_args()
    run(args.paired_root, args.ablation_root, args.output, args.iterations, args.seed)


if __name__ == "__main__":
    main()
