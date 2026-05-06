"""PCB exporter — writes .kicad_pcb files via pcbnew API (not string concatenation).

Per important.md: "绝对禁止：使用字符串拼接或正则表达式来生成或修改 .kicad_pcb 文件内容"
String-based S-expression generation is FORBIDDEN. All board creation goes through
engines/kicad_native.py which wraps pcbnew.
"""

from __future__ import annotations
from pathlib import Path
import json
from .board_builder import PCBBoard


class PCBExporter:
    """Export PCBBoard to .kicad_pcb format.

    Primary path: KiCadEngine (pcbnew API).
    Fallback: JSON export (debugging/inspection only, not for KiCad).
    """

    def export(self, board: PCBBoard, path: str | Path, format: str = "kicad_pcb"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if format == "kicad_pcb":
            self._export_kicad_pcb(board, path)
        elif format == "json":
            self._export_json(board, path)
        else:
            self._export_json(board, path.with_suffix(".json"))

    def _export_kicad_pcb(self, board: PCBBoard, path: Path) -> None:
        """Export to .kicad_pcb using KiCad's native pcbnew API.

        This is the ONLY approved method for generating .kicad_pcb files.
        Falls back to JSON if pcbnew is not available.
        """
        from ..engines.kicad_native import KiCadEngine, KiCadNativeError, PCBNEW_AVAILABLE

        if not PCBNEW_AVAILABLE:
            print("Warning: pcbnew not available. Falling back to JSON export.")
            self._export_json(board, path.with_suffix(".json"))
            return

        engine = KiCadEngine()
        engine.create_board()

        # 1. Define board outline
        b = board.bounds
        engine.set_board_outline(b.x, b.y, b.w, b.h)

        # 2. Create nets
        for net_name, net_code in board.nets.items():
            engine.get_or_create_net(net_name, net_code)

        # 3. Place footprints
        for fp in board.footprints:
            try:
                engine.add_footprint(
                    reference=fp.reference,
                    fp_name=fp.footprint_name,
                    x_mm=fp.position.x,
                    y_mm=fp.position.y,
                    rotation_deg=fp.rotation,
                    layer=fp.layer,
                )
            except KiCadNativeError as e:
                print(f"Warning: skipping {fp.reference} — {e}")

        # 4. Add tracks
        for trace in board.traces:
            engine.add_track(
                x1_mm=trace.start.x,
                y1_mm=trace.start.y,
                x2_mm=trace.end.x,
                y2_mm=trace.end.y,
                width_mm=trace.width,
                layer_name=trace.layer,
                net_code=trace.net_code,
            )

        # 5. Add vias
        for via in board.vias:
            engine.add_via(
                x_mm=via.position.x,
                y_mm=via.position.y,
                size_mm=via.size,
                drill_mm=via.drill,
                net_code=via.net_code,
                top_layer=via.layers[0] if via.layers else "F.Cu",
                bottom_layer=via.layers[1] if len(via.layers) > 1 else "B.Cu",
            )

        # 6. Save
        engine.save_board(path)
        print(f"Board saved via pcbnew API to: {path}")

    def _export_json(self, board: PCBBoard, path: Path) -> None:
        """Export PCB as JSON for debugging/inspection."""
        data = {
            "name": board.name,
            "stackup": {"layers": board.stackup.num_layers},
            "bounds": {
                "x": board.bounds.x, "y": board.bounds.y,
                "w": board.bounds.w, "h": board.bounds.h,
            },
            "components": [
                {
                    "ref": fp.reference,
                    "footprint": fp.footprint_name,
                    "position": {"x": fp.position.x, "y": fp.position.y},
                    "rotation": fp.rotation,
                    "layer": fp.layer,
                }
                for fp in board.footprints
            ],
            "traces": [
                {
                    "start": {"x": t.start.x, "y": t.start.y},
                    "end": {"x": t.end.x, "y": t.end.y},
                    "width": t.width,
                    "layer": t.layer,
                    "net": t.net_code,
                }
                for t in board.traces
            ],
            "vias": [
                {
                    "position": {"x": v.position.x, "y": v.position.y},
                    "size": v.size,
                    "drill": v.drill,
                    "layers": list(v.layers),
                    "net": v.net_code,
                }
                for v in board.vias
            ],
            "summary": board.summary,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
