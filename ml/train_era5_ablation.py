"""Run controlled track-only and 500/850 hPa ERA5 fusion ablations."""

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from ml.data_audit import PROJECT_ROOT
from ml.models.residual_cnn import ResidualTrackCNN, residual_loss
from ml.models.track_cnn import TrackCNN, multi_task_loss
from ml.train import metric_summary, seed_everything
from ml.train_residual import constant_velocity_normalized


VARIANTS = {
    "track_only": (0, 1, 2, 3, 4, 5),
    "fusion_500_850": tuple(range(11)),
    "fusion_500_only": (0, 1, 2, 3, 4, 5, 6, 7, 10),
    "fusion_850_only": (0, 1, 2, 3, 4, 5, 8, 9, 10),
}


def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_inputs(rows: List[Dict[str, Any]], indices: Sequence[int], train: bool = False, scaler=None):
    x = np.asarray([[[step[index] for index in indices] for step in row["x"]] for row in rows], dtype=np.float32)
    weather_positions = [position for position, source_index in enumerate(indices) if source_index >= 6]
    if train and weather_positions:
        values = x[:, :, weather_positions]
        scaler = {"mean": values.mean(axis=(0, 1)), "std": np.maximum(values.std(axis=(0, 1)), 1e-6)}
    if weather_positions:
        x[:, :, weather_positions] = (x[:, :, weather_positions] - scaler["mean"]) / scaler["std"]
    y = np.asarray([row["y"] for row in rows], dtype=np.float32)
    raw = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    return torch.from_numpy(x), torch.from_numpy(y), raw, scaler


def denormalize_predictions(normalized: np.ndarray, normalizer: Dict[str, Any]) -> np.ndarray:
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    predictions = normalized.astype(np.float64) * scales + means
    predictions[..., 0] %= 360.0
    predictions[..., 1] = np.clip(predictions[..., 1], -90.0, 90.0)
    predictions[..., 2] = np.maximum(predictions[..., 2], 0.0)
    return predictions


def baseline_metrics(rows: List[Dict[str, Any]], normalizer: Dict[str, Any]) -> Dict[str, Any]:
    normalized = np.asarray([[step[:6] for step in row["x"]] for row in rows], dtype=np.float64)
    means = np.asarray(normalizer["mean"], dtype=np.float64)
    scales = np.asarray(normalizer["std"], dtype=np.float64)
    raw = normalized * scales + means
    last = raw[:, -1, :]
    actual = np.asarray([row["target_raw"] for row in rows], dtype=np.float64)
    persistence = np.zeros_like(actual)
    persistence[..., 0] = last[:, None, 0]
    persistence[..., 1] = last[:, None, 1]
    persistence[..., 2] = last[:, None, 2]
    cv = persistence.copy()
    steps = np.arange(1, actual.shape[1] + 1, dtype=np.float64)[None, :]
    cv[..., 0] = (last[:, None, 0] + steps * last[:, None, 4]) % 360.0
    cv[..., 1] = np.clip(last[:, None, 1] + steps * last[:, None, 5], -90.0, 90.0)
    return {
        "persistence": metric_summary(persistence, actual),
        "constant_velocity": metric_summary(cv, actual),
    }


