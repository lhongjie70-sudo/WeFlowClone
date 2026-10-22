"""极简终端输出：不引入 rich / colorama，Windows 10+ 直接支持 ANSI。

统一在这里控制颜色，保证 CLI 输出好看但可被 ``NO_COLOR`` 关掉。
"""

from __future__ import annotations

import os
import sys

_COLOR = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(text: str) -> str:
    return _paint("1", text)


def dim(text: str) -> str:
    return _paint("2", text)


def ok(text: str) -> str:
    return _paint("32", text)


def warn(text: str) -> str:
    return _paint("33", text)


def err(text: str) -> str:
    return _paint("31", text)


def info(text: str) -> str:
    return _paint("36", text)


def say(message: str = "") -> None:
    print(message)


def step(message: str) -> None:
    print(f"{info('▶')} {message}")


def success(message: str) -> None:
    print(f"{ok('✓')} {message}")


def warning(message: str) -> None:
    print(f"{warn('!')} {message}")


def failure(message: str) -> None:
    print(f"{err('✗')} {message}", file=sys.stderr)


def table(headers: list[str], rows: list[list[str]], indent: str = "  ") -> None:
    """按内容宽度对齐的简易表格（中文字符按 2 列估算）。"""
    widths = [display_width(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], display_width(cell))
    print(indent + "  ".join(bold(pad(h, widths[i])) for i, h in enumerate(headers)))
    print(indent + "  ".join(dim("─" * width) for width in widths))
    for row in rows:
        print(indent + "  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)))


def display_width(text: str) -> int:
    width = 0
    for char in str(text):
        width += 2 if _is_wide(char) else 1
    return width


def pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - display_width(text))


def truncate(text: str, limit: int = 96) -> str:
    """按显示宽度截断，超出加省略号（避免一行几百字符把表格撑爆）。"""
    text = str(text)
    if display_width(text) <= limit:
        return text
    out: list[str] = []
    width = 0
    for char in text:
        step = 2 if _is_wide(char) else 1
        if width + step > limit - 1:
            break
        out.append(char)
        width += step
    return "".join(out) + "…"


def _is_wide(char: str) -> bool:
    code = ord(char)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
        or 0x1F300 <= code <= 0x1F9FF
    )
