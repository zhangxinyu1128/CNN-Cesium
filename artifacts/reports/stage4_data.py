# -*- coding: utf-8 -*-
"""Aggregate stage-4 conformal location-region results for reporting."""
import json
from pathlib import Path

TRACK = Path(r"D:\project\CNN-Cesium\artifacts\reports\track_api_uncertainty_20261008.json")
RING = Path(r"D:\project\CNN-Cesium\artifacts\era5\annular_steering_flow\uncertainty\ring_500_850_uncertainty.json")


def load():
    track = json.loads(TRACK.read_text(encoding="utf-8"))
    ring = json.loads(RING.read_text(encoding="utf-8"))
    return {"track": track, "ring": ring}


def horizons(track, ring):
    return [h["lead_hours"] for h in track["evaluation"]["by_horizon"]]


def pct(base, cand):
    return 100.0 * (base - cand) / base
