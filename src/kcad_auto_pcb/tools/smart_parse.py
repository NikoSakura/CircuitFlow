"""smart_parse: schematic → physical spec with exact coordinates.

Parses the schematic, resolves footprints, computes pad positions,
board outline, and component placement grid.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import math

from ..geometry.point import Point
from ..geometry.rect import Rect
from ..schematic.parser import SchematicParser
from ..schematic.pdf_parser import PDFSchematicParser
from ..footprint.cache import FootprintCache
from .spec import BoardSpec, ComponentSpec


@dataclass
class PhysicalSpec:
    """Complete physical PCB specification with exact coordinates."""
    board: BoardSpec
    footprint_map: Dict[str, str]       # ref → footprint_name
    pad_positions: Dict[str, List[Tuple[float, float]]]  # ref → [(pad_x, pad_y), ...]
    pad_numbers: Dict[str, List[str]]   # ref → [pad_number, ...]
    component_size: Dict[str, Tuple[float, float]]  # ref → (width_mm, height_mm)
    nets: List[dict]                     # [{name, is_power, pins: [(ref, pin_number), ...]}]
    warnings: List[str]


def smart_parse(schematic_path: str | Path) -> PhysicalSpec:
    """Parse schematic and generate complete physical spec.

    Returns exact coordinates for every pad, component size, and
    board dimensions. Ready for routing.
    """
    path = Path(schematic_path)
    suffix = path.suffix.lower()

    # Parse schematic
    if suffix == ".kicad_sch":
        parser = SchematicParser()
        design = parser.parse(str(path))
    elif suffix == ".pdf":
        parser = PDFSchematicParser()
        design = parser.parse(str(path))
    else:
        raise ValueError(f"Unsupported format: {suffix}")

    fp_cache = FootprintCache()
    footprint_map = {}
    pad_positions = {}
    pad_numbers = {}
    component_size = {}
    warnings = []

    # Resolve footprints and extract pad geometry
    for ref, comp in design.components.items():
        fp_name = comp.footprint_name
        footprint_map[ref] = fp_name

        fp = fp_cache.get(fp_name) if fp_name else None
        if fp:
            sizes = [(pad.position.x, pad.position.y) for pad in fp.pads]
            pad_positions[ref] = sizes
            pad_numbers[ref] = [pad.number for pad in fp.pads]
            component_size[ref] = (fp.body_size[0], fp.body_size[1])
        else:
            # Generic fallback
            pad_positions[ref] = [(-0.85, 0), (0.85, 0)]  # 0603 default
            pad_numbers[ref] = ["1", "2"]
            component_size[ref] = (3, 2)
            if ref[0].upper() in ("U", "J"):
                pad_positions[ref] = [(-1.905, -2.54), (1.905, -2.54),
                                      (-1.905, 2.54), (1.905, 2.54)]
                pad_numbers[ref] = ["1", "2", "3", "4"]
                component_size[ref] = (8, 8)
            warnings.append(f"{ref}: footprint '{fp_name}' not found, using generic")

    # Compute board size
    total_area = sum(w * h for w, h in component_size.values())
    n = len(design.components)
    bw = max(60, min(200, int(math.sqrt(total_area * 2.5) + 20)))
    bh = max(40, min(160, int(bw * 0.7)))

    # Grid placement: ICs first, then grouped
    def sort_key(ref):
        fp = footprint_map.get(ref, "")
        prefix = ref.rstrip("0123456789").upper() if ref else ""
        if "U" in prefix: return (0, ref)
        if "J" in prefix: return (1, ref)
        if "Q" in prefix: return (2, ref)
        if "Y" in prefix or "X" in prefix: return (3, ref)
        if "R" in prefix: return (4, ref)
        if "C" in prefix: return (5, ref)
        if "L" in prefix: return (6, ref)
        if "D" in prefix or "LED" in prefix: return (7, ref)
        return (8, ref)

    sorted_refs = sorted(design.components.keys(), key=sort_key)
    # Filter out virtual power symbols
    sorted_refs = [r for r in sorted_refs if not r.startswith("PWR") and not r.startswith("#")]
    cols = max(1, int((bw - 10) / 14))
    margin = 8
    components = []
    for i, ref in enumerate(sorted_refs):
        row, col = i // cols, i % cols
        x = margin + col * 14 + 7
        y = margin + row * 12 + 6
        components.append(ComponentSpec(ref=ref, x=x, y=y))

    # Extract nets with pin info
    nets = []
    for name, net in sorted(design.nets.items()):
        nets.append({
            "name": name,
            "is_power": net.is_power,
            "pins": [(p.component_ref, p.pin_number) for p in net.pins],
        })

    spec = BoardSpec(
        name=path.stem,
        width=bw, height=bh,
        layers=2,
        components=components,
    )

    return PhysicalSpec(
        board=spec,
        footprint_map=footprint_map,
        pad_positions=pad_positions,
        pad_numbers=pad_numbers,
        component_size=component_size,
        nets=nets,
        warnings=warnings,
    )
