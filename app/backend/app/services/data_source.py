"""历史行情适配层。

数据来源仍是 build_web_data.py 生成的 web/data/*.json，不导入数据库。
- index.json 提供品种列表、中文名、合约乘数
- {symbol}.json 是完整日线，其中 oc_chain 很大，API 只返回轻量字段
"""

import json
from functools import lru_cache

from ..config import settings

# 前端画图和策略扫描需要的字段；oc_chain/GEX 等大字段不往外传
# expiry 用于策略方向计算：期权换月后 MP 跳空，需要复权补齐窗口
LIGHT_FIELDS = (
    "d", "o", "c", "h", "l", "mp", "co", "po", "bec", "bep", "atm_iv", "expiry",
    "fut_contract", "gpk",
)

# 回测复盘额外需要的字段：到期天数、期权标的价和完整期权链
# oc_chain 每天几 KB，只有回测图会用到，不放进策略扫描的轻量缓存；
# gex/vr/ivs 等标量供附图指标直接读取，体积很小
CHART_FIELDS = LIGHT_FIELDS + (
    "dte", "oc", "oc_chain",
    "gex", "vr", "ivs", "oi_total", "fut_vol", "fut_oi", "gam", "veg",
)


def load_symbols() -> list[dict]:
    """读取 index.json，拼成前端需要的 [{symbol, name, exchange, mult}]。"""
    index_path = settings.web_data_dir / "index.json"
    if not index_path.is_file():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    names = payload.get("names", {})
    mults = payload.get("mult", {})
    exchanges = payload.get("exchange", {})
    return [
        {
            "symbol": symbol,
            "name": names.get(symbol, symbol),
            "exchange": exchanges.get(symbol, ""),
            "mult": mults.get(symbol, 1),
        }
        for symbol in payload.get("symbols", [])
    ]


@lru_cache(maxsize=16)
def _load_light(symbol: str, mtime: float) -> tuple[dict, ...]:
    """解析一次完整 JSON，缓存轻量结果。

    mtime 也作为缓存键：文件被重新生成后（修改时间变化）会自动重新读取。
    缓存最多保留 16 个品种，避免把全部历史数据常驻内存。
    """
    path = settings.web_data_dir / f"{symbol}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple({key: row.get(key) for key in LIGHT_FIELDS} for row in payload)


def load_history(symbol: str) -> list[dict]:
    """返回完整日线，供策略扫描/回测使用；品种不存在时返回空列表。"""
    path = settings.web_data_dir / f"{symbol}.json"
    if not path.is_file():
        return []
    return list(_load_light(symbol, path.stat().st_mtime))


@lru_cache(maxsize=2)
def _load_chart(symbol: str, mtime: float) -> tuple[dict, ...]:
    """解析完整 JSON，只保留复盘图所需字段。

    oc_chain 体积大，最多缓存两个品种，够回测页来回切换；
    策略扫描仍走 _load_light，避免全市场期权链常驻内存。
    """
    path = settings.web_data_dir / f"{symbol}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple({key: row.get(key) for key in CHART_FIELDS} for row in payload)


def load_chart_history(symbol: str) -> list[dict]:
    """返回回测复盘用的完整日线（含期权链）；品种不存在时返回空列表。"""
    path = settings.web_data_dir / f"{symbol}.json"
    if not path.is_file():
        return []
    return list(_load_chart(symbol, path.stat().st_mtime))


def load_bars(symbol: str, limit: int) -> list[dict]:
    """返回最近 limit 根日线；品种不存在时返回空列表。"""
    return load_history(symbol)[-limit:]


# 看板筛选只关心最近几根 K 线；完整品种文件含 oc_chain（几十 MB），
# 全量解析会让首屏筛选卡住，所以由 build_web_data.py 预生成 screen.json，
# 每个品种只保留最近 61 根日线的轻量字段（字段与 build 脚本的 SCREEN_FIELDS 一致）
TAIL_FIELDS = (
    "d", "o", "h", "l", "c",
    "mp", "bec", "bep", "gpk", "gex",
    "oi_pcr", "ivs", "vr", "atm_iv",
)


@lru_cache(maxsize=4)
def _load_screen_tail(mtime: float) -> dict[str, tuple[dict, ...]]:
    """解析 screen.json；按文件 mtime 缓存，数据更新后自动失效。"""
    path = settings.web_data_dir / "screen.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {sym: tuple(rows) for sym, rows in payload.items()}


def load_screen_tail(symbol: str, limit: int = 3) -> list[dict]:
    """返回最近 limit 根日线（轻量字段）；品种不存在时返回空列表。"""
    path = settings.web_data_dir / "screen.json"
    if not path.is_file():
        # 兼容旧数据目录：还没有 screen.json 时回退到解析完整文件
        full_path = settings.web_data_dir / f"{symbol}.json"
        if not full_path.is_file():
            return []
        payload = json.loads(full_path.read_text(encoding="utf-8"))
        return [
            {key: row.get(key) for key in TAIL_FIELDS}
            for row in payload[-limit:]
        ]
    rows = _load_screen_tail(path.stat().st_mtime).get(symbol, ())
    return list(rows[-limit:])
