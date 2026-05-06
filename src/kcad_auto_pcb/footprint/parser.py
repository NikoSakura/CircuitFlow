from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from ..geometry.point import Point
from ..geometry.rect import Rect


@dataclass
class PadGeometry:
    number: str
    type: str  # "smd", "thru_hole"
    shape: str  # "rect", "roundrect", "circle", "oval"
    position: Point
    size: Tuple[float, float]  # (w, h) in mm
    drill: Optional[float] = None
    layers: List[str] = field(default_factory=list)


@dataclass
class ResolvedFootprint:
    name: str
    library: str = ""
    body_size: Tuple[float, float] = (0, 0)
    pads: List[PadGeometry] = field(default_factory=list)

    @property
    def courtyard(self) -> Rect:
        w, h = self.body_size
        margin = 0.5
        return Rect(-w / 2 - margin, -h / 2 - margin, w + 2 * margin, h + 2 * margin)

    @property
    def pad_count(self) -> int:
        return len(self.pads)


class FootprintParser:
    """Parse .kicad_mod footprint files to extract pad geometry.

    Uses a lightweight approach: regex-based S-expression parser
    that extracts pad positions and sizes without heavy dependencies.
    """

    # Default footprints for common SMD passives (no KiCad lib needed)
    BUILTIN: Dict[str, dict] = {
        "Resistor_SMD:R_0603_1608Metric": {
            "body": (1.6, 0.8),
            "pads": [
                {"num": "1", "type": "smd", "shape": "rect", "pos": (-0.85, 0), "size": (0.8, 1.0)},
                {"num": "2", "type": "smd", "shape": "rect", "pos": (0.85, 0), "size": (0.8, 1.0)},
            ],
        },
        "Capacitor_SMD:C_0603_1608Metric": {
            "body": (1.6, 0.8),
            "pads": [
                {"num": "1", "type": "smd", "shape": "rect", "pos": (-0.85, 0), "size": (0.8, 1.0)},
                {"num": "2", "type": "smd", "shape": "rect", "pos": (0.85, 0), "size": (0.8, 1.0)},
            ],
        },
        "LED_SMD:LED_0603_1608Metric": {
            "body": (1.6, 0.8),
            "pads": [
                {"num": "1", "type": "smd", "shape": "rect", "pos": (-0.85, 0), "size": (0.8, 1.0)},
                {"num": "2", "type": "smd", "shape": "rect", "pos": (0.85, 0), "size": (0.8, 1.0)},
            ],
        },
        "Package_DIP:DIP-8_W7.62mm": {
            "body": (9.8, 6.6),
            "pads": [
                {"num": str(i + 1), "type": "thru_hole", "shape": "oval",
                 "pos": (-3.81 + 2.54 * (i % 4), -4.0 if i < 4 else 4.0),
                 "size": (1.8, 1.8), "drill": 0.8}
                for i in range(8)
            ],
        },
    }

    def resolve(self, footprint_name: str) -> Optional[ResolvedFootprint]:
        """Resolve a footprint name to its geometry data.

        1. Built-in library (fast, no I/O)
        2. Parametric generator (for common packages: SOIC-8, SOT-23, QFN-32, etc.)
        3. KiCad library search (filesystem .kicad_mod)
        4. Generic fallback
        """
        if footprint_name in self.BUILTIN:
            return self._from_builtin(footprint_name)

        # Try parametric generator by extracting package type from footprint name
        fp = self._try_generate(footprint_name)
        if fp:
            return fp

        return self._from_library(footprint_name)

    def resolve_batch(self, names: List[str]) -> Dict[str, ResolvedFootprint]:
        result = {}
        for name in set(names):
            fp = self.resolve(name)
            if fp:
                result[name] = fp
        return result

    def _from_builtin(self, name: str) -> ResolvedFootprint:
        data = self.BUILTIN[name]
        lib, _, fp_name = name.partition(":")
        fp = ResolvedFootprint(
            name=fp_name,
            library=lib,
            body_size=data["body"],
        )
        for pad in data["pads"]:
            fp.pads.append(PadGeometry(
                number=pad["num"],
                type=pad["type"],
                shape=pad["shape"],
                position=Point(pad["pos"][0], pad["pos"][1]),
                size=pad["size"],
                drill=pad.get("drill"),
                layers=["F.Cu", "B.Cu"] if pad["type"] == "thru_hole" else ["F.Cu"],
            ))
        return fp

    def _from_library(self, name: str) -> Optional[ResolvedFootprint]:
        """Try to load footprint from KiCad library paths."""
        import os
        lib, _, fp_name = name.partition(":")

        search_paths = [
            os.environ.get("KICAD_FOOTPRINT_DIR", ""),
            os.environ.get("KISYS3DMOD", "").replace("3dmodels", "footprints"),
            os.environ.get("KICAD7_FOOTPRINT_DIR", ""),
            os.environ.get("KICAD8_FOOTPRINT_DIR", ""),
            os.path.expandvars("%APPDATA%\\kicad\\8.0\\footprints"),
            os.path.expandvars("%APPDATA%\\kicad\\7.0\\footprints"),
            os.path.expandvars("%APPDATA%\\kicad\\footprints"),
            os.path.expandvars("$HOME/.local/share/kicad/8.0/footprints"),
            os.path.expandvars("$HOME/.local/share/kicad/footprints"),
            "/usr/share/kicad/footprints",
            "C:\\Program Files\\KiCad\\8.0\\share\\kicad\\footprints",
            "C:\\Program Files\\KiCad\\share\\kicad\\footprints",
        ]

        for base_dir in search_paths:
            if not base_dir or not os.path.isdir(base_dir):
                continue
            for root, _dirs, files in os.walk(base_dir):
                for f in files:
                    if f == f"{fp_name}.kicad_mod":
                        return self._parse_kicad_mod(os.path.join(root, f), name)

        # Fallback: generate a generic footprint
        return self._generate_generic(name)

    def _parse_kicad_mod(self, path: str, name: str) -> Optional[ResolvedFootprint]:
        """Parse a .kicad_mod file."""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return None

        fp = ResolvedFootprint(name=name)
        import re
        # Extract pads
        for m in re.finditer(
            r'\(pad\s+"([^"]+)"\s+(smd|thru_hole)\s+(rect|roundrect|circle|oval)'
            r'\s+\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)'
            r'\s+\(size\s+([-\d.]+)\s+([-\d.]+)\)'
            r'(?:\s+\(drill(?:\s+([-\d.]+))?[\s)]*)?',
            text
        ):
            fp.pads.append(PadGeometry(
                number=m.group(1),
                type=m.group(2),
                shape=m.group(3),
                position=Point(float(m.group(4)), float(m.group(5))),
                size=(float(m.group(7)), float(m.group(8))),
                drill=float(m.group(9)) if m.group(9) else None,
            ))

        return fp if fp.pads else None

    def _try_generate(self, name: str) -> Optional[ResolvedFootprint]:
        """Try to generate footprint from package type in the name."""
        from .generator import FootprintGenerator

        # Extract package hints from the footprint name
        # e.g., "Package_SO:SOIC-8_3.9x4.9mm" -> "SOIC-8"
        package_hints = []
        _, _, fp_part = name.partition(":")
        if not fp_part:
            fp_part = name

        # Split on common separators to extract package keywords
        parts = fp_part.replace("_", "-").replace(":", "-").split("-")

        import re
        # Try multi-word patterns first
        for pattern in [r'(SOIC-\d+)', r'(TSSOP-\d+)', r'(SSOP-\d+)',
                        r'(SOT-23)', r'(SOT-223)', r'(TO-92)', r'(TO-220)',
                        r'(L?QFP-\d+)', r'(DIP-\d+)', r'(QFN-\d+)',
                        r'(\d{4})']:  # matches 0201, 0402, 0603, 0805, 1206, 1210, 2512 etc.
            match = re.search(pattern, fp_part, re.IGNORECASE)
            if match:
                package_hints.append(match.group(1))

        # Try individual parts as package hints (handles cases like "SOIC-8" split into parts)
        for part in parts:
            if part.upper() in ("SOIC", "TSSOP", "SSOP", "QFP", "QFN", "LQFP", "TQFP", "DIP"):
                # Look for pin count in adjacent parts
                for p2 in parts:
                    if p2.isdigit():
                        package_hints.append(f"{part.upper()}-{p2}")
                        break

        for hint in package_hints:
            fp = FootprintGenerator.generate(hint)
            if fp:
                return fp

        return None

    def _generate_generic(self, name: str) -> ResolvedFootprint:
        """Generate a conservative generic footprint for unknown components."""
        # Default to 0805 size as safe middle-ground
        fp = ResolvedFootprint(name=name, body_size=(3, 2))
        fp.pads.append(PadGeometry("1", "smd", "rect", Point(-1.2, 0), (1.3, 1.3)))
        fp.pads.append(PadGeometry("2", "smd", "rect", Point(1.2, 0), (1.3, 1.3)))
        return fp
