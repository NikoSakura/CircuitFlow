from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
import heapq
import math
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..geometry.grid import Grid
from ..schematic.model import Design, Net


@dataclass
class RouteSegment:
    start: Point
    end: Point
    width: float
    layer: str
    net_code: int


@dataclass
class RouteVia:
    position: Point
    layers: Tuple[str, str]
    size: float
    drill: float
    net_code: int


@dataclass
class RoutingSolution:
    segments: List[RouteSegment] = field(default_factory=list)
    vias: List[RouteVia] = field(default_factory=list)
    unrouted_nets: List[str] = field(default_factory=list)
    total_wirelength: float = 0.0
    via_count: int = 0


class AStarRouter:
    """A* grid-based router with obstacle avoidance."""

    def __init__(self, grid: Grid, clearance: float = 0.2):
        self.grid = grid
        self.clearance = clearance

    def route_net(
        self,
        net: Net,
        pad_positions: Dict[Tuple[str, str], Point],  # (ref, pin_num) -> position
        layer: str = "F.Cu",
        width: float = 0.25,
    ) -> Optional[List[RouteSegment]]:
        """Route a single net connecting all its pins in a Steiner tree fashion.

        Strategy: route pin-to-pin sequentially, building up obstacle map.
        """
        pins = net.pins
        if len(pins) < 2:
            return None

        # Collect pin positions
        points = []
        for p in pins:
            key = (p.component_ref, p.pin_number)
            if key in pad_positions:
                points.append(pad_positions[key])

        if len(points) < 2:
            return None

        # Route sequentially: closest-first heuristic
        routed_points = [points[0]]
        remaining = points[1:]
        segments = []

        for target in remaining:
            nearest = min(routed_points, key=lambda rp: rp.manhattan_distance(target))
            path = self._find_path(nearest, target)
            if path is None:
                return None  # Net failed to route

            # Convert path to segments (defer obstacle marking until whole net is routed)
            for i in range(len(path) - 1):
                seg = RouteSegment(
                    start=path[i],
                    end=path[i + 1],
                    width=width,
                    layer=layer,
                    net_code=net.code,
                )
                segments.append(seg)

            routed_points.append(target)

        # Mark all segments as obstacles after all pins connected
        for seg in segments:
            self._mark_segment_obstacle(seg)

        return segments

    def _find_path(self, start: Point, end: Point) -> Optional[List[Point]]:
        """A* pathfinding on the grid."""
        sr, sc = self.grid.world_to_grid(start)
        er, ec = self.grid.world_to_grid(end)

        if not self.grid.is_free(er, ec):
            # Try neighbors of end point
            best = None
            for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)]:
                nr, nc = er + dr, ec + dc
                if self.grid.is_free(nr, nc):
                    best = (nr, nc)
                    break
            if best:
                er, ec = best
            else:
                return None

        # A* search
        open_set = [(0, 0, sr, sc)]  # (f, tiebreaker, row, col)
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        g_score: Dict[Tuple[int, int], float] = {(sr, sc): 0}

        tiebreaker = 1
        while open_set:
            _, _, cr, cc = heapq.heappop(open_set)

            if (cr, cc) == (er, ec):
                # Reconstruct path
                path = []
                cur = (cr, cc)
                while cur in came_from:
                    path.append(self.grid.grid_to_world(*cur))
                    cur = came_from[cur]
                path.append(start)
                path.reverse()
                return self._simplify_path(path)

            # Orthogonal-only moves (horizontal/vertical) for clean PCB traces
            for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nr, nc = cr + dr, cc + dc
                if not self.grid.is_free(nr, nc):
                    continue

                # Diagonal cost
                move_cost = math.sqrt(2) if abs(dr) + abs(dc) == 2 else 1.0
                tentative_g = g_score[(cr, cc)] + move_cost

                if tentative_g < g_score.get((nr, nc), float("inf")):
                    came_from[(nr, nc)] = (cr, cc)
                    g_score[(nr, nc)] = tentative_g
                    h = abs(nr - er) + abs(nc - ec)  # Manhattan heuristic
                    heapq.heappush(open_set, (tentative_g + h, tiebreaker, nr, nc))
                    tiebreaker += 1

        return None

    def _simplify_path(self, path: List[Point]) -> List[Point]:
        """Remove unnecessary intermediate points (collinear points)."""
        if len(path) <= 2:
            return path
        simplified = [path[0]]
        for i in range(1, len(path) - 1):
            prev, curr, nxt = path[i - 1], path[i], path[i + 1]
            # Check if prev -> curr -> next are collinear
            cross = (curr.x - prev.x) * (nxt.y - curr.y) - (curr.y - prev.y) * (nxt.x - curr.x)
            if abs(cross) > 0.001:
                simplified.append(curr)
        simplified.append(path[-1])
        return simplified

    def _mark_segment_obstacle(self, seg: RouteSegment):
        """Mark a routed segment as obstacle for subsequent nets."""
        margin = self.clearance + seg.width / 2
        min_x = min(seg.start.x, seg.end.x) - margin
        min_y = min(seg.start.y, seg.end.y) - margin
        max_x = max(seg.start.x, seg.end.x) + margin
        max_y = max(seg.start.y, seg.end.y) + margin
        self.grid.add_rect_obstacle(min_x, min_y, max_x - min_x, max_y - min_y)

    def clear_pad_area(self, position: Point, radius: float = 0.5):
        """Clear a small area around a pad to ensure it's reachable by A*."""
        r1, c1 = self.grid.world_to_grid(Point(position.x - radius, position.y - radius))
        r2, c2 = self.grid.world_to_grid(Point(position.x + radius, position.y + radius))
        r1, c1 = max(0, r1), max(0, c1)
        r2, c2 = min(self.grid.rows - 1, r2), min(self.grid.cols - 1, c2)
        self.grid.cells[r1:r2+1, c1:c2+1] = 0
