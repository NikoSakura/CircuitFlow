"""Tests for the parametric footprint generator."""

from kcad_auto_pcb.footprint.generator import FootprintGenerator
from kcad_auto_pcb.footprint.parser import FootprintParser


def test_generate_0603():
    fp = FootprintGenerator.generate("0603")
    assert fp is not None
    assert fp.pad_count == 2
    assert fp.pads[0].type == "smd"
    assert fp.body_size[0] > 0


def test_generate_soic8():
    fp = FootprintGenerator.generate("SOIC-8")
    assert fp is not None
    assert fp.pad_count == 8
    assert all(p.type == "smd" for p in fp.pads)


def test_generate_soic16():
    fp = FootprintGenerator.generate("SOIC-16")
    assert fp is not None
    assert fp.pad_count == 16


def test_generate_tssop16():
    fp = FootprintGenerator.generate("TSSOP-16")
    assert fp is not None
    assert fp.pad_count == 16


def test_generate_qfp32():
    fp = FootprintGenerator.generate("LQFP-32")
    assert fp is not None
    assert fp.pad_count == 32


def test_generate_dip14():
    fp = FootprintGenerator.generate("DIP-14")
    assert fp is not None
    assert fp.pad_count == 14
    assert all(p.type == "thru_hole" for p in fp.pads)


def test_generate_dip8():
    fp = FootprintGenerator.generate("DIP-8")
    assert fp is not None
    assert fp.pad_count == 8


def test_generate_sot23():
    fp = FootprintGenerator.generate("SOT-23")
    assert fp is not None
    assert fp.pad_count == 3


def test_generate_to92():
    fp = FootprintGenerator.generate("TO-92")
    assert fp is not None
    assert fp.pad_count == 3


def test_generate_qfn32():
    fp = FootprintGenerator.generate("QFN-32")
    assert fp is not None
    assert fp.pad_count > 32  # Includes exposed pad


def test_generate_pin_header():
    fp = FootprintGenerator.pin_header(8, 1)
    assert fp is not None
    assert fp.pad_count == 8
    assert all(p.type == "thru_hole" for p in fp.pads)


def test_generate_dual_pin_header():
    fp = FootprintGenerator.pin_header(4, 2)
    assert fp.pad_count == 8


def test_generate_unknown_returns_none():
    fp = FootprintGenerator.generate("MysteryPackage-42")
    assert fp is None


def test_parser_tries_generator():
    """Test that FootprintParser now falls back to generator."""
    parser = FootprintParser()
    fp = parser.resolve("Package_SO:SOIC-8_3.9x4.9mm")
    assert fp is not None
    assert fp.pad_count == 8
