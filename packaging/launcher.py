"""打包入口（PyInstaller）。

双击产物时的行为：

1. 无参数 → 直接启动内置机器人并打开浏览器；
2. 有参数 → 走正常 CLI（``WeFlowClone.exe pull`` 等）。

异常兜底：打包后没有终端，出错必须想办法让用户看见 ——
Windows 写日志并保留控制台；macOS 写日志并用系统弹窗提示。
"""

from __future__ import annotations

import multiprocessing
import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    try:
        from weflow_clone.paths import project_root

        root = project_root()
    except Exception:  # noqa: BLE001 - 连路径模块都挂了就退到临时目录
        root = Path.home() / "WeFlowClone"
    path = root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _notify_failure(message: str) -> None:
    log_path = _log_dir() / "crash.log"
    log_path.write_text(message, encoding="utf-8")
    if sys.platform == "darwin":
        try:
            import subprocess

            safe = message.replace('"', "'")[:800]
            subprocess.run(
                ["osascript", "-e", f'display dialog "WeFlowClone 启动失败：{safe}" buttons {{"好"}}'],
                capture_output=True,
                check=False,
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        print(message, file=sys.stderr)
    print(f"详细日志：{log_path}", file=sys.stderr)


def main() -> int:
    multiprocessing.freeze_support()
    try:
        from weflow_clone.cli import main as cli_main

        argv = sys.argv[1:]
        if not argv:
            argv = ["bot"]
        return int(cli_main(argv) or 0)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130
    except BaseException:  # noqa: BLE001 - 打包环境的最后一道防线
        _notify_failure(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
