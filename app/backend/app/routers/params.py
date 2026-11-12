"""策略参数读写。

存储键是 (strategy, symbol)，参数内容整体作为一个 JSON 保存，
前端可以按策略自由增减字段，不需要改表结构。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StrategyParam
from ..schemas import ParamsOut, ParamsUpsert

router = APIRouter(prefix="/params", tags=["params"])


@router.get("/{strategy}", response_model=list[ParamsOut])
def list_params(strategy: str, db: Session = Depends(get_db)):
    """列出某个策略下所有品种已保存的参数。"""
    stmt = (
        select(StrategyParam)
        .where(StrategyParam.strategy == strategy)
        .order_by(StrategyParam.symbol)
    )
    return db.scalars(stmt).all()


@router.get("/{strategy}/{symbol}", response_model=ParamsOut | None)
def get_params(strategy: str, symbol: str, db: Session = Depends(get_db)):
    """读取单品种参数；没保存过时返回 null，前端用默认值。"""
    stmt = select(StrategyParam).where(
        StrategyParam.strategy == strategy,
        StrategyParam.symbol == symbol.upper(),
    )
    return db.scalar(stmt)


@router.put("/{strategy}/{symbol}", response_model=ParamsOut)
def upsert_params(
    strategy: str,
    symbol: str,
    body: ParamsUpsert,
    db: Session = Depends(get_db),
):
    """保存参数：存在则覆盖，不存在则插入。"""
    normalized = symbol.upper()
    stmt = select(StrategyParam).where(
        StrategyParam.strategy == strategy,
        StrategyParam.symbol == normalized,
    )
    row = db.scalar(stmt)
    if row is None:
        row = StrategyParam(
            strategy=strategy, symbol=normalized, params=body.params
        )
        db.add(row)
    else:
        # 合并而不是整体替换：保留表单未展示的字段（EWMA/斜率/波段等）
        row.params = {**(row.params or {}), **body.params}
    db.commit()
    db.refresh(row)
    return row
