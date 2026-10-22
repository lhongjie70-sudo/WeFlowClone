# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格。

产物：
* Windows：``dist/WeFlowClone.exe``（单文件，双击即启动内置机器人）
* macOS：``dist/WeFlowClone`` + ``dist/WeFlowClone.app``

只打包整合层，**不打 WeClone**（torch 十几 GB，而且训练本来就需要完整环境）。
"""

from pathlib import Path
import sys

PROJECT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH 是 spec 所在目录（packaging/）
PACKAGING = Path(SPECPATH).resolve()  # noqa: F821

datas = [
    (str(PROJECT / "weflow_clone" / "bot" / "web" / "index.html"), "weflow_clone/bot/web"),
]

icon_file = None
if sys.platform == "win32":  # noqa: F821
    candidate = PACKAGING / "icon.ico"
    icon_file = str(candidate) if candidate.is_file() else None
elif sys.platform == "darwin":  # noqa: F821
    candidate = PACKAGING / "icon.icns"
    icon_file = str(candidate) if candidate.is_file() else None

excludes = [
    "tkinter",
    "pandas",
    "numpy",
    "torch",
    "transformers",
    "llamafactory",
    "weclone",
    "matplotlib",
    "PIL",
    "pytest",
    "setuptools",
    "pip",
]

a = Analysis(  # noqa: F821
    [str(PACKAGING / "launcher.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeFlowClone",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# onedir 而不是 onefile：
#   * 启动不需要往 %TEMP% 解压（某些企业环境会禁止，单文件会直接启动失败）
#   * 冷启动快 2~3 倍
#   * 杀软误报率明显更低
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WeFlowClone",
)

if sys.platform == "darwin":  # noqa: F821
    app = BUNDLE(  # noqa: F821
        coll,
        name="WeFlowClone.app",
        icon=icon_file,
        bundle_identifier="com.weflowclone.app",
        info_plist={
            "CFBundleName": "WeFlowClone",
            "CFBundleDisplayName": "WeFlowClone",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "AGPL-3.0 · 基于 WeClone（AGPL-3.0），对接 WeFlow（CC BY-NC-SA 4.0，不可商用）",
        },
    )
