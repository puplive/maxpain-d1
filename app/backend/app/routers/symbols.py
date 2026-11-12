"""品种列表与日线接口。数据来自现有 web/data/ 静态文件。"""

import re

from fastapi import APIRouter, HTTPException, Query

from ..schemas import SymbolInfo
from ..services.data_source import load_bars, load_symbols

router = APIRouter(prefix="/symbols", tags=["symbols"])

# 品种代码只允许字母数字，避免拼出 ../ 之类的路径
SYMBOL_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")


@router.get("", response_model=list[SymbolInfo])
def list_symbols() -> list[dict]:
    """返回 index.json 里的品种、中文名和合约乘数。"""
    return load_symbols()


@router.get("/{symbol}/bars")
def get_bars(
    symbol: str, limit: int = Query(300, ge=1, le=2000)
) -> list[dict]:
    """返回最近 limit 根轻量日线，供看板/图表使用。"""
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    bars = load_bars(symbol.upper(), limit)
    if not bars:
        raise HTTPException(status_code=404, detail=f"symbol {symbol} not found")
    return bars
