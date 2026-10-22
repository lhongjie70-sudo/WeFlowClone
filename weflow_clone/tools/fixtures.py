"""演示 / 测试用的假数据。

真实的 WeFlow 需要已登录的微信和可解密的本机数据库，CI 里不可能有；
于是这里造一份结构完全一致的合成数据：私聊 + 群聊、文本 / 图片 / 链接 /
表情 / 语音，覆盖类型映射、媒体落盘、分页、鉴权等所有分支。

数据是确定的（固定种子），所以测试可以断言具体数值。
"""

from __future__ import annotations

import random
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """标准库生成一张纯色 PNG（避免测试依赖 Pillow）。"""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


PRIVATE_TALKER = "wxid_zhangsan"
PRIVATE_NAME = "张三"
GROUP_TALKER = "43876543210@chatroom"
GROUP_NAME = "周末爬山群"
MY_WXID = "wxid_me"

_LINES = [
    ("other", "在吗？周末有空一起去爬山吗"),
    ("me", "在的，我先看看天气"),
    ("other", "天气预报说周六多云，二十度左右"),
    ("me", "那可以，几点出发"),
    ("other", "早上七点半在你家楼下集合怎么样"),
    ("me", "没问题，我带点水和面包"),
    ("other", "我这边有两个登山杖可以借你一根"),
    ("me", "太好了，正好我最近膝盖不太舒服"),
    ("other", "那就用杖，别硬撑"),
    ("me", "好，路线你定了吗"),
    ("other", "走老路线吧，从北坡上去，来回大概五个小时"),
    ("me", "五小时有点久，我们中午能下山吗"),
    ("other", "能，走快点十一点半就到停车场了"),
    ("me", "行，那我周六早点睡"),
    ("other", "对了，山上有段路在修，要绕一下"),
    ("me", "绕多远"),
    ("other", "也就多走二十分钟，跟着指示牌走就行"),
    ("me", "明白，我把充电宝带上，拍点照片"),
    ("other", "我新买了个相机，正好试试"),
    ("me", "那你负责拍，我负责背东西"),
    ("other", "成交"),
    ("me", "要不要叫上小李"),
    ("other", "他周六加班，说下次一起"),
    ("me", "可惜了，下次吧"),
    ("other", "对了，午饭在山下那家面馆吃？"),
    ("me", "可以，他家牛肉面不错"),
    ("other", "那就这么定了"),
    ("me", "ok，周六见"),
    ("other", "记得穿防滑的鞋"),
    ("me", "放心，我的鞋抓地很好"),
]


@dataclass(slots=True)
class FixtureData:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    members: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    media_root: Path | None = None

    def session(self, username: str) -> dict[str, Any] | None:
        for item in self.sessions:
            if item["username"] == username:
                return item
        return None


