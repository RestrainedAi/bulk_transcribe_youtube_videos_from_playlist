@echo off
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"
"%SCRIPT_DIR%venv\Scripts\pythonw.exe" "%SCRIPT_DIR%watcher_control_panel.py"
if errorlevel 1 (
    "%SCRIPT_DIR%venv\Scripts\python.exe" "%SCRIPT_DIR%watcher_control_panel.py"
    pause
)
