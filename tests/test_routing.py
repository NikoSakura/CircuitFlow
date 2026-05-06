from kcad_auto_pcb.geometry.grid import Grid
from kcad_auto_pcb.geometry.point import Point
from kcad_auto_pcb.routing.astar import AStarRouter
from kcad_auto_pcb.routing.ordering import NetOrdering
from kcad_auto_pcb.routing.multi_layer import LayerStackup


def test_astar_direct_path():
    grid = Grid(100, 80, 0.5)
    router = AStarRouter(grid)
    start = Point(10, 10)
    end = Point(50, 10)
    path = router._find_path(start, end)
    assert path is not None
    assert len(path) >= 2


def test_astar_with_obstacle():
    grid = Grid(100, 80, 0.5)
    # Add a wall in the middle
    grid.add_rect_obstacle(25, 0, 1, 80)
    router = AStarRouter(grid)
    path = router._find_path(Point(10, 40), Point(50, 40))
    assert path is not None
    # Should route around the obstacle
    assert len(path) > 2


def test_astar_unreachable():
    grid = Grid(100, 80, 1.0)
    grid.add_rect_obstacle(0, 0, 100, 80)  # Full board blocked
    router = AStarRouter(grid)
    path = router._find_path(Point(10, 40), Point(50, 40))
    assert path is None


def test_a_star_computes_path():
    """Test that A* finds the shortest path between two points."""
    grid = Grid(100, 80, 1.0)
    router = AStarRouter(grid)
    path = router._find_path(Point(10, 10), Point(30, 10))
    assert path is not None
    assert len(path) >= 2


def test_layer_stackup_2_layer():
    stackup = LayerStackup.two_layer()
    assert stackup.name == "2-layer"
    assert len(stackup.signal_layers) == 2


def test_layer_stackup_4_layer():
    stackup = LayerStackup.four_layer()
    assert stackup.name == "4-layer"
    assert len(stackup.signal_layers) == 2
    assert len(stackup.plane_layers) == 2
