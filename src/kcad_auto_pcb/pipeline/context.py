from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional
from ..schematic.model import Design
from ..footprint.parser import ResolvedFootprint
from ..placement.force_directed import PlacementSolution
from ..routing.astar import RoutingSolution
from ..pcb.board_builder import PCBBoard


@dataclass
class PipelineContext:
    """Shared state passed between pipeline stages."""
    source_path: str = ""
    output_path: str = ""

    # Stage outputs
    design: Optional[Design] = None
    footprints: Dict[str, ResolvedFootprint] = field(default_factory=dict)
    footprint_map: Dict[str, str] = field(default_factory=dict)  # component_ref -> footprint_name
    net_map: Dict[str, int] = field(default_factory=dict)  # net_name -> net_code
    placement: Optional[PlacementSolution] = None
    routing: Optional[RoutingSolution] = None
    board: Optional[PCBBoard] = None

    # Metadata
    stage: str = "init"
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
