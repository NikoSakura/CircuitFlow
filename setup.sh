#!/bin/bash
echo "========================================"
echo "  CircuitFlow - Setup"
echo "========================================"
echo

# Check Python
if command -v python3 &>/dev/null; then
    echo "[OK] Python found: $(python3 --version)"
elif command -v python &>/dev/null; then
    echo "[OK] Python found: $(python --version)"
else
    echo "[ERROR] Python not found. Install Python 3.10+"
    exit 1
fi

PYTHON=$(command -v python3 || command -v python)

echo "[*] Installing Python dependencies..."
$PYTHON -m pip install networkx numpy pydantic pydantic-settings typer rich httpx pyyaml openai anthropic websockets fastapi uvicorn python-multipart PyMuPDF

# Check Java
if command -v java &>/dev/null; then
    echo "[OK] Java found"
else
    echo "[WARN] Java not found. Install Java 17+ (sudo apt install openjdk-17-jre)"
fi

# Check KiCad
if $PYTHON -c "import pcbnew" 2>/dev/null; then
    echo "[OK] KiCad/pcbnew available"
else
    echo "[WARN] pcbnew not available. PCB export uses fallback."
    echo "       Install KiCad: https://www.kicad.org/download/"
fi

# Download FreeRouting
if [ -f "freerouting-1.9.0.jar" ]; then
    echo "[OK] FreeRouting JAR found"
else
    echo "[*] Downloading FreeRouting..."
    curl -L -o freerouting-1.9.0.jar "https://github.com/freerouting/freerouting/releases/download/v1.9.0/freerouting-1.9.0.jar" 2>/dev/null
    if [ -f "freerouting-1.9.0.jar" ]; then
        echo "[OK] FreeRouting downloaded"
    else
        echo "[WARN] Could not download FreeRouting."
    fi
fi

echo
echo "========================================"
echo "  Setup complete! Run: ./run.sh"
echo "========================================"
