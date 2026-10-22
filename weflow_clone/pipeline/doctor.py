"""自检：一条命令回答「现在到底能不能跑」。

``wfc doctor`` 会把「环境 / WeFlow 连接 / WeClone 管线 / 数据」四类问题
一次性列出来，每条都带上可执行的修复建议。
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, load_jsonc
from ..errors import WeFlowAuthError, WeFlowError, WeFlowUnavailable
from ..weflow.client import ClientOptions, WeFlowClient
from .dataset import scan
from .weclone import WeCloneRunner

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    @property
    def symbol(self) -> str:
        return {"ok": "✓", "warn": "!", "fail": "✗"}.get(self.status, "?")


def _check_python() -> Check:
    version = sys.version_info
    text = f"Python {version.major}.{version.minor}.{version.micro}"
    if version < (3, 10):
        return Check("运行环境", FAIL, text, "WeFlowClone 需要 Python 3.10+；WeClone 本体要求 3.12")
    return Check("运行环境", OK, text)


def _check_config(settings: Settings) -> Check:
    if not settings.source.is_file():
        return Check(
            "整合配置",
            FAIL,
            f"找不到 {settings.source.name}",
            "运行 `wfc init` 生成配置",
        )
    return Check("整合配置", OK, str(settings.source))


def _check_dirs(settings: Settings) -> Check:
    targets = [settings.csv_dir, settings.media_dir, settings.data_dir]
    for target in targets:
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".wfc_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return Check("数据目录", FAIL, f"{target} 不可写：{exc}", "检查目录权限，或用管理员身份运行一次")
    return Check("数据目录", OK, str(settings.data_dir))


def _check_disk(settings: Settings) -> Check:
    try:
        usage = shutil.disk_usage(settings.data_dir)
    except OSError as exc:
        return Check("磁盘空间", WARN, f"无法读取：{exc}")
    free_gb = usage.free / 1024**3
    if free_gb < 10:
        return Check(
            "磁盘空间", WARN, f"剩余 {free_gb:.1f} GB", "微调 7B 模型建议至少 30 GB（权重 + 数据集 + 输出）"
        )
    return Check("磁盘空间", OK, f"剩余 {free_gb:.1f} GB")


def _check_weflow(settings: Settings) -> list[Check]:
    client = WeFlowClient(
        ClientOptions(
            base_url=settings.weflow.base_url,
            access_token=settings.weflow.access_token,
            timeout=min(15.0, settings.weflow.timeout_seconds),
            retries=0,
        )
    )
    checks: list[Check] = []
    try:
        client.health()
    except WeFlowUnavailable as exc:
        checks.append(
            Check(
                "WeFlow 服务",
                FAIL,
                str(exc),
                "打开 WeFlow → 设置 → API 服务 → 启动服务；端口需与 weflow.base_url 一致",
            )
        )
        return checks
    except WeFlowError as exc:  # pragma: no cover - 少见
        checks.append(Check("WeFlow 服务", WARN, str(exc)))
        return checks

    checks.append(Check("WeFlow 服务", OK, f"{settings.weflow.base_url} 在线"))

    try:
        sessions = client.sessions(limit=5)
    except WeFlowAuthError as exc:
        checks.append(
            Check(
                "WeFlow 鉴权",
                FAIL,
                str(exc),
                "把 WeFlow 设置页里的 Token 填进 weflow.jsonc 的 weflow.access_token",
            )
        )
        return checks
    except WeFlowError as exc:
        checks.append(Check("WeFlow 鉴权", WARN, str(exc)))
        return checks

    checks.append(
        Check("WeFlow 鉴权", OK, f"Token 可用，前几个会话：{'、'.join(s.display_name for s in sessions) or '（暂无）'}")
    )
    return checks


def _check_weclone(settings: Settings) -> list[Check]:
    runner = WeCloneRunner(settings)
    checks: list[Check] = []
    if not runner.is_present:
        return [
            Check(
                "WeClone 源码",
                FAIL,
                f"找不到 {settings.weclone_dir}",
                "git clone https://github.com/xming521/WeClone.git vendor/WeClone",
            )
        ]
    checks.append(Check("WeClone 源码", OK, str(settings.weclone_dir)))

    if not runner.settings_path.is_file():
        checks.append(
            Check(
                "WeClone 配置",
                FAIL,
                f"缺少 {runner.settings_path}",
                "运行 `wfc init`（会按 WeClone 官方位置生成 settings.jsonc）",
            )
        )
    else:
        try:
            data = load_jsonc(runner.settings_path)
            csv_folder = str((data.get("make_dataset_args") or {}).get("csv_folder") or "")
            expect = str(settings.csv_dir)
            if csv_folder and Path(csv_folder) != Path(expect):
                checks.append(
                    Check(
                        "WeClone 配置",
                        WARN,
                        f"csv_folder 与整合配置不一致：{csv_folder} != {expect}",
                        "运行 `wfc init --force` 重新生成",
                    )
                )
            else:
                checks.append(Check("WeClone 配置", OK, str(runner.settings_path)))
        except Exception as exc:  # noqa: BLE001 - 配置文件任何异常都值得提示
            checks.append(Check("WeClone 配置", WARN, f"解析失败：{exc}", "重新运行 `wfc init --force`"))

    ok_python, detail = runner.python_ok()
    checks.append(
        Check(
            "WeClone 解释器",
            OK if ok_python else FAIL,
            f"{runner.python_executable()}（{detail}）" if ok_python else detail,
            "运行 scripts/setup.ps1 创建 .venv，或在 weflow.jsonc 里指定 weclone.python",
        )
    )

    deps = runner.check_dependencies()
    checks.append(
        Check(
            "训练依赖",
            OK if deps.train_ready else WARN,
            deps.describe(),
            "训练前执行 scripts/setup.ps1 安装 WeClone 依赖（torch + llamafactory）",
        )
    )
    return checks


def _check_dataset(settings: Settings) -> Check:
    stats = scan(settings)
    if stats.messages == 0:
        return Check(
            "数据集",
            WARN,
            "还没有数据",
            "运行 `wfc pull`（从 WeFlow 拉取）或 `wfc import <导出目录>`",
        )
    return Check("数据集", OK, stats.describe())


def run_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = [
        _check_python(),
        _check_config(settings),
        _check_dirs(settings),
        _check_disk(settings),
    ]
    checks.extend(_check_weflow(settings))
    checks.extend(_check_weclone(settings))
    checks.append(_check_dataset(settings))
    return checks


def exit_code(checks: list[Check]) -> int:
    if any(check.status == FAIL for check in checks):
        return 1
    return 0
