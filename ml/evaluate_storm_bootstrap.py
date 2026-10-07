"""Compare residual CNN and constant-velocity errors with storm-cluster bootstrap CIs."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.models.residual_cnn import ResidualTrackCNN
from ml.train import PROJECT_ROOT, WindowDataset, haversine_km
from ml.train_residual import constant_velocity_normalized


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def storm_average(errors: np.ndarray, storm_ids: Sequence[str]) -> Tuple[List[str], np.ndarray]:
    identifiers = sorted(set(storm_ids))
    values = np.stack([errors[np.asarray(storm_ids) == identifier].mean(axis=0) for identifier in identifiers])
    return identifiers, values


def storm_power_tertiles(power: np.ndarray, storm_ids: Sequence[str]) -> Tuple[float, float]:
    if len(power) != len(storm_ids) or len(power) == 0:
        raise ValueError("power and storm_ids must be non-empty and aligned")
    storm_values = [
        float(np.median(power[np.asarray(storm_ids) == identifier]))
        for identifier in sorted(set(storm_ids))
    ]
    lower, upper = np.quantile(storm_values, [1.0 / 3.0, 2.0 / 3.0])
    return float(lower), float(upper)


def assign_power_tertiles(power: np.ndarray, thresholds: Tuple[float, float]) -> np.ndarray:
    lower, upper = thresholds
    return np.where(power <= lower, 0, np.where(power <= upper, 1, 2))


def bootstrap_mean_ci(
    improvements: np.ndarray, iterations: int, seed: int
) -> Tuple[float, float, float]:
    if improvements.ndim != 1 or improvements.size < 2 or iterations < 1:
        raise ValueError("bootstrap requires at least two storm-level values and positive iterations")
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, improvements.size, size=(iterations, improvements.size))
    sampled_means = improvements[indexes].mean(axis=1)
    lower, upper = np.quantile(sampled_means, [0.025, 0.975])
    return float(improvements.mean()), float(lower), float(upper)


def predict_path_errors(
    model: ResidualTrackCNN,
    dataset: WindowDataset,
    device: torch.device,
    normalizer: Dict[str, Any],
    horizons: int,
) -> Tuple[np.ndarray, np.ndarray]:
    model.eval()
    outputs = []
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    with torch.no_grad():
        for features, _ in loader:
            features = features.to(device)
            prior = constant_velocity_normalized(features, normalizer, horizons)
            residual = model(features)
            outputs.append((prior + torch.cat((residual["track"], residual["wind"]), dim=-1)).cpu().numpy())
    normalized = np.concatenate(outputs, axis=0)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    prediction = normalized * scales + means
    prediction[..., 0] %= 360.0
    prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)

    features = dataset.features_tensor.numpy().astype(np.float64)
    all_means = np.asarray(normalizer["mean"], dtype=np.float64)
    all_scales = np.asarray(normalizer["std"], dtype=np.float64)
    last = features[:, -1, :] * all_scales + all_means
    steps = np.arange(1, horizons + 1, dtype=np.float64)[None, :]
    baseline_lng = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
    baseline_lat = np.clip(last[:, None, 1] + steps * last[:, None, 5], -90.0, 90.0)
    actual = dataset.raw_targets_tensor.numpy()
    cnn_error = haversine_km(prediction[..., 0], prediction[..., 1], actual[..., 0], actual[..., 1])
    baseline_error = haversine_km(baseline_lng, baseline_lat, actual[..., 0], actual[..., 1])
    return cnn_error, baseline_error


def locate_run(root: Path) -> Path:
    candidates = sorted((root / "runs").glob("*/best.pth"))
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one run checkpoint under {root / 'runs'}, found {len(candidates)}")
    return candidates[0].parent


def run(args: argparse.Namespace) -> Dict[str, Any]:
    manifest = load_json(args.data_root / "processed" / "manifest.json")
    normalizer = manifest["preprocessing"]["normalizer"]
    dataset = WindowDataset(args.data_root / "processed" / "test.jsonl")
    training_data = WindowDataset(args.data_root / "processed" / "train.jsonl")
    power_mean = normalizer["mean"][3]
    power_std = normalizer["std"][3]
    train_power = training_data.features_tensor[:, -1, 3].numpy() * power_std + power_mean
    test_power = dataset.features_tensor[:, -1, 3].numpy() * power_std + power_mean
    power_thresholds = storm_power_tertiles(train_power, training_data.track_ids)
    power_groups = assign_power_tertiles(test_power, power_thresholds)
    power_labels = ("low", "medium", "high")
    device = torch.device(args.device)
    per_seed: Dict[str, Any] = {}

    for run_root in args.run_roots:
        run_dir = locate_run(run_root)
        config = load_json(run_dir / "config.json")
        seed = int(config["seed"])
        state = torch.load(run_dir / "best.pth", map_location=device, weights_only=False)
        model = ResidualTrackCNN(
            input_features=config["input_features"],
            horizons=config["target_steps"],
            dropout=config["dropout"],
        ).to(device)
        model.load_state_dict(state["model_state_dict"])
        cnn_errors, baseline_errors = predict_path_errors(
            model, dataset, device, normalizer, config["target_steps"]
        )
        storm_ids, cnn_by_storm = storm_average(cnn_errors, dataset.track_ids)
        baseline_ids, baseline_by_storm = storm_average(baseline_errors, dataset.track_ids)
        if storm_ids != baseline_ids:
            raise ValueError("model and baseline storm sets differ")

        horizon_results = []
        for horizon in range(config["target_steps"]):
            improvements = baseline_by_storm[:, horizon] - cnn_by_storm[:, horizon]
            mean, lower, upper = bootstrap_mean_ci(
                improvements, args.iterations, args.bootstrap_seed + seed + horizon
            )
            horizon_results.append(
                {
                    "lead_hours": 6 * (horizon + 1),
                    "storm_weighted_cnn_mae_km": float(cnn_by_storm[:, horizon].mean()),
                    "storm_weighted_baseline_mae_km": float(baseline_by_storm[:, horizon].mean()),
                    "mean_paired_improvement_km": mean,
                    "paired_improvement_ci95_km": [lower, upper],
                    "ci_excludes_zero": lower > 0.0,
                }
            )
        stratified = {}
        for group_index, label in enumerate(power_labels):
            selected = power_groups == group_index
            selected_ids = [identifier for identifier, keep in zip(dataset.track_ids, selected) if keep]
            if not selected.any():
                continue
            group_storm_ids, group_cnn = storm_average(cnn_errors[selected], selected_ids)
            baseline_storm_ids, group_baseline = storm_average(baseline_errors[selected], selected_ids)
            if group_storm_ids != baseline_storm_ids:
                raise ValueError(f"model and baseline storm sets differ in {label} power group")
            group_horizons = []
            for horizon in range(config["target_steps"]):
                improvements = group_baseline[:, horizon] - group_cnn[:, horizon]
                mean, lower, upper = bootstrap_mean_ci(
                    improvements,
                    args.iterations,
                    args.bootstrap_seed + seed + horizon + 100 * (group_index + 1),
                )
                group_horizons.append(
                    {
                        "lead_hours": 6 * (horizon + 1),
                        "storm_weighted_cnn_mae_km": float(group_cnn[:, horizon].mean()),
                        "storm_weighted_baseline_mae_km": float(group_baseline[:, horizon].mean()),
                        "mean_paired_improvement_km": mean,
                        "paired_improvement_ci95_km": [lower, upper],
                        "ci_excludes_zero": lower > 0.0,
                    }
                )
            stratified[label] = {
                "sample_count": int(selected.sum()),
                "storm_count": len(group_storm_ids),
                "by_horizon": group_horizons,
            }
        per_seed[str(seed)] = {
            "best_epoch": int(load_json(run_dir / "test_metrics.json")["best_epoch"]),
            "storm_count": len(storm_ids),
            "by_horizon": horizon_results,
            "power_tertiles": stratified,
        }

    return {
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "split_file_sha256": manifest["preprocessing"]["split_file_sha256"],
        "test_window_count": len(dataset),
        "test_storm_count": len(set(dataset.track_ids)),
        "bootstrap": {
            "unit": "storm",
            "iterations": args.iterations,
            "confidence_level": 0.95,
            "seed": args.bootstrap_seed,
            "estimand": "equal-weighted mean per-storm path-MAE improvement over constant velocity",
        },
        "intensity_stratification": {
            "feature": "input power at the forecast origin",
            "method": "low/middle/high tertiles of each training storm's median input power; thresholds are train-only",
            "training_storm_power_tertile_thresholds": list(power_thresholds),
            "labels_are_official_categories": False,
        },
        "seeds": per_seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--run-roots",
        type=Path,
        nargs="+",
        default=[
            PROJECT_ROOT / "artifacts" / "residual",
            PROJECT_ROOT / "artifacts" / "residual_seed_sensitivity" / "seed_2027",
            PROJECT_ROOT / "artifacts" / "residual_seed_sensitivity" / "seed_2028",
        ],
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts" / "residual" / "storm_bootstrap.json"
    )
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--bootstrap-seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
