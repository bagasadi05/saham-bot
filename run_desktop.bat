@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" desktop.py
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" desktop.py
) else (
    python desktop.py
)
