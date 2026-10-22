@echo off
chcp 65001 >nul
title WeFlowClone - 安装
cd /d "%~dp0"

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" %*
)

echo.
pause
