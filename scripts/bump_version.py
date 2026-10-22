"""把版本号从 1.0.0 升到 1.0.1 这种事，交给脚本做。

版本号在本项目里出现在 5 个地方，手改必漏一个：

| 文件 | 作用 |
| --- | --- |
| `pyproject.toml` | 包元数据、PyPI / 工具链读取 |
| `weflow_clone/__init__.py` | 运行时 `__version__`、`wfc version` |
| `packaging/build.ps1` | Windows 产物命名 |
| `packaging/build.sh` | macOS 产物命名 |
| `packaging/wfc.spec` | macOS `.app` 的 `CFBundleShortVersionString` |

外加文档里的示例文件名（`WeFlowClone-1.0.0-win64.zip`）。

用法：

    python scripts/bump_version.py 1.1.0          # 改文件
    python scripts/bump_version.py 1.1.0 --dry-run # 只看会改什么
    python scripts/bump_version.py 1.1.0 --tag     # 改完顺便提交并打 tag
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def read_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 里找不到 version")
    return match.group(1)


def rewrite(path: Path, pattern: str, replacement: str) -> int:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count and new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return count


def bump(new_version: str, *, dry_run: bool = False) -> list[tuple[str, int]]:
    old = read_version()
    changes: list[tuple[str, int]] = []

    targets = [
        (ROOT / "pyproject.toml", r'^version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        (
            ROOT / "weflow_clone" / "__init__.py",
            r'__version__\s*=\s*"[^"]+"',
            f'__version__ = "{new_version}"',
        ),
        (ROOT / "packaging" / "build.ps1", r'\$Version\s*=\s*"[^"]+"', f'$Version = "{new_version}"'),
        (ROOT / "packaging" / "build.sh", r'VERSION="[^"]+"', f'VERSION="{new_version}"'),
        (
            ROOT / "packaging" / "wfc.spec",
            r'"CFBundleShortVersionString":\s*"[^"]+"',
            f'"CFBundleShortVersionString": "{new_version}"',
        ),
        (
            ROOT / "packaging" / "wfc.spec",
            r'"CFBundleVersion":\s*"[^"]+"',
            f'"CFBundleVersion": "{new_version}"',
        ),
    ]

    for path, pattern, replacement in targets:
        if not path.is_file():
            continue
        if dry_run:
            text = path.read_text(encoding="utf-8")
            count = len(re.findall(pattern, text, flags=re.MULTILINE))
        else:
            count = rewrite(path, pattern, replacement)
        if count:
            changes.append((str(path.relative_to(ROOT)), count))

    # 文档里的示例产物名：WeFlowClone-1.0.0-win64.zip 之类
    doc_pattern = re.compile(r"WeFlowClone-\d+\.\d+\.\d+-")
    for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not doc_pattern.search(text):
            continue
        new_text = doc_pattern.sub(f"WeFlowClone-{new_version}-", text)
        if new_text != text:
            if not dry_run:
                path.write_text(new_text, encoding="utf-8")
            changes.append((str(path.relative_to(ROOT)), 1))

    print(f"版本：{old} → {new_version}" + ("（dry-run，未写入）" if dry_run else ""))
    for name, count in changes:
        print(f"  ✓ {name}（{count} 处）")
    if not changes:
        print("  没有需要修改的地方（可能已经是对应版本）")
    return changes


def git_release(new_version: str) -> int:
    commands = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"release: v{new_version}"],
        ["git", "tag", "-a", f"v{new_version}", "-m", f"WeFlowClone v{new_version}"],
    ]
    for command in commands:
        print("$", " ".join(command))
        result = subprocess.run(command, cwd=str(ROOT))
        if result.returncode != 0:
            return result.returncode
    print("\n下一步：git push origin main --tags")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一升级版本号")
    parser.add_argument("version", help="新版本号，例如 1.1.0")
    parser.add_argument("--dry-run", action="store_true", help="只显示会改哪些文件")
    parser.add_argument("--tag", action="store_true", help="改完自动提交并打 tag")
    args = parser.parse_args(argv)

    if not SEMVER.match(args.version):
        print(f"版本号格式不对：{args.version}（应形如 1.1.0 或 1.1.0-rc.1）", file=sys.stderr)
        return 2

    changes = bump(args.version, dry_run=args.dry_run)
    if args.dry_run or not changes:
        return 0
    if args.tag:
        return git_release(args.version)

    print("\n下一步：")
    print("  python scripts/release_check.py --build")
    print(f'  git commit -am "release: v{args.version}"')
    print(f"  git tag v{args.version}")
    print("  git push origin main --tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
