"""发布前本地闸门：把「能不能发 GitHub」变成一条命令。

它检查的是**发布时才会暴露**的问题：

1. 版本号在多处是否一致（pyproject / __init__ / spec / 构建脚本 / README）；
2. README 与 docs 里的相对链接是否都存在（GitHub 上点开会 404）；
3. 仓库里有没有混进 API Key、Token（一旦推到公开仓库就是事故）；
4. 生成物是否被 .gitignore 排除（data/、dist/、settings.jsonc）；
5. 两个 workflow 的 YAML 结构是否合法、关键步骤是否齐全；
6. 测试是否真的通过（不是「我记得通过」）；
7. 构建产物是否存在、能否运行、sha256 是多少。

用法：

    python scripts/release_check.py              # 全量检查（测试 + 产物 + 校验和）
    python scripts/release_check.py --no-tests   # 只做静态检查
    python scripts/release_check.py --build      # 先构建再检查（需要 pyinstaller）

退出码 0 = 可以发布；1 = 有阻塞项。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_MIN = 90


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    blocking: bool = True


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, result: Result) -> None:
        self.results.append(result)

    @property
    def failures(self) -> list[Result]:
        return [item for item in self.results if not item.ok and item.blocking]

    @property
    def warnings(self) -> list[Result]:
        return [item for item in self.results if not item.ok and not item.blocking]

    def render(self) -> str:
        lines: list[str] = []
        for item in self.results:
            mark = "✓" if item.ok else ("✗" if item.blocking else "!")
            lines.append(f"  {mark} {item.name}" + (f"：{item.detail}" if item.detail else ""))
            if not item.ok and item.hint:
                lines.append(f"      → {item.hint}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 静态检查
# --------------------------------------------------------------------------- #
def check_versions(report: Report) -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    pyproject_version = match.group(1) if match else ""

    init_text = (ROOT / "weflow_clone" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    init_version = init_match.group(1) if init_match else ""

    sources = {
        "pyproject.toml": pyproject_version,
        "weflow_clone/__init__.py": init_version,
        "packaging/build.ps1": _grep(ROOT / "packaging" / "build.ps1", r'\$Version\s*=\s*"([^"]+)"'),
        "packaging/build.sh": _grep(ROOT / "packaging" / "build.sh", r'VERSION="([^"]+)"'),
        "packaging/wfc.spec": _grep(ROOT / "packaging" / "wfc.spec", r'CFBundleShortVersionString":\s*"([^"]+)"'),
    }
    versions = {name: version for name, version in sources.items() if version}
    unique = set(versions.values())
    missing = [name for name, version in sources.items() if not version]

    if missing:
        report.add(Result("版本号", False, f"这些文件里没找到版本号：{', '.join(missing)}"))
    elif len(unique) != 1:
        detail = "；".join(f"{name}={version}" for name, version in versions.items())
        report.add(Result("版本号", False, f"不一致：{detail}", "统一成同一个版本号再发"))
    else:
        report.add(Result("版本号", True, f"全部为 {versions['pyproject.toml']}"))
    return pyproject_version


def _grep(path: Path, pattern: str) -> str:
    if not path.is_file():
        return ""
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def check_files(report: Report) -> None:
    required = [
        "README.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "NOTICE.md",
        "pyproject.toml",
        ".gitignore",
        "weflow_clone/pipeline/bootstrap.py",
        "weflow_clone/cli.py",
        "weflow_clone/bot/web/index.html",
        "packaging/wfc.spec",
        "packaging/build.ps1",
        "packaging/build.sh",
        "packaging/launcher.py",
        "packaging/make_icons.py",
        "scripts/setup.ps1",
        "scripts/start.ps1",
        "scripts/release_check.py",
        "scripts/bump_version.py",
        "安装.bat",
        "启动.bat",
        "启动-模型服务.bat",
        ".github/workflows/release.yml",
        ".github/workflows/ci.yml",
    ]
    missing = [item for item in required if not (ROOT / item).exists()]
    if missing:
        report.add(Result("必要文件", False, f"缺少 {len(missing)} 个：{', '.join(missing)}"))
    else:
        report.add(Result("必要文件", True, f"{len(required)} 个齐全"))


def check_docs_links(report: Report) -> None:
    broken: list[str] = []
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "integrations").rglob("*.md"))]
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\]\(([^)\s]+)\)", text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            # GitHub 仓库内相对跳转（../../releases、/issues 等）不是本地文件
            if target.startswith(("..", "/")) or "releases" in target:
                continue
            target = target.split("#")[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} → {target}")
    if broken:
        report.add(Result("文档链接", False, f"{len(broken)} 个失效", "；".join(broken[:5])))
    else:
        report.add(Result("文档链接", True, f"检查了 {len(files)} 个文件"))


def check_secrets(report: Report) -> None:
    problems: list[str] = []
    config = ROOT / "weflow.jsonc"
    if config.is_file():
        text = config.read_text(encoding="utf-8")
        token = re.search(r'"access_token"\s*:\s*"([^"]*)"', text)
        if token and token.group(1).strip():
            problems.append("weflow.jsonc 里填了 access_token")

    bot_config = ROOT / "data" / "bot" / "config.json"
    if bot_config.is_file():
        try:
            payload = json.loads(bot_config.read_text(encoding="utf-8"))
            if str(payload.get("api_key") or "").strip():
                problems.append("data/bot/config.json 里有 api_key")
        except json.JSONDecodeError:
            problems.append("data/bot/config.json 无法解析")

    # 提交范围内扫明显的 Key 形态（排除文档里的示例占位）
    pattern = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b")
    skip_dirs = {".git", "dist", "build", "data", "logs", ".venv", ".build-venv", "vendor", "__pycache__"}
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".jsonc", ".ps1", ".sh", ".yml", ".html", ".toml"}:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            hits.append(str(path.relative_to(ROOT)))
    if hits:
        problems.append(f"疑似密钥：{', '.join(hits[:5])}")

    if problems:
        report.add(Result("敏感信息", False, "；".join(problems), "把 Key 从文件里删掉，改用环境变量"))
    else:
        report.add(Result("敏感信息", True, "未发现 Token / API Key"))


def check_gitignore(report: Report) -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    need = ["data/", "logs/", "dist/", "build/", "weflow.jsonc", "vendor/WeClone/settings.jsonc", ".venv/"]
    missing = [item for item in need if item not in text]
    if missing:
        report.add(Result("忽略规则", False, f"缺少：{', '.join(missing)}"))
    else:
        report.add(Result("忽略规则", True, "数据 / 产物 / 密钥文件均已忽略"))


def check_workflows(report: Report) -> None:
    for name in ("ci.yml", "release.yml"):
        path = ROOT / ".github" / "workflows" / name
        if not path.is_file():
            report.add(Result(f"workflow {name}", False, "文件不存在"))
            continue
        text = path.read_text(encoding="utf-8")
        problems: list[str] = []
        if "runs-on:" not in text:
            problems.append("缺 runs-on")
        if "actions/checkout" not in text:
            problems.append("缺 checkout")
        if "setup-python" not in text:
            problems.append("缺 setup-python")
        if name == "release.yml":
            for token in ("windows-latest", "macos-13", "macos-14", "upload-artifact", "action-gh-release"):
                if token not in text:
                    problems.append(f"缺 {token}")
        parsed = _yaml_check(path)
        if parsed:
            problems.append(parsed)
        if problems:
            report.add(Result(f"workflow {name}", False, "；".join(problems)))
        else:
            report.add(Result(f"workflow {name}", True, "结构检查通过"))


def _yaml_check(path: Path) -> str:
    """有 PyYAML 就真解析；没有就做缩进与冒号的基础校验。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        text = path.read_text(encoding="utf-8")
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if line.startswith("\t"):
                return f"第 {index} 行用了 Tab 缩进（YAML 不允许）"
        return ""
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"YAML 解析失败：{exc}"


