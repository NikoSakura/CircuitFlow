from __future__ import annotations
from typing import Optional, Dict, List
import json
import httpx
from ..base import AbstractLLMBackend, LLMMessage, LLMResponse


class OllamaBackend(AbstractLLMBackend):
    """Local Ollama backend via REST API."""

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", **kwargs):
        self._model = model
        self._base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str: return "ollama"
    @property
    def model_name(self) -> str: return self._model
    @property
    def supports_images(self) -> bool: return True

    def token_count(self, text: str) -> int:
        return len(text) // 4

    async def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict] = None,
    ) -> LLMResponse:
        api_messages = []
        for m in messages:
            msg: dict = {"role": m.role, "content": m.content}
            if m.images:
                import base64
                msg["images"] = [base64.b64encode(img).decode() for img in m.images]
            api_messages.append(msg)

        body = {
            "model": self._model,
            "messages": api_messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if response_format and response_format.get("type") == "json_object":
            body["format"] = "json"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()

        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            tokens_used=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
            model=data.get("model", self._model),
        )
