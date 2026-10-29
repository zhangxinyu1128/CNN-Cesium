"""Deterministic, data-bounded analyses for the local assistant."""

from bisect import bisect_left
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Dict, List, Optional, Tuple


CITY_COORDINATES = {
    "南京": (118.7969, 32.0603),
    "上海": (121.4737, 31.2304),
    "杭州": (120.1551, 30.2741),
    "宁波": (121.5503, 29.8746),
    "温州": (120.6994, 27.9943),
    "福州": (119.2965, 26.0745),
    "厦门": (118.0894, 24.4798),
    "泉州": (118.6759, 24.8741),
    "广州": (113.2644, 23.1291),
    "深圳": (114.0579, 22.5431),
    "珠海": (113.5767, 22.2707),
    "海口": (110.1983, 20.0440),
    "三亚": (109.5119, 18.2528),
    "青岛": (120.3826, 36.0671),
    "南通": (120.8943, 31.9802),
    "苏州": (120.5853, 31.2989),
    "武汉": (114.3054, 30.5931),
    "合肥": (117.2272, 31.8206),
    "南昌": (115.8582, 28.6829),
}

SIMILARITY_TERMS = ("相似台风", "相似案例", "历史案例", "类似台风", "找相似", "匹配案例")
TIMELINE_TERMS = ("时间轴", "各时效", "分时效", "路径演变", "逐时效", "未来路径")
DEFENSE_TERMS = ("防御建议", "防台建议", "怎么防御", "如何防御", "准备什么", "避险建议")
CITY_TERMS = ("影响", "距离", "到我", "日常生活", "生活起居", "会不会到", "会影响")
TERM_TERMS = ("什么意思", "怎么理解", "通俗解释", "解释一下", "代表什么", "影响描述", "翻译")


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _haversine_km(first: Dict[str, Any], second: Dict[str, Any]) -> Optional[float]:
    lng1, lat1 = _number(first.get("lng")), _number(first.get("lat"))
    lng2, lat2 = _number(second.get("lng")), _number(second.get("lat"))
    if None in (lng1, lat1, lng2, lat2):
        return None
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = lat2_rad - lat1_rad
    delta_lng = math.radians(lng2 - lng1)
    arc = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(arc), math.sqrt(max(0.0, 1 - arc)))


def _compact_points(items: Any, limit: int = 48) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result = []
    for item in items[-limit:]:
        lng, lat = _number(_get(item, "lng")), _number(_get(item, "lat"))
        if lng is None or lat is None or not -180 <= lng <= 360 or not -90 <= lat <= 90:
            continue
        result.append({
            "time": _get(item, "time"),
            "lng": lng,
            "lat": lat,
            "power": _number(_get(item, "power")),
            "speed": _number(_get(item, "speed")),
            "strong": _get(item, "strong"),
        })
    return result


