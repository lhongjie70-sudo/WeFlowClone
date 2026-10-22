<#
.SYNOPSIS
    WeFlowClone 一键启动。

.DESCRIPTION
    默认启动**内置机器人**（不需要显卡、不需要训练，只要 API Key）。
    要跑微调后的模型服务，加 -Serve。

.EXAMPLE
    .\scripts\start.ps1                 # 自检 → 启动内置机器人
    .\scripts\start.ps1 -Pull           # 先从 WeFlow 同步聊天记录，再启动机器人
    .\scripts\start.ps1 -Serve          # 启动 WeClone 模型服务（需要已训练）
    .\scripts\start.ps1 -Pull -Serve    # 先同步，再起模型服务
    .\scripts\start.ps1 -SkipDoctor     # 跳过自检
#>
[CmdletBinding()]
param(
    [switch]$Pull,
    [switch]$Serve,
    [switch]$SkipDoctor,
    [int]$Port = 0
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$Py = if (Test-Path $VenvPy) { $VenvPy } else { "python" }

function Step($msg) { Write-Host "`n> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[!!] $msg" -ForegroundColor Yellow }

Set-Location $Root

if (-not $SkipDoctor) {
    Step "环境自检"
    & $Py -m weflow_clone doctor
    if ($LASTEXITCODE -ne 0) {
        Warn "自检有未通过项（通常是 WeFlow 没启动 / Token 没填）。机器人本身仍可用。"
        $answer = Read-Host "继续吗？(Y/n)"
        if ($answer -in @("n", "N", "no", "NO")) { exit 1 }
    }
}

if ($Pull) {
    Step "从 WeFlow 同步聊天记录"
    & $Py -m weflow_clone pull
    if ($LASTEXITCODE -ne 0) {
        Warn "同步失败：检查 WeFlow 是否在运行、weflow.jsonc 里的 Token"
    }
}

if ($Serve) {
    Step "启动 WeClone 模型服务（需要已安装训练依赖并完成微调）"
    $cliArgs = @("-m", "weflow_clone", "serve")
    if ($Port -gt 0) { $cliArgs += @("--port", "$Port") }
    & $Py @cliArgs
} else {
    Step "启动内置机器人（在浏览器里填 API Key 即可聊天）"
    $cliArgs = @("-m", "weflow_clone", "bot")
    if ($Port -gt 0) { $cliArgs += @("--port", "$Port") }
    & $Py @cliArgs
}

Write-Host ""
Write-Host "本次运行结束。" -ForegroundColor Green
