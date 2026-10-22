"""WeClone 管线封装：不重写训练逻辑，只用正确的 cwd / 配置调用它。

WeClone 的 ``weclone-cli`` 有一个硬性约束：``_check_project_root()`` 要求当前
工作目录里有 ``pyproject.toml`` 且 project.name == "WeClone"。所以本模块：

* 永远在 ``vendor/WeClone``（或配置指定的目录）下执行；
* 通过 ``WECLONE_CONFIG_PATH`` 指向生成的 ``settings.jsonc``；
* 数据路径在 settings.jsonc 里写绝对路径，避免「cwd 变了数据跑到别处」。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..config import Settings
from ..errors import WeFlowCloneError


@dataclass(slots=True)
class DependencyStatus:
    torch: bool
    llamafactory: bool
    transformers: bool
    detail: str = ""

    @property
    def train_ready(self) -> bool:
        return self.torch and self.llamafactory and self.transformers

    def describe(self) -> str:
        if self.train_ready:
            return "训练依赖齐全（torch / transformers / llamafactory）"
        missing = [
            name
            for name, present in (
                ("torch", self.torch),
                ("transformers", self.transformers),
                ("llamafactory", self.llamafactory),
            )
            if not present
        ]
        return f"缺少训练依赖：{', '.join(missing)}（数据管线不受影响）"


class WeCloneRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ #
    @property
    def source_dir(self) -> Path:
        return self.settings.weclone_dir

    @property
    def settings_path(self) -> Path:
        return self.source_dir / "settings.jsonc"

    @property
    def config_path(self) -> Path:
        return self.settings.weclone_settings_path

    @property
    def is_present(self) -> bool:
        return (self.source_dir / "pyproject.toml").is_file() and (
            self.source_dir / "weclone"
        ).is_dir()

    def python_executable(self) -> str:
        """优先显式配置，其次项目 venv，最后当前解释器。"""
        if self.settings.weclone.python:
            return self.settings.weclone.python
        for candidate in (
            self.settings.root / ".venv" / "Scripts" / "python.exe",
            self.settings.root / ".venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return str(candidate)
        return sys.executable

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["WECLONE_CONFIG_PATH"] = str(self.config_path.resolve())
        return env

    # ------------------------------------------------------------------ #
    def run(
        self,
        args: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if not self.is_present:
            raise WeFlowCloneError(
                f"没找到 WeClone 源码：{self.source_dir}\n"
                "请执行 `wfc init`（或把 WeClone 克隆到该目录），再重试。"
            )
        command = [self.python_executable(), "-m", "weclone.cli", *args]
        proc = subprocess.run(  # noqa: S603 - 参数由本模块固定构造
            command,
            cwd=str(self.source_dir),
            env=self.env(),
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and proc.returncode != 0:
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-800:]
            raise WeFlowCloneError(
                f"WeClone 命令失败（exit {proc.returncode}）：weclone-cli {' '.join(args)}\n{tail}"
            )
        return proc

    def check_dependencies(self) -> DependencyStatus:
        script = (
            "import importlib.util as u;"
            "mods=['torch','transformers','llamafactory'];"
            "print(','.join(m for m in mods if u.find_spec(m)))"
        )
        try:
            proc = subprocess.run(
                [self.python_executable(), "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return DependencyStatus(False, False, False, str(exc))
        found = {name for name in (proc.stdout or "").strip().split(",") if name}
        return DependencyStatus(
            torch="torch" in found,
            llamafactory="llamafactory" in found,
            transformers="transformers" in found,
            detail=(proc.stderr or "").strip()[:300],
        )

    # ------------------------------------------------------------------ #
    def make_dataset(self, extra: Sequence[str] = ()) -> subprocess.CompletedProcess[str]:
        return self.run(["make-dataset", *extra])

    def build_profile(self, extra: Sequence[str] = ()) -> subprocess.CompletedProcess[str]:
        return self.run(["build-profile", *extra])

    def train_sft(self, extra: Sequence[str] = ()) -> subprocess.CompletedProcess[str]:
        return self.run(["train-sft", *extra])

    def serve(
        self,
        host: str,
        port: int,
        *,
        inference: bool = True,
        database: str | None = None,
        extra: Sequence[str] = (),
    ) -> subprocess.CompletedProcess[str]:
        args = ["server", "--host", host, "--port", str(port)]
        if inference:
            args.append("--inference")
        if database:
            args.extend(["--database", database])
        args.extend(extra)
        return self.run(args)

    def version(self) -> str:
        try:
            proc = self.run(["version"], capture=True)
        except WeFlowCloneError:
            return "unknown"
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return text.splitlines()[-1] if text else "unknown"

    # ------------------------------------------------------------------ #
    def python_ok(self) -> tuple[bool, str]:
        exe = self.python_executable()
        if not Path(exe).exists() and shutil.which(exe) is None:
            return False, f"Python 解释器不存在：{exe}"
        try:
            proc = subprocess.run(
                [exe, "-c", "import sys;print(sys.version.split()[0])"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if proc.returncode != 0:
            return False, (proc.stderr or "").strip()[:300]
        return True, proc.stdout.strip()
