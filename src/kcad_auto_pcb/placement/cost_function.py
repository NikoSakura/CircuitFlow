from __future__ import annotations
from typing import Dict, List, Tuple
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..schematic.model import Design
from .force_directed import PlacementResult


class PlacementCost:
    """Score placement quality: lower is better."""

    def __init__(self, design: Design, board_bounds: Rect):
        self.design = design
        self.board_bounds = board_bounds

    def total_wirelength(self, placements: List[PlacementResult]) -> float:
        """Estimate total wirelength using half-perimeter wirelength (HPWL)."""
        pos = {p.component: p.position for p in placements}
        total = 0.0
        for net in self.design.nets.values():
            refs = [p.component_ref for p in net.pins if p.component_ref in pos]
            if len(refs) < 2:
                continue
            xs = [pos[r].x for r in refs]
            ys = [pos[r].y for r in refs]
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    def density_score(self, placements: List[PlacementResult], grid_cells: int = 10) -> float:
        """Score component density uniformity. Lower = more uniform."""
        bw, bh = self.board_bounds.w, self.board_bounds.h
        cell_w, cell_h = bw / grid_cells, bh / grid_cells
        grid = [[0] * grid_cells for _ in range(grid_cells)]

        for p in placements:
            ci = int((p.position.x - self.board_bounds.x) / cell_w)
            ri = int((p.position.y - self.board_bounds.y) / cell_h)
            ci = max(0, min(ci, grid_cells - 1))
            ri = max(0, min(ri, grid_cells - 1))
            grid[ri][ci] += 1

        counts = [c for row in grid for c in row if c > 0]
        if not counts:
            return 0
        avg = sum(counts) / len(counts)
        variance = sum((c - avg) ** 2 for c in counts) / len(counts)
        return variance

    def score(self, placements: List[PlacementResult]) -> Tuple[float, dict]:
        wl = self.total_wirelength(placements)
        density = self.density_score(placements)
        # Combined: normalize wirelength by number of nets
        normalized_wl = wl / max(len(self.design.nets), 1)
        combined = normalized_wl + density * 100
        return combined, {
            "wirelength": wl,
            "density_variance": density,
            "normalized_wirelength": normalized_wl,
        }
