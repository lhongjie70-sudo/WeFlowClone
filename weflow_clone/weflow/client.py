"""WeFlow HTTP API 客户端（纯标准库）。

对应 WeFlow 的「设置 → API 服务」，默认监听 ``127.0.0.1:5031``：

* ``/health``、``/api/v1/health``  健康检查（不需要 Token）
* ``/api/v1/sessions``             会话列表
* ``/api/v1/messages``             会话消息（分页）
* ``/api/v1/contacts``             联系人
* ``/api/v1/group-members``        群成员
* ``/api/v1/media/<path>``         媒体文件
* ``/api/v1/push/messages``        SSE 实时推送

这里只做「薄客户端」：解析 JSON、翻页、下载媒体、透传错误。
所有业务语义（怎么变成训练数据）在 ``mapping.py`` / ``exporter.py``。
"""

from __future__ import annotations

import json
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..errors import WeFlowApiError, WeFlowAuthError, WeFlowUnavailable
from .schema import Contact, GroupMember, Message, MessagePage, PushEvent, Session

USER_AGENT = "WeFlowClone/1.0 (+local bridge)"


@dataclass(slots=True)
class ClientOptions:
    base_url: str = "http://127.0.0.1:5031"
    access_token: str = ""
    timeout: float = 30.0
    retries: int = 2
    page_size: int = 1000


