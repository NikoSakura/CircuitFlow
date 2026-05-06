from __future__ import annotations
import asyncio, json
from pathlib import Path
from typing import List, Optional, Dict, Any
from ..geometry.rect import Rect
from ..config.settings import AppSettings
from ..schematic.parser import SchematicParser
from ..schematic.model import Design
from ..footprint.cache import FootprintCache
from ..pcb.stackup import BoardStackup
from ..llm.base import AbstractLLMBackend, LLMBackendFactory, LLMMessage, LLMResponse
from ..llm.token_counter import TokenBudget
from .tools import AgentTools, ToolResult


# Agent system prompt: tells the LLM how to design a PCB step by step
SYSTEM_PROMPT = """You are an expert PCB design agent. You have tools to design a PCB autonomously.

Design process:
1. Call place_components to create the initial layout
2. Call get_placement_stats to review quality
3. Call adjust_placement or swap_components to fix issues
4. Call assign_layers to set routing layers for each net
5. Call route_nets to auto-route all connections
6. Call get_routing_stats to check routing quality
7. Call run_drc to verify design rules
8. Fix any issues found, then call export_pcb to save
9. Call finalize when done

Key design rules:
- Decoupling capacitors must be within 5mm of IC power pins
- Connectors should be at board edges
- Power nets on inner layers for 4-layer boards
- Minimize total wirelength
- High pin-count components should be central

Work step by step. Call ONE tool at a time, then analyze the result before proceeding."""


