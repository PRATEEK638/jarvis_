@echo off
REM JARVIS launcher. Double-click, or run from a terminal.
REM   JARVIS.bat              the desktop application (default)
REM   JARVIS.bat --cli        text-only terminal session
REM   JARVIS.bat --web        the browser control interface, at localhost:8731
REM   JARVIS.bat --voice      talk to it
REM   JARVIS.bat --status     what is available right now
REM   JARVIS.bat --progress   how much of the vision is actually built
REM   JARVIS.bat "create a folder called reports on my desktop"

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment. Is Python 3.11+ installed
        echo and on PATH? Try: python --version
        pause
        exit /b 1
    )
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r jarvis\requirements.txt
)

REM Ollama serves the local model; start it if it is installed but not running.
curl -s -m 2 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo Starting Ollama...
        start "" /min ollama serve
        timeout /t 3 /nobreak >nul
    )
)

if "%~1"=="--web" (
    ".venv\Scripts\python.exe" -m jarvis.web
    goto :eof
)

REM Default to the desktop app: it is the real product surface, and unlike the
REM browser it gets direct microphone/speaker access, so voice actually works.
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m jarvis.desktop
    goto :eof
)

if "%~1"=="--progress" (
    ".venv\Scripts\python.exe" -m jarvis.progress
    pause
    goto :eof
)

if "%~1"=="--cli" (
    shift
    ".venv\Scripts\python.exe" -m jarvis %*
    pause
    goto :eof
)

".venv\Scripts\python.exe" -m jarvis %*
endlocal
