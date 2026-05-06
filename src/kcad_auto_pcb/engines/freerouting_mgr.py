"""FreeRouting engine manager — delegates autorouting to the FreeRouting Java engine.

Per important.md architecture: LLM must NOT calculate coordinates or paths.
Instead, export DSN → call FreeRouting via subprocess → import SES result.

Supports two paths:
1. pcbnew-native: ExportSpecctraDSN / ImportSpecctraSES (when KiCad Python available)
2. Pure-Python: DSN generation + SES parsing (works without pcbnew)
"""

from __future__ import annotations
import subprocess
import re
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

try:
    import pcbnew
    PCBNEW_AVAILABLE = True
except ImportError:
    pcbnew = None  # type: ignore
    PCBNEW_AVAILABLE = False


class FreeRoutingError(Exception):
    """Raised when the FreeRouting process fails."""


@dataclass
class DSNPadInfo:
    """Pad geometry for DSN export."""
    number: str
    abs_x: float
    abs_y: float
    shape: str = "roundrect"
    size_w: float = 1.0
    size_h: float = 1.0

@dataclass
class DSNComponentInfo:
    """Component placement info for DSN export."""
    reference: str
    footprint_name: str
    x: float
    y: float
    rotation: float  # degrees
    side: str = "front"  # "front" or "back"
    pads: List[DSNPadInfo] = field(default_factory=list)

@dataclass
class SESWireSegment:
    """A wire segment parsed from SES output."""
    x1: float; y1: float
    x2: float; y2: float
    layer: str
    width: float = 0.25

@dataclass
class SESViaInfo:
    """A via parsed from SES output."""
    x: float; y: float
    size: float = 1.0; drill: float = 0.6
    top_layer: str = "F.Cu"; bottom_layer: str = "B.Cu"

@dataclass
class SESNetResult:
    """Routing result for a single net from SES."""
    net_name: str
    wires: List[SESWireSegment] = field(default_factory=list)
    vias: List[SESViaInfo] = field(default_factory=list)

@dataclass
class DSNNetInfo:
    """Net connectivity for DSN export."""
    name: str
    code: int
    pins: List[Tuple[str, str]] = field(default_factory=list)  # (ref, pad_num)


