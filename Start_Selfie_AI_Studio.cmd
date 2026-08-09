@echo off
cd /d "%~dp0"
py -3 "%~dp0main.py"
if errorlevel 1 (
  echo.
  echo Selfie AI Studio failed to start.
  echo Please send a screenshot of this window.
  pause
)
