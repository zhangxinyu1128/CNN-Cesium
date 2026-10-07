"""Run controlled feature ablations for the residual CNN on fixed splits."""

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.models.residual_cnn import ResidualTrackCNN, residual_loss
from ml.train import PROJECT_ROOT, WindowDataset, evaluate_baselines, metric_summary
from ml.train_residual import constant_velocity_normalized


FEATURES = ("lng", "lat", "speed", "power", "delta_lng", "delta_lat")
VARIANTS = {
    "full": (1, 1, 1, 1, 1, 1),
    "without_intensity": (1, 1, 0, 0, 1, 1),
    "without_motion": (1, 1, 1, 1, 0, 0),
    "position_only": (1, 1, 0, 0, 0, 0),
}


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def masked(features: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    return features * mask.view(1, 1, -1)


def train_one(
    model: ResidualTrackCNN,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    normalizer: Dict[str, Any],
    mask: torch.Tensor,
) -> float:
    model.train()
    total, count = 0.0, 0
    for features, targets in loader:
        features = masked(features.to(device), mask)
        targets = targets.to(device)
        prior = constant_velocity_normalized(features, normalizer, 6)
        optimizer.zero_grad(set_to_none=True)
        loss = residual_loss(model, model(features), targets - prior)
        loss.backward()
        optimizer.step()
        total += loss.item() * len(features)
        count += len(features)
    return total / max(count, 1)


def validation_loss(
    model: ResidualTrackCNN,
    loader: DataLoader,
    device: torch.device,
    normalizer: Dict[str, Any],
    mask: torch.Tensor,
) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for features, targets in loader:
            features = masked(features.to(device), mask)
            targets = targets.to(device)
            prior = constant_velocity_normalized(features, normalizer, 6)
            total += residual_loss(model, model(features), targets - prior).item() * len(features)
            count += len(features)
    return total / max(count, 1)


def evaluate(
    model: ResidualTrackCNN,
    dataset: WindowDataset,
    device: torch.device,
    normalizer: Dict[str, Any],
    mask: torch.Tensor,
) -> Dict[str, Any]:
    model.eval()
    outputs = []
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    with torch.no_grad():
        for features, _ in loader:
            features = masked(features.to(device), mask)
            prior = constant_velocity_normalized(features, normalizer, 6)
            result = model(features)
            outputs.append((prior + torch.cat((result["track"], result["wind"]), dim=-1)).cpu().numpy())
    predictions = np.concatenate(outputs, axis=0) * scales + means
    predictions[..., 0] %= 360.0
    predictions[..., 1] = np.clip(predictions[..., 1], -90.0, 90.0)
    predictions[..., 2] = np.maximum(predictions[..., 2], 0.0)
    return metric_summary(predictions, dataset.raw_targets_tensor.numpy())


def run(args: argparse.Namespace) -> Dict[str, Any]:
    seed_everything(args.seed)
    processed = args.data_root / "processed"
    manifest = load_json(processed / "manifest.json")
    normalizer = manifest["preprocessing"]["normalizer"]
    train_data = WindowDataset(processed / "train.jsonl")
    validation_data = WindowDataset(processed / "validation.jsonl")
    test_data = WindowDataset(processed / "test.jsonl")
    device = torch.device(args.device)
    validation_loader = DataLoader(validation_data, batch_size=args.batch_size, shuffle=False)
    all_results: Dict[str, Any] = {
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "split_file_sha256": manifest["preprocessing"]["split_file_sha256"],
        "config": {
            "seed": args.seed,
            "max_epochs": args.epochs,
            "patience": args.patience,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "dropout": args.dropout,
            "device": str(device),
            "same_initialization_and_batch_order_per_variant": True,
        },
        "variants": {},
    }
    for name, values in VARIANTS.items():
        seed_everything(args.seed)
        generator = torch.Generator().manual_seed(args.seed)
        train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True, generator=generator)
        mask = torch.tensor(values, dtype=torch.float32, device=device)
        model = ResidualTrackCNN(6, 6, args.dropout).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
        best_loss, best_state, stale = math.inf, None, 0
        for epoch in range(args.epochs):
            train_one(model, train_loader, optimizer, device, normalizer, mask)
            current = validation_loss(model, validation_loader, device, normalizer, mask)
            if current < best_loss:
                best_loss = current
                best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
                stale = 0
            else:
                stale += 1
                if stale >= args.patience:
                    break
        assert best_state is not None
        model.load_state_dict(best_state)
        all_results["variants"][name] = {
            "enabled_features": [feature for feature, enabled in zip(FEATURES, values) if enabled],
            "best_validation_loss": best_loss,
            "epochs_trained": epoch + 1,
            "test": evaluate(model, test_data, device, normalizer, mask),
        }
        print(name, all_results["variants"][name]["test"]["by_horizon"][0])
    all_results["baselines"] = evaluate_baselines(test_data, normalizer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(all_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "ablation" / "metrics.json")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
