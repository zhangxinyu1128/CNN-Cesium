"""Read and normalize the local typhoon JSON dataset."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


TYPHOON_ID_PATTERN = re.compile(r"^\d{4,8}$")


def _first_value(source: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in source:
            return source[name]
    return None


def _clean_number(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_landing(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    normalized["land_time"] = _first_value(item, "land_time", "landTime")
    normalized["lng"] = _clean_number(_first_value(item, "lng", "lon", "longitude"))
    normalized["lat"] = _clean_number(_first_value(item, "lat", "latitude"))
    return normalized


def _normalize_point(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    normalized["time"] = _first_value(item, "time", "timestamp", "datetime")
    normalized["lng"] = _clean_number(_first_value(item, "lng", "lon", "longitude"))
    normalized["lat"] = _clean_number(_first_value(item, "lat", "latitude"))
    for field in (
        "power",
        "speed",
        "pressure",
        "move_dir",
        "move_speed",
        "radius7",
        "radius10",
    ):
        normalized[field] = _clean_number(
            _first_value(item, field, _camel_case(field))
        )
    return normalized


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _normalize_meta(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    normalized["tfbh"] = str(_first_value(item, "tfbh", "id", "ident") or "")
    normalized["ident"] = _first_value(item, "ident", "identifier")
    normalized["name"] = _first_value(item, "name", "cname")
    normalized["ename"] = _first_value(item, "ename", "english_name")
    normalized["is_current"] = _clean_number(
        _first_value(item, "is_current", "isCurrent")
    )
    normalized["begin_time"] = _first_value(item, "begin_time", "beginTime")
    normalized["end_time"] = _first_value(item, "end_time", "endTime")
    land = _first_value(item, "land", "landings")
    normalized["land"] = [_normalize_landing(entry) for entry in land] if isinstance(land, list) else []
    return normalized


class DataRepository:
    """File-backed repository that keeps the large dataset out of the bundle."""

    def __init__(self, data_root: Path) -> None:
        self.root = data_root.resolve()
        self.year_root = self.root / "year"
        self.typhoon_root = self.root / "typhoon"

    def years(self) -> List[int]:
        if not self.year_root.exists():
            return []
        return sorted(
            [
                int(path.stem)
                for path in self.year_root.glob("*.json")
                if re.fullmatch(r"\d{4}", path.stem)
            ],
            reverse=True,
        )

    def list_typhoons(
        self,
        year: int,
        query: str = "",
        landing_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        source = self.year_root / (str(year) + ".json")
        records = self._read_json(source)
        if not isinstance(records, list):
            return []

        normalized_query = query.strip().lower()
        result: List[Dict[str, Any]] = []
        for record in records:
            item = _normalize_meta(record)
            searchable = " ".join(
                str(item.get(field) or "") for field in ("tfbh", "name", "ename")
            ).lower()
            if normalized_query and normalized_query not in searchable:
                continue
            if landing_only and not item["land"]:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def get_typhoon(self, tfbh: str) -> Optional[Dict[str, Any]]:
        if not TYPHOON_ID_PATTERN.fullmatch(tfbh):
            return None
        source = self.typhoon_root / (tfbh + ".json")
        records = self._read_json(source)
        if not isinstance(records, list) or not records:
            return None
        record = next((item for item in records if isinstance(item, dict)), None)
        if record is None:
            return None
        normalized = _normalize_meta(record)
        points = record.get("points")
        normalized["points"] = [_normalize_point(item) for item in points] if isinstance(points, list) else []
        return normalized

    def data_status(self) -> Dict[str, Any]:
        year_files = len(list(self.year_root.glob("*.json"))) if self.year_root.exists() else 0
        typhoon_paths = list(self.typhoon_root.glob("*.json")) if self.typhoon_root.exists() else []
        empty_count = sum(1 for path in typhoon_paths if path.stat().st_size <= 2)
        return {
            "status": "ready" if year_files and typhoon_paths else "missing",
            "root": str(self.root),
            "year_files": year_files,
            "typhoon_files": len(typhoon_paths),
            "empty_typhoon_files": empty_count,
        }

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
