"""Multimodal schematic reader: extract circuit from PDF/images via LLM vision.

Supports:
- PDF files (rendered as images)
- PNG/JPG schematic images
- Hand-drawn circuit diagrams

Uses multimodal LLM (Anthropic Claude, GPT-4o, etc.) to:
1. Identify all components and their values
2. Read component reference designators (R1, C2, U1...)
3. Trace connections between pins
4. Extract net labels (VCC, GND, signal names)
5. Infer footprints from component packages

Output: unified Design model, same as KiCad parser.
"""

from __future__ import annotations
import asyncio, base64, io, json
from pathlib import Path
from typing import List, Optional, Dict, Any
from .model import Component, Net, Pin, PinRef, Design
from ..geometry.point import Point
from ..llm.base import AbstractLLMBackend, LLMMessage


# STEP 1 prompt: identify components only
STEP1_PROMPT = """You are a PCB design assistant. Look at this schematic image and list EVERY component.

For each component, report:
- reference: the label next to it (R1, C2, U1, LED1, etc.)
- value: the value text (10k, 100nF, NE555, LED_RED, etc.)
- package: the physical size (DIP-8, SOIC-8, 0603, 0805, TO-92, SOT-23, THT, AXIAL, etc.)
  Look at the SYMBOL shape: rectangle with pins on sides=SOIC/DIP, small rectangle=0603/0805 SMD, circle with line=LED, triangle=transistor

Rules:
- Count EVERY symbol. If there are 3 resistors, list all 3 (R1, R2, R3).
- If a symbol has no value text visible, write "?" for value.
- Power/GND symbols (VCC, GND arrows) → list as component with reference=PWR1/PWR2, value=VCC/GND, package="virtual"
- For ICs, read the part number printed on the symbol (NE555, LM358, etc.)
- Package: if the symbol has pins on 2 sides → DIP or SOIC. If small SMD rectangle → 0603. If large THT → AXIAL or DIP.

Return ONLY a JSON object:
{
  "components": [
    {"reference": "U1", "value": "NE555", "package": "DIP-8"},
    {"reference": "R1", "value": "10k", "package": "0603"},
    {"reference": "LED1", "value": "LED_RED", "package": "0603"}
  ]
}"""

# STEP 2 prompt: trace connections given component list
STEP2_PROMPT = """You are a PCB design assistant. Given this schematic image and the known component list, trace ALL electrical connections.

Component list:
{component_list}

For each wire/net in the schematic:
- Identify which component pins are connected together
- Note net labels if visible (VCC, GND, OUT, etc.)
- Mark power nets (VCC, GND, VDD, VSS) as is_power=true

For ICs with known pinouts, use PHYSICAL PIN NUMBERS (1, 2, 3...), not function names.
Example: NE555 pin 1=GND, pin 4=RESET, pin 8=VCC

Return ONLY a JSON object:
{
  "nets": [
    {
      "name": "VCC",
      "is_power": true,
      "connections": [
        {"reference": "U1", "pin": "8"},
        {"reference": "R1", "pin": "1"}
      ]
    }
  ],
  "board_notes": {"estimated_size_mm": "50x40", "recommended_layers": 2}
}

Rules:
- USE PHYSICAL PIN NUMBERS for ICs (1-8 for DIP-8), not function names
- Trace every visible wire - no shortcuts
- If unsure about a connection, omit it rather than guessing"""


