"""PCB layout SVG preview renderer.

Generates a visual SVG representation from either PCBBoard or .kicad_pcb file.
"""

from __future__ import annotations
import io, re
from typing import Optional
from pathlib import Path
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..pcb.board_builder import PCBBoard


def render_pcb_svg_from_file(kicad_path: str | Path, width: int = 800) -> str:
    """Render SVG preview directly from a .kicad_pcb file (no pcbnew needed)."""
    path = Path(kicad_path)
    if not path.exists():
        return _empty_svg("PCB file not found")

    content = path.read_text(encoding="utf-8", errors="replace")

    # Parse footprints (positions)
    footprints = []
    for m in re.finditer(r'\(footprint\s+"([^"]+)"\s*\(at\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)', content):
        fp_name = m.group(1)
        x = float(m.group(2))
        y = float(m.group(3))
        rot = float(m.group(4))
        # Find reference
        ref = ""
        ref_m = re.search(rf'\(fp_text\s+reference\s+"([^"]+)"', content[m.start():m.start()+2000])
        if ref_m:
            ref = ref_m.group(1)
        else:
            # Try to extract from footprint name context
            ref = fp_name
        footprints.append({"ref": ref, "x": x, "y": y, "fp": fp_name})

    # Parse segments (traces)
    segments = []
    for m in re.finditer(r'\(segment\s+\(start\s+([\d.-]+)\s+([\d.-]+)\)\s+\(end\s+([\d.-]+)\s+([\d.-]+)\)\s+\(width\s+([\d.-]+)\)\s+\(layer\s+"([^"]+)"\)', content):
        segments.append({
            "x1": float(m.group(1)), "y1": float(m.group(2)),
            "x2": float(m.group(3)), "y2": float(m.group(4)),
            "width": float(m.group(5)), "layer": m.group(6),
        })

    # Parse board outline from Edge.Cuts
    edge_xs, edge_ys = [], []
    for m in re.finditer(r'\(gr_line\s+\(start\s+([\d.-]+)\s+([\d.-]+)\)\s+\(end\s+([\d.-]+)\s+([\d.-]+)\)\s+\(layer\s+"Edge\.Cuts"', content):
        edge_xs.extend([float(m.group(1)), float(m.group(3))])
        edge_ys.extend([float(m.group(2)), float(m.group(4))])

    if not footprints and not segments:
        return _empty_svg("Empty PCB - no components or traces")

    # Calculate bounds
    all_xs = [f["x"] for f in footprints] + edge_xs
    all_ys = [f["y"] for f in footprints] + edge_ys
    if not all_xs:
        all_xs = [0, 100]
        all_ys = [0, 80]

    margin = 5
    min_x, max_x = min(all_xs) - margin, max(all_xs) + margin
    min_y, max_y = min(all_ys) - margin, max(all_ys) + margin
    bw, bh = max_x - min_x, max_y - min_y

    scale = width / bw if bw > 0 else 8
    height = int(bh * scale) if bh > 0 else int(width * 0.75)

    # Build SVG
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{min_x * scale:.1f} {min_y * scale:.1f} {bw * scale:.1f} {bh * scale:.1f}">',
        '<style>',
        '  .board { fill: #1a1a2e; stroke: #444; stroke-width: 2; }',
        '  .comp { fill: #16213e; stroke: #0f3460; stroke-width: 1.5; rx: 3; }',
        '  .comp-th { fill: #1a1a3e; stroke: #3498db; stroke-width: 2; rx: 2; }',
        '  .comp-label { fill: #e0e0e0; font-size: 10px; font-family: monospace; text-anchor: middle; }',
        '  .trace { stroke: #00ff88; stroke-width: 1.5; fill: none; stroke-linecap: round; opacity: 0.8; }',
        '  .trace-bottom { stroke: #4488ff; stroke-width: 1.5; fill: none; stroke-linecap: round; opacity: 0.6; }',
        '  .grid { stroke: #2a2a3e; stroke-width: 0.5; }',
        '</style>',
        # Board background
        f'<rect x="{min_x * scale:.1f}" y="{min_y * scale:.1f}" '
        f'width="{bw * scale:.1f}" height="{bh * scale:.1f}" class="board"/>',
    ]

    # Grid
    grid_step = 5
    for gx in range(int(min_x / grid_step) * grid_step, int(max_x) + grid_step, grid_step):
        lines.append(
            f'<line x1="{gx * scale:.1f}" y1="{min_y * scale:.1f}" '
            f'x2="{gx * scale:.1f}" y2="{max_y * scale:.1f}" class="grid"/>')
    for gy in range(int(min_y / grid_step) * grid_step, int(max_y) + grid_step, grid_step):
        lines.append(
            f'<line x1="{min_x * scale:.1f}" y1="{gy * scale:.1f}" '
            f'x2="{max_x * scale:.1f}" y2="{gy * scale:.1f}" class="grid"/>')

    # Traces
    for seg in segments:
        cls = "trace-bottom" if "B.Cu" in seg["layer"] else "trace"
        lines.append(
            f'<line x1="{seg["x1"] * scale:.1f}" y1="{seg["y1"] * scale:.1f}" '
            f'x2="{seg["x2"] * scale:.1f}" y2="{seg["y2"] * scale:.1f}" class="{cls}"/>'
        )

    # Components
    for fp in footprints:
        x, y = fp["x"] * scale, fp["y"] * scale
        w, h = 16, 12
        lines.append(
            f'<rect x="{x - w/2:.1f}" y="{y - h/2:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" class="comp"/>')
        lines.append(
            f'<text x="{x:.1f}" y="{y + 3:.1f}" class="comp-label">{fp["ref"]}</text>')

    # Stats
    lines.append(
        f'<text x="{width - 5}" y="15" fill="#888" font-size="8px" font-family="monospace" text-anchor="end">'
        f'{len(footprints)} comps, {len(segments)} traces</text>')

    lines.append('</svg>')
    return '\n'.join(lines)


