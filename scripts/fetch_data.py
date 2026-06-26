"""从 AKShare 获取郑商所期货 + 期权数据，计算 Max Pain

期货: get_czce_daily(date) — 单日全部 CZCE 合约
期权: option_hist_czce(symbol, date) — 单日单品种全部期权

首次全量: ~1500 交易日 × 4 调用 × 0.3s ≈ 30min
增量: --max-dates 10
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import pandas as pd

try:
    import akshare as ak
except ImportError:
    print("请先安装: pip install akshare")
    sys.exit(1)

# 品种配置: 代码 → {名称, 交易所, 合约乘数}
# 交易所决定使用哪个 AKShare 函数拉取期货和期权数据
# 所有品种配置: 代码 → {名称, 交易所, 合约乘数}
# active=True 的品种是默认启用的产业成熟品种
# active=False 的品种仍然支持通过 --symbol 手动指定
SYMBOL_CFG: dict[str, dict] = {
    # ===== CZCE (郑商所) =====
    'TA': {'name': 'PTA期权',              'exchange': 'czce', 'mult': 5},
    'MA': {'name': '甲醇期权',             'exchange': 'czce', 'mult': 10},
    'SA': {'name': '纯碱期权',             'exchange': 'czce', 'mult': 20},
    'SR': {'name': '白糖期权',             'exchange': 'czce', 'mult': 10},
    'CF': {'name': '棉花期权',             'exchange': 'czce', 'mult': 5},
    'RM': {'name': '菜粕期权',             'exchange': 'czce', 'mult': 10},
    'OI': {'name': '菜油期权',             'exchange': 'czce', 'mult': 10},
    'PK': {'name': '花生期权',             'exchange': 'czce', 'mult': 5},
    'PF': {'name': '短纤期权',             'exchange': 'czce', 'mult': 5},
    'SM': {'name': '锰硅期权',             'exchange': 'czce', 'mult': 5},
    'SF': {'name': '硅铁期权',             'exchange': 'czce', 'mult': 5},
    'UR': {'name': '尿素期权',             'exchange': 'czce', 'mult': 20},
    'AP': {'name': '苹果期权',             'exchange': 'czce', 'mult': 10},
    'CJ': {'name': '红枣期权',             'exchange': 'czce', 'mult': 5},
    'FG': {'name': '玻璃期权',             'exchange': 'czce', 'mult': 20},
    'PX': {'name': '对二甲苯期权',          'exchange': 'czce', 'mult': 5},
    'SH': {'name': '烧碱期权',             'exchange': 'czce', 'mult': 10},
    # ===== DCE (大商所) =====
    'C':  {'name': '玉米期权',             'exchange': 'dce', 'mult': 10},
    'M':  {'name': '豆粕期权',             'exchange': 'dce', 'mult': 10},
    'I':  {'name': '铁矿石期权',           'exchange': 'dce', 'mult': 100},
    'PG': {'name': '液化石油气期权',        'exchange': 'dce', 'mult': 20},
    'L':  {'name': '聚乙烯期权',           'exchange': 'dce', 'mult': 5},
    'V':  {'name': '聚氯乙烯期权',         'exchange': 'dce', 'mult': 5},
    'PP': {'name': '聚丙烯期权',           'exchange': 'dce', 'mult': 5},
    'P':  {'name': '棕榈油期权',           'exchange': 'dce', 'mult': 10},
    'A':  {'name': '豆一期权',             'exchange': 'dce', 'mult': 10},
    'B':  {'name': '豆二期权',             'exchange': 'dce', 'mult': 10},
    'Y':  {'name': '豆油期权',             'exchange': 'dce', 'mult': 10},
    'EG': {'name': '乙二醇期权',           'exchange': 'dce', 'mult': 10},
    'EB': {'name': '苯乙烯期权',           'exchange': 'dce', 'mult': 5},
    'JD': {'name': '鸡蛋期权',             'exchange': 'dce', 'mult': 5},
    'CS': {'name': '玉米淀粉期权',         'exchange': 'dce', 'mult': 10},
    'LH': {'name': '生猪期权',             'exchange': 'dce', 'mult': 16},
    'LG': {'name': '原木期权',             'exchange': 'dce', 'mult': 90},
    # ===== SHFE (上期所) =====
    'CU': {'name': '铜期权',               'exchange': 'shfe', 'mult': 5},
    'AL': {'name': '铝期权',               'exchange': 'shfe', 'mult': 5},
    'ZN': {'name': '锌期权',               'exchange': 'shfe', 'mult': 5},
    'PB': {'name': '铅期权',               'exchange': 'shfe', 'mult': 5},
    'RB': {'name': '螺纹钢期权',           'exchange': 'shfe', 'mult': 10},
    'NI': {'name': '镍期权',               'exchange': 'shfe', 'mult': 1},
    'SN': {'name': '锡期权',               'exchange': 'shfe', 'mult': 1},
    'AU': {'name': '黄金期权',             'exchange': 'shfe', 'mult': 1000},
    'AG': {'name': '白银期权',             'exchange': 'shfe', 'mult': 15},
    'RU': {'name': '橡胶期权',             'exchange': 'shfe', 'mult': 10},
    'BR': {'name': '丁二烯橡胶期权',        'exchange': 'shfe', 'mult': 5},
    'AO': {'name': '氧化铝期权',           'exchange': 'shfe', 'mult': 20},
    # ===== INE (能源中心, 通过 SHFE 函数获取) =====
    'SC': {'name': '原油期权',             'exchange': 'ine', 'mult': 1000},
    'NR': {'name': '20号胶期权',           'exchange': 'ine', 'mult': 10},
}

# 默认活跃品种（产业成熟，期权流动性好）
# 不传 --symbol 时默认处理这些
DEFAULT_SYMBOLS = [
    'TA', 'MA', 'SA', 'SR', 'CF', 'RM', 'OI',    # CZCE
    'C', 'M', 'I', 'PG', 'L', 'V', 'PP', 'P', 'Y',  # DCE
    'CU', 'AL', 'ZN', 'RB', 'AU', 'AG', 'RU',      # SHFE
    'SC',                                            # INE
]

# AKShare 期权函数分发表
EXCHANGE_OPTION_FUNC = {
    'czce': ak.option_hist_czce,
    'dce': ak.option_hist_dce,
    'shfe': ak.option_hist_shfe,
    'ine': ak.option_hist_shfe,
}

# AKShare 期货函数分发表
EXCHANGE_FUTURES_FUNC = {
    'czce': ak.get_czce_daily,
    'dce': ak.get_dce_daily,
    'shfe': ak.get_shfe_daily,
    'ine': ak.get_shfe_daily,
}

# 各交易所期货数据列名映射 → 统一英文名
_FUTURES_COLUMN_MAP = {
    'czce': {},  # CZCE 返回英文列名
    'dce': {
        '合约代码': 'symbol',
        '开盘价': 'open',
        '最高价': 'high',
        '最低价': 'low',
        '收盘价': 'close',
        '成交量': 'volume',
    },
    'shfe': {
        '合约代码': 'symbol',
        '开盘价': 'open',
        '最高价': 'high',
        '最低价': 'low',
        '收盘价': 'close',
        '成交量': 'volume',
    },
    'ine': {
        '合约代码': 'symbol',
        '开盘价': 'open',
        '最高价': 'high',
        '最低价': 'low',
        '收盘价': 'close',
        '成交量': 'volume',
    },
}

# 各交易所期权数据列名映射 → 统一中文名
_OPTION_COLUMN_MAP = {
    'czce': {},  # CZCE 列名正确
    'dce': {},   # DCE 可能不同, 运行时检测
    'shfe': {},  # SHFE 可能不同, 运行时检测
    'ine': {},
}

_UPLOAD_INTERVAL = 100  # 每处理多少天中途上传一次
_TIMEOUT = 30  # 单次 API 调用超时（秒），有重试兜底
_MAX_RETRY = 3  # 超时日期最大重试次数
_timeout_executor = ThreadPoolExecutor(max_workers=1)

_TIME_OUT = object()


def _call_with_timeout(fn, timeout=_TIMEOUT):
    """调用 fn()，超时返回 _TIME_OUT"""
    try:
        return _timeout_executor.submit(fn).result(timeout=timeout)
    except:
        return _TIME_OUT


def _upload_batch(symbol: str, records: list[dict], worker_url: str, api_key: str, gh_token: str = ''):
    """上传一批数据到 Worker API"""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    if gh_token:
        headers['X-GitHub-Token'] = gh_token
    payload = json.dumps({'symbol': symbol, 'data': records}).encode('utf-8')
    req = Request(f'{worker_url}/api/update', data=payload, headers=headers, method='POST')
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            if result.get('ok'):
                print(f'    ↗ {symbol}: {result["count"]} 条已上传', flush=True)
            else:
                print(f'    ⚠ {symbol}: {result}', flush=True)
    except HTTPError as e:
        print(f'    ⚠ {symbol}: HTTP {e.code} {e.read().decode()[:100]}', flush=True)
    except Exception as e:
        print(f'    ⚠ {symbol}: {e}', flush=True)





def parse_opt_code(code: str) -> tuple[float, str] | None:
    m = re.search(r'([CP])(\d+\.?\d*)$', code.strip())
    if m:
        return float(m.group(2)), m.group(1)
    return None



_TRADE_DAYS: set[str] | None = None


def _load_trade_days() -> set[str] | None:
    """加载 A 股交易日历，缓存到全局变量"""
    global _TRADE_DAYS
    if _TRADE_DAYS is not None:
        return _TRADE_DAYS
    try:
        df = ak.tool_trade_date_hist_sina()
        _TRADE_DAYS = set(pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d').tolist())
        print(f'  交易日历: {len(_TRADE_DAYS)} 天', flush=True)
        return _TRADE_DAYS
    except Exception as e:
        print(f'  ⚠ 加载交易日历失败: {e}，将逐个尝试', flush=True)
        return None



def _fetch_one_option(sym: str, oname: str, exchange: str, trade_date: str, day: str) -> tuple[str, pd.DataFrame]:
    """拉取并处理单个品种的期权数据（根据交易所分发到不同 AKShare 函数）"""
    try:
        func = EXCHANGE_OPTION_FUNC.get(exchange)
        if not func:
            return sym, pd.DataFrame()
        o = func(symbol=oname, trade_date=trade_date)
        if o.empty:
            return sym, pd.DataFrame()
        o.columns = o.columns.str.strip()
        for c in o.select_dtypes(include='str'):
            o[c] = o[c].str.strip()
        # 列名映射（如有需要）
        col_map = _OPTION_COLUMN_MAP.get(exchange, {})
        if col_map:
            o = o.rename(columns=col_map)
        parsed = o['合约代码'].apply(parse_opt_code)
        o['strike'] = parsed.apply(lambda x: x[0] if x else None)
        o['type'] = parsed.apply(lambda x: x[1] if x else None)
        o = o.dropna(subset=['strike'])
        o['strike'] = o['strike'].astype(float)
        o['oi'] = pd.to_numeric(o['持仓量'], errors='coerce').fillna(0)
        o['close'] = pd.to_numeric(o['今收盘'], errors='coerce').fillna(0)
        o['volume'] = pd.to_numeric(o['成交量(手)'], errors='coerce').fillna(0)
        o['iv'] = pd.to_numeric(o['隐含波动率'], errors='coerce')
        o['delta'] = pd.to_numeric(o['DELTA'], errors='coerce')
        return sym, o
    except Exception:
        return sym, pd.DataFrame()


def calc_max_pain(opt_df: pd.DataFrame) -> int:
    if opt_df.empty:
        return 0
    strikes = sorted(opt_df['strike'].unique())
    best_s, best_val = 0, float('inf')
    for s in strikes:
        val = 0.0
        calls = opt_df[(opt_df['strike'] < s) & (opt_df['type'] == 'C')]
        puts = opt_df[(opt_df['strike'] > s) & (opt_df['type'] == 'P')]
        if not calls.empty:
            val += ((s - calls['strike']) * calls['oi']).sum()
        if not puts.empty:
            val += ((puts['strike'] - s) * puts['oi']).sum()
        if val < best_val:
            best_val, best_s = val, s
    return int(best_s)


def calc_be(opt_df: pd.DataFrame, px: float, is_call: bool) -> float | None:
    filtered = opt_df[opt_df['type'] == ('C' if is_call else 'P')]
    if filtered.empty:
        return None
    total_cost = (filtered['close'] * filtered['oi']).sum()
    total_oi = filtered['oi'].sum()
    if total_oi == 0 or total_cost == 0:
        return None
    low, high = px * 0.7, px * 1.3
    for _ in range(100):
        mid = (low + high) / 2
        if is_call:
            intrinsic = (np.maximum(mid - filtered['strike'].values, 0) * filtered['oi'].values).sum()
        else:
            intrinsic = (np.maximum(filtered['strike'].values - mid, 0) * filtered['oi'].values).sum()
        if is_call:
            if intrinsic < total_cost:
                low = mid    # 看涨：现价太低，需提高
            else:
                high = mid   # 看涨：现价太高，需降低
        else:
            if intrinsic < total_cost:
                high = mid   # 看跌：现价太高，需降低
            else:
                low = mid    # 看跌：现价太低，需提高
        if high - low < 0.01:
            break
    return round((low + high) / 2, 2)


def _process_date(d: str, ds: str, symbols: list[str], cfg: dict[str, dict]) -> tuple[str, dict]:
    """处理单个日期：按交易所分组获取期货和期权数据，计算指标"""
    # 按交易所分组品种
    by_exchange: dict[str, list[str]] = {}
    for sym in symbols:
        if sym in cfg:
            exc = cfg[sym]['exchange']
            by_exchange.setdefault(exc, []).append(sym)
    if not by_exchange:
        return ('empty', {})

    # 期货：每个交易所调用对应函数
    per_exchange_futures: dict[str, pd.DataFrame] = {}
    for exc, syms in by_exchange.items():
        fut_func = EXCHANGE_FUTURES_FUNC.get(exc)
        if not fut_func:
            continue
        try:
            fut = _call_with_timeout(lambda: fut_func(date=ds))
            if fut is _TIME_OUT:
                return ('timeout', {})
            if fut is None or fut.empty:
                continue
            fut['date'] = d
            # 列名标准化
            col_map = _FUTURES_COLUMN_MAP.get(exc, {})
            if col_map:
                fut = fut.rename(columns=col_map)
            for c in ['open', 'high', 'low', 'close']:
                fut[c] = pd.to_numeric(fut[c], errors='coerce')
            fut['volume'] = pd.to_numeric(fut['volume'], errors='coerce').fillna(0)
            per_exchange_futures[exc] = fut
        except Exception:
            continue

    if not per_exchange_futures:
        return ('empty', {})

    # 期权：并行拉取所有品种（按交易所分发函数）
    opts: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs = {}
        for sym in symbols:
            if sym not in cfg:
                continue
            oname = cfg[sym]['name']
            exchange = cfg[sym]['exchange']
            f = ex.submit(_fetch_one_option, sym, oname, exchange, ds, d)
            fs[f] = sym
        try:
            for f in as_completed(fs, timeout=_TIMEOUT):
                sym, df = f.result()
                opts[sym] = df
        except TimeoutError:
            return ('timeout', {})

    # 计算
    entries = {}
    for sym in symbols:
        if sym not in cfg:
            continue
        exchange = cfg[sym]['exchange']
        fut = per_exchange_futures.get(exchange)
        opt = opts.get(sym)
        if fut is None or opt is None or opt.empty:
            continue
        day_fut = fut[fut['symbol'].astype(str).str.upper().str.startswith(sym)]
        if day_fut.empty:
            continue
        fr = day_fut.loc[day_fut['volume'].idxmax()]
        px = float(fr['close'])
        if px <= 0:
            continue
        rng = 0.20
        do_mp = opt[opt['strike'].between(px * (1-rng), px * (1+rng))]
        mp = calc_max_pain(do_mp if len(do_mp) > 0 else opt)
        co = float(opt[(opt['strike'] > px) & (opt['type'] == 'C')]['oi'].sum())
        po = float(opt[(opt['strike'] < px) & (opt['type'] == 'P')]['oi'].sum())
        do_be = opt[opt['strike'].between(px * (1-rng), px * (1+rng))]
        bec = calc_be(do_be, px, True) if len(do_be) > 0 else None
        bep = calc_be(do_be, px, False) if len(do_be) > 0 else None
        cv = float(opt[opt['type'] == 'C']['volume'].sum())
        pv = float(opt[opt['type'] == 'P']['volume'].sum())
        vr = round(cv / pv, 2) if pv > 0 else None
        civ = opt[(opt['type'] == 'C') & (opt['delta'].between(0.20, 0.30))]['iv'].mean()
        piv = opt[(opt['type'] == 'P') & (opt['delta'].between(-0.30, -0.20))]['iv'].mean()
        ivs = round(piv - civ, 4) if (pd.notna(civ) and pd.notna(piv)) else None
        entries[sym] = {
            'd': d, 'o': round(float(fr['open']), 2), 'c': round(px, 2),
            'h': round(float(fr['high']), 2), 'l': round(float(fr['low']), 2),
            'mp': mp, 'co': co, 'po': po,
            'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs,
        }

    return ('ok' if entries else 'empty', entries)


def run(max_dates: int = 0, recent: int = 0, year: int = 0, symbols: list[str] | None = None,
        worker_url: str | None = None, api_key: str | None = None,
        gh_token: str = '') -> dict:
    """逐日获取并处理数据（从最新一天往前，连续30天无数据自动停）"""
    symbols = symbols or list(DEFAULT_SYMBOLS)
    cfg_subset = {sym: SYMBOL_CFG[sym] for sym in symbols if sym in SYMBOL_CFG}
    result = {s: [] for s in symbols}
    t0 = time.time()
    MAX_EMPTY = 30
    empty_days = {s: 0 for s in symbols}
    success = 0
    today = datetime.now()

    # year 模式：从该年最后一天往前，超出该年即停
    if year:
        year_end = datetime(year, 12, 31)
        ref_date = min(today, year_end)
        year_start = datetime(year, 1, 1)
    else:
        ref_date = today

    limit = recent or max_dates or 0
    day_offset = 0
    processed = 0
    timed_out: set[str] = set()

    trade_days = _load_trade_days()

    BATCH_SIZE = 30 if limit == 0 else 1
    while True:
        # 收集一批日期
        batch = []
        while len(batch) < BATCH_SIZE:
            d = (ref_date - timedelta(days=day_offset)).strftime('%Y-%m-%d')
            ds = d.replace('-', '')
            day_offset += 1
            if limit > 0 and processed >= limit:
                break
            if year and d < str(year):
                break
            # 交易日历过滤：非交易日跳过，不计入空日
            if trade_days is not None and d not in trade_days:
                continue
            batch.append((d, ds))

        if not batch:
            break

        # 批量并行处理
        batch_results: dict[str, tuple[str, dict]] = {}
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            fs_map = {}
            for d, ds in batch:
                f = ex.submit(_process_date, d, ds, symbols, cfg_subset)
                fs_map[f] = d
            for f in as_completed(fs_map):
                d = fs_map[f]
                batch_results[d] = f.result()

        # 从新到旧处理空日计数
        stop = False
        for d in sorted(batch_results.keys(), reverse=True):
            status, entries = batch_results[d]
            if status == 'timeout':
                timed_out.add(d)
                continue
            if status == 'empty':
                if limit == 0:
                    for sym in symbols:
                        empty_days[sym] += 1
                    if all(empty_days[s] >= MAX_EMPTY for s in symbols):
                        print(f'  连续 {MAX_EMPTY} 天无数据，提前终止', flush=True)
                        stop = True
                        break
                continue
            # status == 'ok'
            got_any = False
            for sym, entry in entries.items():
                result[sym].append(entry)
                got_any = True
            processed += 1
            success += 1
            if limit == 0:
                for sym in symbols:
                    empty_days[sym] = 0 if got_any else empty_days[sym] + 1

        if stop:
            break

        if success % 50 == 0:
            elapsed = time.time() - t0
            print(f'  进度: {day_offset}天尝试, {success}成功, {elapsed:.0f}s', flush=True)
            if worker_url and api_key:
                for sym in symbols:
                    if result[sym]:
                        _upload_batch(sym, result[sym], worker_url, api_key, gh_token)

    # 恢复为按日期正序
    for s in symbols:
        result[s].sort(key=lambda r: r['d'])

    # ── 重试超时日期 ──
    retry_round = 0
    while timed_out and retry_round < _MAX_RETRY:
        retry_round += 1
        print(f'\n{"="*40}')
        print(f'第 {retry_round} 轮重试 ({len(timed_out)} 天)')
        print(f'{"="*40}')
        still_timed_out: set[str] = set()
        for d in sorted(timed_out):
            ds = d.replace('-', '')
            status, entries = _process_date(d, ds, symbols, cfg_subset)
            if status == 'timeout':
                still_timed_out.add(d)
                print(f'  ⚠ {d} 重试超时', flush=True)
                continue
            if status == 'empty':
                # 空数据说明 API 正常但无数据，不重试
                continue
            got_any = False
            for sym, entry in entries.items():
                result[sym].append(entry)
                got_any = True
            if not got_any:
                still_timed_out.add(d)
            elif worker_url and api_key:
                for sym in symbols:
                    new_entries = [e for e in result[sym] if e['d'] == d]
                    if new_entries:
                        _upload_batch(sym, new_entries, worker_url, api_key, gh_token)

        timed_out = still_timed_out

    # 去重：保留每个日期最后一条（重试覆盖旧数据）
    for s in symbols:
        seen = {}
        for entry in result[s]:
            seen[entry['d']] = entry
        result[s] = list(seen.values())
        result[s].sort(key=lambda r: r['d'])

    elapsed = time.time() - t0
    print(f'✅ 完成: {success} 天 ({elapsed:.0f}s)')
    if timed_out:
        failed = sorted(timed_out)
        print(f'⚠ 以下 {len(failed)} 天重试 {_MAX_RETRY} 次后仍超时: {", ".join(failed[:20])}{"..." if len(failed) > 20 else ""}', flush=True)
    else:
        print(f'所有超时日期已重试完成', flush=True)
    for s in symbols:
        print(f'  {s}: {len(result[s])} 条')
    return result


def _normalize_symbols(raw: str) -> list[str]:
    """解析用户输入的品种参数，空=默认活跃品种"""
    if not raw:
        return list(DEFAULT_SYMBOLS)
    syms = []
    for s in raw.split(','):
        s = s.strip().upper()
        if s in SYMBOL_CFG:
            syms.append(s)
        else:
            print(f'⚠ 未知品种: {s}，可用: {", ".join(SYMBOL_CFG.keys())}')
            sys.exit(1)
    return syms


def _load_db_symbols(worker_url: str, api_key: str, gh_token: str = '') -> list[str] | None:
    """查询 Worker API 获取数据库中已有的品种列表，失败返回 None"""
    headers = {'Authorization': f'Bearer {api_key}'}
    if gh_token:
        headers['X-GitHub-Token'] = gh_token
    req = Request(f'{worker_url}/api/symbols', headers=headers, method='GET')
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get('symbols', [])
    except Exception as e:
        print(f'  ⚠ 查询 DB 品种失败: {e}', flush=True)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', '-o', default='data.json')
    parser.add_argument('--max-dates', type=int, default=0, help='测试用限制处理日期数')
    parser.add_argument('--recent', type=int, default=0, help='仅处理最近 N 个交易日')
    parser.add_argument('--year', type=int, default=0, help='指定年份，如 2026，只处理该年数据')
    parser.add_argument('--symbol', default='',
                        help='品种，用逗号分隔 (如 TA,MA,SA)，空=查 DB 或默认品种')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', ''),
                        help='Worker API 地址')
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''),
                        help='API 密钥')
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''),
                        help='GitHub Token')
    args = parser.parse_args()

    upload = args.worker_url and args.api_key

    # 确定品种列表
    if args.symbol:
        syms = _normalize_symbols(args.symbol)
    elif args.worker_url and args.api_key:
        db_syms = _load_db_symbols(args.worker_url, args.api_key, args.gh_token)
        if db_syms:
            # 只取 DB 已有且在配置中的品种
            syms = [s for s in db_syms if s in SYMBOL_CFG]
            print(f'从 DB 获取 {len(syms)} 个品种: {", ".join(syms)}')
        else:
            syms = list(DEFAULT_SYMBOLS)
            print(f'⚠ 查询 DB 失败，回退到默认 {len(syms)} 个品种')
    else:
        syms = list(DEFAULT_SYMBOLS)

    # 非交易日跳过（recent 模式，避免周末假日空跑）
    if args.recent and not args.symbol:
        trade_days = _load_trade_days()
        today_str = datetime.now().strftime('%Y-%m-%d')
        if trade_days is not None and today_str not in trade_days:
            print(f'{today_str} 非交易日，跳过')
            return

    print(f'处理品种: {", ".join(syms)} ({len(syms)} 个)')
    all_data = run(max_dates=args.max_dates, recent=args.recent, year=args.year, symbols=syms,
                   worker_url=args.worker_url if upload else None,
                   api_key=args.api_key if upload else None,
                   gh_token=args.gh_token)

    if upload:
        for sym, records in all_data.items():
            if records:
                _upload_batch(sym, records, args.worker_url, args.api_key, args.gh_token)

    out = Path(args.output)
    out.write_text(json.dumps(all_data, ensure_ascii=False))
    total = sum(len(v) for v in all_data.values())
    print(f'📦 {total} 条 → {out}')


if __name__ == '__main__':
    main()
