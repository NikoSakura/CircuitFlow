from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BoardStackup:
    """Define the physical layer stackup for a PCB."""

    num_layers: int
    layers: List[dict] = field(default_factory=list)
    thickness: float = 1.6  # Total thickness in mm

    def __post_init__(self):
        if not self.layers:
            self.layers = self._default_layers(self.num_layers)

    @staticmethod
    def _default_layers(n: int) -> List[dict]:
        if n == 2:
            return [
                {"name": "F.Cu", "type": "signal", "thickness": 0.035},
                {"name": "B.Cu", "type": "signal", "thickness": 0.035},
            ]
        elif n == 4:
            return [
                {"name": "F.Cu", "type": "signal", "thickness": 0.035},
                {"name": "In1.Cu", "type": "power_plane", "thickness": 0.035},
                {"name": "In2.Cu", "type": "ground_plane", "thickness": 0.035},
                {"name": "B.Cu", "type": "signal", "thickness": 0.035},
            ]
        else:
            return BoardStackup._default_layers(2)

    @property
    def signal_layers(self) -> List[str]:
        return [l["name"] for l in self.layers if l["type"] == "signal"]

    @property
    def plane_layers(self) -> List[str]:
        return [l["name"] for l in self.layers if "plane" in l["type"]]

    @property
    def layer_names(self) -> List[str]:
        return [l["name"] for l in self.layers]

    def as_routing_stackup(self):
        from ..routing.multi_layer import LayerStackup
        return LayerStackup(
            name=f"{self.num_layers}-layer",
            signal_layers=self.signal_layers,
            plane_layers=self.plane_layers,
        )
