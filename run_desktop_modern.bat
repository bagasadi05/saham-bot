@echo off
cd /d "%~dp0"
set "SAHAMBOT_LAN=1"
if exist "%~dp0..\stockbit-mcp\dist\bin\stockbit-mcp.js" (
    set "SAHAMBOT_STOCKBIT_MCP=1"
    set "STOCKBIT_MCP_PATH=%~dp0..\stockbit-mcp\dist\bin\stockbit-mcp.js"
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" desktop_modern.py
) else if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" desktop_modern.py
) else (
    start "" python desktop_modern.py
)
