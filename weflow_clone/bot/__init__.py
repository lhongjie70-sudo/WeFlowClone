"""内置机器人：只用 API Key，不需要显卡、不需要微调。

组成：

* ``config``    —— 机器人自己的配置（API 地址 / Key / 模型 / 采样参数）
* ``persona``   —— 从聊天记录里提炼「你的说话方式」，拼成 system prompt
* ``retriever`` —— 用聊天记录做轻量检索，给模型几句话当参考
* ``llm``       —— 任意 OpenAI 兼容接口的客户端（支持流式）
* ``server``    —— 本地 Web 服务：聊天界面 + OpenAI 兼容 ``/v1``
"""

from __future__ import annotations

from .config import BotConfig, load_bot_config, save_bot_config
from .persona import Persona, build_persona, load_persona
from .retriever import ExampleIndex

__all__ = [
    "BotConfig",
    "load_bot_config",
    "save_bot_config",
    "Persona",
    "build_persona",
    "load_persona",
    "ExampleIndex",
]
