from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
from ..geometry.point import Point
from ..geometry.grid import Grid
from ..schematic.model import Design, Net
from .astar import AStarRouter, RouteSegment, RouteVia, RoutingSolution


@dataclass
class LayerStackup:
    name: str
    signal_layers: List[str]
    plane_layers: List[str] = field(default_factory=list)
    layer_thickness: Dict[str, float] = field(default_factory=dict)

    @property
    def all_layers(self) -> List[str]:
        return self.signal_layers + self.plane_layers

    @staticmethod
    def two_layer() -> "LayerStackup":
        return LayerStackup(
            name="2-layer",
            signal_layers=["F.Cu", "B.Cu"],
        )

    @staticmethod
    def four_layer() -> "LayerStackup":
        return LayerStackup(
            name="4-layer",
            signal_layers=["F.Cu", "B.Cu"],
            plane_layers=["In1.Cu", "In2.Cu"],
        )


class MultiLayerRouter:
    """Multi-layer-aware router with via insertion for layer transitions."""

    def __init__(self, stackup: LayerStackup, grid: Grid,
                 clearance: float = 0.2, via_cost: float = 5.0):
        self.stackup = stackup
        self.grid = grid
        self.clearance = clearance
        self.via_cost = via_cost  # Cost penalty for adding a via
        # Create per-layer grids
        self.layer_grids: Dict[str, Grid] = {}
        for layer in stackup.all_layers:
            self.layer_grids[layer] = Grid(
                grid.cols * grid.resolution,
                grid.rows * grid.resolution,
                grid.resolution,
            )

    def route_all_nets(
        self,
        design: Design,
        pad_layer_map: Dict[Tuple[str, str], Tuple[Point, str]],  # (ref, pin) -> (pos, preferred_layer)
        net_layer_assignments: Dict[str, List[str]],  # net_name -> [preferred_layers]
        width: float = 0.25,
    ) -> RoutingSolution:
        """Route all nets across multiple layers."""
        solution = RoutingSolution()
        ordered_nets = self._order_nets(design)

        for net_name in ordered_nets:
            net = design.nets[net_name]
            preferred_layers = net_layer_assignments.get(net_name, ["F.Cu"])

            # Collect pin positions and their layers
            pin_data = []
            for p in net.pins:
                key = (p.component_ref, p.pin_number)
                if key in pad_layer_map:
                    pos, layer = pad_layer_map[key]
                    pin_data.append((p, pos, layer))

            if len(pin_data) < 2:
                continue

            # Route with multi-layer awareness
            net_segments = self._route_net_multilayer(
                net, pin_data, preferred_layers, width
            )
            if net_segments:
                for seg in net_segments:
                    if isinstance(seg, RouteSegment):
                        solution.segments.append(seg)
                    elif isinstance(seg, RouteVia):
                        solution.vias.append(seg)
                for seg in net_segments:
                    if isinstance(seg, RouteSegment):
                        self._mark_on_layer(seg)
            else:
                solution.unrouted_nets.append(net_name)

        # Retry failed nets — clear all obstacles and try with a clean slate
        retry_nets = list(solution.unrouted_nets)
        for _ in range(3):  # up to 3 retry rounds
            if not retry_nets: break
            # Clear all segment obstacles
            for layer_name, lg in self.layer_grids.items():
                lg.cells.fill(0)
            # Re-mark only component footprints (pad areas stay clear)
            solution.unrouted_nets.clear()
            for net_name in retry_nets:
                net = design.nets[net_name]
                pin_data = []
                for p in net.pins:
                    key = (p.component_ref, p.pin_number)
                    if key in pad_layer_map:
                        pos, layer = pad_layer_map[key]
                        pin_data.append((p, pos, layer))
                if len(pin_data) < 2:
                    solution.unrouted_nets.append(net_name)
                    continue
                net_segments = self._route_net_multilayer(
                    net, pin_data, net_layer_assignments.get(net_name, self.stackup.all_layers), width
                )
                if net_segments:
                    for seg in net_segments:
                        if isinstance(seg, RouteSegment):
                            solution.segments.append(seg)
                            self._mark_on_layer(seg)
                        elif isinstance(seg, RouteVia):
                            solution.vias.append(seg)
                else:
                    solution.unrouted_nets.append(net_name)
            retry_nets = list(solution.unrouted_nets)

        solution.via_count = len(solution.vias)
        solution.total_wirelength = sum(
            seg.start.distance_to(seg.end) for seg in solution.segments
        )
        return solution

    def _route_net_multilayer(
        self, net, pin_data, preferred_layers, width
    ) -> Optional[List]:
        """Route a single net, trying each preferred layer then all layers as fallback."""
        pad_positions = {}
        for p, pos, _pl in pin_data:
            pad_positions[(p.component_ref, p.pin_number)] = pos

        # Try preferred layers first
        for layer in preferred_layers:
            if layer not in self.stackup.all_layers:
                continue
            router = AStarRouter(self.layer_grids[layer], self.clearance)
            segs = router.route_net(net, pad_positions, layer, width)
            if segs is not None:
                return list(segs)

        # Fallback: try every available layer
        for layer in self.stackup.all_layers:
            if layer in preferred_layers:
                continue  # already tried
            router = AStarRouter(self.layer_grids[layer], self.clearance)
            segs = router.route_net(net, pad_positions, layer, width)
            if segs is not None:
                return list(segs)

        return None

    def _order_nets(self, design: Design) -> List[str]:
        """Order nets: short/simple nets first, then by pin count (ascending), power last."""
        nets = list(design.nets.values())
        # Route simple nets first (fewer pins = easier), power nets last (complex, many pins)
        nets.sort(key=lambda n: (n.is_power, len(n.pins)))
        return [n.name for n in nets]

    def _mark_on_layer(self, seg: RouteSegment):
        """Mark a segment as obstacle on its assigned layer."""
        grid = self.layer_grids.get(seg.layer)
        if grid:
            margin = self.clearance + seg.width / 2
            min_x = min(seg.start.x, seg.end.x) - margin
            min_y = min(seg.start.y, seg.end.y) - margin
            max_x = max(seg.start.x, seg.end.x) + margin
            max_y = max(seg.start.y, seg.end.y) + margin
            grid.add_rect_obstacle(min_x, min_y, max_x - min_x, max_y - min_y)
