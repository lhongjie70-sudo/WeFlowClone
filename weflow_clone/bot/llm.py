"""OpenAI 兼容客户端（标准库实现，支持流式）。

内置机器人不绑定任何厂商：只要对方提供 ``/chat/completions``，填 base_url + key + model
就能用。DeepSeek、Moonshot、通义、OpenAI、本地 Ollama / vLLM / LM Studio 都行。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Iterator

from ..errors import WeFlowCloneError
from .config import BotConfig

USER_AGENT = "WeFlowClone-Bot/1.0"


class LLMError(WeFlowCloneError):
    """模型接口调用失败（消息已翻译成可操作的中文）。"""


def _endpoint(base: str, path: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        raise LLMError("还没配置 API 地址（api_base），例如 https://api.deepseek.com/v1")
    if base.endswith(path):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return f"{base}{path}"


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # 多模态数组形式
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(parts)
    return str(content or "")


class LLMClient:
    def __init__(self, config: BotConfig, *, timeout: float = 120.0, retries: int = 1) -> None:
        self.config = config
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _payload(self, messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
            "stream": stream,
        }

    def _open(self, payload: dict[str, Any], stream: bool):
        if not self.config.configured:
            raise LLMError("还没填 API Key。打开 http://127.0.0.1:8765 的「设置」，或者设置环境变量 WFC_API_KEY。")
        url = _endpoint(self.config.api_base, "/chat/completions")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict):
                    detail = str(error.get("message") or error)
                elif error:
                    detail = str(error)
                else:
                    detail = raw[:300]
            except Exception:  # noqa: BLE001 - 错误体解析失败不影响主错误
                detail = ""
            raise LLMError(self._explain(exc.code, detail, url)) from None
        except urllib.error.URLError as exc:
            raise LLMError(
                f"连不上模型接口 {url}：{getattr(exc, 'reason', exc)}\n"
                "检查网络、代理，或 api_base 是否写对（多数服务需要带 /v1）。"
            ) from None
        except socket.timeout:
            raise LLMError(f"模型接口超时（{self.timeout:g}s）。可以调小 max_tokens 或换更快的模型。") from None

    @staticmethod
    def _explain(status: int, detail: str, url: str) -> str:
        hints = {
            400: "请求被拒绝：多半是模型名不存在，或 messages 格式不对。",
            401: "API Key 无效或已过期。",
            403: "这个 Key 没有访问该模型的权限。",
            404: "接口地址不对：确认 api_base 是否要带 /v1。",
            429: "触发限流或余额不足，稍后再试或换 Key。",
            500: "模型服务内部错误，稍后重试。",
            502: "网关错误：代理或 api_base 配置有问题。",
            503: "模型服务暂时不可用。",
        }
        hint = hints.get(status, "")
        suffix = f"（{detail}）" if detail else ""
        return f"模型接口返回 HTTP {status}：{hint}{suffix}\n地址：{url}"

    # ------------------------------------------------------------------ #
    def chat(self, messages: list[dict[str, str]]) -> str:
        """非流式，返回完整回复。"""
        response = self._open(self._payload(messages, stream=False), stream=False)
        try:
            raw = response.read()
        finally:
            response.close()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMError(f"模型返回的不是 JSON：{exc}") from None
        text = _message_text(data)
        if not text:
            raise LLMError("模型返回了空内容，可能是提示词被安全策略拦截。")
        return text

    def chat_stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        """流式，逐段产出文本增量。"""
        response = self._open(self._payload(messages, stream=True), stream=True)
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, list):
                    piece = "".join(part.get("text", "") for part in piece if isinstance(part, dict))
                if piece:
                    yield str(piece)
        except socket.timeout:
            raise LLMError("流式读取超时，已中断。") from None
        finally:
            response.close()

    # ------------------------------------------------------------------ #
    def test_connection(self) -> str:
        """用一条最短的请求验证 Key / 地址 / 模型三件事。"""
        text = self.chat([{"role": "user", "content": "回复两个字：在的"}])
        return text.strip()
