"""校验 tag 与代码里的版本一致（CI 用）。

为什么单独一个脚本，而不是写在 workflow 的 heredoc 里：
heredoc 在 Windows runner 上受 shell / 编码影响，出问题很难查；
脚本可以用 unittest 或本地直接跑，行为完全一致。

用法（CI 会自动设置 GITHUB_REF_NAME）：

    python scripts/verify_tag.py            # tag 必须等于 pyproject 的 version
    python scripts/verify_tag.py v1.2.3     # 显式指定
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def code_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 里找不到 version")
    return match.group(1)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tag = argv[0] if argv else os.environ.get("GITHUB_REF_NAME", "")
    tag = tag.strip()
    if not tag:
        print("没有拿到 tag：本地运行时可以显式传入，例如 python scripts/verify_tag.py v1.0.0")
        return 2

    version = code_version()
    normalized = tag[1:] if tag.startswith("v") else tag
    print(f"tag={tag}  code={version}")
    if normalized != version:
        print(
            f"[x] tag 与代码版本不一致。\n"
            f"    先运行：python scripts/bump_version.py {normalized}\n"
            f"    再提交并重新打 tag。",
            file=sys.stderr,
        )
        return 1
    print("[ok] 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
