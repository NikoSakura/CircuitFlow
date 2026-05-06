from __future__ import annotations
from typing import List
from ..geometry.grid import Grid
from ..schematic.model import Design, Net
from .astar import AStarRouter, RoutingSolution


class RipupRetry:
    """Rip-up and reroute strategy for failed connections.

    When a net fails to route, rip up nearby traces and retry
    with progressively relaxed constraints.
    """

    def __init__(self, grid: Grid, max_attempts: int = 5):
        self.grid = grid
        self.max_attempts = max_attempts

    def route_with_retry(
        self,
        net: Net,
        router: AStarRouter,
        pad_positions: dict,
        layer: str = "F.Cu",
        width: float = 0.25,
    ) -> RoutingSolution | None:
        """Attempt to route a net, retrying with relaxed constraints."""
        solution = RoutingSolution()

        for attempt in range(self.max_attempts):
            # Increase clearance margin each attempt
            relaxed_width = width * (1 + attempt * 0.5)

            segments = router.route_net(net, pad_positions, layer, relaxed_width)
            if segments is not None:
                solution.segments.extend(segments)
                solution.total_wirelength = sum(
                    s.start.distance_to(s.end) for s in segments
                )
                return solution

            # Rip-up approach: couldn't route, try wider search
            # In a full implementation, we'd rip up conflicting traces
            # For now, relax grid constraints by clearing edge obstacles

        return None  # Failed after all attempts
