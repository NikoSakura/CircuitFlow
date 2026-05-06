"""KiCad native engine — wraps pcbnew API for all board operations.

This is the ONLY module allowed to create or modify .kicad_pcb content.
String concatenation / regex generation of board files is FORBIDDEN.
"""

from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import pcbnew

    PCBNEW_AVAILABLE = True
except ImportError:
    pcbnew = None  # type: ignore
    PCBNEW_AVAILABLE = False


class KiCadNativeError(Exception):
    """Raised when a pcbnew operation fails."""


class KiCadEngine:
    """Safe wrapper around KiCad's pcbnew Python API.

    All coordinates are in millimeters — conversion to KiCad internal
    units (nanometers) is handled internally via pcbnew.FromMM().
    """

    def __init__(self):
        if not PCBNEW_AVAILABLE:
            raise KiCadNativeError(
                "pcbnew module not available. Install KiCad to use KiCadEngine."
            )
        self._board: Optional[pcbnew.BOARD] = None
        self._net_map: dict[str, pcbnew.NETINFO_ITEM] = {}

    # ── Board lifecycle ─────────────────────────────────────────────

    def create_board(self) -> pcbnew.BOARD:
        """Create a new empty BOARD."""
        self._board = pcbnew.BOARD()
        self._net_map.clear()
        return self._board

    @property
    def board(self) -> pcbnew.BOARD:
        if self._board is None:
            raise KiCadNativeError("No board created. Call create_board() first.")
        return self._board

    def save_board(self, output_path: str | Path) -> None:
        """Persist the board to a .kicad_pcb file via pcbnew API."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pcbnew.SaveBoard(str(path), self.board)

    def load_board(self, path: str | Path) -> pcbnew.BOARD:
        """Load an existing .kicad_pcb file."""
        self._board = pcbnew.LoadBoard(str(path))
        self._rebuild_net_map()
        return self._board

    # ── Net management ──────────────────────────────────────────────

    def get_or_create_net(self, name: str, code: int) -> pcbnew.NETINFO_ITEM:
        """Return existing net or create a new one on the board."""
        if name in self._net_map:
            return self._net_map[name]
        net = pcbnew.NETINFO_ITEM(self.board, name, code)
        self.board.Add(net)
        self._net_map[name] = net
        return net

    def _rebuild_net_map(self) -> None:
        """Rebuild internal net lookup from the board's net info."""
        self._net_map.clear()
        net_info = self.board.GetNetInfo()
        for i in range(net_info.GetNetCount()):
            net = net_info.GetNetItem(i)
            self._net_map[net.GetNetname()] = net

    # ── Footprint library resolution ───────────────────────────────

    @staticmethod
    def _find_fp_library(nickname: str, fp_name: str = "") -> str | None:
        """Resolve a footprint library nickname to a filesystem path.

        Searches KiCad installation footprint directories for a .pretty
        folder matching the nickname. Includes alias mappings for common
        library name variations.
        """
        import os

        # Library name aliases (what the schematic says → what's installed)
        aliases = {
            "Connector_PinHeader": "Connector_PinHeader_2.54mm",
            "Connector_PinSocket": "Connector_PinSocket_2.54mm",
            "Package_QFN": "Package_DFN_QFN",
            "Package_SOIC": "Package_SO",
            "Package_TSSOP": "Package_SO",
            "Package_SSOP": "Package_SO",
            "Package_SON": "Package_SON",
            "Package_SOT": "Package_TO_SOT_SMD",
            "Capacitor_THT": "Capacitor_THT",
            "Resistor_THT": "Resistor_THT",
            "Crystal": "Crystal",
            "LED_SMD": "LED_SMD",
        }
        # Also try without the alias first — the original name might work
        nickname = aliases.get(nickname, nickname)

        search_roots = [
            "C:/Program Files/KiCad/10.0/share/kicad/footprints",
            "C:/Program Files/KiCad/share/kicad/footprints",
            os.path.expandvars("%LOCALAPPDATA%/kicad/10.0/footprints"),
            os.path.expandvars("%APPDATA%/kicad/10.0/footprints"),
        ]
        for root in search_roots:
            if not os.path.isdir(root):
                continue
            candidate = os.path.join(root, f"{nickname}.pretty")
            if os.path.isdir(candidate):
                return candidate
            try:
                for entry in os.listdir(root):
                    if entry.lower() == f"{nickname.lower()}.pretty":
                        return os.path.join(root, entry)
            except OSError:
                pass
        return None

    # ── Footprint placement ─────────────────────────────────────────

    def add_footprint(
        self,
        reference: str,
        fp_name: str,
        x_mm: float,
        y_mm: float,
        rotation_deg: float = 0.0,
        layer: str = "F.Cu",
    ) -> pcbnew.FOOTPRINT:
        """Load a footprint from KiCad libraries and place it on the board.

        Args:
            reference: Component reference designator (e.g. "U1", "R3").
            fp_name: Full footprint name including library (e.g. "Resistor_SMD:R_0603").
            x_mm, y_mm: Position in millimeters.
            rotation_deg: Rotation in degrees.
            layer: "F.Cu" or "B.Cu".

        Returns:
            The placed FOOTPRINT object.

        Raises:
            KiCadNativeError: If the footprint cannot be loaded.
        """
        # Parse library nickname and footprint name
        if ":" in fp_name:
            lib_nickname, fp_short = fp_name.split(":", 1)
        else:
            lib_nickname = ""
            fp_short = fp_name

        # Load the footprint from KiCad library (KiCad 10 API: needs filesystem path)
        lib_path = self._find_fp_library(lib_nickname, fp_short)
        if lib_path is None:
            raise KiCadNativeError(
                f"Footprint library '{lib_nickname}' not found. "
                f"Ensure KiCad is installed with footprint libraries."
            )

        module = pcbnew.FootprintLoad(lib_path, fp_short)

        # Fuzzy match: try to find a close match if exact not found
        if module is None:
            import os as _os
            try:
                best = None
                search = fp_short.lower().replace('_', '').replace('-', '')
                for f in _os.listdir(lib_path):
                    if f.endswith('.kicad_mod'):
                        fn = _os.path.splitext(f)[0].lower().replace('_', '').replace('-', '')
                        # Check if search is a substring of fn or vice versa
                        if search in fn or fn in search:
                            best = f
                            break
                if best:
                    module = pcbnew.FootprintLoad(lib_path, _os.path.splitext(best)[0])
            except OSError:
                pass

        if module is None:
            raise KiCadNativeError(
                f"Footprint '{fp_short}' not found in library '{lib_nickname}'. "
                f"Ensure the footprint library is configured in KiCad."
            )

        # Set reference
        module.SetReference(reference)

        # Set position (convert mm → KiCad internal nanometers)
        # Ensure numeric values, clamp to valid range
        try:
            nx = int(pcbnew.FromMM(float(x_mm)))
            ny = int(pcbnew.FromMM(float(y_mm)))
        except (ValueError, TypeError, OverflowError):
            raise KiCadNativeError(f"Invalid position for {reference}: ({x_mm}, {y_mm})")
        pos = pcbnew.VECTOR2I(nx, ny)
        module.SetPosition(pos)

        # Set rotation (KiCad 10+ uses EDA_ANGLE)
        module.SetOrientation(pcbnew.EDA_ANGLE(rotation_deg, pcbnew.DEGREES_T))

        # Set layer
        if layer == "B.Cu":
            module.Flip(pos)

        # Add to board
        self.board.Add(module)
        return module

    # ── Track / segment drawing ─────────────────────────────────────

    def add_track(
        self,
        x1_mm: float,
        y1_mm: float,
        x2_mm: float,
        y2_mm: float,
        width_mm: float,
        layer_name: str,
        net_code: int,
    ) -> pcbnew.TRACK:
        """Add a straight track segment to the board.

        Returns the created TRACK object.
        """
        track = pcbnew.PCB_TRACK(self.board)
        track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1_mm), pcbnew.FromMM(y1_mm)))
        track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2_mm), pcbnew.FromMM(y2_mm)))
        track.SetWidth(pcbnew.FromMM(width_mm))

        # Set layer
        layer_id = self._layer_name_to_id(layer_name)
        track.SetLayer(layer_id)

        # Set net
        track.SetNetCode(net_code)

        self.board.Add(track)
        return track

    def add_via(
        self,
        x_mm: float,
        y_mm: float,
        size_mm: float,
        drill_mm: float,
        net_code: int,
        top_layer: str = "F.Cu",
        bottom_layer: str = "B.Cu",
    ) -> pcbnew.VIA:
        """Add a via at the given position."""
        via = pcbnew.PCB_VIA(self.board)
        via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm)))
        via.SetWidth(pcbnew.FromMM(size_mm))
        via.SetDrill(pcbnew.FromMM(drill_mm))

        top_id = self._layer_name_to_id(top_layer)
        bottom_id = self._layer_name_to_id(bottom_layer)
        via.SetLayerPair(top_id, bottom_id)
        via.SetViaType(pcbnew.VIATYPE_THROUGH)
        via.SetNetCode(net_code)

        self.board.Add(via)
        return via

    # ── Board outline ───────────────────────────────────────────────

    def set_board_outline(
        self,
        x_mm: float,
        y_mm: float,
        w_mm: float,
        h_mm: float,
    ) -> None:
        """Draw the Edge.Cuts boundary rectangle."""
        x_nm = pcbnew.FromMM(x_mm)
        y_nm = pcbnew.FromMM(y_mm)
        w_nm = pcbnew.FromMM(w_mm)
        h_nm = pcbnew.FromMM(h_mm)

        edges = [
            (x_nm, y_nm, x_nm + w_nm, y_nm),
            (x_nm + w_nm, y_nm, x_nm + w_nm, y_nm + h_nm),
            (x_nm + w_nm, y_nm + h_nm, x_nm, y_nm + h_nm),
            (x_nm, y_nm + h_nm, x_nm, y_nm),
        ]
        for sx, sy, ex, ey in edges:
            seg = pcbnew.PCB_SHAPE(self.board)
            seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
            seg.SetStart(pcbnew.VECTOR2I(sx, sy))
            seg.SetEnd(pcbnew.VECTOR2I(ex, ey))
            seg.SetLayer(pcbnew.Edge_Cuts)
            seg.SetWidth(pcbnew.FromMM(0.15))
            self.board.Add(seg)

    # ── DSN export for FreeRouting ──────────────────────────────────

    def export_dsn(self, dsn_path: str | Path) -> None:
        """Export board to Specctra DSN format for FreeRouting."""
        pcbnew.ExportSpecctraDSN(self.board, str(dsn_path))

    # ── Helpers ─────────────────────────────────────────────────────

    def _layer_name_to_id(self, name: str) -> int:
        """Convert a KiCad layer name string to its internal layer ID."""
        # KiCad 10: use board's GetLayerID
        try:
            return self.board.GetLayerID(name)
        except Exception:
            pass
        # Fallback: known layer names
        if name == "F.Cu": return 0
        if name == "B.Cu": return 31  # back copper
        if name == "Edge.Cuts": return 44
        raise KiCadNativeError(f"Unknown layer name: {name}")

    @staticmethod
    def mm_to_kicad(mm: float) -> int:
        """Convert millimeters to KiCad internal units (nanometers)."""
        return pcbnew.FromMM(mm)

    @staticmethod
    def kicad_to_mm(nm: int) -> float:
        """Convert KiCad internal units to millimeters."""
        return pcbnew.ToMM(nm)
