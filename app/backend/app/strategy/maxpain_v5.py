"""MaxPain 趋势跟踪 v5。

逻辑从 web/index.html 的 computeNextDirPred / _runCore 移植，
只保留“盘后扫描次日机会”需要的部分：

1. 方向：lookback 日 MP 的 EMA 偏离 + 窗口净变化（mpDirection）
2. 过滤：ATR/价格分位、EWMA 快/慢波动比、价格斜率
3. 入场止损：收盘价 ∓ ATR × entryAtrMult（次日开盘参考）
4. 仓位：权益 × 保证金率 → 手数，再按波动率目标化缩放

扫描时点是当日收盘后：trade_date 是最后一根日线日期，信号对应次日开盘。
回测走逐日撮合，包含 skipCount、回踩挂单、信号准确率门控和期货换月；
扫描入口当前只做最后一天的过滤/仓位计算，后续再统一到同一套逐日状态。
"""

import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from itertools import product

from .base import Bar, SignalResult, register

# 交易成本：固定 10 元/手，开平双边
FEE_PER_LOT = 10.0


def _mp_direction(
    values: list[float | None], period: int, buffer: float
) -> str | None:
    """MP 趋势方向。

    与旧页面 mpDirection 一致：最新值相对 EMA 偏离超过 buffer，
    并且窗口净变化同向，才给出 up/down；否则视为震荡。
    净变化条件用来过滤 V 型假信号（回到起点但没创新高/新低）。
    """
    if len(values) < period:
        return None
    window = values[-period:]
    if any(value is None for value in window):
        return None

    first = window[0]
    last = window[-1]
    k = 2 / (period + 1)
    ema = first
    for value in window[1:]:
        ema = value * k + ema * (1 - k)

    if last > ema * (1 + buffer) and last > first:
        return "up"
    if last < ema * (1 - buffer) and last < first:
        return "down"
    return None


def _calc_atr(bars: list[Bar], period: int) -> list[float | None]:
    """Wilder ATR；和旧页面 calcATR 一样先简单平均，之后递推平滑。"""
    atr: list[float | None] = [None] * len(bars)
    if len(bars) < period + 1:
        return atr

    total = 0.0
    for i in range(1, period + 1):
        prev, cur = bars[i - 1], bars[i]
        total += max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c))
    atr[period] = total / period

    for i in range(period + 1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        tr = max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c))
        prev_atr = atr[i - 1]
        if prev_atr is not None:
            atr[i] = (prev_atr * (period - 1) + tr) / period
    return atr


def _calc_mp_rebase(bars: list[Bar]) -> list[float]:
    """期权换月后的 MP 跳空累计偏移。

    换月当天 MP 会跳到新合约的水平，直接用历史 MP 算 EMA 会失真；
    这里记下每次换月的累计差值，供补齐窗口时使用。
    """
    offsets = [0.0] * len(bars)
    cum = 0.0
    prev_expiry: str | None = None
    prev_mp: float | None = None
    for i, bar in enumerate(bars):
        cur_mp = bar.mp
        if (
            bar.expiry
            and prev_expiry
            and cur_mp is not None
            and prev_mp is not None
            and bar.expiry != prev_expiry
        ):
            cum += cur_mp - prev_mp
        offsets[i] = cum
        prev_expiry = bar.expiry
        prev_mp = cur_mp
    return offsets


def _calc_ewma_vol(
    bars: list[Bar],
) -> tuple[list[float | None], list[float | None]]:
    """快/慢 EWMA 波动率，与旧页面 fastVol/slowVol 一致。

    快线 0.94 权重关注当期，慢线 0.99 作为长期目标；
    快/慢比值用于 EWMA 过滤和波动率目标化仓位。
    """
    fast: list[float | None] = [None] * len(bars)
    slow: list[float | None] = [None] * len(bars)
    if len(bars) < 2:
        return fast, slow

    seed_sum = 0.0
    seed_count = 0
    for i in range(1, min(len(bars), 31)):
        prev = bars[i - 1].c
        if prev:
            ret = (bars[i].c - prev) / prev
            seed_sum += ret * ret
            seed_count += 1
    fast_var = seed_sum / (seed_count - 1) if seed_count > 2 else 0.0
    slow_var = fast_var

    for i in range(1, len(bars)):
        prev = bars[i - 1].c
        ret = (bars[i].c - prev) / prev if prev else math.nan
        sq = ret * ret if math.isfinite(ret) else 0.0
        fast_var = 0.94 * fast_var + 0.06 * sq
        slow_var = 0.99 * slow_var + 0.01 * sq
        fast[i] = math.sqrt(fast_var)
        slow[i] = math.sqrt(slow_var)
    return fast, slow


def _price_slope(bars: list[Bar], end_idx: int, days: int) -> float | None:
    """收盘价回归斜率 / 收盘价，用于价格斜率过滤。"""
    days = max(2, days)
    if end_idx + 1 < days:
        return None
    start = end_idx - days + 1
    sx = sy = sxx = sxy = 0.0
    for j in range(start, end_idx + 1):
        x = j - start
        c = bars[j].c
        sx += x
        sy += c
        sxx += x * x
        sxy += x * c
    den = days * sxx - sx * sx
    close = bars[end_idx].c
    if den <= 0 or close <= 0:
        return None
    return (days * sxy - sx * sy) / den / close


def _calc_roll_seg_start(bars: list[Bar]) -> list[int]:
    """每天所属“换月段”的起点下标；换月后方向窗口从新合约重新累计。"""
    seg = [0] * len(bars)
    start = 0
    for i in range(len(bars)):
        if (
            i > 0
            and bars[i].expiry
            and bars[i - 1].expiry
            and bars[i].expiry != bars[i - 1].expiry
        ):
            start = i
        seg[i] = start
    return seg


