@echo off
chcp 65001 >nul
title WeFlowClone
cd /d "%~dp0"

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
)

echo.
pause
