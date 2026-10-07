"""Evaluate predictive intervals for the residual CNN with MC Dropout."""

import argparse
import json
from pathlib import Path
from math import ceil
from typing import Any, Dict, List, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.models.residual_cnn import ResidualTrackCNN
from ml.train import PROJECT_ROOT, WindowDataset, haversine_km
from ml.train_residual import constant_velocity_normalized


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wrapped_delta(value: np.ndarray) -> np.ndarray:
    return (value + 180.0) % 360.0 - 180.0


def enable_mc_dropout(model: torch.nn.Module) -> None:
    model.train()
    for module in model.modules():
        if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
            module.eval()


def conformal_group_quantile(scores: np.ndarray, groups: Sequence[str], coverage: float) -> float:
    if not 0.0 < coverage < 1.0 or len(scores) != len(groups) or len(scores) == 0:
        raise ValueError("scores/groups must be non-empty and coverage must be between 0 and 1")
    maxima: Dict[str, float] = {}
    for score, group in zip(scores, groups):
        maxima[group] = max(maxima.get(group, 0.0), float(score))
    ordered = np.sort(np.asarray(list(maxima.values()), dtype=np.float64))
    rank = min(ceil((len(ordered) + 1) * coverage), len(ordered))
    return float(ordered[rank - 1])


def storm_coverage(covered: np.ndarray, groups: Sequence[str]) -> float:
    totals: Dict[str, bool] = {}
    for is_covered, group in zip(covered, groups):
        totals[group] = totals.get(group, True) and bool(is_covered)
    return float(np.mean(list(totals.values())))


def raw_predictions(
    model: ResidualTrackCNN,
    dataset: WindowDataset,
    device: torch.device,
    normalizer: Dict[str, Any],
    samples: int,
) -> np.ndarray:
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    draws: List[np.ndarray] = []
    for _ in range(samples):
        batches: List[np.ndarray] = []
        with torch.no_grad():
            for features, _ in loader:
                features = features.to(device)
                prior = constant_velocity_normalized(features, normalizer, 6)
                output = model(features)
                normalized = prior + torch.cat((output["track"], output["wind"]), dim=-1)
                batches.append(normalized.cpu().numpy())
        prediction = np.concatenate(batches, axis=0) * scales + means
        prediction[..., 0] %= 360.0
        prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)
        prediction[..., 2] = np.maximum(prediction[..., 2], 0.0)
        draws.append(prediction)
    return np.stack(draws, axis=0)


