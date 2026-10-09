# -*- coding: utf-8 -*-
"""Aggregate stage-3 annular steering-flow results for reporting."""
import json
import statistics
from pathlib import Path

SUMMARY = Path(r"D:\project\CNN-Cesium\artifacts\era5\annular_steering_flow\ablation\ablation_summary.json")
OFFICIAL_COMPARISON = Path(r"D:\project\CNN-Cesium\artifacts\era5\annular_steering_flow\official_comparison\official_comparison.json")
STAGE1_REPORT = Path(r"D:\project\CNN-Cesium\official_forecast\build_report.json")


def load():
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    names = ["track_only", "center_500_850", "ring_500_850", "center_plus_ring"]
    horizons = [h["lead_hours"] for h in data["runs"][0]["variants"]["track_only"]["test_metrics"]["by_horizon"]]
    stats = {}
    for name in names:
        per = []
        for i in range(len(horizons)):
            vals = [r["variants"][name]["test_metrics"]["by_horizon"][i]["path_mae_km"] for r in data["runs"]]
            per.append({"mae": statistics.mean(vals), "std": statistics.stdev(vals), "values": vals})
        stats[name] = per
    for name in ("persistence", "constant_velocity"):
        stats[name] = [
            {"mae": h["path_mae_km"], "std": 0.0, "values": [h["path_mae_km"]]}
            for h in data["test_baselines"][name]["by_horizon"]
        ]
    official = json.loads(OFFICIAL_COMPARISON.read_text(encoding="utf-8"))
    stage1 = json.loads(STAGE1_REPORT.read_text(encoding="utf-8"))
    return data, horizons, stats, official, stage1
