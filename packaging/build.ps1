<#
.SYNOPSIS
    构建 Windows 安装包（单文件 exe）。

.DESCRIPTION
    在独立的 .build-venv 里装 PyInstaller，避免污染运行环境。
    产物：
      dist\WeFlowClone.exe                 ← 双击即用的主程序
      dist\WeFlowClone-<版本>-win64.zip    ← 对外分发的压缩包

.EXAMPLE
    .\packaging\build.ps1
    .\packaging\build.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BuildVenv = Join-Path $Root ".build-venv"
$VenvPy = Join-Path $BuildVenv "Scripts\python.exe"
$Version = "1.0.1"

# 版本号必须与 pyproject.toml 一致，否则产物名和 Release 标签会对不上
$pyprojectVersion = (
    Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"'
).Matches[0].Groups[1].Value
if ($pyprojectVersion -and $pyprojectVersion -ne $Version) {
    throw "build.ps1 版本 $Version 与 pyproject.toml 的 $pyprojectVersion 不一致，请先运行 scripts/bump_version.py"
}

function Step($msg) { Write-Host "`n> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "[ok] $msg" -ForegroundColor Green }

if ($Clean) {
    foreach ($path in @("$Root\dist", "$Root\build", $BuildVenv)) {
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
}

if (-not (Test-Path $VenvPy)) {
    Step "创建构建用虚拟环境"
    $candidates = @()
    if ($Python) { $candidates += $Python }
    $candidates += @("py -3.12", "python")
    $chosen = $null
    foreach ($candidate in $candidates) {
        $parts = $candidate.Split(" ")
        $pre = @()
        if ($parts.Length -gt 1) { $pre = $parts[1..($parts.Length - 1)] }
        try {
            # 注意：变量名不能叫 $version —— PowerShell 变量名不区分大小写，
            # 会把上面的 $Version（产物版本号）覆盖成 "3.12"。
            $pyVersion = & $parts[0] @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($pyVersion -eq "3.12") { $chosen = $candidate; break }
    }
    if (-not $chosen) { throw "需要 Python 3.12（WeClone 要求），请先安装" }
    $parts = $chosen.Split(" ")
    $pre = @()
    if ($parts.Length -gt 1) { $pre = $parts[1..($parts.Length - 1)] }
    & $parts[0] @pre -m venv $BuildVenv
}

Step "安装打包工具"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install --quiet pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw "安装 pyinstaller 失败" }

Step "生成图标"
& $VenvPy (Join-Path $Root "packaging\make_icons.py")

# 注意：不需要 `pip install -e .`。
# wfc.spec 里用 pathex 指向项目源码，PyInstaller 直接从源码收集；
# 少一步 pip 就少一个构建失败点（企业网络 / 索引镜像 / 构建后端都可能卡住）。

Step "PyInstaller 打包"
& $VenvPy -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $Root "build") `
    (Join-Path $Root "packaging\wfc.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

$exe = Join-Path $Root "dist\WeFlowClone\WeFlowClone.exe"
if (-not (Test-Path $exe)) { throw "没有生成 $exe" }

Step "打包压缩包"
$zip = Join-Path $Root "dist\WeFlowClone-$Version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $Root "dist\WeFlowClone") -DestinationPath $zip -CompressionLevel Optimal

$folderMb = [math]::Round((Get-ChildItem (Join-Path $Root "dist\WeFlowClone") -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Ok "程序目录：dist\WeFlowClone（$folderMb MB）"
Ok "exe：$exe"
Ok "zip：$zip（$zipMb MB）"
Write-Host ""
Write-Host "自检：& `"$exe`" version" -ForegroundColor DarkGray
Write-Host "启动：& `"$exe`" bot --no-browser" -ForegroundColor DarkGray
