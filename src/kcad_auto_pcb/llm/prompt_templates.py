from __future__ import annotations
from typing import Dict, List
from ..schematic.model import Design
from ..placement.force_directed import PlacementSolution, PlacementMetrics
from ..pcb.stackup import BoardStackup


class PromptTemplates:
    """Token-minimized prompt templates for LLM-driven PCB design decisions.

    Each template produces ~150-500 tokens of prompt text.
    Responses are forced to JSON format, producing ~50-200 tokens.
    """

    @staticmethod
    def placement_review(
        design: Design,
        solution: PlacementSolution,
        footprint_map: Dict[str, str],
    ) -> str:
        """Prompt LLM to review and suggest placement improvements.

        Focus on: decoupling cap proximity, thermal considerations,
        connector placement, and signal path optimization.
        """
        metrics = solution.metrics
        board = solution.board_bounds

        # Build a compact component summary
        comp_lines = []
        for p in solution.placements[:20]:  # Limit to top 20 for token budget
            comp = design.components.get(p.component)
            if comp:
                comp_lines.append(
                    f"  {p.component}({comp.value}) at ({p.position.x:.1f},{p.position.y:.1f}) "
                    f"fp:{footprint_map.get(p.component, '?')}"
                )
        comp_summary = "\n".join(comp_lines) if comp_lines else "No components"

        # Identify critical relationships
        power_nets = [n.name for n in design.nets.values() if n.is_power]
        signal_nets = [n.name for n in design.nets.values() if not n.is_power]

        return f"""You are designing a PCB. Review this placement and suggest improvements.

Design: {design.component_count} components, {design.net_count} nets
Board: {board.w:.0f}x{board.h:.0f}mm
Power nets: {", ".join(power_nets[:5])}
Signal nets: {len(signal_nets)} total

Current placement (top {min(20, len(solution.placements))}):
{comp_summary}

Metrics:
- Total wirelength: {metrics.total_wirelength:.0f}mm
- Density max: {metrics.density_max} comp/cell

Rules to apply:
- Decoupling caps must be <5mm from their IC power pins
- Connectors on board edge
- High-speed signals: shortest path
- Heat-generating components: spread apart
- Group related components

Suggest up to 5 swaps and rotations to improve.
Return ONLY JSON: {{"swaps": [["ref1","ref2"],...], "rotations": {{"ref": angle}} }}"""

    @staticmethod
    def routing_strategy(
        design: Design,
        net_order: List[str],
        stackup: BoardStackup,
    ) -> str:
        """Prompt LLM to determine routing strategy: layer assignments and net priority.

        Key decisions:
        - Which nets go on which layers
        - Power nets should use inner planes on 4-layer
        - High-speed/clock nets get priority on outer layers
        - Net ordering for routing sequence
        """
        nets_info = []
        for name in net_order[:30]:  # Limit for token budget
            net = design.nets.get(name)
            if net:
                nets_info.append(
                    f"  {name}: {len(net.pins)} pins, "
                    f"{'POWER' if net.is_power else 'signal'}"
                )

        layers = [l["name"] for l in stackup.layers]
        layer_info = "\n".join(
            f"  {l['name']} ({l['type']})" for l in stackup.layers
        )

        return f"""Assign PCB routing layers for a {stackup.num_layers}-layer board.

Layers:
{layer_info}

Nets ({len(net_order)} total, showing top {min(30, len(net_order))}):
{chr(10).join(nets_info[:30])}

Strategy:
- Power nets (VCC, GND): route on inner plane layers
- High pin-count signal nets: priority on outer signal layers
- 4-layer: In1.Cu for VCC plane, In2.Cu for GND plane
- 2-layer: all on F.Cu and B.Cu

Assign each net to layers and optionally reorder for routing priority.
Return ONLY JSON: {{
  "layer_assignments": {{"net_name": ["layer1", "layer2"], ...}},
  "net_order": ["net1", "net2", ...],
  "critical_nets": ["net3"]
}}"""

    @staticmethod
    def design_review(board_summary: dict) -> str:
        """Final design review: check for common PCB issues."""
        return f"""Review this PCB design for issues:

{board_summary}

Check:
- Unrouted nets
- Clearance violations
- Thermal relief
- Signal integrity concerns
- Manufacturing feasibility

Return ONLY JSON with issues found and severity."""
