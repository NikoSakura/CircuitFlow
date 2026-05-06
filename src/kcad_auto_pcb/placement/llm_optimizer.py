from __future__ import annotations
from typing import Dict
from ..llm.base import AbstractLLMBackend, LLMMessage
from ..llm.prompt_templates import PromptTemplates
from ..llm.response_parser import ResponseParser
from ..llm.token_counter import TokenBudget
from ..schematic.model import Design
from ..footprint.cache import FootprintCache
from .force_directed import PlacementSolution, PlacementResult


class LLMPlacementOptimizer:
    """Use LLM to review and refine component placement.

    The LLM analyzes placement quality and suggests specific improvements:
    - Component swaps
    - Rotation adjustments
    - Proximity improvements (decoupling caps near ICs, etc.)

    This is the key differentiator from pure algorithmic placers.
    """

    def __init__(self, backend: AbstractLLMBackend, budget: TokenBudget):
        self.backend = backend
        self.budget = budget

    async def optimize(
        self,
        solution: PlacementSolution,
        design: Design,
        footprint_map: Dict[str, str],
        fp_cache: FootprintCache,
    ) -> PlacementSolution:
        """Review placement via LLM and apply suggested improvements."""
        # Build prompt
        prompt = PromptTemplates.placement_review(design, solution, footprint_map)
        estimated_tokens = self.backend.token_count(prompt) + 200

        if not self.budget.can_call(estimated_tokens):
            return solution

        try:
            response = await self.backend.chat(
                messages=[
                    LLMMessage(role="system", content="You are a PCB layout expert. Respond only with JSON."),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=0.2,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            self.budget.consume(response.tokens_used)
        except Exception:
            return solution  # LLM failed, keep algorithmic result

        # Parse and apply suggestions
        suggestions = ResponseParser.parse_placement_response(response.text)
        swaps = suggestions.get("swaps", [])
        rotations = suggestions.get("rotations", {})

        # Apply swaps
        placements_by_ref = {p.component: p for p in solution.placements}
        for ref1, ref2 in swaps:
            if ref1 in placements_by_ref and ref2 in placements_by_ref:
                p1, p2 = placements_by_ref[ref1], placements_by_ref[ref2]
                p1.position, p2.position = p2.position, p1.position

        # Apply rotations
        for ref, angle in rotations.items():
            if ref in placements_by_ref:
                try:
                    placements_by_ref[ref].rotation = float(angle)
                except (ValueError, TypeError):
                    pass

        # Move decoupling caps near their ICs
        self._optimize_decoupling(placements_by_ref, design, fp_cache)

        return solution

    def _optimize_decoupling(
        self,
        placements: Dict[str, PlacementResult],
        design: Design,
        fp_cache: FootprintCache,
    ):
        """Heuristic: move capacitors close to IC power pins."""
        # Identify decoupling caps (small capacitors on power nets)
        caps = {
            ref: comp for ref, comp in design.components.items()
            if comp.value and "n" in comp.value.lower() or "u" in comp.value.lower()
            and comp.lib_id and "C" in comp.lib_id
        }

        for cap_ref, cap in caps.items():
            if cap_ref not in placements:
                continue
            # Find which IC this cap connects to via power nets
            for net in design.nets.values():
                if not net.is_power:
                    continue
                cap_pins = [p for p in net.pins if p.component_ref == cap_ref]
                if not cap_pins:
                    continue
                # Find IC on same net
                for p in net.pins:
                    if p.component_ref in placements and p.component_ref != cap_ref:
                        ic_pos = placements[p.component_ref].position
                        cap_pos = placements[cap_ref].position
                        # Move cap closer to IC
                        dist = ic_pos.distance_to(cap_pos)
                        if dist > 8:  # mm - too far
                            from ..geometry.point import Point
                            # Place cap near IC (offset by ~4mm)
                            placements[cap_ref].position = Point(
                                ic_pos.x + 4, ic_pos.y + 2
                            )
