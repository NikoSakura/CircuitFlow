"""LLM provider configuration management — inspired by AstrBot's provider system.

Two-level design:
  - Provider (提供商): a configured service with API key, base URL, model list
  - The same provider type can have multiple instances (e.g., two OpenAI accounts)

Config persisted to ~/.kcad_auto_pcb/providers.json
"""

from __future__ import annotations
import json
import uuid
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── Provider templates (like AstrBot's config_template) ──────────────

# ── Icons from LobeHub CDN (same source as AstrBot) ──
_ICON = "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons"

PROVIDER_TEMPLATES: Dict[str, dict] = {
    # ── 国际主流 ──
    "openai": {
        "name": "OpenAI",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/openai.svg",
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_doc": "https://platform.openai.com/api-keys",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "default_model": "gpt-4o-mini",
        "requires_key": True,
        "supports_vision": True,
        "supports_model_fetch": True,    # GET /v1/models
        "description": "OpenAI GPT 系列模型，性价比高",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/anthropic.svg",
        "type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_doc": "https://console.anthropic.com/keys",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250514", "claude-opus-4-20250514"],
        "default_model": "claude-sonnet-4-20250514",
        "requires_key": True,
        "supports_vision": True,
        "supports_model_fetch": False,   # Anthropic has no public model list API
        "description": "多模态最强，可识别 PDF/图片原理图",
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/deepseek.svg",
        "type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_doc": "https://platform.deepseek.com/api_keys",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "requires_key": True,
        "supports_vision": False,
        "supports_model_fetch": True,     # OpenAI-compatible /v1/models
        "description": "国产高性价比模型",
    },
    "ollama": {
        "name": "Ollama (本地)",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/ollama.svg",
        "type": "ollama",
        "base_url": "http://localhost:11434",
        "api_doc": "https://ollama.com/download",
        "models": ["llama3.2", "qwen2.5", "gemma3"],
        "default_model": "llama3.2",
        "requires_key": False,
        "supports_vision": True,
        "supports_model_fetch": True,     # GET /api/tags
        "description": "本地运行，无需 API 密钥，需先安装 Ollama",
    },
    "openai_compatible": {
        "name": "OpenAI 兼容接口",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/openai.svg",
        "type": "openai_compatible",
        "base_url": "https://your-api-endpoint.com/v1",
        "api_doc": "",
        "models": [],
        "default_model": "",
        "requires_key": True,
        "supports_vision": False,
        "supports_model_fetch": True,     # If compatible, GET /v1/models should work
        "description": "任何兼容 OpenAI API 的服务（SiliconFlow、Groq、vLLM 等）",
    },
    "siliconflow": {
        "name": "SiliconFlow (硅基流动)",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/siliconcloud.svg",
        "type": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_doc": "https://cloud.siliconflow.cn/",
        "models": ["Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-VL-72B-Instruct"],
        "default_model": "Qwen/Qwen3-235B-A22B",
        "requires_key": True,
        "supports_vision": True,
        "supports_model_fetch": True,
        "description": "硅基流动 — 国产模型聚合平台，Qwen/DeepSeek 等一键调用",
    },
    "zhipu": {
        "name": "智谱 AI (GLM)",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/zhipu.svg",
        "type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_doc": "https://open.bigmodel.cn/",
        "models": ["glm-4-plus", "glm-4v-plus", "glm-4-flash"],
        "default_model": "glm-4-plus",
        "requires_key": True,
        "supports_vision": True,
        "supports_model_fetch": False,
        "description": "智谱 GLM 系列 — 国产大模型，GLM-4V 支持视觉",
    },
    "groq": {
        "name": "Groq",
        "icon": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/groq.svg",
        "type": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "api_doc": "https://console.groq.com/keys",
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"],
        "default_model": "llama-3.3-70b-versatile",
        "requires_key": True,
        "supports_vision": False,
        "supports_model_fetch": True,
        "description": "Groq — LPU 推理，速度极快",
    },
    # ── 国内平台 ──
    "dashscope": {
        "name": "阿里云百炼",
        "icon": f"{_ICON}/alibabacloud-color.svg",
        "type": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_doc": "https://bailian.console.aliyun.com/",
        "models": ["qwen3-omni-flash", "qwen3-vl-plus", "qwen3-vl-flash", "qvq-72b-preview"],
        "default_model": "qwen3-omni-flash",
        "requires_key": True, "supports_vision": True, "supports_model_fetch": True,
        "description": "阿里云百炼 — Qwen-Omni/VL 多模态",
    },
    "zhipu": {
        "name": "智谱 AI (GLM)",
        "icon": f"{_ICON}/zhipu.svg",
        "type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_doc": "https://open.bigmodel.cn/",
        "models": ["glm-4-plus", "glm-4v-plus", "glm-4-flash"],
        "default_model": "glm-4-plus",
        "requires_key": True, "supports_vision": True, "supports_model_fetch": False,
        "description": "智谱 GLM-4 系列，GLM-4V 支持视觉",
    },
    "siliconflow": {
        "name": "SiliconFlow (硅基流动)",
        "icon": f"{_ICON}/siliconcloud.svg",
        "type": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_doc": "https://cloud.siliconflow.cn/",
        "models": ["Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-VL-72B-Instruct"],
        "default_model": "Qwen/Qwen3-235B-A22B",
        "requires_key": True, "supports_vision": True, "supports_model_fetch": True,
        "description": "硅基流动 — 国产模型聚合平台",
    },
    "moonshot": {
        "name": "Moonshot (月之暗面)",
        "icon": f"{_ICON}/kimi.svg",
        "type": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "api_doc": "https://platform.moonshot.cn/",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-32k",
        "requires_key": True, "supports_vision": False, "supports_model_fetch": True,
        "description": "月之暗面 Kimi — 超长上下文",
    },
    "modelscope": {
        "name": "ModelScope (魔搭)",
        "icon": f"{_ICON}/modelscope.svg",
        "type": "openai_compatible",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_doc": "https://modelscope.cn/",
        "models": ["Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-V3"],
        "default_model": "Qwen/Qwen3-235B-A22B",
        "requires_key": True, "supports_vision": False, "supports_model_fetch": True,
        "description": "ModelScope 魔搭社区模型推理",
    },
    "volcengine": {
        "name": "火山引擎 (豆包)",
        "icon": f"{_ICON}/volcengine-color.svg",
        "type": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_doc": "https://console.volcengine.com/ark/",
        "models": ["doubao-pro-32k", "doubao-lite-32k"],
        "default_model": "doubao-pro-32k",
        "requires_key": True, "supports_vision": False, "supports_model_fetch": True,
        "description": "火山引擎 — 豆包大模型",
    },
    # ── 国际平台 ──
    "xai": {
        "name": "xAI (Grok)",
        "icon": f"{_ICON}/xai.svg",
        "type": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_doc": "https://x.ai/api",
        "models": ["grok-3-beta", "grok-2"],
        "default_model": "grok-3-beta",
        "requires_key": True, "supports_vision": True, "supports_model_fetch": True,
        "description": "xAI Grok 系列 — 马斯克出品",
    },
    "groq": {
        "name": "Groq",
        "icon": f"{_ICON}/groq.svg",
        "type": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "api_doc": "https://console.groq.com/keys",
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "default_model": "llama-3.3-70b-versatile",
        "requires_key": True, "supports_vision": False, "supports_model_fetch": True,
        "description": "Groq LPU 推理 — 速度极快",
    },
    "openrouter": {
        "name": "OpenRouter",
        "icon": f"{_ICON}/openrouter.svg",
        "type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "api_doc": "https://openrouter.ai/keys",
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.5-pro"],
        "default_model": "openai/gpt-4o",
        "requires_key": True, "supports_vision": True, "supports_model_fetch": True,
        "description": "OpenRouter — 多模型聚合路由",
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "icon": f"{_ICON}/nvidia-color.svg",
        "type": "openai_compatible",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_doc": "https://build.nvidia.com/",
        "models": ["nvidia/llama-3.1-nemotron-70b"],
        "default_model": "nvidia/llama-3.1-nemotron-70b",
        "requires_key": True, "supports_vision": False, "supports_model_fetch": True,
        "description": "NVIDIA NIM 推理微服务",
    },
    # ── 本地/自部署 ──
    "vllm": {
        "name": "vLLM",
        "icon": f"{_ICON}/vllm.svg",
        "type": "openai_compatible",
        "base_url": "http://localhost:8000/v1",
        "api_doc": "https://docs.vllm.ai/",
        "models": [],
        "default_model": "",
        "requires_key": False, "supports_vision": False, "supports_model_fetch": True,
        "description": "vLLM 自部署推理引擎",
    },
    "lmstudio": {
        "name": "LM Studio",
        "icon": f"{_ICON}/lmstudio.svg",
        "type": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "api_doc": "https://lmstudio.ai/",
        "models": [],
        "default_model": "",
        "requires_key": False, "supports_vision": False, "supports_model_fetch": True,
        "description": "LM Studio 本地模型管理器",
    },
}


# ── Data model ───────────────────────────────────────────────────────

@dataclass
class ProviderConfig:
    id: str = ""
    name: str = ""
    type: str = ""              # runtime adapter type (openai, anthropic, openai_compatible, ollama)
    template_key: str = ""      # template lookup key (may differ from type, e.g. dashscope)
    api_key: str = ""
    base_url: str = ""
    models: List[str] = field(default_factory=list)
    default_model: str = ""
    enable: bool = True
    created_at: float = 0.0

    @property
    def full_spec(self) -> str:
        model = self.default_model or (self.models[0] if self.models else "")
        return f"{self.type}:{model}"

    def get_template(self) -> dict:
        """Get the correct template, trying template_key first then type."""
        return PROVIDER_TEMPLATES.get(self.template_key) or PROVIDER_TEMPLATES.get(self.type, {})

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "template_key": self.template_key,
            "api_key": self.api_key, "base_url": self.base_url,
            "models": self.models, "default_model": self.default_model,
            "enable": self.enable, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        tk = d.get("template_key", "")
        # Auto-detect template_key for providers created before this field existed
        if not tk:
            url = d.get("base_url", "")
            type_ = d.get("type", "")
            for key, tmpl in PROVIDER_TEMPLATES.items():
                if tmpl.get("base_url") and tmpl["base_url"] in url:
                    tk = key; break
            if not tk: tk = type_  # fallback to type
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            type=d.get("type", ""), template_key=tk,
            api_key=d.get("api_key", ""), base_url=d.get("base_url", ""),
            models=d.get("models", []), default_model=d.get("default_model", ""),
            enable=d.get("enable", True), created_at=d.get("created_at", 0.0),
        )

    @classmethod
    def from_template(cls, template_key: str, name: str = "", api_key: str = "",
                      base_url: str = "") -> "ProviderConfig":
        tmpl = PROVIDER_TEMPLATES.get(template_key, {})
        return cls(
            id=f"{template_key}_{uuid.uuid4().hex[:8]}",
            name=name or tmpl.get("name", template_key),
            type=tmpl.get("type", template_key),
            template_key=template_key,
            api_key=api_key,
            base_url=base_url or tmpl.get("base_url", ""),
            models=list(tmpl.get("models", [])),
            default_model=tmpl.get("default_model", ""),
            created_at=time.time(),
        )


