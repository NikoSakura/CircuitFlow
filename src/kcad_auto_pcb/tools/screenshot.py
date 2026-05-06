"""PCB screenshot tool — renders current board state as an image for LLM visual inspection.

Used in the routing flow: take screenshot → analyze obstacles → plan path → execute.
"""

from __future__ import annotations
import io, base64
from pathlib import Path
from ..geometry.rect import Rect
from ..pcb.board_builder import PCBBoard


def render_board_image(board: PCBBoard, width: int = 1200) -> str:
    """Render current PCB as a PNG base64 string for LLM vision.

    Returns a data URI string ready for multimodal LLM input.
    """
    try:
        import cairo
        return _render_cairo(board, width)
    except ImportError:
        pass

    # Fallback: render as SVG and return as text description
    from ..web.preview import render_pcb_svg
    svg = render_pcb_svg(board, width)
    return f"data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}"


def _render_cairo(board: PCBBoard, width: int) -> str:
    """Render PCB using Cairo for high-quality PNG output."""
    import cairo

    bounds = board.bounds
    margin = 20
    scale = (width - 2 * margin) / bounds.w
    height = int(bounds.h * scale + 2 * margin)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)

    # White background
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()

    # Board outline
    ctx.set_source_rgb(0.9, 0.9, 0.85)
    ctx.rectangle(margin, margin, bounds.w * scale, bounds.h * scale)
    ctx.fill()
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.set_line_width(2)
    ctx.rectangle(margin, margin, bounds.w * scale, bounds.h * scale)
    ctx.stroke()

    # Draw traces
    for trace in board.traces:
        x1 = margin + trace.start.x * scale
        y1 = margin + trace.start.y * scale
        x2 = margin + trace.end.x * scale
        y2 = margin + trace.end.y * scale
        ctx.set_source_rgb(0.8, 0.2, 0.2)
        ctx.set_line_width(max(2, trace.width * scale))
        ctx.move_to(x1, y1)
        ctx.line_to(x2, y2)
        ctx.stroke()

    # Draw components
    for fp in board.footprints:
        cx = margin + fp.position.x * scale
        cy = margin + fp.position.y * scale

        has_th = any(getattr(p, 'drill', None) for p in fp.pads)
        if has_th:
            ctx.set_source_rgb(0.2, 0.4, 0.8)
            w, h = 16, 12
            ctx.rectangle(cx - w/2, cy - h/2, w, h)
            ctx.fill()
            # Draw pads
            for pad in fp.pads:
                if getattr(pad, 'drill', None):
                    px = cx + pad.position.x * scale
                    py = cy + pad.position.y * scale
                    ctx.set_source_rgb(1, 1, 1)
                    ctx.arc(px, py, 3, 0, 6.28)
                    ctx.fill()
        else:
            ctx.set_source_rgb(0.9, 0.3, 0.3)
            w, h = 10, 8
            ctx.rectangle(cx - w/2, cy - h/2, w, h)
            ctx.fill()

        # Label
        ctx.set_source_rgb(0, 0, 0)
        ctx.set_font_size(8)
        ctx.move_to(cx - 10, cy - 10)
        ctx.show_text(fp.reference)

    buf = io.BytesIO()
    surface.write_to_png(buf)
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"


def get_board_description(board: PCBBoard) -> str:
    """Generate a text description of the current board state for LLM analysis.

    Returns a structured text summary that the LLM can use to plan routing.
    """
    lines = [
        f"## PCB State: {board.name}",
        f"Board: {board.bounds.w:.0f}x{board.bounds.h:.0f}mm, {board.stackup.num_layers} layers",
        f"",
        f"### Components ({len(board.footprints)})",
    ]
    for fp in board.footprints:
        pads_desc = ", ".join(
            f"pad{p.number}@({p.position.x:.1f},{p.position.y:.1f})"
            for p in fp.pads[:4]
        )
        lines.append(f"  {fp.reference} @ ({fp.position.x:.1f}, {fp.position.y:.1f}) [{pads_desc}]")

    lines.extend(["", f"### Traces ({len(board.traces)})"])
    for t in board.traces[:20]:
        lines.append(
            f"  net{t.net_code}: ({t.start.x:.1f},{t.start.y:.1f}) → "
            f"({t.end.x:.1f},{t.end.y:.1f}) w={t.width}mm {t.layer}"
        )
    if len(board.traces) > 20:
        lines.append(f"  ... and {len(board.traces) - 20} more traces")

    return "\n".join(lines)
