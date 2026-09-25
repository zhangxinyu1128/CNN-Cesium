"""Train and evaluate the reproducible single-track CNN baseline."""

import argparse
import hashlib
import json
import logging
import math
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from ml.data_audit import PROJECT_ROOT
from ml.models.track_cnn import TrackCNN, multi_task_loss


class WindowDataset(Dataset):
    def __init__(self, path: Path) -> None:
        self.features: List[List[List[float]]] = []
        self.targets: List[List[List[float]]] = []
        self.raw_targets: List[List[List[float]]] = []
        self.track_ids: List[str] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                self.features.append(record["x"])
                self.targets.append(record["y"])
                self.raw_targets.append(record["target_raw"])
                self.track_ids.append(record["typhoon_id"])
        if not self.features:
            raise ValueError("dataset is empty: " + str(path))
        self.features_tensor = torch.tensor(self.features, dtype=torch.float32)
        self.targets_tensor = torch.tensor(self.targets, dtype=torch.float32)
        self.raw_targets_tensor = torch.tensor(self.raw_targets, dtype=torch.float64)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, index: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features_tensor[index], self.targets_tensor[index]


def configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("track_cnn_training")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def train_epoch(
    model: TrackCNN,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
) -> float:
    model.train()
    total_loss = 0.0
    total_count = 0
    for features, targets in loader:
        features = features.to(device, non_blocking=True)
        targets = targets.to(device, non_blocking=True)
        optimizer.zero_grad(set_to_none=True)
        predictions = model(features)
        loss = multi_task_loss(model, predictions, targets)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(features)
        total_count += len(features)
    return total_loss / max(total_count, 1)


