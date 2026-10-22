"""同步编排：从 WeFlow 拉会话 → 变成 WeClone 数据集。

这一层负责「选哪些会话、拉多少、失败怎么办」，不负责具体格式转换
（那是 ``weflow/mapping.py`` + ``weflow/exporter.py`` 的事）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from .config import Settings
from .errors import WeFlowCloneError, WeFlowError
from .weflow.client import WeFlowClient
from .weflow.exporter import CsvExporter, ExportResult, collect_contact_names, collect_member_names
from .weflow.schema import PushEvent, Session


@dataclass(slots=True)
class SyncSummary:
    results: list[ExportResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return sum(item.kept for item in self.results)

    @property
    def messages(self) -> int:
        return sum(item.written for item in self.results)

    @property
    def media(self) -> int:
        return sum(item.media_files for item in self.results)

    def describe(self) -> str:
        return (
            f"{len(self.results)} 个会话，{self.kept}/{self.messages} 条消息，{self.media} 个媒体"
        )


def normalize_date(value: str | int | None) -> str | None:
    """把 ``2024-01-01`` / ``20240101`` / 时间戳统一成 WeFlow 接受的形式。"""
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text.isdigit():
        return text
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%Y%m%d")
        except ValueError:
            continue
    raise WeFlowCloneError(f"无法识别的时间：{value}（支持 YYYYMMDD / YYYY-MM-DD / 时间戳）")


def select_sessions(
    sessions: Iterable[Session],
    settings: Settings,
    selectors: Sequence[str] = (),
    *,
    include_groups: bool | None = None,
    max_sessions: int = 0,
) -> list[Session]:
    """按配置 + 命令行选择器筛选会话（选择器匹配展示名或 wxid，支持子串）。"""
    allow_groups = settings.weflow.include_groups if include_groups is None else include_groups
    includes = {item.lower() for item in settings.weflow.include}
    excludes = {item.lower() for item in settings.weflow.exclude}
    wanted = [item.strip().lower() for item in selectors if item.strip()]

    chosen: list[Session] = []
    for session in sessions:
        name = session.display_name.lower()
        username = session.username.lower()
        if session.is_group and not allow_groups:
            continue
        if includes and not ({name, username} & includes) and not any(
            item in name or item in username for item in includes
        ):
            continue
        if excludes and ({name, username} & excludes or any(
            item in name or item in username for item in excludes
        )):
            continue
        if wanted and not any(item in name or item in username for item in wanted):
            continue
        chosen.append(session)

    limit = max_sessions or settings.weflow.max_sessions
    if limit and limit > 0:
        chosen = chosen[:limit]
    return chosen


def pull(
    settings: Settings,
    client: WeFlowClient,
    *,
    selectors: Sequence[str] = (),
    since: str | int | None = None,
    until: str | int | None = None,
    limit: int = 0,
    include_groups: bool | None = None,
    media: bool | None = None,
    dry_run: bool = False,
    progress=None,
) -> SyncSummary:
    """拉取并导出。``media=None`` 时遵循配置。"""
    summary = SyncSummary()

    sessions = client.sessions(limit=10000)
    chosen = select_sessions(
        sessions, settings, selectors, include_groups=include_groups
    )
    if not chosen:
        raise WeFlowCloneError(
            "没有选中任何会话。检查 weflow.include / exclude 配置，"
            "或用 `wfc sessions` 看看 WeFlow 里有哪些会话可用。"
        )

    if media is None:
        media_enabled = settings.export.media.download
    else:
        media_enabled = media

    contacts = []
    try:
        contacts = client.contacts(limit=5000)
    except WeFlowError:
        contacts = []
    contact_names = collect_contact_names(contacts)

    exporter = CsvExporter(settings, client=client if media_enabled else None)
    since_value = normalize_date(since)
    until_value = normalize_date(until)

    for session in chosen:
        if dry_run:
            summary.skipped.append((session.display_name, "dry-run，未写入"))
            continue
        try:
            member_names: dict[str, str] = {}
            if session.is_group:
                try:
                    member_names = collect_member_names(client.group_members(session.username))
                except WeFlowError:
                    member_names = {}

            messages = list(
                client.iter_messages(
                    session.username,
                    start=since_value,
                    end=until_value,
                    media=media_enabled,
                    max_messages=limit,
                )
            )
            if not messages:
                summary.skipped.append((session.display_name, "该时间范围内没有消息"))
                continue

            result = exporter.export_session(
                session,
                messages,
                contact_names=contact_names,
                member_names=member_names,
                progress=progress,
            )
            summary.results.append(result)
        except WeFlowError as exc:
            summary.failures.append((session.display_name, str(exc)))
        except OSError as exc:
            summary.failures.append((session.display_name, f"写入失败：{exc}"))

    return summary


class Watcher:
    """监听 WeFlow SSE 推送，按 debounce 做增量拉取。

    WeFlow 的推送只给一行摘要，没有完整消息体，所以这里不在事件里造数据，
    而是在「安静期」后用 ``since`` 增量拉取，保证数据与 WeFlow 完全一致。
    """

    def __init__(
        self,
        settings: Settings,
        client: WeFlowClient,
        *,
        on_pull=None,
        log=print,
    ) -> None:
        self.settings = settings
        self.client = client
        self.on_pull = on_pull
        self.log = log
        self._pending: set[str] = set()
        self._pending_names: dict[str, str] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_pull = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        debounce = max(1.0, self.settings.watch.debounce_seconds)
        self.log(f"开始监听 WeFlow 推送（debounce {debounce:g}s，Ctrl+C 退出）")
        while not self._stop.is_set():
            try:
                for event in self.client.stream_events():
                    if self._stop.is_set():
                        break
                    self._handle(event, debounce)
            except WeFlowError as exc:
                self.log(f"推送连接中断：{exc}，5 秒后重连")
                time.sleep(5.0)
            except KeyboardInterrupt:  # pragma: no cover - 交互退出
                break

    # ------------------------------------------------------------------ #
    def _handle(self, event: PushEvent, debounce: float) -> None:
        if event.event not in {"message.new", "message.revoke"} or not event.session_id:
            return
        with self._lock:
            self._pending.add(event.session_id)
            self._pending_names[event.session_id] = event.group_name or event.source_name
            idle = time.monotonic() - self.last_pull
            names = ", ".join(
                self._pending_names.get(sid, sid) for sid in list(self._pending)[:5]
            )
            self.log(f"新消息：{names}（累计 {len(self._pending)} 个会话待同步）")
            if idle >= debounce and (self._worker is None or not self._worker.is_alive()):
                self._start_worker()

    def _start_worker(self) -> None:
        sessions = list(self._pending)
        names = dict(self._pending_names)
        self._pending.clear()
        self._pending_names.clear()
        self._worker = threading.Thread(
            target=self._pull_incremental,
            args=(sessions, names),
            name="wfc-incremental-pull",
            daemon=True,
        )
        self._worker.start()

    def _pull_incremental(self, session_ids: list[str], names: dict[str, str]) -> None:
        """增量策略：对变化过的会话做「全量重拉」。

        为什么不是追加？WeClone 的 CSV 是 `_<起>_<止>` 分片，追加会让序号与
        分片命名冲突；而全量重拉保证「一个会话 = 一份一致的数据」。
        个人号的聊天量级下这个代价很小。
        """
        try:
            summary = pull(
                self.settings,
                self.client,
                selectors=session_ids,
                media=True,
            )
            self.log(f"增量同步完成：{summary.describe()}")
            if self.on_pull is not None:
                self.on_pull(summary)
        except WeFlowCloneError as exc:
            self.log(f"增量同步失败：{exc}")
        finally:
            with self._lock:
                self.last_pull = time.monotonic()
