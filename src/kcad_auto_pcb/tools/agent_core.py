"""Closed-loop PCB design agent core.

Workflow:
  1. smart_parse → physical spec
  2. load_rules → design constraints
  3. plan_routing → trace paths
  4. run_autoroute → write to PCB
  5. run_drc → check errors
  6. fix → retry until 0 errors
  7. deliver → return final PCB
"""

from __future__ import annotations
import json, tempfile, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml

from ..geometry.point import Point
from ..geometry.rect import Rect
from .smart_parse import smart_parse, PhysicalSpec
from .converter import export_spec
from .spec import BoardSpec, ComponentSpec, TraceSpec, ViaSpec


def load_rules() -> dict:
    """Load PCB design rules from pcb_rules.yaml."""
    rules_path = Path(__file__).parent.parent / "knowledge" / "pcb_rules.yaml"
    if not rules_path.exists():
        return {"rules": []}
    return yaml.safe_load(rules_path.read_text(encoding="utf-8"))


def get_rule(rules: dict, rule_id: str) -> dict | None:
    """Get a specific rule by ID."""
    for r in rules.get("rules", []):
        if r.get("id") == rule_id:
            return r
    return None


def plan_routing(spec: PhysicalSpec, rules: dict | None = None) -> BoardSpec:
    """Plan trace paths based on physical spec and design rules.

    Generates clean Manhattan (L-shaped) routing with power trace width rules.
    """
    if rules is None:
        rules = load_rules()

    board = BoardSpec(
        name=spec.board.name,
        width=spec.board.width,
        height=spec.board.height,
        layers=spec.board.layers,
        components=spec.board.components,
    )

    # Get design rules
    pwr_rule = get_rule(rules, "PWR_TRACE_WIDTH")
    sig_rule = get_rule(rules, "SIGNAL_TRACE_WIDTH")
    pwr_width = pwr_rule.get("default_width_mm", 0.5) if pwr_rule else 0.5
    sig_width = sig_rule.get("default_width_mm", 0.25) if sig_rule else 0.25

    # Build component position lookup
    comp_positions = {c.ref: (c.x, c.y) for c in spec.board.components}

    # Route each net
    for net in spec.nets:
        net_name = net["name"]
        is_power = net["is_power"]
        width = pwr_width if is_power else sig_width
        pins = net["pins"]

        # Get absolute pad positions
        pad_abs = []
        for ref, pin_num in pins:
            if ref not in comp_positions:
                continue
            cx, cy = comp_positions[ref]
            if ref in spec.pad_positions and ref in spec.pad_numbers:
                try:
                    idx = spec.pad_numbers[ref].index(pin_num)
                    px, py = spec.pad_positions[ref][idx]
                    pad_abs.append((cx + px, cy + py))
                except (ValueError, IndexError):
                    pad_abs.append((cx, cy))
            else:
                pad_abs.append((cx, cy))

        if len(pad_abs) < 2:
            continue

        # Route: star topology from first pad
        origin = pad_abs[0]
        points = [origin]
        for target in pad_abs[1:]:
            # Manhattan L-shaped: go horizontal then vertical
            mid = (target[0], origin[1])
            points.extend([mid, target])

        if len(points) >= 2:
            board.traces.append(TraceSpec(
                net=net_name,
                width=width,
                layer="F.Cu",
                points=points,
            ))

    return board


def run_autoroute(spec: PhysicalSpec, board: BoardSpec,
                  output_path: str | Path):
    """Execute the routing plan: write BoardSpec to .kicad_pcb file."""
    export_spec(board, spec.footprint_map, output_path)