class FreeRoutingManager:
    """Manage FreeRouting Java engine lifecycle and SES import."""

    def __init__(self, jar_path: str = "freerouting.jar", max_passes: int = 15):
        self.jar_path = jar_path
        self.max_passes = max_passes

    # ── Main entry point ───────────────────────────────────────────

    def is_available(self) -> bool:
        """Check whether the FreeRouting JAR and Java runtime are reachable."""
        if not Path(self.jar_path).exists():
            return False
        try:
            subprocess.run(
                ["java", "-version"],
                capture_output=True, text=True, timeout=10,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run_autorouter(
        self,
        dsn_file_path: str | Path,
        output_ses_path: Optional[str | Path] = None,
    ) -> Optional[str]:
        """Run FreeRouting on a Specctra DSN file, producing an SES session file.

        Args:
            dsn_file_path: Path to input .dsn file.
            output_ses_path: Optional explicit output path; auto-derived if omitted.

        Returns:
            Path to the generated .ses file, or None on failure.
        """
        dsn = str(dsn_file_path)
        ses = str(output_ses_path) if output_ses_path else dsn.replace(".dsn", ".ses")

        if not Path(self.jar_path).exists():
            raise FreeRoutingError(
                f"FreeRouting JAR not found at '{self.jar_path}'. "
                f"Download from https://github.com/freerouting/freerouting/releases"
            )

        cmd = [
            "java", "-jar", self.jar_path,
            "-de", dsn,
            "-do", ses,
            "-mp", str(self.max_passes),
            "-us", "Hybrid",   # Use all layers with global optimization
            "-mt", "1",        # Single-threaded (avoids DRC violations)
        ]

        print(f"Launching FreeRouting engine...")
        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True,
                timeout=600,
            )
            if result.stdout:
                # Print last ~2000 chars of log
                tail = result.stdout.strip().split("\n")
                for line in tail[-15:]:
                    print(f"  [FR] {line}")
            print(f"FreeRouting completed. SES output: {ses}")
            return ses
        except subprocess.TimeoutExpired:
            raise FreeRoutingError(
                "FreeRouting timed out after 600 seconds."
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or "(no stderr)"
            stdout = e.stdout or "(no stdout)"
            raise FreeRoutingError(
                f"FreeRouting exited with code {e.returncode}.\n"
                f"STDERR: {stderr[-1000:]}\n"
                f"STDOUT: {stdout[-1000:]}"
            )

    # ── Pure-Python DSN export ─────────────────────────────────────

    def export_dsn_py(
        self,
        dsn_path: str | Path,
        components: List[DSNComponentInfo],
        nets: List[DSNNetInfo],
        layers: List[str],
        board_w_mm: float,
        board_h_mm: float,
        resolution: int = 10000,
    ) -> None:
        """Generate a Specctra DSN file from component/net data (no pcbnew needed).

        Format verified against FreeRouting v1.9.0 source code (DsnFile.write_pcb_scope).
        Key structural requirements:
        - (pcb ...), (parser ...), (resolution ...), (unit ...) at top level
        - (structure ...) contains only: layers, boundary
        - (placement ...), (library ...), (network ...), (wiring ...) are ALL top-level
        - Coordinates are in resolution units (e.g. 50mm * 10000 = 500000)

        Args:
            dsn_path: Output .dsn file path.
            components: Placed components with pad positions (absolute coords in mm).
            nets: Net connectivity data.
            layers: Signal layer names (e.g. ["F.Cu", "B.Cu"]).
            board_w_mm, board_h_mm: Board outline dimensions in mm.
            resolution: Specctra resolution (units per mm, default 10000).
        """
        r = resolution  # shorthand

        lines: List[str] = []
        a = lines.append

        # ── Top-level pcb scope ──────────────────────────────────
        a(f"(pcb output")
        a(f"  (parser")
        a(f'    (string_quote ")')
        a(f"    (space_in_quoted_tokens on)")
        a(f'    (host_cad "kcad-auto-pcb")')
        a(f"  )")
        a(f"  (resolution mm {r})")
        a(f"  (unit mm)")
        a(f"")

        # ── Structure (layers + boundary only) ───────────────────
        a(f"  (structure")
        a(f"    (layers {len(layers)}")
        for layer in layers:
            a(f'      (layer "{layer}" (type signal))')
        a(f"    )")
        a(f"    (boundary")
        a(f"      (rect 0.0 0.0 {board_w_mm * r:.0f} {board_h_mm * r:.0f})")
        a(f"    )")
        a(f"  )")
        a(f"")

        # ── Placement (top-level) ────────────────────────────────
        a(f"  (placement")
        for comp in components:
            fp_short = comp.footprint_name.replace(":", "_")
            side = comp.side if comp.side else "front"
            a(f'    (component "{comp.reference}"')
            a(f'      (place "{fp_short}" {comp.x * r:.0f} {comp.y * r:.0f} {comp.rotation:.0f} {side})')
            a(f"    )")
        a(f"  )")
        a(f"")

        # ── Library (top-level) ──────────────────────────────────
        # Pad positions in library are relative to the component origin
        fp_pads: Dict[str, Tuple[float, float, List[DSNPadInfo]]] = {}
        for comp in components:
            if comp.footprint_name not in fp_pads:
                fp_pads[comp.footprint_name] = (comp.x, comp.y, comp.pads)

        a(f"  (library")
        for fp_full, (cx, cy, pads) in fp_pads.items():
            fp_short = fp_full.replace(":", "_")
            a(f'    (image "{fp_short}"')
            for pad in pads:
                rel_x = (pad.abs_x - cx) * r
                rel_y = (pad.abs_y - cy) * r
                shape = pad.shape if pad.shape in ("circle", "rect", "oval", "roundrect") else "roundrect"
                a(f'      (pad "{pad.number}" smd {shape} '
                  f'(at {rel_x:.0f} {rel_y:.0f}) '
                  f'(size {pad.size_w * r:.0f} {pad.size_h * r:.0f}) '
                  f'(layer "F.Cu"))')
            a(f"    )")
        a(f"  )")
        a(f"")

        # ── Part Library (top-level, can be empty) ──────────────
        a(f"  (part_library)")
        a(f"")

        # ── Network (top-level) ──────────────────────────────────
        a(f"  (network")
        for net in nets:
            if len(net.pins) < 2:
                continue
            a(f'    (net "{net.name}"')
            for ref, pin_num in net.pins:
                a(f'      (pins "{ref}" "{pin_num}")')
            a(f"    )")
        a(f"  )")
        a(f"")

        # ── Wiring (top-level) ───────────────────────────────────
        a(f"  (wiring")
        a(f"    (resolution mm {r})")
        a(f"  )")
        a(f")")

        with open(dsn_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"DSN exported (pure Python): {dsn_path}")

    # ── Pure-Python SES import ─────────────────────────────────────

    @staticmethod
    def parse_ses(ses_path: str | Path) -> List[SESNetResult]:
        """Parse a Specctra SES file and extract routed wires and vias.

        Returns a list of SESNetResult, one per routed net.
        """
        ses = Path(ses_path)
        if not ses.exists():
            raise FreeRoutingError(f"SES file not found: {ses_path}")

        text = ses.read_text(encoding="utf-8", errors="replace")
        results: List[SESNetResult] = []

        # Split into network_out blocks
        net_blocks = re.split(r'\(\s*network_out\b', text)[1:]

        for block in net_blocks:
            # Extract net name
            net_match = re.search(r'\(\s*net\s+"([^"]*)"', block)
            if not net_match:
                continue
            net_name = net_match.group(1)
            net_result = SESNetResult(net_name=net_name)

            # Extract wire paths
            for wire_match in re.finditer(
                r'\(\s*wire\s+\(path\s+"([^"]+)"\s+([\d.]+)\s*(.*?)\s*\)\s*\)',
                block, re.DOTALL
            ):
                layer = wire_match.group(1)
                width = float(wire_match.group(2))
                path_body = wire_match.group(3)

                # Extract xy coordinates from path
                coords = re.findall(r'\(xy\s+([\d.-]+)\s+([\d.-]+)\)', path_body)
                for i in range(len(coords) - 1):
                    x1, y1 = float(coords[i][0]), float(coords[i][1])
                    x2, y2 = float(coords[i+1][0]), float(coords[i+1][1])
                    net_result.wires.append(SESWireSegment(
                        x1=x1, y1=y1, x2=x2, y2=y2,
                        layer=layer, width=width,
                    ))

            # Extract vias
            for via_match in re.finditer(
                r'\(\s*via\s+\(at\s+([\d.-]+)\s+([\d.-]+)\)\s+'
                r'\(size\s+([\d.]+)\)\s+\(drill\s+([\d.]+)\)',
                block
            ):
                net_result.vias.append(SESViaInfo(
                    x=float(via_match.group(1)),
                    y=float(via_match.group(2)),
                    size=float(via_match.group(3)),
                    drill=float(via_match.group(4)),
                ))

            results.append(net_result)

        print(f"SES parsed: {len(results)} nets, "
              f"{sum(len(r.wires) for r in results)} wires, "
              f"{sum(len(r.vias) for r in results)} vias")
        return results

    # ── pcbnew-native SES import ───────────────────────────────────

    @staticmethod
    def import_ses_to_board(board: "pcbnew.BOARD", ses_path: str | Path) -> None:
        """Import FreeRouting SES into a pcbnew BOARD via KiCad API."""
        if not PCBNEW_AVAILABLE:
            raise FreeRoutingError("pcbnew not available — cannot import SES.")
        ses_path_str = str(ses_path)
        if not Path(ses_path_str).exists():
            raise FreeRoutingError(f"SES file not found: {ses_path_str}")
        pcbnew.ImportSpecctraSES(board, ses_path_str)
        print(f"SES imported into board: {ses_path_str}")

    # ── Convenience: full pure-Python flow ─────────────────────────

    def route_via_dsn(
        self,
        dsn_path: str | Path,
        components: List[DSNComponentInfo],
        nets: List[DSNNetInfo],
        layers: List[str],
        board_w_mm: float,
        board_h_mm: float,
    ) -> List[SESNetResult]:
        """Full routing flow without pcbnew: export DSN, run FreeRouting, parse SES.

        Returns the list of SESNetResult with routed wires and vias.
        """
        dsn_str = str(dsn_path)
        ses_str = dsn_str.replace(".dsn", ".ses")

        # Export DSN
        self.export_dsn_py(dsn_str, components, nets, layers, board_w_mm, board_h_mm)

        # Run FreeRouting
        ses_path = self.run_autorouter(dsn_str, ses_str)
        if not ses_path:
            raise FreeRoutingError("FreeRouting did not produce an SES file.")

        # Parse SES result
        return self.parse_ses(ses_path)

    # ── pcbnew-native full flow ────────────────────────────────────

    def route_board(
        self,
        board: "pcbnew.BOARD",
        dsn_path: str | Path,
        cleanup_dsn: bool = True,
    ) -> Optional[str]:
        """Export DSN (pcbnew), run FreeRouting, import SES — all in one call."""
        if not PCBNEW_AVAILABLE:
            return None

        dsn_str = str(dsn_path)
        pcbnew.ExportSpecctraDSN(board, dsn_str)
        ses_path = self.run_autorouter(dsn_str)
        if ses_path is None:
            return None
        self.import_ses_to_board(board, ses_path)
        if cleanup_dsn and Path(dsn_str).exists():
            Path(dsn_str).unlink()
        return ses_path
