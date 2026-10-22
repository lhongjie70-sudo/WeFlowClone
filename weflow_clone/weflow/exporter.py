"""导出器：把一个 WeFlow 会话写成 WeClone 能直接吃的 CSV 目录。

产物结构（与 WeClone ``dataset/csv`` 约定完全一致）::

    data/dataset/csv/张三/
        张三_1_1523.csv
        users.json
    data/dataset/media/images/abc123.jpg

要点：

* 文件名 ``<会话>_<起始序号>_<结束序号>.csv``，WeClone 按序号排序并合并；
* 超过 ``max_messages_per_file`` 自动分片，避免单个文件过大；
* ``users.json`` 写入备注 / 关系，WeClone 会用它填 ``chat_with`` 字段，
  让模型知道「在跟谁聊天」；
* 媒体按 ``WeClone`` 的约定落到 ``dataset/media/images/``，
  CSV 的 ``src`` 只写相对名，WeClone 会用 basename 做匹配。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..config import Settings
from .mapping import (
    CSV_HEADER,
    build_row,
    csv_line,
    is_media_type,
    is_supported,
    sanitize_folder_name,
    type_name_for,
    unique_folder_name,
)
from .schema import Message, Session

ProgressFn = Callable[[int, int], None]


@dataclass(slots=True)
class ExportResult:
    session: Session
    folder: str
    csv_files: list[Path] = field(default_factory=list)
    written: int = 0
    kept: int = 0
    media_files: int = 0
    media_bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def folder_name(self) -> str:
        return self.folder

    def summary(self) -> str:
        parts = [f"{self.session.display_name}: 写入 {self.kept}/{self.written} 条"]
        if self.media_files:
            parts.append(f"媒体 {self.media_files} 个")
        dropped = {k: v for k, v in self.skipped.items() if v}
        if dropped:
            detail = "、".join(f"{k}×{v}" for k, v in sorted(dropped.items()))
            parts.append(f"跳过（{detail}）")
        if not self.csv_files:
            parts.append("无可用数据")
        return "，".join(parts)


def _media_subdir(type_name: str) -> str:
    return {
        "image": "images",
        "voice": "voices",
        "video": "videos",
        "sticker": "emojis",
    }.get(type_name, "files")


class CsvExporter:
    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self.client = client
        self._folders: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    def export_session(
        self,
        session: Session,
        messages: Sequence[Message],
        *,
        contact_names: dict[str, str] | None = None,
        member_names: dict[str, str] | None = None,
        progress: ProgressFn | None = None,
        overwrite: bool = True,
    ) -> ExportResult:
        """把 ``messages`` 写到 ``data/dataset/csv/<会话>/``。"""
        contact_names = contact_names or {}
        member_names = member_names or {}

        preferred = (
            session.display_name
            if self.settings.export.session_folder_mode == "display"
            else session.username
        )
        folder = unique_folder_name(preferred, session.username, self._folders)
        session_dir = self.settings.csv_dir / folder
        if session_dir.exists() and overwrite:
            for stale in session_dir.glob("*.csv"):
                stale.unlink()
        session_dir.mkdir(parents=True, exist_ok=True)

        result = ExportResult(session=session, folder=folder, written=len(messages))
        include_types = list(self.settings.export.include_types)
        rows: list[list[object]] = []
        row_index = 0

        for position, message in enumerate(messages, start=1):
            type_name = type_name_for(message.local_type, message.content)
            if not is_supported(type_name, include_types):
                result.skipped[type_name] = result.skipped.get(type_name, 0) + 1
                continue

            talker = self._talker_name(message, session, contact_names, member_names)
            src = self._handle_media(message, type_name, result)
            row = build_row(message, 0, talker, src, self.settings.export.timestamp_format)

            if type_name == "text" and not str(row[5]).strip():
                result.skipped["empty_text"] = result.skipped.get("empty_text", 0) + 1
                continue

            row_index += 1
            row[0] = row_index
            rows.append(row)
            result.kept += 1

            if progress and (position % 200 == 0 or position == len(messages)):
                progress(position, len(messages))

        result.csv_files = self._write_csv_files(session_dir, folder, rows)
        self._write_users_json(session_dir, session, contact_names)
        return result

    # ------------------------------------------------------------------ #
    def _talker_name(
        self,
        message: Message,
        session: Session,
        contact_names: dict[str, str],
        member_names: dict[str, str],
    ) -> str:
        if message.is_send:
            return self.settings.export.self_name or "我"
        if message.sender_name:
            return message.sender_name
        sender = message.sender
        if sender:
            for lookup in (member_names, contact_names):
                if sender in lookup:
                    return lookup[sender]
            # 私聊里对方只有一个，用会话名比裸 wxid 可读得多
            if not session.is_group:
                return session.display_name
            return sender
        return session.display_name

    def _handle_media(self, message: Message, type_name: str, result: ExportResult) -> str:
        if not is_media_type(type_name):
            return self._non_media_src(message, type_name)

        media_opts = self.settings.export.media
        allowed = {
            "image": media_opts.images,
            "voice": media_opts.voices,
            "video": media_opts.videos,
            "sticker": media_opts.emojis,
        }.get(type_name, False)
        if not media_opts.download or not allowed:
            return ""

        basename = message.media_basename
        if not basename:
            return ""

        subdir = _media_subdir(type_name)
        target_dir = self.settings.media_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / basename

        if target.is_file():
            result.media_files += 1
            return f"{subdir}/{basename}"

        copied: Path | None = None
        if self.client is not None:
            try:
                copied = self.client.copy_or_download_media(message, target_dir)
            except Exception:  # 单个媒体失败不影响整体导出
                copied = None
        if copied is None and message.media_local_path:
            # 即使没有 client（例如 import 场景），本机已有文件就直接复用
            local = Path(message.media_local_path)
            if local.is_file():
                shutil.copyfile(local, target)
                copied = target

        if copied is None or not copied.is_file():
            return ""

        size = copied.stat().st_size
        max_bytes = int(max(0.0, media_opts.max_mb) * 1024 * 1024)
        if max_bytes and size > max_bytes:
            copied.unlink(missing_ok=True)
            return ""

        result.media_files += 1
        result.media_bytes += size
        return f"{subdir}/{copied.name}"

    @staticmethod
    def _non_media_src(message: Message, type_name: str) -> str:
        if type_name == "file":
            return message.media_file_name or ""
        return ""

    def _write_csv_files(self, session_dir: Path, folder: str, rows: list[list[object]]) -> list[Path]:
        if not rows:
            return []
        chunk_size = max(1, int(self.settings.export.max_messages_per_file))
        paths: list[Path] = []
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            first = int(chunk[0][0])
            last = int(chunk[-1][0])
            path = session_dir / f"{folder}_{first}_{last}.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                handle.write(",".join(CSV_HEADER) + "\r\n")
                for row in chunk:
                    handle.write(csv_line(row) + "\r\n")
            paths.append(path)
        return paths

    def _write_users_json(
        self, session_dir: Path, session: Session, contact_names: dict[str, str]
    ) -> None:
        partner_name = contact_names.get(session.username) or session.display_name
        payload = {
            session.display_name: {
                "remark": partner_name,
                "nickname": session.display_name,
                "username": session.username,
            },
            "relation": "群聊" if session.is_group else "好友",
        }
        path = session_dir / "users.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_contact_names(contacts: Iterable) -> dict[str, str]:
    """wxid -> 展示名（备注优先）。"""
    names: dict[str, str] = {}
    for contact in contacts:
        name = getattr(contact, "remark", "") or getattr(contact, "display_name", "")
        if contact.username and name:
            names[contact.username] = name
    return names


def collect_member_names(members: Iterable) -> dict[str, str]:
    names: dict[str, str] = {}
    for member in members:
        name = member.group_nickname or member.remark or member.display_name
        if member.wxid and name:
            names[member.wxid] = name
    return names


__all__ = [
    "CsvExporter",
    "ExportResult",
    "collect_contact_names",
    "collect_member_names",
    "sanitize_folder_name",
]
