"""Train 12-step multi-scale residual CNN experiments on the frozen storm split.

Prepare a separate long-window dataset first with `python -m ml.prepare_dataset
--input-steps 12 --output-root data/processed_12`. Training writes candidate checkpoints
under the experiment output; select one for API inference through `TC_MODEL_PATH`.
"""

import argparse
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

from ml.data_audit import PROJECT_ROOT
from ml.features.local_motion import destination_lonlat
from ml.losses.physics_loss import physics_constrained_loss
from ml.models.multiscale_residual_cnn import MultiScaleResidualCNN
from ml.train import WindowDataset, haversine_km, seed_everything


VARIANTS = ("multiscale", "physics", "quantile")
SEEDS = (2026, 2027, 2028)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def local_prior(features: torch.Tensor, normalizer: Dict[str, Any]) -> torch.Tensor:
    means = torch.as_tensor(normalizer["mean"], dtype=features.dtype, device=features.device)
    scales = torch.as_tensor(normalizer["std"], dtype=features.dtype, device=features.device)
    last = features[:, -1, :] * scales + means
    target_mean = torch.as_tensor(normalizer["target_mean"], dtype=features.dtype, device=features.device)
    target_std = torch.as_tensor(normalizer["target_std"], dtype=features.dtype, device=features.device)
    steps = torch.arange(1, 7, dtype=features.dtype, device=features.device).view(1, 6)
    raw = torch.stack(
        (steps * last[:, None, 0], steps * last[:, None, 1], last[:, None, 2].expand(-1, 6)),
        dim=-1,
    )
    return (raw - target_mean) / target_std


def storm_split(validation: WindowDataset, seed: int) -> Tuple[List[int], List[int]]:
    storms = sorted(set(validation.track_ids))
    if len(storms) < 4:
        raise ValueError("validation requires at least four storms for stopping and calibration")
    random.Random(seed).shuffle(storms)
    calibration_storms = set(storms[::2])
    stop_indices = [i for i, storm in enumerate(validation.track_ids) if storm not in calibration_storms]
    calibration_indices = [i for i, storm in enumerate(validation.track_ids) if storm in calibration_storms]
    return stop_indices, calibration_indices


def calculate_loss(model, features, targets, normalizer, variant):
    prior = local_prior(features, normalizer)
    output = model(features)
    absolute = {
        "track": output["track"] + prior[..., :2],
        "wind": output["wind"] + prior[..., 2:3],
        "quantiles": None,
    }
    if variant == "quantile":
        absolute["quantiles"] = output["quantiles"] + prior[..., None, :2]
        # Keep the point estimate equal to the learned median head.
        absolute["track"] = absolute["quantiles"][:, :, 1, :]
    weights = (0.05, 0.002, 0.002) if variant == "physics" else (0.0, 0.0, 0.0)
    parts = physics_constrained_loss(
        absolute,
        targets,
        torch.as_tensor(normalizer["target_mean"], dtype=targets.dtype, device=targets.device),
        torch.as_tensor(normalizer["target_std"], dtype=targets.dtype, device=targets.device),
        velocity_weight=weights[0],
        acceleration_weight=weights[1],
        turn_weight=weights[2],
    )
    return parts["loss"], output, prior


def predict_normalized(model, dataset, normalizer, device) -> Tuple[np.ndarray, np.ndarray]:
    model.eval()
    centers, bounds = [], []
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    with torch.no_grad():
        for features, _ in loader:
            features = features.to(device)
            output = model(features)
            prior = local_prior(features, normalizer)
            center = output["track"] + prior[..., :2]
            if output["quantiles"] is None:
                bounds.append(np.full((*center.shape[:2], 2, 2), np.nan, dtype=np.float32))
            else:
                values = output["quantiles"] + prior[..., None, :2]
                values, _ = values.sort(dim=2)
                target_scales = torch.as_tensor(normalizer["target_std"][:2], dtype=values.dtype, device=device)
                bounds.append((values[:, :, (0, 2), :] * target_scales).cpu().numpy())
                center = values[:, :, 1, :]
            centers.append(center.cpu().numpy())
    return np.concatenate(centers), np.concatenate(bounds)


def decode_tracks(normalized: np.ndarray, dataset, normalizer: Dict[str, Any]) -> np.ndarray:
    means = np.asarray(normalizer["target_mean"], dtype=np.float64)
    scales = np.asarray(normalizer["target_std"], dtype=np.float64)
    local = normalized.astype(np.float64) * scales[:2] + means[:2]
    geographic = np.zeros((*local.shape[:2], 2), dtype=np.float64)
    origins = (
        [dataset.dataset.origins[index] for index in dataset.indices]
        if isinstance(dataset, Subset)
        else dataset.origins
    )
    for sample, origin in enumerate(origins):
        for lead, (east, north) in enumerate(local[sample]):
            geographic[sample, lead] = destination_lonlat(origin, float(east), float(north))
    return geographic


