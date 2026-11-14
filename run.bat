@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   AUTO VIDEO EDITOR - Twitter / Meme Template Generator
echo ============================================================
echo.

if "%~1"=="" (
    python main.py
) else (
    set /p CAPTION="Enter caption: "
    python main.py "%~1" --caption "%CAPTION%"
)

pause
