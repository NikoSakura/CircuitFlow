"""PCB Spec format — LLM-friendly structured PCB layout description.

Like Mermaid for diagrams, this spec is an intermediate representation
between LLM reasoning and KiCad geometry. The Agent generates this spec,
then drawing tools execute it.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import json, yaml


@dataclass
class ComponentSpec:
    ref: str
    x: float
    y: float
    layer: str = "F.Cu"       # F.Cu or B.Cu
    rotation: float = 0.0     # 0, 90, 180, 270
    side: str = "top"         # top or bottom


@dataclass
class TraceSpec:
    net: str
    width: float = 0.25
    layer: str = "F.Cu"
    points: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class ViaSpec:
    x: float
    y: float
    net: str
    layers: Tuple[str, str] = ("F.Cu", "B.Cu")
    size: float = 1.0
    drill: float = 0.6


@dataclass
class BoardSpec:
    """Complete PCB layout specification — what the Agent outputs.

    Format is designed to be both LLM-generatable and tool-executable.
    Can be serialized to JSON/YAML for persistence.
    """
    name: str = "unnamed"
    width: float = 80.0
    height: float = 60.0
    layers: int = 2
    components: List[ComponentSpec] = field(default_factory=list)
    traces: List[TraceSpec] = field(default_factory=list)
    vias: List[ViaSpec] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        return yaml.dump(self._to_dict(), allow_unicode=True, sort_keys=False)

    def to_json(self) -> str:
        return json.dumps(self._to_dict(), indent=2, ensure_ascii=False)

    def _to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width, "height": self.height,
            "layers": self.layers,
            "components": [
                {"ref": c.ref, "x": round(c.x, 2), "y": round(c.y, 2),
                 "layer": c.layer, "rotation": c.rotation}
                for c in self.components
            ],
            "traces": [
                {"net": t.net, "width": t.width, "layer": t.layer,
                 "points": [(round(x, 2), round(y, 2)) for x, y in t.points]}
                for t in self.traces
            ],
            "vias": [
                {"x": round(v.x, 2), "y": round(v.y, 2),
                 "net": v.net, "layers": list(v.layers),
                 "size": v.size, "drill": v.drill}
                for v in self.vias
            ],
            "notes": self.notes,
        }

    @classmethod
    def from_yaml(cls, text: str) -> "BoardSpec":
        return cls._from_dict(yaml.safe_load(text))

    @classmethod
    def from_json(cls, text: str) -> "BoardSpec":
        return cls._from_dict(json.loads(text))

    @classmethod
    def _from_dict(cls, d: dict) -> "BoardSpec":
        spec = cls(
            name=d.get("name", "unnamed"),
            width=d.get("width", 80), height=d.get("height", 60),
            layers=d.get("layers", 2),
            notes=d.get("notes", []),
        )
        for c in d.get("components", []):
            spec.components.append(ComponentSpec(
                ref=c["ref"], x=c.get("x", 0), y=c.get("y", 0),
                layer=c.get("layer", "F.Cu"), rotation=c.get("rotation", 0),
            ))
        for t in d.get("traces", []):
            spec.traces.append(TraceSpec(
                net=t["net"], width=t.get("width", 0.25),
                layer=t.get("layer", "F.Cu"),
                points=[tuple(p) for p in t.get("points", [])],
            ))
        for v in d.get("vias", []):
            spec.vias.append(ViaSpec(
                x=v["x"], y=v["y"], net=v["net"],
                layers=tuple(v.get("layers", ["F.Cu", "B.Cu"])),
                size=v.get("size", 1.0), drill=v.get("drill", 0.6),
            ))
        return spec


# ── Agent prompt template for spec generation ─────────────────────────

SPEC_GENERATION_PROMPT = """You are a PCB layout engineer. Given a schematic design, generate a PCB layout specification.

Output a YAML document with this EXACT structure:

```yaml
name: board_name
width: 80.0
height: 60.0
layers: 2
components:
  - ref: U1
    x: 40.0
    y: 30.0
    layer: F.Cu
    rotation: 0
  - ref: R1
    x: 30.0
    y: 30.0
    layer: F.Cu
traces:
  - net: VCC
    width: 0.5
    layer: F.Cu
    points:
      - [30.0, 30.0]
      - [35.0, 30.0]
      - [40.0, 30.0]
vias: []
notes: []
```

Layout rules:
1. Place the main IC (U1) at the center of the board
2. Place decoupling capacitors within 5mm of their IC power pins
3. Group components near their connected IC pins
4. Space components at least 3mm apart
5. Keep board size just large enough to fit all components
6. Power traces (VCC, GND) should be 0.5mm wide; signal traces 0.25mm

Given the design:
{design_summary}

Generate the complete PCB layout YAML specification."""
