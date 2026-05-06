@echo off
title CircuitFlow

REM Check if setup needed
python -c "import networkx" 2>nul
if %errorlevel% neq 0 (
    echo First run detected. Running setup...
    call setup.bat
)

echo ========================================
echo   CircuitFlow - PCB Design Pipeline
echo ========================================
echo   Web UI: http://127.0.0.1:7860
echo ========================================
echo.

REM Auto-detect KiCad Python for best results
set KICAD_PYTHON=
if exist "C:\Program Files\KiCad\10.0\bin\python.exe" set KICAD_PYTHON=C:\Program Files\KiCad\10.0\bin\python.exe
if exist "C:\Program Files\KiCad\9.0\bin\python.exe" set KICAD_PYTHON=C:\Program Files\KiCad\9.0\bin\python.exe

if defined KICAD_PYTHON (
    echo KiCad Python detected. Full pipeline available.
) else (
    echo KiCad not found. JSON fallback mode.
)

python -m kcad_auto_pcb.cli.main web
pause
