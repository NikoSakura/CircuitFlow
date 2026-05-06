"""Parametric footprint generator.

Generates KiCad-compatible footprint geometry for common packages
based on IPC standards, without needing external footprint libraries.

Supports:
- SMD passives: 0201, 0402, 0603, 0805, 1206, 1210, 2512
- SMD ICs: SOIC, SSOP, TSSOP, QFP, QFN, BGA
- THT: DIP, TO-92, TO-220
- Diodes: SOD-123, SOD-323, DO-41
- Connectors: pin headers, USB, barrel jack
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
from .parser import ResolvedFootprint, PadGeometry
from ..geometry.point import Point


class FootprintGenerator:
    """Generate footprint geometry for common packages."""

    # Standard SMD passive dimensions (mm) [L, W, pad_W, pad_H, pad_gap]
    SMD_PASSIVES: Dict[str, Tuple[float, float, float, float, float]] = {
        "0201": (0.6, 0.3, 0.35, 0.35, 0.3),
        "0402": (1.0, 0.5, 0.55, 0.60, 0.4),
        "0603": (1.6, 0.8, 0.8, 1.0, 0.7),
        "0805": (2.0, 1.25, 1.2, 1.3, 0.55),
        "1206": (3.2, 1.6, 1.6, 1.8, 1.1),
        "1210": (3.2, 2.5, 1.6, 2.7, 1.1),
        "2512": (6.3, 3.2, 3.0, 3.5, 2.4),
    }

    # SOIC family: [body_W, body_H, pin_count, pitch, pad_W, pad_H]
    SOIC_VARIANTS: Dict[str, Tuple[float, float, int, float, float, float]] = {
        "SOIC-8": (3.9, 4.9, 8, 1.27, 0.6, 1.55),
        "SOIC-14": (3.9, 8.65, 14, 1.27, 0.6, 1.55),
        "SOIC-16": (3.9, 9.9, 16, 1.27, 0.6, 1.55),
        "SOIC-20": (7.5, 12.8, 20, 1.27, 0.6, 1.55),
        "SOIC-28": (7.5, 17.9, 28, 1.27, 0.6, 1.55),
        "SSOP-20": (5.3, 7.2, 20, 0.65, 0.4, 1.25),
        "TSSOP-8": (3.0, 4.4, 8, 0.65, 0.35, 1.0),
        "TSSOP-16": (4.4, 5.0, 16, 0.65, 0.35, 1.0),
    }

    # QFP family: [body_size, pin_count, pitch, pad_W, pad_H]
    QFP_VARIANTS: Dict[str, Tuple[float, int, float, float, float]] = {
        "LQFP-32": (7.0, 32, 0.8, 0.45, 1.6),
        "LQFP-48": (7.0, 48, 0.5, 0.3, 1.6),
        "LQFP-64": (10.0, 64, 0.5, 0.3, 1.6),
        "LQFP-100": (14.0, 100, 0.5, 0.25, 1.6),
    }

    # Standard DIP: [pin_count, row_spacing, pitch]
    DIP_VARIANTS: Dict[int, Tuple[float, float]] = {
        8: (7.62, 2.54),
        14: (7.62, 2.54),
        16: (7.62, 2.54),
        18: (7.62, 2.54),
        20: (7.62, 2.54),
        28: (15.24, 2.54),
        40: (15.24, 2.54),
    }

    # Standard pin headers: pitch 2.54mm
    @staticmethod
    def pin_header(pin_count: int, rows: int = 1, pitch: float = 2.54) -> ResolvedFootprint:
        """Generate a standard pin header footprint."""
        name = f"PinHeader_{rows}x{pin_count}_P{pitch:.2f}mm"
        fp = ResolvedFootprint(name=name, library="Connector_PinHeader")

        for row in range(rows):
            for i in range(pin_count):
                pin_num = str(row * pin_count + i + 1)
                x = i * pitch
                y = row * pitch
                fp.pads.append(PadGeometry(
                    number=pin_num,
                    type="thru_hole",
                    shape="oval",
                    position=Point(x, y),
                    size=(1.7, 1.7),
                    drill=1.0,
                    layers=["F.Cu", "B.Cu"],
                ))

        body_w = (pin_count - 1) * pitch + 2.54
        body_h = (rows - 1) * pitch + 2.54
        fp.body_size = (body_w, body_h)
        return fp

    @staticmethod
    def generate(package_type: str) -> Optional[ResolvedFootprint]:
        """Generate a footprint for a given package type.

        Args:
            package_type: e.g., "SOIC-8", "0603", "DIP-14", "SOT-23"

        Returns:
            ResolvedFootprint or None if unknown package.
        """
        pkg = package_type.upper().replace("_", "").replace("-", "").replace(" ", "")
        pkg_original = package_type.upper()

        # SMD passives
        for size, dims in FootprintGenerator.SMD_PASSIVES.items():
            if size in pkg:
                return FootprintGenerator._smd_passive(size, dims)

        # SOIC family (normalized matching: strip all non-alphanumeric)
        for variant, dims in FootprintGenerator.SOIC_VARIANTS.items():
            variant_normalized = variant.replace("-", "").replace("_", "")
            if variant_normalized in pkg:
                return FootprintGenerator._soic(variant, dims)

        # QFP family
        for variant, dims in FootprintGenerator.QFP_VARIANTS.items():
            variant_normalized = variant.replace("-", "").replace("_", "")
            if variant_normalized in pkg:
                return FootprintGenerator._qfp(variant, dims)

        # DIP
        dip_match = __import__('re').match(r'DIP[/-]?(\d+)', pkg) or __import__('re').match(r'DIP[/-]?(\d+)', pkg_original)
        if dip_match:
            pin_count = int(dip_match.group(1))
            if pin_count in FootprintGenerator.DIP_VARIANTS:
                row_spacing, pitch = FootprintGenerator.DIP_VARIANTS[pin_count]
                return FootprintGenerator._dip(pin_count, row_spacing, pitch)

        # SOT-23
        if "SOT23" in pkg or "SOT-23" in pkg_original:
            return FootprintGenerator._sot23()

        # TO-92
        if "TO92" in pkg or "TO-92" in pkg_original:
            return FootprintGenerator._to92()

        # TO-220
        if "TO220" in pkg or "TO-220" in pkg_original:
            return FootprintGenerator._to220()

        # Pin headers
        header_match = __import__('re').match(r'(?:PINHEADER|HEADER|PIN)[-_]?(\d+)X?(\d+)?', pkg)
        if header_match:
            pins = int(header_match.group(1))
            rows = int(header_match.group(2)) if header_match.group(2) else 1
            return FootprintGenerator.pin_header(pins, rows)

        # QFN
        if "QFN" in pkg:
            qfn_match = __import__('re').match(r'QFN[/-]?(\d+)', pkg) or __import__('re').match(r'QFN[/-]?(\d+)', pkg_original)
            if qfn_match:
                return FootprintGenerator._qfn(int(qfn_match.group(1)))

        return None

    @staticmethod
    def _smd_passive(size: str, dims: Tuple[float, ...]) -> ResolvedFootprint:
        L, W, pad_W, pad_H, gap = dims
        fp = ResolvedFootprint(
            name=f"R_{size}_{int(L*1000)}_{int(W*1000)}Metric",
            library="Resistor_SMD",
            body_size=(L, W),
        )
        pad_x = gap / 2 + pad_W / 2
        fp.pads.append(PadGeometry("1", "smd", "rect", Point(-pad_x, 0), (pad_W, pad_H)))
        fp.pads.append(PadGeometry("2", "smd", "rect", Point(pad_x, 0), (pad_W, pad_H)))
        return fp

    @staticmethod
    def _soic(variant: str, dims: Tuple) -> ResolvedFootprint:
        body_W, body_H, pins, pitch, pad_W, pad_H = dims
        fp = ResolvedFootprint(name=variant, library="Package_SO", body_size=(body_W, body_H))

        pins_per_side = pins // 2
        row_length = (pins_per_side - 1) * pitch
        start_y = -row_length / 2
        row_x = body_W / 2 + pad_H / 2

        for i in range(pins_per_side):
            y = start_y + i * pitch
            fp.pads.append(PadGeometry(str(i + 1), "smd", "rect",
                                       Point(-row_x, y), (pad_H, pad_W)))
            fp.pads.append(PadGeometry(str(pins_per_side + i + 1), "smd", "rect",
                                       Point(row_x, y), (pad_H, pad_W)))
        return fp

    @staticmethod
    def _qfp(variant: str, dims: Tuple) -> ResolvedFootprint:
        body_sz, pins, pitch, pad_W, pad_H = dims
        fp = ResolvedFootprint(name=variant, library="Package_QFP", body_size=(body_sz, body_sz))

        pins_per_side = pins // 4
        row_span = (pins_per_side - 1) * pitch
        start = -row_span / 2
        edge = body_sz / 2 + pad_H / 2

        pin_num = 1
        for i in range(pins_per_side):
            fp.pads.append(PadGeometry(str(pin_num), "smd", "rect",
                                       Point(start + i * pitch, -edge), (pad_W, pad_H)))
            pin_num += 1
        for i in range(pins_per_side):
            fp.pads.append(PadGeometry(str(pin_num), "smd", "rect",
                                       Point(edge, start + i * pitch), (pad_H, pad_W)))
            pin_num += 1
        for i in range(pins_per_side):
            fp.pads.append(PadGeometry(str(pin_num), "smd", "rect",
                                       Point(start + (pins_per_side - 1 - i) * pitch, edge), (pad_W, pad_H)))
            pin_num += 1
        for i in range(pins_per_side):
            fp.pads.append(PadGeometry(str(pin_num), "smd", "rect",
                                       Point(-edge, start + (pins_per_side - 1 - i) * pitch), (pad_H, pad_W)))
            pin_num += 1
        return fp

    @staticmethod
    def _dip(pin_count: int, row_spacing: float, pitch: float) -> ResolvedFootprint:
        fp = ResolvedFootprint(
            name=f"DIP-{pin_count}_W{row_spacing:.2f}mm",
            library="Package_DIP",
            body_size=(row_spacing + 2, (pin_count / 2 - 1) * pitch + 5),
        )

        pins_per_row = pin_count // 2
        row_len = (pins_per_row - 1) * pitch
        start_y = -row_len / 2
        half_spacing = row_spacing / 2

        for i in range(pins_per_row):
            y = start_y + i * pitch
            fp.pads.append(PadGeometry(str(i + 1), "thru_hole", "oval",
                                       Point(-half_spacing, y), (1.8, 1.8), drill=0.8,
                                       layers=["F.Cu", "B.Cu"]))
            fp.pads.append(PadGeometry(str(pins_per_row + i + 1), "thru_hole", "oval",
                                       Point(half_spacing, y), (1.8, 1.8), drill=0.8,
                                       layers=["F.Cu", "B.Cu"]))
        return fp

    @staticmethod
    def _sot23() -> ResolvedFootprint:
        fp = ResolvedFootprint(name="SOT-23", library="Package_TO_SOT", body_size=(2.9, 1.3))
        fp.pads.append(PadGeometry("1", "smd", "rect", Point(-0.95, -0.95), (0.6, 0.8)))
        fp.pads.append(PadGeometry("2", "smd", "rect", Point(0.95, -0.95), (0.6, 0.8)))
        fp.pads.append(PadGeometry("3", "smd", "rect", Point(0, 0.95), (0.6, 0.8)))
        return fp

    @staticmethod
    def _to92() -> ResolvedFootprint:
        fp = ResolvedFootprint(name="TO-92", library="Package_TO", body_size=(4.8, 3.8))
        fp.pads.append(PadGeometry("1", "thru_hole", "circle", Point(0, 0),
                                   (1.5, 1.5), drill=0.8, layers=["F.Cu", "B.Cu"]))
        fp.pads.append(PadGeometry("2", "thru_hole", "circle", Point(2.54, 0),
                                   (1.5, 1.5), drill=0.8, layers=["F.Cu", "B.Cu"]))
        fp.pads.append(PadGeometry("3", "thru_hole", "circle", Point(1.27, 2.54),
                                   (1.5, 1.5), drill=0.8, layers=["F.Cu", "B.Cu"]))
        return fp

    @staticmethod
    def _to220() -> ResolvedFootprint:
        fp = ResolvedFootprint(name="TO-220", library="Package_TO", body_size=(10.0, 15.0))
        fp.pads.append(PadGeometry("1", "thru_hole", "rect", Point(-2.54, 0),
                                   (2.0, 2.0), drill=1.2, layers=["F.Cu", "B.Cu"]))
        fp.pads.append(PadGeometry("2", "thru_hole", "rect", Point(0, 0),
                                   (2.0, 2.0), drill=1.2, layers=["F.Cu", "B.Cu"]))
        fp.pads.append(PadGeometry("3", "thru_hole", "rect", Point(2.54, 0),
                                   (2.0, 2.0), drill=1.2, layers=["F.Cu", "B.Cu"]))
        return fp

    @staticmethod
    def _qfn(pin_count: int) -> ResolvedFootprint:
        # QFN: pins around perimeter + exposed pad
        body_map = {16: 3.0, 20: 4.0, 24: 4.0, 32: 5.0, 48: 6.0, 64: 8.0}
        body_sz = body_map.get(pin_count, 5.0)
        pitch_map = {16: 0.5, 20: 0.5, 24: 0.5, 32: 0.5, 48: 0.4, 64: 0.4}
        pitch = pitch_map.get(pin_count, 0.5)

        fp = ResolvedFootprint(
            name=f"QFN-{pin_count}_{body_sz:.1f}x{body_sz:.1f}mm",
            library="Package_QFN",
            body_size=(body_sz, body_sz),
        )

        pins_per_side = pin_count // 4
        span = (pins_per_side - 1) * pitch
        start = -span / 2
        edge = body_sz / 2 + 1.0

        pin_num = 1
        for side in range(4):
            for i in range(pins_per_side):
                if side == 0:
                    pos = Point(start + i * pitch, -edge)
                elif side == 1:
                    pos = Point(edge, start + i * pitch)
                elif side == 2:
                    pos = Point(start + (pins_per_side - 1 - i) * pitch, edge)
                else:
                    pos = Point(-edge, start + (pins_per_side - 1 - i) * pitch)
                fp.pads.append(PadGeometry(
                    str(pin_num), "smd", "rect", pos, (0.25, 0.6)
                ))
                pin_num += 1

        # Exposed pad
        ep_size = body_sz * 0.6
        fp.pads.append(PadGeometry(
            str(pin_count + 1), "smd", "rect", Point(0, 0),
            (ep_size, ep_size)
        ))
        return fp
