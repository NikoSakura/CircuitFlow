from __future__ import annotations
from abc import ABC, abstractmethod
from .context import PipelineContext


class PipelineStep(ABC):
    """Base class for pipeline steps."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def run(self, ctx: PipelineContext) -> PipelineContext:
        ...

    def log(self, ctx: PipelineContext, msg: str, level: str = "info"):
        if level == "error":
            ctx.errors.append(f"[{self.name}] {msg}")
        elif level == "warning":
            ctx.warnings.append(f"[{self.name}] {msg}")
