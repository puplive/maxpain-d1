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

SYMBOLS = ['TA', 'MA', 'SA']
OPT_NAMES = {'TA': 'PTA期权', 'MA': '甲醇期权', 'SA': '纯碱期权'}

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


def _contract_year_month(code: str) -> tuple[int, int] | None:
    """从合约代码提取 (年, 月)  例: TA502C4250 → (2025, 2)"""
    m = re.match(r'[A-Z]{2}(\d)(\d{2})', code.strip())
    if m:
        y = 2020 + int(m.group(1))
        return y, int(m.group(2))
    return None


def _filter_nearby_month(opt_df: pd.DataFrame) -> pd.DataFrame:
    """取最临近合约月（可用数据中合约月最小的）"""
    if opt_df.empty:
        return opt_df
    months = {}
    for code in opt_df['合约代码']:
        ym = _contract_year_month(str(code))
        if ym:
            months[code] = ym
    if not months:
        return opt_df
    min_ym = min(months.values())
    filter_codes = [c for c, ym in months.items() if ym == min_ym]
    return opt_df[opt_df['合约代码'].isin(filter_codes)]


def _fetch_one_option(sym: str, oname: str, trade_date: str, day: str) -> tuple[str, pd.DataFrame]:
    """拉取并处理单个品种的期权数据（用于 ThreadPoolExecutor）"""
    try:
        o = ak.option_hist_czce(symbol=oname, trade_date=trade_date)
        if o.empty:
            return sym, pd.DataFrame()
        o.columns = o.columns.str.strip()
        for c in o.select_dtypes(include='str'):
            o[c] = o[c].str.strip()
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
        o = _filter_nearby_month(o)
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
        if intrinsic < total_cost:
            high = mid if not is_call else low
        else:
            low = mid if not is_call else high
        if high - low < 0.01:
            break
    return round((low + high) / 2, 2)


def _process_date(d: str, ds: str, symbols: list[str], opt_names: dict[str, str]) -> tuple[str, dict]:
    """处理单个日期：期货 + 期权 + 计算，返回 (status, {sym: entry})"""
    # 期货
    try:
        fut = _call_with_timeout(lambda: ak.get_czce_daily(date=ds))
        if fut is _TIME_OUT:
            return ('timeout', {})
        if fut is None or fut.empty:
            return ('empty', {})
        fut['date'] = d
        for c in ['open', 'high', 'low', 'close']:
            fut[c] = pd.to_numeric(fut[c], errors='coerce')
        fut['volume'] = pd.to_numeric(fut['volume'], errors='coerce').fillna(0)
    except Exception:
        return ('empty', {})

    # 期权
    opts: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs = {ex.submit(_fetch_one_option, sym, oname, ds, d): sym for sym, oname in opt_names.items()}
        try:
            for f in as_completed(fs, timeout=_TIMEOUT):
                sym, df = f.result()
                opts[sym] = df
        except TimeoutError:
            return ('timeout', {})

    # 计算
    entries = {}
    for sym in symbols:
        opt = opts.get(sym)
        if opt is None or opt.empty:
            continue
        day_fut = fut[fut['symbol'].astype(str).str.startswith(sym)]
        if day_fut.empty:
            continue
        fr = day_fut.loc[day_fut['volume'].idxmax()]
        px = float(fr['close'])
        if px <= 0:
            continue
        rng = 0.20
        do_mp = opt[opt['strike'].between(px * (1-rng), px * (1+rng))]
        mp = calc_max_pain(do_mp if len(do_mp) > 0 else opt)
        co = float(opt[opt['strike'] > px]['oi'].sum())
        po = float(opt[opt['strike'] < px]['oi'].sum())
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
    symbols = symbols or SYMBOLS
    opt_names = {sym: name for sym, name in OPT_NAMES.items() if sym in symbols}
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
            batch.append((d, ds))

        if not batch:
            break

        # 批量并行处理
        batch_results: dict[str, tuple[str, dict]] = {}
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            fs_map = {}
            for d, ds in batch:
                f = ex.submit(_process_date, d, ds, symbols, opt_names)
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
    for s in (symbols or SYMBOLS):
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
            status, entries = _process_date(d, ds, symbols, opt_names)
            if status == 'timeout':
                still_timed_out.add(d)
                print(f'  ⚠ {d} 重试超时', flush=True)
                continue
            if status == 'empty':
                still_timed_out.add(d)
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
    for s in (symbols or SYMBOLS):
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
    for s in (symbols or SYMBOLS):
        print(f'  {s}: {len(result[s])} 条')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', '-o', default='data.json')
    parser.add_argument('--max-dates', type=int, default=0, help='测试用限制处理日期数')
    parser.add_argument('--recent', type=int, default=0, help='仅处理最近 N 个交易日')
    parser.add_argument('--year', type=int, default=0, help='指定年份，如 2026，只处理该年数据')
    parser.add_argument('--symbol', help='品种，用逗号分隔 (如 TA,MA)；不传则逐个处理')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', ''),
                        help='Worker API 地址，设置后每 100 天中途上传一次')
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''),
                        help='API 密钥')
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''),
                        help='GitHub Token')
    args = parser.parse_args()

    upload = args.worker_url and args.api_key
    all_data = {}

    if args.symbol:
        syms = [s.strip() for s in args.symbol.split(',')]
        data = run(max_dates=args.max_dates, recent=args.recent, year=args.year, symbols=syms,
                   worker_url=args.worker_url if upload else None,
                   api_key=args.api_key if upload else None,
                   gh_token=args.gh_token)
        all_data.update(data)
        if upload:
            for sym in syms:
                if data.get(sym):
                    _upload_batch(sym, data[sym], args.worker_url, args.api_key, args.gh_token)
    else:
        for sym in SYMBOLS:
            print(f'\n{"="*40}')
            print(f'处理品种: {sym}')
            print(f'{"="*40}')
            data = run(max_dates=args.max_dates, recent=args.recent, year=args.year, symbols=[sym],
                       worker_url=args.worker_url if upload else None,
                       api_key=args.api_key if upload else None,
                       gh_token=args.gh_token)
            all_data[sym] = data.get(sym, [])
            if upload and all_data[sym]:
                _upload_batch(sym, all_data[sym], args.worker_url, args.api_key, args.gh_token)

    out = Path(args.output)
    out.write_text(json.dumps(all_data, ensure_ascii=False))
    total = sum(len(v) for v in all_data.values())
    print(f'📦 {total} 条 → {out}')


if __name__ == '__main__':
    main()
