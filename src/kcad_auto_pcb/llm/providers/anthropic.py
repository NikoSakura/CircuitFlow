from __future__ import annotations
from typing import Optional, Dict, List
from ..base import AbstractLLMBackend, LLMMessage, LLMResponse


class AnthropicBackend(AbstractLLMBackend):
    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: Optional[str] = None, **kwargs):
        import anthropic
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=0, **kwargs)

    @property
    def provider_name(self) -> str: return "anthropic"
    @property
    def model_name(self) -> str: return self._model
    @property
    def supports_images(self) -> bool: return True

    def token_count(self, text: str) -> int:
        try:
            return self._client.count_tokens(text)
        except Exception:
            return len(text) // 4

    async def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict] = None,
    ) -> LLMResponse:
        system_msg = ""
        api_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                content: list = [{"type": "text", "text": m.content}]
                if m.images:
                    import base64
                    for img in m.images:
                        content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(img).decode(),
                            }
                        })
                api_messages.append({"role": m.role, "content": content})

        kwargs = dict(
            model=self._model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if system_msg:
            kwargs["system"] = system_msg

        try:
            resp = await self._client.messages.create(**kwargs)
            return LLMResponse(
                text=resp.content[0].text if resp.content else "",
                tokens_used=resp.usage.input_tokens + resp.usage.output_tokens,
                model=resp.model,
            )
        except Exception as e:
            return LLMResponse(
                text=f"LLM_ERROR: {e}",
                tokens_used=0,
                model=self._model,
            )
