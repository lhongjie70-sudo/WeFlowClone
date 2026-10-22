"""机器人配置：存在 ``<data>/bot/config.json``，界面上可以直接改。

优先级（从高到低）：

1. 环境变量（``WFC_API_KEY`` / ``OPENAI_API_KEY``、``WFC_API_BASE``、``WFC_MODEL``）
2. 配置文件
3. 内置默认值（空 Key，需要用户填）

这样既能「界面里粘一次」，也能在服务器上纯用环境变量部署。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


@dataclass(slots=True)
class BotConfig:
    api_base: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    temperature: float = 0.75
    max_tokens: int = 512
    top_p: float = 0.9
    # 从聊天记录里取几条最相似的对话当"参考"，0 = 关闭
    retrieve_top_k: int = 3
    # 是否把「说话习惯」写进 system prompt
    use_persona: bool = True
    # 机器人自称（留空则用数据里推断出的名字）
    bot_name: str = ""
    # 额外的系统提示词（用户自定义补充）
    extra_system: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    def sanitized(self) -> dict[str, Any]:
        """给前端看的版本：Key 打码。"""
        data = asdict(self)
        key = self.api_key or ""
        data["api_key"] = f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("已配置" if key else "")
        data["configured"] = self.configured
        return data


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def config_path(data_dir: Path) -> Path:
    return data_dir / "bot" / "config.json"


def load_bot_config(data_dir: Path) -> BotConfig:
    path = config_path(data_dir)
    config = BotConfig()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if hasattr(config, key) and value is not None:
                    setattr(config, key, value)

    # 环境变量覆盖（部署时不必把 Key 写进文件）
    config.api_key = _env("WFC_API_KEY") or _env("OPENAI_API_KEY") or config.api_key
    config.api_base = _env("WFC_API_BASE") or config.api_base
    config.model = _env("WFC_MODEL") or config.model
    return config


def save_bot_config(data_dir: Path, updates: dict[str, Any]) -> BotConfig:
    config = load_bot_config(data_dir)
    for key, value in updates.items():
        if not hasattr(config, key):
            continue
        if key in {"temperature", "top_p"}:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            value = min(2.0, max(0.0, value))
        elif key in {"max_tokens", "retrieve_top_k"}:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            value = max(0, value)
        elif key == "use_persona":
            value = bool(value)
        elif isinstance(value, str):
            value = value.strip()
        setattr(config, key, value)

    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:  # 只给当前用户读写（含 API Key）
        path.chmod(0o600)
    except OSError:
        pass
    return config
