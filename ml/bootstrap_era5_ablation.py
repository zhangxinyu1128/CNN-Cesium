"""Storm-paired bootstrap for the three-seed 500/850 hPa residual ablation."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import torch

from ml.data_audit import PROJECT_ROOT
from ml.models.residual_cnn import ResidualTrackCNN
from ml.train import haversine_km
from ml.train_era5_ablation import VARIANTS, denormalize_predictions, make_inputs, read_rows
from ml.train_residual import constant_velocity_normalized


def predict(run_dir: Path, variant: str, rows, device: torch.device) -> np.ndarray:
    state = torch.load(run_dir / (variant + ".pth"), map_location=device, weights_only=False)
    indices = state["source_feature_indices"]
    features, _, _, _ = make_inputs(rows, indices, scaler=state["weather_scaler"])
    model = ResidualTrackCNN(input_features=len(indices), horizons=6, dropout=0.2).to(device)
    model.load_state_dict(state["model_state_dict"])
    model.eval()
    with torch.no_grad():
        features = features.to(device)
        prior = constant_velocity_normalized(features[:, :, :6], state["track_normalizer"], 6)
        outputs = model(features)
        normalized = prior + torch.cat((outputs["track"], outputs["wind"]), dim=-1)
    return denormalize_predictions(normalized.cpu().numpy(), state["track_normalizer"])


def run(paired_root: Path, ablation_root: Path, output_path: Path, iterations: int, seed: int) -> Dict[str, Any]:
    rows = read_rows(paired_root / "test.jsonl")
    actual = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    storm_ids = np.asarray([str(row["typhoon_id"]) for row in rows])
    residual_runs = []
    for metrics_path in sorted(ablation_root.glob("*/metrics.json")):
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics.get("architecture") == "residual" and {"track_only", "fusion_500_850"}.issubset(metrics.get("variants", {})):
            residual_runs.append((metrics_path.parent, metrics))
    if len(residual_runs) != 3:
        raise ValueError("expected exactly three residual runs with track-only and 500/850 variants; found " + str(len(residual_runs)))

    rng = np.random.default_rng(seed)
    by_run = []
    for run_dir, metrics in residual_runs:
        predictions = {
            name: predict(run_dir, name, rows, torch.device("cpu"))
            for name in ("track_only", "fusion_500_850")
        }
        path_errors = {
            name: haversine_km(value[..., 0], value[..., 1], actual[..., 0], actual[..., 1])
            for name, value in predictions.items()
        }
        groups = sorted(set(storm_ids.tolist()))
        storm_means = {}
        for storm_id in groups:
            mask = storm_ids == storm_id
            storm_means[storm_id] = {
                name: errors[mask].mean(axis=0)
                for name, errors in path_errors.items()
            }
        differences = np.asarray(
            [storm_means[storm]["track_only"] - storm_means[storm]["fusion_500_850"] for storm in groups],
            dtype=np.float64,
        )
        track_values = np.asarray([storm_means[storm]["track_only"] for storm in groups], dtype=np.float64)
        draws = rng.integers(0, len(groups), size=(iterations, len(groups)))
        sampled_diff = differences[draws].mean(axis=1)
        sampled_track = track_values[draws].mean(axis=1)
        leads = []
        for index in range(actual.shape[1]):
            delta = float(differences[:, index].mean())
            track_mean = float(track_values[:, index].mean())
            percent = 100.0 * delta / track_mean if track_mean else 0.0
            pct_draws = 100.0 * sampled_diff[:, index] / np.maximum(sampled_track[:, index], 1e-9)
            leads.append(
                {
                    "lead_hours": (index + 1) * 6,
                    "mean_storm_weighted_mae_improvement_km": delta,
                    "improvement_95ci_km": [float(x) for x in np.quantile(sampled_diff[:, index], [0.025, 0.975])],
                    "relative_improvement_pct": percent,
                    "relative_improvement_95ci_pct": [float(x) for x in np.quantile(pct_draws, [0.025, 0.975])],
                }
            )
        by_run.append(
            {
                "run_id": metrics["run_id"],
                "seed": metrics["seed"],
                "test_windows": len(rows),
                "test_storms": len(groups),
                "bootstrap_unit": "typhoon; mean window MAE within each typhoon, then paired resampling",
                "by_horizon": leads,
            }
        )
    result = {
        "experiment": "storm_paired_bootstrap_era5_residual_ablation",
        "iterations": iterations,
        "bootstrap_seed": seed,
        "comparison": "track-only residual CNN versus 500/850 hPa fusion residual CNN",
        "runs": by_run,
        "note": "Intervals quantify storm-to-storm variation on this paired 2020/2021/2025 test subset, not year-to-year or basin-wide generalization.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "windows")
    parser.add_argument("--ablation-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "ablation")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "storm_bootstrap_residual_500_850.json")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    run(args.paired_root, args.ablation_root, args.output, args.iterations, args.seed)


if __name__ == "__main__":
    main()
