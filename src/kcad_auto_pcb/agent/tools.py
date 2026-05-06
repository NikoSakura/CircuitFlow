from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..geometry.grid import Grid
from ..schematic.model import Design
from ..placement.force_directed import ForceDirectedPlacer, PlacementSolution, PlacementResult
from ..placement.legalizer import Legalizer
from ..placement.cost_function import PlacementCost
from ..routing.astar import AStarRouter, RoutingSolution
from ..routing.multi_layer import MultiLayerRouter
from ..routing.ordering import NetOrdering
from ..footprint.cache import FootprintCache
from ..pcb.stackup import BoardStackup
from ..pcb.board_builder import BoardBuilder
from ..pcb.exporter import PCBExporter


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    message: str = ""


class AgentTools:
    """Toolkit exposed to the PCB Agent LLM.

    Each tool is a callable method that the LLM can invoke
    to perform design actions. Results include quality metrics
    for the LLM to evaluate.
    """

    def __init__(self, design: Design, stackup: BoardStackup,
                 footprint_cache: FootprintCache, board_bounds: Rect):
        self.design = design
        self.stackup = stackup
        self.fp_cache = footprint_cache
        self.board_bounds = board_bounds

        # State
        self.placement: Optional[PlacementSolution] = None
        self.routing: Optional[RoutingSolution] = None
        self.grid: Optional[Grid] = None
        self.footprint_map: Dict[str, str] = {}
        self.net_map: Dict[str, int] = {}
        self.layer_assignments: Dict[str, List[str]] = {}
        self.history: List[dict] = []  # Action history for the agent

    def get_tool_schemas(self) -> List[dict]:
        """Return tool descriptions for the LLM (OpenAI function-calling format)."""
        return [
            {
                "name": "place_components",
                "description": "Run force-directed placement algorithm to position all components on the PCB.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "iterations": {"type": "integer", "default": 200, "description": "Number of force-directed iterations"}
                    }
                }
            },
            {
                "name": "get_placement_stats",
                "description": "Get current placement quality metrics (wirelength, density, bounding box).",
                "parameters": {"type": "object", "properties": {}}
            },
            {
                "name": "adjust_placement",
                "description": "Manually adjust a component's position or rotation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reference": {"type": "string", "description": "Component reference like R1, U1"},
                        "x": {"type": "number", "description": "New X position in mm"},
                        "y": {"type": "number", "description": "New Y position in mm"},
                        "rotation": {"type": "number", "description": "Rotation in degrees (0, 90, 180, 270)"},
                    },
                    "required": ["reference", "x", "y"]
                }
            },
            {
                "name": "swap_components",
                "description": "Swap positions of two components.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref1": {"type": "string"},
                        "ref2": {"type": "string"},
                    },
                    "required": ["ref1", "ref2"]
                }
            },
            {
                "name": "assign_layers",
                "description": "Assign nets to routing layers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "assignments": {
                            "type": "object",
                            "description": "Map of net_name to list of layer names"
                        }
                    },
                    "required": ["assignments"]
                }
            },
            {
                "name": "route_nets",
                "description": "Run autorouter to connect all nets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trace_width": {"type": "number", "default": 0.25, "description": "Trace width in mm"}
                    }
                }
            },
            {
                "name": "get_routing_stats",
                "description": "Get routing statistics (routed/unrouted nets, wirelength, vias).",
                "parameters": {"type": "object", "properties": {}}
            },
            {
                "name": "run_drc",
                "description": "Run basic design rule check and return violations.",
                "parameters": {"type": "object", "properties": {}}
            },
            {
                "name": "export_pcb",
                "description": "Export the PCB to .kicad_pcb file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_path": {"type": "string", "description": "Output file path"}
                    },
                    "required": ["output_path"]
                }
            },
            {
                "name": "finalize",
                "description": "Mark the design as complete. Call this when satisfied with the PCB.",
                "parameters": {"type": "object", "properties": {}}
            },
        ]

    # ---- Tool implementations ----

    def place_components(self, iterations: int = 200) -> ToolResult:
        placer = ForceDirectedPlacer(self.design, self.board_bounds, iterations=iterations)
        self.placement = placer.place()

        legalizer = Legalizer(self.fp_cache)
        self.placement = legalizer.legalize(self.placement, self.footprint_map)

        cost = PlacementCost(self.design, self.board_bounds)
        score, metrics = cost.score(self.placement.placements)
        self.placement.score = score

        self.history.append({"action": "place", "score": score, "metrics": metrics})
        return ToolResult(
            success=True,
            data={"score": score, "components": len(self.placement.placements)},
            message=f"Placed {len(self.placement.placements)} components. Score: {score:.1f}"
        )

    def get_placement_stats(self) -> ToolResult:
        if not self.placement:
            return ToolResult(success=False, message="No placement data. Run place_components first.")

        cost = PlacementCost(self.design, self.board_bounds)
        score, metrics = cost.score(self.placement.placements)

        comp_list = []
        for p in self.placement.placements[:30]:
            comp_list.append({
                "ref": p.component,
                "x": round(p.position.x, 1),
                "y": round(p.position.y, 1),
                "layer": p.layer,
            })

        return ToolResult(success=True, data={
            "score": score,
            "wirelength": metrics["wirelength"],
            "density_variance": metrics["density_variance"],
            "components": comp_list,
            "component_count": len(self.placement.placements),
        })

    def adjust_placement(self, reference: str, x: float, y: float, rotation: float = 0) -> ToolResult:
        if not self.placement:
            return ToolResult(success=False, message="No placement. Run place_components first.")

        for p in self.placement.placements:
            if p.component == reference:
                p.position = Point(x, y)
                p.rotation = rotation
                self.history.append({"action": "adjust", "ref": reference, "pos": (x, y)})
                return ToolResult(success=True, message=f"Moved {reference} to ({x:.1f}, {y:.1f})")

        return ToolResult(success=False, message=f"Component {reference} not found.")

    def swap_components(self, ref1: str, ref2: str) -> ToolResult:
        if not self.placement:
            return ToolResult(success=False, message="No placement. Run place_components first.")

        p1 = p2 = None
        for p in self.placement.placements:
            if p.component == ref1:
                p1 = p
            elif p.component == ref2:
                p2 = p

        if p1 and p2:
            p1.position, p2.position = p2.position, p1.position
            self.history.append({"action": "swap", "refs": (ref1, ref2)})
            return ToolResult(success=True, message=f"Swapped {ref1} <-> {ref2}")
        return ToolResult(success=False, message="One or both components not found.")

    def assign_layers(self, assignments: Dict[str, List[str]]) -> ToolResult:
        self.layer_assignments = assignments
        return ToolResult(
            success=True,
            data=assignments,
            message=f"Assigned {len(assignments)} nets to layers."
        )

    def route_nets(self, trace_width: float = 0.25) -> ToolResult:
        if not self.placement:
            return ToolResult(success=False, message="Place components first.")

        bw, bh = self.board_bounds.w, self.board_bounds.h
        grid = Grid(bw, bh, 0.1)
        self.grid = grid

        # Mark obstacles from placed components
        for p in self.placement.placements:
            fp_name = self.footprint_map.get(p.component, "")
            fp = self.fp_cache.get(fp_name)
            if fp:
                c = fp.courtyard
                grid.add_rect_obstacle(
                    p.position.x + c.x, p.position.y + c.y, c.w, c.h, margin=0.3
                )

        # Default layer assignments if not set
        if not self.layer_assignments:
            for net_name in self.design.nets:
                net = self.design.nets[net_name]
                self.layer_assignments[net_name] = (
                    ["In1.Cu", "In2.Cu"] if (net.is_power and self.stackup.num_layers >= 4)
                    else ["F.Cu", "B.Cu"]
                )

        # Build pad layer map
        pad_layer_map = {}
        for p in self.placement.placements:
            fp_name = self.footprint_map.get(p.component, "")
            fp = self.fp_cache.get(fp_name)
            if fp:
                for pad in fp.pads:
                    key = (p.component, pad.number)
                    pos = Point(
                        p.position.x + pad.position.x,
                        p.position.y + pad.position.y,
                    )
                    pad_layer_map[key] = (pos, p.layer)

        router = MultiLayerRouter(
            self.stackup.as_routing_stackup(), grid
        )
        self.routing = router.route_all_nets(
            self.design, pad_layer_map, self.layer_assignments, width=trace_width
        )

        self.history.append({"action": "route", "segments": len(self.routing.segments)})
        return ToolResult(
            success=True,
            data={
                "routed_segments": len(self.routing.segments),
                "unrouted": self.routing.unrouted_nets,
                "vias": self.routing.via_count,
                "wirelength": self.routing.total_wirelength,
            },
            message=f"Routed {len(self.routing.segments)} segments, "
                    f"{len(self.routing.unrouted_nets)} unrouted nets, "
                    f"{self.routing.via_count} vias."
        )

    def get_routing_stats(self) -> ToolResult:
        if not self.routing:
            return ToolResult(success=False, message="No routing data. Run route_nets first.")
        return ToolResult(success=True, data={
            "segments": len(self.routing.segments),
            "vias": self.routing.via_count,
            "wirelength_mm": round(self.routing.total_wirelength, 1),
            "unrouted_nets": self.routing.unrouted_nets,
            "completion": f"{len(self.routing.segments)} traces, {len(self.routing.unrouted_nets)} unrouted",
        })

    def run_drc(self) -> ToolResult:
        violations = []
        if self.routing:
            if self.routing.unrouted_nets:
                violations.append({
                    "type": "unrouted_net",
                    "severity": "error",
                    "nets": self.routing.unrouted_nets,
                    "message": f"{len(self.routing.unrouted_nets)} unrouted nets"
                })

        if self.placement and self.board_bounds:
            for p in self.placement.placements:
                fp_name = self.footprint_map.get(p.component, "")
                fp = self.fp_cache.get(fp_name)
                if fp:
                    c = fp.courtyard
                    r = Rect(
                        p.position.x + c.x, p.position.y + c.y, c.w, c.h
                    )
                    if not self.board_bounds.contains(Point(r.x, r.y)) or \
                       not self.board_bounds.contains(Point(r.right, r.top)):
                        violations.append({
                            "type": "component_out_of_bounds",
                            "severity": "error",
                            "component": p.component,
                            "message": f"{p.component} outside board bounds"
                        })

        return ToolResult(
            success=len(violations) == 0,
            data={"violations": violations, "count": len(violations)},
            message=f"DRC: {len(violations)} violations found."
        )

    def export_pcb(self, output_path: str) -> ToolResult:
        if not self.placement:
            return ToolResult(success=False, message="Nothing to export.")

        builder = BoardBuilder(self.fp_cache)
        board = builder.build(
            name="auto_pcb",
            placement=self.placement,
            routing=self.routing,
            stackup=self.stackup,
            footprint_map=self.footprint_map,
            net_map=self.net_map,
        )

        exporter = PCBExporter()
        exporter.export(board, output_path)
        exporter.export(board, output_path.replace(".kicad_pcb", ".json"), format="json")

        return ToolResult(
            success=True,
            data=board.summary,
            message=f"Exported to {output_path}. {board.summary}"
        )

    def finalize(self) -> ToolResult:
        stats = {}
        if self.placement:
            cost = PlacementCost(self.design, self.board_bounds)
            _, metrics = cost.score(self.placement.placements)
            stats.update(metrics)
        if self.routing:
            stats["routed_segments"] = len(self.routing.segments)
            stats["unrouted_nets"] = self.routing.unrouted_nets
        return ToolResult(
            success=True,
            data={"actions": len(self.history), "stats": stats},
            message=f"Design finalized after {len(self.history)} actions."
        )
