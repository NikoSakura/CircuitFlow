from __future__ import annotations
from typing import List
from ..schematic.model import Design


class NetOrdering:
    """Determine routing order for nets.

    Priority: power nets first, then critical signals (high pin count),
    then remaining nets.
    """

    @staticmethod
    def order(design: Design) -> List[str]:
        nets = list(design.nets.values())
        nets.sort(key=lambda n: (
            0 if n.is_power else 1,  # Power nets first
            -len(n.pins),             # More pins = higher priority
            n.name,                   # Alphabetical tiebreaker
        ))
        return [n.name for n in nets]

    @staticmethod
    def classify(design: Design) -> dict:
        """Classify nets into categories for LLM decision-making."""
        power_nets = []
        signal_nets = []
        for net in design.nets.values():
            if net.is_power:
                power_nets.append(net.name)
            else:
                signal_nets.append(net.name)
        return {
            "power": power_nets,
            "signal": signal_nets,
            "total": len(power_nets) + len(signal_nets),
        }