@dataclass
class ProviderStore:
    providers: List[ProviderConfig] = field(default_factory=list)
    active_provider_id: str = ""


# ── Persistence manager ──────────────────────────────────────────────

class ProviderManager:
    """Manage LLM provider configurations — persistent to JSON file."""

    def __init__(self, config_path: str | Path = None):
        if config_path is None:
            config_path = Path.home() / ".kcad_auto_pcb" / "providers.json"
        self._path = Path(config_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._store = self._load()

    def _load(self) -> ProviderStore:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                providers = [ProviderConfig.from_dict(p) for p in data.get("providers", [])]
                return ProviderStore(
                    providers=providers,
                    active_provider_id=data.get("active_provider_id", ""),
                )
            except Exception:
                pass
        return ProviderStore()

    def _save(self):
        data = {
            "providers": [p.to_dict() for p in self._store.providers],
            "active_provider_id": self._store.active_provider_id,
        }
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── CRUD ─────────────────────────────────────────────────────────

    def list_providers(self) -> List[ProviderConfig]:
        return [p for p in self._store.providers if p.enable]

    def list_all(self) -> List[ProviderConfig]:
        return list(self._store.providers)

    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        for p in self._store.providers:
            if p.id == provider_id:
                return p
        return None

    def add(self, provider: ProviderConfig) -> ProviderConfig:
        self._store.providers.append(provider)
        if not self._store.active_provider_id:
            self._store.active_provider_id = provider.id
        self._save()
        return provider

    def update(self, provider_id: str, **kwargs) -> Optional[ProviderConfig]:
        p = self.get(provider_id)
        if not p:
            return None
        for k, v in kwargs.items():
            if hasattr(p, k):
                setattr(p, k, v)
        self._save()
        return p

    def delete(self, provider_id: str) -> bool:
        before = len(self._store.providers)
        self._store.providers = [p for p in self._store.providers if p.id != provider_id]
        if self._store.active_provider_id == provider_id:
            self._store.active_provider_id = self._store.providers[0].id if self._store.providers else ""
        self._save()
        return len(self._store.providers) < before

    def set_active(self, provider_id: str):
        self._store.active_provider_id = provider_id
        self._save()

    def get_active(self) -> Optional[ProviderConfig]:
        return self.get(self._store.active_provider_id)

    def get_active_spec(self) -> Optional[str]:
        """Return 'type:model' for the active provider, suitable for LLMBackendFactory."""
        active = self.get_active()
        return active.full_spec if active else None

    def fetch_models(self, provider_type: str, api_key: str = "",
                     base_url: str = "") -> List[str]:
        """Auto-discover available models from the provider's API.

        Like AstrBot's get_models() — calls the provider's model list endpoint.
        Supports: OpenAI-compatible (/v1/models), Ollama (/api/tags).
        """
        import urllib.request, urllib.error

        tmpl = PROVIDER_TEMPLATES.get(provider_type, {})
        if not tmpl.get("supports_model_fetch"):
            return []

        url = (base_url or tmpl.get("base_url", "")).rstrip("/")

        try:
            # OpenAI-compatible: GET /v1/models
            if provider_type in ("openai", "deepseek", "openai_compatible"):
                req = urllib.request.Request(
                    f"{url}/models" if not url.endswith("/v1") else f"{url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    return sorted(
                        [m["id"] for m in data.get("data", [])
                         if not any(x in m["id"].lower() for x in ("whisper", "tts", "dall-e", "embedding", "moderation"))],
                        key=lambda x: ("gpt" not in x.lower() and "claude" not in x.lower(), x),
                    )

            # Ollama: GET /api/tags
            elif provider_type == "ollama":
                req = urllib.request.Request(f"{url}/api/tags")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    return sorted([m["name"] for m in data.get("models", [])])

        except Exception as e:
            # Return error info as a special marker
            return [f"__error__:{str(e)[:150]}"]

        return []

    def get_template(self, type_name: str) -> Optional[dict]:
        return PROVIDER_TEMPLATES.get(type_name)

    def list_templates(self) -> List[dict]:
        return [
            {"type": k, "name": v["name"], "icon": v.get("icon", ""),
             "description": v["description"], "base_url": v.get("base_url", ""),
             "api_doc": v.get("api_doc", ""),
             "supports_vision": v["supports_vision"],
             "supports_model_fetch": v.get("supports_model_fetch", False),
             "requires_key": v["requires_key"]}
            for k, v in PROVIDER_TEMPLATES.items()
        ]

    def export_for_web(self) -> dict:
        """Export data for the WebUI provider management page."""
        providers_data = []
        for p in self._store.providers:
            d = p.to_dict()
            # Redact API key for frontend display
            key = d.get("api_key", "")
            if key:
                d["api_key"] = key[:8] + "****" + key[-4:] if len(key) > 12 else "****"
            providers_data.append(d)

        return {
            "providers": providers_data,
            "active_provider_id": self._store.active_provider_id,
            "templates": self.list_templates(),
        }