def render_pcb_svg(board: PCBBoard, width: int = 800) -> str:
    """Render a PCBBoard as an SVG image.

    Returns an SVG string ready for display in browser.
    """
    if not board or not board.footprints:
        return _empty_svg("No PCB data - run design first")

    bounds = board.bounds
    # Add margin
    # Auto-adjust scale: use component bounding box if board is huge
    margin = 10
    if board.footprints:
        xs = [f.position.x for f in board.footprints]
        ys = [f.position.y for f in board.footprints]
        comp_bounds = Rect(min(xs)-20, min(ys)-20, max(xs)-min(xs)+40, max(ys)-min(ys)+40)
        # Use the smaller of board bounds or component bounds
        effective_w = min(bounds.w, max(comp_bounds.w, 100))
        effective_h = min(bounds.h, max(comp_bounds.h, 80))
        offset_x = comp_bounds.x if bounds.w > 500 else bounds.x
        offset_y = comp_bounds.y if bounds.h > 500 else bounds.y
    else:
        effective_w, effective_h = bounds.w, bounds.h
        offset_x, offset_y = bounds.x, bounds.y

    bw, bh = effective_w + 2 * margin, effective_h + 2 * margin
    scale = width / bw
    height = int(bh * scale)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{offset_x * scale:.1f} {offset_y * scale:.1f} {bw * scale:.1f} {bh * scale:.1f}">',
        '<style>',
        '  .board { fill: #f0f0e0; stroke: #888; stroke-width: 2; }',
        '  .comp-label { fill: #111; font-size: 8px; font-family: monospace; text-anchor: middle; font-weight: bold; }',
        '  .comp-smd { fill: #e94560; opacity: 0.9; stroke: #c0392b; stroke-width: 0.5; }',
        '  .comp-th { fill: #3498db; stroke: #2471a3; stroke-width: 1; }',
        '  .trace { stroke: #c0392b; stroke-width: 2; fill: none; stroke-linecap: round; }',
        '  .trace-inner { stroke: #2980b9; stroke-width: 1.5; fill: none; stroke-linecap: round; }',
        '  .via { fill: #f39c12; stroke: #e67e22; stroke-width: 1; }',
        '  .grid { stroke: #e8e8e0; stroke-width: 0.3; }',
        '</style>',
        # Background grid — use effective bounds for grid
        _grid_lines(Rect(offset_x, offset_y, effective_w, effective_h), scale),
        # Board outline
        f'<rect x="{offset_x * scale:.1f}" y="{offset_y * scale:.1f}" '
        f'width="{effective_w * scale:.1f}" height="{effective_h * scale:.1f}" class="board"/>',
    ]

    # Layer order (bottom to top for rendering)
    layer_colors = {
        "B.Cu": "#4488ff",
        "In2.Cu": "#8888ff",
        "In1.Cu": "#aa88ff",
        "F.Cu": "#00ff88",
    }

    # Traces
    for trace in board.traces:
        color = layer_colors.get(trace.layer, "#00ff88")
        opacity = "0.6" if "In" in trace.layer else "0.9"
        lines.append(
            f'<line x1="{trace.start.x * scale:.1f}" y1="{trace.start.y * scale:.1f}" '
            f'x2="{trace.end.x * scale:.1f}" y2="{trace.end.y * scale:.1f}" '
            f'stroke="{color}" stroke-width="{max(1.5, trace.width * scale):.1f}" '
            f'opacity="{opacity}" stroke-linecap="round"/>'
        )

    # Vias
    for via in board.vias:
        r = via.size / 2 * scale
        lines.append(
            f'<circle cx="{via.position.x * scale:.1f}" cy="{via.position.y * scale:.1f}" '
            f'r="{max(2, r):.1f}" class="via"/>'
        )

    # Components
    for fp in board.footprints:
        x = (fp.position.x - offset_x) * scale
        y = (fp.position.y - offset_y) * scale
        has_th = any(getattr(p, 'drill', None) for p in fp.pads)

        # Use proportional body size from footprint pads
        if fp.pads:
            pad_xs = [pd.position.x for pd in fp.pads]
            pad_ys = [pd.position.y for pd in fp.pads]
            body_w = (max(pad_xs) - min(pad_xs) + 3) * scale
            body_h = (max(pad_ys) - min(pad_ys) + 3) * scale
            body_w = max(body_w, 12)
            body_h = max(body_h, 10)
        else:
            body_w, body_h = 16, 12

        if has_th:
            lines.append(
                f'<rect x="{x - body_w/2:.1f}" y="{y - body_h/2:.1f}" '
                f'width="{body_w:.1f}" height="{body_h:.1f}" '
                f'fill="#1a1a3e" stroke="#3498db" stroke-width="1" rx="2"/>'
            )
            # Draw pad holes
            for pad in fp.pads:
                if getattr(pad, 'drill', None):
                    px = (fp.position.x + pad.position.x - offset_x) * scale
                    py = (fp.position.y + pad.position.y - offset_y) * scale
                    r = pad.drill / 2 * scale if pad.drill else 2
                    lines.append(
                        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{max(2, r):.1f}" '
                        f'fill="#fff" stroke="#3498db" stroke-width="1"/>'
                    )
        else:
            # SMD component
            lines.append(
                f'<rect x="{x - body_w/2:.1f}" y="{y - body_h/2:.1f}" '
                f'width="{body_w:.1f}" height="{body_h:.1f}" '
                f'fill="#e94560" opacity="0.9" rx="1"/>'
            )
            # Draw SMD pads
            for pad in fp.pads:
                px = (fp.position.x + pad.position.x - offset_x) * scale
                py = (fp.position.y + pad.position.y - offset_y) * scale
                pw = pad.size[0] * scale if hasattr(pad, 'size') else 3
                ph = pad.size[1] * scale if hasattr(pad, 'size') else 3
                lines.append(
                    f'<rect x="{px - pw/2:.1f}" y="{py - ph/2:.1f}" '
                    f'width="{pw:.1f}" height="{ph:.1f}" '
                    f'fill="#ccc" stroke="#999" stroke-width="0.5"/>'
                )

        # Reference label
        lines.append(
            f'<text x="{x:.1f}" y="{y - body_h/2 - 3:.1f}" class="comp-label" font-size="9px">{fp.reference}</text>'
        )

    # Legend
    legend_y = 15
    for layer, color in layer_colors.items():
        if any(t.layer == layer for t in board.traces):
            lines.append(
                f'<text x="{5}" y="{legend_y}" fill="{color}" font-size="8px" '
                f'font-family="monospace">{layer}</text>'
            )
            legend_y += 10

    # Stats
    stats = board.summary
    stat_lines = [
        f'{stats["components"]} components',
        f'{stats["traces"]} traces',
        f'{stats["total_trace_length_mm"]:.0f}mm total',
        f'{stats["layers"]} layers',
        f'{stats["board_size"]}',
    ]
    for i, s in enumerate(stat_lines):
        lines.append(
            f'<text x="{width - 5}" y="{15 + i * 10}" fill="#888888" '
            f'fill="#cccccc" font-size="8px" font-family="monospace" text-anchor="end">{s}</text>'
        )

    lines.append('</svg>')
    return '\n'.join(lines)


def _grid_lines(bounds: Rect, scale: float) -> str:
    """Generate grid pattern."""
    lines = []
    step = 5  # mm
    for x in range(int(bounds.x), int(bounds.right) + 1, step):
        lines.append(
            f'<line x1="{x * scale:.1f}" y1="{bounds.y * scale:.1f}" '
            f'x2="{x * scale:.1f}" y2="{bounds.top * scale:.1f}" class="grid"/>'
        )
    for y in range(int(bounds.y), int(bounds.top) + 1, step):
        lines.append(
            f'<line x1="{bounds.x * scale:.1f}" y1="{y * scale:.1f}" '
            f'x2="{bounds.right * scale:.1f}" y2="{y * scale:.1f}" class="grid"/>'
        )
    return '\n'.join(lines)


def _empty_svg(msg: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400">
  <rect width="100%" height="100%" fill="#0d1117"/>
  <text x="400" y="200" fill="#ffffff" font-size="18px" font-family="sans-serif"
        text-anchor="middle">{msg}</text>
</svg>'''
