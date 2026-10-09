"""Storm-group conformal calibration for two-dimensional location regions."""

from math import ceil
from typing import Dict, Sequence, Tuple

import numpy as np


EARTH_KM_PER_DEGREE = 111.195


def local_xy_errors(center: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Return east/north location residuals in km using a local tangent plane."""
    longitude_delta = (actual[:, 0] - center[:, 0] + 180.0) % 360.0 - 180.0
    mean_latitude = np.deg2rad((actual[:, 1] + center[:, 1]) * 0.5)
    east = longitude_delta * np.cos(mean_latitude) * EARTH_KM_PER_DEGREE
    north = (actual[:, 1] - center[:, 1]) * EARTH_KM_PER_DEGREE
    return np.column_stack((east, north))


def storm_group_quantile(scores: np.ndarray, groups: Sequence[str], coverage: float) -> float:
    if not 0.0 < coverage < 1.0 or len(scores) != len(groups) or not len(scores):
        raise ValueError("scores/groups must be non-empty and coverage must be between 0 and 1")
    maxima: Dict[str, float] = {}
    for score, group in zip(scores, groups):
        maxima[group] = max(maxima.get(group, 0.0), float(score))
    ordered = np.sort(np.asarray(list(maxima.values()), dtype=np.float64))
    rank = min(ceil((len(ordered) + 1) * coverage), len(ordered))
    return float(ordered[rank - 1])


def calibrate_location_ellipse(
    center: np.ndarray,
    actual: np.ndarray,
    groups: Sequence[str],
    coverage: float = 0.9,
) -> Tuple[dict, np.ndarray]:
    errors = local_xy_errors(center, actual)
    covariance = np.cov(errors, rowvar=False)
    covariance = np.asarray(covariance, dtype=np.float64) + np.eye(2) * 1e-6
    inverse = np.linalg.inv(covariance)
    scores = np.sqrt(np.maximum(np.einsum("ni,ij,nj->n", errors, inverse, errors), 0.0))
    quantile = storm_group_quantile(scores, groups, coverage)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    axes = quantile * np.sqrt(np.maximum(eigenvalues[order], 0.0))
    major_vector = eigenvectors[:, order[0]]
    bearing = float(np.degrees(np.arctan2(major_vector[0], major_vector[1])) % 180.0)
    calibration = {
        "geometry": "conformal_ellipse",
        "coverage": float(coverage),
        "mahalanobis_quantile": float(quantile),
        "covariance_km2": covariance.tolist(),
        "semi_major_axis_km": float(axes[0]),
        "semi_minor_axis_km": float(axes[1]),
        "bearing_deg": bearing,
        "area_km2": float(np.pi * axes[0] * axes[1]),
    }
    return calibration, scores


def ellipse_metrics(center: np.ndarray, actual: np.ndarray, groups: Sequence[str], calibration: dict) -> dict:
    errors = local_xy_errors(center, actual)
    angle = np.deg2rad(float(calibration["bearing_deg"]))
    along = errors[:, 0] * np.sin(angle) + errors[:, 1] * np.cos(angle)
    across = errors[:, 0] * np.cos(angle) - errors[:, 1] * np.sin(angle)
    score = (along / float(calibration["semi_major_axis_km"])) ** 2
    score += (across / float(calibration["semi_minor_axis_km"])) ** 2
    covered = score <= 1.0
    per_storm: Dict[str, bool] = {}
    for is_covered, group in zip(covered, groups):
        per_storm[group] = per_storm.get(group, True) and bool(is_covered)
    return {
        "geometry": calibration["geometry"],
        "location_coverage_90": float(np.mean(covered)),
        "location_storm_coverage_90": float(np.mean(list(per_storm.values()))),
        "semi_major_axis_km": calibration["semi_major_axis_km"],
        "semi_minor_axis_km": calibration["semi_minor_axis_km"],
        "bearing_deg": calibration["bearing_deg"],
        "area_km2": calibration["area_km2"],
        "mean_error_km": float(np.mean(np.linalg.norm(errors, axis=1))),
    }


def location_energy_score(draws: np.ndarray, actual: np.ndarray) -> float:
    """Multivariate energy score for location ensembles, in kilometers."""
    if draws.ndim != 3 or draws.shape[0] != len(actual) or draws.shape[2] != 2:
        raise ValueError("draws must have shape (windows, samples, 2)")
    centers = np.median(draws, axis=1)
    sample_errors = local_xy_errors(
        np.repeat(centers, draws.shape[1], axis=0),
        draws.reshape(-1, 2),
    ).reshape(draws.shape)
    actual_errors = local_xy_errors(centers, actual)
    first_term = np.linalg.norm(sample_errors - actual_errors[:, None, :], axis=2).mean(axis=1)
    pairwise = np.linalg.norm(
        sample_errors[:, :, None, :] - sample_errors[:, None, :, :], axis=3
    ).mean(axis=(1, 2))
    return float(np.mean(first_term - 0.5 * pairwise))
