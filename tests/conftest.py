import pytest
from pathlib import Path
from kcad_auto_pcb.geometry.point import Point
from kcad_auto_pcb.geometry.rect import Rect
from kcad_auto_pcb.config.settings import AppSettings


@pytest.fixture
def examples_dir():
    return Path(__file__).parent.parent / "examples"


@pytest.fixture
def simple_schematic_path(examples_dir):
    return examples_dir / "simple_led.kicad_sch"


@pytest.fixture
def ne555_schematic_path(examples_dir):
    return examples_dir / "ne555_astable.kicad_sch"


@pytest.fixture
def board_bounds():
    return Rect(0, 0, 100, 80)


@pytest.fixture
def settings():
    return AppSettings()