def build_fixture(media_root: Path) -> FixtureData:
    """造一份覆盖各类型的聊天数据，媒体文件写到 ``media_root``。"""
    media_root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20240501)
    base_ts = 1_724_000_000  # 2024-08-16 左右

    data = FixtureData(media_root=media_root)
    data.sessions = [
        {
            "username": PRIVATE_TALKER,
            "displayName": PRIVATE_NAME,
            "type": 1,
            "lastTimestamp": base_ts + len(_LINES) * 90,
            "unreadCount": 0,
        },
        {
            "username": GROUP_TALKER,
            "displayName": GROUP_NAME,
            "type": 2,
            "lastTimestamp": base_ts + 3600,
            "unreadCount": 3,
        },
    ]
    data.contacts = [
        {
            "username": PRIVATE_TALKER,
            "displayName": PRIVATE_NAME,
            "remark": "老张",
            "nickname": "张三",
            "alias": "zhangsan88",
            "avatarUrl": "",
            "type": "friend",
        },
        {
            "username": MY_WXID,
            "displayName": "我自己",
            "remark": "",
            "nickname": "我自己",
            "alias": "me",
            "avatarUrl": "",
            "type": "self",
        },
    ]
    data.members = {
        GROUP_TALKER: [
            {"wxid": MY_WXID, "displayName": "我自己", "groupNickname": "阿泽", "isOwner": True},
            {"wxid": "wxid_li", "displayName": "李雷", "groupNickname": "雷子", "isOwner": False},
            {"wxid": "wxid_han", "displayName": "韩梅", "groupNickname": "", "isOwner": False},
        ]
    }

    # ---- 私聊：文本 + 图片 + 链接 + 表情 + 语音 ------------------------- #
    private: list[dict[str, Any]] = []
    for index, (who, text) in enumerate(_LINES):
        ts = base_ts + index * 90
        private.append(
            {
                "localId": 1000 + index,
                "serverId": str(7_300_000_000_000_000_000 + index),
                "localType": 1,
                "createTime": ts,
                "isSend": 1 if who == "me" else 0,
                "senderUsername": MY_WXID if who == "me" else PRIVATE_TALKER,
                "content": text,
                "rawContent": text,
                "parsedContent": text,
            }
        )

    image_name = "photo_mountain.png"
    (media_root / image_name).write_bytes(make_png(32, 32, (76, 154, 210)))
    private.append(
        {
            "localId": 2000,
            "serverId": "7300000000000001000",
            "localType": 3,
            "createTime": base_ts + 1800,
            "isSend": 1,
            "senderUsername": MY_WXID,
            "content": "[图片]",
            "parsedContent": "[图片]",
            "mediaType": "image",
            "mediaFileName": image_name,
            "mediaLocalPath": str(media_root / image_name),
            "mediaUrl": f"http://127.0.0.1:5031/api/v1/media/images/{image_name}",
        }
    )
    private.append(
        {
            "localId": 2001,
            "serverId": "7300000000000001001",
            "localType": 49,
            "createTime": base_ts + 1860,
            "isSend": 0,
            "senderUsername": PRIVATE_TALKER,
            "content": (
                "<msg><appmsg appid='' sdkver='0'><title>北坡登山路线攻略</title>"
                "<des>含停车场位置与补给点</des><type>5</type>"
                "<url>https://example.com/hike-north</url></appmsg></msg>"
            ),
            "parsedContent": "",
        }
    )
    private.append(
        {
            "localId": 2002,
            "serverId": "7300000000000001002",
            "localType": 47,
            "createTime": base_ts + 1920,
            "isSend": 0,
            "senderUsername": PRIVATE_TALKER,
            "content": "[动画表情]",
            "parsedContent": "[动画表情]",
        }
    )
    private.append(
        {
            "localId": 2003,
            "serverId": "7300000000000001003",
            "localType": 34,
            "createTime": base_ts + 1980,
            "isSend": 0,
            "senderUsername": PRIVATE_TALKER,
            "content": "[语音]",
            "parsedContent": "[语音]",
        }
    )
    private.append(
        {
            "localId": 2004,
            "serverId": "7300000000000001004",
            "localType": 10000,
            "createTime": base_ts + 2000,
            "isSend": 0,
            "senderUsername": "",
            "content": "你已添加了张三，现在可以开始聊天了。",
            "parsedContent": "你已添加了张三，现在可以开始聊天了。",
        }
    )
    data.messages[PRIVATE_TALKER] = private

    # ---- 群聊 ----------------------------------------------------------- #
    group: list[dict[str, Any]] = []
    speakers = [("wxid_li", "李雷"), ("wxid_han", "韩梅"), ("wxid_me", "我自己")]
    for index in range(8):
        speaker, _name = speakers[index % len(speakers)]
        ts = base_ts + 3000 + index * 120
        group.append(
            {
                "localId": 3000 + index,
                "serverId": str(7_310_000_000_000_000_000 + index),
                "localType": 1,
                "createTime": ts,
                "isSend": 1 if speaker == MY_WXID else 0,
                "senderUsername": speaker,
                "content": f"群里第 {index + 1} 条：{'我先到停车场' if index % 2 else '我买好了水'}",
                "parsedContent": f"群里第 {index + 1} 条：{'我先到停车场' if index % 2 else '我买好了水'}",
            }
        )
    data.messages[GROUP_TALKER] = group

    # 让数据有一点点随机性，避免所有时间戳完全等差
    for messages in data.messages.values():
        for message in messages:
            message["createTime"] += rng.randint(0, 5)
    return data


def to_chatlab(data: FixtureData, talker: str) -> dict[str, Any]:
    """把原始消息转成 ChatLab 结构（模拟 WeFlow 的 chatlab=1 响应）。"""
    session = data.session(talker) or {"displayName": talker}
    members = data.members.get(talker) or []
    names = {member["wxid"]: member for member in members}
    type_map = {1: 0, 3: 1, 34: 2, 43: 3, 47: 5, 48: 8, 49: 7}
    messages = []
    for message in data.messages.get(talker, []):
        sender = message.get("senderUsername") or talker
        member = names.get(sender) or {}
        content = message.get("parsedContent") or message.get("content") or ""
        messages.append(
            {
                "sender": sender,
                "accountName": member.get("displayName") or sender,
                "groupNickname": member.get("groupNickname") or None,
                "timestamp": message["createTime"],
                "type": type_map.get(int(message["localType"]), 99),
                "content": content,
                "platformMessageId": message["serverId"],
            }
        )
    return {
        "chatlab": {"version": "0.0.2", "exportedAt": 0, "generator": "WeFlow"},
        "meta": {
            "name": session.get("displayName", talker),
            "platform": "im",
            "type": "group" if talker.endswith("@chatroom") else "private",
            "groupId": talker,
            "ownerId": MY_WXID,
        },
        "members": members,
        "messages": messages,
    }
