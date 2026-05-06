from __future__ import annotations
from typing import Optional, Dict, List
from ..base import AbstractLLMBackend, LLMMessage, LLMResponse


class OpenAIBackend(AbstractLLMBackend):
    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None, **kwargs):
        import openai
        self._model = model
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=30.0, max_retries=0, **kwargs)

    @property
    def provider_name(self) -> str: return "openai"
    @property
    def model_name(self) -> str: return self._model
    @property
    def supports_images(self) -> bool: return True

    def token_count(self, text: str) -> int:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self._model)
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4

    async def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict] = None,
    ) -> LLMResponse:
        import base64
        api_messages = []
        for m in messages:
            if m.images:
                # OpenAI vision format: content is an array of text + image parts
                content = [{"type": "text", "text": m.content}]
                for img in m.images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(img).decode()}"}
                    })
                api_messages.append({"role": m.role, "content": content})
            else:
                api_messages.append({"role": m.role, "content": m.content})

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
