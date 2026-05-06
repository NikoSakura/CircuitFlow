from __future__ import annotations
import json
import re
from typing import Any, Optional


class ResponseParser:
    """Parse structured JSON from LLM responses.

    Handles common LLM output quirks:
    - Markdown code fences (```json ... ```)
    - Leading/trailing text
    - Multiple JSON objects (takes first valid)
    """

    @staticmethod
    def parse_json(text: str) -> Optional[dict]:
        """Extract and parse JSON from LLM response text."""
        if not text:
            return None

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fences
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find JSON object boundaries
        for match in re.finditer(r'\{[\s\S]*?\}', text):
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def parse_placement_response(text: str) -> dict:
        """Parse placement optimization response.

        Expected format: {"swaps": [["R1", "R2"], ...], "rotations": {"U1": 90, ...}}
        """
        data = ResponseParser.parse_json(text)
        if data is None:
            return {"swaps": [], "rotations": {}}
        return {
            "swaps": data.get("swaps", []),
            "rotations": data.get("rotations", {}),
        }

    @staticmethod
    def parse_routing_response(text: str) -> dict:
        """Parse routing strategy response.

        Expected format: {
            "layer_assignments": {"VCC": ["In1.Cu"], "Net-1": ["F.Cu"], ...},
            "net_order": ["VCC", "GND", "Net-1", ...],
            "critical_nets": ["Net-3"]
        }
        """
        data = ResponseParser.parse_json(text)
        if data is None:
            return {"layer_assignments": {}, "net_order": [], "critical_nets": []}
        return {
            "layer_assignments": data.get("layer_assignments", {}),
            "net_order": data.get("net_order", []),
            "critical_nets": data.get("critical_nets", []),
        }
