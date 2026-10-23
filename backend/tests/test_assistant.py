"""Tests for the knowledge-base assistant service and its request schema."""

import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.schemas import AssistantQueryRequest
from backend.app.services.knowledge import AssistantService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARDS_PATH = PROJECT_ROOT / "backend" / "app" / "knowledge" / "cards.json"


class AssistantServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = AssistantService(CARDS_PATH)

    def test_load_cards_reads_eight_unique_cards(self):
        cards = self.service.load_cards(CARDS_PATH)
        self.assertEqual(len(cards), 8)
        ids = [card["id"] for card in cards]
        self.assertEqual(len(ids), len(set(ids)))

    def test_official_forecast_query_matches_with_sources(self):
        result = self.service.query("官方预报对比")
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["sources"])

    def test_online_model_query_matches_without_era5(self):
        result = self.service.query("在线预测模型是什么")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_card_ids"][0], "online_model")
        self.assertNotIn("ERA5", result["answer"])

    def test_era5_fusion_query_matches_era5_fusion_card(self):
        result = self.service.query("ERA5 融合有效吗")
        self.assertIn("era5_fusion", result["matched_card_ids"])

    def test_dynamic_position_query_returns_coordinates(self):
        context = {"forecast_position": {"lng": 130.5, "lat": 20.1}}
        result = self.service.query("当前预测位置", context)
        self.assertEqual(result["status"], "dynamic")
        self.assertIn("130.5", result["answer"])
        self.assertIn("20.1", result["answer"])

    def test_uncertainty_query_keeps_not_disaster_caveat(self):
        result = self.service.query("误差范围口径")
        self.assertEqual(result["matched_card_ids"][0], "uncertainty")
        self.assertTrue(any("不是灾害概率" in caveat for caveat in result["caveats"]))

    def test_unknown_query_falls_back_without_raising(self):
        result = self.service.query("xyz乱词")
        self.assertEqual(result["status"], "fallback")

    def test_empty_query_triggers_validation_error(self):
        with self.assertRaises(ValidationError):
            AssistantQueryRequest(query="   ")


if __name__ == "__main__":
    unittest.main()
