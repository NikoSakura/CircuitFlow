from __future__ import annotations
import numpy as np
from .point import Point


class Grid:
    """Uniform grid for A* routing with obstacle representation."""

    def __init__(self, width_mm: float, height_mm: float, resolution: float = 0.1):
        self.resolution = resolution
        self.cols = int(width_mm / resolution) + 1
        self.rows = int(height_mm / resolution) + 1
        # 0 = free, 1 = obstacle
        self.cells = np.zeros((self.rows, self.cols), dtype=np.uint8)

    def world_to_grid(self, p: Point) -> tuple[int, int]:
        col = int(p.x / self.resolution)
        row = int(p.y / self.resolution)
        return (max(0, min(col, self.cols - 1)),
                max(0, min(row, self.rows - 1)))

    def grid_to_world(self, row: int, col: int) -> Point:
        return Point(col * self.resolution, row * self.resolution)

    def is_free(self, row: int, col: int) -> bool:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.cells[row, col] == 0
        return False

    def set_obstacle(self, row: int, col: int):
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.cells[row, col] = 1

    def add_rect_obstacle(self, x: float, y: float, w: float, h: float, margin: float = 0.0):
        """Mark a rectangular area as obstacle on the grid."""
        x -= margin
        y -= margin
        w += 2 * margin
        h += 2 * margin
        r1, c1 = self.world_to_grid(Point(x, y))
        r2, c2 = self.world_to_grid(Point(x + w, y + h))
        self.cells[r1:r2+1, c1:c2+1] = 1

    def is_line_free(self, p1: Point, p2: Point, clearance: float = 0.0) -> bool:
        """Check if a line segment between two points is obstacle-free."""
        r1, c1 = self.world_to_grid(p1)
        r2, c2 = self.world_to_grid(p2)
        steps = max(abs(c2 - c1), abs(r2 - r1)) + 1
        for i in range(steps + 1):
            t = i / steps if steps > 0 else 0
            col = int(c1 + (c2 - c1) * t)
            row = int(r1 + (r2 - r1) * t)
            if not self.is_free(row, col):
                return False
        return True
