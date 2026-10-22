#!/usr/bin/env bash
# 构建 macOS / Linux 产物。
#
# macOS 产物：
#   dist/WeFlowClone                 （命令行版，可传 --port 等参数）
#   dist/WeFlowClone.app             （双击启动）
#   dist/WeFlowClone-<ver>-macos-<arch>.dmg
# Linux 产物（仅用于本地调试）：
#   dist/WeFlowClone
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_VENV="$ROOT/.build-venv"
VERSION="1.0.0"
ARCH="$(uname -m)"
PY="${PYTHON:-python3}"

echo "> 项目根：$ROOT"

if ! "$PY" -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
  echo "!! 需要 Python 3.12（WeClone 的要求）。可用 PYTHON=/path/to/python3.12 指定。"
  "$PY" -V || true
  exit 1
fi

if [ ! -x "$BUILD_VENV/bin/python" ]; then
  echo "> 创建构建用虚拟环境"
  "$PY" -m venv "$BUILD_VENV"
fi
VENV_PY="$BUILD_VENV/bin/python"

echo "> 安装打包工具"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install --quiet pyinstaller pillow

echo "> 生成图标"
"$VENV_PY" "$ROOT/packaging/make_icons.py"

# 不需要 pip install -e：spec 用 pathex 直接从源码收集，少一个构建失败点。

echo "> PyInstaller 打包"
"$VENV_PY" -m PyInstaller --noconfirm --clean \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build" \
  "$ROOT/packaging/wfc.spec"

if [ "$(uname)" = "Darwin" ]; then
  echo "> 生成 DMG"
  rm -f "$ROOT/dist/WeFlowClone-$VERSION-macos-$ARCH.dmg"
  # 首次运行提示：未签名应用需要右键打开
  hdiutil create \
    -volname "WeFlowClone" \
    -srcfolder "$ROOT/dist/WeFlowClone.app" \
    -ov -format UDZO \
    "$ROOT/dist/WeFlowClone-$VERSION-macos-$ARCH.dmg"
  echo "[ok] dist/WeFlowClone-$VERSION-macos-$ARCH.dmg"
  echo "[ok] dist/WeFlowClone.app"
else
  # Linux 下给一个 tar.gz，方便本地验证
  tar -C "$ROOT/dist" -czf "$ROOT/dist/WeFlowClone-$VERSION-linux-$ARCH.tar.gz" WeFlowClone
  echo "[ok] dist/WeFlowClone-$VERSION-linux-$ARCH.tar.gz"
fi

echo
echo "自检：dist/WeFlowClone/WeFlowClone version"
echo "启动：dist/WeFlowClone/WeFlowClone bot --no-browser"
