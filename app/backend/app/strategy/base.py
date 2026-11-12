"""策略核心接口。

实现新策略只需：
1. 继承/实现 Strategy 协议（key、name、default_params、scan）
2. 用 @register 注册

回测、扫描、实盘都调用同一个 scan()，避免多套逻辑漂移。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class Bar:
    """一根日线；字段名和前端 JSON 保持一致。

    expiry 是期权到期日：换月后 MP 会跳空，方向窗口需要做复权处理。
    """

    d: str
    o: float
    h: float
    l: float
    c: float
    mp: float
    expiry: str | None = None
    # 期货主力合约：换月当天旧仓按昨收平掉、新仓按今开重开
    fut_contract: str | None = None


@dataclass(slots=True)
class SignalResult:
    """扫描结果。

    direction 为 "none" 表示没有机会；此时也可以带上 reason 说明被过滤原因。
    """

    direction: str  # "long" | "short" | "none"
    price: float | None = None
    stop: float | None = None
    qty: int | None = None
    reason: str = ""


class Strategy(Protocol):
    key: str
    name: str
    default_params: dict

    def scan(self, bars: list[Bar], params: dict) -> SignalResult:
        """输入完整历史（至少覆盖指标预热期），返回当前交易信号。"""
        ...

    def backtest(
        self,
        bars: list[Bar],
        params: dict,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """逐日撮合回测，返回交易明细、权益曲线与统计指标。"""
        ...


# 策略注册表：key -> 策略实例
registry: dict[str, Strategy] = {}


def register(cls):
    """类装饰器：实例化策略并登记到 registry。"""
    registry[cls.key] = cls()
    return cls