def train_variant(
    architecture: str,
    name: str,
    indices: Sequence[int],
    train_rows: List[Dict[str, Any]],
    validation_rows: List[Dict[str, Any]],
    test_rows: List[Dict[str, Any]],
    normalizer: Dict[str, Any],
    output_dir: Path,
    seed: int,
    epochs: int,
    patience: int,
    batch_size: int,
    device: torch.device,
) -> Dict[str, Any]:
    seed_everything(seed)
    train_x, train_y, _, weather_scaler = make_inputs(train_rows, indices, train=True)
    valid_x, valid_y, _, _ = make_inputs(validation_rows, indices, scaler=weather_scaler)
    test_x, _, test_raw, _ = make_inputs(test_rows, indices, scaler=weather_scaler)
    train_loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=batch_size,
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    if architecture == "residual":
        model = ResidualTrackCNN(input_features=len(indices), horizons=train_y.shape[1], dropout=0.2).to(device)
    else:
        model = TrackCNN(input_features=len(indices), horizons=train_y.shape[1], dropout=0.2).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    best_loss, best_epoch, stale = math.inf, 0, 0
    best_state = None
    started = time.time()
    for epoch in range(1, epochs + 1):
        model.train()
        for features, targets in train_loader:
            features, targets = features.to(device), targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            if architecture == "residual":
                prior = constant_velocity_normalized(features[:, :, :6], normalizer, train_y.shape[1])
                loss = residual_loss(model, model(features), targets - prior)
            else:
                loss = multi_task_loss(model, model(features), targets)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            valid_x_device, valid_y_device = valid_x.to(device), valid_y.to(device)
            if architecture == "residual":
                prior = constant_velocity_normalized(valid_x_device[:, :, :6], normalizer, train_y.shape[1])
                valid_loss = float(residual_loss(model, model(valid_x_device), valid_y_device - prior))
            else:
                valid_loss = float(multi_task_loss(model, model(valid_x_device), valid_y_device))
        print(f"{name} epoch={epoch} validation_loss={valid_loss:.6f}", flush=True)
        if valid_loss < best_loss:
            best_loss, best_epoch, stale = valid_loss, epoch, 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale += 1
            if stale >= patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        test_x_device = test_x.to(device)
        outputs = model(test_x_device)
        normalized_prediction = torch.cat((outputs["track"], outputs["wind"]), dim=-1)
        if architecture == "residual":
            prior = constant_velocity_normalized(test_x_device[:, :, :6], normalizer, train_y.shape[1])
            normalized_prediction = normalized_prediction + prior
        normalized_prediction = normalized_prediction.cpu().numpy()
    predictions = denormalize_predictions(normalized_prediction, normalizer)
    metrics = metric_summary(predictions, test_raw)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "variant": name,
            "architecture": architecture,
            "source_feature_indices": list(indices),
            "weather_scaler": None
            if weather_scaler is None
            else {key: value.tolist() for key, value in weather_scaler.items()},
            "track_normalizer": normalizer,
            "seed": seed,
        },
        output_dir / f"{name}.pth",
    )
    return {
        "test_metrics": metrics,
        "best_validation_loss": best_loss,
        "best_epoch": best_epoch,
        "training_seconds": round(time.time() - started, 2),
        "weather_scaler": None
        if weather_scaler is None
        else {key: value.tolist() for key, value in weather_scaler.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--paired-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "windows",
    )
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "historical_500_850" / "ablation")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--architecture", choices=("direct", "residual"), default="direct")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    args = parser.parse_args()
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    train_rows = read_rows(args.paired_root / "train.jsonl")
    validation_rows = read_rows(args.paired_root / "validation.jsonl")
    test_rows = read_rows(args.paired_root / "test.jsonl")
    if not train_rows or not validation_rows or not test_rows:
        raise ValueError("paired train, validation, and test splits must all contain windows")
    manifest = json.loads((args.data_root / "processed" / "manifest.json").read_text(encoding="utf-8"))
    normalizer = manifest["preprocessing"]["normalizer"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    print(
        f"device={device} train={len(train_rows)} validation={len(validation_rows)} test={len(test_rows)} seed={args.seed}",
        flush=True,
    )
    selected_variants = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = sorted(set(selected_variants) - set(VARIANTS))
    if unknown or not selected_variants:
        raise ValueError("unknown or empty variants: " + ", ".join(unknown or selected_variants))
    results = {}
    for name in selected_variants:
        indices = VARIANTS[name]
        results[name] = train_variant(
            args.architecture,
            name,
            indices,
            train_rows,
            validation_rows,
            test_rows,
            normalizer,
            output_dir,
            args.seed,
            args.epochs,
            args.patience,
            args.batch_size,
            device,
        )

    baseline = baseline_metrics(test_rows, normalizer)
    improvements = {}
    if "track_only" in results:
        track_mae = results["track_only"]["test_metrics"]["by_horizon"]
        for name, result in results.items():
            improvements[name] = []
            for reference, candidate in zip(track_mae, result["test_metrics"]["by_horizon"]):
                improvements[name].append(
                    {
                        "lead_hours": reference["lead_hours"],
                        "path_mae_improvement_vs_track_only_pct": (
                            100.0 * (reference["path_mae_km"] - candidate["path_mae_km"]) / reference["path_mae_km"]
                        ),
                    }
                )
    output = {
        "experiment": "controlled_era5_500_850_ablation",
        "architecture": args.architecture,
        "run_id": run_id,
        "device": str(device),
        "seed": args.seed,
        "epochs_max": args.epochs,
        "patience": args.patience,
        "features": {
            "track_only": ["track_6_features"],
            "fusion_500_850": ["track_6_features", "u500", "v500", "u850", "v850", "era5_age_hours"],
            "fusion_500_only": ["track_6_features", "u500", "v500", "era5_age_hours"],
            "fusion_850_only": ["track_6_features", "u850", "v850", "era5_age_hours"],
        },
        "paired_counts": {"train": len(train_rows), "validation": len(validation_rows), "test": len(test_rows)},
        "paired_storms": {
            "train": len({str(row["typhoon_id"]) for row in train_rows}),
            "validation": len({str(row["typhoon_id"]) for row in validation_rows}),
            "test": len({str(row["typhoon_id"]) for row in test_rows}),
        },
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "split_file_sha256": manifest["preprocessing"].get("split_file_sha256"),
        "test_baselines": baseline,
        "variants": results,
        "improvements_vs_track_only": improvements,
        "limitations": [
            "Single random seed; repeat seeds before making a stability claim.",
            "ERA5-paired subset only; test split includes matched years 2020, 2021, and 2025, not every frozen test storm.",
            "Scores are conditional on available historical ERA5 files and the exact paired-window rule.",
        ],
    }
    (output_dir / "metrics.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_dir.resolve()), "test_baselines": baseline, "variants": {k: v["test_metrics"] for k, v in results.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
