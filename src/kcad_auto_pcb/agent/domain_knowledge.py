"""Domain knowledge: infer PCB parameters from actual footprint geometry.

Physics-based: uses real component footprint areas, not preset guesses.
Works for any design - original or conventional.
LLM optional: provides scene-specific tuning when available.
"""

from __future__ import annotations
import math
from typing import Optional
from collections import Counter

# ── Footprint physical sizes (mm² per component body) ─────────────

FOOTPRINT_AREA = {
    # Chip resistors/capacitors (body LxW in mm)
    "0201": 0.6 * 0.3,    # 0.18 mm²
    "0402": 1.0 * 0.5,    # 0.50 mm²
    "0603": 1.6 * 0.8,    # 1.28 mm²
    "1608": 1.6 * 0.8,
    "0805": 2.0 * 1.2,    # 2.40 mm²
    "2012": 2.0 * 1.2,
    "1206": 3.2 * 1.6,    # 5.12 mm²

    # Diodes
    "SOD-123": 3.5 * 1.5,     # 5.25 mm²
    "SOD-323": 2.5 * 1.2,     # 3.00 mm²
    "SOT-23": 3.0 * 2.5,      # 7.50 mm²
    "SOT-223": 6.5 * 3.5,     # 22.75 mm²

    # ICs
    "SOIC-8": 5.0 * 4.0,
    "SOIC-16": 10.0 * 4.0,
    "QFN-32": 5.0 * 5.0,      # 25 mm²
    "QFN-48": 7.0 * 7.0,      # 49 mm²
    "TQFP-32": 7.0 * 7.0,
    "TQFP-64": 10.0 * 10.0,
    "BGA": 12.0 * 12.0,
    "DIP-8": 10.0 * 7.0,

    # Other
    "Crystal": 3.2 * 2.5,      # 8 mm²
    "TestPoint": 1.5 * 1.5,    # 2.25 mm²
    "PinHeader": 10.0 * 5.0,   # ~50 mm² (varies by pin count)
    "Inductor": 2.0 * 1.2,     # similar to 0805
    "LED": 1.6 * 0.8,          # similar to 0603
    "default": 3.0 * 2.0,      # 6 mm² generic
}


def footprint_area(fp_name: str) -> float:
    """Get physical body area (mm²) for a footprint name."""
    for key, area in FOOTPRINT_AREA.items():
        if key.lower() in fp_name.lower():
            return area
    return FOOTPRINT_AREA["default"]


def analyze_footprints(footprint_map: dict[str, str]) -> dict:
    """Analyze a ref->footprint_name map and return distribution."""
    sizes = Counter()
    for ref, fp_name in footprint_map.items():
        for key in FOOTPRINT_AREA:
            if key.lower() in fp_name.lower():
                sizes[key] += 1
                break
        else:
            sizes["default"] += 1

    total_components = sum(sizes.values())
    total_body_area = sum(count * FOOTPRINT_AREA.get(k, 6.0) for k, count in sizes.items())

    return {
        "total_components": total_components,
        "total_body_area_mm2": round(total_body_area, 1),
        "distribution": dict(sizes.most_common()),
    }


def infer_board_params(
    component_count: int = 0,
    footprint_map: dict[str, str] | None = None,
    application_context: str = "",
) -> dict:
    """Compute board parameters from actual footprint data.

    Args:
        component_count: Total components (used if footprint_map not provided)
        footprint_map: {ref: "Capacitor_SMD:C_0603_1608Metric", ...}
        application_context: Optional scene hint for aspect ratio tuning

    Returns dict with width, height, layers, traces, vias, density.
    """
    # Step 1: Total component body area from real footprints
    if footprint_map:
        analysis = analyze_footprints(footprint_map)
        total_body_area = analysis["total_body_area_mm2"]
        n = analysis["total_components"]
    else:
        n = component_count
        total_body_area = n * 4.0  # 4mm² default per component

    # Step 2: Pad area + keepout zone (~3x body area for 0603, ~2x for larger)
    # Each component needs pad area + keepout for assembly
    avg_pad_mult = 2.5 if n < 30 else 2.0  # smaller designs need proportionally more space
    placement_area = total_body_area * avg_pad_mult

    # Step 3: Routing channels (space between components for traces)
    # Dense: 0.5x placement, Normal: 1.0x, Spacious: 2.0x
    routing_mult = 0.8 if n > 100 else 1.2
    total_area = placement_area * (1.0 + routing_mult)

    # Step 4: Determine dimensions from area + aspect ratio
    aspect = _infer_aspect(application_context, n)
    width = max(25, math.sqrt(total_area * aspect))
    height = max(20, total_area / max(width, 1))

    # Step 5: Layer count from density
    density = n / (total_area / 100)  # comps/cm²
    if density > 12:
        layers, tw_sig, tw_pwr, via_sz, via_drill = 8, 0.10, 0.20, 0.3, 0.15
    elif density > 8:
        layers, tw_sig, tw_pwr, via_sz, via_drill = 6, 0.10, 0.20, 0.3, 0.15
    elif density > 5:
        layers, tw_sig, tw_pwr, via_sz, via_drill = 4, 0.12, 0.25, 0.4, 0.2
    else:
        layers, tw_sig, tw_pwr, via_sz, via_drill = 2, 0.20, 0.50, 0.6, 0.3

    return {
        "width": round(width),
        "height": round(height),
        "layers": layers,
        "density_comps_per_cm2": round(density, 1),
        "total_area_cm2": round(total_area / 100, 1),
        "component_body_area_mm2": round(total_body_area, 1),
        "trace_width_signal": tw_sig,
        "trace_width_power": tw_pwr,
        "via_size": via_sz,
        "via_drill": via_drill,
        "component_count": n,
    }


def _infer_aspect(context: str, n: int) -> float:
    """Infer W/H ratio from context description."""
    c = context.lower()
    if any(k in c for k in ["眼镜", "glasses", "手环", "wrist", "笔", "pen"]):
        return 2.5
    if any(k in c for k in ["桌面", "desktop", "副屏", "display", "面板", "panel"]):
        return 2.0
    if any(k in c for k in ["传感器", "sensor", "tag", "标签"]):
        return 1.0
    if any(k in c for k in ["电池", "battery", "配重", "power"]):
        return 1.2
    # Default: auto from component count
    return 1.8 if n > 50 else 1.5


# ── Public API ─────────────────────────────────────────────────────

def get_design_params(
    component_count: int = 0,
    footprint_map: dict[str, str] | None = None,
    application_context: str = "",
) -> dict:
    """Main entry point: component data → board parameters.

    Usage:
        # From parsed schematic:
        fp_map = {ref: comp.footprint_name for ref, comp in design.components.items()}
        params = get_design_params(footprint_map=fp_map, application_context="智能眼镜主板")

        # Quick estimate:
        params = get_design_params(component_count=168)
    """
    return infer_board_params(
        component_count=component_count,
        footprint_map=footprint_map,
        application_context=application_context,
    )
