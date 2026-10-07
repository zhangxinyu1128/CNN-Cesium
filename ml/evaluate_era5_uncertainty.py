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
from ml.train import PROJECT_ROOT, haversine_km
from ml.train_residual import constant_velocity_normalized


def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_features(rows: List[Dict[str, Any]], weather_scaler: Dict[str, Any]):
    values = np.asarray([row["x"] for row in rows], dtype=np.float32)
    weather = values[:, :, 6:]
    mean = np.asarray(weather_scaler["mean"], dtype=np.float32)
    std = np.asarray(weather_scaler["std"], dtype=np.float32)
    values[:, :, 6:] = (weather - mean) / std
    targets = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    return torch.from_numpy(values), targets, [str(row["typhoon_id"]) for row in rows]


def raw_predictions(model, rows, weather_scaler, normalizer, device, samples):
    features, _, _ = make_features(rows, weather_scaler)
    loader = DataLoader(TensorDataset(features), batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    draws = []
    for _ in range(samples):
        batches = []
        with torch.no_grad():
            for (batch,) in loader:
                batch = batch.to(device)
                prior = constant_velocity_normalized(batch[:, :, :6], normalizer, 6)
                output = model(batch)
                normalized = prior + torch.cat((output["track"], output["wind"]), dim=-1)
                batches.append(normalized.cpu().numpy())
        prediction = np.concatenate(batches, axis=0) * scales + means
        prediction[..., 0] %= 360.0
        prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)
        prediction[..., 2] = np.maximum(prediction[..., 2], 0.0)
        draws.append(prediction)
    return np.stack(draws, axis=0)


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
    weather_scaler = checkpoint["weather_scaler"]
    model = ResidualTrackCNN(input_features=11, horizons=6, dropout=0.2).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    enable_mc_dropout(model)

    validation_rows = read_rows(args.paired_root / "validation.jsonl")
    test_rows = read_rows(args.paired_root / "test.jsonl")
    validation_draws = raw_predictions(model, validation_rows, weather_scaler, normalizer, device, args.samples)
    test_draws = raw_predictions(model, test_rows, weather_scaler, normalizer, device, args.samples)
    validation_actual = np.asarray([row["target_raw"] for row in validation_rows], dtype=np.float64)
    test_actual = np.asarray([row["target_raw"] for row in test_rows], dtype=np.float64)
    validation_groups = [str(row["typhoon_id"]) for row in validation_rows]
    test_groups = [str(row["typhoon_id"]) for row in test_rows]

    quantiles = np.zeros((6, 3), dtype=np.float64)
    location_radii = np.zeros(6, dtype=np.float64)
    validation_center = np.median(validation_draws, axis=0)
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

    result = {
        "model_version": "era5-500-850-residual-mc-dropout",
        "seed": args.seed,
        "mc_samples": args.samples,
        "checkpoint": str(args.checkpoint.resolve()),
        "calibration": {
            "method": "storm-group split conformal absolute residual quantile",
            "target_coverage": 0.9,
            "validation_windows": len(validation_rows),
            "validation_storms": len(set(validation_groups)),
            "coordinate_quantiles": quantiles.tolist(),
            "location_radius_km": location_radii.tolist(),
        },
        "evaluation": interval_metrics(
            test_draws, test_actual, test_groups, quantiles, location_radii
        ),
        "probability_cone": {
            "horizons_hours": [6, 12, 18, 24, 30, 36],
            "radius_km_90": location_radii.tolist(),
            "interpretation": "historical 90% storm-calibrated location radius; not a real-time probability guarantee",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
