"""API 请求/响应模型。

FastAPI 用这些模型做参数校验和序列化；from_attributes 表示可以直接
返回 SQLAlchemy 的 ORM 对象，不用手动转 dict。
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class SymbolInfo(BaseModel):
    symbol: str
    name: str
    exchange: str = ""
    mult: float


class ParamsUpsert(BaseModel):
    """PUT /params 的请求体；参数内容由策略自己解释。"""

    params: dict


class ParamsOut(BaseModel):
    strategy: str
    symbol: str
    params: dict
    updated_at: datetime

    model_config = {"from_attributes": True}


class SignalOut(BaseModel):
    id: int
    trade_date: date
    strategy: str
    symbol: str
    direction: str
    price: float | None
    mp: float | None
    stop: float | None
    qty: int | None
    reason: str | None

    model_config = {"from_attributes": True}


class ExpiryEvent(BaseModel):
    """期权日历的一条到期记录；同一个品种按到期日去重。"""

    symbol: str
    name: str
    exchange: str
    expiry: str
    contract: str | None = None
    last_date: str | None = None


class ScreenOption(BaseModel):
    """筛选元信息里的下拉选项。"""

    id: str
    label: str


class ScreenIndicator(ScreenOption):
    """可用指标；range 表示价格区间，value 表示单值指标。"""

    kind: Literal["range", "value"]


class ScreenBehavior(ScreenOption):
    """可用行为；kinds 决定能搭配哪类指标。"""

    kinds: list[str]


class ScreenMeta(BaseModel):
    """条件构建器的全部选项。"""

    windows: list[int]
    aggregates: list[ScreenOption]
    indicators: list[ScreenIndicator]
    behaviors: list[ScreenBehavior]


class ScreenTarget(BaseModel):
    """条件目标：另一个指标，或固定数值。"""

    type: Literal["indicator", "value"]
    value: str | float


class ScreenCondition(BaseModel):
    """一个原子条件：窗口 + 左指标 + 行为 + 目标 + 聚合方式。"""

    window: int = Field(default=3, ge=1, le=60)
    left: str
    behavior: str
    target: ScreenTarget
    aggregate: Literal["any", "last", "all"] = "any"


class ScreenRequest(BaseModel):
    """筛选请求；conditions 按 combine 组合，symbols 为空表示全部品种。"""

    conditions: list[ScreenCondition] = Field(min_length=1)
    combine: Literal["and", "or"] = "and"
    symbols: list[str] = Field(default_factory=list)


class ScreenMatch(BaseModel):
    """命中筛选条件的品种；note 记录触发依据（日期/指标/价位）。"""

    symbol: str
    name: str
    date: str
    note: str


class NoteCheckIn(BaseModel):
    """新增验证记录：结论 + 说明。"""

    result: Literal["pass", "fail", "observe"]
    comment: str = ""


class NoteCheckOut(NoteCheckIn):
    id: int
    checked_at: datetime

    model_config = {"from_attributes": True}


class NoteIn(BaseModel):
    """新建/编辑笔记的请求体。"""

    title: str = Field(min_length=1, max_length=200)
    kind: Literal["knowledge", "case", "experience"] = "knowledge"
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    symbol: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["pending", "verified", "invalid"] = "pending"


class NoteOut(NoteIn):
    id: int
    review_stage: int
    next_review: date | None
    created_at: datetime
    updated_at: datetime
    checks: list[NoteCheckOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class NoteReviewIn(BaseModel):
    """回顾结果：记住则拉长间隔，模糊则重置到 1 天。"""

    remembered: bool
