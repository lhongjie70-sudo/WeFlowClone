"""生成应用图标（跨平台）。

* 任何平台都生成 ``icon.png`` 与 Windows 的 ``icon.ico``；
* 在 macOS 上额外用 ``iconutil`` 生成 ``icon.icns``。

图形用几何线条画一个 "W"，不依赖任何字体文件，避免打包机缺字体。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
GREEN = (7, 193, 96)
DARK = (5, 150, 74)
WHITE = (255, 255, 255)


def draw_icon(size: int) -> Image.Image:
    scale = 4  # 先用 4 倍画，再缩小，边缘更干净
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = int(canvas * 0.22)
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=GREEN)
    # 顶部高光，让图标不那么平
    draw.rounded_rectangle(
        [int(canvas * 0.06), int(canvas * 0.06), int(canvas * 0.94), int(canvas * 0.5)],
        radius=int(radius * 0.8),
        fill=(9, 205, 104),
    )

    # 用 4 段折线画 W
    width = max(2, int(canvas * 0.085))
    left = canvas * 0.24
    right = canvas * 0.76
    top = canvas * 0.3
    bottom = canvas * 0.72
    mid = canvas * 0.5
    points = [
        (left, top),
        (left + (mid - left) * 0.55, bottom),
        (mid, canvas * 0.52),
        (mid + (right - mid) * 0.45, bottom),
        (right, top),
    ]
    draw.line(points, fill=WHITE, width=width, joint="curve")

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    images = {size: draw_icon(size) for size in sizes}

    png_path = HERE / "icon.png"
    images[512].save(png_path)
    print(f"写入 {png_path}")

    ico_path = HERE / "icon.ico"
    images[256].save(ico_path, format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"写入 {ico_path}")

    if sys.platform == "darwin":
        iconset = HERE / "icon.iconset"
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir()
        # iconutil 要求的固定命名
        mapping = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
            "icon_512x512@2x.png": 1024,
        }
        for name, size in mapping.items():
            images[size].save(iconset / name)

        icns_path = HERE / "icon.icns"
        result = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"写入 {icns_path}")
        else:
            print(f"iconutil 失败：{result.stderr.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
