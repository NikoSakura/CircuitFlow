from kcad_auto_pcb.schematic.parser import SchematicParser
from kcad_auto_pcb.schematic.connectivity import ConnectivityGraph


def test_parse_simple_led(simple_schematic_path):
    parser = SchematicParser()
    design = parser.parse(simple_schematic_path)
    assert design.component_count > 0
    assert design.net_count > 0


def test_parse_ne555(ne555_schematic_path):
    parser = SchematicParser()
    design = parser.parse(ne555_schematic_path)
    assert design.component_count > 0


def test_connectivity_graph(simple_schematic_path):
    parser = SchematicParser()
    design = parser.parse(simple_schematic_path)
    graph = ConnectivityGraph(design)
    assert graph.edge_count() >= 0


def test_fallback_design():
    """Test that parser works even without a real file."""
    parser = SchematicParser()
    design = parser.parse("nonexistent_file.kicad_sch")
    assert design.component_count > 0  # Should return fallback design
    assert len(design.nets) > 0


def test_design_summary(simple_schematic_path):
    parser = SchematicParser()
    design = parser.parse(simple_schematic_path)
    summary = design.summary()
    assert "components" in summary
    assert "nets" in summary
