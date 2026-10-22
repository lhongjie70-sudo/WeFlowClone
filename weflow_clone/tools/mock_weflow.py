"""模拟 WeFlow HTTP API（仅标准库）。

用途：
* ``wfc demo`` —— 没有微信 / 没装 WeFlow 的机器上演示完整数据链路
* 自动化测试 —— 断言客户端的分页、鉴权、媒体下载、ChatLab 转换

它**不是** WeFlow，只是按 ``docs/HTTP-API.md`` 的契约回放数据。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .fixtures import FixtureData, build_fixture, to_chatlab

DEFAULT_TOKEN = "demo-token"


class MockWeFlowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockWeFlow/1.0"

    # ------------------------------------------------------------------ #
    @property
    def data(self) -> FixtureData:
        return self.server.fixture  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 静默日志
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        body: dict[str, Any] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {}
            query.update({k: str(v) for k, v in body.items() if not isinstance(v, (list, dict))})

        self.server.calls.append((method, path, dict(query)))  # type: ignore[attr-defined]

        if path in {"/health", "/api/v1/health"}:
            return self._json({"status": "ok"})

        if path.startswith("/api/v1/push/messages"):
            return self._sse()

        if not self._authorized(query):
            return self._json({"error": "unauthorized"}, status=401)

        if path == "/api/v1/sessions":
            return self._sessions(query)
        if path == "/api/v1/contacts":
            return self._json({"success": True, "count": len(self.data.contacts), "contacts": self.data.contacts})
        if path == "/api/v1/group-members":
            return self._group_members(query)
        if path == "/api/v1/messages":
            return self._messages(query)
        if path.startswith("/api/v1/sessions/") and path.endswith("/messages"):
            talker = urllib.parse.unquote(path[len("/api/v1/sessions/") : -len("/messages")])
            return self._messages({**query, "talker": talker, "chatlab": "1"})
        if path.startswith("/api/v1/media/"):
            return self._media(path[len("/api/v1/media/") :])
        return self._json({"error": "not found", "path": path}, status=404)

    # ------------------------------------------------------------------ #
    def _authorized(self, query: dict[str, str]) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            header_token = header[7:].strip()
        else:
            header_token = ""
        return self.token in {header_token, query.get("access_token", "")}

    def _sessions(self, query: dict[str, str]) -> None:
        keyword = (query.get("keyword") or "").lower()
        limit = int(query.get("limit") or 100)
        items = [
            session
            for session in self.data.sessions
            if not keyword
            or keyword in session["username"].lower()
            or keyword in session["displayName"].lower()
        ][:limit]
        if query.get("format") == "chatlab":
            payload = {
                "sessions": [
                    {
                        "id": item["username"],
                        "name": item["displayName"],
                        "platform": "im",
                        "type": "group" if item["username"].endswith("@chatroom") else "private",
                        "messageCount": len(self.data.messages.get(item["username"], [])),
                        "lastMessageAt": item["lastTimestamp"],
                    }
                    for item in items
                ]
            }
            return self._json(payload)
        self._json({"success": True, "count": len(items), "sessions": items})

    def _group_members(self, query: dict[str, str]) -> None:
        chatroom = query.get("chatroomId") or query.get("talker") or ""
        members = self.data.members.get(chatroom, [])
        if query.get("includeMessageCounts") in {"1", "true"}:
            counts: dict[str, int] = {}
            for message in self.data.messages.get(chatroom, []):
                sender = message.get("senderUsername") or ""
                counts[sender] = counts.get(sender, 0) + 1
            members = [{**member, "messageCount": counts.get(member["wxid"], 0)} for member in members]
        self._json(
            {
                "success": True,
                "chatroomId": chatroom,
                "count": len(members),
                "fromCache": False,
                "updatedAt": int(time.time() * 1000),
                "members": members,
            }
        )

    def _messages(self, query: dict[str, str]) -> None:
        talker = query.get("talker") or ""
        if not self.data.session(talker):
            return self._json({"error": f"unknown talker: {talker}"}, status=404)

        messages = list(self.data.messages.get(talker, []))
        start = query.get("start")
        end = query.get("end")
        messages = [m for m in messages if _in_range(m["createTime"], start, end)]
        messages.sort(key=lambda m: m["createTime"])

        limit = max(1, min(10000, int(query.get("limit") or 100)))
        offset = max(0, int(query.get("offset") or 0))
        page = messages[offset : offset + limit]
        has_more = offset + limit < len(messages)

        wants_chatlab = query.get("chatlab") in {"1", "true"} or query.get("format") == "chatlab"
        if wants_chatlab:
            bundle = to_chatlab(self.data, talker)
            bundle["messages"] = bundle["messages"][offset : offset + limit]
            bundle["sync"] = {
                "hasMore": has_more,
                "nextSince": 0,
                "nextOffset": offset + len(page),
                "watermark": messages[-1]["createTime"] if messages else 0,
            }
            bundle["hasMore"] = has_more
            return self._json(bundle)

        payload: dict[str, Any] = {
            "success": True,
            "talker": talker,
            "count": len(page),
            "hasMore": has_more,
            "messages": page,
        }
        if query.get("media") in {"1", "true"}:
            payload["media"] = {
                "enabled": True,
                "exportPath": str(self.data.media_root or ""),
                "count": sum(1 for m in page if m.get("mediaLocalPath")),
            }
        self._json(payload)

    def _media(self, relative: str) -> None:
        name = urllib.parse.unquote(relative).replace("\\", "/").rsplit("/", 1)[-1]
        root = self.data.media_root
        target = (root / name) if root else None
        if not target or not target.is_file():
            return self._json({"error": "Media not found"}, status=404)
        payload = target.read_bytes()
        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".wav": "audio/wav",
            ".mp4": "video/mp4",
        }.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [
            {
                "event": "message.new",
                "sessionId": self.data.sessions[0]["username"],
                "sessionType": "other",
                "rawid": "7300000000000099999",
                "sourceName": self.data.sessions[0]["displayName"],
                "content": "演示推送：我到家了",
                "timestamp": int(time.time()),
            }
        ]
        try:
            for payload in events:
                self.wfile.write(f"event: {payload['event']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------ #
    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _in_range(timestamp: int, start: str | None, end: str | None) -> bool:
    if start:
        bound = _parse_bound(start)
        if bound and timestamp < bound:
            return False
    if end:
        bound = _parse_bound(end, end_of_day=True)
        if bound and timestamp > bound:
            return False
    return True


def _parse_bound(value: str, end_of_day: bool = False) -> int | None:
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        from datetime import datetime

        parsed = datetime.strptime(text, "%Y%m%d")
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return int(parsed.timestamp())
    try:
        return int(text)
    except ValueError:
        return None


class MockWeFlowServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], fixture: FixtureData, token: str = DEFAULT_TOKEN) -> None:
        super().__init__(address, MockWeFlowHandler)
        self.fixture = fixture
        self.token = token
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.verbose = False

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def requests_for(self, needle: str) -> list[tuple[str, str, dict[str, str]]]:
        return [call for call in self.calls if needle in call[1]]


def start_server(
    media_root: Path,
    *,
    port: int = 0,
    token: str = DEFAULT_TOKEN,
) -> tuple[MockWeFlowServer, threading.Thread]:
    fixture = build_fixture(media_root)
    server = MockWeFlowServer(("127.0.0.1", port), fixture, token=token)
    thread = threading.Thread(target=server.serve_forever, name="mock-weflow", daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    """命令行入口：python -m weflow_clone.tools.mock_weflow --port 5031"""
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="启动模拟的 WeFlow API 服务")
    parser.add_argument("--port", type=int, default=5031)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--media-dir", default="")
    args = parser.parse_args(argv)

    media_root = Path(args.media_dir) if args.media_dir else Path(tempfile.mkdtemp(prefix="wfc-mock-media-"))
    server, _thread = start_server(media_root, port=args.port, token=args.token)
    print(f"模拟 WeFlow API 已启动：{server.base_url}（Token: {args.token}）")
    print(f"媒体目录：{media_root}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
