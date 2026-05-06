from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> Point:
        return Point(self.x * scalar, self.y * scalar)

    def __truediv__(self, scalar: float) -> Point:
        return Point(self.x / scalar, self.y / scalar)

    def distance_to(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def manhattan_distance(self, other: Point) -> float:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def rotated(self, angle_deg: float, origin: Point | None = None) -> Point:
        if origin is None:
            origin = Point(0, 0)
        rad = math.radians(angle_deg)
        dx, dy = self.x - origin.x, self.y - origin.y
        return Point(
            x=origin.x + dx * math.cos(rad) - dy * math.sin(rad),
            y=origin.y + dx * math.sin(rad) + dy * math.cos(rad),
        )

    def __repr__(self) -> str:
        return f"({self.x:.3f}, {self.y:.3f})"
