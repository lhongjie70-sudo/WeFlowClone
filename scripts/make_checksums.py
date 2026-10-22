"""给 dist/ 里的 zip / dmg 生成 SHA256SUMS.txt（CI 与本地共用）。

    python scripts/make_checksums.py            # 扫描 dist/
    python scripts/make_checksums.py --dir dist # 指定目录

输出格式与 ``sha256sum`` 一致，方便 mac/Linux 用户校验：

    <hash>  <file>
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

SUFFIXES = {".zip", ".dmg", ".exe", ".tar.gz"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 SHA256SUMS.txt")
    parser.add_argument("--dir", default="dist", help="产物目录（默认 dist）")
    parser.add_argument("--output", default="", help="输出文件（默认 <dir>/SHA256SUMS.txt）")
    args = parser.parse_args(argv)

    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"目录不存在：{folder}", file=sys.stderr)
        return 2

    targets = [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file() and (path.suffix.lower() in SUFFIXES or path.name.endswith(".tar.gz"))
    ]
    if not targets:
        print(f"{folder} 里没有 zip / dmg 可校验", file=sys.stderr)
        return 1

    lines = [f"{sha256(path)}  {path.name}" for path in targets]
    output = Path(args.output) if args.output else folder / "SHA256SUMS.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
