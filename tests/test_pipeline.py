import pytest
from pathlib import Path
from kcad_auto_pcb.config.settings import AppSettings


@pytest.mark.asyncio
async def test_pipeline_no_llm(simple_schematic_path, tmp_path):
    """Test the full algorithmic pipeline without LLM."""
    from kcad_auto_pcb.pipeline.orchestrator import PipelineOrchestrator

    settings = AppSettings(
        placement_llm_spec="",  # No LLM
        routing_llm_spec="",
    )
    output = tmp_path / "output.kicad_pcb"

    orchestrator = PipelineOrchestrator(settings)
    ctx = await orchestrator.run(
        schematic_path=str(simple_schematic_path),
        output_path=str(output),
        board_layers=2,
        enable_llm_placement=False,
        enable_llm_routing=False,
    )

    assert ctx.stage == "done"
    assert ctx.design is not None
    assert ctx.design.component_count > 0
    assert ctx.placement is not None
    assert ctx.board is not None

    # Verify output files exist
    assert output.exists() or Path(str(output).replace(".kicad_pcb", ".json")).exists()


@pytest.mark.asyncio
async def test_agent_fallback(simple_schematic_path, tmp_path):
    """Test the agent in fallback (no LLM) mode."""
    from kcad_auto_pcb.agent.agent import PCBAgent

    settings = AppSettings(
        placement_llm_spec="",  # Will use fallback
    )
    output = tmp_path / "agent_output.kicad_pcb"

    agent = PCBAgent(settings)
    result = await agent.design(
        schematic_path=str(simple_schematic_path),
        output_path=str(output),
        board_layers=2,
    )

    assert result["status"] is not None
    assert output.exists() or Path(str(output).replace(".kicad_pcb", ".json")).exists()
