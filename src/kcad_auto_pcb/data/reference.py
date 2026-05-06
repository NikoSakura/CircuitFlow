"""Reference design templates for common PCB layout patterns.

These encode layout knowledge that LLMs would otherwise need to
rediscover from scratch, saving tokens and improving quality.

Each template describes a proven layout pattern that can be
instantiated with specific component values.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from ..geometry.point import Point


@dataclass
class LayoutRule:
    """A single layout constraint or recommendation."""
    rule_type: str  # "proximity", "orientation", "trace_width", "keepout", "via_stitching"
    description: str
    params: dict = field(default_factory=dict)


@dataclass
class ReferenceTemplate:
    """A reference layout template for a specific circuit topology."""

    name: str
    description: str
    topology: str  # "buck_converter", "ldo", "crystal", "diff_pair", "rf_match", etc.
    rules: List[LayoutRule] = field(default_factory=list)
    # Relative component positions (ratios of board space)
    relative_placements: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    critical_traces: List[dict] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Convert template to a compact text block for LLM prompts (~50-100 tokens)."""
        lines = [f"Template: {self.name} ({self.topology})"]
        for rule in self.rules:
            lines.append(f"  RULE: {rule.description}")
        return "\n".join(lines)


# ── Curated reference templates ──────────────────────────────────────────

REFERENCE_TEMPLATES: Dict[str, ReferenceTemplate] = {
    "buck_converter": ReferenceTemplate(
        name="Buck Converter Layout",
        description="Standard step-down DC-DC converter layout minimizing loop area",
        topology="buck_converter",
        rules=[
            LayoutRule("proximity",
                "Input capacitor must be within 2mm of IC VIN and GND pins"),
            LayoutRule("trace_width",
                "Power path traces (VIN→SW→L→VOUT) width >= 0.5mm per 1A current"),
            LayoutRule("keepout",
                "No signals under inductor and SW node area (noisy switching node)"),
            LayoutRule("orientation",
                "Feedback trace route away from inductor and SW node, use Kelvin connection"),
            LayoutRule("via_stitching",
                "At least 4 GND vias connecting IC thermal pad to ground plane"),
        ],
        critical_traces=[
            {"net": "SW", "description": "Switching node - keep as short as possible, wide trace"},
            {"net": "FB", "description": "Feedback - route away from noise sources, thin trace OK"},
        ],
    ),

    "ldo_regulator": ReferenceTemplate(
        name="LDO Regulator Layout",
        description="Low-dropout linear regulator layout for clean output",
        topology="ldo",
        rules=[
            LayoutRule("proximity",
                "Input capacitor within 3mm of VIN pin, output capacitor within 3mm of VOUT pin"),
            LayoutRule("trace_width",
                "VIN trace: 0.3mm per 100mA; VOUT trace: 0.3mm per 100mA"),
            LayoutRule("orientation",
                "Place LDO between input and output capacitors for cleanest layout"),
        ],
    ),

    "crystal_oscillator": ReferenceTemplate(
        name="Crystal Oscillator Layout",
        description="High-frequency crystal oscillator for MCU clock",
        topology="crystal",
        rules=[
            LayoutRule("proximity",
                "Crystal within 10mm of MCU oscillator pins, load capacitors adjacent to crystal"),
            LayoutRule("keepout",
                "No digital signals crossing under crystal or its traces"),
            LayoutRule("trace_width",
                "Crystal traces: 0.15-0.2mm, matched length (<2mm difference)"),
            LayoutRule("orientation",
                "Guard ring (GND) around crystal and traces for EMI shielding"),
        ],
    ),

    "usb_diff_pair": ReferenceTemplate(
        name="USB 2.0 Differential Pair",
        description="USB D+/D- 90Ω differential pair routing",
        topology="diff_pair",
        rules=[
            LayoutRule("trace_width", "D+ and D- width: 0.3mm, spacing: 0.15mm for 90Ω on FR-4"),
            LayoutRule("orientation", "Route D+ and D- together with equal length (<0.5mm skew)"),
            LayoutRule("keepout", "No other signals within 0.5mm of differential pair"),
            LayoutRule("proximity", "ESD protection diode within 5mm of USB connector"),
        ],
    ),

    "decoupling_strategy": ReferenceTemplate(
        name="Decoupling Capacitor Strategy",
        description="General decoupling capacitor placement guidelines",
        topology="decoupling",
        rules=[
            LayoutRule("proximity", "100nF caps within 3mm of each power pin"),
            LayoutRule("proximity", "10uF bulk capacitor within 10mm of IC"),
            LayoutRule("trace_width", "VCC trace to decoupling cap >= 0.3mm"),
            LayoutRule("orientation",
                "Route VCC → capacitor pad → IC pin (not VCC → T-junction → cap + IC)"),
        ],
    ),

    "thermal_management": ReferenceTemplate(
        name="Thermal Management",
        description="Heat dissipation layout for power components",
        topology="thermal",
        rules=[
            LayoutRule("keepout",
                "Power components (regulators, MOSFETs) spaced >= 10mm from temperature-sensitive parts"),
            LayoutRule("via_stitching",
                "Thermal vias under hot components: grid of 0.3mm drills at 1mm spacing"),
            LayoutRule("orientation",
                "Place hot components near board edge for better airflow"),
        ],
    ),
}


def get_reference_for_components(components: List[tuple]) -> List[ReferenceTemplate]:
    """Auto-select relevant reference templates based on components used.

    Args:
        components: list of (reference, value) tuples, e.g., [("U1", "AMS1117-3.3"), ("Y1", "16MHz")]

    Returns:
        List of applicable ReferenceTemplates.
    """
    templates = []

    values = " ".join(v for _, v in components).upper()

    # Detect topology by component values
    if any(kw in values for kw in ("LM2596", "MP1584", "TPS543", "BUCK", "DC-DC")):
        templates.append(REFERENCE_TEMPLATES["buck_converter"])
    if any(kw in values for kw in ("AMS1117", "LM1117", "LDO", "REGULATOR", "78")):
        templates.append(REFERENCE_TEMPLATES["ldo_regulator"])
    if any(kw in values for kw in ("CRYSTAL", "OSC", "XTAL", "MHz", "kHz")):
        templates.append(REFERENCE_TEMPLATES["crystal_oscillator"])
    if any(kw in values for kw in ("USB", "D+", "D-", "DP", "DM")):
        templates.append(REFERENCE_TEMPLATES["usb_diff_pair"])

    # Always include decoupling and thermal
    templates.append(REFERENCE_TEMPLATES["decoupling_strategy"])
    templates.append(REFERENCE_TEMPLATES["thermal_management"])

    return templates
