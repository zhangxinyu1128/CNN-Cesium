"""Controlled ablation for the ERA5 annular steering-flow features.

The frozen splits, model, baselines and metrics are identical to the existing
center-point ERA5 experiment; only the input feature indices change.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ml.train_era5_ablation import baseline_metrics, read_rows, train_variant
from ml.data_audit import PROJECT_ROOT
import torch


RING_INDICES = (11, 12, 13, 14)
VARIANTS = {
    "track_only": (0, 1, 2, 3, 4, 5),
    "center_500_850": tuple(range(11)),
    "ring_500_850": (0, 1, 2, 3, 4, 5, 11, 12, 13, 14),
    "center_plus_ring": tuple(range(15)),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--paired-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "ring_windows",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "ablation",
    )
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seeds", default="2026,2027,2028")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--architecture", choices=("direct", "residual"), default="direct")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    args = parser.parse_args()

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    train_rows = read_rows(args.paired_root / "train.jsonl")
    validation_rows = read_rows(args.paired_root / "validation.jsonl")
    test_rows = read_rows(args.paired_root / "test.jsonl")
    if not train_rows or not validation_rows or not test_rows:
        raise ValueError("ring-window train, validation, and test splits must all contain windows")
    manifest = json.loads((args.data_root / "processed" / "manifest.json").read_text(encoding="utf-8"))
    normalizer = manifest["preprocessing"]["normalizer"]
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    selected = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = sorted(set(selected) - set(VARIANTS))
    if unknown or not selected:
        raise ValueError("unknown or empty variants: " + ", ".join(unknown or selected))

    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment": "era5_annular_steering_flow_ablation",
        "architecture": args.architecture,
        "seeds": seeds,
        "epochs_max": args.epochs,
        "patience": args.patience,
        "batch_size": args.batch_size,
        "device": str(device),
        "features": {
            "track_only": ["track_6_features"],
            "center_500_850": ["track_6_features", "u500_center", "v500_center", "u850_center", "v850_center", "era5_age_hours"],
            "ring_500_850": ["track_6_features", "u500_ring", "v500_ring", "u850_ring", "v850_ring"],
            "center_plus_ring": ["track_6_features", "center_500_850", "ring_500_850"],
        },
        "paired_counts": {"train": len(train_rows), "validation": len(validation_rows), "test": len(test_rows)},
        "paired_storms": {
            "train": len({str(row["typhoon_id"]) for row in train_rows}),
            "validation": len({str(row["typhoon_id"]) for row in validation_rows}),
            "test": len({str(row["typhoon_id"]) for row in test_rows}),
        },
        "dataset_fingerprint_sha256": manifest["source"]["fingerprint_sha256"],
        "test_baselines": baseline_metrics(test_rows, normalizer),
        "runs": [],
    }
    for seed in seeds:
        run_dir = root / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_seed{seed}"
        run_dir.mkdir(parents=True, exist_ok=False)
        print(f"=== seed {seed} -> {run_dir} ===", flush=True)
        results = {}
        for name in selected:
            results[name] = train_variant(
                args.architecture,
                name,
                VARIANTS[name],
                train_rows,
                validation_rows,
                test_rows,
                normalizer,
                run_dir,
                seed,
                args.epochs,
                args.patience,
                args.batch_size,
                device,
            )
        improvements = {}
        if "track_only" in results:
            reference = results["track_only"]["test_metrics"]["by_horizon"]
            for name, result in results.items():
                improvements[name] = [
                    {
                        "lead_hours": base["lead_hours"],
                        "path_mae_improvement_vs_track_only_pct": 100.0
                        * (base["path_mae_km"] - candidate["path_mae_km"])
                        / base["path_mae_km"],
                    }
                    for base, candidate in zip(reference, result["test_metrics"]["by_horizon"])
                ]
        payload = {
            key: value
            for key, value in summary.items()
            if key != "runs"
        }
        payload.update(
            {
                "seed": seed,
                "run_dir": str(run_dir.resolve()),
                "variants": results,
                "improvements_vs_track_only": improvements,
            }
        )
        (run_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["runs"].append(payload)
        print(
            json.dumps(
                {
                    "seed": seed,
                    "variants": {
                        name: result["test_metrics"]["by_horizon"][-1]["path_mae_km"]
                        for name, result in results.items()
                    },
                    "lead_hours": results[selected[0]]["test_metrics"]["by_horizon"][-1]["lead_hours"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )

    (root / "ablation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("summary written to", (root / "ablation_summary.json").resolve())


if __name__ == "__main__":
    main()
