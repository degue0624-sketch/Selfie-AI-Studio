@echo off
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
  echo ERROR: Git was not found.
  pause
  exit /b 1
)

if exist ".git" (
  echo Git is already initialized.
  git status
  pause
  exit /b 0
)

git init
if errorlevel 1 goto :error

git add .
if errorlevel 1 goto :error

git config user.name >nul 2>&1
if errorlevel 1 (
  echo Git user.name is not configured.
  echo Run:
  echo   git config --global user.name "Your Name"
  echo   git config --global user.email "you@example.com"
  pause
  exit /b 1
)

git config user.email >nul 2>&1
if errorlevel 1 (
  echo Git user.email is not configured.
  echo Run:
  echo   git config --global user.email "you@example.com"
  pause
  exit /b 1
)

git commit -m "Selfie AI Studio v1.5 baseline"
if errorlevel 1 goto :error

git tag v1.5
echo.
echo Git initialization complete.
echo Tag: v1.5
git status
pause
exit /b 0

:error
echo.
echo ERROR: Git operation failed.
pause
exit /b 1
