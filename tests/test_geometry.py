import math
from kcad_auto_pcb.geometry.point import Point
from kcad_auto_pcb.geometry.rect import Rect
from kcad_auto_pcb.geometry.grid import Grid
from kcad_auto_pcb.geometry.transform import snap_to_grid, snap_point_to_grid


class TestPoint:
    def test_add(self):
        assert Point(1, 2) + Point(3, 4) == Point(4, 6)

    def test_sub(self):
        assert Point(5, 5) - Point(2, 3) == Point(3, 2)

    def test_distance(self):
        assert Point(0, 0).distance_to(Point(3, 4)) == 5.0

    def test_manhattan(self):
        assert Point(0, 0).manhattan_distance(Point(3, 4)) == 7.0

    def test_rotate_90(self):
        p = Point(1, 0).rotated(90)
        assert abs(p.x - 0) < 0.001
        assert abs(p.y - 1) < 0.001


class TestRect:
    def test_overlap(self):
        r1 = Rect(0, 0, 10, 10)
        r2 = Rect(5, 5, 10, 10)
        assert r1.overlaps(r2)

    def test_no_overlap(self):
        r1 = Rect(0, 0, 10, 10)
        r2 = Rect(20, 20, 10, 10)
        assert not r1.overlaps(r2)

    def test_contains(self):
        r = Rect(0, 0, 10, 10)
        assert r.contains(Point(5, 5))
        assert not r.contains(Point(15, 15))

    def test_center(self):
        r = Rect(0, 0, 10, 20)
        c = r.center
        assert c.x == 5
        assert c.y == 10


class TestGrid:
    def test_init(self):
        g = Grid(100, 80, 0.1)
        assert g.cols > 0
        assert g.rows > 0

    def test_world_to_grid(self):
        g = Grid(100, 80, 0.1)
        row, col = g.world_to_grid(Point(50, 40))
        assert 0 <= row < g.rows
        assert 0 <= col < g.cols

    def test_obstacle(self):
        g = Grid(100, 80, 1.0)
        g.add_rect_obstacle(45, 35, 10, 10)
        r, c = g.world_to_grid(Point(50, 40))
        assert not g.is_free(r, c)


class TestTransform:
    def test_snap_to_grid(self):
        assert snap_to_grid(1.23, 0.1) == 1.2

    def test_snap_point(self):
        p = snap_point_to_grid(Point(1.234, 5.678), 0.1)
        assert p.x == 1.2
        assert p.y == 5.7
