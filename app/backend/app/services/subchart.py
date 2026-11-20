"""回测附图指标。

逐日序列供复盘图下方附图使用，口径沿用旧 web/index.html 的 #subChart：
- gex / 归一化 gex、OI 偏度与偏度差、OI 净差、量比、IV 偏度、期货成交量/持仓量
- ATR / TR（Wilder 平滑，周期取 atrPeriod）
- MP 置信、净对冲（ΣΔ×OI）、DEX 方向
- Vanna / Charm 流（只统计显示带内有 IV 的合约；有按月链 ocm 时逐月用各自 dte）
- 再平衡流量 gflow：ΣΓ×OI×S×当日 ATR 波幅，即按当日实际波动预期需要的对冲手数；
  到期周 Gamma 占比 gexp：dte≤7 的月份 Γ×OI 占全部月份比例
- 行权量 exv：转换脚本写入的当日合计行权量（CZCE/DCE/GFEX 有，SHFE/CFFEX 为 0）
- 被动压力 liqp：期货持仓量日减幅(%)×当日涨跌幅(%)，只取减仓日、按涨跌方向带符号；
  作为强平/被动减仓的代理，不假设保证金率（保证金数据行情里拿不到）
- Gamma 场景、Vega 直接用转换脚本写好的标量
- 多头/空头噪音（lookback 日 Σ 毛刺 / ΣATR）

衍生链很大，这里和 derivatives.py 一样按品种 + 文件 mtime + 参数缓存；
返回值顺序与 bars 一致，字段缺失时用 0 方便前端直接画线。
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

from ..config import settings
from .data_source import load_chart_history
from .derivatives import (
    _SQRT_1_2PI,
    _gamma_oi,
    _load_months,
    _month_t,
    _normalize_band,
    iter_chain_entries,
)

_EMPTY_KEYS = (
    "gex", "gex_norm", "skew", "skew_delta", "oi_net", "vr", "iv",
    "fut_vol", "fut_oi", "atr", "tr", "mp_conf", "hdg", "dex",
    "vanna", "charm", "gflow", "gexp", "exv", "liqp", "gam", "veg",
    "noise_l", "noise_s",
)


def _num(value: object, default: float = 0.0) -> float:
    """取有限数值，缺失或非法时回退默认值。"""
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return default


def _trim(value: float) -> float:
    """按数量级截断小数位，避免整段序列把响应撑大。"""
    if value == 0:
        return 0.0
    if abs(value) >= 1000:
        return round(value, 2)
    if abs(value) >= 1:
        return round(value, 4)
    return round(value, 6)


def _calc_row(row: dict, prev_skew: float | None, prev_atr: float | None,
              prev_close: float | None, prev_fut_oi: float | None,
              band: float, atr_period: int) -> dict:
    """计算单日附图数值；行内字段约定见类文档。"""
    point: dict = {"d": row.get("d")}
    point.update({key: 0.0 for key in _EMPTY_KEYS})

    close = _num(row.get("c"))
    high = _num(row.get("h"))
    low = _num(row.get("l"))
    call_oi = _num(row.get("co"))
    put_oi = _num(row.get("po"))

    # 基础 OI / 成交量类指标直接来自数据文件
    skew = (call_oi - put_oi) / (call_oi + put_oi + 1e-10)
    gex = _num(row.get("gex"))
    oi_total = _num(row.get("oi_total"), call_oi + put_oi)
    gex_norm = (
        gex / (oi_total * close * close) * 1e4
        if gex != 0 and oi_total > 0 and close > 0
        else 0.0
    )
    point["gex"] = gex
    point["gex_norm"] = gex_norm
    point["skew"] = skew
    point["skew_delta"] = skew - prev_skew if prev_skew is not None else 0.0
    point["oi_net"] = call_oi - put_oi
    point["vr"] = _num(row.get("vr"), 1.0)
    point["iv"] = _num(row.get("ivs"))
    point["fut_vol"] = _num(row.get("fut_vol"))
    point["fut_oi"] = _num(row.get("fut_oi"))
    point["gam"] = _num(row.get("gam"))
    point["veg"] = _num(row.get("veg"))
    point["exv"] = _num(row.get("exv"))

    # 被动压力代理：期货减仓幅度(%) × 当日涨跌幅(%)，只统计减仓日。
    # 减仓越猛、波动越大越可能是强平/被动平仓；加仓日的波动不算被动压力
    if (prev_fut_oi and prev_fut_oi > 0 and point["fut_oi"] > 0
            and close > 0 and prev_close and prev_close > 0):
        oi_drop = max(0.0, prev_fut_oi - point["fut_oi"]) / prev_fut_oi * 100
        ret = (close - prev_close) / prev_close * 100
        point["liqp"] = oi_drop * ret

    # 真实波幅与 Wilder ATR
    tr = high - low if prev_close is None else max(
        high - low, abs(high - prev_close), abs(low - prev_close)
    )
    if prev_atr is None:
        atr = tr
    else:
        atr = (prev_atr * (atr_period - 1) + tr) / atr_period
    point["tr"] = tr
    point["atr"] = atr

    # 期权链：MP 置信、净对冲、DEX、Vanna、Charm
    raw = row.get("oc_chain")
    if raw and close > 0:
        try:
            chain = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            chain = None
        if isinstance(chain, list) and chain:
            spot = _num(row.get("oc"))
            if spot <= 0:
                spot = close
            rng = band / 100
            low_band = close * (1 - rng)
            high_band = close * (1 + rng)

            mp = _num(row.get("mp"), close)
            best_oi = 0.0
            best_dist = math.inf
            total_oi = 0.0
            hedge = 0.0
            for item in chain:
                if not isinstance(item, dict):
                    continue
                strike = item.get("s")
                if not isinstance(strike, (int, float)) or strike <= 0:
                    continue
                strike = float(strike)
                c_oi = _num(item.get("co"))
                p_oi = _num(item.get("po"))
                contract_oi = c_oi + p_oi
                total_oi += contract_oi
                hedge += _num(item.get("cdw")) + _num(item.get("pdw"))

                dist = abs(strike - mp)
                if dist < best_dist:
                    best_dist = dist
                    best_oi = contract_oi

            # Vanna/Charm 对 T 敏感：按月链逐月用各自 dte，旧数据回退近月 dte
            vanna = 0.0
            charm = 0.0
            for strike, c_oi, p_oi, civ, piv, t, sqrt_t in iter_chain_entries(
                row, chain, low_band, high_band
            ):
                if c_oi > 0 and civ > 0:
                    d1 = (math.log(spot / strike) + 0.5 * civ * civ * t) / (civ * sqrt_t)
                    npdf = _SQRT_1_2PI * math.exp(-0.5 * d1 * d1)
                    vanna += -npdf * (d1 - civ * sqrt_t) / civ * c_oi
                    charm += npdf * (d1 - civ * sqrt_t) / (2 * t) * c_oi
                if p_oi > 0 and piv > 0:
                    d1 = (math.log(spot / strike) + 0.5 * piv * piv * t) / (piv * sqrt_t)
                    npdf = _SQRT_1_2PI * math.exp(-0.5 * d1 * d1)
                    vanna -= -npdf * (d1 - piv * sqrt_t) / piv * p_oi
                    charm -= npdf * (d1 - piv * sqrt_t) / (2 * t) * p_oi

            point["mp_conf"] = best_oi / total_oi if total_oi > 0 else 0.0
            point["hdg"] = hedge
            point["dex"] = hedge / total_oi if total_oi > 0 else 0.0
            # 旧页口径：Vanna 是 IV 降 1 点（0.01）的净买入手数，Charm 是时间流逝 1 天
            point["vanna"] = vanna * 0.01
            point["charm"] = -charm / 365

            # 再平衡流量：ΣΓ×OI×S×预期日波幅(ATR/收盘)，单位手；
            # 到期周 Gamma 占比：dte≤7 的月份占全部月份 Γ×OI 的比例
            gamma_total, gamma_week = _month_gamma(row, low_band, high_band, spot)
            move = atr / close if atr > 0 and close > 0 else 0.01
            point["gflow"] = gamma_total * spot * move
            point["gexp"] = gamma_week / gamma_total * 100 if gamma_total > 0 else 0.0

    return point


def _month_gamma(row: dict, low: float, high: float, spot: float) -> tuple[float, float]:
    """按月链 ocm 汇总 Γ×OI，返回 (全部月份, dte≤7 的月份)。

    没有 ocm 的旧数据返回 (0, 0)，gflow/gexp 也就为 0。
    """
    months = _load_months(row.get("ocm"))
    if not months:
        return 0.0, 0.0
    total = 0.0
    week = 0.0
    for month in months:
        if not isinstance(month, dict):
            continue
        t, sqrt_t = _month_t(month)
        month_gamma = 0.0
        for entry in month.get("k") or []:
            if not isinstance(entry, (list, tuple)) or len(entry) < 5:
                continue
            strike = entry[0]
            if not isinstance(strike, (int, float)) or strike <= 0:
                continue
            strike = float(strike)
            if strike < low or strike > high:
                continue
            # 按月链条目 [行权价, 看涨OI, 看跌OI, 看涨IV, 看跌IV]
            for oi_idx, iv_idx in ((1, 3), (2, 4)):
                oi = entry[oi_idx]
                iv = entry[iv_idx]
                if not isinstance(oi, (int, float)) or not isinstance(iv, (int, float)):
                    continue
                if oi > 0 and iv > 0:
                    month_gamma += _gamma_oi(strike, float(iv), float(oi), spot, t, sqrt_t)
        total += month_gamma
        dte = month.get("dte")
        if isinstance(dte, (int, float)) and dte <= 7:
            week += month_gamma
    return total, week


@lru_cache(maxsize=4)
def _cached(
    symbol: str, mtime: float, band: float, atr_period: int, lookback: int
) -> tuple[dict, ...]:
    """按品种 + 文件 mtime + 参数缓存整段附图序列。"""
    rows = load_chart_history(symbol)
    points: list[dict] = []
    prev_skew: float | None = None
    prev_atr: float | None = None
    prev_close: float | None = None
    prev_fut_oi: float | None = None
    for row in rows:
        point = _calc_row(
            row, prev_skew, prev_atr, prev_close, prev_fut_oi, band, atr_period
        )
        points.append(point)
        prev_skew = point["skew"]
        prev_atr = point["atr"]
        prev_close = _num(row.get("c"), prev_close or 0.0)
        prev_fut_oi = point["fut_oi"]

    # 噪音：最近 lookback 天 Σmax(0, 昨收-最低) / ΣATR ×100，多空分别统计
    for index, point in enumerate(points):
        if index == 0:
            continue
        low_sum = 0.0
        high_sum = 0.0
        atr_sum = 0.0
        for j in range(max(1, index - lookback + 1), index + 1):
            prev = _num(rows[j - 1].get("c"))
            low_sum += max(0.0, prev - _num(rows[j].get("l")))
            high_sum += max(0.0, _num(rows[j].get("h")) - prev)
            atr_sum += points[j]["atr"]
        if atr_sum > 0:
            point["noise_l"] = low_sum / atr_sum * 100
            point["noise_s"] = high_sum / atr_sum * 100

    for point in points:
        for key, value in point.items():
            if key != "d":
                point[key] = _trim(value)

    return tuple(points)


def compute_subchart(
    symbol: str,
    band: float | None = 20,
    atr_period: int | None = 5,
    lookback: int | None = 10,
) -> list[dict]:
    """返回逐日附图指标，顺序与 bars 一致。"""
    path = settings.web_data_dir / f"{symbol}.json"
    if not path.is_file():
        return []
    atr_period = max(1, int(atr_period or 5))
    lookback = max(1, int(lookback or 10))
    return list(
        _cached(
            symbol,
            path.stat().st_mtime,
            _normalize_band(band),
            atr_period,
            lookback,
        )
    )