class PCBAgent:
    """Autonomous PCB design agent.

    The agent receives a schematic and uses LLM-driven tool calling
    to iteratively design, route, and verify a PCB.

    Supports any LLM backend (OpenAI, Anthropic, DeepSeek, Ollama).

    Usage:
        agent = PCBAgent(settings)
        result = await agent.design("input.kicad_sch", "output.kicad_pcb")
    """

    MAX_ITERATIONS = 20  # Safety limit

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.token_budget = TokenBudget(settings.token_budget_per_run)
        self.backend: Optional[AbstractLLMBackend] = None

        # Initialize the primary LLM
        spec = settings.placement_llm_spec or settings.routing_llm_spec or "openai:gpt-4o-mini"
        self.backend = self._init_backend(spec)

    def _init_backend(self, spec: str) -> Optional[AbstractLLMBackend]:
        try:
            provider = spec.split(":")[0]
            kwargs = {}
            if provider in ("openai", "openai_compatible"):
                kwargs["api_key"] = self.settings.openai_api_key
            elif provider == "anthropic":
                kwargs["api_key"] = self.settings.anthropic_api_key
            elif provider == "deepseek":
                kwargs["api_key"] = self.settings.deepseek_api_key or self.settings.openai_api_key
            elif provider == "ollama":
                kwargs["base_url"] = self.settings.ollama_base_url
            return LLMBackendFactory.create(spec, **kwargs)
        except Exception:
            return None

    async def design(
        self,
        schematic_path: str,
        output_path: str,
        board_layers: int = 2,
        board_width: float | None = None,
        board_height: float | None = None,
        pre_parsed_design=None,  # Skip parsing if Design already extracted
    ) -> dict:
        """Run the autonomous PCB design agent."""
        if pre_parsed_design is not None:
            design = pre_parsed_design
        else:
            suffix = Path(schematic_path).suffix.lower()
            if suffix == ".pdf":
                try:
                    from ..schematic.pdf_parser import PDFSchematicParser
                    design = PDFSchematicParser().parse(schematic_path)
                except Exception:
                    parser = SchematicParser()
                    design = parser.parse(schematic_path)
            else:
                parser = SchematicParser()
                design = parser.parse(schematic_path)

        # Setup
        bw = board_width or min(max(50, design.component_count * 12), 300)
        bh = board_height or min(max(40, design.component_count * 10), 240)
        board_bounds = Rect(0, 0, bw, bh)
        stackup = BoardStackup(board_layers)

        # Footprint cache
        fp_cache = FootprintCache()
        footprint_map = {}
        for ref, comp in design.components.items():
            if comp.footprint_name:
                fp_cache.get(comp.footprint_name)
                footprint_map[ref] = comp.footprint_name

        net_map = {name: net.code for name, net in design.nets.items()}

        # Initialize tools
        tools = AgentTools(design, stackup, fp_cache, board_bounds)
        tools.footprint_map = footprint_map
        tools.net_map = net_map

        # Check if LLM is actually usable (has API key or is ollama)
        can_use_llm = False
        if self.backend:
            provider = self.backend.provider_name
            if provider == "ollama":
                can_use_llm = True  # Ollama doesn't need a key
            elif provider in ("openai", "openai_compatible") and self.settings.openai_api_key:
                can_use_llm = True
            elif provider == "anthropic" and self.settings.anthropic_api_key:
                can_use_llm = True
            elif provider == "deepseek" and (self.settings.deepseek_api_key or self.settings.openai_api_key):
                can_use_llm = True

        if not can_use_llm:
            return self._fallback_design(tools, output_path, design)

        # Agent loop
        messages = [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(role="user", content=self._build_context_message(design, board_bounds, stackup)),
        ]

        tool_schemas = tools.get_tool_schemas()

        for iteration in range(self.MAX_ITERATIONS):
            if self.token_budget.exhausted:
                break

            estimated_tokens = 500
            if not self.token_budget.can_call(estimated_tokens):
                break

            try:
                response = await asyncio.wait_for(
                    self._call_llm(messages, tool_schemas), timeout=30
                )
                self.token_budget.consume(response.tokens_used)
            except (asyncio.TimeoutError, Exception):
                break

            # Parse tool call from response
            tool_call = self._parse_tool_call(response.text)
            if tool_call is None:
                # No tool call - LLM is just talking, ask it to proceed
                messages.append(LLMMessage(
                    role="user",
                    content="Please call a tool to proceed with the PCB design."
                ))
                continue

            # Execute tool
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args", {})
            result = self._execute_tool(tools, tool_name, tool_args, output_path)

            # Check for finalize
            if tool_name == "finalize":
                return {
                    "status": "completed",
                    "iterations": iteration + 1,
                    "actions": len(tools.history),
                    "output": output_path,
                    "history": tools.history,
                    "message": result.message,
                }

            # Feed result back to LLM
            messages.append(LLMMessage(
                role="assistant",
                content=f"Called {tool_name}({json.dumps(tool_args)}): {result.message}"
            ))
            messages.append(LLMMessage(
                role="user",
                content=f"Result: {json.dumps(result.data) if result.data else result.message}\n"
                        f"What's the next step?"
            ))

        # Reached max iterations - finalize anyway
        tools.export_pcb(output_path)
        return {
            "status": "max_iterations" if not self.token_budget.exhausted else "budget_exhausted",
            "iterations": self.MAX_ITERATIONS,
            "actions": len(tools.history),
            "output": output_path,
            "history": tools.history,
        }

    def _build_context_message(self, design: Design, bounds: Rect, stackup: BoardStackup) -> str:
        comps = []
        for ref, c in design.components.items():
            comps.append(f"  {ref}: {c.value} ({c.footprint_name or 'no footprint'})")

        nets = []
        for name, net in design.nets.items():
            pins = [f"{p.component_ref}.{p.pin_number}" for p in net.pins[:10]]
            nets.append(f"  {name} ({'POWER' if net.is_power else 'signal'}): {', '.join(pins)}")

        return f"""Design a PCB for this schematic:

Components ({design.component_count}):
{chr(10).join(comps[:40])}

Nets ({design.net_count}):
{chr(10).join(nets[:30])}

Board: {bounds.w:.0f}x{bounds.h:.0f}mm
Layers: {stackup.num_layers} ({", ".join(l['name'] for l in stackup.layers)})

Begin by calling place_components."""

    async def _call_llm(
        self, messages: List[LLMMessage], tools: List[dict]
    ) -> LLMResponse:
        """Call LLM with tool definitions."""
        if self.backend.provider_name == "openai" or self.backend.provider_name == "deepseek":
            return await self._call_openai_style(messages, tools)
        else:
            return await self._call_text_style(messages, tools)

    async def _call_openai_style(
        self, messages: List[LLMMessage], tools: List[dict]
    ) -> LLMResponse:
        """Use OpenAI native function calling format."""
        import openai

        api_messages = []
        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        tool_map = {}
        for t in tools:
            tool_map[t["name"]] = t

        client = self.backend._client
        resp = await client.chat.completions.create(
            model=self.backend.model_name,
            messages=api_messages,
            tools=[{"type": "function", "function": t} for t in tools],
            tool_choice="auto",
            temperature=0.2,
            max_tokens=1024,
        )

        choice = resp.choices[0]
        if choice.message.tool_calls:
            tc = choice.message.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            return LLMResponse(
                text=json.dumps({
                    "tool": tc.function.name,
                    "args": args,
                }),
                tokens_used=resp.usage.total_tokens if resp.usage else 0,
                model=resp.model,
            )

        return LLMResponse(
            text=choice.message.content or "",
            tokens_used=resp.usage.total_tokens if resp.usage else 0,
            model=resp.model,
        )

    async def _call_text_style(
        self, messages: List[LLMMessage], tools: List[dict]
    ) -> LLMResponse:
        """Fallback: embed tool descriptions in system prompt for non-OpenAI backends."""
        tool_text = "Available tools:\n"
        for t in tools:
            tool_text += f"  {t['name']}: {t['description']}\n"
        tool_text += "\nCall a tool by responding with:\n"
        tool_text += '{"tool": "tool_name", "args": {...}}\n'
        tool_text += "Respond ONLY with the JSON tool call."

        # Add tool info to last message
        modified = list(messages)
        modified.append(LLMMessage(role="user", content=tool_text))

        response = await self.backend.chat(
            messages=modified,
            temperature=0.2,
            max_tokens=500,
        )
        return response

    def _parse_tool_call(self, text: str) -> Optional[dict]:
        """Parse tool call from LLM response text.

        Handles common issues: markdown fences, LLM error messages, non-JSON text.
        """
        import re

        if not text:
            return None

        # Bail early on obvious error responses
        if text.strip().startswith(("Internal Server Error", "Error:", "<html", "<!DOCTYPE")):
            return None

        # Try JSON format first
        for pattern in [r'\{[\s\S]*"tool"[\s\S]*\}', r'\{[\s\S]*"name"[\s\S]*\}']:
            match = re.search(pattern, text)
            if match:
                try:
                    data = json.loads(match.group())
                    if "tool" in data:
                        return {"name": data["tool"], "args": data.get("args", {})}
                    if "name" in data:
                        return {"name": data["name"], "args": data.get("arguments", data.get("args", {}))}
                except (json.JSONDecodeError, TypeError):
                    continue

        # Try to find function call pattern: func_name({...})
        match = re.search(r'(\w+)\((\{.*?\})\)', text, re.DOTALL)
        if match:
            tool_name = match.group(1)
            try:
                args = json.loads(match.group(2))
                return {"name": tool_name, "args": args}
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    def _execute_tool(self, tools: AgentTools, name: str, args: dict,
                      output_path: str) -> ToolResult:
        """Execute a tool by name."""
        tool_map = {
            "place_components": lambda: tools.place_components(**args),
            "get_placement_stats": tools.get_placement_stats,
            "adjust_placement": lambda: tools.adjust_placement(**args),
            "swap_components": lambda: tools.swap_components(**args),
            "assign_layers": lambda: tools.assign_layers(**args),
            "route_nets": lambda: tools.route_nets(**args),
            "get_routing_stats": tools.get_routing_stats,
            "run_drc": tools.run_drc,
            "export_pcb": lambda: tools.export_pcb(args.get("output_path", output_path)),
            "finalize": tools.finalize,
        }

        func = tool_map.get(name)
        if func:
            try:
                return func()
            except Exception as e:
                return ToolResult(success=False, message=f"Tool error: {e}")
        return ToolResult(success=False, message=f"Unknown tool: {name}")

    def _fallback_design(self, tools: AgentTools, output_path: str, design: Design) -> dict:
        """Algorithmic-only fallback when no LLM is available."""
        tools.place_components()
        tools.route_nets()
        tools.export_pcb(output_path)
        return {
            "status": "completed (algorithmic fallback)",
            "iterations": 0,
            "actions": len(tools.history),
            "output": output_path,
            "history": tools.history,
        }
