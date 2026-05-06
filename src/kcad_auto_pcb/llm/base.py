from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class LLMMessage:
    role: str  # "system", "user", "assistant"
    content: str
    images: Optional[List[bytes]] = None  # base64-encoded for multimodal


@dataclass
class LLMResponse:
    text: str
    tokens_used: int
    model: str
    raw: Dict[str, Any] = field(default_factory=dict)


class AbstractLLMBackend(ABC):
    """Provider-agnostic LLM interface."""

    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict] = None,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def token_count(self, text: str) -> int:
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def supports_images(self) -> bool:
        ...


class LLMBackendFactory:
    """Create backends from configuration string.

    Usage:
        backend = LLMBackendFactory.create("openai:gpt-4o-mini", api_key="...")
        backend = LLMBackendFactory.create("anthropic:claude-sonnet-4-20250514", api_key="...")
        backend = LLMBackendFactory.create("ollama:qwen2.5:7b", base_url="http://localhost:11434")
    """

    @staticmethod
    def create(spec: str, **kwargs) -> AbstractLLMBackend:
        if ":" not in spec:
            raise ValueError(f"Invalid LLM spec: {spec}. Use format 'provider:model'")
        provider, _, model = spec.partition(":")
        kwargs = {k: v for k, v in kwargs.items() if v is not None}

        if provider in ("openai", "openai_compatible"):
            from .providers.openai import OpenAIBackend
            return OpenAIBackend(model=model, **kwargs)
        elif provider == "anthropic":
            from .providers.anthropic import AnthropicBackend
            return AnthropicBackend(model=model, **kwargs)
        elif provider == "deepseek":
            from .providers.deepseek import DeepSeekBackend
            return DeepSeekBackend(model=model, **kwargs)
        elif provider == "ollama":
            from .providers.ollama import OllamaBackend
            return OllamaBackend(model=model, **kwargs)
        raise ValueError(f"Unknown provider: {provider}")