class WeFlowClient:
    def __init__(self, options: ClientOptions | None = None, **kwargs: Any) -> None:
        opts = options or ClientOptions()
        for key, value in kwargs.items():
            setattr(opts, key, value)
        self.options = opts
        self.base_url = opts.base_url.rstrip("/")
        self._token = opts.access_token.strip()

    # ------------------------------------------------------------------ #
    # 基础请求
    # ------------------------------------------------------------------ #
    @property
    def token(self) -> str:
        return self._token

    def set_token(self, token: str) -> None:
        self._token = (token or "").strip()

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean, doseq=True)}"
        return url

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        raw_response: bool = False,
        stream: bool = False,
    ) -> Any:
        url = self._build_url(path, params)
        last_error: Exception | None = None

        for attempt in range(self.options.retries + 1):
            request = urllib.request.Request(url, headers=self._headers(), method="GET")
            try:
                response = urllib.request.urlopen(request, timeout=self.options.timeout)
            except urllib.error.HTTPError as exc:
                body = self._read_error_body(exc)
                if exc.code in (401, 403):
                    raise WeFlowAuthError(
                        "WeFlow 拒绝了请求：Access Token 缺失或错误。\n"
                        "请打开 WeFlow → 设置 → API 服务，复制 Token 填到 weflow.jsonc 的 "
                        "weflow.access_token（或设置环境变量 WEFLOW_TOKEN）。"
                    ) from None
                if exc.code >= 500 and attempt < self.options.retries:
                    last_error = exc
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise WeFlowApiError(
                    f"WeFlow 返回 HTTP {exc.code}：{body or exc.reason}",
                    status=exc.code,
                    path=path,
                ) from None
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < self.options.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise WeFlowUnavailable(
                    f"连接不上 WeFlow（{self.base_url}）：{self._hint_for(exc)}"
                ) from None
            except socket.timeout:
                last_error = TimeoutError("timeout")
                if attempt < self.options.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise WeFlowUnavailable(
                    f"WeFlow 响应超时（{self.options.timeout:g}s）。如果聊天记录很多，"
                    "可以在 weflow.jsonc 里调大 weflow.timeout_seconds。"
                ) from None

            try:
                if stream:
                    return response
                if raw_response:
                    return response.read()
                return self._parse_json(response, path)
            finally:
                if not stream:
                    response.close()

        raise WeFlowUnavailable(f"连接不上 WeFlow（{self.base_url}）：{last_error}")

    def _hint_for(self, exc: urllib.error.URLError) -> str:
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        guidance = (
            "请确认 WeFlow 正在运行，并在「设置 → API 服务」中启用 API 服务；"
            f"weflow.base_url 当前是 {self.base_url}。"
        )
        if "refused" in text.lower() or "10061" in text or "actively refused" in text.lower():
            return "连接被拒绝。" + guidance
        return f"{text}。{guidance}"

    @staticmethod
    def _read_error_body(exc: urllib.error.HTTPError) -> str:
        try:
            payload = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - 极少触发
            return ""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return payload[:300]
        if isinstance(data, dict):
            return str(data.get("error") or data.get("message") or payload[:300])
        return payload[:300]

    @staticmethod
    def _parse_json(response: Any, path: str) -> Any:
        payload = response.read()
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeFlowApiError(f"WeFlow 返回的不是 JSON（{path}）：{exc}") from None

    # ------------------------------------------------------------------ #
    # 业务接口
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        """健康检查，不需要 Token。"""
        return self._request("/health")

    def sessions(self, keyword: str | None = None, limit: int = 100) -> list[Session]:
        data = self._request("/api/v1/sessions", {"keyword": keyword, "limit": limit})
        items = data.get("sessions") if isinstance(data, dict) else None
        return [Session.from_api(item) for item in (items or []) if isinstance(item, dict)]

    def contacts(self, keyword: str | None = None, limit: int = 100) -> list[Contact]:
        data = self._request("/api/v1/contacts", {"keyword": keyword, "limit": limit})
        items = data.get("contacts") if isinstance(data, dict) else None
        return [Contact.from_api(item) for item in (items or []) if isinstance(item, dict)]

    def group_members(self, chatroom_id: str, with_counts: bool = False) -> list[GroupMember]:
        data = self._request(
            "/api/v1/group-members",
            {"chatroomId": chatroom_id, "includeMessageCounts": 1 if with_counts else None},
        )
        items = data.get("members") if isinstance(data, dict) else None
        return [GroupMember.from_api(item) for item in (items or []) if isinstance(item, dict)]

    def messages(
        self,
        talker: str,
        *,
        limit: int = 100,
        offset: int = 0,
        start: str | int | None = None,
        end: str | int | None = None,
        chatlab: bool = False,
        media: bool = False,
    ) -> MessagePage:
        params = {
            "talker": talker,
            "limit": limit,
            "offset": offset,
            "start": start,
            "end": end,
            "chatlab": 1 if chatlab else None,
            "media": 1 if media else None,
        }
        data = self._request("/api/v1/messages", params)
        if not isinstance(data, dict):
            raise WeFlowApiError("WeFlow /api/v1/messages 返回结构异常")
        return MessagePage.from_api(data)

    def iter_messages(
        self,
        talker: str,
        *,
        page_size: int | None = None,
        start: str | int | None = None,
        end: str | int | None = None,
        media: bool = False,
        max_messages: int = 0,
    ) -> Iterator[Message]:
        """按 offset 翻页直到 ``hasMore`` 为假，按 serverId 去重。"""
        size = max(1, min(10000, page_size or self.options.page_size))
        offset = 0
        seen: set[str] = set()
        yielded = 0
        while True:
            page = self.messages(
                talker, limit=size, offset=offset, start=start, end=end, media=media
            )
            if not page.messages:
                return
            new_count = 0
            for message in page.messages:
                key = message.server_id or f"{message.local_id}:{message.create_time}:{message.sender}"
                if key in seen:
                    continue
                seen.add(key)
                new_count += 1
                yielded += 1
                yield message
                if max_messages and yielded >= max_messages:
                    return
            offset += len(page.messages)
            if not page.has_more or new_count == 0:
                return

    # ------------------------------------------------------------------ #
    # 媒体
    # ------------------------------------------------------------------ #
    def copy_or_download_media(self, message: Message, destination_dir: Path) -> Path | None:
        """优先复用 WeFlow 已导出的本地文件，其次走 HTTP 下载。"""
        name = message.media_basename or f"media_{message.server_id or message.local_id}"
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / name

        local = message.media_local_path
        if local:
            local_path = Path(local)
            if local_path.is_file():
                if target.exists() and target.stat().st_size == local_path.stat().st_size:
                    return target
                try:
                    shutil.copyfile(local_path, target)
                    return target
                except OSError:
                    pass  # 跨盘 / 权限问题，退回 HTTP

        if message.media_url:
            raw = self._request(message.media_url, raw_response=True)
            if not raw:
                return None
            target.write_bytes(raw)
            return target

        if local:
            return None
        return None

    def download_media(self, relative_path: str, destination: Path) -> Path:
        path = relative_path if relative_path.startswith("/api/") else f"/api/v1/media/{relative_path.lstrip('/')}"
        raw = self._request(path, raw_response=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return destination

    # ------------------------------------------------------------------ #
    # SSE
    # ------------------------------------------------------------------ #
    def stream_events(self, idle_timeout: float = 0.0) -> Iterator[PushEvent]:
        """订阅 WeFlow 的实时推送（需要同时开启「主动推送」）。"""
        params: dict[str, Any] = {}
        if self._token:
            params["access_token"] = self._token
        url = self._build_url("/api/v1/push/messages", params)
        request = urllib.request.Request(
            url, headers=self._headers(accept="text/event-stream"), method="GET"
        )
        try:
            response = urllib.request.urlopen(request, timeout=idle_timeout or None)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise WeFlowAuthError("SSE 订阅被拒绝：Token 无效。请检查 weflow.access_token。") from None
            raise WeFlowApiError(f"SSE 订阅失败：HTTP {exc.code}", status=exc.code) from None
        except urllib.error.URLError as exc:
            raise WeFlowUnavailable(f"SSE 订阅失败：{self._hint_for(exc)}") from None

        event_name = ""
        data_lines: list[str] = []
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            data = {}
                        if isinstance(data, dict):
                            yield PushEvent.from_api(event_name, data)
                    event_name, data_lines = "", []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
        finally:
            response.close()
