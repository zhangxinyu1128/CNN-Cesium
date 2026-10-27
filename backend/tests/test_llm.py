"""Tests for the DeepSeek LLM enhancement with graceful rule-based fallback."""

import unittest
from unittest import mock

from backend.app.main import assistant_query, deepseek_client
from backend.app.schemas import AssistantQueryRequest


class DeepSeekAssistantTests(unittest.TestCase):
    def _request(self, query: str, context=None):
        return AssistantQueryRequest(query=query, context=context if context is not None else {})

    def test_llm_branch_embeds_cards_and_keeps_card_sources(self):
        captured = {}

        def fake_chat(messages):
            captured["messages"] = messages
            return "固定 LLM 回答"

        with mock.patch.object(deepseek_client, "api_key", "test-key"), \
                mock.patch.object(deepseek_client, "chat", side_effect=fake_chat):
            result = assistant_query(self._request("在线预测模型是什么"))

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["answer"], "固定 LLM 回答")

        user_content = captured["messages"][1]["content"]
        self.assertIn("track_cnn_physics_multiscale.pth", user_content)
        self.assertIn("在线预测模型是什么", user_content)

        self.assertTrue(result["sources"])
        for source in result["sources"]:
            self.assertIn("path", source)
            self.assertNotEqual(source["path"], "固定 LLM 回答")

        self.assertTrue(any("ERA5 离线融合实验" in caveat for caveat in result["caveats"]))

    def test_disabled_when_no_api_key_uses_rules(self):
        with mock.patch.object(deepseek_client, "api_key", ""):
            result = assistant_query(self._request("在线预测模型是什么"))

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_card_ids"][0], "online_model")
        self.assertIn("不把 ERA5 作为输入", result["answer"])

    def test_chat_error_falls_back_to_rules(self):
        def boom(messages):
            raise RuntimeError("LLM 挂了")

        with mock.patch.object(deepseek_client, "api_key", "test-key"), \
                mock.patch.object(deepseek_client, "chat", side_effect=boom):
            result = assistant_query(self._request("在线预测模型是什么"))

        self.assertEqual(result["status"], "matched")
        self.assertIn("不把 ERA5 作为输入", result["answer"])
        self.assertEqual(result["matched_card_ids"][0], "online_model")


if __name__ == "__main__":
    unittest.main()