def _dir_mp_hist(
    bars: list[Bar],
    offsets: list[float],
    seg: list[int],
    i: int,
    lookback: int,
) -> list[float | None]:
    """取截至第 i 天的 lookback 个 MP。

    换月后不足 lookback 天时，用换月前数据按偏移量复权补齐，
    保证每天都有完整窗口可算方向。
    """
    if i < 0:
        return []
    start = seg[i]
    seg_len = i - start + 1
    if seg_len >= lookback:
        return [bars[k].mp for k in range(i - lookback + 1, i + 1)]

    need = lookback - seg_len
    fill_start = max(0, start - need)
    anchor = offsets[start]
    hist: list[float | None] = []
    for k in range(fill_start, start):
        mp = bars[k].mp
        hist.append(mp - offsets[k] + anchor if mp is not None else None)
    hist.extend(bars[k].mp for k in range(start, i + 1))
    return hist


def _direction_series(
    bars: list[Bar],
    offsets: list[float],
    seg: list[int],
    lookback: int,
    min_pct: float,
) -> tuple[list[str | None], list[str | None], list[float | None]]:
    """逐日预计算三组量：原始信号、EMA 反转方向、MP 偏离。

    三组共用同一次窗口 EMA：回测/参数优化每个组合都要用它们，
    只算一遍比在每日循环里反复重算快得多。
    - preds: 当日收盘信号（含窗口净变化条件），次日开盘执行
    - ema_dirs: 只看 EMA 偏离的方向，用于持仓后的方向反转出场
    - devs: MP 相对 EMA 的偏离百分比，供回踩挂单门控使用
    """
    mps = [bar.mp for bar in bars]
    n = len(bars)
    preds: list[str | None] = [None] * n
    ema_dirs: list[str | None] = [None] * n
    devs: list[float | None] = [None] * n
    k = 2 / (lookback + 1)
    for i in range(n):
        if i - seg[i] + 1 >= lookback:
            # 换月段内直接切片，避免逐日重建窗口
            window = mps[i - lookback + 1 : i + 1]
        else:
            window = _dir_mp_hist(bars, offsets, seg, i, lookback)
        if len(window) < lookback or None in window:
            continue
        first = window[0]
        last = window[-1]
        ema = first
        for value in window[1:]:
            ema = value * k + ema * (1 - k)
        if ema > 0:
            devs[i] = (last - ema) / ema * 100
        up = last > ema * (1 + min_pct)
        down = last < ema * (1 - min_pct)
        ema_dirs[i] = "up" if up else "down" if down else None
        if up and last > first:
            preds[i] = "long"
        elif down and last < first:
            preds[i] = "short"
    return preds, ema_dirs, devs


class _CoreCache:
    """逐日撮合的预计算序列，供多个参数组合复用。

    初始化开销只和 bars 有关；ATR、价格斜率、方向序列按对应参数
    惰性计算并缓存，参数优化时同一品种只算一次。
    """

    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.offsets = _calc_mp_rebase(bars)
        self.seg = _calc_roll_seg_start(bars)
        self.fast_vol, self.slow_vol = _calc_ewma_vol(bars)
        self._atr: dict[int, tuple[list[float | None], list[float | None]]] = {}
        self._slope: dict[int, list[float]] = {}
        self._dirs: dict[
            tuple[int, float],
            tuple[list[str | None], list[str | None], list[float | None]],
        ] = {}

    def atr(self, period: int) -> tuple[list[float | None], list[float | None]]:
        """ATR 和 ATR/价格，按周期缓存。"""
        hit = self._atr.get(period)
        if hit is None:
            atr = _calc_atr(self.bars, period)
            ratio = [
                atr[i] / self.bars[i].c
                if atr[i] is not None and self.bars[i].c > 0
                else None
                for i in range(len(self.bars))
            ]
            hit = (atr, ratio)
            self._atr[period] = hit
        return hit

    def slope(self, days: int) -> list[float]:
        """价格回归斜率/收盘价，按窗口缓存。"""
        days = max(2, days)
        hit = self._slope.get(days)
        if hit is None:
            hit = [0.0] * len(self.bars)
            for i in range(days, len(self.bars)):
                value = _price_slope(self.bars, i, days)
                hit[i] = value if value is not None else 0.0
            self._slope[days] = hit
        return hit

    def directions(
        self, lookback: int, min_pct: float
    ) -> tuple[
        list[str | None], list[str | None], list[float | None]
    ]:
        """原始信号 + 反转方向 + MP 偏离，按 (lookback, minPct) 缓存。"""
        key = (int(lookback), float(min_pct))
        hit = self._dirs.get(key)
        if hit is None:
            hit = _direction_series(
                self.bars, self.offsets, self.seg, key[0], key[1]
            )
            self._dirs[key] = hit
        return hit


def _acc_warn_idx(
    bars: list[Bar],
    preds: list[str | None],
    acc_window: int,
    acc_streak: int,
) -> list[int]:
    """连续 acc_streak 天按信号方向累计涨跌为负的日期下标。

    用当日收盘价对比 acc_window 日前收盘：等价于该方向单手持仓的窗口盈亏。
    """
    n = len(bars)
    window = max(1, int(acc_window or 3))
    streak = max(1, int(acc_streak or 3))

    neg = [False] * n
    for i in range(n):
        pred = preds[i - 1] if i >= 1 else None
        if pred and i >= window:
            cum = bars[i].c - bars[i - window].c
            neg[i] = (cum if pred == "long" else -cum) < 0

    warn: list[int] = []
    run = 0
    for i in range(n):
        run = run + 1 if neg[i] else 0
        if run >= streak:
            warn.append(i)
    return warn


def _gate_preds(
    preds: list[str | None], warn_idx: list[int], mode: str
) -> tuple[list[str | None], set[int]]:
    """按准确率标记门控信号：skip 不开仓，rev 反向开仓。

    rev_idx 记录被反向的信号下标，反向单跳过“方向反转出场”，
    避免趋势未变时立刻被误触发。
    """
    rev_idx: set[int] = set()
    if mode not in ("skip", "rev"):
        return list(preds), rev_idx

    gated = list(preds)
    warn_set = set(warn_idx)
    for i, pred in enumerate(gated):
        if i not in warn_set:
            continue
        if mode == "skip":
            gated[i] = None
        else:
            if pred is not None:
                rev_idx.add(i)
            gated[i] = (
                "short" if pred == "long" else "long" if pred == "short" else None
            )
    return gated, rev_idx


