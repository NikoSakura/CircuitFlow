"""Bridge: run PCB pipeline via KiCad's Python when pcbnew is not in current env.

Usage:
    python kicad_bridge.py <schematic_path> <output_path> [--layers N] [--no-llm]

When pcbnew IS available in the current Python, pipeline runs directly.
When it's NOT, this module shells out to KiCad's bundled Python automatically.
"""

import subprocess, sys, os, json
from pathlib import Path


def find_kicad_python() -> str | None:
    """Locate KiCad's bundled Python interpreter."""
    candidates = [
        "C:/Program Files/KiCad/10.0/bin/python.exe",
        "C:/Program Files/KiCad/9.0/bin/python.exe",
        "C:/Program Files/KiCad/8.0/bin/python.exe",
        "C:/Program Files/KiCad/bin/python.exe",
    ]
    import glob
    for pattern in ["C:/Program Files/KiCad/*/bin/python.exe"]:
        for path in sorted(glob.glob(pattern), reverse=True):
            candidates.append(path)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def run_pipeline(schematic_path: str, output_path: str,
                 layers: int = 2, enable_llm: bool = False) -> dict:
    """Run the full PCB pipeline, delegating to KiCad Python if needed.

    Returns dict with keys: success, output, summary, errors, router
    """
    try:
        import pcbnew
        kicad_python = sys.executable  # already running in KiCad Python
    except ImportError:
        kicad_python = find_kicad_python()
        if not kicad_python:
            return {"success": False, "error": "KiCad Python not found. Install KiCad."}

    # Build the runner script
    script = f'''
import asyncio, sys, json
sys.path.insert(0, r"{Path(__file__).parent.parent.parent}")

from kcad_auto_pcb.config.settings import AppSettings
from kcad_auto_pcb.pipeline.orchestrator import PipelineOrchestrator
from pathlib import Path

async def main():
    settings = AppSettings(
        placement_llm_spec="" if not {str(enable_llm).lower()} else "openai:gpt-4o-mini",
        routing_llm_spec="" if not {str(enable_llm).lower()} else "openai:gpt-4o-mini",
    )
    orch = PipelineOrchestrator(settings)
    ctx = await orch.run(
        schematic_path=r"{schematic_path}",
        output_path=r"{output_path}",
        board_layers={layers},
        enable_llm_placement={str(enable_llm).lower()},
        enable_llm_routing={str(enable_llm).lower()},
    )
    result = {{
        "success": ctx.stage == "done",
        "stage": ctx.stage,
        "summary": ctx.stats.get("summary", {{}}),
        "errors": ctx.errors,
        "warnings": ctx.warnings,
        "router": ctx.stats.get("router", "unknown"),
        "placement_score": ctx.stats.get("placement_score", 0),
    }}
    print("BRIDGE_RESULT:" + json.dumps(result, ensure_ascii=False))

asyncio.run(main())
'''

    result = subprocess.run(
        [kicad_python, "-c", script],
        capture_output=True, text=True, timeout=300,
        cwd=str(Path(__file__).parent.parent.parent),
    )

    # Parse result from stdout
    for line in result.stdout.split("\n"):
        if line.startswith("BRIDGE_RESULT:"):
            return json.loads(line[14:])

    # If no result marker, check for errors
    stderr = result.stderr[-500:] if result.stderr else ""
    return {
        "success": False,
        "error": f"Pipeline failed (rc={result.returncode})",
        "stderr": stderr,
        "stdout": result.stdout[-500:],
    }
