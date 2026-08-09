@echo off
cd /d "%~dp0"

if not exist ".git" (
  echo Git is not initialized.
  pause
  exit /b 1
)

set /p MSG=Commit message: 
if "%MSG%"=="" set MSG=Studio update

git add .
git commit -m "%MSG%"
git status
pause
