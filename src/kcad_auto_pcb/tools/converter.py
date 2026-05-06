"""Spec → PCB converter. Takes a BoardSpec and generates .kicad_pcb.

Uses existing footprint library + geometry primitives to produce
valid KiCad PCB files from the structured spec.
"""

from __future__ import annotations
from pathlib import Path
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..pcb.board_builder import BoardBuilder, PCBBoard, FootprintInstance, TraceItem, ViaItem
from ..pcb.stackup import BoardStackup
from ..pcb.exporter import PCBExporter
from ..footprint.cache import FootprintCache
from ..geometry.point import Point as _Pt
from .spec import BoardSpec


def spec_to_pcb(spec: BoardSpec, footprint_map: dict[str, str],
                net_map: dict[str, int] | None = None,
                fp_cache: FootprintCache | None = None) -> PCBBoard:
    """Convert a BoardSpec into a PCBBoard ready for export.

    Args:
        spec: The board layout specification
        footprint_map: {ref: footprint_name} mapping
        net_map: {net_name: net_code} mapping (auto-generated if None)
        fp_cache: footprint geometry cache
    """
    if fp_cache is None:
        fp_cache = FootprintCache()

    if net_map is None:
        net_map = {}
        code = 0
        for t in spec.traces:
            if t.net not in net_map:
                code += 1
                net_map[t.net] = code
        for v in spec.vias:
            if v.net not in net_map:
                code += 1
                net_map[v.net] = code

    stackup = BoardStackup(spec.layers)

    # Build placements with pad data from footprint cache
    placements = []
    for comp in spec.components:
        fp_name = footprint_map.get(comp.ref, "")
        if not fp_name:
            continue  # Skip components without footprints (e.g. PWR symbols)
        fp = fp_cache.get(fp_name) if fp_cache else None
        fp_pads = []
        if fp:
            for pad in fp.pads:
                fp_pads.append(pad)  # Pass through the existing pad objects
        placements.append(FootprintInstance(
            reference=comp.ref,
            footprint_name=fp_name,
            position=_Pt(comp.x, comp.y),
            rotation=comp.rotation,
            layer=comp.layer,
            pads=fp_pads,
        ))

    # Build traces
    traces = []
    for t in spec.traces:
        net_code = net_map.get(t.net, 0)
        for i in range(len(t.points) - 1):
            traces.append(TraceItem(
                start=_Pt(t.points[i][0], t.points[i][1]),
                end=_Pt(t.points[i+1][0], t.points[i+1][1]),
                width=t.width, layer=t.layer, net_code=net_code,
            ))

    # Build vias
    vias = []
    for v in spec.vias:
        vias.append(ViaItem(
            position=_Pt(v.x, v.y),
            layers=v.layers, size=v.size, drill=v.drill,
            net_code=net_map.get(v.net, 0),
        ))

    board = PCBBoard(
        name=spec.name,
        bounds=Rect(0, 0, spec.width, spec.height),
        stackup=stackup,
        footprints=placements,
        traces=traces,
        vias=vias,
        nets=net_map,
    )

    return board


def export_spec(spec: BoardSpec, footprint_map: dict[str, str],
                output_path: str | Path, net_map: dict[str, int] | None = None):
    """Export a BoardSpec to .kicad_pcb file."""
    board = spec_to_pcb(spec, footprint_map, net_map)
    exporter = PCBExporter()
    exporter.export(board, output_path)
    # Also export JSON
    json_path = Path(output_path).with_suffix(".json")
    exporter.export(board, json_path, format="json")
    return board
