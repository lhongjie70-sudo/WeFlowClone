"""消息映射：WeFlow 消息 -> WeClone 数据集行。

WeClone 从 ``dataset/csv/<会话>/<会话>_<起>_<止>.csv`` 读入，必需列：

    id, MsgSvrID, type_name, is_sender, talker, msg, src, CreateTime

* ``type_name`` 用 WeClone 双语词典里的英文名（text / image / voice /
  video / sticker / file / location），和 WeFlow 自带的 WeClone 导出格式一致。
* ``is_sender`` 1 = 我发出，0 = 对方发出（WeClone 靠它判断谁在说话）。
* ``talker`` 是「发送者展示名」，文件夹名是「会话对方展示名」，
  WeClone 会用 ``_find_partner_talker`` 把两者对上。
* ``src`` 只在媒体消息里有意义：WeClone 会拿 ``basename`` 去
  ``dataset/media/images/<name>.*`` 里匹配。

本模块刻意不依赖 pandas，纯标准库，便于在没有 GPU 的机器上先跑通数据链路。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from .schema import Message

#: WeChat localType -> WeClone type_name（与 WeFlow 的 WeCloneFormatter 对齐）
LOCAL_TYPE_TO_NAME = {
    1: "text",
    3: "image",
    34: "voice",
    43: "video",
    47: "sticker",
    48: "location",
    49: "text",  # 公众号 / 链接 / 文件等 appmsg，按内容细分
    10000: "unsupported",  # 系统消息
    10002: "unsupported",  # 撤回提示
    42: "unsupported",  # 名片
    50: "unsupported",  # 通话
}

#: WeClone 认识的 type_name（其 skip/cut 词典），其余一律不写入 CSV
WECLONE_KNOWN_TYPES = {"text", "image", "voice", "video", "sticker", "file", "location"}

CSV_HEADER = ["id", "MsgSvrID", "type_name", "is_sender", "talker", "msg", "src", "CreateTime"]

_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_APPMSG_TYPE = re.compile(r"<type>\s*(\d+)\s*</type>", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# 类型判定
# --------------------------------------------------------------------------- #
def _appmsg_type(content: str) -> int | None:
    match = _APPMSG_TYPE.search(content or "")
    return int(match.group(1)) if match else None


def type_name_for(local_type: int, content: str = "") -> str:
    """推断 WeClone 的 type_name。未知类型返回 ``unsupported``。"""
    direct = LOCAL_TYPE_TO_NAME.get(int(local_type))
    if direct and direct != "text":
        return direct

    text = content or ""
    looks_like_appmsg = (
        int(local_type) == 49
        or "<appmsg" in text
        or "<msg>" in text
        or "<msg " in text
    )
    if looks_like_appmsg:
        sub = _appmsg_type(text)
        if sub == 6:
            return "file"
        if sub is not None:
            return "text"  # 链接 / 小程序 / 引用等，正文提取后当文本
        return "text"

    if direct == "text":
        return "text"
    return "unsupported"


def is_supported(type_name: str, include_types: list[str]) -> bool:
    return type_name in WECLONE_KNOWN_TYPES and type_name in include_types


def is_media_type(type_name: str) -> bool:
    return type_name in {"image", "voice", "video", "sticker"}


# --------------------------------------------------------------------------- #
# 文本提取
# --------------------------------------------------------------------------- #
def _xml_value(xml: str, tag: str) -> str:
    pattern = re.compile(
        rf"<{tag}>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</{tag}>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(xml or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def clean_text(value: str) -> str:
    """去掉不可见控制符，统一换行，压缩首尾空白。"""
    if not value:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    return text.strip()


def extract_text(message: Message) -> str:
    """把一条消息变成适合训练的自然语言文本。

    * 普通文本：原文
    * appmsg（链接 / 文件 / 小程序）：标题 + 描述，文件用文件名
    * 其它：``[图片]`` 之类的占位符（WeClone 对图片是单独走多模态的）
    """
    raw = clean_text(message.content)
    type_name = type_name_for(message.local_type, message.content)

    if type_name == "file":
        filename = _xml_value(raw, "filename") or _xml_value(raw, "title")
        return filename or "[文件]"

    if type_name == "text" and (message.local_type == 49 or "<appmsg" in raw):
        title = _xml_value(raw, "title")
        des = _xml_value(raw, "des")
        url = _xml_value(raw, "url")
        parts = [part for part in (title, des) if part]
        if not parts:
            parts = [clean_text(message.parsed_content) or "[链接]"]
        text = " ".join(parts)
        if url and url not in text:
            text = f"{text} {url}"
        return text.strip()

    if type_name == "image":
        return clean_text(message.parsed_content) or "[图片]"
    if type_name == "voice":
        return clean_text(message.parsed_content) or "[语音]"
    if type_name == "video":
        return clean_text(message.parsed_content) or "[视频]"
    if type_name == "sticker":
        return clean_text(message.parsed_content) or "[表情]"
    if type_name == "location":
        return clean_text(message.parsed_content) or "[位置]"

    return clean_text(message.parsed_content) or raw


# --------------------------------------------------------------------------- #
# 时间与 CSV 基础
# --------------------------------------------------------------------------- #
def format_create_time(epoch_seconds: int, mode: str = "local") -> str:
    """秒级时间戳 -> CSV 里的 CreateTime。

    ``local``：本机时区的 ``YYYY-MM-DD HH:MM:SS``（不带时区，WeClone 用
    ``pd.to_datetime`` 解析后做时间窗口判断，naive 最不容易踩时区坑）。
    ``iso``：UTC 的 ``YYYY-MM-DDTHH:MM:SSZ``，与 WeFlow 原生导出保持一致。
    """
    if epoch_seconds <= 0:
        return ""
    if mode == "iso":
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.fromtimestamp(epoch_seconds).strftime("%Y-%m-%d %H:%M:%S")


def escape_cell(value: object) -> str:
    """CSV 转义：含逗号 / 引号 / 换行时加引号并双写引号。"""
    if value is None:
        return ""
    text = str(value)
    if any(ch in text for ch in (',', '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_line(cells: list[object]) -> str:
    return ",".join(escape_cell(cell) for cell in cells)


def build_row(message: Message, index: int, talker: str, src: str, timestamp_format: str) -> list[object]:
    """一行 CSV。``index`` 从 1 开始。"""
    return [
        index,
        message.server_id,
        type_name_for(message.local_type, message.content),
        1 if message.is_send else 0,
        talker,
        extract_text(message),
        src,
        format_create_time(message.create_time, timestamp_format),
    ]


# --------------------------------------------------------------------------- #
# 命名
# --------------------------------------------------------------------------- #
def sanitize_folder_name(name: str, fallback: str = "chat") -> str:
    """生成 Windows / macOS / Linux 都安全的目录名，中文原样保留。"""
    text = unicodedata.normalize("NFC", (name or "").strip())
    text = _ILLEGAL_FILENAME.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    if len(text) > 80:
        text = text[:80].rstrip(" .")
    # Windows 保留名
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if text.upper() in reserved:
        text = f"_{text}"
    return text


def unique_folder_name(preferred: str, username: str, taken: dict[str, str]) -> str:
    """同名会话（比如两个同名好友）自动加 wxid 后缀，避免互相覆盖。"""
    base = sanitize_folder_name(preferred, fallback=sanitize_folder_name(username, "chat"))
    existing = taken.get(base)
    if existing is None or existing == username:
        taken[base] = username
        return base
    suffix = sanitize_folder_name(username, "chat")[:24]
    candidate = f"{base}_{suffix}"
    counter = 2
    while candidate in taken and taken[candidate] != username:
        candidate = f"{base}_{suffix}_{counter}"
        counter += 1
    taken[candidate] = username
    return candidate