def _atr_at(atr: list[float | None], period: int, idx: int) -> float | None:
    """取第 idx 天可用的 ATR；预热期统一回退到第一个有效值。"""
    pos = max(period, idx)
    if pos < 0 or pos >= len(atr):
        return None
    return atr[pos]


def _make_trade(
    entry_date: str,
    exit_date: str,
    entry_px: float,
    exit_px: float,
    direction: str,
    qty: int,
    pnl: float,
    reason: str,
    entry_reason: str,
) -> dict:
    """统一交易记录格式，持有天数按自然日计算。"""
    return {
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_px": round(entry_px, 3),
        "exit_px": round(exit_px, 3),
        "direction": direction,
        "qty": qty,
        "pnl": round(pnl, 2),
        "reason": reason,
        "entry_reason": entry_reason,
        "hold": (
            date.fromisoformat(exit_date) - date.fromisoformat(entry_date)
        ).days,
    }


def _run_core(
    bars: list[Bar],
    params: dict,
    mult: float,
    preds: list[str | None],
    rev_idx: set[int],
    bt_start: int = 0,
    bt_end: int | None = None,
    cache: _CoreCache | None = None,
) -> dict:
    """逐日撮合核心，对齐 web/index.html 的 _runCore。

    每天顺序：期货换月 → 方向反转出场 → 追踪止损/保本 →
    开仓（过滤/回踩/手数）→ 收盘标记权益与峰值。
    start/end 只限制开仓区间，指标预热始终用全量历史。
    cache 用于优化时跨组合复用预计算序列，不传则现场构建一份。
    """
    n = len(bars)
    if bt_end is None:
        bt_end = n

    lookback = int(params["lookback"])
    min_pct = float(params["minPct"])
    max_pos = float(params["maxPos"])
    margin = float(params["margin"])
    be_th = float(params["beTh"])
    entry_atr_mult = float(params["entryAtrMult"])
    atr_period = int(params["atrPeriod"])
    atr_mult = float(params["atrMult"])
    lock_pct = float(params["lockPct"])
    # 参数里波动率分位是小数（0.05），回测比较用百分数（5）
    vol_low = float(params.get("volFilterLow") or 0) * 100
    vol_high = float(
        1 if params.get("volFilterHigh") is None else params["volFilterHigh"]
    ) * 100
    vol_win = int(params.get("volWindow") or 0)
    vol_target = float(params.get("volTarget") or 0)
    vol_target_cap = float(params.get("volTargetCap") or 0)
    ewma_high = float(params.get("ewmaFilterHigh") or 0)
    ewma_low = float(params.get("ewmaFilterLow") or 0)
    slope_th = float(params.get("priceSlopeTh") or 0)
    slope_days = int(params.get("slopeDays") or 30)
    pb_th = float(params.get("pbSlopeTh") or 0)
    capital = float(params["capital"])
    cap_limit = float(params.get("capLimit") or 0) * 10000
    skip_count = int(params.get("skipCount") or 0)

    if cache is None:
        cache = _CoreCache(bars)
    atr, atr_ratio = cache.atr(atr_period)
    fast_vol, slow_vol = cache.fast_vol, cache.slow_vol
    # 斜率过滤关闭时不预计算，省掉逐日回归
    price_slope = cache.slope(slope_days) if slope_th > 0 else None
    _, ema_dirs, devs = cache.directions(lookback, min_pct)

    eq = capital
    peak_eq = capital
    max_dd = 0.0
    pos: dict | None = None
    pos_days = 0
    trades: list[dict] = []
    # 每天收盘记一个点，和 bars 下标一一对齐，前端复盘直接按下标取
    eq_curve: list[dict] = []
    stop_curve: list[dict] = []
    # 每日收盘后判断“明日是否因方向反转而离场”，和 bars 下标对齐
    rev_exit_curve: list[bool] = []
    skip_long = skip_short = 0
    prev_close = bars[0].o

    for i, bar in enumerate(bars):
        d = bar.d
        op = bar.o
        px = bar.c
        hi = bar.h
        lo = bar.l
        mp = bar.mp
        reason = ""
        day_stop: float | None = None

        if pos is not None:
            prev_fc = bars[i - 1].fut_contract if i > 0 else None
            if bar.fut_contract and prev_fc and bar.fut_contract != prev_fc:
                # 期货换月：昨收平旧仓 + 今开重开新仓，跳空不计入盈亏
                sign = 1 if pos["direction"] == "long" else -1
                roll_pnl = (
                    (prev_close - pos["entry_px"]) * pos["qty"] * mult * sign
                )
                roll_fee = FEE_PER_LOT * pos["qty"] * 2
                trades.append(
                    _make_trade(
                        pos["entry_date"],
                        d,
                        pos["entry_px"],
                        prev_close,
                        pos["direction"],
                        pos["qty"],
                        roll_pnl - roll_fee,
                        "换月",
                        pos["entry_reason"],
                    )
                )
                eq += roll_pnl - roll_fee
                roll_atr = _atr_at(atr, atr_period, i - 1)
                entry_atr = roll_atr if roll_atr is not None else op * 0.005
                entry_stop = (
                    op - entry_atr * entry_atr_mult
                    if pos["direction"] == "long"
                    else op + entry_atr * entry_atr_mult
                )
                pos = {
                    "direction": pos["direction"],
                    "entry_px": op,
                    "entry_date": d,
                    "qty": pos["qty"],
                    "entry_reason": pos["entry_reason"],
                    "peak_px": op,
                    "be_active": False,
                    "entry_trend": pos["entry_trend"],
                    "entry_stop": entry_stop,
                    "reversed": pos["reversed"],
                }
            else:
                exit_px: float | None = None
                if not pos["reversed"]:
                    curr_dir = ema_dirs[i - 1] if i > 0 else None
                    if curr_dir is not None and curr_dir != pos["entry_trend"]:
                        exit_px = op
                        reason = "方向反转"

                if exit_px is None:
                    if pos["entry_date"] == d:
                        stop = pos["entry_stop"]
                    elif pos["be_active"]:
                        profit = (
                            pos["peak_px"] - pos["entry_px"]
                            if pos["direction"] == "long"
                            else pos["entry_px"] - pos["peak_px"]
                        )
                        stop = (
                            pos["entry_px"] + profit * lock_pct
                            if pos["direction"] == "long"
                            else pos["entry_px"] - profit * lock_pct
                        )
                    else:
                        atr_val = _atr_at(atr, atr_period, i - 1)
                        if pos["direction"] == "long":
                            atr_stop = (
                                prev_close - atr_val * atr_mult
                                if atr_val is not None
                                else prev_close * (1 - 0.005)
                            )
                            stop = max(atr_stop, pos["entry_stop"])
                        else:
                            atr_stop = (
                                prev_close + atr_val * atr_mult
                                if atr_val is not None
                                else prev_close * (1 + 0.005)
                            )
                            stop = min(atr_stop, pos["entry_stop"])
                    day_stop = stop

                    if pos["direction"] == "long":
                        if op < stop:
                            exit_px = op
                        elif lo < stop:
                            exit_px = stop
                    else:
                        if op > stop:
                            exit_px = op
                        elif hi > stop:
                            exit_px = stop

                    if exit_px is not None:
                        if stop == pos["entry_px"]:
                            reason = "保本出场"
                        elif (
                            pos["direction"] == "long"
                            and stop > pos["entry_px"]
                        ) or (
                            pos["direction"] == "short"
                            and stop < pos["entry_px"]
                        ):
                            reason = "追踪止盈"
                        else:
                            reason = "止损"

                if exit_px is not None:
                    sign = 1 if pos["direction"] == "long" else -1
                    pnl = (
                        (exit_px - pos["entry_px"])
                        * pos["qty"]
                        * mult
                        * sign
                    )
                    net = pnl - FEE_PER_LOT * pos["qty"] * 2
                    trades.append(
                        _make_trade(
                            pos["entry_date"],
                            d,
                            pos["entry_px"],
                            exit_px,
                            pos["direction"],
                            pos["qty"],
                            net,
                            reason,
                            pos["entry_reason"],
                        )
                    )
                    eq += net
                    if net > 0:
                        if pos["direction"] == "long":
                            skip_short = skip_count
                        else:
                            skip_long = skip_count
                    pos = None

        if pos is None and (reason == "" or reason == "方向反转"):
            target = preds[i - 1] if i > 0 else None
            if i < bt_start or i >= bt_end:
                target = None

            if target == "long" and skip_long > 0:
                skip_long -= 1
                target = None
            elif target == "short" and skip_short > 0:
                skip_short -= 1
                target = None

            if target:
                # 波动率分位：用昨日 ATR/价格，窗口 0 = 全部历史
                if vol_low > 0 or vol_high < 100:
                    cur_ratio = atr_ratio[i - 1] if i > 0 else None
                    if cur_ratio is not None:
                        win_lo = (
                            max(0, i - 1 - vol_win) if vol_win > 0 else 0
                        )
                        sample = [
                            atr_ratio[j]
                            for j in range(win_lo, i - 1)
                            if atr_ratio[j] is not None
                        ]
                        pct = (
                            sum(1 for v in sample if v < cur_ratio)
                            / len(sample)
                            * 100
                            if sample
                            else 50.0
                        )
                        if pct < vol_low or pct > vol_high:
                            target = None

                # EWMA 快/慢波动比
                if target and (ewma_high > 0 or ewma_low > 0) and i >= 2:
                    fast_now = fast_vol[i - 1]
                    slow_now = slow_vol[i - 1]
                    if (
                        fast_now is not None
                        and slow_now is not None
                        and fast_now > 1e-9
                    ):
                        ratio = fast_now / slow_now
                        if (
                            ewma_high > 0 and ratio > ewma_high
                        ) or (ewma_low > 0 and ratio < ewma_low):
                            target = None

                # 价格斜率：与信号方向相反则不开仓
                if target and slope_th > 0:
                    value = price_slope[i - 1] if i > 0 else 0.0
                    if (
                        target == "long" and value < -slope_th
                    ) or (target == "short" and value > slope_th):
                        target = None

                if target:
                    entry_px: float | None = op
                    # 回踩挂单：偏离不大时挂 MP 限价，当日未触及则不成交
                    if pb_th > 0 and i > 1:
                        dev = devs[i - 1]
                        if dev is not None and abs(dev) <= pb_th:
                            if target == "long":
                                if op <= mp:
                                    entry_px = op
                                elif lo <= mp:
                                    entry_px = mp
                                else:
                                    entry_px = None
                            else:
                                if op >= mp:
                                    entry_px = op
                                elif hi >= mp:
                                    entry_px = mp
                                else:
                                    entry_px = None

                    if entry_px is not None:
                        per_lot = entry_px * mult * margin
                        if per_lot > 0:
                            alloc = eq if eq < 500000 else eq * max_pos
                            if cap_limit > 0:
                                alloc = min(alloc, cap_limit)
                            if vol_target > 0 and i >= 2:
                                fast_now = fast_vol[i - 1]
                                slow_now = slow_vol[i - 1]
                                if (
                                    fast_now is not None
                                    and slow_now is not None
                                    and fast_now > 1e-9
                                ):
                                    scale = min(
                                        vol_target_cap
                                        if vol_target_cap > 0
                                        else 2.0,
                                        max(0.2, slow_now / fast_now),
                                    )
                                    alloc *= scale
                            qty = int(alloc // per_lot)

                            if qty >= 1:
                                en_atr = _atr_at(atr, atr_period, i - 1)
                                atr_value = (
                                    en_atr
                                    if en_atr is not None
                                    else entry_px * 0.005
                                )
                                stop2 = (
                                    entry_px - atr_value * entry_atr_mult
                                    if target == "long"
                                    else entry_px + atr_value * entry_atr_mult
                                )
                                entry_reason = (
                                    "趋势多"
                                    if target == "long"
                                    else "趋势空"
                                )
                                pos = {
                                    "direction": target,
                                    "entry_px": entry_px,
                                    "entry_date": d,
                                    "qty": qty,
                                    "entry_reason": entry_reason,
                                    "peak_px": entry_px,
                                    "be_active": False,
                                    "entry_trend": (
                                        "up"
                                        if target == "long"
                                        else "down"
                                    ),
                                    "entry_stop": stop2,
                                    "reversed": (i - 1) in rev_idx,
                                }

                                # 入场当天检查止损，防跳空击穿
                                if (
                                    target == "long" and lo < stop2
                                ) or (
                                    target == "short" and hi > stop2
                                ):
                                    sign = 1 if target == "long" else -1
                                    pnl = (
                                        (stop2 - entry_px)
                                        * qty
                                        * mult
                                        * sign
                                    )
                                    net = pnl - FEE_PER_LOT * qty * 2
                                    trades.append(
                                        _make_trade(
                                            d,
                                            d,
                                            entry_px,
                                            stop2,
                                            target,
                                            qty,
                                            net,
                                            "保本出场"
                                            if stop2 == entry_px
                                            else "止损",
                                            entry_reason,
                                        )
                                    )
                                    eq += net
                                    pos = None

        # 当日收盘权益 = 已实现 + 持仓浮盈浮亏
        float_pnl = 0.0
        if pos is not None:
            if pos["direction"] == "long":
                float_pnl = (px - pos["entry_px"]) * pos["qty"] * mult
            else:
                float_pnl = (pos["entry_px"] - px) * pos["qty"] * mult
        mtm_eq = eq + float_pnl
        peak_eq = max(peak_eq, mtm_eq)
        if peak_eq > 0:
            max_dd = max(max_dd, (peak_eq - mtm_eq) / peak_eq * 100)

        if pos is not None:
            pos_days += 1
            if pos["direction"] == "long" and px > pos["peak_px"]:
                pos["peak_px"] = px
            elif pos["direction"] == "short" and px < pos["peak_px"]:
                pos["peak_px"] = px

            # 收盘后用收盘价更新峰值并判断保本锁
            if be_th > 0 and lock_pct > 0 and not pos["be_active"]:
                if (
                    pos["direction"] == "long"
                    and pos["peak_px"]
                    >= pos["entry_px"] * (1 + be_th / lock_pct)
                ):
                    pos["be_active"] = True
                elif (
                    pos["direction"] == "short"
                    and pos["peak_px"]
                    <= pos["entry_px"] * (1 - be_th / lock_pct)
                ):
                    pos["be_active"] = True

            # 明日止损：今日收盘 + 今日 ATR + 收盘后的峰值/锁定状态
            current_stop = (
                pos["entry_stop"]
                if pos["entry_date"] == d
                else day_stop
            )
            if pos["be_active"]:
                profit = (
                    pos["peak_px"] - pos["entry_px"]
                    if pos["direction"] == "long"
                    else pos["entry_px"] - pos["peak_px"]
                )
                next_stop = (
                    pos["entry_px"] + profit * lock_pct
                    if pos["direction"] == "long"
                    else pos["entry_px"] - profit * lock_pct
                )
            else:
                next_atr = _atr_at(atr, atr_period, i)
                atr_value = (
                    next_atr if next_atr is not None else px * 0.005
                )
                if pos["direction"] == "long":
                    atr_stop = px - atr_value * atr_mult
                    next_stop = max(atr_stop, pos["entry_stop"])
                else:
                    atr_stop = px + atr_value * atr_mult
                    next_stop = min(atr_stop, pos["entry_stop"])
            stop_curve.append(
                {
                    "d": d,
                    "stop": current_stop,
                    "next": next_stop,
                    "start": pos["entry_date"] == d,
                }
            )
            # 明日开盘方向反转离场：EMA 偏离方向与持仓方向相反；换月日先换月不触发
            next_ema = ema_dirs[i]
            next_roll = (
                i + 1 < len(bars)
                and bars[i + 1].fut_contract
                and bar.fut_contract
                and bars[i + 1].fut_contract != bar.fut_contract
            )
            rev_exit_curve.append(
                not pos["reversed"]
                and not next_roll
                and next_ema is not None
                and next_ema != pos["entry_trend"]
            )
        else:
            stop_curve.append(
                {"d": d, "stop": None, "next": None, "start": False}
            )
            rev_exit_curve.append(False)

        prev_close = px
        eq_curve.append({"d": d, "eq": mtm_eq})

    # 最后一单按收盘价计入浮盈浮亏
    if pos is not None:
        sign = 1 if pos["direction"] == "long" else -1
        eq += (px - pos["entry_px"]) * pos["qty"] * mult * sign
        eq_curve[-1]["eq"] = eq

    return {
        "trades": trades,
        "eq": eq,
        "eq_curve": eq_curve,
        "peak_eq": peak_eq,
        "max_dd": max_dd,
        "open_position": pos,
        "pos_days": pos_days,
        "stop_curve": stop_curve,
        "rev_exit": rev_exit_curve,
        # 逐日指标序列，与 bars 等长，供复盘图表悬停展示
        "atr": atr,
        "atr_ratio": atr_ratio,
        "fast_vol": fast_vol,
        "slow_vol": slow_vol,
        "price_slope": price_slope,
    }


# 参数优化进程池的子进程状态；spawn 模式下每个进程初始化一次
_OPT_WORKER: dict = {}


def _opt_combo_row(
    bars: list[Bar],
    merged: dict,
    keys: list[str],
    combo: tuple,
    mult: float,
    capital: float,
    bt_start: int,
    bt_end: int,
    cache: _CoreCache,
    preds_for,
) -> dict | None:
    """跑单个参数组合；没有交易返回 None。"""
    pp = {**merged}
    for key, value in zip(keys, combo):
        pp[key] = value
    trading_preds, rev_idx = preds_for(pp)
    core = _run_core(
        bars, pp, mult, trading_preds, rev_idx, bt_start, bt_end, cache
    )
    trades = core["trades"]
    if not trades:
        return None
    wins = [trade for trade in trades if trade["pnl"] > 0]
    losses = [trade for trade in trades if trade["pnl"] <= 0]
    win_sum = sum(trade["pnl"] for trade in wins)
    loss_sum = sum(abs(trade["pnl"]) for trade in losses)
    return {
        "values": {
            key: (int(value) if float(value).is_integer() else value)
            for key, value in zip(keys, combo)
        },
        "end_equity": round(core["eq"]),
        "total_return": (
            round((core["eq"] / capital - 1) * 100, 1) if capital > 0 else 0
        ),
        "trades": len(trades),
        "coverage": round(
            core["pos_days"] / max(1, bt_end - bt_start) * 100, 1
        ),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "profit_factor": (
            round(win_sum / loss_sum, 2) if loss_sum > 0 else None
        ),
        "max_dd": round(core["max_dd"], 1),
        "avg_pnl": round(sum(trade["pnl"] for trade in trades) / len(trades)),
    }


def _opt_batch(
    bars: list[Bar],
    merged: dict,
    keys: list[str],
    combos: list[tuple],
    mult: float,
    capital: float,
    bt_start: int,
    bt_end: int,
    cache: _CoreCache | None = None,
) -> list[dict]:
    """跑一批参数组合；方向预测按 (lookback, minPct, 门控) 缓存。"""
    if cache is None:
        cache = _CoreCache(bars)
    pred_cache: dict[tuple, tuple[list[str | None], set[int]]] = {}

    def preds_for(pp: dict) -> tuple[list[str | None], set[int]]:
        cache_key = (
            int(pp["lookback"]),
            float(pp["minPct"]),
            int(pp.get("accWindow") or 3),
            int(pp.get("accStreak") or 3),
            str(pp.get("accMode") or "off"),
        )
        cached = pred_cache.get(cache_key)
        if cached is None:
            raw, _, _ = cache.directions(cache_key[0], cache_key[1])
            warn = _acc_warn_idx(bars, raw, cache_key[2], cache_key[3])
            cached = _gate_preds(raw, warn, cache_key[4])
            pred_cache[cache_key] = cached
        return cached

    rows: list[dict] = []
    for combo in combos:
        row = _opt_combo_row(
            bars,
            merged,
            keys,
            combo,
            mult,
            capital,
            bt_start,
            bt_end,
            cache,
            preds_for,
        )
        if row is not None:
            rows.append(row)
    return rows


def _opt_worker_init(
    bars: list[Bar],
    merged: dict,
    keys: list[str],
    mult: float,
    capital: float,
    bt_start: int,
    bt_end: int,
) -> None:
    """子进程初始化：预建 cache，避免每个分块重复计算。"""
    _OPT_WORKER.update(
        bars=bars,
        merged=merged,
        keys=keys,
        mult=mult,
        capital=capital,
        bt_start=bt_start,
        bt_end=bt_end,
        cache=_CoreCache(bars),
    )


def _opt_worker(combos: list[tuple]) -> list[dict]:
    state = _OPT_WORKER
    return _opt_batch(
        state["bars"],
        state["merged"],
        state["keys"],
        combos,
        state["mult"],
        state["capital"],
        state["bt_start"],
        state["bt_end"],
        state["cache"],
    )


@register
class MaxPainV5:
    key = "maxpain_v5"
    name = "MaxPain 趋势跟踪 v5"

    # 回退值取 web/index.html 的 defaultParams（未保存参数时使用）；
    # 实际扫描优先用 strategy_params 表里按品种保存的优化参数。
    # 百分比类统一存小数：0.002 = 0.2%。
    default_params = {
        # 趋势方向
        "lookback": 10,          # MP 的 EMA 周期
        "minPct": 0.002,         # 趋势分类阈值（0.2%）
        # 仓位
        "maxPos": 1.0,           # 权益 >= 50 万时的最大仓位比例（100%）
        "margin": 0.15,          # 保证金率
        "capital": 100000,       # 初始权益
        "capLimit": 200,         # 单笔名义本金上限（万元），0 = 不限
        # 止损与保本
        "entryAtrMult": 0.5,     # 入场止损 ATR 倍数
        "atrPeriod": 5,          # ATR 周期
        "atrMult": 0.5,          # 追踪止损 ATR 倍数（回测用）
        "beTh": 0.01,            # 激活保本锁所需盈利（1%）
        "lockPct": 0.5,          # 激活后锁定峰值利润的比例
        # 过滤
        "volFilterLow": 0.0,     # 波动率分位下限（0 = 关闭）
        "volFilterHigh": 1.0,    # 波动率分位上限（100% = 关闭）
        "volWindow": 0,          # 分位统计窗口，0 = 全部历史
        "ewmaFilterHigh": 1.2,   # 快/慢 EWMA 波动比上限
        "ewmaFilterLow": 0,      # 快/慢 EWMA 波动比下限（0 = 关闭）
        "priceSlopeTh": 0,       # 价格斜率过滤阈值（0 = 关闭）
        "slopeDays": 30,         # 价格斜率窗口
        "volTarget": 1,          # 波动率目标化仓位开关
        "volTargetCap": 2,       # 仓位缩放上限
        "skipCount": 0,          # 盈利后跳过的反向信号次数（回测用）
        "pbSlopeTh": 0,          # 回踩挂单阈值：|MP 偏离%| 小于该值时挂 MP 限价
        "accWindow": 3,          # 准确率统计窗口（天）
        "accStreak": 3,          # 连续为负多少天后触发门控
        "accMode": "off",        # off / skip / rev
        # 复盘图衍生线
        "band": 20,              # 显示带宽（现价 ±%，只影响展示，不参与信号）
    }

    def scan(self, bars: list[Bar], params: dict) -> SignalResult:
        """扫描最后一根日线，给出次日开盘的交易机会。

        params 里额外需要 ``mult``（合约乘数），扫描入口会从 index.json 注入。
        """
        merged = {**self.default_params, **params}
        lookback = int(merged["lookback"])
        atr_period = int(merged["atrPeriod"])
        if len(bars) < max(lookback + 1, atr_period + 2):
            return SignalResult(direction="none", reason="历史数据不足")

        last = bars[-1]

        # 1) 方向：用截至今日收盘的 MP 窗口
        offsets = _calc_mp_rebase(bars)
        seg = _calc_roll_seg_start(bars)
        mp_hist = _dir_mp_hist(bars, offsets, seg, len(bars) - 1, lookback)
        trend = _mp_direction(mp_hist, lookback, float(merged["minPct"]))
        if trend is None:
            return SignalResult(
                direction="none", price=last.c, reason="MP 趋势震荡"
            )
        direction = "long" if trend == "up" else "short"

        # 2) 波动率过滤：ATR/价格 的历史分位
        # 旧引擎在“入场当天”用昨日 ATR/价格，并只统计到昨日为止；
        # 对次日入场来说，今天收盘就是“昨日”。
        atr = _calc_atr(bars, atr_period)
        atr_now = atr[-1]
        if atr_now is None or last.c <= 0:
            return SignalResult(
                direction="none", price=last.c, reason="ATR 数据不足"
            )
        ratio_now = atr_now / last.c

        vol_window = int(merged.get("volWindow", 0))
        start = max(0, len(bars) - 1 - vol_window) if vol_window > 0 else 0
        sample = [
            atr[i] / bars[i].c
            for i in range(start, len(bars) - 1)
            if atr[i] is not None and bars[i].c > 0
        ]
        pct = (
            sum(1 for value in sample if value < ratio_now) / len(sample) * 100
            if sample
            else 50.0
        )

        # 新系统百分比参数存小数：0.05 / 0.95 → 5% / 95%
        vol_low = float(merged.get("volFilterLow", 0)) * 100
        vol_high = float(merged.get("volFilterHigh", 1)) * 100
        if (vol_low > 0 or vol_high < 100) and (pct < vol_low or pct > vol_high):
            return SignalResult(
                direction="none",
                price=last.c,
                reason=f"波动率分位 {pct:.0f}% 超出 {vol_low:g}%~{vol_high:g}%",
            )

        # 3) EWMA 波动率过滤：快/慢波动比超出区间则不开仓
        fast_vol, slow_vol = _calc_ewma_vol(bars)
        fast_now = fast_vol[-1]
        slow_now = slow_vol[-1]
        ewma_high = float(merged.get("ewmaFilterHigh", 0) or 0)
        ewma_low = float(merged.get("ewmaFilterLow", 0) or 0)
        if (
            (ewma_high > 0 or ewma_low > 0)
            and fast_now is not None
            and slow_now is not None
            and fast_now > 1e-9
        ):
            vol_ratio = fast_now / slow_now
            if (ewma_high > 0 and vol_ratio > ewma_high) or (
                ewma_low > 0 and vol_ratio < ewma_low
            ):
                return SignalResult(
                    direction="none",
                    price=last.c,
                    reason=f"EWMA 波动比 {vol_ratio:.2f} 超出过滤区间",
                )

        # 4) 价格斜率过滤：斜率方向与信号相反则不开仓
        slope_th = float(merged.get("priceSlopeTh", 0) or 0)
        if slope_th > 0:
            slope = _price_slope(
                bars, len(bars) - 1, int(merged.get("slopeDays", 30) or 30)
            )
            if slope is not None and (
                (direction == "long" and slope < -slope_th)
                or (direction == "short" and slope > slope_th)
            ):
                return SignalResult(
                    direction="none",
                    price=last.c,
                    reason=f"价格斜率 {slope:.4f} 与信号方向相反",
                )

        # 5) 入场止损：次日开盘价未知，用今收盘估算
        stop = (
            last.c - atr_now * float(merged["entryAtrMult"])
            if direction == "long"
            else last.c + atr_now * float(merged["entryAtrMult"])
        )

        # 6) 仓位：保证金 = 价格 × 合约乘数 × 保证金率
        mult = float(merged.get("mult") or 1)
        margin = float(merged["margin"])
        capital = float(merged["capital"])
        cap_limit = float(merged.get("capLimit", 0)) * 10000
        per_lot = last.c * mult * margin
        alloc = capital if capital < 500000 else capital * float(merged["maxPos"])
        if cap_limit > 0:
            alloc = min(alloc, cap_limit)

        # 波动率目标化：当期波动高时缩仓、低时加仓，缩放限制在 0.2~cap
        vol_target = float(merged.get("volTarget", 0) or 0)
        if (
            vol_target > 0
            and fast_now is not None
            and slow_now is not None
            and fast_now > 1e-9
        ):
            cap = float(merged.get("volTargetCap", 0) or 0)
            alloc *= min(cap if cap > 0 else 2.0, max(0.2, slow_now / fast_now))

        qty = int(alloc // per_lot) if per_lot > 0 else 0
        if qty < 1:
            return SignalResult(
                direction="none",
                price=last.c,
                stop=round(stop, 2),
                qty=0,
                reason="资金不足，1 手保证金超过可用资金",
            )

        trend_text = "上行" if direction == "long" else "下行"
        return SignalResult(
            direction=direction,
            price=last.c,
            stop=round(stop, 2),
            qty=qty,
            reason=f"MP 趋势{trend_text}，波动率分位 {pct:.0f}%",
        )

    def backtest(
        self,
        bars: list[Bar],
        params: dict,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """逐日撮合回测，返回交易明细、权益曲线和统计指标。"""
        merged = {**self.default_params, **params}
        lookback = int(merged["lookback"])
        min_pct = float(merged["minPct"])
        if len(bars) < max(lookback + 1, int(merged["atrPeriod"]) + 2):
            raise ValueError("历史数据不足")

        # 逐日方向 → 准确率门控 → 逐日撮合；预计算序列统一走 cache
        cache = _CoreCache(bars)
        preds, _, devs = cache.directions(lookback, min_pct)
        warn = _acc_warn_idx(
            bars,
            preds,
            int(merged.get("accWindow") or 3),
            int(merged.get("accStreak") or 3),
        )
        trading_preds, rev_idx = _gate_preds(
            preds, warn, str(merged.get("accMode") or "off")
        )

        bt_start = 0
        if start:
            bt_start = next(
                (i for i, bar in enumerate(bars) if bar.d >= start),
                len(bars),
            )
        bt_end = len(bars)
        if end:
            bt_end = next(
                (i for i, bar in enumerate(bars) if bar.d > end),
                len(bars),
            )
        if bt_end - bt_start < 2:
            raise ValueError("所选区间无数据或数据不足")

        mult = float(merged.get("mult") or 1)
        core = _run_core(
            bars, merged, mult, trading_preds, rev_idx, bt_start, bt_end, cache
        )

        # 复盘指标：与 bars 按下标一一对齐，前端悬停时直接取用。
        # pred 是当日收盘信号（次日开盘执行），trade_pred 是门控后的实际信号。
        # 斜率不参与信号时也照常计算，图表悬停要用。
        slope_series = cache.slope(int(merged.get("slopeDays") or 30))
        warn_set = set(warn)
        indicators = []
        for i, bar in enumerate(bars):
            fast = core["fast_vol"][i]
            slow = core["slow_vol"][i]
            ewma_ratio = (
                fast / slow
                if fast is not None and slow is not None and slow > 0
                else None
            )
            dev = devs[i]
            atr_value = core["atr"][i]
            atr_ratio = core["atr_ratio"][i]
            indicators.append(
                {
                    "d": bar.d,
                    "atr": (
                        round(atr_value, 4) if atr_value is not None else None
                    ),
                    "atr_ratio": (
                        round(atr_ratio, 6) if atr_ratio is not None else None
                    ),
                    "ewma_ratio": (
                        round(ewma_ratio, 4) if ewma_ratio is not None else None
                    ),
                    "slope": round(slope_series[i], 6),
                    "mp_dev": round(dev, 4) if dev is not None else None,
                    "pred": preds[i],
                    "trade_pred": trading_preds[i],
                    "warn": i in warn_set,
                    # 持仓中且明日开盘会因方向反转离场
                    "rev_exit": core["rev_exit"][i],
                }
            )

        trades = core["trades"]
        capital = float(merged["capital"])
        wins = [trade for trade in trades if trade["pnl"] > 0]
        losses = [trade for trade in trades if trade["pnl"] <= 0]
        win_sum = sum(trade["pnl"] for trade in wins)
        loss_sum = sum(abs(trade["pnl"]) for trade in losses)
        eq = core["eq"]

        # 方向准确率：只看原始信号（不含门控），统计未来 N 日涨跌
        dir_accuracy: dict[str, float | None] = {}
        for horizon in (1, 3, 5, 10):
            correct = total = 0
            for i in range(bt_start, min(bt_end, len(bars) - 1)):
                pred = preds[i]
                if pred is None or i + horizon >= len(bars):
                    continue
                total += 1
                is_up = bars[i + horizon].c > bars[i].c
                if (pred == "long" and is_up) or (
                    pred == "short" and not is_up
                ):
                    correct += 1
            dir_accuracy[str(horizon)] = (
                round(correct / total * 100, 1) if total else None
            )

        return {
            "strategy": self.key,
            "start": bars[bt_start].d,
            "end": bars[bt_end - 1].d,
            "summary": {
                "capital": capital,
                "end_equity": round(eq),
                "total_return": (
                    round((eq / capital - 1) * 100, 1) if capital > 0 else 0
                ),
                "max_dd": round(core["max_dd"], 1),
                "trades": len(trades),
                "win_rate": (
                    round(len(wins) / len(trades) * 100, 1) if trades else 0
                ),
                "profit_factor": (
                    round(win_sum / loss_sum, 2) if loss_sum > 0 else None
                ),
                "avg_hold": (
                    round(sum(t["hold"] for t in trades) / len(trades), 1)
                    if trades
                    else 0
                ),
                "coverage": round(
                    core["pos_days"] / max(1, bt_end - bt_start) * 100, 1
                ),
                "avg_pnl": (
                    round(sum(t["pnl"] for t in trades) / len(trades))
                    if trades
                    else 0
                ),
            },
            "trades": trades,
            "equity_curve": core["eq_curve"],
            "stop_curve": core["stop_curve"],
            "open_position": core["open_position"],
            "dir_accuracy": dir_accuracy,
            "indicators": indicators,
        }

    # 同步请求能承受的网格上限，超过请缩小候选范围；并行后 10 万组约 10~30 分钟
    MAX_OPT_COMBOS = 100000

    def optimize(
        self,
        bars: list[Bar],
        params: dict,
        grid: dict[str, list[float]],
        start: str | None = None,
        end: str | None = None,
        top: int = 40,
    ) -> dict:
        """参数网格扫描，对应旧页面“优化”按钮。

        在基准参数（保存值回退默认值）上做笛卡尔积，逐组合跑逐日撮合，
        按期末权益排序返回前 top 组；日期区间只限制开仓范围。
        """
        merged = {**self.default_params, **params}
        if len(bars) < max(
            int(merged["lookback"]) + 1, int(merged["atrPeriod"]) + 2
        ):
            raise ValueError("历史数据不足")

        keys: list[str] = []
        value_lists: list[list[float]] = []
        for key, values in grid.items():
            if key not in merged or not values:
                continue
            keys.append(key)
            value_lists.append(list(values))
        if not keys:
            raise ValueError("没有可优化的参数")

        combos = 1
        for values in value_lists:
            combos *= len(values)
        if combos > self.MAX_OPT_COMBOS:
            raise ValueError(
                f"组合数 {combos} 超过上限 {self.MAX_OPT_COMBOS}，请减少优化参数"
            )

        bt_start = 0
        if start:
            bt_start = next(
                (i for i, bar in enumerate(bars) if bar.d >= start), len(bars)
            )
        bt_end = len(bars)
        if end:
            bt_end = next(
                (i for i, bar in enumerate(bars) if bar.d > end), len(bars)
            )
        if bt_end - bt_start < 2:
            raise ValueError("所选区间无数据或数据不足")

        mult = float(merged.get("mult") or 1)
        capital = float(merged["capital"])

        all_combos = list(product(*value_lists))
        workers = min(os.cpu_count() or 1, 8)
        if combos >= 400 and workers > 1:
            # 组合多时分给子进程；spawn 启动有成本，小网格仍走单进程
            chunk_count = workers * 3
            chunks = [all_combos[i::chunk_count] for i in range(chunk_count)]
            mp_ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=mp_ctx,
                initializer=_opt_worker_init,
                initargs=(bars, merged, keys, mult, capital, bt_start, bt_end),
            ) as pool:
                results = [
                    row for part in pool.map(_opt_worker, chunks) for row in part
                ]
        else:
            results = _opt_batch(
                bars, merged, keys, all_combos, mult, capital, bt_start, bt_end
            )

        results.sort(key=lambda item: item["end_equity"], reverse=True)
        return {
            "combos": combos,
            "tested": len(results),
            "results": results[: max(1, top)],
        }
