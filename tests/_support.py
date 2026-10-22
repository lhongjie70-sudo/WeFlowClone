"""测试共用的构造器。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 让 `python -m unittest discover -s tests` 能直接 import
    sys.path.insert(0, str(ROOT))

from weflow_clone.config import (  # noqa: E402
    ExportSettings,
    MediaSettings,
    ServerSettings,
    Settings,
    WatchSettings,
    WeCloneSettings,
    WeFlowSettings,
)


def make_settings(
    root: Path,
    *,
    include_types: list[str] | None = None,
    max_messages_per_file: int = 20000,
    download_media: bool = True,
    base_url: str = "http://127.0.0.1:5031",
    token: str = "",
    page_size: int = 100,
) -> Settings:
    root.mkdir(parents=True, exist_ok=True)
    return Settings(
        weflow=WeFlowSettings(
            base_url=base_url,
            access_token=token,
            timeout_seconds=10,
            retries=0,
            page_size=page_size,
        ),
        export=ExportSettings(
            data_dir=str(root / "data"),
            include_types=include_types or ["text", "image", "file"],
            max_messages_per_file=max_messages_per_file,
            media=MediaSettings(download=download_media, images=True),
        ),
        weclone=WeCloneSettings(),
        server=ServerSettings(),
        watch=WatchSettings(),
        root=root,
        source=root / "weflow.jsonc",
    )