def check_packaging_assets(report: Report) -> None:
    spec = (ROOT / "packaging" / "wfc.spec").read_text(encoding="utf-8")
    problems: list[str] = []
    if "weflow_clone/bot/web" not in spec and "bot\" / \"web" not in spec:
        problems.append("spec 没有包含 bot/web 界面资源")
    if "COLLECT" not in spec:
        problems.append("spec 不是 onedir（缺 COLLECT）")
    if "excludes" not in spec:
        problems.append("spec 没有 excludes，体积会爆炸")
    icon = ROOT / "packaging" / "icon.ico"
    if not icon.is_file():
        problems.append("缺少 packaging/icon.ico（先跑 make_icons.py）")
    if problems:
        report.add(Result("打包资源", False, "；".join(problems)))
    else:
        report.add(Result("打包资源", True, "界面资源 / onedir / 图标齐全"))


def check_source_only(report: Report) -> None:
    """仓库是「源码仓库」：不能把构建产物提交进去。

    约束：被 git 跟踪的文件里不能有 exe/dll/zip/dmg，单个文件不超过 2MB
    （图标、vendor 里的小资源除外）。
    """
    if not (ROOT / ".git").exists():
        report.add(Result("源码仓库", False, "还没有 git 仓库，无法判断", "先 git init 并提交", blocking=False))
        return

    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True, check=True
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError) as exc:
        report.add(Result("源码仓库", False, f"git 调用失败：{exc}", blocking=False))
        return

    tracked = [item for item in listed.split("\0") if item]
    binaries = [
        item
        for item in tracked
        if Path(item).suffix.lower() in {".exe", ".dll", ".so", ".dylib", ".zip", ".dmg", ".msi"}
    ]
    oversized: list[str] = []
    for item in tracked:
        path = ROOT / item
        try:
            if path.is_file() and path.stat().st_size > 2 * 1024 * 1024:
                oversized.append(f"{item}({path.stat().st_size // 1024}KB)")
        except OSError:
            continue

    problems: list[str] = []
    if binaries:
        problems.append(f"提交了二进制：{', '.join(binaries[:3])}")
    if oversized:
        problems.append(f"超过 2MB：{', '.join(oversized[:3])}")
    if problems:
        report.add(Result("源码仓库", False, "；".join(problems), "产物应由 Releases 分发，不要提交进仓库"))
    else:
        report.add(Result("源码仓库", True, f"{len(tracked)} 个文件，无构建产物"))


