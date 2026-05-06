@echo off
echo ========================================
echo   CircuitFlow - Setup
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)
echo [OK] Python found

REM Install Python deps
echo [*] Installing Python dependencies...
pip install networkx numpy pydantic pydantic-settings typer rich httpx pyyaml openai anthropic websockets fastapi uvicorn python-multipart PyMuPDF
if %errorlevel% neq 0 (
    echo [WARN] Some packages failed to install. Continuing...
)

REM Check Java
java -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Java not found. FreeRouting autorouter will not work.
    echo        Install Java 17+ from https://adoptium.net
) else (
    echo [OK] Java found
)

REM Check KiCad
if exist "C:\Program Files\KiCad\10.0\bin\python.exe" (
    echo [OK] KiCad 10.0 found
) else if exist "C:\Program Files\KiCad\9.0\bin\python.exe" (
    echo [OK] KiCad 9.0 found
) else if exist "C:\Program Files\KiCad\bin\python.exe" (
    echo [OK] KiCad found
) else (
    echo [WARN] KiCad not detected. PCB export will use JSON fallback.
    echo        Install from https://www.kicad.org/download/
)

REM Download FreeRouting JAR
if exist "freerouting-1.9.0.jar" (
    echo [OK] FreeRouting JAR found
) else (
    echo [*] Downloading FreeRouting...
    curl -L -o freerouting-1.9.0.jar "https://github.com/freerouting/freerouting/releases/download/v1.9.0/freerouting-1.9.0.jar" 2>nul
    if exist "freerouting-1.9.0.jar" (
        echo [OK] FreeRouting downloaded
    ) else (
        echo [WARN] Could not download FreeRouting. Autorouting disabled.
        echo        Download manually: https://github.com/freerouting/freerouting/releases
    )
)

echo.
echo ========================================
echo   Setup complete!
echo   Run: run.bat
echo ========================================
pause
