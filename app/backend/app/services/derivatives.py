"""复盘图衍生指标线。

从 web/data 每行的 oc_chain / ocm 计算：
- Gamma 类指标（Call/Put Wall、Gamma 峰值、Magnet、ZeroGamma）按月链 ocm
  逐月用各自 dte 计算再汇总；旧数据没有 ocm 时回退近月 dte 口径
- OI 类指标（Pin、OI 重心、25Δ）与 T 无关，直接用全月份汇总链 oc_chain
- S 用期权标的期货价（缺失时回退收盘价）
- band 是“现价 ±%”显示带宽，只影响展示，不参与策略信号
- Call/Put Wall、Gamma 峰值、Magnet、ZeroGamma 依赖 IV；SHFE 无 IV 时这些线为空，
  仍可显示 OI 重心、Pin 和 Δ25（若转换数据带 Delta）
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

from ..config import settings
from .data_source import load_chart_history

_SQRT_1_2PI = 0.3989422804014327
_DEFAULT_BAND = 20.0
_ZG_SEGMENTS = 80  # ZeroGamma 先在带内均匀扫描，再二分细化
_ZG_BISECT = 10

_EMPTY_KEYS = (
    "gpk", "cw", "pw", "zg", "magnet", "pin", "d25c", "d25p", "oic", "oicc", "oicp",
)


def _normalize_band(band: float | None) -> float:
    """带宽按百分数处理（20 = 现价 ±20%），非法值回退默认。"""
    try:
        value = float(band) if band is not None else _DEFAULT_BAND
    except (TypeError, ValueError):
        return _DEFAULT_BAND
    if not math.isfinite(value) or value <= 0 or value >= 100:
        return _DEFAULT_BAND
    return value


def _gamma_oi(
    strike: float, iv: float, oi: float, spot: float, t: float, sqrt_t: float
) -> float:
    """Black-Scholes 无股息 Gamma × OI；只用于展示，不需要精确到合约乘数。"""
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * sqrt_t)
    return _SQRT_1_2PI * math.exp(-0.5 * d1 * d1) / (spot * iv * sqrt_t) * oi


def _load_months(raw: object) -> list | None:
    """解析按月链 ocm；旧数据没有该字段时返回 None（调用方回退近月 dte）。"""
    if not raw:
        return None
    try:
        months = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return months if isinstance(months, list) and months else None


def _month_t(month: dict) -> tuple[float, float]:
    """按月链里该月的 T（年）与 √T。"""
    dte = month.get("dte")
    t = float(dte) / 365 if isinstance(dte, (int, float)) and dte > 0 else 1 / 365
    return t, math.sqrt(t)


def _entry_num(value: object) -> float:
    """取有限数值；None 或非法值回退 0，调用方用 >0 过滤。"""
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return 0.0


def iter_chain_entries(row: dict, chain: list, low: float, high: float):
    """产出显示带内的 (strike, 看涨OI, 看跌OI, 看涨IV, 看跌IV, T, √T)。

    有按月链 ocm 时逐月用各自的 dte；旧数据回退到行内近月 dte。
    """
    months = _load_months(row.get("ocm"))
    if months:
        for month in months:
            if not isinstance(month, dict):
                continue
            t, sqrt_t = _month_t(month)
            for entry in month.get("k") or []:
                if not isinstance(entry, (list, tuple)) or len(entry) < 5:
                    continue
                strike = entry[0]
                if not isinstance(strike, (int, float)) or strike <= 0:
                    continue
                strike = float(strike)
                if strike < low or strike > high:
                    continue
                yield (
                    strike, _entry_num(entry[1]), _entry_num(entry[2]),
                    _entry_num(entry[3]), _entry_num(entry[4]), t, sqrt_t,
                )
        return

    dte = row.get("dte")
    t = float(dte) / 365 if isinstance(dte, (int, float)) and dte > 0 else 1 / 365
    sqrt_t = math.sqrt(t)
    for item in chain:
        if not isinstance(item, dict):
            continue
        strike = item.get("s")
        if not isinstance(strike, (int, float)) or strike <= 0:
            continue
        strike = float(strike)
        if strike < low or strike > high:
            continue
        yield (
            strike, _entry_num(item.get("co")), _entry_num(item.get("po")),
            _entry_num(item.get("civ")), _entry_num(item.get("piv")), t, sqrt_t,
        )


def _zero_gamma(
    calls: list[tuple[float, float, float, float, float]],
    puts: list[tuple[float, float, float, float, float]],
    close: float,
    low: float,
    high: float,
) -> float | None:
    """带内扫描总 Gamma（Call 正 / Put 负）变号处，取离现价最近的翻转点。

    每条 (strike, iv, oi, T, √T) 自带期限，不同月份可以用各自的 dte。
    """

    def total(spot: float) -> float:
        acc = 0.0
        for strike, iv, oi, t, sqrt_t in calls:
            d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * sqrt_t)
            acc += math.exp(-0.5 * d1 * d1) / (spot * iv * sqrt_t) * oi
        for strike, iv, oi, t, sqrt_t in puts:
            d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * sqrt_t)
            acc -= math.exp(-0.5 * d1 * d1) / (spot * iv * sqrt_t) * oi
        return acc

    prev_spot = low
    prev_value = total(low)
    best: float | None = None
    best_dist = math.inf
    for segment in range(1, _ZG_SEGMENTS + 1):
        spot = low + (high - low) * segment / _ZG_SEGMENTS
        value = total(spot)
        if (prev_value < 0 < value) or (value < 0 < prev_value):
            left, right, left_value = prev_spot, spot, prev_value
            for _ in range(_ZG_BISECT):
                mid = (left + right) / 2
                mid_value = total(mid)
                if (left_value < 0) == (mid_value < 0):
                    left, left_value = mid, mid_value
                else:
                    right = mid
            root = (left + right) / 2
            dist = abs(root - close)
            if dist < best_dist:
                best_dist = dist
                best = root
        prev_spot, prev_value = spot, value
    return best


def _calc_row(row: dict, band: float) -> dict:
    """计算单日衍生指标；没有期权链或没有 IV/Delta 时对应字段为 None。"""
    point: dict = {"d": row.get("d")}
    point.update({key: None for key in _EMPTY_KEYS})

    raw = row.get("oc_chain")
    close = row.get("c")
    if not raw or close is None:
        return point
    try:
        chain = json.loads(raw)
    except (TypeError, ValueError):
        return point
    if not isinstance(chain, list) or not chain:
        return point

    close = float(close)
    if close <= 0:
        return point
    oc = row.get("oc")
    spot = float(oc) if isinstance(oc, (int, float)) and oc > 0 else close
    rng = band / 100
    low, high = close * (1 - rng), close * (1 + rng)

    # OI 类指标与 T 无关：直接用全月份汇总链
    pin_oi = pin_strike = 0.0
    call_oi_sum = call_oi_weight = 0.0
    put_oi_sum = put_oi_weight = 0.0
    all_oi_sum = all_oi_weight = 0.0
    d25c: float | None = None
    d25p: float | None = None
    d25c_dist = d25p_dist = math.inf

    for item in chain:
        if not isinstance(item, dict):
            continue
        strike = item.get("s")
        if not isinstance(strike, (int, float)) or strike <= 0:
            continue
        strike = float(strike)
        if strike < low or strike > high:
            continue

        try:
            call_oi = float(item.get("co") or 0)
            put_oi = float(item.get("po") or 0)
        except (TypeError, ValueError):
            continue
        total_oi = call_oi + put_oi

        if total_oi > pin_oi:
            pin_oi, pin_strike = total_oi, strike
        call_oi_sum += call_oi * strike
        call_oi_weight += call_oi
        put_oi_sum += put_oi * strike
        put_oi_weight += put_oi
        all_oi_sum += total_oi * strike
        all_oi_weight += total_oi

        cd = item.get("cd")
        if isinstance(cd, (int, float)) and abs(cd) > 0.01:
            diff = abs(cd - 0.25)
            if diff < d25c_dist and diff <= 0.2:
                d25c_dist, d25c = diff, strike
        pd = item.get("pd")
        if isinstance(pd, (int, float)) and abs(pd) > 0.01:
            diff = abs(pd + 0.25)
            if diff < d25p_dist and diff <= 0.2:
                d25p_dist, d25p = diff, strike

    # Gamma 类指标：按月链逐月用自己的 dte 算 γ 再汇总；
    # 旧数据没有 ocm 时 iter_chain_entries 回退近月 dte
    call_gamma: dict[float, float] = {}
    put_gamma: dict[float, float] = {}
    zg_calls: list[tuple[float, float, float, float, float]] = []
    zg_puts: list[tuple[float, float, float, float, float]] = []
    for strike, call_oi, put_oi, civ, piv, t, sqrt_t in iter_chain_entries(
        row, chain, low, high
    ):
        if call_oi > 0 and civ > 0:
            gamma = _gamma_oi(strike, civ, call_oi, spot, t, sqrt_t)
            call_gamma[strike] = call_gamma.get(strike, 0.0) + gamma
            zg_calls.append((strike, civ, call_oi, t, sqrt_t))
        if put_oi > 0 and piv > 0:
            gamma = _gamma_oi(strike, piv, put_oi, spot, t, sqrt_t)
            put_gamma[strike] = put_gamma.get(strike, 0.0) + gamma
            zg_puts.append((strike, piv, put_oi, t, sqrt_t))

    cw_strike = cw_gamma = 0.0
    for strike, gamma in call_gamma.items():
        if gamma > cw_gamma:
            cw_gamma, cw_strike = gamma, strike
    pw_strike = pw_gamma = 0.0
    for strike, gamma in put_gamma.items():
        if gamma > pw_gamma:
            pw_gamma, pw_strike = gamma, strike

    gpk_strike = gpk_weight = 0.0
    magnet_sum = magnet_weight = 0.0
    for strike in set(call_gamma) | set(put_gamma):
        gamma = call_gamma.get(strike, 0.0) + put_gamma.get(strike, 0.0)
        if gamma > gpk_weight:
            gpk_weight, gpk_strike = gamma, strike
        magnet_sum += gamma * strike
        magnet_weight += gamma

    point["gpk"] = round(gpk_strike, 2) if gpk_weight > 0 else None
    point["cw"] = round(cw_strike, 2) if cw_gamma > 0 else None
    point["pw"] = round(pw_strike, 2) if pw_gamma > 0 else None
    zero_gamma = _zero_gamma(zg_calls, zg_puts, close, low, high)
    point["zg"] = round(zero_gamma, 2) if zero_gamma is not None else None
    point["magnet"] = (
        round(magnet_sum / magnet_weight, 2) if magnet_weight > 0 else None
    )
    point["pin"] = round(pin_strike, 2) if pin_oi > 0 else None
    point["d25c"] = round(d25c, 2) if d25c is not None else None
    point["d25p"] = round(d25p, 2) if d25p is not None else None
    point["oic"] = round(all_oi_sum / all_oi_weight, 2) if all_oi_weight > 0 else None
    point["oicc"] = round(call_oi_sum / call_oi_weight, 2) if call_oi_weight > 0 else None
    point["oicp"] = round(put_oi_sum / put_oi_weight, 2) if put_oi_weight > 0 else None
    return point


@lru_cache(maxsize=4)
def _cached(
    symbol: str, mtime: float, band: float
) -> tuple[dict, ...]:
    """按 品种 + 文件修改时间 + 带宽 缓存；换策略参数回测时不用重解析期权链。"""
    return tuple(_calc_row(row, band) for row in load_chart_history(symbol))


def compute_derivatives(symbol: str, band: float | None = 20) -> list[dict]:
    """返回逐日衍生指标，顺序与 bars 一致。"""
    path = settings.web_data_dir / f"{symbol}.json"
    if not path.is_file():
        return []
    return list(_cached(symbol, path.stat().st_mtime, _normalize_band(band)))
