"""每日扫描结果查询。"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DailySignal
from ..schemas import SignalOut

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalOut])
def list_signals(
    # 对外参数仍叫 date；内部换个名字，避免和 date 类型混淆
    trade_date: date | None = Query(None, alias="date"),
    strategy: str | None = None,
    db: Session = Depends(get_db),
):
    """按日期/策略筛选扫描结果，默认返回全部（最新在前）。"""
    stmt = select(DailySignal).order_by(
        DailySignal.trade_date.desc(), DailySignal.symbol
    )
    if trade_date is not None:
        stmt = stmt.where(DailySignal.trade_date == trade_date)
    if strategy is not None:
        stmt = stmt.where(DailySignal.strategy == strategy)
    return db.scalars(stmt).all()