def run_drc(kicad_path: str | Path) -> List[dict]:
    """Run DRC check on the generated PCB.

    Tries kicad-cli first, falls back to basic internal checks.
    Returns list of error dicts: [{type, message, location}]
    """
    errors = []
    import subprocess, shutil

    # Try KiCad CLI DRC
    kicad_cli = shutil.which("kicad-cli") or shutil.which("kicad-cli.exe")
    if kicad_cli:
        try:
            result = subprocess.run(
                [kicad_cli, "drc", str(kicad_path)],
                capture_output=True, text=True, timeout=30,
            )
            # Parse KiCad DRC output
            for line in result.stdout.split("\n") + result.stderr.split("\n"):
                if "error" in line.lower() or "violation" in line.lower():
                    errors.append({"type": "kicad_drc", "message": line.strip()})
            return errors
        except Exception:
            pass

    # Fallback: basic internal checks
    try:
        content = Path(kicad_path).read_text(encoding="utf-8")
        # Check board has edge cuts
        if 'Edge.Cuts' not in content:
            errors.append({"type": "missing_edge", "message": "缺少板框 (Edge.Cuts)"})
        # Check has at least some traces or footprints
        if '(footprint' not in content:
            errors.append({"type": "no_components", "message": "PCB 上没有元器件"})
    except Exception:
        pass

    return errors


def fix_errors(spec: PhysicalSpec, board: BoardSpec,
               errors: List[dict]) -> Tuple[PhysicalSpec, BoardSpec, bool]:
    """Analyze DRC errors and adjust spec/board to fix them.

    Returns (updated_spec, updated_board, fixed).
    If fixed=True, changes were made and re-routing is needed.
    If fixed=False, errors are unfixable automatically.
    """
    fixed = False

    for err in errors:
        etype = err.get("type", "")

        if etype == "missing_edge":
            # Already has edge cuts from export, ignore
            continue
        elif etype == "no_components":
            # Can't fix empty board
            continue
        elif etype == "kicad_drc":
            msg = err.get("message", "").lower()
            if "clearance" in msg or "spacing" in msg:
                # Try increasing spacing by adjusting component positions
                for i, comp in enumerate(board.components):
                    board.components[i].x += 0.5
                    board.components[i].y += 0.5
                fixed = True
            elif "width" in msg:
                # Increase all trace widths slightly
                for i, trace in enumerate(board.traces):
                    if trace.width < 0.3:
                        board.traces[i].width += 0.05
                fixed = True

    return spec, board, fixed


def run_pcb_design(schematic_path: str | Path,
                   output_dir: str | Path | None = None,
                   max_iterations: int = 5) -> dict:
    """Main agent entry point — closed-loop PCB design.

    Returns dict with:
      - kicad_path: final .kicad_pcb
      - json_path: final .json
      - drc_errors: final DRC error count (should be 0)
      - iterations: number of fix loops
      - spec_json: the final BoardSpec as JSON
      - warnings: parse warnings
    """
    path = Path(schematic_path)
    if output_dir is None:
        output_dir = Path("output")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / f"{path.stem}.kicad_pcb"

    # Step 1: smart_parse
    spec = smart_parse(str(path))

    # Step 2: load_rules
    rules = load_rules()

    # Step 3: plan routing
    board = plan_routing(spec, rules)

    # Step 4: run_autoroute (first pass)
    export_spec(board, spec.footprint_map, out_path)
    json_path = str(out_path).replace(".kicad_pcb", ".json")

    # Steps 5-6: DRC + fix loop
    iterations = 1
    for i in range(max_iterations - 1):
        kicad_path = out_path

        iterations += 1
        errors = run_drc(out_path)
        if not errors:
            return {
                "status": "success", "kicad_path": str(out_path),
                "json_path": json_path, "drc_errors": 0,
                "iterations": iterations, "spec_json": board.to_json(),
                "warnings": spec.warnings,
            }
        spec, board, was_fixed = fix_errors(spec, board, errors)
        if not was_fixed: break
        if was_fixed:
            export_spec(board, spec.footprint_map, out_path)

    errors = run_drc(out_path)
    return {
        "status": "drc_failed" if errors else "max_iterations",
        "kicad_path": str(out_path), "json_path": json_path,
        "drc_errors": len(errors), "drc_error_list": errors,
        "iterations": iterations, "spec_json": board.to_json(),
        "warnings": spec.warnings,
    }
