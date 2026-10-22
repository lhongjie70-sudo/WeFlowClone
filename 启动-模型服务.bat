@echo off
chcp 65001 >nul
title WeFlowClone - 模型服务
cd /d "%~dp0"

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" -Serve %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" -Serve %*
)

echo.
pause
