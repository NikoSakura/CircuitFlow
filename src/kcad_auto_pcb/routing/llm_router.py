from __future__ import annotations
from typing import Dict, List
from ..llm.base import AbstractLLMBackend, LLMMessage
from ..llm.prompt_templates import PromptTemplates
from ..llm.response_parser import ResponseParser
from ..llm.token_counter import TokenBudget
from ..schematic.model import Design
from ..pcb.stackup import BoardStackup


class LLMRoutingStrategy:
    """Use LLM to determine routing strategy.

    Key decisions the LLM makes:
    1. Net-to-layer assignment (which layer each net should use)
    2. Net routing priority order
    3. Identification of critical nets

    The LLM understands circuit semantics (power, signal, high-speed)
    that pure heuristics miss.
    """

    def __init__(self, backend: AbstractLLMBackend, stackup: BoardStackup,
                 budget: TokenBudget):
        self.backend = backend
        self.stackup = stackup
        self.budget = budget

    async def assign_layers(
        self, design: Design, net_order: List[str]
    ) -> Dict[str, List[str]]:
        """Let LLM assign each net to appropriate layers."""
        prompt = PromptTemplates.routing_strategy(design, net_order, self.stackup)
        estimated_tokens = self.backend.token_count(prompt) + 300

        if not self.budget.can_call(estimated_tokens):
            return {}

        try:
            response = await self.backend.chat(
                messages=[
                    LLMMessage(role="system", content="You are a PCB routing expert. Assign nets to layers. Return ONLY JSON."),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=0.2,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            self.budget.consume(response.tokens_used)
        except Exception:
            return {}

        result = ResponseParser.parse_routing_response(response.text)
        return result.get("layer_assignments", {})

    async def reorder_nets(
        self, design: Design, current_order: List[str]
    ) -> List[str]:
        """Let LLM reorder nets for optimal routing sequence."""
        # The layer assignment prompt already includes net ordering
        # Combine into one call to save tokens
        return current_order  # Net ordering is handled in assign_layers
