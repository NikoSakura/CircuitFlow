"""CLI entry point for kcad-auto-pcb."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..config.settings import AppSettings
from ..pipeline.orchestrator import PipelineOrchestrator
from ..agent.agent import PCBAgent

app = typer.Typer(name="kcad-auto-pcb", help="AI-driven automatic PCB design")
console = Console()


@app.command()
def run(
    schematic: str = typer.Argument(..., help="Path to .kicad_sch file"),
    output: str = typer.Option("output.kicad_pcb", "-o", help="Output .kicad_pcb path"),
    layers: int = typer.Option(2, "-l", "--layers", help="Board layers (2 or 4)"),
    board_width: Optional[float] = typer.Option(None, "--width", help="Board width in mm"),
    board_height: Optional[float] = typer.Option(None, "--height", help="Board height in mm"),
    llm_placement: bool = typer.Option(True, "--llm-placement/--no-llm-placement", help="Enable LLM placement optimization"),
    llm_routing: bool = typer.Option(True, "--llm-routing/--no-llm-routing", help="Enable LLM routing strategy"),
    placement_llm: Optional[str] = typer.Option(None, "--placement-llm", help="LLM for placement (e.g., openai:gpt-4o-mini)"),
    routing_llm: Optional[str] = typer.Option(None, "--routing-llm", help="LLM for routing (e.g., anthropic:claude-haiku)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without executing"),
):
    """Generate a PCB from a KiCad schematic."""
    settings = AppSettings()

    if placement_llm:
        settings.placement_llm_spec = placement_llm
    if routing_llm:
        settings.routing_llm_spec = routing_llm

    if dry_run:
        console.print(f"[bold]Dry run[/] - would process {schematic}")
        console.print(f"  Output: {output}")
        console.print(f"  Layers: {layers}")
        console.print(f"  Placement LLM: {settings.placement_llm_spec}")
        console.print(f"  Routing LLM: {settings.routing_llm_spec}")
        console.print(f"  Token budget: {settings.token_budget_per_run}")
        return

    if not Path(schematic).exists():
        console.print(f"[red]Error:[/] Schematic not found: {schematic}")
        raise typer.Exit(1)

    if layers not in (2, 4):
        console.print(f"[red]Error:[/] Layers must be 2 or 4, got {layers}")
        raise typer.Exit(1)

    console.print(f"[bold]kcad-auto-pcb[/] v0.1.0")
    console.print(f"  Input: [cyan]{schematic}[/]")
    console.print(f"  Output: [cyan]{output}[/]")
    console.print(f"  Layers: {layers}")
    console.print(f"  Placement LLM: {settings.placement_llm_spec}")
    console.print(f"  Routing LLM: {settings.routing_llm_spec}")

    orchestrator = PipelineOrchestrator(settings)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        async def _run():
            task = progress.add_task("Processing...", total=None)
            ctx = await orchestrator.run(
                schematic_path=schematic,
                output_path=output,
                board_layers=layers,
                board_width=board_width,
                board_height=board_height,
                enable_llm_placement=llm_placement,
                enable_llm_routing=llm_routing,
            )
            progress.remove_task(task)
            return ctx

        ctx = asyncio.run(_run())

    # Results
    console.print()
    console.print("[bold green]Done![/]")

    summary = ctx.stats.get("summary", {})
    table = Table(title="PCB Design Summary")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    for k, v in summary.items():
        if isinstance(v, float):
            table.add_row(k, f"{v:.1f}")
        else:
            table.add_row(k, str(v))
    console.print(table)

    if ctx.warnings:
        console.print("\n[yellow]Warnings:[/]")
        for w in ctx.warnings:
            console.print(f"  - {w}")

    if ctx.errors:
        console.print("\n[red]Errors:[/]")
        for e in ctx.errors:
            console.print(f"  - {e}")

    console.print(f"\nOutput files:")
    console.print(f"  [green]→[/] {output}")
    console.print(f"  [green]→[/] {Path(output).with_suffix('.json')}")


@app.command()
def agent(
    schematic: str = typer.Argument(..., help="Path to .kicad_sch file"),
    output: str = typer.Option("output.kicad_pcb", "-o", help="Output .kicad_pcb path"),
    layers: int = typer.Option(2, "-l", "--layers", help="Board layers (2 or 4)"),
    board_width: Optional[float] = typer.Option(None, "--width", help="Board width in mm"),
    board_height: Optional[float] = typer.Option(None, "--height", help="Board height in mm"),
    llm_spec: Optional[str] = typer.Option(None, "--llm", help="LLM for agent (e.g., openai:gpt-4o-mini)"),
    max_iterations: int = typer.Option(20, "--max-iterations", help="Max agent iterations"),
):
    """Run the autonomous PCB design agent (LLM-driven iterative design)."""
    settings = AppSettings()
    if llm_spec:
        settings.placement_llm_spec = llm_spec

    if not Path(schematic).exists():
        console.print(f"[red]Error:[/] Schematic not found: {schematic}")
        raise typer.Exit(1)

    console.print(f"[bold]PCB Agent Mode[/]")
    console.print(f"  Input: [cyan]{schematic}[/]")
    console.print(f"  LLM: {settings.placement_llm_spec}")
    console.print(f"  Max iterations: {max_iterations}")
    console.print(f"  Token budget: {settings.token_budget_per_run}")
    console.print()

    agent = PCBAgent(settings)
    agent.MAX_ITERATIONS = max_iterations

    async def _run_agent():
        return await agent.design(
            schematic_path=schematic,
            output_path=output,
            board_layers=layers,
            board_width=board_width,
            board_height=board_height,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Agent designing PCB...", total=None)
        result = asyncio.run(_run_agent())
        progress.remove_task(task)

    console.print(f"\n[bold green]Agent finished![/]")
    console.print(f"  Status: {result['status']}")
    console.print(f"  Iterations: {result['iterations']}")
    console.print(f"  Actions: {result['actions']}")
    console.print(f"  Output: [cyan]{result['output']}[/]")

    if result.get("history"):
        table = Table(title="Agent Action History")
        table.add_column("#", style="dim")
        table.add_column("Action", style="cyan")
        for i, h in enumerate(result["history"], 1):
            table.add_row(str(i), str(h.get("action", "?")))
        console.print(table)


@app.command()
def read(
    schematic: str = typer.Argument(..., help="Path to schematic PDF/PNG/JPG"),
    output: str = typer.Option("extracted.json", "-o", help="Output JSON path"),
    llm_spec: Optional[str] = typer.Option(None, "--llm", help="Multimodal LLM (e.g., anthropic:claude-sonnet-4-20250514)"),
):
    """Extract circuit from PDF/image schematic via multimodal LLM."""
    from ..llm.base import LLMBackendFactory
    from ..schematic.multimodal_reader import MultimodalSchematicReader

    settings = AppSettings()
    if llm_spec:
        settings.placement_llm_spec = llm_spec

    if not Path(schematic).exists():
        console.print(f"[red]Error:[/] File not found: {schematic}")
        raise typer.Exit(1)

    spec = llm_spec or "anthropic:claude-sonnet-4-20250514"
    console.print(f"[bold]Reading schematic via multimodal LLM[/]")
    console.print(f"  Input: [cyan]{schematic}[/]")
    console.print(f"  LLM: {spec}")

    try:
        provider = spec.split(":")[0]
        kwargs = {"api_key": settings.anthropic_api_key} if provider == "anthropic" else \
                 {"api_key": settings.openai_api_key} if provider == "openai" else \
                 {"api_key": settings.deepseek_api_key or settings.openai_api_key} if provider == "deepseek" else \
                 {"base_url": settings.ollama_base_url}
        backend = LLMBackendFactory.create(spec, **{k: v for k, v in kwargs.items() if v})
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1)

    reader = MultimodalSchematicReader(backend)

    async def _read():
        return await reader.read(schematic)

    with Progress(SpinnerColumn(), TextColumn("Analyzing schematic..."), console=console) as progress:
        task = progress.add_task("", total=None)
        design = asyncio.run(_read())
        progress.remove_task(task)

    # Save as JSON
    import json
    data = {
        "components": {ref: {"value": c.value, "footprint": c.footprint_name,
                              "lib_id": c.lib_id}
                       for ref, c in design.components.items()},
        "nets": {name: {"code": net.code, "pins": [(p.component_ref, p.pin_number) for p in net.pins],
                         "is_power": net.is_power}
                 for name, net in design.nets.items()},
        "summary": design.summary(),
    }
    Path(output).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    console.print(f"\n[bold green]Extracted![/]")
    console.print(f"  Components: {design.component_count}")
    console.print(f"  Nets: {design.net_count}")
    console.print(f"  Power nets: {sum(1 for n in design.nets.values() if n.is_power)}")
    console.print(f"  Output: [cyan]{output}[/]")


@app.command()
def web(
    port: int = typer.Option(7860, "-p", "--port", help="Server port"),
):
    """Launch the web UI (FastAPI)."""
    from ..web.server import main as web_main
    import uvicorn, os, sys
    console.print(f"[bold]Starting kcad-auto-pcb Web UI[/]")
    console.print(f"  URL: [cyan]http://127.0.0.1:{port}[/]")
    # Override port via env
    os.environ["KCAD_WEB_PORT"] = str(port)
    from ..web.server import app as fastapi_app
    uvicorn.run(fastapi_app, host="127.0.0.1", port=port)


@app.command()
def version():
    """Show version info."""
    from .. import __version__
    console.print(f"kcad-auto-pcb v{__version__}")


def main():
    app()


if __name__ == "__main__":
    main()
