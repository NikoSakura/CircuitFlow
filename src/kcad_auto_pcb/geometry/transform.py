import math
from .point import Point
from .rect import Rect


def rotate_point(p: Point, angle_deg: float, origin: Point | None = None) -> Point:
    return p.rotated(angle_deg, origin)


def translate_rect(r: Rect, dx: float, dy: float) -> Rect:
    return Rect(r.x + dx, r.y + dy, r.w, r.h)


def snap_to_grid(value: float, grid_size: float = 0.1) -> float:
    steps = round(value / grid_size)
    return round(steps * grid_size, 6)


def snap_point_to_grid(p: Point, grid_size: float = 0.1) -> Point:
    return Point(snap_to_grid(p.x, grid_size), snap_to_grid(p.y, grid_size))


def bounding_box(points: list[Point]) -> Rect:
    if not points:
        return Rect(0, 0, 0, 0)
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