def validation_loss(model: TrackCNN, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    total_loss = 0.0
    total_count = 0
    with torch.no_grad():
        for features, targets in loader:
            features = features.to(device, non_blocking=True)
            targets = targets.to(device, non_blocking=True)
            loss = multi_task_loss(model, model(features), targets)
            total_loss += loss.item() * len(features)
            total_count += len(features)
    return total_loss / max(total_count, 1)


def haversine_km(
    predicted_lng: np.ndarray,
    predicted_lat: np.ndarray,
    actual_lng: np.ndarray,
    actual_lat: np.ndarray,
) -> np.ndarray:
    radians = np.pi / 180.0
    lat_delta = (predicted_lat - actual_lat) * radians
    lng_delta = ((predicted_lng - actual_lng + 180.0) % 360.0 - 180.0) * radians
    lat1 = actual_lat * radians
    lat2 = predicted_lat * radians
    value = np.sin(lat_delta / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(lng_delta / 2.0) ** 2
    return 6371.0088 * 2.0 * np.arctan2(np.sqrt(value), np.sqrt(np.maximum(1.0 - value, 0.0)))


def metric_summary(predictions: np.ndarray, actual: np.ndarray) -> Dict[str, Any]:
    path_error = haversine_km(predictions[..., 0], predictions[..., 1], actual[..., 0], actual[..., 1])
    wind_error = predictions[..., 2] - actual[..., 2]
    per_horizon = []
    for index in range(actual.shape[1]):
        errors = path_error[:, index]
        wind = wind_error[:, index]
        per_horizon.append(
            {
                "lead_hours": 6 * (index + 1),
                "path_mae_km": float(np.mean(np.abs(errors))),
                "path_rmse_km": float(np.sqrt(np.mean(errors ** 2))),
                "path_median_km": float(np.median(errors)),
                "wind_mae_ms": float(np.mean(np.abs(wind))),
                "wind_rmse_ms": float(np.sqrt(np.mean(wind ** 2))),
                "longitude_mae_deg": float(np.mean(np.abs(((predictions[:, index, 0] - actual[:, index, 0] + 180.0) % 360.0) - 180.0))),
                "latitude_mae_deg": float(np.mean(np.abs(predictions[:, index, 1] - actual[:, index, 1]))),
            }
        )
    return {"sample_count": int(actual.shape[0]), "by_horizon": per_horizon}


def evaluate_model(
    model: TrackCNN,
    dataset: WindowDataset,
    device: torch.device,
    scaler: Dict[str, List[float]],
) -> Dict[str, Any]:
    model.eval()
    loader = DataLoader(dataset, batch_size=512, shuffle=False)
    outputs = []
    with torch.no_grad():
        for features, _ in loader:
            result = model(features.to(device))
            tracks = result["track"].cpu().numpy()
            winds = result["wind"].cpu().numpy()
            outputs.append(np.concatenate((tracks, winds), axis=-1))
    normalized_predictions = np.concatenate(outputs, axis=0)
    means = np.asarray([scaler["mean"][index] for index in (0, 1, 2)], dtype=np.float64)
    scales = np.asarray([scaler["std"][index] for index in (0, 1, 2)], dtype=np.float64)
    predictions = normalized_predictions * scales + means
    actual = dataset.raw_targets_tensor.numpy()
    predictions[..., 0] %= 360.0
    predictions[..., 1] = np.clip(predictions[..., 1], -90.0, 90.0)
    predictions[..., 2] = np.maximum(predictions[..., 2], 0.0)
    return metric_summary(predictions, actual)


def evaluate_baselines(
    dataset: WindowDataset,
    scaler: Dict[str, List[float]],
) -> Dict[str, Any]:
    normalized = dataset.features_tensor.numpy().astype(np.float64)
    means = np.asarray(scaler["mean"], dtype=np.float64)
    scales = np.asarray(scaler["std"], dtype=np.float64)
    raw_features = normalized * scales + means
    actual = dataset.raw_targets_tensor.numpy()
    last = raw_features[:, -1, :]
    persistence = np.zeros_like(actual)
    persistence[..., 0] = last[:, None, 0]
    persistence[..., 1] = last[:, None, 1]
    persistence[..., 2] = last[:, None, 2]

    constant_velocity = persistence.copy()
    steps = np.arange(1, actual.shape[1] + 1, dtype=np.float64)[None, :]
    constant_velocity[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
    constant_velocity[..., 1] = last[:, None, 1] + steps * last[:, None, 5]
    return {
        "last_observation": metric_summary(persistence, actual),
        "constant_velocity": metric_summary(constant_velocity, actual),
    }


def train(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_json(args.config)
    if args.epochs is not None:
        config["epochs"] = args.epochs
    if args.batch_size is not None:
        config["batch_size"] = args.batch_size
    seed_everything(config["seed"])

    processed_root = args.data_root / "processed"
    manifest = load_json(processed_root / "manifest.json")
    if "preprocessing" not in manifest:
        raise ValueError("run `python -m ml.prepare_dataset` before training")
    scaler = manifest["preprocessing"]["normalizer"]
    train_data = WindowDataset(processed_root / "train.jsonl")
    validation_data = WindowDataset(processed_root / "validation.jsonl")
    test_data = WindowDataset(processed_root / "test.jsonl")

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
    config.update(
        {
            "device": str(device),
            "run_id": run_id,
            "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
            "dataset_split_counts": manifest["preprocessing"]["split_sample_counts"],
            "model_version": "track-cnn-1d-v1",
        }
    )
    save_json(run_dir / "config.json", config)

    generator = torch.Generator().manual_seed(config["seed"])
    train_loader = DataLoader(
        train_data,
        batch_size=config["batch_size"],
        shuffle=True,
        generator=generator,
        pin_memory=device.type == "cuda",
        num_workers=0,
    )
    validation_loader = DataLoader(
        validation_data,
        batch_size=config["batch_size"],
        shuffle=False,
        pin_memory=device.type == "cuda",
        num_workers=0,
    )
    model = TrackCNN(
        input_features=config["input_features"],
        horizons=config["target_steps"],
        dropout=config["dropout"],
    ).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=config["learning_rate"],
        weight_decay=config["weight_decay"],
    )
    best_loss = math.inf
    best_epoch = 0
    epochs_without_improvement = 0
    run_checkpoint = run_dir / "best.pth"

    logger.info("device=%s train=%d validation=%d test=%d", device, len(train_data), len(validation_data), len(test_data))
    for epoch in range(1, config["epochs"] + 1):
        train_loss = train_epoch(model, train_loader, optimizer, device)
        valid_loss = validation_loss(model, validation_loader, device)
        logger.info("epoch=%d train_loss=%.6f validation_loss=%.6f", epoch, train_loss, valid_loss)
        if valid_loss < best_loss:
            best_loss = valid_loss
            best_epoch = epoch
            epochs_without_improvement = 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "model_version": "track-cnn-1d-v1",
                    "architecture": {
                        "input_steps": config["input_steps"],
                        "input_features": config["input_features"],
                        "target_steps": config["target_steps"],
                        "target_features": config["target_features"],
                        "dropout": config["dropout"],
                    },
                    "normalizer": scaler,
                },
                run_checkpoint,
            )
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config["patience"]:
                logger.info("early stopping after epoch %d", epoch)
                break

    checkpoint = torch.load(run_checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(device)
    evaluation = {
        "cnn": evaluate_model(model, test_data, device, scaler),
        "baselines": evaluate_baselines(test_data, scaler),
    }

    checkpoint_dir = args.output_root / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    published_checkpoint = checkpoint_dir / "track_cnn_baseline.pth"
    shutil.copy2(run_checkpoint, published_checkpoint)
    checkpoint_sha = hashlib.sha256(published_checkpoint.read_bytes()).hexdigest()
    results = {
        "run_id": run_id,
        "model_version": "track-cnn-1d-v1",
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "dataset_split_counts": manifest["preprocessing"]["split_sample_counts"],
        "device": str(device),
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "checkpoint": {
            "path": str(published_checkpoint.resolve()),
            "size_bytes": published_checkpoint.stat().st_size,
            "sha256": checkpoint_sha,
        },
        "evaluation": evaluation,
    }
    save_json(run_dir / "test_metrics.json", results)
    logger.info("best_epoch=%d best_validation_loss=%.6f", best_epoch, best_loss)
    logger.info("checkpoint_sha256=%s", checkpoint_sha)
    for lead in evaluation["cnn"]["by_horizon"]:
        logger.info(
            "cnn lead=%dh path_mae=%.2fkm path_rmse=%.2fkm wind_mae=%.2fm/s",
            lead["lead_hours"], lead["path_mae_km"], lead["path_rmse_km"], lead["wind_mae_ms"],
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "ml" / "configs" / "baseline.json")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
