from __future__ import annotations
from typing import Optional, List
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    # LLM Configuration
    placement_llm_spec: str = "openai:gpt-4o-mini"
    routing_llm_spec: str = "openai:gpt-4o-mini"
    fallback_llm_spec: str = ""
    token_budget_per_run: int = 10000

    # API Keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"

    # PCB Defaults
    default_board_width: float = 100.0
    default_board_height: float = 80.0
    default_trace_width: float = 0.25
    default_via_size: float = 1.0
    default_via_drill: float = 0.6
    default_clearance: float = 0.2

    # KiCad paths
    kicad_python_path: str = ""  # Auto-detected if empty
    kicad_footprint_dirs: List[str] = []

    # FreeRouting
    freerouting_jar_path: str = "d:/project/PCB Design/freerouting-1.9.0.jar"
    freerouting_max_passes: int = 15
    freerouting_timeout_seconds: int = 600

    # Algorithm tuning
    grid_resolution: float = 0.1
    force_directed_iterations: int = 200
    ripup_max_attempts: int = 5

    model_config = {"env_prefix": "KCAD_", "env_file": ".env"}

    @staticmethod
    def detect_kicad_python() -> str | None:
        """Auto-detect KiCad's bundled Python interpreter."""
        import os, glob
        candidates = [
            "C:/Program Files/KiCad/10.0/bin/python.exe",
            "C:/Program Files/KiCad/9.0/bin/python.exe",
            "C:/Program Files/KiCad/8.0/bin/python.exe",
            "C:/Program Files/KiCad/bin/python.exe",
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/usr/bin/python3",
        ]
        # Also search with glob for versioned paths
        for pattern in [
            "C:/Program Files/KiCad/*/bin/python.exe",
        ]:
            for path in sorted(glob.glob(pattern), reverse=True):
                candidates.append(path)

        for path in candidates:
            if os.path.isfile(path):
                return path
        return None
