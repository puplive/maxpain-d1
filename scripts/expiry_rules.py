"""期权最后交易日规则（唯一维护入口）。

各交易所的到期日规则会调整、新品种也会带来个别口径，这里按
「交易所 + 品种 + 交割月」集中维护，转换脚本只调用 last_trading_day()。

交易日历约定：{'YYYY-MM': ['YYYY-MM-DD', ...]}，按升序排列；未来月份可以缺失，
缺失时返回 None，由调用方回退近似值。

规则依据（2026-11 核对原始行情后整理）：

DCE 大商所
  - 交割月 <= 2412：交割月前一个月第 5 个交易日
  - 交割月 >= 2501：交割月前一个月第 12 个交易日（大商所〔2023〕65 号）
  - C/M 无特殊规则（旧脚本曾误用"前二个月"）
  - A/Y 系列期权（大商所〔2026〕52 号）：前两个月第 12 个交易日；
    目前原始数据里还没有系列期权合约，待挂牌后按合约码区分再接入。

CZCE 郑商所
  - 交割月 <= 1904：交割月前两个月倒数第 5 个交易日（旧规）
  - 1905 ~ 2312：交割月前一个月第 3 个交易日
    白糖 1905/1907 仍按旧规，1909 起才用新规，故 SR 单独列出
  - 交割月 >= 2401：交割月前一个月第 15 个日历日（含）之前的倒数第 3 个交易日
    （2022-11-04 公告，2401 合约对应的期权起实施）
  - AP/CJ/PX（系列期权）：交割月前两个月倒数第 3 个交易日
  - 交割月 >= 2711：常规期权改为前一个月第 13 个交易日，
    系列期权改为前两个月第 13 个交易日（2026-11-06 公告）
  - 2026-08-28 公告的 CF/OI/RM 系列期权（前两个月 15 日含前倒数第 3）
    尚未出现在原始数据中，待有合约后再补。

SHFE 上期所
  - 常规：交割月前一个月倒数第 5 个交易日
  - FU 燃料油：倒数第 10 个交易日（2025-09-10 上市）
  - SC 原油：倒数第 13 个交易日
"""

DCE_SERIES_RULE_FROM = (2026, 6)   # 预留：A/Y 系列期权
CZCE_SERIES_RULE_FROM = (2026, 9)  # 预留：CF/OI/RM 系列期权

# CZCE 常规期权「交割月前一月第 3 个交易日」规则的起始交割月；
# 表中没有的品种默认从 1905 起（之后上市的品种自然都落在这条规则里）。
_CZCE_THIRD_DAY_FROM = {
    'SR': (2019, 9),  # 白糖 1905/1907 仍按旧规
    'CF': (2019, 5),  # 棉花期权 2019-01-21 公告
}

# SHFE 按交割月前一月倒数第 N 个交易日到期的品种
_SHFE_COUNTDOWN = {
    'SC': 13,
    'FU': 10,
}


def last_trading_day(exchange, symbol, delivery_year, delivery_month, calendar):
    """返回期权最后交易日 'YYYY-MM-DD'；日历不完整时返回 None。

    delivery_year/month 是标的期货合约的交割年月。
    """
    symbol = (symbol or '').upper()
    fn = {'DCE': _dce, 'CZCE': _czce, 'SHFE': _shfe}.get(exchange.upper())
    if fn is None:
        raise ValueError(f'unknown exchange: {exchange}')
    return fn(symbol, delivery_year, delivery_month, calendar)


def prev_month(year, month, k=1):
    """往前推 k 个月，返回 (year, month)。"""
    month -= k
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def _month_dates(calendar, year, month):
    return calendar.get(f'{year:04d}-{month:02d}', [])


def _nth_from_start(dates, n):
    """从月初数第 n 个交易日。"""
    return dates[n - 1] if len(dates) >= n else None


def _nth_from_end(dates, n):
    """倒数第 n 个交易日。"""
    return dates[-n] if len(dates) >= n else None


def _dce(symbol, year, month, calendar):
    # 2025-01 系列起改为第 12 个交易日，之前是第 5 个
    em_y, em_m = prev_month(year, month, 1)
    dates = _month_dates(calendar, em_y, em_m)
    if (year, month) >= (2025, 1):
        return _nth_from_start(dates, 12)
    return _nth_from_start(dates, 5)


def _czce(symbol, year, month, calendar):
    if symbol in ('AP', 'CJ', 'PX'):
        # 系列期权：前两个月
        em_y, em_m = prev_month(year, month, 2)
        dates = _month_dates(calendar, em_y, em_m)
        if (year, month) >= (2027, 11):
            return _nth_from_start(dates, 13)
        return _nth_from_end(dates, 3)

    em_y, em_m = prev_month(year, month, 1)
    dates = _month_dates(calendar, em_y, em_m)

    if (year, month) >= (2027, 11):
        return _nth_from_start(dates, 13)

    if (year, month) >= (2024, 1):
        pool = [d for d in dates if int(d[8:10]) <= 15]
        return _nth_from_end(pool, 3)

    start = _CZCE_THIRD_DAY_FROM.get(symbol, (2019, 5))
    if (year, month) >= start:
        return _nth_from_start(dates, 3)

    # 旧规：前两个月倒数第 5 个交易日
    em_y, em_m = prev_month(year, month, 2)
    return _nth_from_end(_month_dates(calendar, em_y, em_m), 5)


def _shfe(symbol, year, month, calendar):
    em_y, em_m = prev_month(year, month, 1)
    dates = _month_dates(calendar, em_y, em_m)
    return _nth_from_end(dates, _SHFE_COUNTDOWN.get(symbol, 5))
