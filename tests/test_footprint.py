from kcad_auto_pcb.footprint.parser import FootprintParser
from kcad_auto_pcb.footprint.cache import FootprintCache


def test_resolve_builtin_resistor():
    parser = FootprintParser()
    fp = parser.resolve("Resistor_SMD:R_0603_1608Metric")
    assert fp is not None
    assert fp.pad_count == 2
    assert fp.pads[0].type == "smd"
    assert fp.body_size[0] > 0


def test_resolve_builtin_dip8():
    parser = FootprintParser()
    fp = parser.resolve("Package_DIP:DIP-8_W7.62mm")
    assert fp is not None
    assert fp.pad_count == 8
    assert fp.pads[0].type == "thru_hole"


def test_resolve_unknown_returns_generic():
    parser = FootprintParser()
    fp = parser.resolve("SomeLib:UnknownPart")
    assert fp is not None  # Should return generic fallback
    assert fp.pad_count >= 2


def test_resolve_batch():
    parser = FootprintParser()
    names = [
        "Resistor_SMD:R_0603_1608Metric",
        "Capacitor_SMD:C_0603_1608Metric",
        "LED_SMD:LED_0603_1608Metric",
    ]
    result = parser.resolve_batch(names)
    assert len(result) == len(set(names))


def test_cache():
    cache = FootprintCache(max_size=10)
    fp1 = cache.get("Resistor_SMD:R_0603_1608Metric")
    assert fp1 is not None
    # Second get should come from cache
    fp2 = cache.get("Resistor_SMD:R_0603_1608Metric")
    assert fp2 is not None
