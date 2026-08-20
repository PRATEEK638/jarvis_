@echo off
REM One-command setup for a new machine.
REM
REM Safe to re-run: it creates what is missing and leaves what already exists.
REM Everything machine-specific (local models on/off, model choice) is decided
REM at runtime from measured hardware, so there is nothing to hand-edit here.

setlocal
cd /d "%~dp0"
echo.
echo   J.A.R.V.I.S  -  setup
echo   =====================
echo.

REM --- Python -------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo   [X] Python is not on PATH.
    echo       Install Python 3.11 or newer from python.org and tick
    echo       "Add python.exe to PATH" during installation, then re-run this.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   [ok] Python %PYVER%

REM --- virtual environment -------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo   [..] creating virtual environment
    python -m venv .venv
    if errorlevel 1 (
        echo   [X] could not create the virtual environment
        pause
        exit /b 1
    )
)
echo   [ok] virtual environment

REM --- dependencies --------------------------------------------------------
echo   [..] installing dependencies ^(this takes a minute on a new machine^)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r jarvis\requirements.txt
if errorlevel 1 (
    echo   [X] dependency installation failed
    pause
    exit /b 1
)
echo   [ok] dependencies

REM --- API keys ------------------------------------------------------------
if not exist "jarvis\config\keys.json" (
    copy /y "jarvis\config\keys.example.json" "jarvis\config\keys.json" >nul
    echo   [!] created jarvis\config\keys.json from the example.
    echo       Add at least one API key to it, or JARVIS runs local-only.
) else (
    echo   [ok] API keys present
)

REM --- Ollama, only where it is worth having -------------------------------
where ollama >nul 2>&1
if errorlevel 1 (
    echo   [--] Ollama not installed ^(optional; only useful on a 32GB+ machine^)
) else (
    echo   [ok] Ollama installed
)

REM --- report what this machine can do ------------------------------------
echo.
".venv\Scripts\python.exe" -m jarvis.setupcheck
if errorlevel 1 (
    echo.
    echo   Setup finished with warnings above.
) else (
    echo.
    echo   Setup complete.
)
echo.
echo   Start it with:  JARVIS.bat
echo.
pause
endlocal
