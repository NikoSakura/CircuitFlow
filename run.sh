#!/bin/bash
echo "========================================"
echo "  CircuitFlow - PCB Design Pipeline"
echo "========================================"
echo
echo "Starting Web UI at http://127.0.0.1:7860"
echo
python -m kcad_auto_pcb.cli.main web
