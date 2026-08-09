@echo off
cd /d "%~dp0"

if not exist ".git" (
  echo Git is not initialized.
  pause
  exit /b 1
)

echo ===== STATUS =====
git status
echo.
echo ===== RECENT COMMITS =====
git --no-pager log --oneline --decorate -10
echo.
echo ===== TAGS =====
git tag
pause
