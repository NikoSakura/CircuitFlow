from __future__ import annotations
from typing import List, Tuple
from ..geometry.point import Point
from .astar import RouteVia


class ViaPlacer:
    """Determine optimal via positions for layer transitions.

    Avoids placing vias inside pads and respects via-to-trace clearance.
    """

    def __init__(self, via_size: float = 1.0, via_drill: float = 0.6):
        self.via_size = via_size
        self.via_drill = via_drill

    def place_via(
        self,
        position: Point,
        from_layer: str,
        to_layer: str,
        net_code: int,
        pad_positions: set[Point],
    ) -> RouteVia | None:
        """Place a via at given position if valid, or find nearest valid spot."""
        # Check if position overlaps any pad
        for pad_pos in pad_positions:
            if position.distance_to(pad_pos) < self.via_size:
                # Find nearest valid position
                candidates = [
                    Point(position.x + self.via_size * 2, position.y),
                    Point(position.x - self.via_size * 2, position.y),
                    Point(position.x, position.y + self.via_size * 2),
                    Point(position.x, position.y - self.via_size * 2),
                ]
                for cand in candidates:
                    if all(cand.distance_to(pp) >= self.via_size for pp in pad_positions):
                        return RouteVia(
                            position=cand,
                            layers=(from_layer, to_layer),
                            size=self.via_size,
                            drill=self.via_drill,
                            net_code=net_code,
                        )
                return None

        return RouteVia(
            position=position,
            layers=(from_layer, to_layer),
            size=self.via_size,
            drill=self.via_drill,
            net_code=net_code,
        )

    def place_layer_transitions(
        self,
        points: List[Tuple[Point, str]],  # (position, current_layer)
        target_layer: str,
        net_code: int,
        pad_positions: set[Point],
    ) -> List[RouteVia]:
        """Place vias at all points that need layer transitions."""
        vias = []
        for pos, layer in points:
            if layer != target_layer:
                via = self.place_via(pos, layer, target_layer, net_code, pad_positions)
                if via:
                    vias.append(via)
        return vias