def summarize_path(predicted: np.ndarray, actual: np.ndarray) -> Dict[str, Any]:
    errors = haversine_km(predicted[..., 0], predicted[..., 1], actual[..., 0], actual[..., 1])
    return {
        "sample_count": int(errors.shape[0]),
        "by_horizon": [
            {"lead_hours": (index + 1) * 6, "path_mae_km": float(np.abs(errors[:, index]).mean()),
             "path_rmse_km": float(np.sqrt(np.square(errors[:, index]).mean()))}
            for index in range(errors.shape[1])
        ],
    }


def conformal_radii(errors: np.ndarray, storm_ids: Sequence[str], coverage: float = 0.9) -> List[float]:
    radii = []
    ids = np.asarray(storm_ids)
    for lead in range(errors.shape[1]):
        storm_scores = np.asarray([errors[ids == storm, lead].max() for storm in sorted(set(storm_ids))])
        rank = min(len(storm_scores), int(math.ceil((len(storm_scores) + 1) * coverage)))
        radii.append(float(np.sort(storm_scores)[rank - 1]))
    return radii


def train_one(args, variant: str, seed: int, train_data, validation, test_data, normalizer, device):
    seed_everything(seed)
    # Deterministic cuDNN kernels are prohibitively slow for these short
    # multi-branch convolutions on laptop GPUs. Keep seed control and let cuDNN
    # benchmark fast kernels for the fixed batch shapes used by this run.
    torch.backends.cudnn.deterministic = False
    torch.backends.cudnn.benchmark = True
    stop_indices, calibration_indices = storm_split(validation, seed)
    stop_data = Subset(validation, stop_indices)
    calibration_data = Subset(validation, calibration_indices)
    model = MultiScaleResidualCNN(
        input_features=len(normalizer["feature_names"]),
        horizons=6,
        dropout=args.dropout,
        quantile=variant == "quantile",
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True,
                              generator=torch.Generator().manual_seed(seed), num_workers=0)
    stop_loader = DataLoader(stop_data, batch_size=args.batch_size, shuffle=False, num_workers=0)
    best_loss, best_state, stale = math.inf, None, 0
    start_time = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        model.train()
        for features, targets in train_loader:
            features, targets = features.to(device), targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss, _, _ = calculate_loss(model, features, targets, normalizer, variant)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        model.eval()
        losses = []
        with torch.no_grad():
            for features, targets in stop_loader:
                loss, _, _ = calculate_loss(model, features.to(device), targets.to(device), normalizer, variant)
                losses.append(float(loss))
        validation_loss = float(np.mean(losses))
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                break
        if getattr(args, "progress_path", None):
            write_json(args.progress_path, {
                "status": "training",
                "variant": variant,
                "seed": seed,
                "epoch": epoch,
                "epochs": args.epochs,
                "best_validation_loss": best_loss,
                "stale_epochs": stale,
            })
        print("variant={} seed={} epoch={}/{} val_loss={:.6f} best={:.6f}".format(
            variant, seed, epoch, args.epochs, validation_loss, best_loss), flush=True)
    if best_state is None:
        raise RuntimeError("training did not produce a checkpoint")
    model.load_state_dict(best_state)
    calibration_normalized, calibration_bounds = predict_normalized(model, calibration_data, normalizer, device)
    test_normalized, test_bounds = predict_normalized(model, test_data, normalizer, device)
    latency_features = test_data.features_tensor[:1].to(device)
    model.eval()
    with torch.no_grad():
        for _ in range(5):
            model(latency_features)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        latency_start = time.perf_counter()
        repetitions = 100
        for _ in range(repetitions):
            model(latency_features)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        inference_latency_ms = (time.perf_counter() - latency_start) * 1000.0 / repetitions
    calibration_geo = decode_tracks(calibration_normalized, calibration_data, normalizer)
    test_geo = decode_tracks(test_normalized, test_data, normalizer)
    calibration_actual = calibration_data.dataset.raw_targets_tensor[calibration_data.indices].numpy()[..., :2]
    test_actual = test_data.raw_targets_tensor.numpy()[..., :2]
    calibration_errors = haversine_km(calibration_geo[..., 0], calibration_geo[..., 1],
                                      calibration_actual[..., 0], calibration_actual[..., 1])
    test_errors = haversine_km(test_geo[..., 0], test_geo[..., 1], test_actual[..., 0], test_actual[..., 1])
    radii = conformal_radii(calibration_errors, [validation.track_ids[i] for i in calibration_indices])
    coverage_by_lead = []
    for lead, radius in enumerate(radii):
        storm_ids = np.asarray(test_data.track_ids)
        inside = test_errors[:, lead] <= radius
        per_storm = [bool(inside[storm_ids == storm].all()) for storm in sorted(set(test_data.track_ids))]
        coverage_by_lead.append({
            "lead_hours": 6 * (lead + 1),
            "conformal_radius_km": radius,
            "window_coverage": float(inside.mean()),
            "storm_coverage": float(np.mean(per_storm)),
            "mean_band_width_km": 2.0 * radius,
        })
    params = sum(parameter.numel() for parameter in model.parameters())
    return {
        "variant": variant,
        "seed": seed,
        "best_validation_loss": best_loss,
        "epochs_trained": epoch,
        "parameter_count": params,
        "train_wall_seconds": time.perf_counter() - start_time,
        "point_metrics": summarize_path(test_geo, test_actual),
        "calibration": {
            "method": "storm-max split conformal radius calibrated on validation storms",
            "target_coverage": 0.9,
            "calibration_windows": len(calibration_indices),
            "calibration_storms": len(set(validation.track_ids[i] for i in calibration_indices)),
            "by_horizon": coverage_by_lead,
        },
        "direct_quantile_coverage": _quantile_coverage(test_bounds, test_data, normalizer),
        "inference_latency_ms_per_sample": inference_latency_ms,
        "checkpoint_state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
    }


