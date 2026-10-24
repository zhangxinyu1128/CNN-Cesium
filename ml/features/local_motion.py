"""Local east-north motion coordinates for short-range typhoon track windows."""

import math
from typing import Any, Dict, Sequence, Tuple


EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE = math.pi * EARTH_RADIUS_KM / 180.0
FEATURE_NAMES = (
    "east_km",
    "north_km",
    "speed",
    "power",
    "acceleration_ms_per_hour",
    "turn_rate_rad_per_hour",
    "heading_sin",
    "heading_cos",
)
TARGET_NAMES = ("east_km", "north_km", "speed")


def wrap_longitude_delta(delta_degrees: float) -> float:
    """Return the shortest signed longitude difference in degrees."""
    return (delta_degrees + 180.0) % 360.0 - 180.0


def displacement_km(origin: Dict[str, Any], point: Dict[str, Any]) -> Tuple[float, float]:
    """Convert a geographic step to east/north components along a great circle."""
    lon1, lat1 = math.radians(float(origin["lng"])), math.radians(float(origin["lat"]))
    lon2, lat2 = math.radians(float(point["lng"])), math.radians(float(point["lat"]))
    dlon = (lon2 - lon1 + math.pi) % (2.0 * math.pi) - math.pi
    dlat = lat2 - lat1
    haversine = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    distance = 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))
    bearing = math.atan2(
        math.sin(dlon) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon),
    )
    return distance * math.sin(bearing), distance * math.cos(bearing)


def destination_lonlat(origin: Dict[str, Any], east_km: float, north_km: float) -> Tuple[float, float]:
    """Convert local east/north displacement from an origin to longitude/latitude."""
    distance = math.hypot(east_km, north_km)
    if distance < 1e-12:
        return float(origin["lng"]) % 360.0, float(origin["lat"])
    bearing = math.atan2(east_km, north_km)
    angular_distance = distance / EARTH_RADIUS_KM
    lat1 = math.radians(float(origin["lat"]))
    lon1 = math.radians(float(origin["lng"]))
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lon2) % 360.0, math.degrees(lat2)


def _heading(east_km: float, north_km: float) -> float:
    return math.atan2(east_km, north_km) if math.hypot(east_km, north_km) > 1e-9 else 0.0


def encode_track(points: Sequence[Dict[str, Any]], interval_hours: float = 6.0) -> list:
    """Encode each point after the first as local motion, intensity and turn features."""
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    rows = []
    previous_heading = None
    previous_speed = float(points[0]["speed"]) if points else None
    for previous, point in zip(points, points[1:]):
        east, north = displacement_km(previous, point)
        heading = _heading(east, north)
        speed = float(point["speed"])
        acceleration = 0.0 if previous_speed is None else (speed - previous_speed) / interval_hours
        turn_rate = 0.0
        if previous_heading is not None:
            delta = (heading - previous_heading + math.pi) % (2.0 * math.pi) - math.pi
            turn_rate = delta / interval_hours
        rows.append(
            {
                "time": point["time"],
                "position": {"lng": float(point["lng"]) % 360.0, "lat": float(point["lat"])},
                "features": [
                    east,
                    north,
                    speed,
                    float(point["power"]),
                    acceleration,
                    turn_rate,
                    math.sin(heading),
                    math.cos(heading),
                ],
                "target": [east, north, speed],
            }
        )
        previous_heading, previous_speed = heading, speed
    return rows
