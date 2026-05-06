from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from ..geometry.point import Point
from ..geometry.rect import Rect


@dataclass
class Component:
    reference: str
    value: str
    lib_id: str
    footprint_name: str
    position: Point
    rotation: float = 0.0
    pins: List[Pin] = field(default_factory=list)


@dataclass
class Pin:
    number: str
    name: str
    electrical_type: str  # input, output, power_in, passive, etc.
    component_ref: str
    position: Point = field(default_factory=lambda: Point(0, 0))


@dataclass
class PinRef:
    component_ref: str
    pin_number: str


@dataclass
class Net:
    name: str
    code: int
    pins: List[PinRef] = field(default_factory=list)

    @property
    def is_power(self) -> bool:
        n = self.name.upper()
        return any(kw in n for kw in ("VCC", "VDD", "GND", "VSS", "PWR", "V+", "V-", "+5", "+3", "VIN", "VBUS"))


@dataclass
class Design:
    components: Dict[str, Component] = field(default_factory=dict)
    nets: Dict[str, Net] = field(default_factory=dict)
    sheet_bounds: Optional[Rect] = None

    @property
    def component_count(self) -> int:
        return len(self.components)

    @property
    def net_count(self) -> int:
        return len(self.nets)

    def summary(self) -> dict:
        return {
            "components": self.component_count,
            "nets": self.net_count,
            "power_nets": sum(1 for n in self.nets.values() if n.is_power),
            "signal_nets": sum(1 for n in self.nets.values() if not n.is_power),
        }
