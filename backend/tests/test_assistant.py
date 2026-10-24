import os
import unittest

from backend.app.services.assistant import answer_question


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.provider = os.environ.pop("TC_ASSISTANT_PROVIDER", None)
        self.api_key = os.environ.pop("DEEPSEEK_API_KEY", None)

    def tearDown(self):
        if self.provider is not None:
            os.environ["TC_ASSISTANT_PROVIDER"] = self.provider
        if self.api_key is not None:
            os.environ["DEEPSEEK_API_KEY"] = self.api_key

    def test_local_answer_is_grounded_and_keeps_model_boundary(self):
        result = answer_question(
            "当前在线模型使用了 ERA5 吗？",
            {
                "selected_typhoon": {"name": "测试台风"},
                "forecast": {"lead_hours": 24, "location_radius_90_km": 622.6},
            },
        )
        self.assertEqual(result["mode"], "local_grounded")
        self.assertIn("多尺度因果残差 CNN", result["answer"])
        self.assertIn("没有作为 /api/predict 的输入", result["answer"])
        self.assertGreaterEqual(len(result["citations"]), 2)

    def test_context_is_used_for_prediction_explanation(self):
        result = answer_question(
            "当前台风的预测结果如何解释？",
            {
                "selected_typhoon": {"name": "测试台风"},
                "current_point": {"time": "2025-08-01T00:00:00", "lng": 130.2, "lat": 20.4},
                "forecast": {
                    "lead_hours": 24,
                    "lng": 132.1,
                    "lat": 21.2,
                    "location_radius_90_km": 622.6,
                },
            },
        )
        self.assertIn("测试台风", result["answer"])
        self.assertIn("24 小时预测", result["answer"])
        self.assertIn("623 km", result["answer"])


if __name__ == "__main__":
    unittest.main()
