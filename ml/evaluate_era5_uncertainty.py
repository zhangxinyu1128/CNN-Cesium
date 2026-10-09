"""Calibrate uncertainty for the expanded 500/850 hPa residual model."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from ml.evaluate_uncertainty import (
    conformal_group_quantile,
    enable_mc_dropout,
    interval_metrics,
)
from ml.models.residual_cnn import ResidualTrackCNN
from ml.models.track_cnn import TrackCNN
from ml.train import PROJECT_ROOT, haversine_km
from ml.train_residual import constant_velocity_normalized
from ml.uncertainty_regions import calibrate_location_ellipse, ellipse_metrics, location_energy_score


def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_features(
    rows: List[Dict[str, Any]],
    weather_scaler: Dict[str, Any],
    source_feature_indices: Sequence[int],
):
    values = np.asarray([row["x"] for row in rows], dtype=np.float32)
    values = values[:, :, list(source_feature_indices)]
    if weather_scaler:
        weather = values[:, :, 6:]
        mean = np.asarray(weather_scaler["mean"], dtype=np.float32)
        std = np.asarray(weather_scaler["std"], dtype=np.float32)
        values[:, :, 6:] = (weather - mean) / std
    targets = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    return torch.from_numpy(values), targets, [str(row["typhoon_id"]) for row in rows]


def raw_predictions(model, rows, weather_scaler, normalizer, source_feature_indices, architecture, device, samples):
    features, _, _ = make_features(rows, weather_scaler, source_feature_indices)
    loader = DataLoader(TensorDataset(features), batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    draws = []
    for _ in range(samples):
        batches = []
        with torch.no_grad():
            for (batch,) in loader:
                batch = batch.to(device)
                output = model(batch)
                normalized = torch.cat((output["track"], output["wind"]), dim=-1)
                if architecture == "residual":
                    normalized = normalized + constant_velocity_normalized(batch[:, :, :6], normalizer, 6)
                batches.append(normalized.cpu().numpy())
        prediction = np.concatenate(batches, axis=0) * scales + means
        prediction[..., 0] %= 360.0
        prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)
        prediction[..., 2] = np.maximum(prediction[..., 2], 0.0)
        draws.append(prediction)
    return np.stack(draws, axis=0)


def deterministic_predictions(model, rows, weather_scaler, normalizer, source_feature_indices, architecture, device):
    features, _, _ = make_features(rows, weather_scaler, source_feature_indices)
    model.eval()
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    batches = []
    loader = DataLoader(TensorDataset(features), batch_size=512, shuffle=False)
    with torch.no_grad():
        for (batch,) in loader:
            batch = batch.to(device)
            output = model(batch)
            normalized = torch.cat((output["track"], output["wind"]), dim=-1)
            if architecture == "residual":
                normalized = normalized + constant_velocity_normalized(batch[:, :, :6], normalizer, 6)
            batches.append(normalized.cpu().numpy())
    prediction = np.concatenate(batches, axis=0) * scales + means
    prediction[..., 0] %= 360.0
    prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)
    prediction[..., 2] = np.maximum(prediction[..., 2], 0.0)
    return prediction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    checkpoint = torch.load(args.checkpoint, map_location=device, weights_only=False)
    normalizer = checkpoint["track_normalizer"]
    weather_scaler = checkpoint.get("weather_scaler")
    source_feature_indices = checkpoint.get("source_feature_indices")
    if not source_feature_indices:
        raise ValueError("checkpoint does not contain source_feature_indices")
    architecture = checkpoint.get("architecture", "residual")
    model_class = ResidualTrackCNN if architecture == "residual" else TrackCNN
    model = model_class(input_features=len(source_feature_indices), horizons=6, dropout=0.2).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    enable_mc_dropout(model)

    validation_rows = read_rows(args.paired_root / "validation.jsonl")
    test_rows = read_rows(args.paired_root / "test.jsonl")
    validation_draws = raw_predictions(
        model, validation_rows, weather_scaler, normalizer, source_feature_indices, architecture, device, args.samples
    )
    test_draws = raw_predictions(
        model, test_rows, weather_scaler, normalizer, source_feature_indices, architecture, device, args.samples
    )
    validation_center = deterministic_predictions(
        model, validation_rows, weather_scaler, normalizer, source_feature_indices, architecture, device
    )
    test_center = deterministic_predictions(
        model, test_rows, weather_scaler, normalizer, source_feature_indices, architecture, device
    )
    validation_actual = np.asarray([row["target_raw"] for row in validation_rows], dtype=np.float64)
    test_actual = np.asarray([row["target_raw"] for row in test_rows], dtype=np.float64)
    validation_groups = [str(row["typhoon_id"]) for row in validation_rows]
    test_groups = [str(row["typhoon_id"]) for row in test_rows]

    quantiles = np.zeros((6, 3), dtype=np.float64)
    location_radii = np.zeros(6, dtype=np.float64)
    location_ellipses = []
    for horizon in range(6):
        for index in range(3):
            error = validation_actual[:, horizon, index] - validation_center[:, horizon, index]
            if index == 0:
                error = (error + 180.0) % 360.0 - 180.0
            quantiles[horizon, index] = conformal_group_quantile(
                np.abs(error), validation_groups, 0.9
            )
        location_error = haversine_km(
            validation_center[:, horizon, 0], validation_center[:, horizon, 1],
            validation_actual[:, horizon, 0], validation_actual[:, horizon, 1],
        )
        location_radii[horizon] = conformal_group_quantile(
            location_error, validation_groups, 0.9
        )
        ellipse, _ = calibrate_location_ellipse(
            validation_center[:, horizon, :2],
            validation_actual[:, horizon, :2],
            validation_groups,
            0.9,
        )
        location_ellipses.append(ellipse)

    # Preserve a non-contracting cone while retaining each horizon's calibrated shape.
    major_axes = np.maximum.accumulate(
        [item["semi_major_axis_km"] for item in location_ellipses]
    )
    minor_axes = np.maximum.accumulate(
        [item["semi_minor_axis_km"] for item in location_ellipses]
    )
    for index, ellipse in enumerate(location_ellipses):
        ellipse["semi_major_axis_km"] = float(major_axes[index])
        ellipse["semi_minor_axis_km"] = float(minor_axes[index])
        ellipse["area_km2"] = float(np.pi * major_axes[index] * minor_axes[index])

    evaluation = interval_metrics(
        test_draws, test_actual, test_groups, quantiles, location_radii
    )
    for horizon, row in enumerate(evaluation["by_horizon"]):
        row["ellipse_90"] = ellipse_metrics(
            test_center[:, horizon, :2],
            test_actual[:, horizon, :2],
            test_groups,
            location_ellipses[horizon],
        )
        row["energy_score_km"] = location_energy_score(
            test_draws[:, :, horizon, :2].transpose(1, 0, 2),
            test_actual[:, horizon, :2],
        )

    result = {
        "model_version": f"era5-500-850-{architecture}-mc-dropout",
        "seed": args.seed,
        "mc_samples": args.samples,
        "checkpoint": str(args.checkpoint.resolve()),
        "source_feature_indices": source_feature_indices,
        "calibration": {
            "method": "storm-group split conformal absolute residual quantile",
            "target_coverage": 0.9,
            "validation_windows": len(validation_rows),
            "validation_storms": len(set(validation_groups)),
            "coordinate_quantiles": quantiles.tolist(),
            "location_radius_km": location_radii.tolist(),
            "location_ellipse_90": location_ellipses,
        },
        "evaluation": evaluation,
        "probability_cone": {
            "horizons_hours": [6, 12, 18, 24, 30, 36],
            "radius_km_90": location_radii.tolist(),
            "location_ellipse_90": location_ellipses,
            "interpretation": "historical 90% storm-group calibrated location regions; not a real-time probability guarantee or hazard probability",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
