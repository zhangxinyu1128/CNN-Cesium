"""Build and train a small 500/850 hPa ERA5 fusion pilot.

This is intentionally a smoke experiment on the 2025 ERA5-paired test storms.
It validates the data path; its validation score must not be reported as a
frozen generalization benchmark.
"""

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Set

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from ml.models.track_cnn import TrackCNN, multi_task_loss
from ml.train import metric_summary, seed_everything


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_rows(track_path: Path, weather_path: Path) -> List[Dict[str, Any]]:
    weather = {}
    for row in read_jsonl(weather_path):
        wind = row.get("wind", {})
        if "500" in wind and "850" in wind:
            weather[(str(row["typhoon_id"]), str(row["time"]))] = [
                float(wind["500"]["u"]), float(wind["500"]["v"]),
                float(wind["850"]["u"]), float(wind["850"]["v"]),
            ]
    rows = []
    for record in read_jsonl(track_path):
        features = []
        for index, timestamp in enumerate(record["history_times"]):
            values = weather.get((str(record["typhoon_id"]), str(timestamp)))
            if values is None:
                break
            features.append(list(record["x"][index]) + values)
        if len(features) == len(record["history_times"]):
            rows.append({"typhoon_id": str(record["typhoon_id"]), "x": features, "y": record["y"], "target_raw": record["target_raw"]})
    return rows


def normalize(rows: List[Dict[str, Any]], train_ids: Set[str]) -> Dict[str, Any]:
    train = [row for row in rows if row["typhoon_id"] in train_ids]
    weather_values = np.asarray([step[6:] for row in train for step in row["x"]], dtype=np.float32)
    mean = weather_values.mean(axis=0)
    std = np.maximum(weather_values.std(axis=0), 1e-6)
    for row in rows:
        for step in row["x"]:
            step[6:] = ((np.asarray(step[6:], dtype=np.float32) - mean) / std).tolist()
    return {"mean": mean.tolist(), "std": std.tolist()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, default=Path("data/processed/test.jsonl"))
    parser.add_argument("--weather", type=Path, default=Path("artifacts/era5/track_points_2025.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/era5/fusion_pilot_500_850"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    seed_everything(args.seed)
    rows = build_rows(args.track, args.weather)
    if not rows:
        raise RuntimeError("no complete 500/850 hPa paired windows were found")
    storm_ids = sorted({row["typhoon_id"] for row in rows})
    random.Random(args.seed).shuffle(storm_ids)
    split = max(1, int(len(storm_ids) * 0.8))
    train_ids, valid_ids = set(storm_ids[:split]), set(storm_ids[split:])
    weather_scaler = normalize(rows, train_ids)
    train_rows = [row for row in rows if row["typhoon_id"] in train_ids]
    valid_rows = [row for row in rows if row["typhoon_id"] in valid_ids]
    if not valid_rows:
        raise RuntimeError("paired pilot needs at least two storms for a storm-level validation split")

    def tensors(items):
        return torch.tensor([row["x"] for row in items], dtype=torch.float32), torch.tensor([row["y"] for row in items], dtype=torch.float32)

    train_x, train_y = tensors(train_rows)
    valid_x, valid_y = tensors(valid_rows)
    model = TrackCNN(input_features=10, horizons=6, dropout=0.2)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    loader = DataLoader(TensorDataset(train_x, train_y), batch_size=min(64, len(train_rows)), shuffle=True, generator=torch.Generator().manual_seed(args.seed))
    best = float("inf")
    best_state = None
    for epoch in range(1, args.epochs + 1):
        model.train()
        for features, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = multi_task_loss(model, model(features), targets)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            valid_loss = float(multi_task_loss(model, model(valid_x), valid_y))
        print(f"epoch={epoch} validation_loss={valid_loss:.6f}")
        if valid_loss < best:
            best, best_state = valid_loss, {key: value.detach().clone() for key, value in model.state_dict().items()}
    model.load_state_dict(best_state)
    with torch.no_grad():
        output = model(valid_x)
        pred = torch.cat((output["track"], output["wind"]), dim=-1).numpy()
    manifest = json.loads(Path("data/processed/manifest.json").read_text(encoding="utf-8"))
    scaler = manifest["preprocessing"]["normalizer"]
    means = np.asarray(scaler["mean"][:3], dtype=np.float64)
    scales = np.asarray(scaler["std"][:3], dtype=np.float64)
    pred = pred * scales + means
    actual = np.asarray([row["target_raw"] for row in valid_rows], dtype=np.float64)
    pred[..., 0] %= 360.0
    pred[..., 1] = np.clip(pred[..., 1], -90.0, 90.0)
    pred[..., 2] = np.maximum(pred[..., 2], 0.0)
    result = {
        "experiment": "era5_500_850_fusion_pilot",
        "status": "pipeline_smoke_test_only",
        "weather_features": ["u500", "v500", "u850", "v850"],
        "sampling": "nearest 0.25 degree grid point; exact UTC synoptic cycle; no extrapolation",
        "paired_windows": len(rows), "paired_storms": len(storm_ids),
        "train_windows": len(train_rows), "validation_windows": len(valid_rows),
        "train_storms": len(train_ids), "validation_storms": len(valid_ids),
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "validation_metrics": metric_summary(pred, actual),
        "weather_scaler": weather_scaler,
        "note": "2025 storms are in the frozen test split; do not use this pilot score as the final test benchmark.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    torch.save({"model_state_dict": model.state_dict(), "input_features": 10, "weather_scaler": weather_scaler}, args.output / "track_cnn_era5_500_850_pilot.pth")
    (args.output / "paired_windows.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("paired_windows", "paired_storms", "train_windows", "validation_windows", "validation_metrics")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
