"""数据库表定义。

- strategy_params: 每个 (策略, 品种) 一条参数记录，参数整体存 JSON
- daily_signals: 每日扫描结果，按 (交易日, 策略, 品种) 唯一
- notes: 研究笔记（知识点/案例/经验），带状态和间隔回顾
- note_checks: 每次验证的结论记录，挂在笔记下

历史行情不落库，仍从 web/data/*.json 读取。
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class StrategyParam(Base):
    __tablename__ = "strategy_params"
    # 同一策略下每个品种只保留一条参数，后续加用户时再加上 user_id
    __table_args__ = (
        UniqueConstraint("strategy", "symbol", name="uq_strategy_param"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    # 参数整体存 JSON：不同策略参数不同，避免为每个参数加一列
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # 首次创建自动填当前时间，每次更新时自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class DailySignal(Base):
    """每日扫描结果，前端“今日机会”页直接查这张表。"""

    __tablename__ = "daily_signals"
    # 同一天、同一策略、同一品种只保留一条，扫描任务可重复执行（幂等）
    __table_args__ = (
        UniqueConstraint("trade_date", "strategy", "symbol", name="uq_daily_signal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    # long / short
    direction: Mapped[str] = mapped_column(String(8))
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mp: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 被过滤时记录原因（波动率、价格不同侧、连胜跳过等）
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 预留：指标快照、过滤器明细，方便复盘
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Note(Base):
    """研究笔记：知识点 / 案例 / 经验，配验证记录和间隔回顾。"""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    # knowledge 知识点 / case 案例 / experience 经验
    kind: Mapped[str] = mapped_column(String(16), index=True, default="knowledge")
    content: Mapped[str] = mapped_column(Text, default="")
    # 标签整体存 JSON 数组，筛选在前端做
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # 可选关联品种和案例日期区间，方便跳回测对照
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # pending 待验证 / verified 已验证 / invalid 已失效
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    # 间隔回顾下标，对应 REVIEW_INTERVALS；next_review 是下次回顾日期
    review_stage: Mapped[int] = mapped_column(Integer, default=0)
    next_review: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    checks: Mapped[list["NoteCheck"]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        order_by="NoteCheck.checked_at.desc()",
    )


class NoteCheck(Base):
    """验证记录：每次复查结论追加一条，不覆盖历史。"""

    __tablename__ = "note_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(
        ForeignKey("notes.id", ondelete="CASCADE"), index=True
    )
    # pass 通过 / fail 未通过 / observe 待观察
    result: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    note: Mapped[Note] = relationship(back_populates="checks")
