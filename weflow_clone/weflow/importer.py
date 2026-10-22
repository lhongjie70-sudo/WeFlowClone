"""导入器：把 WeFlow 手工导出的文件拉进统一数据集。

支持的输入
----------
1. **WeFlow「WeClone」导出目录**：一个会话一个文件夹，内有
   ``id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime`` 的 CSV。
2. **ChatLab JSON**（WeFlow 的 ChatLab 导出 / 通过 API 拿到的 ChatLab 包）。
3. **WeFlow 原始 JSON 导出**（含 ``localType`` 字段的消息数组）。

统一输出到 ``data/dataset/csv/<会话>/<会话>_1_<N>.csv``，
并把能定位到的媒体复制到 ``data/dataset/media/images/``。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import Settings
from ..errors import WeFlowCloneError
from .exporter import CsvExporter, ExportResult
from .mapping import CSV_HEADER, escape_cell, sanitize_folder_name, unique_folder_name
from .schema import Message, Session

REQUIRED_COLUMNS = {"type_name", "is_sender", "talker", "msg", "CreateTime"}


@dataclass(slots=True)
class ImportResult:
    source: Path
    kind: str
    folders: list[str] = field(default_factory=list)
    messages: int = 0
    media: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.source.name}（{self.kind}）→ {len(self.folders)} 个会话 / {self.messages} 条"]
        if self.media:
            parts.append(f"媒体 {self.media} 个")
        if self.warnings:
            parts.append(f"警告 {len(self.warnings)} 条")
        return "，".join(parts)


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                reader = csv.DictReader(handle, dialect=dialect)
                rows = [dict(row) for row in reader]
                header = list(reader.fieldnames or [])
                return header, rows
        except UnicodeDecodeError:
            continue
    raise WeFlowCloneError(f"无法识别 CSV 编码：{path}")


def _looks_like_weclone_csv(header: Sequence[str]) -> bool:
    normalized = {name.strip().lstrip("\ufeff") for name in header if name}
    return REQUIRED_COLUMNS.issubset(normalized)


def _iter_weclone_csv_dirs(root: Path) -> Iterable[tuple[str, list[Path]]]:
    """找出「会话文件夹 + 其中 CSV」的组合，兼容直接把 CSV 扔在根目录。"""
    csv_files = sorted(p for p in root.rglob("*.csv") if p.is_file())
    grouped: dict[Path, list[Path]] = {}
    for path in csv_files:
        grouped.setdefault(path.parent, []).append(path)
    for folder, files in grouped.items():
        yield (root.name if folder == root else folder.name), files


class Importer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._folders: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    def import_path(self, path: Path) -> list[ImportResult]:
        if not path.exists():
            raise WeFlowCloneError(f"路径不存在：{path}")
        if path.is_file():
            suffix = path.suffix.lower()
            if suffix == ".csv":
                return [self._import_csv_folder(path.parent, [path], folder_hint=path.stem)]
            if suffix in {".json", ".jsonl"}:
                return [self._import_json(path)]
            raise WeFlowCloneError(f"不支持的文件类型：{path.suffix}（支持 .csv / .json / .jsonl）")

        results: list[ImportResult] = []
        json_files = [
            p for p in path.rglob("*.json")
            if p.is_file() and p.name.lower() not in {"users.json", "dataset_info.json"}
        ]
        csv_dirs = list(_iter_weclone_csv_dirs(path))

        for folder_hint, files in csv_dirs:
            header, _ = _read_csv_rows(files[0])
            if not _looks_like_weclone_csv(header):
                continue
            results.append(self._import_csv_folder(files[0].parent, files, folder_hint=folder_hint))

        json_errors: list[str] = []
        for json_file in json_files:
            try:
                results.append(self._import_json(json_file))
            except WeFlowCloneError as exc:
                json_errors.append(str(exc))

        if not results:
            detail = ("\n  · " + "\n  · ".join(json_errors[:3])) if json_errors else ""
            raise WeFlowCloneError(
                f"没在 {path} 里找到可导入的 WeFlow 导出：\n"
                "  · WeFlow「导出 → WeClone/CSV」的文件夹（含 id,MsgSvrID,type_name,... 表头）\n"
                "  · ChatLab JSON\n"
                "  · WeFlow 原始 JSON" + detail
            )
        return results

    # ------------------------------------------------------------------ #
    def _import_csv_folder(
        self, folder: Path, files: list[Path], *, folder_hint: str
    ) -> ImportResult:
        result = ImportResult(source=folder, kind="WeClone-CSV")
        rows: list[dict[str, str]] = []
        for file in sorted(files):
            header, file_rows = _read_csv_rows(file)
            if not _looks_like_weclone_csv(header):
                result.warnings.append(f"跳过缺少必需列的 CSV：{file.name}")
                continue
            rows.extend(file_rows)

        if not rows:
            raise WeFlowCloneError(f"{folder} 中的 CSV 缺少必需列（需要 {sorted(REQUIRED_COLUMNS)}）")

        folder_name = unique_folder_name(
            sanitize_folder_name(folder_hint, "chat"), folder_hint, self._folders
        )
        target_dir = self.settings.csv_dir / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        normalized: list[list[object]] = []
        index = 0
        for row in rows:
            msg = (row.get("msg") or "").strip()
            type_name = (row.get("type_name") or "text").strip().lower()
            if type_name == "text" and not msg:
                continue
            index += 1
            normalized.append(
                [
                    index,
                    (row.get("MsgSvrID") or "").strip(),
                    type_name,
                    (row.get("is_sender") or "0").strip() or "0",
                    (row.get("talker") or folder_name).strip(),
                    msg,
                    (row.get("src") or "").strip(),
                    (row.get("CreateTime") or "").strip(),
                ]
            )

        chunk_size = max(1, self.settings.export.max_messages_per_file)
        for start in range(0, len(normalized), chunk_size):
            chunk = normalized[start : start + chunk_size]
            target = target_dir / f"{folder_name}_{int(chunk[0][0])}_{int(chunk[-1][0])}.csv"
            with target.open("w", encoding="utf-8-sig", newline="") as handle:
                handle.write(",".join(CSV_HEADER) + "\r\n")
                for row in chunk:
                    handle.write(",".join(escape_cell(cell) for cell in row) + "\r\n")

        result.media = self._copy_media_from_folder(folder, normalized)
        result.messages = len(normalized)
        result.folders.append(folder_name)
        return result

    def _copy_media_from_folder(self, source_dir: Path, rows: list[list[object]]) -> int:
        if not self.settings.export.media.download:
            return 0
        media_dir = self.settings.media_dir / "images"
        media_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        seen: set[str] = set()
        candidates = [p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() != ".csv"]
        by_name = {p.name: p for p in candidates}
        for row in rows:
            src = str(row[6] or "")
            if not src:
                continue
            name = src.replace("\\", "/").rsplit("/", 1)[-1]
            if not name or name in seen:
                continue
            seen.add(name)
            source = by_name.get(name)
            if source is None:
                continue
            target = media_dir / name
            if not target.exists():
                target.write_bytes(source.read_bytes())
            copied += 1
        return copied

    # ------------------------------------------------------------------ #
    def _import_json(self, path: Path) -> ImportResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeFlowCloneError(f"JSON 解析失败：{path}（{exc}）") from exc

        kind = "WeFlow-JSON"
        session: Session
        messages: list[Message]

        if isinstance(payload, dict) and "chatlab" in payload:
            kind = "ChatLab-JSON"
            meta = payload.get("meta") or {}
            name = str(meta.get("name") or path.stem)
            session_id = str(meta.get("groupId") or meta.get("name") or path.stem)
            is_group = str(meta.get("type") or "").lower() == "group"
            session = Session(
                username=session_id,
                display_name=name,
                type=2 if is_group else 1,
            )
            messages = [
                Message.from_chatlab(item)
                for item in payload.get("messages") or []
                if isinstance(item, dict)
            ]
            # ChatLab 没有 isSend 字段，用 meta.ownerId 反推「谁是我」
            owner = str(meta.get("ownerId") or "")
            if owner:
                for item in messages:
                    item.is_send = item.sender == owner
        elif isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            kind = "WeFlow-JSON"
            name = str(payload.get("talker") or payload.get("name") or path.stem)
            session = Session(username=name, display_name=name)
            messages = [
                Message.from_raw(item) for item in payload["messages"] if isinstance(item, dict)
            ]
        elif isinstance(payload, list):
            session = Session(username=path.stem, display_name=path.stem)
            messages = [
                Message.from_chatlab(item) if "sender" in item else Message.from_raw(item)
                for item in payload
                if isinstance(item, dict)
            ]
        else:
            raise WeFlowCloneError(f"无法识别的 JSON 结构：{path}")

        if not messages:
            raise WeFlowCloneError(f"JSON 里没有消息：{path}")

        messages.sort(key=lambda item: item.create_time)
        exporter = CsvExporter(self.settings)
        export: ExportResult = exporter.export_session(session, messages, overwrite=True)

        result = ImportResult(source=path, kind=kind)
        result.folders.append(export.folder)
        result.messages = export.kept
        result.media = export.media_files
        if export.kept == 0:
            result.warnings.append(
                "没有任何消息被写入：检查 export.include_types 与消息类型是否匹配"
            )
        return result


__all__ = ["Importer", "ImportResult"]
