import json
import unittest
from pathlib import Path

from backend.app.services.knowledge import AssistantService


CARDS_PATH = Path(__file__).resolve().parents[1] / "app" / "knowledge" / "cards.json"


class KnowledgeAssistantTests(unittest.TestCase):
    def setUp(self):
        self.service = AssistantService(CARDS_PATH)

    def test_cards_are_unique_and_describe_current_online_model(self):
        with CARDS_PATH.open("r", encoding="utf-8") as handle:
            cards = json.load(handle)

        ids = [card["id"] for card in cards]
        self.assertEqual(len(ids), len(set(ids)))

        result = self.service.query("在线预测模型是什么")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_card_ids"], ["online_model"])
        self.assertIn("track_cnn_physics_multiscale.pth", result["answer"])
        self.assertIn("不把 ERA5 作为输入", result["answer"])

    def test_dynamic_answer_uses_selected_forecast_context(self):
        result = self.service.query(
            "当前预测位置和误差范围是多少",
            {
                "typhoon_id": "202501",
                "typhoon_name": "测试台风",
                "lead_hours": 24,
                "forecast_position": {"lng": 132.5, "lat": 19.25},
                "location_radius_90_km": 300,
            },
        )

        self.assertEqual(result["status"], "dynamic")
        self.assertIn("经度 132.5", result["answer"])
        self.assertIn("纬度 19.25", result["answer"])
        self.assertIn("300 km", result["answer"])
        self.assertTrue(any(source["path"] == "/api/predict" for source in result["sources"]))

    def test_unmatched_question_returns_grounded_fallback(self):
        result = self.service.query("明天我家会下雨吗")

        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["sources"], [])
        self.assertIn("未找到匹配知识", result["answer"])


if __name__ == "__main__":
    unittest.main()
