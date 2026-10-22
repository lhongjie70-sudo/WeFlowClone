"""WeFlow 数据模型。

只做一件事：把 WeFlow HTTP API 的 JSON 稳定地变成有类型的对象。
WeFlow 同时提供两种消息格式：

* 原始 JSON（``/api/v1/messages`` 默认）——字段是 ``localType`` / ``isSend`` / ``senderUsername``
* ChatLab（``chatlab=1`` 或 ``format=chatlab``）——字段是 ``type`` / ``sender`` / ``timestamp``

两种都支持，因为「API 直连」用原始格式信息最全，而「导入 WeFlow 手工导出」
可能拿到 ChatLab 文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# ChatLab 消息类型 -> WeChat localType
CHATLAB_TYPE_TO_LOCAL = {
    0: 1,    # 文本
    1: 3,    # 图片
    2: 34,   # 语音
    3: 43,   # 视频
    4: 49,   # 文件（appmsg）
    5: 47,   # 表情
    7: 49,   # 链接 / 卡片
    8: 48,   # 位置
    23: 50,  # 通话
    24: 49,  # 小程序 / 分享
    25: 49,  # 引用回复
    27: 42,  # 名片
    80: 10000,  # 系统消息
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def normalize_epoch(value: Any) -> int:
    """把秒 / 毫秒 / 微秒时间戳统一降到秒。"""
    ts = _to_int(value)
    if ts <= 0:
        return 0
    while ts > 10_000_000_000:
        ts //= 1000
    return ts


@dataclass(slots=True)
class Session:
    username: str
    display_name: str
    type: int = 0
    last_timestamp: int = 0
    unread: int = 0

    @property
    def is_group(self) -> bool:
        if "@chatroom" in self.username:
            return True
        return self.type == 2

    @property
    def kind(self) -> str:
        return "group" if self.is_group else "private"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Session":
        username = str(data.get("username") or data.get("id") or "").strip()
        display = str(data.get("displayName") or data.get("name") or username).strip()
        return cls(
            username=username,
            display_name=display or username,
            type=_to_int(data.get("type")),
            last_timestamp=normalize_epoch(data.get("lastTimestamp") or data.get("lastMessageAt")),
            unread=_to_int(data.get("unreadCount")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "displayName": self.display_name,
            "type": self.type,
            "kind": self.kind,
            "lastTimestamp": self.last_timestamp,
            "unreadCount": self.unread,
        }


@dataclass(slots=True)
class Contact:
    username: str
    display_name: str
    remark: str = ""
    nickname: str = ""
    alias: str = ""
    avatar_url: str = ""
    type: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Contact":
        username = str(data.get("username") or "").strip()
        return cls(
            username=username,
            display_name=str(data.get("displayName") or data.get("nickname") or username),
            remark=str(data.get("remark") or ""),
            nickname=str(data.get("nickname") or ""),
            alias=str(data.get("alias") or ""),
            avatar_url=str(data.get("avatarUrl") or ""),
            type=str(data.get("type") or ""),
        )


@dataclass(slots=True)
class GroupMember:
    wxid: str
    display_name: str
    group_nickname: str = ""
    remark: str = ""
    is_owner: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "GroupMember":
        wxid = str(data.get("wxid") or data.get("platformId") or "").strip()
        return cls(
            wxid=wxid,
            display_name=str(data.get("displayName") or data.get("accountName") or wxid),
            group_nickname=str(data.get("groupNickname") or ""),
            remark=str(data.get("remark") or ""),
            is_owner=bool(data.get("isOwner")),
        )


@dataclass(slots=True)
class Message:
    """一条聊天消息的归一化表示。"""

    local_id: int = 0
    server_id: str = ""
    local_type: int = 1
    create_time: int = 0  # 秒级时间戳
    is_send: bool = False
    sender: str = ""  # wxid
    sender_name: str = ""  # 展示名（可选）
    content: str = ""
    parsed_content: str = ""
    raw_content: str = ""
    media_type: str = ""
    media_file_name: str = ""
    media_local_path: str = ""
    media_url: str = ""
    reply_to: str = ""

    # -- 构造 ------------------------------------------------------------- #
    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "Message":
        return cls(
            local_id=_to_int(data.get("localId")),
            server_id=str(data.get("serverId") or data.get("serverIdRaw") or ""),
            local_type=_to_int(data.get("localType"), 1),
            create_time=normalize_epoch(data.get("createTime")),
            is_send=bool(_to_int(data.get("isSend"))),
            sender=str(data.get("senderUsername") or ""),
            content=str(data.get("content") or ""),
            parsed_content=str(data.get("parsedContent") or ""),
            raw_content=str(data.get("rawContent") or ""),
            media_type=str(data.get("mediaType") or ""),
            media_file_name=str(data.get("mediaFileName") or ""),
            media_local_path=str(data.get("mediaLocalPath") or ""),
            media_url=str(data.get("mediaUrl") or ""),
            reply_to=str(data.get("replyToMessageId") or ""),
        )

    @classmethod
    def from_chatlab(cls, data: dict[str, Any]) -> "Message":
        chatlab_type = _to_int(data.get("type"), 0)
        local_type = CHATLAB_TYPE_TO_LOCAL.get(chatlab_type, 49 if chatlab_type else 1)
        content = str(data.get("content") or "")
        return cls(
            local_id=0,
            server_id=str(data.get("platformMessageId") or ""),
            local_type=local_type,
            create_time=normalize_epoch(data.get("timestamp")),
            is_send=bool(_to_int(data.get("isSend"))),
            sender=str(data.get("sender") or ""),
            sender_name=str(data.get("accountName") or data.get("groupNickname") or ""),
            content=content,
            parsed_content=content,
            media_local_path=str(data.get("mediaPath") or ""),
            reply_to=str(data.get("replyToMessageId") or ""),
        )

    # -- 便捷属性 --------------------------------------------------------- #
    @property
    def has_media(self) -> bool:
        return bool(self.media_local_path or self.media_url or self.media_file_name)

    @property
    def media_basename(self) -> str:
        for candidate in (self.media_local_path, self.media_file_name, self.media_url):
            if not candidate:
                continue
            name = str(candidate).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if name:
                return name
        return ""


@dataclass(slots=True)
class MessagePage:
    messages: list[Message] = field(default_factory=list)
    count: int = 0
    has_more: bool = False
    media_export_path: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MessagePage":
        raw_messages: Iterable[dict[str, Any]] = data.get("messages") or []
        is_chatlab = "chatlab" in data or bool(data.get("meta"))
        parsed = [
            Message.from_chatlab(item) if is_chatlab else Message.from_raw(item)
            for item in raw_messages
            if isinstance(item, dict)
        ]
        media = data.get("media") or {}
        return cls(
            messages=parsed,
            count=_to_int(data.get("count"), len(parsed)),
            has_more=bool(data.get("hasMore") or (data.get("sync") or {}).get("hasMore")),
            media_export_path=str(media.get("exportPath") or ""),
        )


@dataclass(slots=True)
class PushEvent:
    """SSE 推送事件（message.new / message.revoke）。"""

    event: str
    session_id: str
    session_type: str = ""
    raw_id: str = ""
    source_name: str = ""
    group_name: str = ""
    content: str = ""
    timestamp: int = 0

    @classmethod
    def from_api(cls, event: str, data: dict[str, Any]) -> "PushEvent":
        return cls(
            event=event or str(data.get("event") or "message.new"),
            session_id=str(data.get("sessionId") or ""),
            session_type=str(data.get("sessionType") or ""),
            raw_id=str(data.get("rawid") or ""),
            source_name=str(data.get("sourceName") or ""),
            group_name=str(data.get("groupName") or ""),
            content=str(data.get("content") or ""),
            timestamp=normalize_epoch(data.get("timestamp")),
        )