class AssistantFeatureService:
    """Answers targeted questions with deterministic calculations and caveats."""

    def __init__(self, repository: Any = None) -> None:
        self.repository = repository
        self._track_cache: Optional[List[Dict[str, Any]]] = None

    @staticmethod
    def _city_in_question(question: str) -> Optional[Tuple[str, Tuple[float, float]]]:
        for name, coordinates in CITY_COORDINATES.items():
            if name in question:
                return name, coordinates
        return None

    @staticmethod
    def _forecast_points(context: Any) -> List[Dict[str, Any]]:
        source = _get(context, "forecast_points", [])
        if not isinstance(source, list):
            source = []
        result = []
        for item in source:
            lead = _number(_get(item, "lead_hours"))
            lng, lat = _number(_get(item, "lng")), _number(_get(item, "lat"))
            if lead is None or lng is None or lat is None:
                continue
            result.append({
                "lead_hours": lead,
                "lng": lng,
                "lat": lat,
                "speed_ms": _number(_get(item, "speed_ms")),
                "location_radius_90_km": _number(_get(item, "location_radius_90_km")),
            })
        return sorted(result, key=lambda item: item["lead_hours"])

    @staticmethod
    def _case_payload(card_id: str, answer: str, source_path: str, caveats: List[str], **extra: Any) -> Dict[str, Any]:
        sources = [{"path": "backend/app/knowledge/cards.json", "section": card_id}]
        if source_path != "backend/app/knowledge/cards.json":
            sources.append({"path": source_path})
        return {
            "feature_card_id": card_id,
            "answer": answer,
            "sources": sources,
            "caveats": caveats,
            **extra,
        }

    def _city_distance(self, context: Any, coordinates: Tuple[float, float]) -> Optional[Dict[str, Any]]:
        lng, lat = coordinates
        target = {"lng": lng, "lat": lat}
        candidates = self._forecast_points(context)
        if not candidates:
            point = _get(context, "forecast_position")
            if point is not None:
                candidates = [{
                    "lead_hours": _number(_get(context, "lead_hours")),
                    "lng": _number(_get(point, "lng")),
                    "lat": _number(_get(point, "lat")),
                    "location_radius_90_km": _number(_get(context, "location_radius_90_km")),
                }]
        distances = []
        for item in candidates:
            distance = _haversine_km(target, item)
            if distance is not None:
                distances.append((distance, item))
        if not distances:
            return None
        distance, item = min(distances, key=lambda value: value[0])
        return {"distance_km": distance, **item}

    def _city_answer(self, question: str, context: Any, city: str, coordinates: Tuple[float, float]) -> Dict[str, Any]:
        nearest = self._city_distance(context, coordinates)
        if nearest is None:
            answer = (
                f"目前没有可用的台风预测路径，无法估算预测路径与{city}的距离。"
                "请先选择台风并运行预测；即使得到距离，也只能说明台风中心路径的相对位置。"
            )
        else:
            lead = nearest.get("lead_hours")
            lead_label = f"+{lead:g} 小时" if isinstance(lead, (int, float)) else "当前预测点"
            radius = nearest.get("location_radius_90_km")
            radius_text = f"；该时效的历史位置校准半径约 {radius:.0f} km" if radius is not None else ""
            answer = (
                f"按当前模型路径点计算，台风中心与{city}（{coordinates[0]:.4f}°E，{coordinates[1]:.4f}°N）"
                f"最近约 {nearest['distance_km']:.0f} km，出现在{lead_label}{radius_text}。"
                "这个距离表示预测中心线与城市坐标的球面距离，不表示风圈覆盖或当地灾害等级。"
            )
            if any(term in question for term in DEFENSE_TERMS):
                answer += "建议关注当地气象部门预警，提前规划出行并收好阳台松动物品；是否停课、停工或转移应以当地主管部门通知为准。"
            else:
                answer += "当前项目没有接入当地实时降雨、地面阵风或官方预警，不能据此判断日常生活是否会受影响；请以当地最新预报预警为准。"
        return self._case_payload(
            "city_proximity",
            answer,
            "/api/predict",
            [
                "中心路径距离不等于风圈半径或城市影响范围。",
                "项目未接入城市实时降雨、地面阵风和官方预警数据。",
            ],
        )

    def _timeline_answer(self, context: Any) -> Dict[str, Any]:
        points = self._forecast_points(context)
        if not points:
            return self._case_payload(
                "forecast_timeline",
                "当前没有可用的预测时效。请先选择台风并运行 CNN 预测；本时间轴只整理模型实际输出的离散时效。",
                "/api/predict",
                ["没有逐小时预测或降雨预报数据，不会对离散时效之间进行风险插值。"],
                forecast_timeline=[],
            )
        rows = []
        for point in points:
            row = (
                f"+{point['lead_hours']:g} h：{point['lat']:.2f}°N，{point['lng']:.2f}°E"
            )
            radius = point.get("location_radius_90_km")
            if radius is not None:
                row += f"；历史位置校准半径约 {radius:.0f} km"
            rows.append(row)
        answer = "预测路径时间轴（只列模型实际输出时效）：\n" + "\n".join(rows)
        answer += "\n以上是台风中心位置与历史校准误差参考，不是逐小时城市风险、降雨预报或灾害概率。"
        return self._case_payload(
            "forecast_timeline",
            answer,
            "/api/predict",
            ["当前在线模型输出 6 小时间隔的路径点；未提供城市级降雨和阵风预测。"],
            forecast_timeline=points,
        )

    def _defense_answer(self, context: Any, question: str) -> Dict[str, Any]:
        city_match = self._city_in_question(question)
        nearest = self._city_distance(context, city_match[1]) if city_match else None
        location_text = ""
        if city_match and nearest is not None:
            location_text = (
                f"当前路径点中，台风中心距{city_match[0]}最近约 {nearest['distance_km']:.0f} km"
                f"（+{nearest['lead_hours']:g} h）。"
            )
        answer = location_text + "一般准备建议：持续关注当地气象部门最新预报预警；若官方提示需避险，及时调整出行并远离海岸、河道及临时构筑物；在可能受大风影响时检查门窗、固定阳台松动物品，备好常用药品、饮水和充电设备。"
        answer += "本项目没有城市级风雨影响、道路和人口数据，不能生成停课停工、转移路线或物资调度指令。"
        return self._case_payload(
            "defensive_guidance",
            answer,
            "backend/app/knowledge/cards.json",
            ["建议为通用准备提示，不是官方应急指令；具体措施以当地政府和气象部门发布为准。"],
        )

    @staticmethod
    def _intensity_label(power: Optional[float]) -> Optional[str]:
        if power is None:
            return None
        grade = int(round(power))
        if grade <= 7:
            category = "热带低压等级"
        elif grade <= 9:
            category = "热带风暴等级"
        elif grade <= 11:
            category = "强热带风暴等级"
        elif grade <= 13:
            category = "台风等级"
        elif grade <= 15:
            category = "强台风等级"
        else:
            category = "超强台风等级"
        return f"{grade} 级（{category}）"

    def _term_answer(self, question: str, context: Any) -> Optional[Dict[str, Any]]:
        grade_match = re.search(r"(?<!\d)(\d{1,2})\s*级", question)
        pressure_match = re.search(r"(?<!\d)(\d{3,4}(?:\.\d+)?)\s*(?:hpa|百帕)", question, re.IGNORECASE)
        observation = _get(context, "current_observation")
        grade = int(grade_match.group(1)) if grade_match else _number(_get(observation, "power"))
        pressure = float(pressure_match.group(1)) if pressure_match else _number(_get(observation, "pressure"))
        if grade is None and pressure is None:
            return None

        parts = []
        if grade is not None:
            label = self._intensity_label(grade)
            parts.append(
                f"{label}表示台风中心附近的强度等级；通常意味着中心附近风力较强，户外作业、临时设施和海上活动需要高度关注官方防台安排。"
            )
            parts.append("该等级不是城市各处的实测风力，离中心距离和地形会显著影响当地实际风况。")
        if pressure is not None:
            parts.append(
                f"中心气压 {pressure:g} hPa 是台风中心气压指标；较低中心气压通常与较强系统相关，但单独一个气压值不能换算成某城市的风雨或损害程度。"
            )
        answer = "".join(parts) + "具体影响应结合位置、时效、当地精细化预报和官方预警判断。"
        return self._case_payload(
            "intensity_explainer",
            answer,
            "backend/app/knowledge/cards.json",
            ["中心强度不等于目标城市的实况风力或损害结论。"],
        )

    def _iter_historical_tracks(self) -> List[Dict[str, Any]]:
        if self._track_cache is None:
            tracks = []
            if self.repository is not None:
                for item in self.repository.iter_typhoons():
                    points = _compact_points(item.get("points"), limit=10000)
                    if len(points) >= 4:
                        tracks.append({
                            "tfbh": str(item.get("tfbh") or ""),
                            "name": item.get("name") or item.get("ename") or str(item.get("tfbh") or ""),
                            "points": points,
                            "valid_indexes": [],
                            "times": [],
                            "month_positions": {month: [] for month in range(1, 13)},
                        })
                        track = tracks[-1]
                        for point_index, point in enumerate(points):
                            stamp = _parse_time(point.get("time"))
                            if stamp is None:
                                continue
                            position = len(track["times"])
                            track["valid_indexes"].append(point_index)
                            track["times"].append(stamp)
                            track["month_positions"][stamp.month].append(position)
            self._track_cache = tracks
        return self._track_cache

    @staticmethod
    def _nearest_point_index(times: List[datetime], target: datetime, tolerance_hours: float = 3.1) -> Optional[int]:
        index = bisect_left(times, target)
        candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(times)]
        if not candidates:
            return None
        best = min(candidates, key=lambda candidate: abs((times[candidate] - target).total_seconds()))
        return best if abs((times[best] - target).total_seconds()) <= tolerance_hours * 3600 else None

    @staticmethod
    def _month_distance(first: int, second: int) -> int:
        delta = abs(first - second)
        return min(delta, 12 - delta)

    def _similar_answer(self, context: Any) -> Dict[str, Any]:
        observed = _compact_points(_get(context, "observed_points", []), limit=8)
        observed = [item for item in observed if _parse_time(item.get("time")) is not None]
        if len(observed) < 4:
            return self._case_payload(
                "similar_typhoons",
                "历史相似案例需要至少 4 个连续有效观测点。请在历史回放中推进到有足够观测记录的位置后再查询。",
                "data/typhoon/*.json",
                ["匹配只使用当前已观测轨迹前缀，不使用当前台风未来轨迹。"],
                historical_cases=[],
            )

        observed_times = [_parse_time(item["time"]) for item in observed]
        anchor_time = observed_times[-1]
        offsets = [(stamp - anchor_time).total_seconds() / 3600 for stamp in observed_times]
        selected_id = str(_get(context, "typhoon_id") or "")
        candidates = []

        for track in self._iter_historical_tracks():
            if track["tfbh"] == selected_id:
                continue
            points = track["points"]
            indexes = track["valid_indexes"]
            times = track["times"]
            if len(times) < len(observed):
                continue
            best = None
            candidate_positions = (
                position
                for month in range(1, 13)
                if self._month_distance(anchor_time.month, month) <= 2
                for position in track["month_positions"][month]
            )
            for end_position in candidate_positions:
                end_time = times[end_position]
                month_delta = self._month_distance(anchor_time.month, end_time.month)
                matched_points = []
                matched_indexes = []
                for offset in offsets:
                    match_index = self._nearest_point_index(times, end_time + timedelta(hours=offset))
                    if match_index is None:
                        break
                    matched_indexes.append(indexes[match_index])
                    matched_points.append(points[indexes[match_index]])
                if len(matched_points) != len(observed) or len(set(matched_indexes)) != len(observed):
                    continue
                distances = [
                    distance
                    for first, second in zip(observed, matched_points)
                    if (distance := _haversine_km(first, second)) is not None
                ]
                if not distances:
                    continue
                mean_distance = sum(distances) / len(distances)
                power_deltas = [
                    abs(first["power"] - second["power"])
                    for first, second in zip(observed, matched_points)
                    if first.get("power") is not None and second.get("power") is not None
                ]
                mean_power_delta = sum(power_deltas) / len(power_deltas) if power_deltas else None
                ranking_score = mean_distance + month_delta * 45 + (mean_power_delta or 0) * 20
                if best is None or ranking_score < best["ranking_score"]:
                    best = {
                        "ranking_score": ranking_score,
                        "mean_distance_km": mean_distance,
                        "mean_power_delta": mean_power_delta,
                        "match_time": end_time,
                        "start_time": end_time + timedelta(hours=offsets[0]),
                        "end_index": indexes[end_position],
                        "month_delta": month_delta,
                    }
            if best is None:
                continue
            year = best["match_time"].year
            future_end = best["match_time"] + timedelta(hours=72)
            case_points = [
                {
                    "time": point.get("time"),
                    "lng": point["lng"],
                    "lat": point["lat"],
                    "strong": point.get("strong"),
                }
                for point in points
                if (stamp := _parse_time(point.get("time"))) is not None
                and best["start_time"] <= stamp <= future_end
            ]
            if len(case_points) >= 2:
                candidates.append({
                    "tfbh": track["tfbh"],
                    "name": track["name"],
                    "year": year,
                    "match_time": best["match_time"].isoformat(timespec="hours"),
                    "mean_distance_km": round(best["mean_distance_km"], 1),
                    "mean_power_delta": round(best["mean_power_delta"], 1) if best["mean_power_delta"] is not None else None,
                    "matched_points": len(observed),
                    "points": case_points,
                    "_ranking_score": best["ranking_score"],
                })

        candidates.sort(key=lambda item: item["_ranking_score"])
        best_cases = candidates[:3]
        for item in best_cases:
            item.pop("_ranking_score", None)
        if not best_cases:
            answer = "在相近季节和可匹配时间间隔的历史轨迹中，没有找到足够完整的可比较案例。"
        else:
            rows = [
                f"{case['year']} 年 {case['name']}（{case['tfbh']}）：已观测段平均位置差 {case['mean_distance_km']:.0f} km，匹配 {case['matched_points']} 个点；可查看匹配时点后最多 72 小时的真实历史路径。"
                for case in best_cases
            ]
            answer = "按历史路径空间距离、季节和可用强度等级排序的参考个例：\n" + "\n".join(rows)
            answer += "\n点击个例可在地图上叠加其历史路径。历史后续路径仅作案例对照，不是当前台风预测。"
        return self._case_payload(
            "similar_typhoons",
            answer,
            "data/typhoon/*.json",
            [
                "相似度排序是基于已观测轨迹前缀的启发式匹配，不代表当前台风未来路径。",
                "项目数据不含灾情资料，因此不提供历史灾损对比。",
            ],
            historical_cases=best_cases,
        )

    def answer(self, question: str, context: Any) -> Optional[Dict[str, Any]]:
        lowered = question.lower()
        compact = re.sub(r"\s+", "", lowered)
        city = self._city_in_question(question)
        if city and any(term in compact for term in CITY_TERMS + DEFENSE_TERMS):
            return self._city_answer(question, context, city[0], city[1])
        similarity_intent = any(term in compact for term in SIMILARITY_TERMS) or (
            "相似" in compact and any(term in compact for term in ("历史", "台风", "路径", "案例"))
        )
        if similarity_intent:
            return self._similar_answer(context)
        if any(term in compact for term in TIMELINE_TERMS):
            return self._timeline_answer(context)
        if any(term in compact for term in DEFENSE_TERMS):
            return self._defense_answer(context, question)
        if any(term in compact for term in TERM_TERMS) or re.search(r"\d{3,4}\s*(?:hpa|百帕)|\d{1,2}\s*级", lowered):
            return self._term_answer(question, context)
        return None
