"""Evaluate conditional and joint uncertainty extensions on the frozen split."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
import torch

from ml.analyze_era5_turn_cases import classify_turns
from ml.data_audit import PROJECT_ROOT
from ml.models.track_cnn import TrackCNN
from ml.uncertainty_regions import (
    calibrate_location_ellipse,
    ellipse_metrics,
    local_xy_errors,
    storm_group_quantile,
)
from ml.train import haversine_km


INTENSITY_GROUPS = ("TD_TS", "STS_TY", "STY_PLUS")
TURN_GROUPS = ("turning_ge_30deg", "not_turning_ge_30deg")


def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def intensity_group(power: float) -> str:
    if not np.isfinite(power):
        return "unknown"
    if power <= 8.0:
        return "TD_TS"
    if power <= 12.0:
        return "STS_TY"
    return "STY_PLUS"


def intensity_groups(rows: Sequence[Dict[str, Any]], normalizer: Dict[str, Any]) -> List[str]:
    means = np.asarray(normalizer["mean"], dtype=np.float64)
    scales = np.asarray(normalizer["std"], dtype=np.float64)
    values = [float(row["x"][-1][3]) * scales[3] + means[3] for row in rows]
    return [intensity_group(value) for value in values]


def predict(model: torch.nn.Module, rows: Sequence[Dict[str, Any]], normalizer: Dict[str, Any], device: torch.device) -> np.ndarray:
    features = torch.as_tensor([row["x"] for row in rows], dtype=torch.float32)
    loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(features), batch_size=512, shuffle=False)
    means = np.asarray(normalizer["mean"][:3], dtype=np.float64)
    scales = np.asarray(normalizer["std"][:3], dtype=np.float64)
    batches = []
    model.eval()
    with torch.no_grad():
        for (batch,) in loader:
            output = model(batch.to(device))
            batches.append(torch.cat((output["track"], output["wind"]), dim=-1).cpu().numpy())
    result = np.concatenate(batches, axis=0) * scales + means
    result[..., 0] %= 360.0
    result[..., 1] = np.clip(result[..., 1], -90.0, 90.0)
    result[..., 2] = np.maximum(result[..., 2], 0.0)
    return result


def group_counts(labels: Sequence[str], storm_ids: Sequence[str]) -> Dict[str, Dict[str, int]]:
    result = {}
    for label in sorted(set(labels)):
        selected = [storm for group, storm in zip(labels, storm_ids) if group == label]
        result[label] = {"windows": len(selected), "storms": len(set(selected))}
    return result


def _noncontracting(ellipses: List[Dict[str, Any]]) -> None:
    major = np.maximum.accumulate([item["semi_major_axis_km"] for item in ellipses])
    minor = np.maximum.accumulate([item["semi_minor_axis_km"] for item in ellipses])
    for index, item in enumerate(ellipses):
        item["semi_major_axis_km"] = float(major[index])
        item["semi_minor_axis_km"] = float(minor[index])
        item["area_km2"] = float(np.pi * major[index] * minor[index])


def conditional_ellipse_evaluation(
    validation_center: np.ndarray,
    validation_actual: np.ndarray,
    validation_rows: Sequence[Dict[str, Any]],
    test_center: np.ndarray,
    test_actual: np.ndarray,
    test_rows: Sequence[Dict[str, Any]],
    normalizer: Dict[str, Any],
    global_ellipses: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    validation_ids = [str(row["typhoon_id"]) for row in validation_rows]
    test_ids = [str(row["typhoon_id"]) for row in test_rows]
    validation_turns = classify_turns(list(validation_rows), normalizer)
    test_turns = classify_turns(list(test_rows), normalizer)
    validation_intensity = intensity_groups(validation_rows, normalizer)
    test_intensity = intensity_groups(test_rows, normalizer)
    definitions = {
        "intensity": (validation_intensity, test_intensity, INTENSITY_GROUPS),
        "turning": (
            ["turning_ge_30deg" if item == "turning_ge_30deg" else "not_turning_ge_30deg" for item in validation_turns],
            ["turning_ge_30deg" if item == "turning_ge_30deg" else "not_turning_ge_30deg" for item in test_turns],
            TURN_GROUPS,
        ),
    }
    output: Dict[str, Any] = {}
    for dimension, (validation_labels, test_labels, labels) in definitions.items():
        dimension_result = {
            "calibration_group_counts": group_counts(validation_labels, validation_ids),
            "evaluation_group_counts": group_counts(test_labels, test_ids),
            "groups": {},
        }
        for label in labels:
            calibration_mask = np.asarray(validation_labels) == label
            evaluation_mask = np.asarray(test_labels) == label
            calibration_storms = len({storm for storm, keep in zip(validation_ids, calibration_mask) if keep})
            evaluation_storms = len({storm for storm, keep in zip(test_ids, evaluation_mask) if keep})
            if calibration_storms < 10 or evaluation_storms == 0:
                dimension_result["groups"][label] = {
                    "status": "insufficient_storms",
                    "calibration_storms": calibration_storms,
                    "evaluation_storms": evaluation_storms,
                }
                continue

            calibrated = []
            for horizon in range(validation_actual.shape[1]):
                ellipse, _ = calibrate_location_ellipse(
                    validation_center[calibration_mask, horizon, :2],
                    validation_actual[calibration_mask, horizon, :2],
                    np.asarray(validation_ids)[calibration_mask].tolist(),
                    0.9,
                )
                calibrated.append(ellipse)
            _noncontracting(calibrated)
            per_horizon = []
            for horizon, ellipse in enumerate(calibrated):
                eval_mask = evaluation_mask
                conditional_metrics = ellipse_metrics(
                    test_center[eval_mask, horizon, :2],
                    test_actual[eval_mask, horizon, :2],
                    np.asarray(test_ids)[eval_mask].tolist(),
                    ellipse,
                )
                global_metrics = ellipse_metrics(
                    test_center[eval_mask, horizon, :2],
                    test_actual[eval_mask, horizon, :2],
                    np.asarray(test_ids)[eval_mask].tolist(),
                    global_ellipses[horizon],
                )
                per_horizon.append({
                    "lead_hours": 6 * (horizon + 1),
                    "conditional_calibration": conditional_metrics,
                    "global_calibration_on_same_group": global_metrics,
                    "area_ratio_conditional_to_global": float(conditional_metrics["area_km2"] / global_metrics["area_km2"]),
                })
            dimension_result["groups"][label] = {
                "status": "evaluated",
                "calibration_storms": calibration_storms,
                "calibration_windows": int(calibration_mask.sum()),
                "evaluation_storms": evaluation_storms,
                "evaluation_windows": int(evaluation_mask.sum()),
                "by_horizon": per_horizon,
            }
        output[dimension] = dimension_result
    return output


def _ellipse_from_joint(errors: np.ndarray, score: np.ndarray, storm_ids: Sequence[str], coverage: float, speed_scale: float) -> Dict[str, Any]:
    covariance = np.cov(errors, rowvar=False) + np.eye(2) * 1e-6
    quantile = storm_group_quantile(score, storm_ids, coverage)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    axes = quantile * np.sqrt(np.maximum(eigenvalues[order], 0.0))
    vector = eigenvectors[:, order[0]]
    bearing = float(np.degrees(np.arctan2(vector[0], vector[1])) % 180.0)
    return {
        "geometry": "conformal_ellipse",
        "coverage": coverage,
        "joint_score_quantile": float(quantile),
        "covariance_km2": covariance.tolist(),
        "semi_major_axis_km": float(axes[0]),
        "semi_minor_axis_km": float(axes[1]),
        "bearing_deg": bearing,
        "area_km2": float(np.pi * axes[0] * axes[1]),
        "speed_scale_native": float(speed_scale),
        "speed_half_width_native": float(quantile * speed_scale),
    }


def joint_location_speed_evaluation(
    validation_center: np.ndarray,
    validation_actual: np.ndarray,
    validation_ids: Sequence[str],
    test_center: np.ndarray,
    test_actual: np.ndarray,
    test_ids: Sequence[str],
) -> Dict[str, Any]:
    coverage = 0.9
    calibrations = []
    for horizon in range(validation_actual.shape[1]):
        xy_errors = local_xy_errors(validation_center[:, horizon, :2], validation_actual[:, horizon, :2])
        speed_errors = validation_actual[:, horizon, 2] - validation_center[:, horizon, 2]
        speed_scale = float(np.std(speed_errors, ddof=1))
        if not np.isfinite(speed_scale) or speed_scale < 1e-8:
            speed_scale = 1.0
        covariance = np.cov(xy_errors, rowvar=False) + np.eye(2) * 1e-6
        inverse = np.linalg.inv(covariance)
        location_score = np.sqrt(np.maximum(np.einsum("ni,ij,nj->n", xy_errors, inverse, xy_errors), 0.0))
        joint_score = np.maximum(location_score, np.abs(speed_errors) / speed_scale)
        calibration = _ellipse_from_joint(xy_errors, joint_score, validation_ids, coverage, speed_scale)
        calibrations.append(calibration)

    major = np.maximum.accumulate([item["semi_major_axis_km"] for item in calibrations])
    minor = np.maximum.accumulate([item["semi_minor_axis_km"] for item in calibrations])
    widths = np.maximum.accumulate([item["speed_half_width_native"] for item in calibrations])
    for horizon, item in enumerate(calibrations):
        item["semi_major_axis_km"] = float(major[horizon])
        item["semi_minor_axis_km"] = float(minor[horizon])
        item["area_km2"] = float(np.pi * major[horizon] * minor[horizon])
        item["speed_half_width_native"] = float(widths[horizon])

    evaluation = []
    test_ids = np.asarray(test_ids)
    for horizon, calibration in enumerate(calibrations):
        xy_errors = local_xy_errors(test_center[:, horizon, :2], test_actual[:, horizon, :2])
        angle = np.deg2rad(calibration["bearing_deg"])
        along = xy_errors[:, 0] * np.sin(angle) + xy_errors[:, 1] * np.cos(angle)
        across = xy_errors[:, 0] * np.cos(angle) - xy_errors[:, 1] * np.sin(angle)
        position_score = (along / calibration["semi_major_axis_km"]) ** 2 + (across / calibration["semi_minor_axis_km"]) ** 2
        speed_error = np.abs(test_actual[:, horizon, 2] - test_center[:, horizon, 2])
        position_inside = position_score <= 1.0
        speed_inside = speed_error <= calibration["speed_half_width_native"]
        joint_inside = position_inside & speed_inside
        storm_joint: Dict[str, bool] = {}
        for storm, inside in zip(test_ids, joint_inside):
            storm_joint[str(storm)] = storm_joint.get(str(storm), True) and bool(inside)
        evaluation.append({
            "lead_hours": 6 * (horizon + 1),
            "window_count": int(len(test_ids)),
            "storm_count": int(len(set(test_ids.tolist()))),
            "position_window_coverage": float(position_inside.mean()),
            "speed_window_coverage": float(speed_inside.mean()),
            "joint_window_coverage": float(joint_inside.mean()),
            "joint_storm_coverage": float(np.mean(list(storm_joint.values()))),
            "ellipse_area_km2": calibration["area_km2"],
            "speed_half_width_native": calibration["speed_half_width_native"],
            "speed_mae_native": float(speed_error.mean()),
        })
    return {
        "target_coverage": coverage,
        "calibration_unit": "storm; per horizon, maximum joint score across each storm's windows",
        "joint_score": "max(position Mahalanobis radius, absolute source-speed residual / validation residual standard deviation)",
        "wind_field": "CMA best-track speed field in source-native units; unit metadata is not verified, so this is not a wind category or hazard probability",
        "calibration_storms": int(len(set(validation_ids))),
        "calibration_windows": len(validation_ids),
        "evaluation": evaluation,
        "calibration_by_horizon": calibrations,
    }


def run(checkpoint_path: Path, data_root: Path, output_path: Path, device_name: str) -> Dict[str, Any]:
    device = torch.device(device_name)
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    normalizer = checkpoint["normalizer"]
    architecture = checkpoint.get("architecture", {})
    model = TrackCNN(input_features=6, horizons=6, dropout=float(architecture.get("dropout", 0.3))).to(device)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    validation_rows = read_rows(data_root / "processed" / "validation.jsonl")
    test_rows = read_rows(data_root / "processed" / "test.jsonl")
    validation_center = predict(model, validation_rows, normalizer, device)
    test_center = predict(model, test_rows, normalizer, device)
    validation_actual = np.asarray([row["target_raw"] for row in validation_rows], dtype=np.float64)
    test_actual = np.asarray([row["target_raw"] for row in test_rows], dtype=np.float64)
    validation_ids = [str(row["typhoon_id"]) for row in validation_rows]
    test_ids = [str(row["typhoon_id"]) for row in test_rows]
    stored = json.loads((PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_20261008.json").read_text(encoding="utf-8"))
    if stored.get("checkpoint_sha256") != hashlib.sha256(checkpoint_path.read_bytes()).hexdigest():
        raise ValueError("stored calibration results do not match the selected checkpoint")
    global_ellipses = stored["calibration"]["location_ellipse_90"]
    global_evaluation = conditional_ellipse_evaluation(
        validation_center, validation_actual, validation_rows,
        test_center, test_actual, test_rows, normalizer, global_ellipses,
    )
    joint = joint_location_speed_evaluation(
        validation_center, validation_actual, validation_ids,
        test_center, test_actual, test_ids,
    )
    result = {
        "experiment": "stage4_conditional_and_joint_uncertainty",
        "checkpoint": str(checkpoint_path.resolve()),
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "dataset_fingerprint_sha256": stored["dataset_fingerprint_sha256"],
        "split": {"calibration": "validation", "evaluation": "frozen_test", "calibration_storms": len(set(validation_ids)), "evaluation_storms": len(set(test_ids))},
        "group_definitions": {
            "intensity": "last observed normalized power, inverse-transformed using training-only scaler; TD/TS (<=8), STS/TY (<=12), STY/SuperTY (>12); these follow source CMA category proxy, not a wind-speed threshold",
            "turning": "course change across latest two input segments; turning >=30 degrees versus all other windows; each segment must be >=50 km",
        },
        "conditional_ellipse": global_evaluation,
        "joint_location_speed": joint,
        "limitations": [
            "No verified coastline geometry or landfall event labels are present; landfall subgroup metrics are not computed.",
            "Speed is retained in the source-native CMA field scale; unit conversion and hazard-category interpretation are not supported by local metadata.",
            "Conditional groups overlap at the storm level and results are exploratory; report group storm counts and do not infer unconditional coverage guarantees.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output_path.resolve()),
        "conditional_ellipse": {
            key: {label: value.get("evaluation_group_counts", {}).get(label) for label in value.get("groups", {})}
            for key, value in global_evaluation.items()
        },
        "joint_location_speed": joint["evaluation"],
        "limitations": result["limitations"],
    }, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "reports" / "stage4_extensions_20261008.json")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    run(args.checkpoint, args.data_root, args.output, args.device)


if __name__ == "__main__":
    main()
