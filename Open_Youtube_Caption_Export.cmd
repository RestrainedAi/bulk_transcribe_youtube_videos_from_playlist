@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
call venv\Scripts\activate.bat
python youtube_transcript_panel.py
