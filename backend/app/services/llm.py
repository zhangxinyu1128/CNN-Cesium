"""DeepSeek chat client using only the Python standard library."""

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List


class DeepSeekClient:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        try:
            self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "10"))
        except ValueError:
            self.timeout = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "max_tokens": 240,
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, socket.timeout) as error:
            raise RuntimeError(f"DeepSeek request failed: {error}") from error

        try:
            data = json.loads(raw)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as error:
            raise RuntimeError(f"DeepSeek response parse failed: {error}") from error
        return str(content)
