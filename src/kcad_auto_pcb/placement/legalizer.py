from __future__ import annotations
from typing import List, Dict
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..geometry.transform import snap_point_to_grid
from ..footprint.cache import FootprintCache
from .force_directed import PlacementResult, PlacementSolution


class Legalizer:
    """Ensure placements don't overlap and are on grid."""

    def __init__(self, footprint_cache: FootprintCache, grid_size: float = 0.1):
        self.fp_cache = footprint_cache
        self.grid_size = grid_size

    def legalize(self, solution: PlacementSolution,
                 footprint_map: Dict[str, str]) -> PlacementSolution:
        """Legalize all placements: snap to grid, resolve overlaps."""
        placements = solution.placements
        board = solution.board_bounds

        # Step 1: Snap all to grid
        for p in placements:
            p.position = snap_point_to_grid(p.position, self.grid_size)

        # Step 2: Resolve overlaps via simple iterative displacement
        max_iterations = 100
        for _ in range(max_iterations):
            overlaps_found = False
            for i in range(len(placements)):
                for j in range(i + 1, len(placements)):
                    # Skip overlap check if on different PCB sides
                    if placements[i].layer != placements[j].layer:
                        continue
                    r1 = self._get_rect(placements[i], footprint_map)
                    r2 = self._get_rect(placements[j], footprint_map)
                    if r1.overlaps(r2):
                        overlaps_found = True
                        self._separate(placements[i], placements[j], r1, r2, board)
            if not overlaps_found:
                break

        # Step 3: Ensure all within board bounds
        if board:
            for p in placements:
                rect = self._get_rect(p, footprint_map)
                if rect.left < board.left:
                    p.position = Point(p.position.x + (board.left - rect.left), p.position.y)
                if rect.right > board.right:
                    p.position = Point(p.position.x + (board.right - rect.right), p.position.y)
                if rect.bottom < board.bottom:
                    p.position = Point(p.position.x, p.position.y + (board.bottom - rect.bottom))
                if rect.top > board.top:
                    p.position = Point(p.position.x, p.position.y + (board.top - rect.top))

        return PlacementSolution(
            placements=placements,
            score=solution.score,
            metrics=solution.metrics,
            board_bounds=board,
        )

    def _get_rect(self, placement: PlacementResult, footprint_map: Dict[str, str]) -> Rect:
        fp_name = footprint_map.get(placement.component, "")
        fp = self.fp_cache.get(fp_name)
        if fp:
            c = fp.courtyard
            return Rect(
                placement.position.x + c.x,
                placement.position.y + c.y,
                c.w, c.h,
            )
        # Default: 5x5mm component
        return Rect(placement.position.x - 2.5, placement.position.y - 2.5, 5, 5)

    def _separate(self, p1: PlacementResult, p2: PlacementResult,
                  r1: Rect, r2: Rect, board: Rect | None):
        """Push two overlapping components apart minimally."""
        c1, c2 = r1.center, r2.center
        dx = c2.x - c1.x
        dy = c2.y - c1.y
        dist = (dx**2 + dy**2) ** 0.5
        if dist < 0.01:
            dx, dy = 1.0, 0.0
            dist = 1.0

        overlap_x = (r1.w + r2.w) / 2 - abs(dx)
        overlap_y = (r1.h + r2.h) / 2 - abs(dy)

        if overlap_x < overlap_y:
            push = overlap_x / 2
            if dx >= 0:
                p1.position = Point(p1.position.x - push, p1.position.y)
                p2.position = Point(p2.position.x + push, p2.position.y)
            else:
                p1.position = Point(p1.position.x + push, p1.position.y)
                p2.position = Point(p2.position.x - push, p2.position.y)
        else:
            push = overlap_y / 2
            if dy >= 0:
                p1.position = Point(p1.position.x, p1.position.y - push)
                p2.position = Point(p2.position.x, p2.position.y + push)
            else:
                p1.position = Point(p1.position.x, p1.position.y + push)
                p2.position = Point(p2.position.x, p2.position.y - push)
