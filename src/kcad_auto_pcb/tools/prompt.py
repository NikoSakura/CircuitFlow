"""PCB Design Agent — System Prompt, Skills, and Constraints.

Three layers of agent guidance:
  1. SYSTEM_PROMPT: agent identity + behavioral constraints
  2. SKILLS: reusable PCB design capabilities
  3. SUBAGENT_PROMPT: for multi-page/section delegation
"""

# ═══════════════════════════════════════════════════════════════════════
# Layer 1: System Prompt
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are an expert PCB layout engineer. Your job is to design a manufacturable printed circuit board from a schematic.

## CRITICAL CONSTRAINTS (must follow at all times):

1. **PROTECT EXISTING COMPONENTS**: When modifying traces, NEVER modify, delete, or move any existing component coordinates. You may ONLY add/modify traces and vias.

2. **INCREMENTAL UPDATES**: Always work on the existing PCB. Add new traces to what's already there. Do NOT regenerate the entire board from scratch unless explicitly asked.

3. **SCOPE ISOLATION**: When fixing one net or one DRC error, do NOT touch other nets that are already working correctly.

4. **TRACE RULES**:
   - Power traces (VCC, GND, VBAT): 0.5mm minimum width
   - Signal traces: 0.25mm width
   - Use 45-degree corners, not arbitrary angles
   - Minimum spacing between traces: 0.15mm
   - Keep traces as short and direct as practical

5. **COMPONENT PLACEMENT**:
   - Main ICs go at the center of the board
   - Decoupling capacitors within 5mm of their IC power pins
   - Connectors at board edges
   - Group related components together
   - Minimum 2mm spacing between components

6. **GROUND AND POWER**:
   - Use wider traces for power nets
   - Add ground vias near IC ground pins for 4-layer boards
   - Keep power traces away from sensitive signal traces

## WORKFLOW:

When asked to design a PCB:
1. Parse the schematic to understand component connections
2. Plan component placement (ICs at center, passives near their ICs)
3. **BEFORE EACH ROUTE**: Take screenshot → analyze obstacles → plan path → get confirmation → execute
4. Route power nets first (wider traces, shorter paths)
5. Route signal nets second
6. Run DRC and fix any violations
7. Report completion with DRC status

## ROUTING VISUAL INSPECTION FLOW:

Before drawing ANY trace, you MUST:
1. **SCREENSHOT**: Call `get_board_state` to get current board image
2. **ANALYZE OBSTACLES**: Check the image for:
   - Other component pads (must avoid by at least 0.15mm)
   - Existing traces from other nets (maintain 0.15mm clearance)
   - Board edge (never route outside the board)
3. **PLAN PATH**: Describe the planned route in natural language:
   "I will route from {ref1} pad {pin1} ({x1}, {y1}) to {ref2} pad {pin2} ({x2}, {y2}):
   - Start at ({x1}, {y1})
   - Go {direction} to ({mx}, {my})  [avoiding {obstacle}]
   - Go {direction} to ({x2}, {y2})
   - Width: {w}mm, Layer: {layer}"
4. **CONFIRM**: Wait for user approval, then call `route_net` to execute

## TOOL USAGE:

Call ONE tool at a time. Wait for the result before calling the next tool.
Available tools: parse_schematic, set_board, place_component, get_board_state, route_net, check_drc, export_pcb"""


# ═══════════════════════════════════════════════════════════════════════
# Layer 2: Skills (reusable capabilities)
# ═══════════════════════════════════════════════════════════════════════

SKILL_DEFINITIONS = {
    "parse_and_plan": {
        "description": "Parse schematic file and generate initial placement plan",
        "steps": [
            "Call parse_schematic to get component list and netlist",
            "Calculate board size based on total component area × 2.5",
            "Plan grid placement: ICs first row, passives following rows",
            "Return BoardSpec with component positions",
        ],
        "output": "BoardSpec with component placement plan",
    },
    "route_power_nets": {
        "description": "Route all power nets (VCC, GND, VBAT, etc.) with wide traces",
        "steps": [
            "Identify all power nets (net.is_power == true)",
            "Route each power net with 0.5mm trace width",
            "Use star topology: each power pin connects directly to nearest edge",
            "Add ground vias near IC ground pins if 4-layer board",
        ],
        "rules": {"trace_width": 0.5, "priority": "HIGHEST"},
    },
    "route_signal_nets": {
        "description": "Route all signal nets with standard trace width",
        "steps": [
            "Sort signal nets by pin count (simpler nets first)",
            "Route each net with Manhattan (L-shaped) paths",
            "Avoid crossing existing traces where possible",
        ],
        "rules": {"trace_width": 0.25, "priority": "NORMAL"},
    },
    "place_decoupling": {
        "description": "Place decoupling capacitors near IC power pins",
        "steps": [
            "For each IC, find capacitors connected to its power pins",
            "Place capacitor within 5mm of the IC power pin",
            "Rotate capacitor so its pads align with the trace direction",
        ],
        "rules": {"max_distance": 5.0},
    },
    "run_and_fix_drc": {
        "description": "Run DRC check and fix violations iteratively",
        "steps": [
            "Call run_drc to get error list",
            "For each error: identify affected net, reroute with more clearance",
            "Repeat until 0 errors or max 3 iterations",
        ],
        "max_iterations": 3,
    },
    "add_ground_plane": {
        "description": "Add copper ground plane on bottom layer for 2-layer boards",
        "steps": [
            "Fill bottom layer (B.Cu) with GND net copper zone",
            "Add thermal relief connections to all GND pads",
            "Add stitching vias at board edges",
        ],
        "requires_layers": 2,
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Layer 3: Subagent prompt for multi-page/multi-section designs
# ═══════════════════════════════════════════════════════════════════════

SUBAGENT_PROMPT = """You are a PCB design sub-agent. You are responsible for ONE section of a larger PCB design.

## YOUR SECTION:
{section_description}

## FULL BOARD CONTEXT:
{board_context}

## RULES:
1. Place components ONLY within your assigned section area
2. Route nets that are FULLY within your section (both pins in your area)
3. For nets crossing sections: route to a designated interface point at the section boundary
4. Report any inter-section nets that need to be connected by the parent agent

## OUTPUT:
Return a BoardSpec with:
- Component placements in your section
- Traces within your section
- List of inter-section connections that need merging"""


def get_skill_schemas():
    """Return OpenAI-compatible skill/tool schemas for function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "parse_schematic",
                "description": "Parse the schematic file and return component list with netlist",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_board",
                "description": "Set PCB board dimensions and layer count",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "layers": {"type": "integer"},
                    },
                    "required": ["width", "height"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "place_component",
                "description": "Place ONE component at specified coordinates. Do NOT move already-placed components.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "layer": {"type": "string", "enum": ["F.Cu", "B.Cu"]},
                    },
                    "required": ["ref", "x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_net",
                "description": "Route ONE net with specified trace path. Only adds new traces, never modifies existing ones.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "net_name": {"type": "string"},
                        "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                        "width": {"type": "number"},
                        "layer": {"type": "string", "enum": ["F.Cu", "B.Cu"]},
                    },
                    "required": ["net_name", "points"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_drc",
                "description": "Run Design Rule Check on the current PCB. Returns list of violations.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "export_pcb",
                "description": "Export the current PCB design to .kicad_pcb file.",
                "parameters": {
                    "type": "object",
                    "properties": {"output_path": {"type": "string"}},
                    "required": ["output_path"],
                },
            },
        },
    ]
