import pytest
from kcad_auto_pcb.geometry.rect import Rect
from kcad_auto_pcb.schematic.parser import SchematicParser
from kcad_auto_pcb.footprint.cache import FootprintCache
from kcad_auto_pcb.placement.force_directed import ForceDirectedPlacer
from kcad_auto_pcb.placement.legalizer import Legalizer
from kcad_auto_pcb.placement.cost_function import PlacementCost


@pytest.fixture
def design(simple_schematic_path):
    parser = SchematicParser()
    return parser.parse(simple_schematic_path)


def test_force_directed_placement(design, board_bounds):
    placer = ForceDirectedPlacer(design, board_bounds, iterations=50)
    solution = placer.place()
    assert len(solution.placements) == design.component_count
    assert solution.score > 0


def test_components_within_bounds(design, board_bounds):
    placer = ForceDirectedPlacer(design, board_bounds, iterations=100)
    solution = placer.place()
    for p in solution.placements:
        assert board_bounds.contains(p.position)


def test_legalizer(design, board_bounds):
    placer = ForceDirectedPlacer(design, board_bounds, iterations=50)
    solution = placer.place()
    fp_cache = FootprintCache()
    fp_map = {}
    for ref, comp in design.components.items():
        fp_map[ref] = comp.footprint_name

    legalizer = Legalizer(fp_cache)
    solution = legalizer.legalize(solution, fp_map)
    assert len(solution.placements) > 0


def test_cost_function(design, board_bounds):
    placer = ForceDirectedPlacer(design, board_bounds, iterations=50)
    solution = placer.place()
    cost = PlacementCost(design, board_bounds)
    score, metrics = cost.score(solution.placements)
    assert score >= 0
    assert "wirelength" in metrics
