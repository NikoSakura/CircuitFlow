"""PCB drawing tools — execute BoardSpec into actual PCB geometry.

These are the tools the Agent calls with parameters from the spec.
Each tool is a deterministic function that modifies PCB state.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from ..geometry.point import Point
from ..geometry.rect import Rect
from .spec import BoardSpec, ComponentSpec, TraceSpec, ViaSpec


class PCBState:
    """Mutable PCB state that tools modify."""

    def __init__(self, width: float = 80, height: float = 60, layers: int = 2):
        self.width = width
        self.height = height
        self.layers = layers
        self.components: Dict[str, dict] = {}  # ref -> {x, y, layer, rotation, footprint}
        self.traces: List[dict] = []  # [{net, points, width, layer}]
        self.vias: List[dict] = []

    def to_board_spec(self) -> BoardSpec:
        spec = BoardSpec(width=self.width, height=self.height, layers=self.layers)
        for ref, c in self.components.items():
            spec.components.append(ComponentSpec(
                ref=ref, x=c["x"], y=c["y"],
                layer=c.get("layer", "F.Cu"), rotation=c.get("rotation", 0),
            ))
        for t in self.traces:
            spec.traces.append(TraceSpec(
                net=t["net"], width=t.get("width", 0.25),
                layer=t.get("layer", "F.Cu"), points=t.get("points", []),
            ))
        for v in self.vias:
            spec.vias.append(ViaSpec(
                x=v["x"], y=v["y"], net=v["net"], size=v.get("size", 1.0),
                drill=v.get("drill", 0.6), layers=tuple(v.get("layers", ["F.Cu", "B.Cu"])),
            ))
        return spec


# ── Tool functions ────────────────────────────────────────────────────

class PCBDrawTools:
    """Set of drawing tools that the Agent calls via tool-use."""

    def __init__(self):
        self.state = PCBState()
        self.history: List[str] = []  # action log

    def set_board(self, width: float, height: float, layers: int = 2) -> str:
        """Set board dimensions and layer count."""
        self.state.width = width
        self.state.height = height
        self.state.layers = layers
        msg = f"板尺寸: {width:.0f}x{height:.0f}mm, {layers}层"
        self.history.append(msg)
        return msg

    def place(self, ref: str, x: float, y: float,
              layer: str = "F.Cu", rotation: float = 0) -> str:
        """Place a component at (x, y)."""
        self.state.components[ref] = {
            "x": x, "y": y, "layer": layer, "rotation": rotation,
        }
        msg = f"放置 {ref} @ ({x:.1f}, {y:.1f}) {layer}"
        self.history.append(msg)
        return msg

    def route(self, net: str, points: List[Tuple[float, float]],
              width: float = 0.25, layer: str = "F.Cu") -> str:
        """Route a net along specified points."""
        self.state.traces.append({
            "net": net, "points": points, "width": width, "layer": layer,
        })
        msg = f"布线 {net}: {len(points)}点, {width}mm, {layer}"
        self.history.append(msg)
        return msg

    def add_via(self, x: float, y: float, net: str,
                size: float = 1.0, drill: float = 0.6) -> str:
        """Add a via at (x, y)."""
        self.state.vias.append({
            "x": x, "y": y, "net": net, "size": size, "drill": drill,
            "layers": ["F.Cu", "B.Cu"],
        })
        msg = f"过孔 @ ({x:.1f}, {y:.1f}) for {net}"
        self.history.append(msg)
        return msg

    def remove_component(self, ref: str) -> str:
        """Remove a placed component."""
        if ref in self.state.components:
            del self.state.components[ref]
            msg = f"移除 {ref}"
            self.history.append(msg)
            return msg
        return f"{ref} 不存在"

    def remove_trace(self, index: int) -> str:
        """Remove a trace by index."""
        if 0 <= index < len(self.state.traces):
            net = self.state.traces[index]["net"]
            del self.state.traces[index]
            msg = f"移除布线 #{index} ({net})"
            self.history.append(msg)
            return msg
        return f"布线 #{index} 不存在"

    def get_state_summary(self) -> str:
        """Return a text summary of current PCB state."""
        c = len(self.state.components)
        t = len(self.state.traces)
        v = len(self.state.vias)
        return (
            f"PCB: {self.state.width:.0f}x{self.state.height:.0f}mm, "
            f"{self.state.layers}层\n"
            f"元器件: {c} 个\n"
            f"布线: {t} 条\n"
            f"过孔: {v} 个"
        )

    def get_tool_schemas(self) -> List[dict]:
        """Return OpenAI-compatible tool schemas for function calling."""
        return [
            {
                "name": "set_board",
                "description": "设置 PCB 板尺寸和层数",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number", "description": "板宽 mm"},
                        "height": {"type": "number", "description": "板高 mm"},
                        "layers": {"type": "integer", "description": "层数 (2 或 4)", "default": 2},
                    },
                    "required": ["width", "height"],
                },
            },
            {
                "name": "place",
                "description": "放置一个元器件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "元器件编号 (如 R1, U1, C3)"},
                        "x": {"type": "number", "description": "X 坐标 mm"},
                        "y": {"type": "number", "description": "Y 坐标 mm"},
                        "layer": {"type": "string", "description": "层 (F.Cu 或 B.Cu)", "default": "F.Cu"},
                        "rotation": {"type": "number", "description": "旋转角度 (0/90/180/270)", "default": 0},
                    },
                    "required": ["ref", "x", "y"],
                },
            },
            {
                "name": "route",
                "description": "布线一条网络",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "net": {"type": "string", "description": "网络名称 (如 VCC, GND, /Net-1)"},
                        "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}, "description": "路径点列表 [[x1,y1], [x2,y2], ...]"},
                        "width": {"type": "number", "description": "线宽 mm", "default": 0.25},
                        "layer": {"type": "string", "description": "层", "default": "F.Cu"},
                    },
                    "required": ["net", "points"],
                },
            },
            {
                "name": "add_via",
                "description": "添加过孔",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "net": {"type": "string"},
                        "size": {"type": "number", "default": 1.0},
                        "drill": {"type": "number", "default": 0.6},
                    },
                    "required": ["x", "y", "net"],
                },
            },
        ]
