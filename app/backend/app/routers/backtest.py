"""回测入口。

GET /api/backtest/{symbol}：读取该品种全部历史，用已保存的优化参数
跑逐日撮合，返回交易明细、权益曲线和统计指标。
POST /api/backtest/{symbol}：同上，但请求体可带 params 覆盖保存值，
供回测页手动微调，覆盖值只用于本次计算，不写库。
start/end 只限制开仓区间，指标预热仍用全量历史。
"""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StrategyParam
from ..services.data_source import load_chart_history, load_symbols
from ..services.derivatives import compute_derivatives
from ..services.subchart import compute_subchart
from ..strategy import Bar, registry
from .symbols import SYMBOL_RE

router = APIRouter(prefix="/backtest", tags=["backtest"])

STRATEGY = "maxpain_v5"


def _to_bars(rows: list[dict]) -> list[Bar]:
    """把 JSON 行转成策略需要的 Bar，包含换月判断用的期货合约。"""
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


class BacktestRequest(BaseModel):
    """POST 回测请求；params 为原始单位（百分比字段为小数）。"""

    start: date | None = None
    end: date | None = None
    params: dict[str, float | int | str] = Field(default_factory=dict)


def _run_backtest(
    symbol: str,
    start: date | None,
    end: date | None,
    overrides: dict | None,
    db: Session,
) -> dict:
    """按品种跑回测；参数优先用保存值，overrides 仅覆盖本次计算。"""
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

    rows = load_chart_history(symbol)
    if len(rows) < 30:
        raise HTTPException(status_code=400, detail="历史数据不足")

    strategy = registry[STRATEGY]
    saved = db.scalar(
        select(StrategyParam).where(
            StrategyParam.strategy == STRATEGY,
            StrategyParam.symbol == symbol,
        )
    )
    params = {
        **strategy.default_params,
        **(saved.params if saved else {}),
        **(overrides or {}),
    }
    # 合约乘数由品种信息决定，不允许前端覆盖
    params["mult"] = info["mult"]

    try:
        result = strategy.backtest(
            _to_bars(rows),
            params,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["symbol"] = symbol
    # 复盘用数据：K 线、BEC/BEP、到期/合约信息和本次实际生效的参数
    result["bars"] = [
        {
            "d": str(row["d"]),
            "o": float(row["o"]),
            "h": float(row["h"]),
            "l": float(row["l"]),
            "c": float(row["c"]),
            "mp": float(row["mp"]),
            "bec": float(row["bec"]) if row.get("bec") is not None else None,
            "bep": float(row["bep"]) if row.get("bep") is not None else None,
            "expiry": row.get("expiry"),
            "dte": row.get("dte"),
            "fut_contract": row.get("fut_contract"),
        }
        for row in rows
    ]
    # 衍生线按品种+带宽缓存，和 bars 下标一一对应
    result["derivatives"] = compute_derivatives(symbol, params.get("band", 20))
    # 下方附图指标，ATR/噪音周期取本次生效参数
    result["subchart"] = compute_subchart(
        symbol,
        params.get("band", 20),
        params.get("atrPeriod", 5),
        params.get("lookback", 10),
    )
    result["params"] = params
    return result


@router.get("/{symbol}")
def run_backtest(
    symbol: str,
    start: date | None = Query(None),
    end: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """按品种跑回测；参数优先用 strategy_params 里保存的优化值。"""
    return _run_backtest(symbol, start, end, None, db)


@router.post("/{symbol}")
def run_backtest_with_overrides(
    symbol: str,
    payload: BacktestRequest,
    db: Session = Depends(get_db),
) -> dict:
    """回测页手动微调：请求体参数覆盖保存值，不写回 strategy_params。"""
    return _run_backtest(symbol, payload.start, payload.end, payload.params, db)


# OI 分布图只需要画图字段，其余列（pvol/pclose 等）不往外传
_CHAIN_FIELDS = ("s", "co", "po", "cd", "pd", "cdw", "pdw", "civ", "piv")


@router.get("/{symbol}/chain")
def get_chain(symbol: str, date: str = Query(...)) -> list[dict]:
    """返回某一天的期权链，供附图2（OI 分布）使用。

    web/data 里 oc_chain 是 JSON 字符串，字段随交易所不同：
    DCE/SHFE 有 cd/pd，CZCE 只有 cdw/pdw；统一成固定键，缺失给 null。
    """
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    symbol = symbol.upper()
    rows = load_chart_history(symbol)
    row = next((item for item in rows if str(item["d"]) == date), None)
    if row is None:
        raise HTTPException(status_code=404, detail="date not found")

    raw = row.get("oc_chain")
    if not raw:
        return []
    chain = json.loads(raw) if isinstance(raw, str) else raw
    return [
        {key: item.get(key) for key in _CHAIN_FIELDS}
        for item in chain
        if isinstance(item, dict)
    ]
