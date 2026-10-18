@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if not exist "%SCRIPT_DIR%logs" mkdir "%SCRIPT_DIR%logs"
set "LOG=%SCRIPT_DIR%logs\launcher.log"
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
echo [%DATE% %TIME%] Launch started >> "%LOG%"

if not exist "%SCRIPT_DIR%venv\Scripts\python.exe" (
    echo [%DATE% %TIME%] ERROR: venv\Scripts\python.exe not found >> "%LOG%"
    exit /b 2
)

"%SCRIPT_DIR%venv\Scripts\python.exe" "%SCRIPT_DIR%startup_selfcheck.py" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] ERROR: startup self-check failed, see logs\selfcheck.log >> "%LOG%"
    exit /b 3
)

"%SCRIPT_DIR%venv\Scripts\python.exe" "%SCRIPT_DIR%watcher_control_panel.py" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] ERROR: control panel exited with code %ERRORLEVEL% >> "%LOG%"
    exit /b 4
)
echo [%DATE% %TIME%] Control panel closed normally >> "%LOG%"
exit /b 0