class MultimodalSchematicReader:
    """Read schematics from PDF/images using multimodal LLM vision.

    Usage:
        backend = LLMBackendFactory.create("anthropic:claude-sonnet-4-20250514", api_key="...")
        reader = MultimodalSchematicReader(backend)
        design = await reader.read("schematic.pdf")
    """

    # Component knowledge: maps common part numbers to their info
    COMPONENT_KNOWLEDGE: Dict[str, dict] = {
        "NE555": {
            "function": "timer IC",
            "pins": {
                "1": "GND",
                "2": "TRIGGER",
                "3": "OUTPUT",
                "4": "RESET",
                "5": "CONTROL",
                "6": "THRESHOLD",
                "7": "DISCHARGE",
                "8": "VCC"
            },
            "common_footprints": ["Package_DIP:DIP-8_W7.62mm", "Package_SO:SOIC-8_3.9x4.9mm"],
            "decoupling": {"VCC": "100nF", "CONTROL": "10nF"}
        },
        "LM358": {
            "function": "dual op-amp",
            "pins": {"1": "OUT A", "2": "IN- A", "3": "IN+ A", "4": "GND",
                     "5": "IN+ B", "6": "IN- B", "7": "OUT B", "8": "VCC"},
            "common_footprints": ["Package_DIP:DIP-8_W7.62mm", "Package_SO:SOIC-8_3.9x4.9mm"],
            "decoupling": {"VCC": "100nF"}
        },
        "ATmega328P": {
            "function": "8-bit MCU",
            "pins": {"7": "VCC", "8": "GND", "22": "GND", "21": "AREF", "20": "AVCC"},
            "common_footprints": ["Package_DIP:DIP-28_W7.62mm", "Package_QFP:TQFP-32_7x7mm"],
            "decoupling": {"VCC": "100nF", "AVCC": "100nF + 10uF"}
        },
        "ESP32": {
            "function": "WiFi/BT MCU",
            "common_footprints": ["Package_QFN:QFN-48_6x6mm"],
            "decoupling": {"VDD3P3": "100nF + 10uF"}
        },
        "AMS1117-3.3": {
            "function": "3.3V LDO regulator",
            "pins": {"1": "GND", "2": "VOUT", "3": "VIN"},
            "common_footprints": ["Package_TO:SOT-223"],
            "decoupling": {"VIN": "10uF", "VOUT": "10uF + 100nF"}
        },
        "BC547": {
            "function": "NPN transistor",
            "pins": {"1": "Collector", "2": "Base", "3": "Emitter"},
            "common_footprints": ["Package_TO:TO-92"]
        },
        "2N2222": {
            "function": "NPN transistor",
            "pins": {"1": "Emitter", "2": "Base", "3": "Collector"},
            "common_footprints": ["Package_TO:TO-92"]
        },
        "1N4148": {
            "function": "signal diode",
            "pins": {"1": "Cathode", "2": "Anode"},
            "common_footprints": ["Diode_SMD:D_SOD-123"]
        },
        "1N4007": {
            "function": "rectifier diode",
            "pins": {"1": "Cathode", "2": "Anode"},
            "common_footprints": ["Diode_THT:D_DO-41"]
        },
    }

    # Package type → footprint mapping
    PACKAGE_TO_FOOTPRINT: Dict[str, str] = {
        "0201": "Resistor_SMD:R_0201_0603Metric",
        "0402": "Resistor_SMD:R_0402_1005Metric",
        "0603": "Resistor_SMD:R_0603_1608Metric",
        "0805": "Resistor_SMD:R_0805_2012Metric",
        "1206": "Resistor_SMD:R_1206_3216Metric",
        "dip-8": "Package_DIP:DIP-8_W7.62mm",
        "dip-14": "Package_DIP:DIP-14_W7.62mm",
        "dip-16": "Package_DIP:DIP-16_W7.62mm",
        "dip-28": "Package_DIP:DIP-28_W7.62mm",
        "soic-8": "Package_SO:SOIC-8_3.9x4.9mm",
        "sot-23": "Package_TO_SOT:SOT-23",
        "sot-223": "Package_TO_SOT:SOT-223",
        "to-92": "Package_TO:TO-92",
        "qfp-32": "Package_QFP:TQFP-32_7x7mm",
        "qfn-48": "Package_QFN:QFN-48_6x6mm",
    }

    def __init__(self, backend: AbstractLLMBackend):
        if not backend.supports_images:
            raise ValueError(f"Backend {backend.provider_name} does not support images. "
                           "Use a multimodal model like claude-sonnet or gpt-4o.")
        self.backend = backend

    async def read(self, path: str | Path) -> Design:
        """Read a schematic from PDF/image and return a Design object."""
        path = Path(path)
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            images = self._render_pdf(path)
        elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
            images = [path.read_bytes()]
        else:
            raise ValueError(f"Unsupported format: {suffix}. Use PDF, PNG, or JPG.")

        if not images:
            raise ValueError("No images extracted from file.")

        # Send to multimodal LLM for analysis
        result = await self._analyze_images(images)

        # Convert to Design model
        return self._build_design(result)

    async def _analyze_images(self, images: List[bytes]) -> dict:
        """Two-pass extraction: components first, then connections."""
        # Step 1: identify components (60s timeout)
        try:
            response = await asyncio.wait_for(self.backend.chat(
                messages=[LLMMessage(role="user", content=STEP1_PROMPT, images=images)],
                temperature=0.1, max_tokens=2048,
            ), timeout=60)
        except asyncio.TimeoutError:
            raise Exception("LLM 调用超时 (60s) — 请检查 API 密钥和网络连接")
        step1 = self._parse_llm_json(response.text)
        components = step1.get("components", [])

        # Step 2: trace connections
        comp_list = "\n".join(
            f"  {c.get('reference','?')}: {c.get('value','?')} ({c.get('package','?')})"
            for c in components
        )
        step2_prompt = STEP2_PROMPT.replace("{component_list}", comp_list)

        try:
            response = await _aio.wait_for(self.backend.chat(
                messages=[LLMMessage(role="user", content=step2_prompt, images=images)],
                temperature=0.1, max_tokens=2048,
            ), timeout=60)
        except _aio.TimeoutError:
            raise Exception("LLM 调用超时 (60s) — 请检查 API 密钥和网络连接")
        step2 = self._parse_llm_json(response.text)

        # Merge results
        result = {"components": components, "nets": step2.get("nets", []),
                  "board_notes": step2.get("board_notes", {})}

        # Apply topology validation
        result = self._validate_topology(result)

        return result

    def _validate_topology(self, raw: dict) -> dict:
        """Post-process LLM output: fix pin numbers, remove hallucinations, standardize footprints."""
        components = raw.get("components", [])
        nets = raw.get("nets", [])

        # Build component lookup
        comp_map = {c["reference"]: c for c in components}

        # Build pin reverse map from knowledge base
        pin_reverse = {}
        for ref, comp in comp_map.items():
            kb = self.COMPONENT_KNOWLEDGE.get(comp.get("value", "").upper(), {})
            if kb and "pins" in kb:
                pin_reverse[ref] = {v.upper(): k for k, v in kb["pins"].items()}

        # Fix net connections: map function names to physical pin numbers
        for net in nets:
            for conn in net.get("connections", []):
                ref = conn.get("reference", "")
                pin = str(conn.get("pin", "1"))
                if ref in pin_reverse and not pin.isdigit():
                    mapped = pin_reverse[ref].get(pin.upper())
                    if mapped:
                        conn["pin"] = mapped

        # Remove power symbols (PWR*) from components — they're virtual
        components = [c for c in components if not c.get("reference","").startswith("PWR")]
        # Also remove power symbol connections from nets that reference removed components
        pwr_refs = {c["reference"] for c in raw.get("components", []) if c.get("reference","").startswith("PWR")}
        for net in nets:
            net["connections"] = [c for c in net.get("connections", [])
                                  if c.get("reference") not in pwr_refs]

        # Standardize IC footprints based on knowledge base
        for comp in components:
            val = comp.get("value", "").upper()
            kb = self.COMPONENT_KNOWLEDGE.get(val, {})
            if kb and "common_footprints" in kb:
                fp_list = kb["common_footprints"]
                # Use first DIP footprint if available (most readable), else first
                dip_fps = [f for f in fp_list if "DIP" in f.upper()]
                comp["footprint"] = dip_fps[0] if dip_fps else fp_list[0]

            # Ensure footprint is set
            if not comp.get("footprint"):
                pkg = comp.get("package", "").upper()
                if "DIP" in pkg:
                    # Extract pin count: DIP-8 → 8
                    import re
                    m = re.search(r'(\d+)', pkg)
                    pins = int(m.group(1)) if m else 8
                    comp["footprint"] = f"Package_DIP:DIP-{pins}_W7.62mm"
                    if pins >= 28:
                        comp["footprint"] = f"Package_DIP:DIP-{pins}_W15.24mm"
                elif "SOIC" in pkg:
                    comp["footprint"] = f"Package_SO:SOIC-8_3.9x4.9mm"
                elif "SOT-23" in pkg:
                    comp["footprint"] = "Package_TO_SOT:SOT-23"
                else:
                    # Default by reference prefix
                    ref = comp.get("reference", "")
                    prefix = ref.rstrip("0123456789").upper() if ref else ""
                    if "U" in prefix:
                        comp["footprint"] = "Package_DIP:DIP-8_W7.62mm"
                    elif "R" in prefix:
                        comp["footprint"] = "Resistor_SMD:R_0603_1608Metric"
                    elif "C" in prefix:
                        comp["footprint"] = "Capacitor_SMD:C_0603_1608Metric"
                    elif "D" in prefix or "LED" in prefix:
                        comp["footprint"] = "LED_SMD:LED_0603_1608Metric"
                    elif "Q" in prefix:
                        comp["footprint"] = "Package_TO:TO-92"

        return {"components": components, "nets": nets,
                "board_notes": raw.get("board_notes", {})}

    def _parse_llm_json(self, text: str) -> dict:
        """Extract JSON from LLM response."""
        import re
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try markdown code block
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {"components": [], "nets": [], "board_notes": {}}

    def _build_design(self, raw: dict) -> Design:
        """Convert LLM output to our Design model, enriching with knowledge base."""
        design = Design()

        for comp_data in raw.get("components", []):
            ref = comp_data.get("reference", "")
            value = comp_data.get("value", "")
            footprint = comp_data.get("footprint", "")

            # Enrich with knowledge base
            kb = self.COMPONENT_KNOWLEDGE.get(value.upper(), {})
            if not footprint and kb:
                # Use first common footprint from KB
                common_fps = kb.get("common_footprints", [])
                if common_fps:
                    footprint = common_fps[0]

            if not footprint:
                # Infer from package type
                pkg = comp_data.get("package_type", "").lower()
                footprint = self.PACKAGE_TO_FOOTPRINT.get(pkg, "")

            if not footprint:
                # Default: try to guess from reference prefix
                prefix = ref.rstrip("0123456789").upper() if ref else ""
                if "U" in prefix:
                    footprint = "Package_DIP:DIP-8_W7.62mm"  # Default IC
                elif "Q" in prefix:
                    footprint = "Package_TO:TO-92"  # Default transistor
                elif "D" in prefix:
                    footprint = "Diode_SMD:D_SOD-123"  # Default diode
                elif "C" in prefix:
                    footprint = "Capacitor_SMD:C_0603_1608Metric"
                elif "R" in prefix:
                    footprint = "Resistor_SMD:R_0603_1608Metric"

            comp = Component(
                reference=ref,
                value=value,
                lib_id=comp_data.get("lib_id", f"Device:{ref[0] if ref else 'U'}"),
                footprint_name=footprint,
                position=Point(0, 0),
            )

            # Add pin information from knowledge base
            if kb and "pins" in kb:
                for pin_num, pin_name in kb["pins"].items():
                    comp.pins.append(Pin(
                        number=pin_num,
                        name=pin_name,
                        electrical_type="passive",
                        component_ref=ref,
                    ))

            design.components[ref] = comp

        # Build pin name→number reverse map from knowledge base
        pin_name_to_num: dict[str, dict[str, str]] = {}  # {comp_ref: {pin_name: pin_num}}
        for ref, comp in design.components.items():
            kb = self.COMPONENT_KNOWLEDGE.get(comp.value.upper(), {})
            if kb and "pins" in kb:
                pin_name_to_num[ref] = {v.upper(): k for k, v in kb["pins"].items()}

        # Build nets
        net_code = 0
        for net_data in raw.get("nets", []):
            net_code += 1
            name = net_data.get("name", f"/Net-{net_code}")
            is_power = net_data.get("is_power", False)

            net = Net(name=name, code=net_code)
            for conn in net_data.get("connections", []):
                ref = conn.get("reference", "")
                pin = str(conn.get("pin", "1"))

                # Try to map function name to physical pin number
                if ref in pin_name_to_num and not pin.isdigit():
                    mapped = pin_name_to_num[ref].get(pin.upper())
                    if mapped:
                        pin = mapped

                net.pins.append(PinRef(component_ref=ref, pin_number=pin))
            design.nets[name] = net

        return design

    def _render_pdf(self, path: Path) -> List[bytes]:
        """Render PDF pages as PNG images."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(path))
            images = []
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                images.append(pix.tobytes("png"))
            doc.close()
            return images
        except ImportError:
            pass

        try:
            from PIL import Image
            import subprocess
            # Try using system tools
            result = subprocess.run(
                ["pdftoppm", "-png", "-r", "200", str(path)],
                capture_output=True
            )
            if result.returncode == 0:
                # This would need more sophisticated handling
                pass
        except Exception:
            pass

        raise ImportError(
            "PDF reading requires PyMuPDF (fitz) or pdf2image. "
            "Install: pip install PyMuPDF"
        )
