"""异常定义。

设计目标：让 CLI 在任何一层失败时都能给出「用户能照做」的中文提示，
而不是甩一个 urllib 的 traceback。
"""

from __future__ import annotations


class WeFlowCloneError(RuntimeError):
    """本项目的所有可预期错误的基类。"""


class ConfigError(WeFlowCloneError):
    """配置缺失或不合法。"""


class WeFlowError(WeFlowCloneError):
    """WeFlow 相关错误的基类。"""


class WeFlowUnavailable(WeFlowError):
    """连不上 WeFlow（未启动 / 端口不对 / API 服务未开启）。"""


class WeFlowAuthError(WeFlowError):
    """Token 缺失或不正确。"""


class WeFlowApiError(WeFlowError):
    """WeFlow 返回了错误响应。"""

    def __init__(self, message: str, *, status: int = 0, path: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.path = path