def check_tag_version(report: Report, version: str) -> None:
    if not (ROOT / ".git").exists():
        report.add(Result("tag 与版本", False, "没有 git 仓库，跳过", blocking=False))
        return
    if not version:
        report.add(Result("tag 与版本", False, "版本号未知", blocking=False))
        return
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        report.add(Result("tag 与版本", False, f"git 调用失败：{exc}", blocking=False))
        return

    tag = (described.stdout or "").strip()
    if described.returncode != 0 or not tag:
        report.add(
            Result(
                "tag 与版本",
                False,
                f"当前提交没有 tag（版本 {version}）",
                f"发版时执行：git tag v{version} && git push origin v{version}",
                blocking=False,
            )
        )
        return
    if tag.lstrip("v") != version:
        report.add(
            Result(
                "tag 与版本",
                False,
                f"tag {tag} 与版本 {version} 不一致",
                "用 scripts/bump_version.py 升版本，再重新打 tag",
            )
        )
    else:
        report.add(Result("tag 与版本", True, tag))


# --------------------------------------------------------------------------- #
# 动态检查
# --------------------------------------------------------------------------- #
def run_tests(report: Report) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stderr or "") + (proc.stdout or "")
    match = re.search(r"Ran (\d+) tests?", output)
    count = int(match.group(1)) if match else 0
    ok = proc.returncode == 0 and count >= TESTS_MIN
    detail = f"{count} 项" if count else "未识别到测试数量"
    if not ok:
        tail = "\n".join(output.strip().splitlines()[-12:])
        report.add(Result("测试", False, detail, tail))
    else:
        report.add(Result("测试", True, f"{detail}全部通过"))


