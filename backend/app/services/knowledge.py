"""Knowledge-base assistant service built on cards.json."""

import json
import re
from typing import Any, Dict, List, Optional


class AssistantService:
    """Answers questions by matching knowledge cards and real-time context."""

    def __init__(self, cards_path: Optional[str] = None):
        self.cards: List[Dict[str, Any]] = []
        if cards_path:
            self.cards = self.load_cards(cards_path)

    def load_cards(self, path) -> List[Dict[str, Any]]:
        """Load cards; never raise, return [] on any failure."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [card for card in data if isinstance(card, dict)]

    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        lowered = str(text).lower()
        return re.sub(r"[^\w\s\u4e00-\u9fff]", " ", lowered)

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text)

    @staticmethod
    def _get(obj: Any, name: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    @staticmethod
    def _fmt(value: Any) -> str:
        if value is None:
            return "未知"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _score_cards(self, text: str) -> List[Dict[str, Any]]:
        compact = self._compact(self._normalize(text))
        scored = []
        for card in self.cards:
            title = str(card.get("title", "")).lower()
            score = 0
            if title and self._compact(title) in compact:
                score += 3
            for keyword in card.get("keywords", []):
                keyword_text = str(keyword).lower()
                if keyword_text and self._compact(keyword_text) in compact:
                    score += 1
            weight = card.get("weight")
            if not isinstance(weight, (int, float)):
                weight = 1
            scored.append((score * weight, card))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [card for score, card in scored if score > 0][:2]

    def _dynamic_answer(self, text: str, context: Any) -> Optional[Dict[str, Any]]:
        if context is None:
            return None
        compact = self._compact(self._normalize(text))
        if not compact:
            return None

        typhoon_id = self._get(context, "typhoon_id")
        typhoon_name = self._get(context, "typhoon_name")
        lead_hours = self._get(context, "lead_hours")
        forecast_position = self._get(context, "forecast_position")
        radius = self._get(context, "location_radius_90_km")
        uncertainty_region = self._get(context, "uncertainty_region")
        era5 = self._get(context, "era5")

        pieces: List[str] = []
        use_predict = False
        use_era5 = False

        if typhoon_name and any(key in compact for key in ("当前台风", "台风编号", "名称", "编号")):
            label = str(typhoon_name)
            if typhoon_id:
                label += f"（编号 {typhoon_id}）"
            pieces.append(f"当前台风：{label}")
            use_predict = True

        if forecast_position and any(key in compact for key in ("预测位置", "经纬度", "经度", "纬度")):
            lng = self._get(forecast_position, "lng")
            lat = self._get(forecast_position, "lat")
            pieces.append(f"预测位置：经度 {self._fmt(lng)}、纬度 {self._fmt(lat)}")
            use_predict = True

        if lead_hours is not None and any(key in compact for key in ("时效", "lead")):
            pieces.append(f"预测时效：{self._fmt(lead_hours)} 小时")
            use_predict = True

        if (
            any(key in compact for key in ("误差", "半径", "不确定"))
            and (radius is not None or uncertainty_region is not None)
        ):
            detail_parts = []
            if radius is not None:
                detail_parts.append(f"位置误差半径 {self._fmt(radius)} km")
            if uncertainty_region is not None:
                semi_major = self._get(uncertainty_region, "semi_major_axis_km")
                semi_minor = self._get(uncertainty_region, "semi_minor_axis_km")
                coverage = self._get(uncertainty_region, "coverage")
                detail_parts.append(
                    f"不确定性椭圆半长轴 {self._fmt(semi_major)} km、"
                    f"半短轴 {self._fmt(semi_minor)} km、覆盖率 {self._fmt(coverage)}"
                )
            pieces.append("误差范围：" + "；".join(detail_parts))
            use_predict = True

        if era5 is not None and any(key in compact for key in ("风场", "风速", "风向")):
            level = self._get(era5, "level")
            u = self._get(era5, "u")
            v = self._get(era5, "v")
            era5_speed = self._get(era5, "speed_ms")
            direction = self._get(era5, "direction_deg")
            shear = self._get(era5, "shear")
            parts = []
            if level is not None:
                parts.append(f"层次 {self._fmt(level)} hPa")
            if u is not None and v is not None:
                parts.append(f"u/v = {self._fmt(u)}/{self._fmt(v)}")
            if era5_speed is not None:
                parts.append(f"风速 {self._fmt(era5_speed)} m/s")
            if direction is not None:
                parts.append(f"风向 {self._fmt(direction)}°")
            if shear is not None:
                parts.append(f"切变 {self._fmt(shear)}")
            pieces.append("环境风场：" + "、".join(parts))
            use_era5 = True

        if not pieces:
            return None

        answer = "；".join(pieces) + "（实时上下文）"
        sources: List[Dict[str, Any]] = []
        if use_predict:
            sources.append({"path": "/api/predict"})
        if use_era5:
            era5_id = typhoon_id if typhoon_id else "id"
            sources.append({"path": f"/api/typhoons/{era5_id}/era5"})
        if not sources:
            sources = [{"path": "/api/predict"}]
        return {
            "status": "dynamic",
            "answer": answer,
            "sources": sources,
            "matched_card_ids": [],
            "caveats": [],
        }

    def _card_answer(self, top: List[Dict[str, Any]]) -> Dict[str, Any]:
        matched_ids = [str(card.get("id")) for card in top if card.get("id") is not None]
        answer = str(top[0].get("answer", ""))
        sources: List[Dict[str, Any]] = []
        seen_paths = set()
        for card in top:
            for source in card.get("sources", []):
                if not isinstance(source, dict):
                    continue
                path = source.get("path")
                if path is None or path in seen_paths:
                    continue
                seen_paths.add(path)
                item = {"path": str(path)}
                if source.get("section") is not None:
                    item["section"] = str(source["section"])
                sources.append(item)
        caveats: List[str] = []
        for card in top:
            for caveat in card.get("caveats", []):
                if caveat is not None and str(caveat) not in caveats:
                    caveats.append(str(caveat))
        return {
            "status": "matched",
            "answer": answer,
            "sources": sources,
            "matched_card_ids": matched_ids,
            "caveats": caveats,
        }

    def query(self, text: str, context: Any = None) -> Dict[str, Any]:
        if not self.cards:
            return {
                "status": "fallback",
                "answer": "知识库未加载，请检查 backend/app/knowledge/cards.json",
                "sources": [],
                "matched_card_ids": [],
                "caveats": [],
            }

        top = self._score_cards(text)

        wants_live = any(
            key in self._compact(self._normalize(text))
            for key in ("当前", "现在", "实时", "此刻")
        )

        if not wants_live and top:
            return self._card_answer(top)

        if wants_live:
            dynamic = self._dynamic_answer(text, context)
            if dynamic is not None:
                return dynamic

        if top:
            return self._card_answer(top)

        dynamic = self._dynamic_answer(text, context)
        if dynamic is not None:
            return dynamic

        return {
            "status": "fallback",
            "answer": "未找到匹配知识，可尝试：在线预测模型是什么 / ERA5 融合实验 / 官方预报对比 / 误差范围口径",
            "sources": [],
            "matched_card_ids": [],
            "caveats": [],
        }
