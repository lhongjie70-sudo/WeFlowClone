"""整合层配置：读取 ``weflow.jsonc``。

为什么单独一个配置文件？
------------------------
WeClone 的 ``settings.jsonc`` 由 pydantic 模型校验，部分子模型是
``extra="forbid"``；把 WeFlow 的配置写进去有破坏上游 schema 的风险。
因此这里坚持「上游零改动」：

* ``settings.jsonc`` —— WeClone 原生配置（训练 / 推理 / 数据处理）
* ``weflow.jsonc``   —— 本项目的配置（WeFlow 连接、导出策略、管线参数）

两个文件都在项目根，``wfc init`` 一次性生成。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .paths import config_path, project_root, resolve_inside_project

DEFAULT_BASE_URL = "http://127.0.0.1:5031"


# --------------------------------------------------------------------------- #
# JSONC 解析（标准库实现，只处理注释与尾逗号，拒绝花哨语法）
# --------------------------------------------------------------------------- #
def _strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释、尾逗号，字符串内容原样保留。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue

        out.append(ch)
        i += 1

    cleaned = "".join(out)
    # 去掉 } , ] 前面的尾逗号（字符串内的 , 已在上面整体拷贝，需再判断一次）
    return _strip_trailing_commas(cleaned)


def _strip_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # 丢弃这个逗号
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def load_jsonc(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")
    try:
        # utf-8-sig：兼容 Windows 记事本 / PowerShell 写入的 BOM
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(_strip_jsonc(text))
    except json.JSONDecodeError as exc:  # pragma: no cover - 依赖用户输入
        raise ConfigError(f"配置文件不是合法 JSON/JSONC：{path}（{exc}）") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"配置文件不是 UTF-8 编码：{path}（{exc}）") from exc


# --------------------------------------------------------------------------- #
# 配置模型
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class WeFlowSettings:
    base_url: str = DEFAULT_BASE_URL
    access_token: str = ""
    timeout_seconds: float = 30.0
    retries: int = 2
    page_size: int = 1000
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    include_groups: bool = False
    max_sessions: int = 0


@dataclass(slots=True)
class MediaSettings:
    download: bool = True
    images: bool = True
    voices: bool = False
    videos: bool = False
    emojis: bool = False
    max_mb: float = 20.0


@dataclass(slots=True)
class ExportSettings:
    data_dir: str = "data"
    session_folder_mode: str = "display"  # display | username
    timestamp_format: str = "local"  # local | iso
    self_name: str = "我"
    max_messages_per_file: int = 20000
    include_types: list[str] = field(
        default_factory=lambda: ["text", "image", "file"]
    )
    media: MediaSettings = field(default_factory=MediaSettings)


@dataclass(slots=True)
class WeCloneSettings:
    project_dir: str = "vendor/WeClone"
    config_path: str = "settings.jsonc"
    python: str = ""
    auto_make_dataset: bool = False


@dataclass(slots=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 5175
    inference: bool = True
    open_browser: bool = True


@dataclass(slots=True)
class WatchSettings:
    debounce_seconds: float = 20.0
    sessions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Settings:
    weflow: WeFlowSettings
    export: ExportSettings
    weclone: WeCloneSettings
    server: ServerSettings
    watch: WatchSettings
    root: Path
    source: Path

    # -- 派生路径 --------------------------------------------------------- #
    @property
    def data_dir(self) -> Path:
        return resolve_inside_project(self.export.data_dir, self.root)

    @property
    def csv_dir(self) -> Path:
        return self.data_dir / "dataset" / "csv"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "dataset" / "media"

    @property
    def weclone_dir(self) -> Path:
        """WeClone 源码目录（用于以正确 cwd 调用 weclone-cli）。"""
        return resolve_inside_project(self.weclone.project_dir, self.root)

    @property
    def weclone_settings_path(self) -> Path:
        path = Path(self.weclone.config_path)
        return path if path.is_absolute() else self.weclone_dir / path


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_settings(path: Path | None = None, *, root: Path | None = None) -> Settings:
    """读取并校验配置。缺失时给出「先运行 wfc init」的明确提示。"""
    source = path or config_path()
    raw = load_jsonc(source)
    root_dir = root or project_root()

    weflow_raw = raw.get("weflow") or {}
    export_raw = raw.get("export") or {}
    media_raw = export_raw.get("media") or {}
    weclone_raw = raw.get("weclone") or {}
    server_raw = raw.get("server") or {}
    watch_raw = raw.get("watch") or {}

    weflow = WeFlowSettings(
        base_url=str(weflow_raw.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        access_token=str(weflow_raw.get("access_token") or ""),
        timeout_seconds=float(weflow_raw.get("timeout_seconds", 30) or 30),
        retries=int(weflow_raw.get("retries", 2) or 0),
        page_size=max(1, min(10000, int(weflow_raw.get("page_size", 1000) or 1000))),
        include=_as_list(weflow_raw.get("include")),
        exclude=_as_list(weflow_raw.get("exclude")),
        include_groups=_as_bool(weflow_raw.get("include_groups"), False),
        max_sessions=int(weflow_raw.get("max_sessions", 0) or 0),
    )

    media = MediaSettings(
        download=_as_bool(media_raw.get("download"), True),
        images=_as_bool(media_raw.get("images"), True),
        voices=_as_bool(media_raw.get("voices"), False),
        videos=_as_bool(media_raw.get("videos"), False),
        emojis=_as_bool(media_raw.get("emojis"), False),
        max_mb=float(media_raw.get("max_mb", 20) or 20),
    )

    include_types = _as_list(export_raw.get("include_types")) or ["text", "image", "file"]
    known_types = {"text", "image", "voice", "video", "sticker", "file", "location", "other"}
    unknown = [t for t in include_types if t not in known_types]
    if unknown:
        raise ConfigError(f"export.include_types 含未知类型：{', '.join(unknown)}")

    export = ExportSettings(
        data_dir=str(export_raw.get("data_dir") or "data"),
        session_folder_mode=str(export_raw.get("session_folder_mode") or "display"),
        timestamp_format=str(export_raw.get("timestamp_format") or "local"),
        self_name=str(export_raw.get("self_name") or "我"),
        max_messages_per_file=int(export_raw.get("max_messages_per_file", 20000) or 20000),
        include_types=include_types,
        media=media,
    )
    if export.session_folder_mode not in {"display", "username"}:
        raise ConfigError("export.session_folder_mode 只能是 display 或 username")
    if export.timestamp_format not in {"local", "iso"}:
        raise ConfigError("export.timestamp_format 只能是 local 或 iso")

    weclone = WeCloneSettings(
        project_dir=str(weclone_raw.get("project_dir") or "vendor/WeClone"),
        config_path=str(weclone_raw.get("config_path") or "settings.jsonc"),
        python=str(weclone_raw.get("python") or ""),
        auto_make_dataset=_as_bool(weclone_raw.get("auto_make_dataset"), False),
    )

    server = ServerSettings(
        host=str(server_raw.get("host") or "127.0.0.1"),
        port=int(server_raw.get("port", 5175) or 5175),
        inference=_as_bool(server_raw.get("inference"), True),
        open_browser=_as_bool(server_raw.get("open_browser"), True),
    )

    watch = WatchSettings(
        debounce_seconds=float(watch_raw.get("debounce_seconds", 20) or 20),
        sessions=_as_list(watch_raw.get("sessions")),
    )

    return Settings(
        weflow=weflow,
        export=export,
        weclone=weclone,
        server=server,
        watch=watch,
        root=root_dir,
        source=source,
    )
