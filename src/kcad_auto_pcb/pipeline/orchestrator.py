from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..config.settings import AppSettings
from ..geometry.point import Point
from ..geometry.rect import Rect
from ..geometry.grid import Grid
from ..schematic.parser import SchematicParser
from ..schematic.connectivity import ConnectivityGraph
from ..footprint.cache import FootprintCache
from ..placement.force_directed import ForceDirectedPlacer
from ..placement.legalizer import Legalizer
from ..placement.cost_function import PlacementCost
from ..placement.llm_optimizer import LLMPlacementOptimizer
from ..routing.multi_layer import MultiLayerRouter
from ..routing.ordering import NetOrdering
from ..routing.llm_router import LLMRoutingStrategy
from ..geometry.grid import Grid
from ..geometry.rect import Rect
from ..pcb.board_builder import BoardBuilder
from ..pcb.stackup import BoardStackup
from ..pcb.exporter import PCBExporter
from ..llm.base import AbstractLLMBackend, LLMBackendFactory
from ..llm.token_counter import TokenBudget
from .context import PipelineContext


class PipelineOrchestrator:
    """Orchestrates the schematic → PCB pipeline.

    LLM is used for high-level design decisions:
    1. Placement review and optimization
    2. Layer assignment strategy
    3. Net ordering for routing priority
    """

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.placement_llm: Optional[AbstractLLMBackend] = None
        self.routing_llm: Optional[AbstractLLMBackend] = None
        self.token_budget = TokenBudget(settings.token_budget_per_run)

        # Initialize LLM backends if configured
        if settings.placement_llm_spec:
            self.placement_llm = self._create_backend(
                settings.placement_llm_spec,
                settings.openai_api_key or settings.deepseek_api_key,
                settings.anthropic_api_key,
                settings.deepseek_api_key,
                settings.ollama_base_url,
            )

        if settings.routing_llm_spec:
            self.routing_llm = self._create_backend(
                settings.routing_llm_spec,
                settings.openai_api_key or settings.deepseek_api_key,
                settings.anthropic_api_key,
                settings.deepseek_api_key,
                settings.ollama_base_url,
            )

    def _create_backend(self, spec: str, openai_key=None, anthropic_key=None,
                        deepseek_key=None, ollama_url=None) -> Optional[AbstractLLMBackend]:
        try:
            provider = spec.split(":")[0]
            kwargs = {}
            if provider in ("openai", "openai_compatible"):
                kwargs["api_key"] = openai_key
            elif provider == "anthropic":
                kwargs["api_key"] = anthropic_key
            elif provider == "deepseek":
                kwargs["api_key"] = deepseek_key or openai_key
            elif provider == "ollama":
                kwargs["base_url"] = ollama_url or "http://localhost:11434"
            return LLMBackendFactory.create(spec, **kwargs)
        except Exception:
            return None

    async def run(
        self,
        schematic_path: str,
        output_path: str,
        board_layers: int = 2,  # clamped to valid range 2-16
        board_width: float | None = None,
        board_height: float | None = None,
        enable_llm_placement: bool = True,
        enable_llm_routing: bool = True,
        pre_parsed_design=None,  # Skip parsing if Design already extracted (e.g. from PDF via LLM)
    ) -> PipelineContext:
        """Execute the full pipeline."""
        ctx = PipelineContext(
            source_path=schematic_path,
            output_path=output_path,
        )
        ctx.stage = "parse"

        # Stage 1: Parse schematic (or use pre-parsed design)
        if pre_parsed_design is not None:
            ctx.design = pre_parsed_design
        else:
            suffix = Path(schematic_path).suffix.lower()
            if suffix == ".pdf":
                try:
                    from ..schematic.pdf_parser import PDFSchematicParser
                    ctx.design = PDFSchematicParser().parse(schematic_path)
                except Exception:
                    parser = SchematicParser()
                    ctx.design = parser.parse(schematic_path)
            else:
                parser = SchematicParser()
                ctx.design = parser.parse(schematic_path)
        ctx.stats["components"] = ctx.design.component_count
        ctx.stats["nets"] = ctx.design.net_count

        # Build connectivity graph
        conn = ConnectivityGraph(ctx.design)
        ctx.stats["connections"] = conn.edge_count()

        # Stage 2: Resolve footprints
        ctx.stage = "footprints"
        fp_cache = FootprintCache()
        for ref, comp in ctx.design.components.items():
            if comp.footprint_name:
                fp_cache.get(comp.footprint_name)
                ctx.footprint_map[ref] = comp.footprint_name

        for net_name, net in ctx.design.nets.items():
            ctx.net_map[net_name] = net.code

        # Stage 3: Place components
        ctx.stage = "place"
        bw = board_width or self.settings.default_board_width
        bh = board_height or self.settings.default_board_height

        # Auto-size board based on component count
        n = ctx.design.component_count
        if board_width is None and board_height is None:
            bw = min(max(50, n * 12), 300)
            bh = min(max(40, n * 10), 240)

        board_bounds = Rect(0, 0, bw, bh)

        placer = ForceDirectedPlacer(
            ctx.design, board_bounds,
            iterations=self.settings.force_directed_iterations,
        )
        solution = placer.place()

        # Legalize (snap to grid, resolve overlaps)
        legalizer = Legalizer(fp_cache, self.settings.grid_resolution)
        solution = legalizer.legalize(solution, ctx.footprint_map)

        # LLM placement optimization
        if enable_llm_placement and self.placement_llm:
            if self.token_budget.can_call(500):
                optimizer = LLMPlacementOptimizer(
                    self.placement_llm, self.token_budget
                )
                solution = await optimizer.optimize(
                    solution, ctx.design, ctx.footprint_map, fp_cache
                )

        # Score placement
        cost = PlacementCost(ctx.design, board_bounds)
        score, metrics = cost.score(solution.placements)
        solution.score = score
        ctx.placement = solution
        ctx.stats["placement_score"] = score

        # Stage 4: Route
        ctx.stage = "route"
        board_layers = max(2, min(16, board_layers))  # clamp to valid range
        stackup = BoardStackup(board_layers)

        # Build pad layer map (needed by both A* and DSN export)
        pad_layer_map = {}
        for p in solution.placements:
            fp_name = ctx.footprint_map.get(p.component, "")
            fp = fp_cache.get(fp_name)
            pad_layer = p.layer
            if fp:
                for pad in fp.pads:
                    key = (p.component, pad.number)
                    pos = Point(
                        p.position.x + pad.position.x,
                        p.position.y + pad.position.y,
                    )
                    pad_layer_map[key] = (pos, pad_layer)

        # ── Try FreeRouting (per important.md) ───────────────────────
        freerouting_ok = False
        fr_mgr = None
        try:
            from ..engines.freerouting_mgr import FreeRoutingManager, DSNComponentInfo, DSNPadInfo, DSNNetInfo

            fr_mgr = FreeRoutingManager(
                jar_path=self.settings.freerouting_jar_path,
                max_passes=self.settings.freerouting_max_passes,
            )
            if fr_mgr.is_available():
                # Build DSN component list from placement
                dsn_components = []
                for p in solution.placements:
                    fp_name = ctx.footprint_map.get(p.component, "")
                    fp = fp_cache.get(fp_name)
                    pads = []
                    if fp:
                        for pad in fp.pads:
                            pads.append(DSNPadInfo(
                                number=pad.number,
                                abs_x=p.position.x + pad.position.x,
                                abs_y=p.position.y + pad.position.y,
                                shape=pad.shape if pad.shape != "oval" else "roundrect",
                                size_w=pad.size[0],
                                size_h=pad.size[1],
                            ))
                    dsn_components.append(DSNComponentInfo(
                        reference=p.component,
                        footprint_name=fp_name,
                        x=p.position.x, y=p.position.y,
                        rotation=p.rotation,
                        side="back" if p.layer == "B.Cu" else "front",
                        pads=pads,
                    ))

                # Build DSN net list
                dsn_nets = []
                net_ordering = NetOrdering.order(ctx.design)
                layer_names = stackup.layer_names
                for net_name in net_ordering:
                    net = ctx.design.nets[net_name]
                    pin_pairs = []
                    for pin_ref in net.pins:
                        if pin_ref.component_ref in ctx.design.components:
                            pin_pairs.append((pin_ref.component_ref, pin_ref.pin_number))
                    if len(pin_pairs) >= 2:
                        dsn_nets.append(DSNNetInfo(
                            name=net_name, code=net.code, pins=pin_pairs,
                        ))

                # Try pcbnew path first
                try:
                    from ..engines.kicad_native import KiCadEngine, PCBNEW_AVAILABLE
                    if PCBNEW_AVAILABLE:
                        engine = KiCadEngine()
                        engine.create_board()

                        # Enable both copper layers for routing
                        engine.board.SetCopperLayerCount(2)

                        engine.set_board_outline(0, 0, bw, bh)
                        for net_name, net_code in ctx.net_map.items():
                            engine.get_or_create_net(net_name, net_code)
                        # Place footprints and assign nets to pads
                        placed_modules = {}
                        for p in solution.placements:
                            fp_name = ctx.footprint_map.get(p.component, "")
                            if fp_name:
                                try:
                                    mod = engine.add_footprint(
                                        reference=p.component, fp_name=fp_name,
                                        x_mm=p.position.x, y_mm=p.position.y,
                                        rotation_deg=p.rotation, layer=p.layer,
                                    )
                                    placed_modules[p.component] = mod
                                except Exception as e:
                                    print(f"  Skipping {p.component}: {e}")

                        # Assign net codes to pads based on schematic connectivity
                        for net_name, net in ctx.design.nets.items():
                            net_info = engine.get_or_create_net(net_name, net.code)
                            for pin_ref in net.pins:
                                ref = pin_ref.component_ref
                                pin_num = pin_ref.pin_number
                                if ref in placed_modules:
                                    mod = placed_modules[ref]
                                    # Find the matching pad
                                    for pad in mod.Pads():
                                        if pad.GetNumber() == pin_num:
                                            pad.SetNet(net_info)
                                            break

                        dsn_path = Path(output_path).with_suffix(".dsn")
                        ses_path = fr_mgr.route_board(engine.board, dsn_path)
                        if ses_path:
                            freerouting_ok = True
                            ctx.stats["router"] = "FreeRouting (pcbnew)"
                            ctx._engine = engine
                            print("  FreeRouting completed (pcbnew path).")
                except ImportError:
                    pass

                # If pcbnew path didn't work, try pure-Python DSN path
                if not freerouting_ok:
                    print("  Trying pure-Python DSN + FreeRouting path...")
                    dsn_path = Path(output_path).with_suffix(".dsn")
                    ses_results = fr_mgr.route_via_dsn(
                        dsn_path, dsn_components, dsn_nets,
                        layer_names, bw, bh,
                    )

                    if ses_results:
                        freerouting_ok = True
                        ctx.stats["router"] = "FreeRouting (pure Python)"

                        # Build RoutingSolution from SES results
                        from ..routing.astar import RouteSegment, RouteVia, RoutingSolution
                        ctx.routing = RoutingSolution()
                        for net_res in ses_results:
                            net = ctx.design.nets.get(net_res.net_name)
                            net_code = net.code if net else 0
                            for w in net_res.wires:
                                ctx.routing.segments.append(RouteSegment(
                                    start=Point(w.x1, w.y1),
                                    end=Point(w.x2, w.y2),
                                    width=w.width,
                                    layer=w.layer,
                                    net_code=net_code,
                                ))
                            for v in net_res.vias:
                                ctx.routing.vias.append(RouteVia(
                                    position=Point(v.x, v.y),
                                    layers=(v.top_layer, v.bottom_layer),
                                    size=v.size,
                                    drill=v.drill,
                                    net_code=net_code,
                                ))
                            ctx.routing.total_wirelength += sum(
                                math.hypot(w.x2 - w.x1, w.y2 - w.y1)
                                for w in net_res.wires
                            )
                        ctx.routing.via_count = len(ctx.routing.vias)
                        print(f"  FreeRouting completed (pure Python). "
                              f"{len(ctx.routing.segments)} segments, {ctx.routing.via_count} vias.")
                    else:
                        print("  FreeRouting produced no results.")
            else:
                print(f"FreeRouting JAR not found at {self.settings.freerouting_jar_path}")
        except ImportError as e:
            print(f"  FreeRouting import error: {e}")
        except Exception as e:
            print(f"  FreeRouting error: {e}")

        # ── Fallback: A* grid-based router ──────────────────────────
        if not freerouting_ok:
            print("  Using A* fallback router...")
            ctx.stats["router"] = "A*"

            grid = Grid(bw, bh, self.settings.grid_resolution)
            for p in solution.placements:
                fp_name = ctx.footprint_map.get(p.component, "")
                fp = fp_cache.get(fp_name)
                if fp:
                    c = fp.courtyard
                    grid.add_rect_obstacle(
                        p.position.x + c.x, p.position.y + c.y,
                        c.w, c.h, margin=0.3,
                    )

            # Layer assignments
            ordered = NetOrdering.order(ctx.design)
            layer_assignments = {}
            for net_name in ordered:
                net = ctx.design.nets[net_name]
                if net.is_power:
                    layer_assignments[net_name] = ["F.Cu", "B.Cu"]
                else:
                    layer_assignments[net_name] = ["F.Cu", "B.Cu"]

            router = MultiLayerRouter(
                stackup.as_routing_stackup(), grid,
                clearance=self.settings.default_clearance,
            )
            for (pos, _) in pad_layer_map.values():
                for lg in [grid] + list(router.layer_grids.values()):
                    r1, c1 = lg.world_to_grid(Point(pos.x - 0.6, pos.y - 0.6))
                    r2, c2 = lg.world_to_grid(Point(pos.x + 0.6, pos.y + 0.6))
                    r1, c1 = max(0, r1), max(0, c1)
                    r2, c2 = min(lg.rows - 1, r2), min(lg.cols - 1, c2)
                    lg.cells[r1:r2+1, c1:c2+1] = 0

            ctx.routing = router.route_all_nets(
                ctx.design, pad_layer_map, layer_assignments,
                width=self.settings.default_trace_width,
            )

        ctx.stats["routed_segments"] = len(ctx.routing.segments) if ctx.routing else 0
        ctx.stats["unrouted_nets"] = len(ctx.routing.unrouted_nets) if ctx.routing else 0
        ctx.stats["vias"] = ctx.routing.via_count if ctx.routing else 0

        # Stage 5: Export
        ctx.stage = "export"

        if hasattr(ctx, '_engine'):
            engine = ctx._engine
            engine.save_board(output_path)
            print(f"Board saved via pcbnew to: {output_path}")
        else:
            builder = BoardBuilder(fp_cache)
            board = builder.build(
                name=Path(schematic_path).stem,
                placement=solution,
                routing=ctx.routing,
                stackup=stackup,
                footprint_map=ctx.footprint_map,
                net_map=ctx.net_map,
            )
            ctx.board = board
            exporter = PCBExporter()
            exporter.export(board, output_path)

        # Always export JSON
        builder = BoardBuilder(fp_cache)
        json_board = builder.build(
            name=Path(schematic_path).stem,
            placement=solution,
            routing=ctx.routing,
            stackup=stackup,
            footprint_map=ctx.footprint_map,
            net_map=ctx.net_map,
        )
        exporter = PCBExporter()
        exporter.export(json_board, Path(output_path).with_suffix(".json"), format="json")
        ctx.stats["summary"] = json_board.summary

        ctx.stage = "done"
        return ctx
