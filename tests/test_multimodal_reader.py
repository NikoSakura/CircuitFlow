"""Tests for the multimodal schematic reader and component knowledge base."""

from kcad_auto_pcb.schematic.multimodal_reader import MultimodalSchematicReader


def test_package_to_footprint_mapping():
    """Verify package type mapping works."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)
    assert reader.PACKAGE_TO_FOOTPRINT["0603"] == "Resistor_SMD:R_0603_1608Metric"
    assert reader.PACKAGE_TO_FOOTPRINT["soic-8"] == "Package_SO:SOIC-8_3.9x4.9mm"
    assert reader.PACKAGE_TO_FOOTPRINT["sot-23"] == "Package_TO_SOT:SOT-23"


def test_component_knowledge_ne555():
    """Verify NE555 knowledge base."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)
    kb = reader.COMPONENT_KNOWLEDGE["NE555"]
    assert kb["function"] == "timer IC"
    assert kb["pins"]["1"] == "GND"
    assert kb["pins"]["8"] == "VCC"
    assert "100nF" in kb["decoupling"]["VCC"]


def test_component_knowledge_ams1117():
    """Verify AMS1117 voltage regulator knowledge."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)
    kb = reader.COMPONENT_KNOWLEDGE["AMS1117-3.3"]
    assert kb["function"] == "3.3V LDO regulator"
    assert kb["pins"]["1"] == "GND"
    assert kb["pins"]["2"] == "VOUT"
    assert "10uF" in kb["decoupling"]["VIN"]


def test_component_knowledge_lm358():
    """Verify LM358 op-amp knowledge."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)
    kb = reader.COMPONENT_KNOWLEDGE["LM358"]
    assert kb["function"] == "dual op-amp"
    assert kb["pins"]["4"] == "GND"
    assert kb["pins"]["8"] == "VCC"


def test_component_knowledge_bc547():
    """Verify BC547 transistor knowledge."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)
    kb = reader.COMPONENT_KNOWLEDGE["BC547"]
    assert kb["function"] == "NPN transistor"
    assert kb["pins"]["1"] == "Collector"
    assert kb["pins"]["2"] == "Base"
    assert kb["pins"]["3"] == "Emitter"


def test_build_design_enriches_footprints():
    """Test that _build_design applies knowledge base to fill in footprints."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)

    # Raw LLM output without footprints
    raw = {
        "components": [
            {"reference": "U1", "value": "NE555", "footprint": ""},
            {"reference": "R1", "value": "10k", "footprint": ""},
            {"reference": "C1", "value": "100nF", "footprint": ""},
        ],
        "nets": [
            {"name": "VCC", "is_power": True, "connections": [
                {"reference": "R1", "pin": "1"}, {"reference": "U1", "pin": "8"}
            ]},
        ],
    }

    design = reader._build_design(raw)

    # NE555 should get DIP-8 from knowledge base
    assert "U1" in design.components
    assert "DIP-8" in design.components["U1"].footprint_name

    # Resistor should get 0603 by default
    assert "R1" in design.components
    assert "0603" in design.components["R1"].footprint_name

    # Capacitor should get 0603 by default
    assert "C1" in design.components
    assert "0603" in design.components["C1"].footprint_name

    # Nets should be built
    assert "VCC" in design.nets
    assert design.nets["VCC"].is_power


def test_build_design_from_llm_output():
    """Full test of building Design from LLM response."""
    reader = MultimodalSchematicReader.__new__(MultimodalSchematicReader)

    raw = {
        "components": [
            {"reference": "U1", "value": "ESP32", "footprint": "", "package_type": "QFN-48"},
            {"reference": "C1", "value": "10uF", "footprint": "", "package_type": "0805"},
            {"reference": "C2", "value": "100nF", "footprint": "", "package_type": "0603"},
        ],
        "nets": [
            {"name": "VDD3P3", "is_power": True, "connections": [
                {"reference": "U1", "pin": "1"}, {"reference": "C1", "pin": "1"}
            ]},
            {"name": "GND", "is_power": True, "connections": [
                {"reference": "U1", "pin": "2"}, {"reference": "C1", "pin": "2"},
                {"reference": "C2", "pin": "2"}
            ]},
        ],
    }

    design = reader._build_design(raw)
    assert design.component_count == 3
    assert design.net_count == 2

    # ESP32 should get QFN-48 footprint from generator
    u1 = design.components["U1"]
    assert "QFN" in u1.footprint_name

    # C1 should get 0805 footprint
    assert "0805" in design.components["C1"].footprint_name

    # C2 should get 0603
    assert "0603" in design.components["C2"].footprint_name
