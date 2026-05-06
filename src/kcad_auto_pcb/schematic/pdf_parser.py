"""PDF schematic parser — extracts components and connections from KiCad-exported PDFs.

Uses PyMuPDF to extract text with spatial positions, then clusters nearby
text elements to identify components (reference + value) and net labels.

Much more accurate than pure LLM vision for vector PDFs from KiCad/Altium.
"""

from __future__ import annotations
import re
import math
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from .model import Component, Net, PinRef, Design
from ..geometry.point import Point


@dataclass
class TextElement:
    text: str
    x: float
    y: float
    page: int


class PDFSchematicParser:
    """Parse KiCad-exported PDF schematics by text extraction + spatial clustering."""

    # Valid component reference prefixes
    VALID_PREFIXES = {
        'R', 'C', 'U', 'L', 'D', 'J', 'Q', 'Y', 'X', 'T', 'TP', 'LED',
        'SW', 'FB', 'F', 'P', 'K', 'M', 'RN', 'RP', 'RV', 'RT', 'JP',
        'BT', 'BZ', 'CN', 'DS', 'FID', 'MH', 'S', 'SG', 'TC', 'TH', 'VR',
    }
    REF_PATTERN = re.compile(r'^([A-Za-z]+)(\d+)$')  # R1, C2, U1, LED0, TP7
    VALUE_PATTERN = re.compile(
        r'^([\d.]+)\s*(k|M|m|u|n|p|μ)?\s*([ΩFHVAsHz%]+\b|[ΩFHVAsHz%]+\b)?$|'
        r'^([\d.]+)([kKmMuUnNpP])([ΩFHVAMHz])$|'
        r'^([\d.]+)([kKmMuUnNpP][ΩFHVAMHz])$'
    )  # 10k, 4.7K, 100nF, 0.1uF, 8.2uH, 470, 0

    def parse(self, pdf_path: str | Path) -> Design:
        """Parse a PDF schematic and return a Design object."""
        import fitz
        doc = fitz.open(str(pdf_path))

        # Extract all text with positions
        elements: List[TextElement] = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            blocks = page.get_text('dict')['blocks']
            for b in blocks:
                if 'lines' not in b:
                    continue
                for line in b['lines']:
                    for span in line['spans']:
                        text = span['text'].strip()
                        if text and len(text) > 0:
                            elements.append(TextElement(
                                text=text,
                                x=span['bbox'][0],
                                y=span['bbox'][1],
                                page=page_num,
                            ))
        doc.close()

        # Phase 1: extract components (reference + value)
        components = self._extract_components(elements)

        # Phase 2: extract net labels
        nets = self._extract_nets(elements)

        design = Design()
        for comp in components:
            design.components[comp.reference] = comp
        for net_name, net in nets.items():
            design.nets[net_name] = net

        if not design.nets:
            # Fallback: create nets from components that share same labels
            self._infer_nets_from_labels(design, elements)

        return design

    def _extract_components(self, elements: List[TextElement]) -> List[Component]:
        """Extract components by matching refs with nearby values."""
        # Find all reference designators
        refs = []
        for e in elements:
            m = self.REF_PATTERN.match(e.text)
            if m:
                prefix, num = m.group(1), m.group(2)
                # Validate prefix
                if prefix.upper() not in self.VALID_PREFIXES: continue
                refs.append(e)

        # For each reference, find the nearest value text
        components = []
        for ref_elem in refs:
            # Find closest value element (within 50 units)
            value_text = ""
            best_dist = 50
            for e in elements:
                if e is ref_elem: continue
                if e.page != ref_elem.page: continue
                if not self._is_value(e.text): continue
                dist = math.hypot(e.x - ref_elem.x, e.y - ref_elem.y)
                if dist < best_dist:
                    value_text = e.text
                    best_dist = dist

            # Determine footprint from prefix
            footprint = self._guess_footprint(ref_elem.text, value_text)

            components.append(Component(
                reference=ref_elem.text,
                value=value_text,
                lib_id=f"Device:{ref_elem.text[0]}" if ref_elem.text[0].isalpha() else "Device:U",
                footprint_name=footprint,
                position=Point(ref_elem.x, ref_elem.y),
            ))

        return components

    def _extract_nets(self, elements: List[TextElement]) -> Dict[str, Net]:
        """Extract net labels from the PDF."""
        nets = {}
        net_code = 0

        # Look for net label patterns
        net_label_pattern = re.compile(
            r'^(VCC|VDD|VSS|GND|VBAT|VBUS|VIN|VOUT|VREF|VCAL|'
            r'I2C_|SPI_|UART_|CAM_|DISP_|FPGA_|TOUCH_|SWD|RESET|'
            r'LED\d|PMIC_|CHARGE|AMUX|CAPSNS|MEM_|DD\d+|ADQ\d|DQS\d|'
            r'MCLK|XCLR|XHD|XVD|XCLK|PCLK|HREF|VSYNC|HSYNC|'
            r'D[0-7]|DD[0-9]+|ADQ[0-7]|DQS[0-9])',
            re.IGNORECASE
        )

        for e in elements:
            if net_label_pattern.match(e.text) and len(e.text) >= 2:
                net_code += 1
                name = f"/{e.text}" if not e.text.startswith("/") else e.text
                nets[name] = Net(
                    name=name,
                    code=net_code,
                )

        return nets

    def _infer_nets_from_labels(self, design: Design, elements: List[TextElement]):
        """Fallback: create basic nets from power labels."""
        # Group power pins
        vcc_nets = ["VCC", "VDD", "VBAT", "VBUS", "VIN"]
        gnd_nets = ["GND", "VSS", "AGND", "DGND"]

        for net_names, is_power in [(vcc_nets, True), (gnd_nets, True)]:
            code = len(design.nets) + 1
            net = Net(name=f"/{'VCC' if is_power else 'GND'}_{code}", code=code)
            # Add all components to power nets as placeholder
            design.nets[net.name] = net

    def _is_value(self, text: str) -> bool:
        """Check if text looks like a component value."""
        if self.REF_PATTERN.match(text): return False
        if re.match(r'^[A-Z_]{3,}$', text): return False  # All caps labels
        if re.match(r'^\d+\.\d+\.\d+$', text): return False  # Version numbers
        return bool(re.match(r'[\d.]+[kKmMuUnNpPμ]', text) or
                   re.match(r'^[\d.]+$', text) and len(text) <= 6)

    def _guess_footprint(self, ref: str, value: str = "") -> str:
        """Guess footprint based on reference prefix and value."""
        prefix = ref.rstrip("0123456789").upper() if ref else ""

        if "U" in prefix or "IC" in prefix:
            return "Package_QFN:QFN-32_5x5mm"  # Default small IC
        elif "R" in prefix:
            return "Resistor_SMD:R_0603_1608Metric"
        elif "C" in prefix:
            val = value.upper()
            if "10U" in val or "4.7U" in val or "22U" in val:
                return "Capacitor_SMD:C_0805_2012Metric"
            return "Capacitor_SMD:C_0603_1608Metric"
        elif "L" in prefix:
            return "Inductor_SMD:L_0805_2012Metric"
        elif "D" in prefix:
            return "LED_SMD:LED_0603_1608Metric" if "LED" in ref.upper() else "Diode_SMD:D_SOD-123"
        elif "J" in prefix:
            return "Connector_PinHeader:PinHeader_1x06_P2.54mm"
        elif "TP" in prefix:
            return "TestPoint:TestPoint_Pad_D1.0mm"
        elif "Q" in prefix:
            return "Package_TO:TO-92"
        elif "Y" in prefix or "X" in prefix:
            return "Crystal:Crystal_SMD_3225-4Pin"
        return "Resistor_SMD:R_0603_1608Metric"
