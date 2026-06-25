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
_TIMEOUT = 45  # 单次 API 调用超时（秒）
_timeout_executor = ThreadPoolExecutor(max_workers=1)


def _call_with_timeout(fn, timeout=_TIMEOUT):
    """调用 fn()，超时返回 None"""
    try:
        return _timeout_executor.submit(fn).result(timeout=timeout)
    except:
        return None


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



def make_calendar() -> list[str]:
    """生成交易日列表（2020-01-01 ~ 至今，过滤周末）"""
    try:
        df = ak.tool_trade_date_hist_sina()
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
        cal = [d for d in sorted(df['trade_date']) if d >= '2020-01-01']
        print(f'交易日历: {len(cal)} 天', flush=True)
        return cal
    except Exception as e:
        print(f'⚠ 交易日历失败: {e}，使用日历天过滤周末', flush=True)
        cal = []
        d = datetime(2020, 1, 1)
        end = datetime.now()
        while d <= end:
            if d.weekday() < 5:
                cal.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
        return cal


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


def _filter_nearby_month(opt_df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """过滤到近月合约"""
    if opt_df.empty:
        return opt_df
    td = datetime.strptime(trade_date, '%Y-%m-%d')
    months = {}
    for code in opt_df['合约代码']:
        ym = _contract_year_month(str(code))
        if ym:
            y, m = ym
            dt = datetime(y, m, 1)
            months[code] = dt
    if not months:
        return opt_df
    # 找离 trade_date 最近的未来月份
    nearest_code = None
    nearest_dt = None
    for code, dt in months.items():
        if dt < datetime(td.year, td.month, 1):
            continue  # 已到期
        if nearest_dt is None or dt < nearest_dt:
            nearest_dt = dt
            nearest_code = code
    if nearest_code is None:
        return opt_df
    # 得到该月份的所有合约
    ym = months[nearest_code]
    filter_codes = [c for c, dt in months.items() if dt == ym]
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
        o = _filter_nearby_month(o, day)
        return sym, o
    except Exception:
        return sym, pd.DataFrame()


def calc_max_pain(opt_df: pd.DataFrame) -> int:
    if opt_df.empty:
        return 0
    strikes = sorted(opt_df['strike'].unique())
    best_s, best_loss = 0, -1
    for s in strikes:
        loss = 0.0
        calls = opt_df[(opt_df['strike'] > s) & (opt_df['type'] == 'C')]
        puts = opt_df[(opt_df['strike'] < s) & (opt_df['type'] == 'P')]
        if not calls.empty:
            loss += ((calls['strike'] - s) * calls['oi']).sum()
        if not puts.empty:
            loss += ((s - puts['strike']) * puts['oi']).sum()
        if loss > best_loss:
            best_loss, best_s = loss, s
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


def run(max_dates: int = 0, recent: int = 0, symbols: list[str] | None = None,
        worker_url: str | None = None, api_key: str | None = None,
        gh_token: str = '') -> dict:
    """逐日获取并处理数据"""
    symbols = symbols or SYMBOLS
    cal = make_calendar()
    if recent > 0:
        cal = cal[-recent:]
    elif max_dates > 0:
        cal = cal[:max_dates]

    # 倒序遍历：最新数据优先处理，用户能更快看到近期数据
    cal = list(reversed(cal))

    result = {s: [] for s in symbols}
    total = len(cal)
    success = 0
    t0 = time.time()

    print(f'📡 逐日处理 {total} 天（倒序，最新优先）...', flush=True)

    MAX_EMPTY = 30
    empty_days = {s: 0 for s in symbols}
    opt_names = {sym: name for sym, name in OPT_NAMES.items() if sym in symbols}

    for i, d in enumerate(cal):
        ds = d.replace('-', '')
        # ── 期货 ──
        try:
            fut = _call_with_timeout(lambda: ak.get_czce_daily(date=ds))
            if fut is None:
                continue
            if fut.empty:
                continue
            fut['date'] = d
            for c in ['open', 'high', 'low', 'close']:
                fut[c] = pd.to_numeric(fut[c], errors='coerce')
            fut['volume'] = pd.to_numeric(fut['volume'], errors='coerce').fillna(0)
        except Exception:
            continue

        # ── 期权（三个品种并行拉取，超时跳过） ──
        opts = {}
        opt_items = list(opt_names.items())
        with ThreadPoolExecutor(max_workers=3) as ex:
            fs = {ex.submit(_fetch_one_option, sym, oname, ds, d): sym for sym, oname in opt_items}
            try:
                for f in as_completed(fs, timeout=_TIMEOUT):
                    sym, df = f.result()
                    opts[sym] = df
            except TimeoutError:
                # 超时的品种留空
                for f, sym in fs.items():
                    if sym not in opts:
                        opts[sym] = pd.DataFrame()
                print(f'  ⚠ {d} 期权部分超时', flush=True)

        success += 1

        # ── 逐品种处理 ──
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

            result[sym].append({
                'd': d, 'o': round(float(fr['open']), 2), 'c': round(px, 2),
                'h': round(float(fr['high']), 2), 'l': round(float(fr['low']), 2),
                'mp': mp, 'co': co, 'po': po,
                'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs,
            })

        # ── 空日计数 + 提前终止 ──
        got_data = {s for s in symbols if result[s] and result[s][-1]['d'] == d}
        for sym in symbols:
            empty_days[sym] = 0 if sym in got_data else empty_days[sym] + 1
        if all(empty_days[s] >= MAX_EMPTY for s in symbols):
            print(f'  所有品种连续 {MAX_EMPTY} 天无数据，提前终止', flush=True)
            cal = cal[:i+1]
            break

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f'  进度: {i+1}/{total} ({success}成功, {elapsed:.0f}s)', flush=True)
            # 每处理 _UPLOAD_INTERVAL 天中途上传一次
            if worker_url and api_key and (i + 1) % _UPLOAD_INTERVAL == 0:
                print(f'  中途上传数据...', flush=True)
                for sym in symbols:
                    if result[sym]:
                        _upload_batch(sym, result[sym], worker_url, api_key, gh_token)

    # 恢复为按日期正序
    for s in (symbols or SYMBOLS):
        result[s].sort(key=lambda r: r['d'])

    elapsed = time.time() - t0
    print(f'✅ 完成: {success} 天 ({elapsed:.0f}s)')
    for s in (symbols or SYMBOLS):
        print(f'  {s}: {len(result[s])} 条')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', '-o', default='data.json')
    parser.add_argument('--max-dates', type=int, default=0, help='测试用限制处理日期数')
    parser.add_argument('--recent', type=int, default=0, help='仅处理最近 N 个交易日')
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
        data = run(max_dates=args.max_dates, recent=args.recent, symbols=syms,
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
            data = run(max_dates=args.max_dates, recent=args.recent, symbols=[sym],
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