def interval_metrics(
    draws: np.ndarray,
    actual: np.ndarray,
    groups: Sequence[str],
    quantiles: np.ndarray = None,
    location_radii_km: np.ndarray = None,
) -> Dict[str, Any]:
    result = []
    for horizon in range(actual.shape[1]):
        point = np.median(draws[:, :, horizon, :], axis=0)
        row: Dict[str, Any] = {"lead_hours": 6 * (horizon + 1)}
        coordinate_coverages = []
        coordinate_widths = []
        for index, name in ((0, "longitude"), (1, "latitude"), (2, "wind")):
            values = draws[:, :, horizon, index]
            center = np.median(values, axis=0)
            if index == 0:
                errors = wrapped_delta(values - center[None, :])
                actual_error = wrapped_delta(actual[:, horizon, index] - center)
            else:
                errors = values - center[None, :]
                actual_error = actual[:, horizon, index] - center
            if quantiles is None:
                lower_error = np.quantile(errors, 0.05, axis=0)
                upper_error = np.quantile(errors, 0.95, axis=0)
                width = upper_error - lower_error
            else:
                lower_error = -quantiles[horizon, index]
                upper_error = quantiles[horizon, index]
                width = np.full(actual.shape[0], 2.0 * quantiles[horizon, index])
            covered = (actual_error >= lower_error) & (actual_error <= upper_error)
            coordinate_coverages.append(covered)
            coordinate_widths.append(width)
            row[f"{name}_coverage_90"] = float(np.mean(covered))
            row[f"{name}_storm_coverage_90"] = storm_coverage(covered, groups)
            row[f"{name}_interval_width"] = float(np.mean(width))
        joint = np.logical_and.reduce(coordinate_coverages)
        row["joint_coverage_90"] = float(np.mean(joint))
        row["joint_storm_coverage_90"] = storm_coverage(joint, groups)
        row["joint_interval_width_proxy"] = float(np.mean(np.sqrt(coordinate_widths[0] ** 2 + coordinate_widths[1] ** 2)))
        lng_error = wrapped_delta(actual[:, horizon, 0] - point[:, 0])
        radius_km = haversine_km(
            point[:, 0], point[:, 1], actual[:, horizon, 0], actual[:, horizon, 1]
        )
        if location_radii_km is None:
            radius_limit = np.quantile(radius_km, 0.9)
        else:
            radius_limit = location_radii_km[horizon]
        row["location_radius_90_km"] = float(radius_limit)
        row["location_coverage_90"] = float(np.mean(radius_km <= radius_limit))
        row["location_storm_coverage_90"] = storm_coverage(radius_km <= radius_limit, groups)
        row["median_prediction_wind_mae_ms"] = float(np.mean(np.abs(point[:, 2] - actual[:, horizon, 2])))
        result.append(row)
    return {"sample_count": int(actual.shape[0]), "mc_samples": int(draws.shape[0]), "by_horizon": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--checkpoint", type=Path, default=PROJECT_ROOT / "artifacts" / "residual" / "checkpoints" / "track_cnn_residual.pth")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "residual" / "uncertainty_metrics.json")
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = torch.device(args.device)
    manifest = load_json(args.data_root / "processed" / "manifest.json")
    dataset = WindowDataset(args.data_root / "processed" / "test.jsonl")
    state = torch.load(args.checkpoint, map_location=device, weights_only=False)
    model = ResidualTrackCNN(input_features=6, horizons=6, dropout=0.3).to(device)
    model.load_state_dict(state["model_state_dict"])
    enable_mc_dropout(model)
    draws = raw_predictions(model, dataset, device, manifest["preprocessing"]["normalizer"], args.samples)
    validation = WindowDataset(args.data_root / "processed" / "validation.jsonl")
    validation_draws = raw_predictions(model, validation, device, manifest["preprocessing"]["normalizer"], args.samples)
    validation_center = np.median(validation_draws, axis=0)
    validation_actual = validation.raw_targets_tensor.numpy()
    calibration_quantiles = np.zeros((6, 3), dtype=np.float64)
    validation_groups = validation.track_ids
    for horizon in range(6):
        for index in range(3):
            error = validation_actual[:, horizon, index] - validation_center[:, horizon, index]
            if index == 0:
                error = wrapped_delta(error)
            calibration_quantiles[horizon, index] = conformal_group_quantile(
                np.abs(error), validation_groups, 0.9
            )
    calibration_location_radii = np.zeros(6, dtype=np.float64)
    for horizon in range(6):
        lng_error = wrapped_delta(validation_actual[:, horizon, 0] - validation_center[:, horizon, 0])
        lat_error = validation_actual[:, horizon, 1] - validation_center[:, horizon, 1]
        radius_km = haversine_km(
            validation_center[:, horizon, 0],
            validation_center[:, horizon, 1],
            validation_actual[:, horizon, 0],
            validation_actual[:, horizon, 1],
        )
        calibration_location_radii[horizon] = conformal_group_quantile(
            radius_km, validation_groups, 0.9
        )
    test_actual = dataset.raw_targets_tensor.numpy()
    metrics = {
        "model_version": "track-cnn-residual-v1-mc-dropout",
        "seed": args.seed,
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "checkpoint": str(args.checkpoint.resolve()),
        "calibration": {
            "method": "split conformal absolute residual quantile",
            "calibration_split": "validation",
            "calibration_unit": "storm; score is the maximum window error within each storm at each horizon",
            "calibration_storm_count": len(set(validation_groups)),
            "target_coverage": 0.9,
            "quantiles": calibration_quantiles.tolist(),
            "location_radius_km": calibration_location_radii.tolist(),
        },
        "uncalibrated_evaluation": interval_metrics(draws, test_actual, dataset.track_ids),
        "evaluation": interval_metrics(
            draws, test_actual, dataset.track_ids, calibration_quantiles, calibration_location_radii
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
