@echo off
cd /d "%~dp0"
echo Starting Selfie AI Studio in diagnostic mode...
echo.
py -3 "%~dp0main.py"
echo.
echo Exit code: %errorlevel%
pause
