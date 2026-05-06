from __future__ import annotations
from dataclasses import dataclass
from .point import Point


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def bottom(self) -> float:
        return self.y

    @property
    def center(self) -> Point:
        return Point(self.x + self.w / 2, self.y + self.h / 2)

    @property
    def area(self) -> float:
        return self.w * self.h

    def contains(self, p: Point) -> bool:
        return self.left <= p.x <= self.right and self.bottom <= p.y <= self.top

    def overlaps(self, other: Rect) -> bool:
        return not (
            self.right <= other.left
            or self.left >= other.right
            or self.top <= other.bottom
            or self.bottom >= other.top
        )

    def expanded(self, margin: float) -> Rect:
        return Rect(
            self.x - margin, self.y - margin,
            self.w + 2 * margin, self.h + 2 * margin,
        )

    def intersection(self, other: Rect) -> Rect | None:
        x = max(self.left, other.left)
        y = max(self.bottom, other.bottom)
        w = min(self.right, other.right) - x
        h = min(self.top, other.top) - y
        if w <= 0 or h <= 0:
            return None
        return Rect(x, y, w, h)

    @staticmethod
    def from_center(center: Point, width: float, height: float) -> Rect:
        return Rect(center.x - width / 2, center.y - height / 2, width, height)

    def __repr__(self) -> str:
        return f"Rect({self.x:.3f}, {self.y:.3f}, {self.w:.3f}, {self.h:.3f})"
