"""Calibrate location uncertainty for the track-only checkpoint served by the API."""

import argparse
import hashlib
import json
from math import ceil
from pathlib import Path
from typing import Dict, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.models.track_cnn import TrackCNN
from ml.train import PROJECT_ROOT, WindowDataset, haversine_km


def storm_group_quantile(scores: np.ndarray, groups: Sequence[str], coverage: float) -> float:
    if not 0 < coverage < 1 or len(scores) != len(groups) or not len(scores):
        raise ValueError("scores/groups must be non-empty and coverage must be between 0 and 1")
    maxima: Dict[str, float] = {}
    for score, group in zip(scores, groups):
        maxima[group] = max(maxima.get(group, 0.0), float(score))
    ordered = np.sort(np.asarray(list(maxima.values()), dtype=np.float64))
    rank = min(ceil((len(ordered) + 1) * coverage), len(ordered))
    return float(ordered[rank - 1])


def enable_mc_dropout(model: torch.nn.Module) -> None:
    model.eval()
    for module in model.modules():
        if isinstance(module, torch.nn.Dropout):
            module.train()


def predict_draws(model, dataset, device, normalizer, samples: int) -> np.ndarray:
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    draws = []
    for _ in range(samples):
        batches = []
        with torch.no_grad():
            for features, _ in loader:
                output = model(features.to(device))
                normalized = torch.cat((output["track"], output["wind"]), dim=-1)
                batches.append(normalized.cpu().numpy())
        prediction = np.concatenate(batches, axis=0) * scales + means
        prediction[..., 0] %= 360.0
        prediction[..., 1] = np.clip(prediction[..., 1], -90.0, 90.0)
        draws.append(prediction)
    return np.stack(draws, axis=0)


def evaluate(draws, actual, groups, radii):
    center = np.median(draws, axis=0)
    result = []
    for index, radius in enumerate(radii):
        errors = haversine_km(
            center[:, index, 0], center[:, index, 1],
            actual[:, index, 0], actual[:, index, 1],
        )
        covered = errors <= radius
        storm_covered = {}
        for value, group in zip(covered, groups):
            storm_covered[group] = storm_covered.get(group, True) and bool(value)
        result.append({
            "lead_hours": 6 * (index + 1),
            "location_radius_90_km": float(radius),
            "location_coverage_90": float(np.mean(covered)),
            "location_storm_coverage_90": float(np.mean(list(storm_covered.values()))),
            "median_error_km": float(np.median(errors)),
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--checkpoint", type=Path, default=PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_20261007.json")
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    if args.samples < 2:
        raise ValueError("at least two MC Dropout samples are required")
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    manifest = json.loads((args.data_root / "processed" / "manifest.json").read_text(encoding="utf-8"))
    checkpoint = torch.load(args.checkpoint, map_location=device, weights_only=False)
    architecture = checkpoint.get("architecture", {})
    model = TrackCNN(
        input_features=6,
        horizons=6,
        dropout=float(architecture.get("dropout", 0.3)),
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    enable_mc_dropout(model)
    normalizer = checkpoint["normalizer"]

    validation = WindowDataset(args.data_root / "processed" / "validation.jsonl")
    test = WindowDataset(args.data_root / "processed" / "test.jsonl")
    validation_draws = predict_draws(model, validation, device, normalizer, args.samples)
    test_draws = predict_draws(model, test, device, normalizer, args.samples)
    validation_center = np.median(validation_draws, axis=0)
    validation_actual = validation.raw_targets_tensor.numpy()
    radii = []
    for horizon in range(6):
        errors = haversine_km(
            validation_center[:, horizon, 0], validation_center[:, horizon, 1],
            validation_actual[:, horizon, 0], validation_actual[:, horizon, 1],
        )
        radii.append(storm_group_quantile(errors, validation.track_ids, 0.9))
    # A forecast cone should not contract with increasing lead time.
    radii = np.maximum.accumulate(np.asarray(radii, dtype=np.float64)).tolist()

    checkpoint_hash = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    output = {
        "model_version": checkpoint.get("model_version", "track-cnn-baseline"),
        "model_type": "track",
        "seed": args.seed,
        "mc_samples": args.samples,
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "checkpoint_sha256": checkpoint_hash,
        "calibration": {
            "method": "storm-group split conformal 90th percentile of location error",
            "split": "validation",
            "storm_count": len(set(validation.track_ids)),
            "window_count": len(validation),
            "target_coverage": 0.9,
            "location_radius_km": radii,
        },
        "evaluation": {
            "split": "frozen_test",
            "storm_count": len(set(test.track_ids)),
            "window_count": len(test),
            "by_horizon": evaluate(
                test_draws, test.raw_targets_tensor.numpy(), test.track_ids, radii
            ),
        },
        "interpretation": "Historical 90% storm-calibrated location radius for this exact checkpoint; not a real-time forecast guarantee or a hazard probability.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