def _quantile_coverage(bounds: np.ndarray, dataset, normalizer: Dict[str, Any]):
    if np.isnan(bounds).all():
        return None
    mean = np.asarray(normalizer["target_mean"][:2], dtype=np.float64)
    scale = np.asarray(normalizer["target_std"][:2], dtype=np.float64)
    actual = dataset.targets_tensor.numpy()[..., :2] * scale + mean
    lower = bounds[:, :, 0, :] + mean
    upper = bounds[:, :, 1, :] + mean
    covered = (actual >= lower) & (actual <= upper)
    widths = np.maximum(0.0, upper - lower)
    return [
        {
            "lead_hours": 6 * (index + 1),
            "east_north_component_coverage": float(covered[:, index, :].mean()),
            "east_north_mean_width_km": float(widths[:, index, :].mean()),
        }
        for index in range(covered.shape[1])
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-root", type=Path, default=PROJECT_ROOT / "data" / "processed_12")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts" / "multiscale")
    parser.add_argument("--variant", choices=(*VARIANTS, "all"), default="all")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--learning-rate", type=float, default=5e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cpu-threads", type=int, default=4,
                        help="Limit CPU threads; small temporal CNNs can slow down with large defaults.")
    args = parser.parse_args()
    if args.cpu_threads < 1:
        raise ValueError("cpu-threads must be positive")
    torch.set_num_threads(args.cpu_threads)
    manifest = read_json(args.processed_root / "manifest.json")
    preprocessing = manifest["preprocessing"]
    if preprocessing.get("coordinate_system") != "local_motion_v1" or preprocessing.get("input_steps") != 12:
        raise ValueError("expected a 12-step local_motion_v1 dataset; run prepare_dataset --input-steps 12")
    normalizer = preprocessing["normalizer"]
    datasets = {name: WindowDataset(args.processed_root / (name + ".jsonl"))
                for name in ("train", "validation", "test")}
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    variants = VARIANTS if args.variant == "all" else (args.variant,)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    args.progress_path = run_dir / "training_progress.json"
    rows = []
    for variant in variants:
        for seed in args.seeds:
            result = train_one(args, variant, seed, datasets["train"], datasets["validation"],
                               datasets["test"], normalizer, device)
            state_dict = result.pop("checkpoint_state_dict")
            checkpoint_path = run_dir / "checkpoints" / "{}-seed-{}.pth".format(variant, seed)
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "model_version": "multiscale-residual-{}-12step-seed{}-v1".format(variant, seed),
                "architecture": {
                    "type": "multiscale_residual",
                    "variant": variant,
                    "input_steps": 12,
                    "input_features": len(normalizer["feature_names"]),
                    "target_steps": 6,
                    "target_features": 3,
                    "dropout": args.dropout,
                    "quantile": variant == "quantile",
                },
                "normalizer": normalizer,
                "model_state_dict": state_dict,
                "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
                "split_file_sha256": preprocessing["split_file_sha256"],
                "variant": variant,
                "seed": seed,
            }, checkpoint_path)
            result["checkpoint_path"] = str(checkpoint_path.resolve())
            rows.append(result)
            progress = {
                "experiment": "12-step local-motion multi-scale residual CNN",
                "status": "training",
                "completed_runs": len(rows),
                "planned_runs": len(variants) * len(args.seeds),
                "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
                "split_file_sha256": preprocessing["split_file_sha256"],
                "runs": rows,
                "online_model_changed": False,
            }
            write_json(run_dir / "experiment_progress.json", progress)
            print("variant={} seed={} test_mae_km={}".format(
                variant, seed, [round(x["path_mae_km"], 2) for x in result["point_metrics"]["by_horizon"]]))
    report = {
        "experiment": "12-step local-motion multi-scale residual CNN",
        "status": "experimental_offline_holdout",
        "online_model_changed": False,
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "split_file_sha256": preprocessing["split_file_sha256"],
        "split_counts": preprocessing["split_sample_counts"],
        "input_steps": 12,
        "history_hours": 72,
        "target_leads_hours": [6, 12, 18, 24, 30, 36],
        "device": str(device),
        "runs": rows,
        "parameter_count": rows[0]["parameter_count"] if rows else 0,
        "note": "Frozen storm holdout used for final metrics; calibration uses a storm-disjoint half of validation storms.",
    }
    write_json(run_dir / "experiment_report.json", report)
    print("report:", run_dir / "experiment_report.json")


if __name__ == "__main__":
    main()
