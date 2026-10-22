"""内置机器人服务：本地网页聊天 + OpenAI 兼容接口。

为什么用标准库的 ``http.server``？
--------------------------------
打包成单文件 exe / app 时，每多一个依赖就多一份体积和一份兼容风险。
聊天服务的并发需求只是「一个人边打字边看流式输出」，``ThreadingHTTPServer`` 足够。

路由
----
``GET  /``                     聊天界面（单文件 HTML）
``GET  /api/state``            配置 + 人设状态
``POST /api/config``           保存 API Key / 模型 / 采样参数
``POST /api/test``             测试连通性（发一条最短请求）
``POST /api/reindex``          重新扫描聊天记录，重建人设与检索索引
``POST /api/chat``             流式回复（SSE）
``POST /v1/chat/completions``  OpenAI 兼容（流式 / 非流式）
``GET  /v1/models``            模型列表（兼容客户端探测）
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import Settings
from .config import load_bot_config, save_bot_config
from .llm import LLMClient, LLMError
from .service import ChatService

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
UI_FILENAME = "index.html"


def web_dir() -> Path:
    """兼容源码运行与 PyInstaller 打包（onefile 会解压到 _MEIPASS）。"""
    candidates = [
        Path(__file__).parent / "web",
        Path(getattr(sys, "_MEIPASS", "")) / "weflow_clone" / "bot" / "web",
        Path(getattr(sys, "_MEIPASS", "")) / "web",
    ]
    for candidate in candidates:
        if (candidate / UI_FILENAME).is_file():
            return candidate
    return candidates[0]


class BotServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Settings) -> None:
        super().__init__(address, BotHandler)
        self.settings = settings
        self.service = ChatService(settings)
        self.started_at = time.time()
        self.verbose = False

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class BotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WeFlowClone-Bot/1.0"

    @property
    def bot(self) -> BotServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 对齐基类签名
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ #
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path in {"/", "/index.html", "/chat"}:
                return self._serve_ui()
            if path == "/api/state":
                return self._state()
            if path == "/v1/models":
                return self._models()
            if path == "/health":
                return self._json({"status": "ok"})
            return self._json({"error": "not found", "path": path}, status=404)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免连接被直接断开
            return self._json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            body = self._read_json()
            if path == "/api/config":
                return self._save_config(body)
            if path == "/api/test":
                return self._test(body)
            if path == "/api/reindex":
                return self._reindex()
            if path == "/api/chat":
                return self._chat(body)
            if path in {"/v1/chat/completions", "/chat/completions"}:
                return self._openai_chat(body)
            return self._json({"error": "not found", "path": path}, status=404)
        except LLMError as exc:
            return self._sse_error(str(exc)) if path == "/api/chat" else self._json(
                {"error": str(exc)}, status=502
            )
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, status=500)

    # ------------------------------------------------------------------ #
    # 基础响应
    # ------------------------------------------------------------------ #
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _serve_ui(self) -> None:
        path = web_dir() / UI_FILENAME
        if not path.is_file():
            return self._json({"error": f"界面文件缺失：{path}"}, status=500)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    # 状态 / 配置
    # ------------------------------------------------------------------ #
    def _state(self) -> None:
        config = load_bot_config(self.bot.settings.data_dir)
        service = self.bot.service
        health = service.health()
        self._json(
            {
                "ok": True,
                "config": config.sanitized(),
                "health": health,
                "data_dir": str(self.bot.settings.data_dir),
                "uptime": int(time.time() - self.bot.started_at),
            }
        )

    def _save_config(self, body: dict[str, Any]) -> None:
        allowed = {
            "api_base",
            "api_key",
            "model",
            "temperature",
            "max_tokens",
            "top_p",
            "retrieve_top_k",
            "use_persona",
            "bot_name",
            "extra_system",
        }
        updates = {key: value for key, value in body.items() if key in allowed}
        # 前端回显的是打码 Key，空值或打码值都不覆盖真实 Key
        if not str(updates.get("api_key", "")).strip() or "…" in str(updates.get("api_key", "")):
            updates.pop("api_key", None)
        config = save_bot_config(self.bot.settings.data_dir, updates)
        self._json({"ok": True, "config": config.sanitized()})

    def _test(self, body: dict[str, Any]) -> None:
        config = load_bot_config(self.bot.settings.data_dir)
        if str(body.get("api_key", "")).strip() and "…" not in str(body.get("api_key", "")):
            save_bot_config(self.bot.settings.data_dir, {"api_key": body["api_key"]})
            config = load_bot_config(self.bot.settings.data_dir)
        started = time.time()
        try:
            reply = LLMClient(config, timeout=30).test_connection()
        except LLMError as exc:
            return self._json({"ok": False, "error": str(exc)}, status=502)
        self._json(
            {
                "ok": True,
                "reply": reply,
                "model": config.model,
                "latency_ms": int((time.time() - started) * 1000),
            }
        )

    def _reindex(self) -> None:
        stats = self.bot.service.reindex()
        self._json({"ok": True, "stats": stats})

    def _models(self) -> None:
        config = load_bot_config(self.bot.settings.data_dir)
        self._json(
            {
                "object": "list",
                "data": [{"id": config.model, "object": "model", "owned_by": "weflowclone"}],
            }
        )

    # ------------------------------------------------------------------ #
    # 聊天
    # ------------------------------------------------------------------ #
    def _chat(self, body: dict[str, Any]) -> None:
        message = str(body.get("message") or "").strip()
        history = body.get("history") or []
        if not message:
            return self._json({"error": "消息为空"}, status=400)
        if not isinstance(history, list):
            history = []

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        try:
            for delta in self.bot.service.stream_reply(message, history):
                self._sse({"delta": delta})
            self._sse({"done": True})
        except LLMError as exc:
            self._sse({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return

    def _openai_chat(self, body: dict[str, Any]) -> None:
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return self._json({"error": {"message": "messages 不能为空"}}, status=400)
        user_message = ""
        history: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = item.get("content")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            content = str(content or "")
            if role == "system":
                continue
            if role in {"user", "assistant"}:
                if role == "user":
                    user_message = content
                history.append({"role": role, "content": content})
        if history:
            history = history[:-1]
        if not user_message:
            return self._json({"error": {"message": "缺少 user 消息"}}, status=400)

        config = load_bot_config(self.bot.settings.data_dir)
        client = LLMClient(config)
        built = self.bot.service.build_messages(user_message, history, config=config)
        stream = bool(body.get("stream"))
        model = str(body.get("model") or config.model)
        completion_id = f"chatcmpl-wfc-{int(time.time() * 1000)}"

        if not stream:
            try:
                text = client.chat(built)
            except LLMError as exc:
                return self._json({"error": {"message": str(exc)}}, status=502)
            return self._json(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
                    ],
                }
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        try:
            for delta in client.chat_stream(built):
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            tail = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(tail, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8"))
            self.wfile.flush()
        except LLMError as exc:
            self.wfile.write(f"data: {json.dumps({'error': {'message': str(exc)}}, ensure_ascii=False)}\n\n".encode())
        except (BrokenPipeError, ConnectionResetError):
            return

    def _sse(self, payload: dict[str, Any]) -> None:
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _sse_error(self, message: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self._sse({"error": message})


def create_server(settings: Settings, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> BotServer:
    return BotServer((host, port), settings)


def serve(
    settings: Settings,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    reindex: bool = False,
    log=print,
) -> int:
    server = create_server(settings, host, port)
    if reindex:
        log("正在重建人设与检索索引…")
        stats = server.service.reindex()
        log(f"完成：{stats['messages']} 条我的消息，{stats['examples']} 组对话样例")

    health = server.service.health()
    if not health["persona_ready"]:
        log("还没有聊天记录：先运行 wfc pull，或在界面里直接开始（会退化为通用人格）。")
    if not health["configured"]:
        log("还没配置 API Key：打开界面后在「设置」里填，或设置环境变量 WFC_API_KEY。")

    log(f"内置机器人已启动：{server.url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(server.url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n已停止")
    finally:
        server.server_close()
    return 0
