"""FastAPI entry point for local typhoon data and prediction services."""

import os
from pathlib import Path
from typing import List

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
from .services.model import ModelService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.getenv("TC_DATA_ROOT", str(PROJECT_ROOT / "data")))
CHECKPOINT_PATH = Path(
    os.getenv("TC_MODEL_PATH", str(PROJECT_ROOT / "artifacts" / "checkpoints" / "track_cnn_baseline.pth"))
)
MODEL_VERSION = os.getenv("TC_MODEL_VERSION") or None

repository = DataRepository(DATA_ROOT)
model_service = ModelService(CHECKPOINT_PATH, MODEL_VERSION)

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


@app.get("/api/years.json", response_model=List[int])
def get_years() -> List[int]:
    return repository.years()


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


@app.get("/api/{year}.json", response_model=List[TyphoonIndexItem])
def get_legacy_year(year: int) -> List[dict]:
    """Keep the original dashboard's year-list request compatible."""
    if year not in repository.years():
        raise HTTPException(status_code=404, detail="year data not found")
    return repository.list_typhoons(year, limit=200)


@app.get("/api/{tfbh}.json", response_model=TyphoonDetail)
def get_legacy_typhoon(tfbh: str) -> dict:
    return get_typhoon(tfbh)


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
