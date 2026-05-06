"""Hierarchical placement with connectivity-based grouping.

Small designs (<30 components): force-directed for tight grouping.
Large designs: connectivity-based block partitioning + grid within blocks.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
import math, random
from collections import defaultdict
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..schematic.model import Design


@dataclass
class PlacementResult:
    component: str
    position: Point
    rotation: float
    layer: str


@dataclass
class PlacementMetrics:
    total_wirelength: float
    bounding_box_area: float
    density_max: int
    thermal_score: float = 0.0


@dataclass
class PlacementSolution:
    placements: List[PlacementResult]
    score: float
    metrics: Optional[PlacementMetrics] = None
    board_bounds: Optional[Rect] = None

    def get_position(self, ref: str) -> Optional[Point]:
        for p in self.placements:
            if p.component == ref:
                return p.position
        return None


class ForceDirectedPlacer:
    """Hierarchical placer — force-directed for small, block-based for large."""

    def __init__(self, design: Design, board_bounds: Rect = None,
                 iterations: int = 300):
        self.design = design
        self.board_bounds = board_bounds or Rect(0, 0, 200, 160)
        self.iterations = iterations
        self.random = random.Random(42)
        self.SMALL_THRESHOLD = 30  # switch to hierarchical above this

    def place(self, fixed_positions: Dict[str, Point] | None = None) -> PlacementSolution:
        # If design components have schematic positions, use hybrid approach
        if self._has_schematic_positions():
            return self._place_hybrid(fixed_positions)

        return self._place_algorithmic(fixed_positions)

    def _place_hybrid(self, fixed_positions: Dict[str, Point] | None = None) -> PlacementSolution:
        """Double-sided compact placement with functional grouping."""
        refs = list(self.design.components.keys())
        # Use board_bounds as canvas if provided (for tests)
        canvas_w = self.board_bounds.w if self.board_bounds and self.board_bounds.w < 500 else 75
        canvas_h = self.board_bounds.h if self.board_bounds and self.board_bounds.h < 500 else 200
        n = len(refs)
        sch_pos = {ref: (c.position.x, c.position.y) for ref, c in self.design.components.items()}

        # Split evenly: ICs + half passives on top, half passives on bottom
        ics = [r for r in refs if r[:1] in 'UJQXY']
        passives = [r for r in refs if r not in ics]
        # Assign passives to balance: even indices on top, odd on bottom
        top_refs = ics + [r for i, r in enumerate(passives) if i % 2 == 0]
        bottom_refs = [r for i, r in enumerate(passives) if i % 2 == 1]

        # Sort each side by schematic position
        def sort_key(r):
            x, y = sch_pos.get(r, (0, 0))
            return (round(y / 25) * 25, x)
        top_refs.sort(key=sort_key)
        bottom_refs.sort(key=sort_key)

        positions: Dict[str, Tuple[float, float]] = {}
        layers: Dict[str, str] = {}

        def grid_place(side_refs, layer_name, start_x, start_y):
            """Place components in a tight grid. Returns max (x, y) reached."""
            cell_w, cell_h = 5.5, 5.0
            cx, cy = start_x, start_y
            max_row_h = max_x = 0
            for ref in side_refs:
                is_large = ref[:1] in 'UJQXY'
                cw = 8.0 if is_large else 4.5
                ch = 8.0 if is_large else 4.0
                if cx + cw > canvas_w - 5 and cx > start_x + 10:
                    cx = start_x
                    cy += max_row_h + 2.0
                    max_row_h = 0
                positions[ref] = (min(cx + cw / 2, canvas_w - 2), cy + ch / 2)
                layers[ref] = layer_name
                cx += cw + 1.5
                max_row_h = max(max_row_h, ch)
                max_x = max(max_x, cx)
            return max_x, cy + max_row_h

        # Both sides share the same board area
        max_x1, max_y1 = grid_place(top_refs, "F.Cu", 4.0, 4.0)
        max_x2, max_y2 = grid_place(bottom_refs, "B.Cu", 4.0, 4.0)
        max_y = max(max_y1, max_y2)

        # Auto-size
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        m = 4.0
        if not xs: xs = [0, 50]
        if not ys: ys = [0, 30]
        w, h = max(50, max(xs) - min(xs) + 2*m), max(20, max(ys) - min(ys) + 2*m)
        sx, sy = -min(xs) + m, -min(ys) + m
        for ref in positions:
            positions[ref] = (positions[ref][0] + sx, positions[ref][1] + sy)

        placements = [PlacementResult(ref,
            Point(positions[ref][0], positions[ref][1]), 0.0,
            layers.get(ref, "F.Cu"))
            for ref in refs]
        wl = self._estimate_wirelength(placements)
        return PlacementSolution(
            placements=placements, score=wl,
            metrics=PlacementMetrics(total_wirelength=wl, bounding_box_area=w*h, density_max=n),
            board_bounds=Rect(0, 0, w, h),
        )

    def _has_schematic_positions(self) -> bool:
        """Check if components have non-zero positions from schematic parsing."""
        positions = [c.position for c in self.design.components.values()]
        if not positions: return False
        xs = [p.x for p in positions if p.x != 0]
        ys = [p.y for p in positions if p.y != 0]
        # At least 80% of components must have non-zero positions
        return len(xs) > len(positions) * 0.8 and max(xs) - min(xs) > 100

    def _place_by_schematic(self) -> PlacementSolution:
        """Place components by scaling schematic positions to PCB dimensions.

        This preserves the logical grouping from the schematic — functionally
        related components that are near each other in the schematic stay
        near each other on the PCB. This is the standard approach used by
        professional PCB designers (place_by_sch method).
        """
        refs = list(self.design.components.keys())
        n = len(refs)

        # Get schematic positions
        sch_positions = {ref: (c.position.x, c.position.y)
                        for ref, c in self.design.components.items()}

        xs = [p[0] for p in sch_positions.values()]
        ys = [p[1] for p in sch_positions.values()]

        sch_w = max(xs) - min(xs)
        sch_h = max(ys) - min(ys)

        # Use provided board bounds if available (e.g. from test fixtures)
        if self.board_bounds and self.board_bounds.w < 500:
            target_w, target_h = self.board_bounds.w, self.board_bounds.h
        else:
            # Auto-size from footprint geometry
            total_fp_area = 0.0
            for ref, c in self.design.components.items():
                fp_name = c.footprint_name
                if not fp_name: continue
                if '0603' in fp_name or '1608' in fp_name: total_fp_area += 1.6 * 0.8
                elif '0805' in fp_name or '2012' in fp_name: total_fp_area += 2.0 * 1.2
                elif 'SOD-123' in fp_name: total_fp_area += 3.5 * 1.5
                elif 'QFN' in fp_name or 'DIP' in fp_name or 'PinHeader' in fp_name: total_fp_area += 8.0 * 8.0
                elif 'Crystal' in fp_name: total_fp_area += 3.2 * 2.5
                elif 'TestPoint' in fp_name: total_fp_area += 1.5 * 1.5
                else: total_fp_area += 3.0 * 2.0
            target_area = total_fp_area * 2.5
            target_w = max(50, min(70, math.sqrt(target_area * 2.5)))
            target_h = max(20, min(35, target_w / 2.5))

        # Scale factors
        scale_x = target_w / sch_w if sch_w > 0 else 1
        scale_y = target_h / sch_h if sch_h > 0 else 1
        # Use uniform scale to preserve aspect ratio
        scale = min(scale_x, scale_y)

        # Center in PCB area
        offset_x = (target_w - sch_w * scale) / 2 - min(xs) * scale + 10
        offset_y = (target_h - sch_h * scale) / 2 - min(ys) * scale + 10

        placements = []
        for ref in refs:
            sx, sy = sch_positions[ref]
            px = sx * scale + offset_x
            py = target_h - (sy * scale + offset_y) + 20
            # Clamp to board bounds
            margin = 10.0
            px = max(margin, min(target_w - margin, px))
            py = max(margin, min(target_h - margin, py))
            placements.append(PlacementResult(ref, Point(px, py), 0.0, "F.Cu"))

        wl = self._estimate_wirelength(placements)
        bb = self._compute_bounding_box(placements)

        return PlacementSolution(
            placements=placements, score=wl,
            metrics=PlacementMetrics(total_wirelength=wl,
                                     bounding_box_area=target_w * target_h,
                                     density_max=n),
            board_bounds=Rect(0, 0, target_w + 20, target_h + 40),
        )

    def _place_algorithmic(self, fixed_positions: Dict[str, Point] | None = None) -> PlacementSolution:
        refs = list(self.design.components.keys())
        n = len(refs)
        if n == 0:
            return PlacementSolution(placements=[], score=0)

        # ── Build adjacency ─────────────────────────────────────
        adjacency: Dict[str, Set[str]] = {ref: set() for ref in refs}
        for net in self.design.nets.values():
            net_refs = [p.component_ref for p in net.pins if p.component_ref in adjacency]
            for i, r1 in enumerate(net_refs):
                for r2 in net_refs[i + 1:]:
                    adjacency[r1].add(r2)
                    adjacency[r2].add(r1)

        if n <= self.SMALL_THRESHOLD:
            positions = self._force_directed(refs, adjacency, n)
        else:
            positions = self._hierarchical_blocks(refs, adjacency, n)

        # Build result
        placements = [PlacementResult(ref, Point(x, y), 0.0, "F.Cu")
                      for ref, (x, y) in positions.items()]
        wl = self._estimate_wirelength(placements)
        bb = self._compute_bounding_box(placements)

        # Auto-size board tightly
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        margin = 10
        w = max(60, max(xs) - min(xs) + 2 * margin)
        h = max(40, max(ys) - min(ys) + 2 * margin)
        shift_x = -min(xs) + margin
        shift_y = -min(ys) + margin

        # Shift all positions
        for ref in positions:
            x, y = positions[ref]
            positions[ref] = (x + shift_x, y + shift_y)

        # Rebuild placements after shift
        placements = [PlacementResult(ref, Point(positions[ref][0], positions[ref][1]), 0.0, "F.Cu")
                      for ref in refs]

        return PlacementSolution(
            placements=placements, score=wl,
            metrics=PlacementMetrics(total_wirelength=wl,
                                     bounding_box_area=w * h, density_max=n),
            board_bounds=Rect(0, 0, w, h),
        )

    # ── Force-directed (small designs) ──────────────────────────

    def _force_directed(self, refs, adjacency, n):
        k = 15.0 + n * 0.5
        positions: Dict[str, Tuple[float, float]] = {}
        # Use board center if bounds provided, else virtual center
        bw, bh = self.board_bounds.w, self.board_bounds.h
        cx, cy = bw / 2, bh / 2

        # Place most-connected IC at center
        ics = [r for r in refs if r and r[0].upper() in ('U', 'J', 'Q')]
        if ics:
            main = max(ics, key=lambda r: len(adjacency[r]))
            positions[main] = (cx, cy)
            for ic in ics[1:]:
                a = self.random.uniform(0, 2 * math.pi)
                positions[ic] = (cx + k * 0.3 * math.cos(a), cy + k * 0.3 * math.sin(a))

        # Place rest near connections
        for ref in refs:
            if ref in positions:
                continue
            conn = [c for c in adjacency[ref] if c in positions]
            if conn:
                mx = sum(positions[c][0] for c in conn) / len(conn)
                my = sum(positions[c][1] for c in conn) / len(conn)
                a = self.random.uniform(0, 2 * math.pi)
                d = k * 0.3
                positions[ref] = (mx + d * math.cos(a), my + d * math.sin(a))
            else:
                a = self.random.uniform(0, 2 * math.pi)
                positions[ref] = (cx + k * 0.1 * math.cos(a), cy + k * 0.1 * math.sin(a))

        # Force iterations (short — just enough to tighten)
        t = k * 0.3
        t_min = 0.05
        cooling = (t_min / t) ** (1.0 / max(self.iterations, 200))
        for _ in range(max(self.iterations, 200)):
            disp = {ref: (0.0, 0.0) for ref in refs}
            # Center of mass pulling
            mx = sum(p[0] for p in positions.values()) / n
            my = sum(p[1] for p in positions.values()) / n
            for ref in refs:
                dx = mx - positions[ref][0]
                dy = my - positions[ref][1]
                d = math.hypot(dx, dy)
                if d > 0.1:
                    f = d * (0.5 if len(adjacency[ref]) == 0 else 0.05)
                    disp[ref] = (disp[ref][0] + dx / d * f, disp[ref][1] + dy / d * f)
            # Repulsion
            rl = list(refs)
            for i in range(n):
                r1, x1, y1 = rl[i], positions[rl[i]][0], positions[rl[i]][1]
                for j in range(i + 1, n):
                    r2, x2, y2 = rl[j], positions[rl[j]][0], positions[rl[j]][1]
                    dx, dy = x1 - x2, y1 - y2
                    d = math.hypot(dx, dy)
                    if d < 0.5: d = 0.5
                    f = k * k / d
                    fx, fy = dx / d * f, dy / d * f
                    disp[r1] = (disp[r1][0] + fx, disp[r1][1] + fy)
                    disp[r2] = (disp[r2][0] - fx, disp[r2][1] - fy)
            # Apply with boundary clamping
            for ref in refs:
                dx, dy = disp[ref]
                m = math.hypot(dx, dy)
                if m < 0.01: continue
                capped = min(m, t)
                scale = capped / m
                nx = max(5, min(bw - 5, positions[ref][0] + dx * scale))
                ny = max(5, min(bh - 5, positions[ref][1] + dy * scale))
                positions[ref] = (nx, ny)
            t *= cooling

        return positions

    # ── Hierarchical blocks (large designs) ─────────────────────

    def _hierarchical_blocks(self, refs, adjacency, n):
        """Partition components by functional group (ref number ranges) then place in grid."""
        # Step 1: Group by reference number ranges (reflects schematic sheets)
        def extract_num(ref):
            """Extract numeric part of reference: R12 -> 12, C100 -> 100"""
            digits = ''.join(ch for ch in ref if ch.isdigit())
            return int(digits) if digits else 0

        def ref_prefix(ref):
            return ''.join(ch for ch in ref if not ch.isdigit())

        # Group components by prefix, then by numeric range
        by_prefix = {}
        for ref in refs:
            pfx = ref_prefix(ref)
            if pfx not in by_prefix: by_prefix[pfx] = []
            by_prefix[pfx].append(ref)

        # Sort each prefix group by number
        for pfx in by_prefix:
            by_prefix[pfx].sort(key=extract_num)

        # Place connectors (J), test points (TP), switches (SW) at board edges
        edge_refs = set()
        for pfx in ['J', 'TP', 'SW', 'BT']:
            if pfx in by_prefix:
                edge_refs.update(by_prefix[pfx])

        # Identify major ICs (U*) and their surrounding passives
        ics = by_prefix.get('U', []) + by_prefix.get('Q', [])
        ics.sort(key=extract_num)

        # Build functional blocks: each IC + nearby passives
        # Passives are assigned to the IC with closest reference number
        passive_refs = [r for r in refs if ref_prefix(r) in ('R', 'C', 'L', 'D', 'LED', 'F')]
        ic_blocks = {}
        for ic in ics:
            ic_blocks[ic] = [ic]

        # Assign passives to nearest IC by ref number
        ic_nums = [(ic, extract_num(ic)) for ic in ics]
        for pref in passive_refs:
            pnum = extract_num(pref)
            # Find closest IC by ref number
            best_ic, best_dist = None, float('inf')
            for ic, inum in ic_nums:
                dist = abs(pnum - inum)
                if dist < best_dist:
                    best_ic, best_dist = ic, dist
            if best_ic and best_dist < 50:  # within reasonable range
                ic_blocks[best_ic].append(pref)
            else:
                # Standalone passive - add to misc block
                if '_misc' not in ic_blocks:
                    ic_blocks['_misc'] = []
                ic_blocks['_misc'].append(pref)

        # Add connectors to separate blocks
        for ref in edge_refs:
            if ref not in sum(ic_blocks.values(), []):
                if '_connectors' not in ic_blocks:
                    ic_blocks['_connectors'] = []
                ic_blocks['_connectors'].append(ref)

        # Step 2: Layout blocks - ICs in center, connectors on edges
        positions: Dict[str, Tuple[float, float]] = {}
        cell_w, cell_h = 9.0, 8.0

        # Place IC blocks in a grid
        ic_block_keys = [k for k in ic_blocks.keys() if k in ics]
        misc_keys = [k for k in ic_blocks.keys() if k not in ic_block_keys]

        # IC grid: ~4-5 per row
        ic_cols = max(1, min(5, int(math.sqrt(len(ic_block_keys)) * 1.5)))
        ic_spacing_x = 35.0  # mm between IC block centers
        ic_spacing_y = 30.0

        for idx, ic in enumerate(ic_block_keys):
            block = ic_blocks[ic]
            ic_row = idx // ic_cols
            ic_col = idx % ic_cols
            bx = 15 + ic_col * ic_spacing_x
            by = 15 + ic_row * ic_spacing_y

            # Place IC first (center of block)
            positions[ic] = (bx + 15, by + 8)

            # Place passives around IC in tight grid
            passives = [r for r in block if r != ic]
            pcols = max(1, int(math.sqrt(len(passives)) * 1.3))
            for pi, ref in enumerate(passives):
                prow = pi // pcols
                pcol = pi % pcols
                x = bx + pcol * cell_w + cell_w / 2
                y = by + 16 + prow * cell_h + cell_h / 2  # below IC
                positions[ref] = (x, y)

        # Catch any refs not yet placed (unusual prefixes, isolated components)
        for ref in refs:
            if ref not in positions:
                if '_misc' not in ic_blocks:
                    ic_blocks['_misc'] = []
                if ref not in ic_blocks['_misc']:
                    ic_blocks['_misc'].append(ref)

        # Place misc/connector blocks below IC area
        last_ic_y = max((p[1] for p in positions.values()), default=0) + 20
        misc_x = 15.0
        for key in misc_keys:
            block = ic_blocks[key]
            cols = max(1, int(math.sqrt(len(block)) * 1.3))
            for i, ref in enumerate(sorted(block, key=extract_num)):
                row = i // cols
                col = i % cols
                x = misc_x + col * cell_w + cell_w / 2
                y = last_ic_y + row * cell_h + cell_h / 2
                positions[ref] = (x, y)
            last_ic_y += ((len(block) - 1) // cols + 1) * cell_h + 15

        return positions

        return positions

    def _estimate_wirelength(self, placements: List[PlacementResult]) -> float:
        pos = {p.component: p.position for p in placements}
        total = 0.0
        for net in self.design.nets.values():
            refs = [p.component_ref for p in net.pins if p.component_ref in pos]
            if len(refs) < 2: continue
            xs = [pos[r].x for r in refs]
            ys = [pos[r].y for r in refs]
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    def _compute_bounding_box(self, placements: List[PlacementResult]) -> Rect:
        if not placements: return Rect(0, 0, 0, 0)
        xs = [p.position.x for p in placements]
        ys = [p.position.y for p in placements]
        return Rect(min(xs) - 5, min(ys) - 5, max(xs) - min(xs) + 10, max(ys) - min(ys) + 10)
