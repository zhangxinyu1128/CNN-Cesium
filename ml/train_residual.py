"""Train and evaluate the residual CNN with a constant-velocity prior."""

import argparse
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.models.residual_cnn import ResidualTrackCNN, residual_loss
from ml.train import (
    PROJECT_ROOT,
    WindowDataset,
    configure_logging,
    evaluate_baselines,
    metric_summary,
    seed_everything,
)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def constant_velocity_normalized(
    features: torch.Tensor, scaler: Dict[str, Any], horizons: int
) -> torch.Tensor:
    means = torch.as_tensor(scaler["mean"], dtype=features.dtype, device=features.device)
    scales = torch.as_tensor(scaler["std"], dtype=features.dtype, device=features.device)
    last = features[:, -1, :] * scales + means
    steps = torch.arange(1, horizons + 1, dtype=features.dtype, device=features.device).view(1, horizons)
    raw = torch.zeros((features.shape[0], horizons, 3), dtype=features.dtype, device=features.device)
    raw[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
    raw[..., 1] = last[:, None, 1] + steps * last[:, None, 5]
    raw[..., 2] = last[:, None, 2]
    target_means = means[:3]
    target_scales = scales[:3]
    return (raw - target_means) / target_scales


def train_epoch(model, loader, optimizer, device, scaler, horizons):
    model.train()
    total_loss = 0.0
    total_count = 0
    for features, targets in loader:
        features, targets = features.to(device), targets.to(device)
        prior = constant_velocity_normalized(features, scaler, horizons)
        optimizer.zero_grad(set_to_none=True)
        loss = residual_loss(model, model(features), targets - prior)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(features)
        total_count += len(features)
    return total_loss / max(total_count, 1)


def validation_loss(model, loader, device, scaler, horizons):
    model.eval()
    total_loss = 0.0
    total_count = 0
    with torch.no_grad():
        for features, targets in loader:
            features, targets = features.to(device), targets.to(device)
            prior = constant_velocity_normalized(features, scaler, horizons)
            loss = residual_loss(model, model(features), targets - prior)
            total_loss += loss.item() * len(features)
            total_count += len(features)
    return total_loss / max(total_count, 1)


def evaluate(model, dataset, device, scaler, horizons):
    model.eval()
    outputs = []
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    with torch.no_grad():
        for features, _ in loader:
            prior = constant_velocity_normalized(features.to(device), scaler, horizons)
            residual = model(features.to(device))
            outputs.append((prior + torch.cat((residual["track"], residual["wind"]), dim=-1)).cpu().numpy())
    normalized = np.concatenate(outputs, axis=0)
    means = np.asarray(scaler["mean"][:3], dtype=np.float64)
    scales = np.asarray(scaler["std"][:3], dtype=np.float64)
    predictions = normalized * scales + means
    predictions[..., 0] %= 360.0
    predictions[..., 1] = np.clip(predictions[..., 1], -90.0, 90.0)
    predictions[..., 2] = np.maximum(predictions[..., 2], 0.0)
    return metric_summary(predictions, dataset.raw_targets_tensor.numpy())


def train(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_json(args.config)
    if args.epochs is not None:
        config["epochs"] = args.epochs
    if args.batch_size is not None:
        config["batch_size"] = args.batch_size
    if args.seed is not None:
        config["seed"] = args.seed
    seed_everything(config["seed"])
    processed = args.data_root / "processed"
    manifest = load_json(processed / "manifest.json")
    scaler = manifest["preprocessing"]["normalizer"]
    train_data = WindowDataset(processed / "train.jsonl")
    validation_data = WindowDataset(processed / "validation.jsonl")
    test_data = WindowDataset(processed / "test.jsonl")
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    logger = configure_logging(run_dir / "train.log")
    config.update({"device": str(device), "run_id": run_id, "model_version": "track-cnn-residual-v1", "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"], "dataset_split_counts": manifest["preprocessing"]["split_sample_counts"]})
    save_json(run_dir / "config.json", config)
    generator = torch.Generator().manual_seed(config["seed"])
    loader = DataLoader(train_data, batch_size=config["batch_size"], shuffle=True, generator=generator)
    valid_loader = DataLoader(validation_data, batch_size=config["batch_size"], shuffle=False)
    model = ResidualTrackCNN(config["input_features"], config["target_steps"], config["dropout"]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    best_loss, best_epoch, stale = math.inf, 0, 0
    checkpoint = run_dir / "best.pth"
    logger.info("device=%s train=%d validation=%d test=%d", device, len(train_data), len(validation_data), len(test_data))
    for epoch in range(1, config["epochs"] + 1):
        train_loss = train_epoch(model, loader, optimizer, device, scaler, config["target_steps"])
        valid_loss = validation_loss(model, valid_loader, device, scaler, config["target_steps"])
        logger.info("epoch=%d train_loss=%.6f validation_loss=%.6f", epoch, train_loss, valid_loss)
        if valid_loss < best_loss:
            best_loss, best_epoch, stale = valid_loss, epoch, 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "model_version": "track-cnn-residual-v1",
                    "architecture": {
                        "type": "residual",
                        "input_steps": config["input_steps"],
                        "input_features": config["input_features"],
                        "target_steps": config["target_steps"],
                        "target_features": config["target_features"],
                        "dropout": config["dropout"],
                    },
                    "normalizer": scaler,
                },
                checkpoint,
            )
        else:
            stale += 1
            if stale >= config["patience"]:
                logger.info("early stopping after epoch %d", epoch)
                break
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(state["model_state_dict"])
    evaluation = {"cnn_residual": evaluate(model, test_data, device, scaler, config["target_steps"]), "baselines": evaluate_baselines(test_data, scaler)}
    published = args.output_root / "checkpoints" / "track_cnn_residual.pth"
    published.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(checkpoint, published)
    result = {"run_id": run_id, "model_version": "track-cnn-residual-v1", "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"], "dataset_split_counts": manifest["preprocessing"]["split_sample_counts"], "device": str(device), "best_epoch": best_epoch, "best_validation_loss": best_loss, "checkpoint": {"path": str(published.resolve()), "sha256": hashlib.sha256(published.read_bytes()).hexdigest()}, "evaluation": evaluation}
    save_json(run_dir / "test_metrics.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "ml" / "configs" / "baseline.json")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts" / "residual")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="auto")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
