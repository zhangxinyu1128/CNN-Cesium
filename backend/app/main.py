"""FastAPI entry point for local typhoon data and prediction services."""

import os
from pathlib import Path
import json
import math
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .schemas import (
    HealthResponse,
    PredictionRequest,
    PredictionResponse,
    TyphoonDetail,
    TyphoonIndexItem,
    TyphoonPoint,
)
from .services.data import DataRepository
from .services.era5 import Era5Repository
from .services.model import ModelService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.getenv("TC_DATA_ROOT", str(PROJECT_ROOT / "data")))
CHECKPOINT_PATH = Path(
    os.getenv("TC_MODEL_PATH", str(PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth"))
)
MODEL_VERSION = os.getenv("TC_MODEL_VERSION") or None
ERA5_ROOT = Path(os.getenv("TC_ERA5_ROOT", str(PROJECT_ROOT / "artifacts" / "era5")))
EXPERIMENT_METRICS_PATH = ERA5_ROOT / "historical_500_850_refresh_20261002" / "ablation_layers" / "20261002T160525Z" / "metrics.json"
UNCERTAINTY_PATH = ERA5_ROOT / "historical_500_850_refresh_20261002" / "uncertainty" / "fusion_500_850_uncertainty.json"
EXPERIMENT_ROOT = ERA5_ROOT / "historical_500_850_refresh_20261002"
MANIFEST_PATH = PROJECT_ROOT / "data" / "processed" / "manifest.json"
OPERATIONAL_AUDIT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "operational_forecast_audit_20261003.json"
PREDICTION_UNCERTAINTY_PATH = Path(os.getenv(
    "TC_PREDICTION_UNCERTAINTY_PATH",
    str(PROJECT_ROOT / "artifacts" / "reports" / "track_api_uncertainty_20261007.json"),
))

repository = DataRepository(DATA_ROOT)
era5_repository = Era5Repository(ERA5_ROOT)
model_service = ModelService(CHECKPOINT_PATH, MODEL_VERSION, PREDICTION_UNCERTAINTY_PATH)

app = FastAPI(
    title="Tropical Cyclone Intelligence API",
    version="0.1.0",
    description="Local historical typhoon data and model readiness API.",
)

origins = [
    item.strip()
    for item in os.getenv(
        "TC_CORS_ORIGINS",
        "http://localhost:10060,http://127.0.0.1:10060",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> dict:
    data = repository.data_status()
    model = model_service.status()
    return {
        "status": "ready"
        if data["status"] == "ready" and model["status"] == "ready"
        else "degraded",
        "service": "typhoon-api",
        "data": data,
        "model": model,
    }


@app.get("/api/years.json")
def get_years() -> List[dict]:
    """Return the object shape expected by the original Cesium dashboard."""
    return [{"year": year} for year in repository.years()]


@app.get("/api/typhoons", response_model=List[TyphoonIndexItem])
def get_typhoons(
    year: int = Query(..., ge=1900, le=2200),
    q: str = Query("", max_length=80),
    landing: int = Query(0, ge=0, le=1),
    limit: int = Query(100, ge=1, le=200),
) -> List[dict]:
    if year not in repository.years():
        raise HTTPException(status_code=404, detail="year data not found")
    return repository.list_typhoons(year, q, landing == 1, limit)


@app.get("/api/typhoons/{tfbh}/points", response_model=List[TyphoonPoint])
def get_typhoon_points(tfbh: str) -> List[dict]:
    typhoon = repository.get_typhoon(tfbh)
    if typhoon is None:
        raise HTTPException(status_code=404, detail="typhoon data not found")
    return typhoon["points"]


@app.get("/api/typhoons/{tfbh}", response_model=TyphoonDetail)
def get_typhoon(tfbh: str) -> dict:
    typhoon = repository.get_typhoon(tfbh)
    if typhoon is None:
        raise HTTPException(status_code=404, detail="typhoon data not found")
    return typhoon


@app.get("/api/typhoons/{tfbh}/era5")
def get_typhoon_era5(tfbh: str) -> dict:
    """Return only track-aligned ERA5 wind samples for the selected storm."""
    if repository.get_typhoon(tfbh) is None:
        raise HTTPException(status_code=404, detail="typhoon data not found")
    return era5_repository.get_typhoon(tfbh)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _experiment_runs(group: str) -> List[Dict[str, Any]]:
    root = EXPERIMENT_ROOT / group
    if not root.is_dir():
        return []
    runs: List[Dict[str, Any]] = []
    for run_dir in sorted(root.iterdir()):
        metrics_path = run_dir / "metrics.json"
        metrics = _read_json(metrics_path)
        if metrics:
            runs.append({"run_id": run_dir.name, "path": str(metrics_path), "metrics": metrics})
    return runs


def _metric_source(metrics: Dict[str, Any], key: str) -> Dict[str, Any]:
    baseline = metrics.get("test_baselines", {}).get(key)
    if isinstance(baseline, dict):
        return baseline
    variant = metrics.get("variants", {}).get(key)
    if isinstance(variant, dict):
        test_metrics = variant.get("test_metrics")
        if isinstance(test_metrics, dict):
            return test_metrics
    return {}


def _metric_runs(key: str, preferred_group: str) -> List[Dict[str, Any]]:
    preferred = _experiment_runs(preferred_group)
    fallback_group = "ablation" if preferred_group == "ablation_layers" else "ablation_layers"
    fallback = _experiment_runs(fallback_group)
    candidates = [run for run in preferred if _metric_source(run["metrics"], key)]
    if len(candidates) >= 2:
        return candidates
    return candidates + [run for run in fallback if _metric_source(run["metrics"], key)]


def _mean_and_std(values: List[float]) -> Dict[str, float]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return {"mean": mean, "std": 0.0}
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {"mean": mean, "std": math.sqrt(variance)}


def _aggregate_metric_runs(runs: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    by_horizon: Dict[int, List[Dict[str, Any]]] = {}
    for run in runs:
        source = _metric_source(run["metrics"], key)
        for item in source.get("by_horizon", []):
            try:
                lead_hours = int(item["lead_hours"])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(item, dict):
                by_horizon.setdefault(lead_hours, []).append(item)

    metric_fields = (
        "path_mae_km",
        "path_rmse_km",
        "path_median_km",
        "wind_mae_ms",
        "wind_rmse_ms",
        "longitude_mae_deg",
        "latitude_mae_deg",
    )
    aggregated: List[Dict[str, Any]] = []
    for lead_hours in sorted(by_horizon):
        output: Dict[str, Any] = {"lead_hours": lead_hours}
        rows = by_horizon[lead_hours]
        for field in metric_fields:
            values = [
                float(row[field])
                for row in rows
                if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
            ]
            if values:
                summary = _mean_and_std(values)
                output[field] = summary["mean"]
                output[f"{field}_std"] = summary["std"]
        output["run_count"] = len(rows)
        aggregated.append(output)

    seeds = sorted({run["metrics"].get("seed") for run in runs if run["metrics"].get("seed") is not None})
    sample_count = next(
        (
            _metric_source(run["metrics"], key).get("sample_count")
            for run in runs
            if _metric_source(run["metrics"], key).get("sample_count") is not None
        ),
        None,
    )
    return {
        "by_horizon": aggregated,
        "seed_count": len(seeds),
        "seeds": seeds,
        "sample_count": sample_count,
    }


def _era5_summary() -> Dict[str, Any]:
    alignment = _read_json(EXPERIMENT_ROOT / "alignment_summary.json")
    by_year = alignment.get("by_track_year", {})
    years = sorted(
        int(year)
        for year, value in by_year.items()
        if isinstance(value, dict) and int(value.get("paired_points", 0) or 0) > 0
    )
    return {
        "source": "ERA5 pressure-level reanalysis",
        "levels_hpa": alignment.get("levels_hPa", [500, 850]),
        "variables": alignment.get("variables", ["u", "v"]),
        "track_points": alignment.get("track_points"),
        "paired_points": alignment.get("paired_points"),
        "paired_point_fraction": alignment.get("paired_point_fraction"),
        "years": years,
        "features": alignment.get("features", ["u500", "v500", "u850", "v850", "era5_age_hours"]),
        "sample_type": "沿台风中心轨迹匹配的样本，不是完整区域网格",
    }


def _official_forecast_summary() -> Dict[str, Any]:
    audit = _read_json(OPERATIONAL_AUDIT_PATH)
    counts = audit.get("counts", {})
    comparable = int(counts.get("comparable_positions", 0) or 0)
    if not audit:
        return {
            "status": "unavailable",
            "reason": "官方预报审计文件不存在",
            "comparable_positions": 0,
        }
    return {
        "status": "ready" if comparable > 0 else "unavailable",
        "reason": None if comparable > 0 else "已完成审计，但没有同时具备官方预报位置与最佳路径验证的公平可比样本",
        "comparable_positions": comparable,
        "test_forecast_origins": counts.get("test_forecast_origins"),
        "test_forecast_positions": counts.get("test_forecast_positions"),
        "source_labels": audit.get("all_forecast_positions_by_source_label", {}),
        "supported_model_leads_hours": audit.get("comparison_policy", {}).get("supported_model_leads_hours", []),
        "source": str(OPERATIONAL_AUDIT_PATH),
    }


def _track_uncertainty_summary() -> Dict[str, Any]:
    raw = _read_json(PREDICTION_UNCERTAINTY_PATH)
    if not raw:
        return {"status": "unavailable", "reason": "在线轨迹 CNN 的历史校准文件不存在"}
    calibration = raw.get("calibration", {})
    evaluation = raw.get("evaluation", {})
    return {
        "status": "ready",
        "model_key": "track_only",
        "model_version": raw.get("model_version"),
        "method": calibration.get("method"),
        "target_coverage": calibration.get("target_coverage"),
        "calibration_storms": calibration.get("storm_count"),
        "evaluation_storms": evaluation.get("storm_count"),
        "evaluation_windows": evaluation.get("window_count"),
        "by_horizon": evaluation.get("by_horizon", []),
        "source": str(PREDICTION_UNCERTAINTY_PATH),
        "interpretation": raw.get("interpretation"),
    }


def _experiment_summary() -> Dict[str, Any]:
    metrics = _read_json(EXPERIMENT_METRICS_PATH)
    all_runs = _experiment_runs("ablation_layers") + _experiment_runs("ablation")
    if not metrics and not all_runs:
        return {"status": "unavailable", "reason": "experiment metrics file not found", "models": []}

    models: List[Dict[str, Any]] = []
    labels = {
        "persistence": "Persistence",
        "constant_velocity": "匀速外推",
        "track_only": "仅轨迹 CNN",
        "fusion_500_850": "轨迹 + ERA5 500/850",
        "fusion_850_only": "轨迹 + ERA5 850",
        "fusion_500_only": "轨迹 + ERA5 500",
    }
    model_groups = {
        "persistence": ("baseline", "ablation_layers", "基于固定台风分组测试集的持续性基线"),
        "constant_velocity": ("baseline", "ablation_layers", "基于最近运动方向和速度的匀速外推基线"),
        "track_only": ("track", "ablation", "仅使用轨迹特征的残差 CNN"),
        "fusion_500_only": ("fusion", "ablation_layers", "轨迹与 500 hPa u/v 风场融合"),
        "fusion_850_only": ("fusion", "ablation_layers", "轨迹与 850 hPa u/v 风场融合"),
        "fusion_500_850": ("fusion", "ablation_layers", "轨迹与 500/850 hPa u/v 风场融合"),
    }
    for key in ("persistence", "constant_velocity", "track_only", "fusion_500_only", "fusion_850_only", "fusion_500_850"):
        kind, group, feature_set = model_groups[key]
        runs = _metric_runs(key, group)
        aggregate = _aggregate_metric_runs(runs, key)
        if not aggregate["by_horizon"]:
            continue
        models.append({
            "key": key,
            "name": labels[key],
            "kind": kind,
            "feature_set": feature_set,
            **aggregate,
        })

    reference_metrics = metrics or (all_runs[0]["metrics"] if all_runs else {})
    manifest = _read_json(MANIFEST_PATH)
    preprocessing = manifest.get("preprocessing", {})
    source = manifest.get("source", {})
    limitations = [
        item for item in reference_metrics.get("limitations", [])
        if not str(item).lower().startswith("single random seed")
    ]
    limitations.extend([
        "当前图表已聚合 3 个随机种子；仍应结合台风级 bootstrap 结果报告跨个例稳定性。",
        "当前保存的指标没有 ADE/FDE、强台风/转向/登陆分组结果，因此界面不展示这些未验证字段。",
        "当前实验只覆盖 6/12/18/24/30/36 小时；没有 48/72 小时实验结果。",
        "官方预报审计的公平可比样本为 0，不能据此声称 CNN 优于官方预报。",
        "当前没有独立的物理约束消融结果；残差 CNN 中的匀速运动先验不作为单独实验行展示。",
    ])
    unique_limitations = list(dict.fromkeys(limitations))
    return {
        "status": "ready",
        "source": str(EXPERIMENT_ROOT),
        "source_files": [run["path"] for run in all_runs],
        "device": reference_metrics.get("device"),
        "test_samples": reference_metrics.get("paired_counts", {}).get("test"),
        "test_storms": reference_metrics.get("paired_storms", {}).get("test"),
        "available_horizons": sorted({
            item["lead_hours"]
            for model in models
            for item in model["by_horizon"]
            if isinstance(item.get("lead_hours"), int)
        }),
        "levels_hpa": [500, 850],
        "models": models,
        "uncertainty": _track_uncertainty_summary(),
        "official_forecast": _official_forecast_summary(),
        "era5": _era5_summary(),
        "split": {
            "rule": "按台风编号/年份划分，不随机切分相邻轨迹点",
            "train_years": preprocessing.get("source_track_splits", {}).get("train", [1945, 2016]),
            "validation_years": preprocessing.get("source_track_splits", {}).get("validation", [2017, 2019]),
            "test_years": preprocessing.get("source_track_splits", {}).get("test", [2020, 2025]),
            "test_storms": preprocessing.get("split_track_file_storm_counts", {}).get("test", reference_metrics.get("paired_storms", {}).get("test")),
        },
        "dataset": {
            "track_years": [manifest.get("coverage", {}).get("year_min"), manifest.get("coverage", {}).get("year_max")],
            "valid_tracks": manifest.get("coverage", {}).get("valid_tracks"),
            "track_points": manifest.get("coverage", {}).get("point_count"),
            "fingerprint_sha256": source.get("fingerprint_sha256", reference_metrics.get("dataset_fingerprint_sha256")),
        },
        "physics_constraint": {
            "status": "unavailable",
            "reason": "没有独立物理约束实验结果，未加入模型对比图",
        },
        "limitations": unique_limitations,
    }


@app.get("/api/experiments/summary")
def get_experiment_summary() -> Dict[str, Any]:
    return _experiment_summary()


@app.get("/api/{legacy_key}.json")
def get_legacy_resource(legacy_key: str):
    """Serve both legacy year lists and legacy typhoon details without route ambiguity."""
    if len(legacy_key) == 4 and legacy_key.isdigit():
        year = int(legacy_key)
        if year not in repository.years():
            raise HTTPException(status_code=404, detail="year data not found")
        return repository.list_typhoons(year, limit=200)
    # The original bundled Cesium page reads the first element of this response.
    return [get_typhoon(legacy_key)]


@app.post("/api/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest) -> dict:
    if not model_service.ready:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_NOT_READY",
                "message": "prediction is unavailable until a compatible checkpoint and PyTorch runtime are available",
                "model": model_service.status(),
            },
        )
    try:
        result = model_service.predict(
            [point.dict() for point in request.history],
            request.horizons_hours,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {**result, "typhoon_id": request.typhoon_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
