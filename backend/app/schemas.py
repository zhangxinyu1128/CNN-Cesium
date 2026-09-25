"""Stable request and response models shared by the API endpoints."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, validator


class LandingRecord(BaseModel):
    position: Optional[str] = None
    land_time: Optional[str] = None
    lng: Optional[float] = None
    lat: Optional[float] = None

    class Config:
        extra = "allow"


class TyphoonPoint(BaseModel):
    time: str
    lng: float
    lat: float
    strong: Optional[str] = None
    power: Optional[float] = None
    speed: Optional[float] = None
    pressure: Optional[float] = None
    move_dir: Optional[float] = None
    move_speed: Optional[float] = None
    radius7: Optional[float] = None
    radius10: Optional[float] = None
    remark: Optional[str] = None
    forecast: Optional[object] = None

    class Config:
        extra = "allow"


class TyphoonIndexItem(BaseModel):
    tfbh: str
    ident: Optional[str] = None
    name: Optional[str] = None
    ename: Optional[str] = None
    is_current: Optional[int] = None
    begin_time: Optional[str] = None
    end_time: Optional[str] = None
    land: List[LandingRecord] = Field(default_factory=list)

    class Config:
        extra = "allow"


class TyphoonDetail(TyphoonIndexItem):
    points: List[TyphoonPoint] = Field(default_factory=list)


class PredictionHistoryPoint(BaseModel):
    time: str
    lng: float
    lat: float
    speed: Optional[float] = None
    power: Optional[float] = None
    pressure: Optional[float] = None


class PredictionRequest(BaseModel):
    typhoon_id: Optional[str] = None
    history: List[PredictionHistoryPoint] = Field(..., min_items=5, max_items=5)
    horizons_hours: List[int] = Field(
        default_factory=lambda: [6, 12, 18, 24, 30, 36],
        min_items=1,
        max_items=6,
    )

    @validator("horizons_hours")
    def validate_horizons(cls, value: List[int]) -> List[int]:
        supported = {6, 12, 18, 24, 30, 36}
        if any(item not in supported for item in value):
            raise ValueError("horizons_hours must be selected from 6, 12, 18, 24, 30, and 36")
        if value != sorted(set(value)):
            raise ValueError("horizons_hours must be strictly increasing")
        return value

    @validator("history")
    def validate_history(cls, value: List[PredictionHistoryPoint]) -> List[PredictionHistoryPoint]:
        previous_time = None
        for point in value:
            if not -180 <= point.lng <= 360:
                raise ValueError("history longitude must be between -180 and 360 degrees")
            if not -90 <= point.lat <= 90:
                raise ValueError("history latitude must be between -90 and 90 degrees")
            try:
                point_time = datetime.fromisoformat(point.time.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("history time must be ISO-8601") from error
            if point_time.utcoffset() is not None:
                point_time = point_time.astimezone(timezone.utc).replace(tzinfo=None)
            if previous_time is not None and point_time - previous_time != timedelta(hours=6):
                raise ValueError("history observations must be exactly six hours apart")
            previous_time = point_time
        return value


class PredictionInterval(BaseModel):
    lng: float
    lat: float


class PredictionPoint(BaseModel):
    lead_hours: int
    lng: float
    lat: float
    speed_ms: Optional[float] = None
    p05: Optional[PredictionInterval] = None
    p95: Optional[PredictionInterval] = None


class PredictionResponse(BaseModel):
    model_version: str
    generated_at: str
    typhoon_id: Optional[str] = None
    input_window: int
    horizons_hours: List[int]
    predictions: List[PredictionPoint]


class ModelStatus(BaseModel):
    status: str
    path: str
    exists: bool
    size_bytes: int
    model_version: Optional[str] = None
    device: Optional[str] = None
    checkpoint_sha256: Optional[str] = None
    reason: Optional[str] = None


class DataStatus(BaseModel):
    status: str
    root: str
    year_files: int
    typhoon_files: int
    empty_typhoon_files: int


class HealthResponse(BaseModel):
    status: str
    service: str
    data: DataStatus
    model: ModelStatus
