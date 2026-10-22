"""项目路径解析。

WeFlowClone 有两种运行方式：

1. 源码 / 仓库方式：``<项目根>/weflow_clone`` 存在，项目根就是包目录的上一级。
2. 已安装方式：包被安装进 site-packages，此时项目根由环境变量
   ``WFC_PROJECT_ROOT`` 指定，找不到则退回当前工作目录。

所有数据都以「项目根」为基准，避免 cwd 变化导致数据写错地方。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_PROJECT_ROOT = "WFC_PROJECT_ROOT"
_DEFAULT_CONFIG_NAME = "weflow.jsonc"
APP_DIR_NAME = "WeFlowClone"


def _user_data_dir() -> Path:
    """打包成 exe / app 后的数据目录（安装目录通常不可写）。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def project_root() -> Path:
    """返回项目根目录（绝对路径）。

    * 环境变量 ``WFC_PROJECT_ROOT`` 优先；
    * PyInstaller 打包后（``sys.frozen``）用用户数据目录，避免写入安装位置；
    * 源码方式：包目录的上一级；
    * 兜底：当前工作目录。
    """
    env = os.environ.get(_ENV_PROJECT_ROOT)
    if env:
        return Path(env).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return _user_data_dir()

    # 源码方式：__file__ = <root>/weflow_clone/paths.py
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / "weflow_clone").is_dir():
        return candidate

    return Path.cwd().resolve()


def config_path() -> Path:
    """返回整合层配置文件路径，可用环境变量覆盖。"""
    env = os.environ.get("WFC_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / _DEFAULT_CONFIG_NAME


def resolve_inside_project(value: str | os.PathLike[str], root: Path | None = None) -> Path:
    """把配置里的相对路径解析到项目根下。"""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (root or project_root()) / path


def data_dir(root: Path | None = None) -> Path:
    return resolve_inside_project("data", root)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
