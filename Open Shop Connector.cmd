@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m agent.gui
) else (
    echo Set up the Python virtual environment first. See README.md.
    pause
)
