"""策略包。

导入 maxpain_v5 会触发 @register，把策略实例放进 registry。
以后回测、每日扫描、实盘执行都应只依赖这里的接口，保证三处逻辑一致。
"""

from .base import Bar, SignalResult, Strategy, register, registry
from . import maxpain_v5  # noqa: F401  导入即注册

__all__ = ["Bar", "SignalResult", "Strategy", "register", "registry"]
