"""扫描入口。

遍历 web/data 里的全部品种，调用策略核心计算次日信号，
把可交易的结果写入 daily_signals。同一天重复扫描会覆盖旧结果，
方向/过滤条件变化后不再成立的信号会删除，保证表里始终是最新扫描状态。
"""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DailySignal, StrategyParam
from ..services.data_source import load_history, load_symbols
from ..strategy import Bar, registry

router = APIRouter(tags=["scan"])

STRATEGY = "maxpain_v5"


def _to_bars(rows: list[dict]) -> list[Bar]:
    """把 JSON 行转成策略需要的 Bar。"""
    return [
        Bar(
            d=str(row["d"]),
            o=float(row["o"]),
            h=float(row["h"]),
            l=float(row["l"]),
            c=float(row["c"]),
            mp=float(row["mp"]),
            expiry=row.get("expiry"),
        )
        for row in rows
    ]


@router.post("/scan")
def run_scan(db: Session = Depends(get_db)) -> dict:
    strategy = registry[STRATEGY]

    # 一次读出该策略已保存的参数，避免循环里逐个查库
    saved_params = {
        row.symbol: row.params or {}
        for row in db.scalars(
            select(StrategyParam).where(StrategyParam.strategy == STRATEGY)
        )
    }

    scanned = 0
    long_count = 0
    short_count = 0

    for info in load_symbols():
        symbol = info["symbol"]
        rows = load_history(symbol)
        if len(rows) < 2:
            continue

        # 品种参数覆盖策略默认值；mult 用于保证金和手数计算
        params = {**strategy.default_params, **saved_params.get(symbol, {})}
        params["mult"] = info["mult"]
        result = strategy.scan(_to_bars(rows), params)

        trade_date = date.fromisoformat(rows[-1]["d"])
        existing = db.scalar(
            select(DailySignal).where(
                DailySignal.trade_date == trade_date,
                DailySignal.strategy == STRATEGY,
                DailySignal.symbol == symbol,
            )
        )

        if result.direction in ("long", "short"):
            # 可重复扫描：同键已存在就更新，否则插入
            if existing is None:
                existing = DailySignal(
                    trade_date=trade_date,
                    strategy=STRATEGY,
                    symbol=symbol,
                )
                db.add(existing)
            existing.direction = result.direction
            existing.price = result.price
            existing.mp = rows[-1].get("mp")
            existing.stop = result.stop
            existing.qty = result.qty
            existing.reason = result.reason
            existing.payload = {}
            if result.direction == "long":
                long_count += 1
            else:
                short_count += 1
        elif existing is not None:
            # 参数或过滤条件变化后旧信号不再成立
            db.delete(existing)

        scanned += 1

    db.commit()
    return {
        "message": f"扫描完成：{scanned} 个品种，多头 {long_count}、空头 {short_count}",
        "scanned": scanned,
        "long": long_count,
        "short": short_count,
    }
