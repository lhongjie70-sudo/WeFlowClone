"""初始化：生成两份配置 + 目录骨架。

* ``weflow.jsonc``（项目根）—— 本项目的配置
* ``vendor/WeClone/settings.jsonc`` —— WeClone 官方位置，数据路径写成绝对路径

WeClone 的 ``_check_versions()`` 会去找「包目录上一级 / settings.jsonc」，
所以配置文件必须落在 WeClone 源码根；``wfc init`` 负责把它和
``weflow.jsonc`` 的路径对齐，用户只改 ``weflow.jsonc`` 即可。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..config import load_jsonc
from ..errors import ConfigError
from ..paths import project_root

WEFLOW_TEMPLATE = r"""{
  // ===================================================================
  // WeFlowClone 配置
  // 修改后不需要重启任何服务，下一次运行 wfc 命令即生效。
  // ===================================================================
  "version": "1.0.0",

  // ---------------- WeFlow 连接 ----------------
  "weflow": {
    // WeFlow → 设置 → API 服务 里的地址与端口
    "base_url": "http://127.0.0.1:5031",
    // WeFlow → 设置 → API 服务 里生成的 Access Token（留空则读环境变量 WEFLOW_TOKEN）
    "access_token": "",
    "timeout_seconds": 30,
    "retries": 2,
    // 单页拉取条数，WeFlow 上限 10000
    "page_size": 1000,
    // 只拉这些会话（填会话显示名或 wxid）；留空 = 除 exclude 外全部
    "include": [],
    "exclude": [],
    // 是否把群聊也拉进来（WeClone 官方建议先用私聊，群聊噪声大）
    "include_groups": false,
    // 最多拉几个会话，0 = 不限
    "max_sessions": 0
  },

  // ---------------- 导出 / 数据集 ----------------
  "export": {
    // 所有产物都在这个目录下：dataset/csv、dataset/media、res_csv、model_output
    "data_dir": "data",
    // 会话文件夹命名：display（展示名，可读）或 username（wxid，稳定）
    "session_folder_mode": "display",
    // CreateTime 写法：local（本机时区，推荐）或 iso（UTC）
    "timestamp_format": "local",
    // 「我」在 talker 列里显示成什么
    "self_name": "我",
    // 单个 CSV 最多多少行，超出自动分片 <会话>_<起>_<止>.csv
    "max_messages_per_file": 20000,
    // 写进 CSV 的消息类型：text/image/file 是 WeClone 的主力；
    // 加 voice/video/sticker/location 可以保留「切分点」，但它们不会进入训练文本
    "include_types": ["text", "image", "file"],
    "media": {
      // 是否把媒体文件落到 data/dataset/media/images
      // （WeClone 的多模态微调只认 images，其它类型默认不下载以省时间）
      "download": true,
      "images": true,
      "voices": false,
      "videos": false,
      "emojis": false,
      // 单个媒体文件上限，超过就丢弃（MB）
      "max_mb": 20
    }
  },

  // ---------------- WeClone ----------------
  "weclone": {
    // WeClone 源码位置（相对项目根）
    "project_dir": "vendor/WeClone",
    // WeClone settings.jsonc 相对 WeClone 源码目录的位置
    "config_path": "settings.jsonc",
    // 指定解释器；留空 = 自动找 <项目根>/.venv 再退回当前解释器
    "python": "",
    // pull 完是否立刻跑 weclone-cli make-dataset（需要装好 WeClone 依赖）
    "auto_make_dataset": false
  },

  // ---------------- 对话服务 ----------------
  "server": {
    "host": "127.0.0.1",
    "port": 5175,
    // 是否加载模型提供 /v1 推理接口（需要权重与显存）
    "inference": true,
    "open_browser": true
  },

  // ---------------- 实时同步 ----------------
  "watch": {
    // 收到新消息后等多久再增量拉取，避免每条消息都打一次 API
    "debounce_seconds": 20,
    // 只监听这些会话，留空 = 全部
    "sessions": []
  }
}
"""


def _abs(root: Path, relative: str) -> str:
    return str((root / relative).resolve())


def write_weflow_config(root: Path, *, force: bool = False) -> Path:
    target = root / "weflow.jsonc"
    if target.exists() and not force:
        return target
    target.write_text(WEFLOW_TEMPLATE, encoding="utf-8")
    return target


def build_weclone_settings(root: Path, weclone_dir: Path, *, storage_mode: str = "plaintext") -> dict[str, Any]:
    """以 WeClone 官方模板为底，改成本项目的数据布局。"""
    template_path = weclone_dir / "settings.template.jsonc"
    if template_path.is_file():
        settings = load_jsonc(template_path)
    else:  # 模板被删时的最小可用兜底
        settings = {
            "version": "0.4.01",
            "common_args": {},
            "cli_args": {},
            "make_dataset_args": {},
            "train_sft_args": {},
            "infer_args": {},
        }

    data = root / "data"
    dataset_dir = data / "res_csv" / "sft"

    settings.setdefault("security_args", {})["storage_mode"] = storage_mode

    common = settings.setdefault("common_args", {})
    common["model_name_or_path"] = str(data / "models" / "Qwen2.5-7B-Instruct")
    common["adapter_name_or_path"] = str(data / "model_output")
    common["media_dir"] = str(data / "dataset" / "media")
    common.setdefault("template", "qwen")
    common.setdefault("default_system", "请你扮演一名人类，不要说自己是人工智能")
    common.setdefault("finetuning_type", "lora")
    common.setdefault("enable_thinking", False)
    common.setdefault("trust_remote_code", True)

    cli_args = settings.setdefault("cli_args", {})
    cli_args.setdefault("full_log", False)
    cli_args.setdefault("log_level", "INFO")

    make_args = settings.setdefault("make_dataset_args", {})
    make_args["platform"] = "chat"
    make_args.setdefault("language", "zh")
    make_args["csv_folder"] = str(data / "dataset" / "csv")

    train_args = settings.setdefault("train_sft_args", {})
    train_args.setdefault("stage", "sft")
    train_args.setdefault("dataset", "chat-sft")
    train_args["dataset_dir"] = str(dataset_dir)

    for key in ("train_pt_args",):
        if isinstance(settings.get(key), dict):
            settings[key]["dataset_dir"] = str(dataset_dir)

    return settings


def write_weclone_settings(
    root: Path, weclone_dir: Path, *, force: bool = False, storage_mode: str = "plaintext"
) -> Path:
    target = weclone_dir / "settings.jsonc"
    if target.exists() and not force:
        return target
    settings = build_weclone_settings(root, weclone_dir, storage_mode=storage_mode)
    with target.open("w", encoding="utf-8") as handle:
        handle.write("// 由 `wfc init` 生成。数据路径已固定为绝对路径，勿手动改相对路径。\n")
        handle.write(json.dumps(settings, ensure_ascii=False, indent=4))
        handle.write("\n")
    return target


def ensure_dirs(root: Path) -> list[Path]:
    data = root / "data"
    created: list[Path] = []
    for relative in (
        "data/dataset/csv",
        "data/dataset/media/images",
        "data/dataset/media/voices",
        "data/dataset/media/videos",
        "data/dataset/media/emojis",
        "data/res_csv/sft",
        "data/model_output",
        "data/models",
        "logs",
    ):
        path = root / relative
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return created


def init_project(
    root: Path | None = None, *, force: bool = False, storage_mode: str = "plaintext"
) -> dict[str, Any]:
    root = root or project_root()
    root.mkdir(parents=True, exist_ok=True)

    # 1. 先把 WeClone 源码准备好（如果 vendor 里没有，尝试用同级目录）
    weclone_dir = root / "vendor" / "WeClone"
    if not (weclone_dir / "pyproject.toml").is_file():
        sibling = root.parent / "WeClone"
        if (sibling / "pyproject.toml").is_file():
            raise ConfigError(
                f"缺少 WeClone 源码：{weclone_dir}\n"
                f"检测到 {sibling}，请把它移动到 vendor/WeClone 或重新 clone。"
            )
        raise ConfigError(
            f"缺少 WeClone 源码：{weclone_dir}\n"
            "请把 WeClone 克隆到 vendor/WeClone：\n"
            "  git clone https://github.com/xming521/WeClone.git vendor/WeClone"
        )

    created = ensure_dirs(root)
    weflow_config = write_weflow_config(root, force=force)
    settings_path = write_weclone_settings(root, weclone_dir, force=force, storage_mode=storage_mode)

    return {
        "root": root,
        "weflow_config": weflow_config,
        "weclone_settings": settings_path,
        "created_dirs": created,
        "storage_mode": storage_mode,
    }


def copy_weclone_sources(source: Path, target: Path) -> None:
    """把 WeClone 源码复制进 vendor/（安装脚本用）。"""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def ensure_runtime(root: Path | None = None) -> Path:
    """打包版首次运行：只保证「能启动机器人」需要的东西。

    与 ``init_project`` 的区别：不要求 ``vendor/WeClone`` 存在
    （下载即用的用户只想要机器人，不一定要训练）。
    """
    root = root or project_root()
    root.mkdir(parents=True, exist_ok=True)
    ensure_dirs(root)
    write_weflow_config(root, force=False)
    return root
