from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..placement.force_directed import PlacementResult, PlacementSolution
from ..routing.astar import RouteSegment, RouteVia, RoutingSolution
from ..footprint.cache import FootprintCache
from .stackup import BoardStackup


@dataclass
class FootprintInstance:
    """A placed component on the PCB."""
    reference: str
    footprint_name: str
    position: Point
    rotation: float
    layer: str  # "F.Cu" or "B.Cu"
    pads: List = field(default_factory=list)


@dataclass
class TraceItem:
    """A trace segment on the PCB."""
    start: Point
    end: Point
    width: float
    layer: str
    net_code: int


@dataclass
class ViaItem:
    """A via on the PCB."""
    position: Point
    size: float
    drill: float
    layers: tuple
    net_code: int


@dataclass
class PCBBoard:
    """Complete PCB representation ready for export."""
    name: str
    bounds: Rect
    stackup: BoardStackup
    footprints: List[FootprintInstance] = field(default_factory=list)
    traces: List[TraceItem] = field(default_factory=list)
    vias: List[ViaItem] = field(default_factory=list)
    nets: Dict[str, int] = field(default_factory=dict)  # name -> code

    @property
    def component_count(self) -> int:
        return len(self.footprints)

    @property
    def trace_count(self) -> int:
        return len(self.traces)

    @property
    def summary(self) -> dict:
        total_trace_len = sum(
            t.start.distance_to(t.end) for t in self.traces
        )
        return {
            "components": self.component_count,
            "traces": self.trace_count,
            "vias": len(self.vias),
            "nets": len(self.nets),
            "total_trace_length_mm": total_trace_len,
            "layers": self.stackup.num_layers,
            "board_size": f"{self.bounds.w:.1f}x{self.bounds.h:.1f}mm",
        }


class BoardBuilder:
    """Assemble a complete PCBBoard from placement + routing results."""

    def __init__(self, footprint_cache: FootprintCache):
        self.fp_cache = footprint_cache

    def build(
        self,
        name: str,
        placement: PlacementSolution,
        routing: Optional[RoutingSolution],
        stackup: BoardStackup,
        footprint_map: Dict[str, str],
        net_map: Dict[str, int],
    ) -> PCBBoard:
        """Build a complete PCB from placement and routing data."""
        bounds = placement.board_bounds or Rect(0, 0, 100, 80)

        board = PCBBoard(
            name=name,
            bounds=bounds,
            stackup=stackup,
            nets=net_map,
        )

        # Add footprints
        for p in placement.placements:
            fp_name = footprint_map.get(p.component, "")
            fp = self.fp_cache.get(fp_name) if fp_name else None
            board.footprints.append(FootprintInstance(
                reference=p.component,
                footprint_name=fp_name,
                position=p.position,
                rotation=p.rotation,
                layer=p.layer,
                pads=fp.pads if fp else [],
            ))

        # Add traces and vias from routing
        if routing:
            for seg in routing.segments:
                board.traces.append(TraceItem(
                    start=seg.start,
                    end=seg.end,
                    width=seg.width,
                    layer=seg.layer,
                    net_code=seg.net_code,
                ))
            for via in routing.vias:
                board.vias.append(ViaItem(
                    position=via.position,
                    size=via.size,
                    drill=via.drill,
                    layers=via.layers,
                    net_code=via.net_code,
                ))

        return board
