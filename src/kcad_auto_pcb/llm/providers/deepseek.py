from __future__ import annotations
from typing import Optional, Dict, List
from ..base import AbstractLLMBackend, LLMMessage, LLMResponse


class DeepSeekBackend(AbstractLLMBackend):
    """DeepSeek uses an OpenAI-compatible API."""

    def __init__(self, model: str = "deepseek-chat", api_key: Optional[str] = None,
                 base_url: str = "https://api.deepseek.com", **kwargs):
        import openai
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            timeout=30.0, max_retries=0, **kwargs
        )

    @property
    def provider_name(self) -> str: return "deepseek"
    @property
    def model_name(self) -> str: return self._model
    @property
    def supports_images(self) -> bool:
        return "vision" in self._model.lower()

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
            api_messages.append(msg)

        kwargs = dict(
            model=self._model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format

        try:
            resp = await self._client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            return LLMResponse(
                text=choice.message.content or "",
                tokens_used=resp.usage.total_tokens if resp.usage else 0,
                model=resp.model,
            )
        except Exception as e:
            return LLMResponse(
                text=f"LLM_ERROR: {e}",
                tokens_used=0,
                model=self._model,
            )
