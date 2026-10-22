"""WeFlowClone —— 以 WeFlow 为数据底座、WeClone 为训练引擎的数字分身工作站。

对外的两层：

* ``wfc`` 命令行（``weflow_clone.cli``）
* ``weflow_clone.weflow``（WeFlow API 客户端 + 格式映射）
* ``weflow_clone.pipeline``（WeClone 管线封装 / 数据集校验 / 自检）
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
