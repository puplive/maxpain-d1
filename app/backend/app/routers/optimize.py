"""参数网格优化入口。

POST /api/optimize/{symbol}：在已保存参数（回退默认值）上对给定网格
做笛卡尔积扫描，按期末权益排序返回前 N 组。计算量较大，接口同步返回，
组合数由策略核心限制在 10 万以内，且组合多时自动走多进程并行。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StrategyParam
from ..services.data_source import load_history, load_symbols
from ..strategy import Bar, registry
from .symbols import SYMBOL_RE

router = APIRouter(prefix="/optimize", tags=["optimize"])

STRATEGY = "maxpain_v5"


class OptimizeRequest(BaseModel):
    start: date | None = None
    end: date | None = None
    # 参数名 → 候选值列表（原始单位，百分比字段为小数）
    grid: dict[str, list[float]] = Field(default_factory=dict)


def _to_bars(rows: list[dict]) -> list[Bar]:
    """JSON 行转策略 Bar；与回测入口一致。"""
    return [
        Bar(
            d=str(row["d"]),
            o=float(row["o"]),
            h=float(row["h"]),
            l=float(row["l"]),
            c=float(row["c"]),
            mp=float(row["mp"]),
            expiry=row.get("expiry"),
            fut_contract=row.get("fut_contract"),
        )
        for row in rows
    ]


@router.post("/{symbol}")
def optimize_params(
    symbol: str,
    payload: OptimizeRequest,
    db: Session = Depends(get_db),
) -> dict:
    """按品种做参数网格扫描；基准参数取保存值，回退默认值。"""
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    symbol = symbol.upper()

    info = next(
        (item for item in load_symbols() if item["symbol"] == symbol), None
    )
    if info is None:
        raise HTTPException(
            status_code=404, detail=f"symbol {symbol} not found"
        )

    rows = load_history(symbol)
    if len(rows) < 30:
        raise HTTPException(status_code=400, detail="历史数据不足")

    strategy = registry[STRATEGY]
    saved = db.scalar(
        select(StrategyParam).where(
            StrategyParam.strategy == STRATEGY,
            StrategyParam.symbol == symbol,
        )
    )
    params = {**strategy.default_params, **(saved.params if saved else {})}
    params["mult"] = info["mult"]

    try:
        result = strategy.optimize(
            _to_bars(rows),
            params,
            payload.grid,
            start=payload.start.isoformat() if payload.start else None,
            end=payload.end.isoformat() if payload.end else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["symbol"] = symbol
    return result
