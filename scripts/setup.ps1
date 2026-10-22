<#
.SYNOPSIS
    WeFlowClone 环境安装脚本（Windows / PowerShell 7+）。

.DESCRIPTION
    做四件事：
      1. 建 .venv（Python 3.12，WeClone 要求）
      2. 安装本项目的整合层（wfc 命令）
      3. 安装 WeClone 本体与训练依赖（torch / llamafactory / transformers ...）
      4. 生成配置与目录（wfc init）

    训练依赖体积很大（数 GB）。只想先把数据链路跑通，用 -SkipTraining。

.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -Mirror           # 国内网络：pip 走清华源
    .\scripts\setup.ps1 -SkipTraining     # 只装数据管线，不装 torch
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipTraining,
    [switch]$Mirror,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$IndexArgs = @()
if ($Mirror) { $IndexArgs = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple") }

function Step($msg) { Write-Host "`n> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "[ok] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[xx] $msg" -ForegroundColor Red }

function Find-Python312 {
    $candidates = @()
    if ($Python) { $candidates += $Python }
    $candidates += @("py -3.12", "python3.12", "python")
    foreach ($candidate in $candidates) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        $pre = @()
        if ($parts.Length -gt 1) { $pre = $parts[1..($parts.Length - 1)] }
        try {
            $out = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
        } catch { continue }
        if (-not $out) { continue }
        $version = $out[0]
        $path = $out[-1]
        if ($version -eq "3.12") { return $path }
    }
    return $null
}

Step "检查 Python 3.12"
$pythonExe = Find-Python312
if (-not $pythonExe) {
    Fail "没找到 Python 3.12。WeClone 明确要求 3.12（>=3.12,<3.13）。"
    Write-Host "  安装方式：winget install Python.Python.3.12"
    Write-Host "  或从 https://www.python.org/downloads/release/python-3129/ 安装后重试"
    exit 1
}
Ok "使用 $pythonExe"

if (-not (Test-Path $VenvPy) -or $Force) {
    Step "创建虚拟环境 .venv"
    & $pythonExe -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "创建 venv 失败"; exit 1 }
}
Ok ".venv 就绪：$VenvPy"

Step "升级 pip"
& $VenvPy -m pip install --upgrade pip @IndexArgs | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "pip 升级失败，继续" }

Step "安装整合层（wfc 命令）"
& $VenvPy -m pip install -e $Root @IndexArgs
if ($LASTEXITCODE -ne 0) { Fail "安装 weflow-clone 失败"; exit 1 }
Ok "wfc 已安装"

if (-not (Test-Path (Join-Path $Root "vendor\WeClone\pyproject.toml"))) {
    Fail "缺少 vendor\WeClone。请先执行："
    Write-Host "  git clone https://github.com/xming521/WeClone.git vendor/WeClone"
    exit 1
}

Step "安装 WeClone 本体"
& $VenvPy -m pip install -e (Join-Path $Root "vendor\WeClone") @IndexArgs
if ($LASTEXITCODE -ne 0) { Fail "安装 WeClone 失败"; exit 1 }
Ok "WeClone 已安装"

if (-not $SkipTraining) {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Step "用 uv 安装训练依赖（torch cu126 + llamafactory，体积较大）"
        $uvArgs = @("pip", "install", "--python", $VenvPy, "--group", "main", "-e", (Join-Path $Root "vendor\WeClone"))
        if ($Mirror) { $uvArgs += @("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple") }
        & uv @uvArgs
        if ($LASTEXITCODE -ne 0) { Warn "训练依赖安装失败，数据管线仍可用；修复后可重跑本脚本" }
    } else {
        Warn "没有检测到 uv。训练依赖（torch/llamafactory）请手动安装："
        Write-Host "  pip install uv"
        Write-Host "  uv pip install --python `"$VenvPy`" --group main -e vendor\WeClone"
        Write-Host "  或参考 docs/05-训练与部署.md"
    }

    Step "安装中文分词模型（WeClone 数据清洗可选依赖）"
    & $VenvPy -m pip install "https://github.com/explosion/spacy-models/releases/download/zh_core_web_sm-3.8.0/zh_core_web_sm-3.8.0-py3-none-any.whl" @IndexArgs 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "zh_core_web_sm 安装失败（不影响主流程）" }
}

Step "生成配置与目录"
& $VenvPy -m weflow_clone init
if ($LASTEXITCODE -ne 0) { Warn "init 返回非 0，请手动执行 wfc init" }

Write-Host ""
Ok "安装完成"
Write-Host ""
Write-Host "接下来：" -ForegroundColor Cyan
Write-Host "  1. 打开 WeFlow -> 设置 -> API 服务 -> 启动服务，复制 Access Token"
Write-Host "  2. 把 Token 写进 weflow.jsonc 的 weflow.access_token"
Write-Host "  3. .\.venv\Scripts\wfc.exe doctor"
Write-Host "  4. .\.venv\Scripts\wfc.exe pull"
