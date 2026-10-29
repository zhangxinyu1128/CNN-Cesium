import unittest
from datetime import datetime, timedelta

from backend.app.services.assistant_features import AssistantFeatureService
from backend.app.main import assistant_query
from backend.app.schemas import AssistantAnswer, AssistantQueryRequest


class FakeRepository:
    def __init__(self, tracks):
        self.tracks = tracks

    def iter_typhoons(self):
        yield from self.tracks


def make_track(identifier, name, start, points):
    return {
        "tfbh": identifier,
        "name": name,
        "points": [
            {
                "time": (start + timedelta(hours=6 * index)).isoformat(timespec="seconds"),
                "lng": lng,
                "lat": lat,
                "power": power,
                "strong": "台风(TY)",
            }
            for index, (lng, lat, power) in enumerate(points)
        ],
    }


class AssistantFeatureTests(unittest.TestCase):
    def test_similar_case_uses_observed_prefix_and_returns_reference_path(self):
        observed_start = datetime(2025, 7, 1)
        observed = [
            {"time": (observed_start + timedelta(hours=6 * index)).isoformat(), "lng": 120 + index, "lat": 15 + index * 0.5, "power": 10}
            for index in range(5)
        ]
        historical = make_track(
            "201801",
            "ANALOG",
            datetime(2018, 8, 3),
            [(120 + index + 0.05, 15 + index * 0.5, 10) for index in range(5)]
            + [(125 + index, 17.5 + index * 0.4, 11) for index in range(1, 13)],
        )
        service = AssistantFeatureService(FakeRepository([historical]))

        result = service.answer("找相似历史台风", {
            "typhoon_id": "202501",
            "observed_points": observed,
        })

        self.assertEqual(result["feature_card_id"], "similar_typhoons")
        self.assertEqual(result["historical_cases"][0]["tfbh"], "201801")
        self.assertLess(result["historical_cases"][0]["mean_distance_km"], 10)
        self.assertGreater(len(result["historical_cases"][0]["points"]), 5)
        self.assertIn("不是当前台风预测", result["answer"])

    def test_natural_similarity_question_routes_to_historical_case_search(self):
        service = AssistantFeatureService(FakeRepository([]))
        result = service.answer("找几个和当前台风路径相似的历史台风", {
            "observed_points": [{"time": (datetime(2025, 7, 1) + timedelta(hours=6 * index)).isoformat(), "lng": 120 + index, "lat": 15} for index in range(4)]
        })
        self.assertEqual(result["feature_card_id"], "similar_typhoons")

    def test_similar_case_requires_enough_observations(self):
        service = AssistantFeatureService(FakeRepository([]))
        result = service.answer("匹配历史案例", {"observed_points": [{"lng": 120, "lat": 20}]})
        self.assertIn("至少 4 个", result["answer"])
        self.assertEqual(result["historical_cases"], [])

    def test_city_distance_uses_forecast_track_and_disclaims_local_impact(self):
        service = AssistantFeatureService()
        result = service.answer("我是南京居民，当前台风会影响生活吗", {
            "forecast_points": [
                {"lead_hours": 6, "lng": 121.0, "lat": 31.0, "location_radius_90_km": 100},
                {"lead_hours": 12, "lng": 118.7969, "lat": 30.0603, "location_radius_90_km": 200},
            ]
        })
        self.assertIn("+12 小时", result["answer"])
        self.assertIn("历史位置校准半径", result["answer"])
        self.assertIn("不能据此判断日常生活", result["answer"])

    def test_assistant_api_returns_a_valid_city_answer(self):
        request = AssistantQueryRequest(
            query="我是南京居民，台风路径会影响生活吗",
            context={"forecast_points": [{"lead_hours": 12, "lng": 120.0, "lat": 31.0}]},
        )
        response = AssistantAnswer(**assistant_query(request))
        self.assertEqual(response.status, "dynamic")
        self.assertEqual(response.feature_card_id, "city_proximity")
        self.assertTrue(response.sources)

    def test_timeline_only_lists_available_forecast_leads(self):
        service = AssistantFeatureService()
        result = service.answer("请整理当前预测时间轴", {
            "forecast_points": [
                {"lead_hours": 6, "lng": 130.0, "lat": 20.0},
                {"lead_hours": 24, "lng": 132.0, "lat": 21.0},
            ]
        })
        self.assertIn("+6 h", result["answer"])
        self.assertIn("+24 h", result["answer"])
        self.assertNotIn("+12 h", result["answer"])
        self.assertIn("不是逐小时", result["answer"])

    def test_term_explanation_does_not_infer_city_damage_from_center_intensity(self):
        service = AssistantFeatureService()
        result = service.answer("中心气压950hPa、最大风力14级是什么意思", {})
        self.assertIn("14 级（强台风等级）", result["answer"])
        self.assertIn("950 hPa", result["answer"])
        self.assertIn("不能换算成某城市", result["answer"])

    def test_defense_guidance_defers_decisions_to_local_authorities(self):
        service = AssistantFeatureService()
        result = service.answer("台风来时有哪些防御建议", {})
        self.assertIn("一般准备建议", result["answer"])
        self.assertIn("不能生成停课停工", result["answer"])


if __name__ == "__main__":
    unittest.main()
