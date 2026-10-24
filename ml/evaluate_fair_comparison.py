"""Evaluate legacy and 12-step models on the identical frozen test windows.

The 12-step dataset stores local-motion features.  This script reconstructs the
last four geographic observations for the legacy 6-channel models, then reports
all models with the same 914-window test set and the same geographic metrics.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.data_audit import PROJECT_ROOT
from ml.features.local_motion import destination_lonlat
from ml.models.multiscale_residual_cnn import MultiScaleResidualCNN
from ml.models.residual_cnn import ResidualTrackCNN
from ml.models.track_cnn import TrackCNN
from ml.train import WindowDataset, haversine_km, metric_summary
from ml.train_residual import constant_velocity_normalized


def load_checkpoint(path: Path) -> Dict[str, Any]:
    return torch.load(path, map_location="cpu")


def geographic_features(dataset: WindowDataset, local_normalizer: Dict[str, Any], old_normalizer: Dict[str, Any]) -> np.ndarray:
    """Decode local rows into the legacy [lng, lat, speed, power, d_lng, d_lat]."""
    local_mean = np.asarray(local_normalizer["mean"], dtype=np.float64)
    local_std = np.asarray(local_normalizer["std"], dtype=np.float64)
    old_mean = np.asarray(old_normalizer["mean"], dtype=np.float64)
    old_std = np.asarray(old_normalizer["std"], dtype=np.float64)
    rows: List[np.ndarray] = []
    for sample, origin in enumerate(dataset.origins):
        raw = dataset.features_tensor[sample].numpy().astype(np.float64) * local_std + local_mean
        # ``origin`` is the final history point.  Each local row is the
        # displacement from the preceding point to the row's current point,
        # therefore decode the history backwards from the origin.
        positions: List[Any] = [None] * len(raw)
        positions[-1] = (float(origin["lng"]) % 360.0, float(origin["lat"]))
        for point_index in range(len(raw) - 1, 0, -1):
            current = positions[point_index]
            east, north = raw[point_index, :2]
            positions[point_index - 1] = destination_lonlat(
                {"lng": current[0], "lat": current[1]}, float(-east), float(-north)
            )
        geographic = []
        for point_index in range(len(positions)):
            lng, lat = positions[point_index]
            if point_index == 0:
                delta_lng, delta_lat = raw[point_index, 0] / 111.2, raw[point_index, 1] / 111.2
            else:
                prev_lng, prev_lat = positions[point_index - 1]
                delta_lng = (lng - prev_lng + 180.0) % 360.0 - 180.0
                delta_lat = lat - prev_lat
            geographic.append([lng, lat, raw[point_index, 2], raw[point_index, 3], delta_lng, delta_lat])
        rows.append((np.asarray(geographic[-4:], dtype=np.float64) - old_mean) / old_std)
    return np.asarray(rows, dtype=np.float32)


def predict_old(model: torch.nn.Module, features: np.ndarray, checkpoint: Dict[str, Any], device: torch.device, residual: bool) -> np.ndarray:
    model.eval()
    scaler = checkpoint["normalizer"]
    outputs = []
    tensor = torch.from_numpy(features)
    for batch in DataLoader(tensor, batch_size=512, shuffle=False):
        batch = batch.to(device)
        with torch.no_grad():
            result = model(batch)
            if residual:
                prior = constant_velocity_normalized(batch, scaler, 6)
                result = {"track": result["track"] + prior[..., :2], "wind": result["wind"] + prior[..., 2:3]}
            outputs.append(torch.cat((result["track"], result["wind"]), dim=-1).cpu().numpy())
    normalized = np.concatenate(outputs)
    mean = np.asarray(scaler["mean"][:3], dtype=np.float64)
    std = np.asarray(scaler["std"][:3], dtype=np.float64)
    predictions = normalized * std + mean
    predictions[..., 0] %= 360.0
    predictions[..., 1] = np.clip(predictions[..., 1], -90.0, 90.0)
    predictions[..., 2] = np.maximum(predictions[..., 2], 0.0)
    return predictions


def predict_new(model: MultiScaleResidualCNN, dataset: WindowDataset, normalizer: Dict[str, Any], device: torch.device) -> np.ndarray:
    model.eval()
    features = dataset.features_tensor.to(device)
    mean = torch.as_tensor(normalizer["target_mean"], dtype=features.dtype, device=device)
    std = torch.as_tensor(normalizer["target_std"], dtype=features.dtype, device=device)
    fmean = torch.as_tensor(normalizer["mean"], dtype=features.dtype, device=device)
    fstd = torch.as_tensor(normalizer["std"], dtype=features.dtype, device=device)
    batches = []
    with torch.no_grad():
        for batch in features.split(512):
            output = model(batch)
            last = batch[:, -1, :] * fstd + fmean
            steps = torch.arange(1, 7, device=device, dtype=batch.dtype).view(1, 6)
            prior = torch.stack((steps * last[:, None, 0], steps * last[:, None, 1], last[:, None, 2].expand(-1, 6)), dim=-1)
            local = (output["track"] + (prior[..., :2] - mean[:2]) / std[:2]) * std[:2] + mean[:2]
            geo = np.zeros((len(batch), 6, 3), dtype=np.float64)
            for i, origin in enumerate(dataset.origins[len(batches) * 512: len(batches) * 512 + len(batch)]):
                for h, (east, north) in enumerate(local[i].cpu().numpy()):
                    geo[i, h, :2] = destination_lonlat(origin, float(east), float(north))
                geo[i, :, 2] = (output["wind"][i, :, 0] + (prior[i, :, 2] - mean[2]) / std[2]).cpu().numpy() * float(std[2].cpu()) + float(mean[2].cpu())
            batches.append(geo)
    return np.concatenate(batches)


def storm_cluster_bootstrap(reference: np.ndarray, candidate: np.ndarray, storm_ids: List[str], repetitions: int = 20000) -> List[Dict[str, Any]]:
    """Paired bootstrap by storm so overlapping windows from one storm stay together."""
    ids = np.asarray(storm_ids)
    storms = np.asarray(sorted(set(storm_ids)))
    members = [np.flatnonzero(ids == storm) for storm in storms]
    rng = np.random.default_rng(20261019)
    draws = rng.integers(0, len(storms), size=(repetitions, len(storms)))
    results = []
    for lead in range(reference.shape[1]):
        ref_sum = np.asarray([reference[index, lead].sum() for index in members])
        cand_sum = np.asarray([candidate[index, lead].sum() for index in members])
        counts = np.asarray([len(index) for index in members], dtype=np.float64)
        denominator = counts[draws].sum(axis=1)
        ref_mae = ref_sum[draws].sum(axis=1) / denominator
        cand_mae = cand_sum[draws].sum(axis=1) / denominator
        delta = cand_mae - ref_mae
        low, high = np.quantile(delta, [0.025, 0.975])
        results.append({
            "lead_hours": (lead + 1) * 6,
            "candidate_minus_reference_mae_km": float(candidate[:, lead].mean() - reference[:, lead].mean()),
            "paired_storm_bootstrap_95ci_km": [float(low), float(high)],
            "bootstrap_probability_of_improvement": float(np.mean(delta < 0.0)),
            "statistically_lower_at_95pct": bool(high < 0.0),
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-root", type=Path, default=PROJECT_ROOT / "data" / "processed_12")
    parser.add_argument("--baseline", type=Path, default=PROJECT_ROOT / "artifacts/checkpoints/track_cnn_baseline.pth")
    parser.add_argument("--residual", type=Path, default=PROJECT_ROOT / "artifacts/residual/checkpoints/track_cnn_residual.pth")
    parser.add_argument("--multiscale-run", type=Path, default=PROJECT_ROOT / "artifacts/multiscale/runs/20261019T031709Z")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    torch.set_num_threads(4)
    device = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    if device.type == "cuda" and not torch.cuda.is_available():
        device = torch.device("cpu")
    dataset = WindowDataset(args.processed_root / "test.jsonl")
    manifest = json.loads((args.processed_root / "manifest.json").read_text(encoding="utf-8"))
    local_norm = manifest["preprocessing"]["normalizer"]
    actual = dataset.raw_targets_tensor.numpy()
    report: Dict[str, Any] = {"dataset": str((args.processed_root / "test.jsonl").resolve()), "sample_count": len(dataset), "storm_count": len(set(dataset.track_ids)), "models": {}}
    error_arrays: Dict[str, np.ndarray] = {}
    for name, path, cls, residual in (("track_cnn_baseline", args.baseline, TrackCNN, False), ("track_cnn_residual", args.residual, ResidualTrackCNN, True)):
        checkpoint = load_checkpoint(path)
        model = cls().to(device)
        model.load_state_dict(checkpoint["model_state_dict"])
        features = geographic_features(dataset, local_norm, checkpoint["normalizer"])
        predictions = predict_old(model, features, checkpoint, device, residual)
        report["models"][name] = metric_summary(predictions, actual)
        error_arrays[name] = haversine_km(predictions[..., 0], predictions[..., 1], actual[..., 0], actual[..., 1])
    for checkpoint_path in sorted((args.multiscale_run / "checkpoints").glob("*.pth")):
        checkpoint = load_checkpoint(checkpoint_path)
        arch = checkpoint["architecture"]
        if arch.get("variant") == "quantile":
            continue
        model = MultiScaleResidualCNN(input_features=arch["input_features"], horizons=arch["target_steps"], dropout=arch["dropout"], quantile=arch.get("quantile", False)).to(device)
        model.load_state_dict(checkpoint["model_state_dict"])
        predictions = predict_new(model, dataset, checkpoint["normalizer"], device)
        report["models"][checkpoint_path.stem] = metric_summary(predictions, actual)
        error_arrays[checkpoint_path.stem] = haversine_km(predictions[..., 0], predictions[..., 1], actual[..., 0], actual[..., 1])
    reference_name = "track_cnn_baseline"
    comparisons = {}
    for name, errors in error_arrays.items():
        if name != reference_name:
            comparisons[name + "_vs_" + reference_name] = storm_cluster_bootstrap(error_arrays[reference_name], errors, dataset.track_ids)
    for variant in ("multiscale", "physics"):
        names = ["{}-seed-{}".format(variant, seed) for seed in (2026, 2027, 2028)]
        if all(name in error_arrays for name in names):
            averaged = np.mean([error_arrays[name] for name in names], axis=0)
            comparisons[variant + "_three_seed_mean_vs_track_cnn_baseline"] = storm_cluster_bootstrap(error_arrays[reference_name], averaged, dataset.track_ids)
            comparisons[variant + "_three_seed_mean_vs_track_cnn_residual"] = storm_cluster_bootstrap(error_arrays["track_cnn_residual"], averaged, dataset.track_ids)
    report["paired_storm_bootstrap"] = {
        "method": "paired cluster bootstrap resampling typhoon IDs; all windows from each sampled storm stay together",
        "resamples": 20000,
        "confidence_level": 0.95,
        "storm_count": report["storm_count"],
        "comparisons": comparisons,
    }
    output = args.multiscale_run / "fair_comparison_914.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("written:", output)


if __name__ == "__main__":
    main()
