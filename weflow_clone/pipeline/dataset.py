"""数据集统计 / 校验：在花几小时训练之前先发现数据问题。

校验项都是「真会导致训练效果差或直接报错」的问题：

* CSV 表头不是 WeClone 要求的 8 列
* type_name 不在 WeClone 词典里（会被静默丢弃）
* 图片消息在 ``dataset/media/images`` 里找不到对应文件（WeClone 会把图片降级为 cut）
* 会话里一条「我」发出的消息都没有（说明 is_sender 映射错了）
* CreateTime 解析不出来
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..weflow.mapping import CSV_HEADER, WECLONE_KNOWN_TYPES

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y/%m/%d %H:%M:%S")


@dataclass(slots=True)
class SessionStat:
    folder: str
    files: int = 0
    messages: int = 0
    from_me: int = 0
    from_other: int = 0
    images: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def has_both_sides(self) -> bool:
        return self.from_me > 0 and self.from_other > 0


@dataclass(slots=True)
class DatasetStats:
    sessions: list[SessionStat] = field(default_factory=list)
    files: int = 0
    messages: int = 0
    media_files: int = 0
    media_bytes: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    def describe(self) -> str:
        return (
            f"{self.session_count} 个会话 / {self.files} 个 CSV / {self.messages} 条消息"
            f" / {self.media_files} 个媒体（{self.media_bytes / 1024 / 1024:.1f} MB）"
        )


@dataclass(slots=True)
class Issue:
    level: str  # fail | warn
    target: str
    message: str
    hint: str = ""


def _parse_time(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    for fmt in _DATETIME_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    try:  # ISO with microseconds / offset
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def scan(settings: Settings) -> DatasetStats:
    stats = DatasetStats()
    csv_root = settings.csv_dir
    if not csv_root.is_dir():
        return stats

    media_images = settings.media_dir / "images"
    if media_images.is_dir():
        for path in media_images.iterdir():
            if path.is_file():
                stats.media_files += 1
                stats.media_bytes += path.stat().st_size

    for folder in sorted(p for p in csv_root.iterdir() if p.is_dir()):
        session = SessionStat(folder=folder.name)
        for csv_path in sorted(folder.glob("*.csv")):
            session.files += 1
            stats.files += 1
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        session.messages += 1
                        stats.messages += 1
                        type_name = (row.get("type_name") or "").strip().lower()
                        stats.by_type[type_name] = stats.by_type.get(type_name, 0) + 1
                        session.by_type[type_name] = session.by_type.get(type_name, 0) + 1
                        if type_name == "image":
                            session.images += 1
                        if (row.get("is_sender") or "").strip() == "1":
                            session.from_me += 1
                        else:
                            session.from_other += 1
            except (OSError, csv.Error):
                continue
        stats.sessions.append(session)
    return stats


def validate(settings: Settings) -> list[Issue]:
    issues: list[Issue] = []
    csv_root = settings.csv_dir
    if not csv_root.is_dir() or not any(csv_root.iterdir()):
        issues.append(
            Issue(
                "fail",
                str(csv_root),
                "数据集为空",
                "先运行 `wfc pull`（直连 WeFlow）或 `wfc import <WeFlow导出目录>`",
            )
        )
        return issues

    media_images = settings.media_dir / "images"
    media_names = (
        {p.stem.lower() for p in media_images.iterdir() if p.is_file()}
        if media_images.is_dir()
        else set()
    )

    for folder in sorted(p for p in csv_root.iterdir() if p.is_dir()):
        session_has_self = False
        missing_media = 0
        unknown_types: dict[str, int] = {}
        bad_time = 0
        for csv_path in sorted(folder.glob("*.csv")):
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    header = [name.strip() for name in (reader.fieldnames or [])]
                    if header[: len(CSV_HEADER)] != CSV_HEADER:
                        issues.append(
                            Issue(
                                "fail",
                                csv_path.name,
                                f"表头不符合 WeClone 约定：{','.join(header)}",
                                f"应为：{','.join(CSV_HEADER)}",
                            )
                        )
                        continue
                    for row in reader:
                        type_name = (row.get("type_name") or "").strip().lower()
                        if type_name not in WECLONE_KNOWN_TYPES:
                            unknown_types[type_name] = unknown_types.get(type_name, 0) + 1
                        if (row.get("is_sender") or "").strip() == "1":
                            session_has_self = True
                        if not _parse_time(row.get("CreateTime") or ""):
                            bad_time += 1
                        if type_name == "image":
                            src = (row.get("src") or "").strip()
                            name = src.replace("\\", "/").rsplit("/", 1)[-1]
                            if name and Path(name).stem.lower() not in media_names:
                                missing_media += 1
            except (OSError, csv.Error) as exc:
                issues.append(Issue("fail", csv_path.name, f"读取失败：{exc}"))

        if unknown_types:
            detail = "、".join(f"{k or '(空)'}×{v}" for k, v in sorted(unknown_types.items()))
            issues.append(
                Issue(
                    "warn",
                    folder.name,
                    f"含 WeClone 不认识的消息类型：{detail}",
                    "这些行会被 WeClone 忽略；如需保留请改 export.include_types",
                )
            )
        if not session_has_self:
            issues.append(
                Issue(
                    "fail",
                    folder.name,
                    "没有任何 is_sender=1 的消息",
                    "WeClone 用它区分「我」和「对方」，缺失会导致无法构造 Q&A",
                )
            )
        if missing_media:
            issues.append(
                Issue(
                    "warn",
                    folder.name,
                    f"{missing_media} 条图片消息找不到媒体文件",
                    "WeClone 会把这类消息降级为截断点；可开启 media.images 后重新 pull",
                )
            )
        if bad_time:
            issues.append(
                Issue(
                    "warn",
                    folder.name,
                    f"{bad_time} 条消息的 CreateTime 无法解析",
                    "检查 export.timestamp_format 设置",
                )
            )

    total_media = len(media_names)
    if total_media == 0 and settings.export.include_types and "image" in settings.export.include_types:
        issues.append(
            Issue(
                "warn",
                "dataset/media/images",
                "没有任何图片",
                "纯文本训练没问题；要启用图片模态请确认 weflow.media.images 为 true",
            )
        )
    return issues


def make_dataset(settings: Settings, runner) -> int:
    """调用 weclone-cli make-dataset，返回退出码。"""
    proc = runner.make_dataset()
    return proc.returncode
