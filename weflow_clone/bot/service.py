"""把「人设 + 检索 + 模型」拼成一次回复。

一次请求的上下文结构：

```
system:   人设（说话习惯 + 行为规则）
          + 本次检索到的几条历史真实对话
messages: 最近若干轮对话
user:     这句话
```

这样即使是通用大模型，也能说出「像你」的短句，而不是客服腔长文。
"""

from __future__ import annotations

import threading
from typing import Iterator

from ..config import Settings
from .config import BotConfig, load_bot_config
from .llm import LLMClient
from .persona import Persona, ensure_persona
from .retriever import Example, ExampleIndex

MAX_HISTORY_TURNS = 12
MAX_HISTORY_CHARS = 2000


class ChatService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._persona: Persona | None = None
        self._index: ExampleIndex | None = None

    # ------------------------------------------------------------------ #
    @property
    def data_dir(self):
        return self.settings.data_dir

    def config(self) -> BotConfig:
        return load_bot_config(self.data_dir)

    def persona(self, *, force: bool = False) -> Persona:
        with self._lock:
            if force or self._persona is None or not self._persona.prompt:
                self._persona = ensure_persona(self.settings, force=force)
            return self._persona

    def index(self) -> ExampleIndex:
        with self._lock:
            if self._index is None:
                self._index = ExampleIndex.from_settings(self.settings)
            return self._index

    def reindex(self) -> dict:
        persona = self.persona(force=True)
        with self._lock:
            self._index = ExampleIndex.from_settings(self.settings)
        return {
            "messages": persona.message_count,
            "exemplars": len(persona.exemplars),
            "examples": self.index().size,
            "sessions": len(persona.sessions),
        }

    # ------------------------------------------------------------------ #
    def _system_prompt(self, config: BotConfig, message: str) -> str:
        persona = self.persona() if config.use_persona else Persona()
        blocks: list[str] = []
        if persona.prompt:
            blocks.append(persona.prompt)
        else:
            blocks.append(
                "你在微信里扮演「我」——一个真实的人。回复要口语、简短，"
                "不要客服腔，不要提到自己是 AI。"
            )

        if config.retrieve_top_k > 0:
            examples: list[Example] = self.index().search(message, top_k=config.retrieve_top_k)
            if examples:
                lines = ["", "【参考：你以前在类似场景下的真实回答（照这个语气，不要照抄）】"]
                for example in examples:
                    lines.append(f"对方：{example.question}")
                    lines.append(f"我：{example.answer}")
                blocks.append("\n".join(lines))

        if config.extra_system.strip():
            blocks.append("\n" + config.extra_system.strip())
        return "\n".join(blocks)

    def build_messages(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        *,
        config: BotConfig | None = None,
    ) -> list[dict[str, str]]:
        config = config or self.config()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt(config, message)}
        ]
        for item in (history or [])[-MAX_HISTORY_TURNS * 2 :]:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content[:MAX_HISTORY_CHARS]})
        messages.append({"role": "user", "content": message})
        return messages

    # ------------------------------------------------------------------ #
    def stream_reply(self, message: str, history: list[dict[str, str]] | None = None) -> Iterator[str]:
        config = self.config()
        client = LLMClient(config)
        yield from client.chat_stream(self.build_messages(message, history, config=config))

    def reply(self, message: str, history: list[dict[str, str]] | None = None) -> str:
        config = self.config()
        client = LLMClient(config)
        return client.chat(self.build_messages(message, history, config=config))

    def health(self) -> dict:
        config = self.config()
        persona = self.persona()
        return {
            "configured": config.configured,
            "model": config.model,
            "api_base": config.api_base,
            "persona_messages": persona.message_count,
            "persona_ready": bool(persona.prompt),
            "sessions": persona.sessions,
            "examples": self.index().size if persona.prompt else 0,
        }
