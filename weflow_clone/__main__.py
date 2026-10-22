"""``python -m weflow_clone`` 等价于 ``wfc``。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