def check_artifacts(report: Report, notes: dict) -> None:
    if os.name == "nt":
        exe = ROOT / "dist" / "WeFlowClone" / "WeFlowClone.exe"
        archive = ROOT / "dist" / "WeFlowClone-1.0.0-win64.zip"
    else:
        exe = ROOT / "dist" / "WeFlowClone" / "WeFlowClone"
        archive = ROOT / "dist" / ("WeFlowClone-1.0.0-macos-arm64.dmg")
    if not exe.is_file():
        report.add(Result("构建产物", False, f"没找到 {exe.relative_to(ROOT)}", "先运行 packaging/build.ps1（或 build.sh）"))
        return

    smoke = subprocess.run([str(exe), "version"], capture_output=True, text=True, timeout=120)
    version_ok = smoke.returncode == 0 and "WeFlowClone" in (smoke.stdout or "")
    report.add(
        Result(
            "产物可运行",
            version_ok,
            (smoke.stdout or smoke.stderr or "").strip()[:80],
            "用 dist 里的 exe 跑 `version` 失败，检查打包是否完整",
        )
    )

    if archive.is_file():
        digest = _sha256(archive)
        notes["archive"] = archive.name
        notes["sha256"] = digest
        notes["size_mb"] = round(archive.stat().st_size / 1024 / 1024, 1)
        report.add(Result("分发压缩包", True, f"{archive.name}（{notes['size_mb']} MB）"))
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
            has_exe = any(name.endswith("WeFlowClone.exe") for name in names)
            report.add(
                Result(
                    "压缩包内容",
                    has_exe,
                    f"{len(names)} 个条目" + ("，含 exe" if has_exe else "，缺 exe"),
                )
            )
    else:
        report.add(Result("分发压缩包", False, f"没找到 {archive.name}", "重新构建以生成 zip/dmg", blocking=False))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(report: Report) -> None:
    script = ROOT / ("packaging" / "build.ps1" if os.name == "nt" else "packaging" / "build.sh")
    command = (
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if os.name == "nt"
        else ["bash", str(script)]
    )
    proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=3600)
    tail = "\n".join(((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-6:])
    report.add(Result("重新构建", proc.returncode == 0, "成功" if proc.returncode == 0 else tail))


# --------------------------------------------------------------------------- #
# 发布物料
# --------------------------------------------------------------------------- #
def write_release_materials(version: str, notes: dict, report: Report) -> None:
    folder = ROOT / "release"
    folder.mkdir(exist_ok=True)

    lines = [f"# WeFlowClone {version} 发布说明", ""]
    lines.append("## 下载")
    lines.append("")
    lines.append("| 平台 | 文件 | 说明 |")
    lines.append("| --- | --- | --- |")
    if os.name == "nt":
        lines.append(f"| Windows 10/11 x64 | `{notes.get('archive', 'WeFlowClone-win64.zip')}` | 解压后双击 `WeFlowClone\\WeFlowClone.exe` |")
    else:
        lines.append("| macOS Intel / Apple Silicon | `WeFlowClone-<ver>-macos-<arch>.dmg` | 拖进「应用程序」 |")
    lines.append("")
    lines.append("## 三步用起来")
    lines.append("")
    lines.append("1. 解压 / 安装后启动，浏览器会打开 `http://127.0.0.1:8765`；")
    lines.append("2. 点右上角「设置」，填 API 地址、API Key、模型名，点「测试连接」；")
    lines.append("3. 保存后即可聊天。")
    lines.append("")
    lines.append("> 想让它像你：装好 WeFlow 并开启 API 服务，设置 `WEFLOW_TOKEN` 后运行")
    lines.append("> `WeFlowClone pull`，再 `WeFlowClone bot --reindex`。")
    lines.append("")
    lines.append("## 本次校验")
    lines.append("")
    lines.append(f"* 测试：{_pick(report, '测试')}")
    lines.append(f"* 产物：{_pick(report, '产物可运行')}")
    lines.append(f"* 包体：{_pick(report, '分发压缩包')}")
    if notes.get("sha256"):
        lines.append("")
        lines.append("## 校验和")
        lines.append("")
        lines.append("```")
        lines.append(f"{notes['sha256']}  {notes.get('archive', '')}")
        lines.append("```")
    lines.append("")
    lines.append("## 许可提醒")
    lines.append("")
    lines.append("本项目以 AGPL-3.0 发布（含 WeClone），并对接 CC BY-NC-SA 4.0 的 WeFlow，不可用于商业用途。")
    (folder / "RELEASE_NOTES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if notes.get("sha256"):
        (folder / "SHA256SUMS.txt").write_text(
            f"{notes['sha256']}  {notes.get('archive', '')}\n", encoding="utf-8"
        )
    report.add(Result("发布物料", True, str(folder.relative_to(ROOT))))


def _pick(report: Report, name: str) -> str:
    for item in report.results:
        if item.name == name:
            return item.detail or ("通过" if item.ok else "未通过")
    return "未检查"


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前本地检查")
    parser.add_argument("--no-tests", action="store_true", help="跳过测试")
    parser.add_argument("--no-artifacts", action="store_true", help="跳过构建产物检查")
    parser.add_argument("--build", action="store_true", help="先重新构建再检查")
    args = parser.parse_args(argv)

    report = Report()
    notes: dict = {}
    print("\nWeFlowClone 发布前检查\n" + "=" * 60)

    version = check_versions(report)
    check_files(report)
    check_docs_links(report)
    check_secrets(report)
    check_gitignore(report)
    check_workflows(report)
    check_packaging_assets(report)
    check_source_only(report)
    check_tag_version(report, version)

    if args.build:
        print("\n> 重新构建（可能耗时几分钟）…")
        build(report)

    if not args.no_tests:
        print("> 运行测试…")
        run_tests(report)

    if not args.no_artifacts:
        check_artifacts(report, notes)

    print()
    print(report.render())

    if report.failures:
        print(f"\n✗ {len(report.failures)} 项阻塞，先修复再发布")
        return 1

    write_release_materials(version, notes, report)
    print("\n✓ 全部通过，可以发布")
    print(f"  发布物料：{(ROOT / 'release').relative_to(ROOT)}/")
    print("  下一步：")
    print("    git add -A && git commit -m \"release: v" + version + "\"")
    print("    git tag v" + version)
    print("    git push origin main --tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
