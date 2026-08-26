"""CFFEX 中金所 CSV → JSON，存入 data/cffex/
读 file/cffex/{YYYYMM}/{YYYYMMDD}_1.csv (GBK, 期货+期权混在一个文件, 按品种分块)
用法: python scripts/convert_cffex.py --date 2026
"""
import argparse, datetime, json, os, re, sys, time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'cffex'
FILE_DIR = ROOT / 'file' / 'cffex'

# 股指期权产品 → 标的股指期货产品 (期权 IO 的标的是 IF)
OPT_UNDERLYING = {'IO': 'IF', 'MO': 'IM', 'HO': 'IH'}
# 股指/国债期货乘数 (元/点)
FUT_MULT = {'IF': 300, 'IH': 300, 'IC': 200, 'IM': 200,
            'T': 10000, 'TF': 10000, 'TS': 20000, 'TL': 10000}
# 股指期权乘数 (元/点)
OPT_MULT = {'IO': 100, 'MO': 100, 'HO': 100}

# ==== Black 76 期货期权定价（中金所期权无 IV 列, 用成交价反推） ====
_RATE = 0.025

def _norm_pdf(x):
    return np.exp(-0.5 * x * x) / np.sqrt(2 * np.pi)

def _norm_cdf(x):
    """Abramowitz & Stegun 近似，精度 ~1.5e-7"""
    x_abs = np.abs(x)
    t = 1 / (1 + 0.2316419 * x_abs)
    a1, a2, a3, a4, a5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    phi = _norm_pdf(x)
    cdf = phi * (a1*t + a2*t**2 + a3*t**3 + a4*t**4 + a5*t**5)
    return np.where(x > 0, 1 - cdf, cdf)

def _black76_price(S, K, T, r, sigma, is_call):
    """Black 76 期货期权定价（向量化）"""
    sqrt_T = np.sqrt(T)
    d1 = (np.log(np.clip(S/K, 0.01, 100)) + 0.5 * sigma**2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc = np.exp(-r * T)
    call = S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    put = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return np.where(is_call, call, put)

def _black76_delta(S, K, T, r, sigma, is_call):
    """Black 76 delta（向量化），sigma=0 返回 0"""
    delta = np.zeros(len(S))
    ok = (sigma > 1e-6) & (T > 0) & (S > 0) & (K > 0)
    if not ok.any():
        return delta
    sqrt_T = np.sqrt(T[ok])
    d1 = (np.log(np.clip(S[ok]/K[ok], 0.01, 100)) + 0.5 * sigma[ok]**2 * T[ok]) / (sigma[ok] * sqrt_T)
    d1 = np.clip(d1, -10, 10)
    delta[ok] = np.where(is_call[ok], _norm_cdf(d1), -_norm_cdf(-d1))
    return delta

def _calc_iv_batch(mkt, S, K, T, r, is_call):
    """向量化二分法反推 IV"""
    n = len(mkt)
    iv = np.zeros(n)
    valid = (mkt > 1e-8) & (S > 0) & (K > 0) & (T > 7/365)
    if not valid.any():
        return iv
    lo = np.full(n, 0.001)
    hi = np.full(n, 3.0)
    for _ in range(80):
        mid = (lo + hi) / 2
        p = _black76_price(S, K, T, r, mid, is_call)
        lo = np.where(p < mkt, mid, lo)
        hi = np.where(p >= mkt, mid, hi)
        if np.max(hi[valid] - lo[valid]) < 0.0001:
            break
    iv[valid] = ((lo[valid] + hi[valid]) / 2)
    return iv


# 交易日历 (全部交易日): 文件名即日期
_TRADING_DAYS = set()


def build_calendar():
    global _TRADING_DAYS
    for f in FILE_DIR.glob('*/*.csv'):
        _TRADING_DAYS.add(f.stem[:8])
    return sorted(_TRADING_DAYS)


def _cffex_expiry(ym):
    """中金所股指期货/期权最后交易日: 交割月份的第三个星期五,
    遇法定节假日顺延至下一交易日. ym = '2606'
    """
    year = 2000 + int(ym[:2])
    month = int(ym[2:4])
    first = datetime.date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    tf = first + datetime.timedelta(days=offset + 14)   # 第三个星期五
    # 日历中 >= tf 的第一个交易日 (处理节假日顺延)
    key = f'{year:04d}{month:02d}'
    for d in sorted(_TRADING_DAYS):
        if d.startswith(key) and d >= tf.strftime('%Y%m%d'):
            return f'{d[:4]}-{d[4:6]}-{d[6:8]}'
    return tf.strftime('%Y-%m-%d')


def _cffex_T(ym, trade_date_str, calendar=None):
    """到期年限 T。ym = '2606'"""
    year = 2000 + int(ym[:2])
    month = int(ym[2:4])
    first = datetime.date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    tf = first + datetime.timedelta(days=offset + 14)
    key = f'{year:04d}{month:02d}'
    expiry_str = None
    for d in sorted(_TRADING_DAYS):
        if d.startswith(key) and d >= tf.strftime('%Y%m%d'):
            expiry_str = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
            break
    if expiry_str is None:
        expiry_str = tf.strftime('%Y-%m-%d')
    ex = datetime.date.fromisoformat(expiry_str)
    td = datetime.date.fromisoformat(trade_date_str)
    return max((ex - td).days / 365, 7 / 365)


def calc_max_pain(opt_df):
    if opt_df.empty:
        return 0
    strikes_arr = np.sort(opt_df['strike'].unique())
    if len(strikes_arr) == 0:
        return 0
    type_arr = opt_df['type'].values
    strike_arr = opt_df['strike'].values
    oi_arr = opt_df['oi'].values

    best_s = int(strikes_arr[0])
    best_val = float('inf')
    for s in strikes_arr:
        call_mask = (strike_arr < s) & (type_arr == 'C')
        put_mask = (strike_arr > s) & (type_arr == 'P')
        val = float(((s - strike_arr[call_mask]) * oi_arr[call_mask]).sum() +
                    ((strike_arr[put_mask] - s) * oi_arr[put_mask]).sum())
        if val < best_val:
            best_val, best_s = val, int(s)
    return best_s


_T_CACHE: dict[tuple[str, str], tuple[float, float]] = {}


def calc_gex(opt_df, main_px, mult, fut_prices, trade_date):
    """计算净 Gamma Exposure (GEX)，向量化 + 到期日缓存
    fut_prices: {IF2608: close} 已按标的期货码
    """
    iv = pd.to_numeric(opt_df.get('iv', pd.Series(0)), errors='coerce')
    oi = pd.to_numeric(opt_df.get('oi', pd.Series(0)), errors='coerce').fillna(0)
    strike = pd.to_numeric(opt_df.get('strike', pd.Series(0)), errors='coerce')
    cp = opt_df.get('type', pd.Series(''))

    valid = iv.notna() & (iv > 1e-6) & (oi > 0) & strike.notna() & (strike > 0)
    if not valid.any():
        return 0.0

    iv, oi, strike = iv[valid], oi[valid], strike[valid]
    cp = cp[valid]
    codes = opt_df.loc[valid, '合约代码'].astype(str)

    n = len(iv)
    T_arr = np.full(n, 30 / 365)
    px_arr = np.full(n, main_px)

    parsed = codes.str.extract(r'^([A-Z]{2})(\d{4})-[CP]-', expand=False)
    fcodes = parsed[0].map(OPT_UNDERLYING).fillna('') + parsed[1].fillna('')

    for ccode in fcodes.dropna().unique():
        ckey = (ccode, trade_date)
        if ckey in _T_CACHE:
            t_val, p_val = _T_CACHE[ckey]
        else:
            p_val = fut_prices.get(ccode, main_px)
            if p_val is None or p_val <= 0:
                p_val = main_px
                t_val = 30 / 365
            else:
                ym = ccode[-4:]
                t_val = _cffex_T(ym, trade_date)
            _T_CACHE[ckey] = (t_val, p_val)

        mask = (fcodes == ccode)
        T_arr[mask] = t_val
        px_arr[mask] = p_val

    sqrt_T = np.sqrt(T_arr)
    d1 = (np.log(px_arr / strike) + 0.5 * iv**2 * T_arr) / (iv * sqrt_T)
    pdf = np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi)
    gamma = pdf / (px_arr * iv * sqrt_T)
    gex = gamma * oi * mult * px_arr * px_arr * 0.01
    gex = gex * cp.map({'C': 1, 'P': -1})

    return round(float(gex.sum()), 2)


def calc_be(opt_df, px, is_call):
    filtered = opt_df[opt_df['type'] == ('C' if is_call else 'P')]
    if filtered.empty: return None
    total_cost = (filtered['close'] * filtered['oi']).sum()
    total_oi = filtered['oi'].sum()
    if total_oi == 0 or total_cost == 0: return None
    low, high = px * 0.7, px * 1.3
    for _ in range(100):
        mid = (low + high) / 2
        if is_call:
            v = (np.maximum(mid - filtered['strike'].values, 0) * filtered['oi'].values).sum()
            if v < total_cost: low = mid
            else: high = mid
        else:
            v = (np.maximum(filtered['strike'].values - mid, 0) * filtered['oi'].values).sum()
            if v < total_cost: high = mid
            else: low = mid
        if high - low < 0.01: break
    return round((low + high) / 2, 2)


def _clean_num(series):
    return pd.to_numeric(series.astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)


def load_year_data(year):
    """读当年所有每日 CSV → (futures_df, options_df)"""
    fut_parts, opt_parts = [], []
    for month_dir in sorted(FILE_DIR.glob(f'{year}*')):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.glob('*.csv')):
            date_str = f.stem[:8]
            df = pd.read_csv(f, encoding='gb18030', on_bad_lines='skip')
            if '合约代码' not in df.columns:
                continue
            df['date'] = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
            codes = df['合约代码'].astype(str).str.strip().str.upper()
            df['合约代码'] = codes
            fut_mask = codes.str.match(r'^[A-Z]{1,2}\d{4}$', na=False)
            opt_mask = codes.str.match(r'^[A-Z]{2}\d{4}-[CP]-\d+$', na=False)
            if fut_mask.any():
                fut_parts.append(df.loc[fut_mask])
            if opt_mask.any():
                opt_parts.append(df.loc[opt_mask])
    if not fut_parts:
        return pd.DataFrame(), pd.DataFrame()

    fut = pd.concat(fut_parts, ignore_index=True)
    for c in ['今开盘','最高价','最低价','今收盘','今结算','成交量','持仓量','成交金额']:
        if c in fut.columns:
            fut[c] = _clean_num(fut[c])

    if opt_parts:
        opt = pd.concat(opt_parts, ignore_index=True)
        for c in ['今开盘','最高价','最低价','今收盘','今结算','成交量','持仓量','成交金额']:
            if c in opt.columns:
                opt[c] = _clean_num(opt[c])
        parsed = opt['合约代码'].str.extract(r'^([A-Z]{2})(\d{4})-([CP])-(\d+)$')
        opt['product'] = parsed[0]
        opt['contract_ym'] = parsed[1]
        opt['type'] = parsed[2]
        opt['strike'] = pd.to_numeric(parsed[3], errors='coerce')
        opt = opt.dropna(subset=['strike'])
        opt['strike'] = opt['strike'].astype(float)
        opt['oi'] = opt['持仓量']
        opt['close'] = opt['今收盘']
        opt['volume'] = opt['成交量']
        opt['delta'] = pd.to_numeric(opt.get('Delta'), errors='coerce')
        opt['iv'] = 0.0   # 无 IV 列, 后续反推
    else:
        opt = pd.DataFrame()

    return fut, opt


def process_one(sym, fut, opt, mult=100):
    """从已过滤的 fut/opt df 计算指标。sym=IF/IH/IM, opt 是其对应期权产品"""
    if fut is None or fut.empty or opt is None or opt.empty:
        return {}

    fut = fut.copy()
    fut['contract_ym'] = fut['合约代码'].str.extract(r'(\d{4})$')[0]

    fut_dates = {}
    fut_nc = {}
    for date, g in fut.groupby('date'):
        if len(g) == 0: continue
        idx = g['成交量'].idxmax()
        r = g.loc[idx]
        if r.get('今收盘', 0) > 0:
            fut_dates[date] = {'o': float(r.get('今开盘', 0)), 'c': float(r.get('今收盘', 0)),
                               'h': float(r.get('最高价', 0)), 'l': float(r.get('最低价', 0)),
                               'fv': float(r.get('成交量', 0)),
                               'foi': float(r.get('持仓量', 0)),
                               'fto': float(r.get('成交金额', 0)),   # 单位已是万元
                               'fc': str(r.get('合约代码', ''))}
        active = g[(g['成交量'] > 0) & (g['今收盘'] > 0)]
        if not active.empty:
            front = active.sort_values('contract_ym').iloc[0]
            fut_nc[date] = float(front.get('今收盘', 0))

    opt_by_date = {str(d): g for d, g in opt.groupby('date')}
    result = {}
    for date, row in sorted(fut_dates.items()):
        if date not in opt_by_date: continue
        o = opt_by_date[date].copy()
        px = row['c']

        # ---- 反推 IV/Delta（中金所期权无 IV 列） ----
        day_fut = fut[fut['date'] == date]
        fut_price_map = {str(k).upper(): float(v) for k, v in zip(day_fut['合约代码'], day_fut['今收盘'])}
        codes = o['合约代码']
        parsed_parts = codes.str.extract(r'^([A-Z]{2})(\d{4})-[CP]-', expand=False)
        fcodes = parsed_parts[0].map(OPT_UNDERLYING).fillna('') + parsed_parts[1].fillna('')
        S_arr = np.where(fcodes.isin(fut_price_map),
                         fcodes.map(fut_price_map).fillna(px).values, px)
        K_arr = o['strike'].values
        mkt_arr = o['close'].values
        is_call = (o['type'] == 'C').values
        unique_yms = o['contract_ym'].dropna().unique()
        T_map = {ym: _cffex_T(ym, date) for ym in unique_yms}
        T_arr = o['contract_ym'].map(T_map).fillna(30/365).values

        iv_arr = _calc_iv_batch(mkt_arr, S_arr, K_arr, T_arr, _RATE, is_call)
        delta_arr = _black76_delta(S_arr, K_arr, T_arr, _RATE, iv_arr, is_call)
        # 优先用交易所给的 Delta, 无效时用反推值
        exch_delta = o['delta'].values
        ok_exch = pd.notna(exch_delta) & (np.abs(exch_delta) > 1e-6) & (np.abs(exch_delta) <= 1)
        o['delta'] = np.where(ok_exch, exch_delta, delta_arr)
        o['iv'] = iv_arr
        # ---- 反推结束 ----

        calls = o[o['type'] == 'C']
        puts = o[o['type'] == 'P']
        rng = 0.20
        near = o[o['strike'].between(px*(1-rng), px*(1+rng))]

        mp = calc_max_pain(near if len(near) > 0 else o)
        co = float(calls[calls['strike'] > px]['oi'].sum())
        po = float(puts[puts['strike'] < px]['oi'].sum())
        bec = calc_be(near, px, True) if len(near) > 0 else None
        bep = calc_be(near, px, False) if len(near) > 0 else None
        cv = float(calls['volume'].sum())
        pv = float(puts['volume'].sum())
        vr = round(cv/pv, 2) if pv > 0 else None
        civ = calls[calls['delta'].between(0.20, 0.30)]['iv'].mean()
        piv = puts[puts['delta'].between(-0.30, -0.20)]['iv'].mean()
        ivs = round(piv - civ, 4) if (pd.notna(civ) and pd.notna(piv)) else None
        gex = calc_gex(o, px, mult, fut_price_map, date)

        vol_call = cv
        vol_put = pv
        vol_total = round(cv + pv, 2)
        oi_total = float(o['oi'].sum())
        call_oi_all = float(calls['oi'].sum())
        put_oi_all = float(puts['oi'].sum())
        oi_pcr = round(put_oi_all / call_oi_all, 2) if call_oi_all > 0 else None
        strike_oi = o.groupby('strike')['oi'].sum()
        oi_max_strike = int(strike_oi.idxmax()) if not strike_oi.empty else None
        if not o.empty:
            atm_idx = (o['strike'] - px).abs().idxmin()
            atm_iv = float(o.loc[atm_idx, 'iv']) if pd.notna(o.loc[atm_idx, 'iv']) else None
        else:
            atm_iv = None

        nearest_expiry = None
        nearest_code = None
        nearest_dte = 0
        seen = set()
        for ym in o['contract_ym'].dropna().unique():
            if ym in seen:
                continue
            seen.add(ym)
            expiry_str = _cffex_expiry(ym)
            if nearest_expiry is None or expiry_str < nearest_expiry:
                nearest_expiry = expiry_str
                nearest_code = OPT_UNDERLYING.get(str(o.loc[o['contract_ym']==ym, 'product'].iloc[0]), sym) + ym
        if nearest_expiry:
            td = datetime.date.fromisoformat(date)
            ex = datetime.date.fromisoformat(nearest_expiry)
            nearest_dte = (ex - td).days

        pt_oi = o.pivot_table(index='strike', columns='type', values='oi', aggfunc='sum', fill_value=0)
        pt_iv = o.pivot_table(index='strike', columns='type', values='iv', aggfunc='mean')
        pt_vol = o.pivot_table(index='strike', columns='type', values='volume', aggfunc='sum')
        pt_close = o.pivot_table(index='strike', columns='type', values='close', aggfunc='mean')
        chain_list = []
        for s in pt_oi.index:
            n = int(s)
            ro = pt_oi.loc[s]
            co_v = int(ro.get('C', 0))
            po_v = int(ro.get('P', 0))
            if co_v <= 0 and po_v <= 0:
                continue
            item = {'s': n, 'co': co_v, 'po': po_v}
            if s in pt_iv.index:
                riv = pt_iv.loc[s]
                if pd.notna(riv.get('C')) and riv['C'] > 0: item['civ'] = round(float(riv['C']), 4)
                if pd.notna(riv.get('P')) and riv['P'] > 0: item['piv'] = round(float(riv['P']), 4)
            if s in pt_vol.index:
                rv = pt_vol.loc[s]
                if rv.get('C', 0) > 0: item['cvol'] = int(rv['C'])
                if rv.get('P', 0) > 0: item['pvol'] = int(rv['P'])
            if s in pt_close.index:
                rc = pt_close.loc[s]
                if rc.get('C', 0) > 0: item['cclose'] = round(float(rc['C']), 2)
                if rc.get('P', 0) > 0: item['pclose'] = round(float(rc['P']), 2)
            chain_list.append(item)
        oc_chain = json.dumps(chain_list, separators=(',', ':'))

        oc_val = None
        opt_underlying = fcodes
        if not opt_underlying.isna().all():
            top_u = o.groupby(opt_underlying)['oi'].sum().idxmax()
            fut_match = fut[(fut['date'] == date) & (fut['合约代码'] == top_u)]
            if not fut_match.empty:
                oc_val = round(float(fut_match['今收盘'].iloc[0]), 2)

        result[date] = {'d': date, 'o': row['o'], 'c': px, 'nc': round(fut_nc.get(date, px), 2),
                        'h': row['h'], 'l': row['l'],
                        'mp': mp, 'co': co, 'po': po, 'bec': bec, 'bep': bep,
                        'vr': vr, 'ivs': ivs, 'gex': gex, 'oc': oc_val, 'oc_chain': oc_chain,
                        'expiry': nearest_expiry, 'dte': nearest_dte, 'opt_contract': nearest_code,
                        'oi_total': oi_total, 'oi_pcr': oi_pcr, 'oi_max_strike': oi_max_strike,
                        'vol_call': vol_call, 'vol_put': vol_put, 'vol_total': vol_total,
                        'fut_vol': row['fv'], 'fut_oi': row['foi'], 'fut_turnover': row['fto'],
                        'fut_contract': row['fc'],
                        'atm_iv': atm_iv}
    return result


def upload_batch(symbol, records, worker_url, api_key, gh_token=''):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'X-GitHub-Token': gh_token,
        'User-Agent': 'MaxPain/1.0',
    }
    payload = json.dumps({'symbol': symbol, 'data': records}).encode('utf-8')
    req = Request(f'{worker_url}/api/update', data=payload, headers=headers, method='POST')
    try:
        with urlopen(req, timeout=120) as resp:
            r = json.loads(resp.read())
            if r.get('ok'):
                return r['count']
            print(f'    ⚠ {symbol}: {r}')
    except HTTPError as e:
        print(f'    ❌ {symbol}: HTTP {e.code}')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='', help='2026(全年) / 202606(月) / 20260629(日)')
    parser.add_argument('--symbol', default='')
    parser.add_argument('--upload', action='store_true', help='转换后直接上传到 D1')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', 'https://api.starrysay.com'))
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''))
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''), help='GitHub Token')
    args = parser.parse_args()

    if args.upload and not args.api_key:
        print('❌ --upload 需要 D1_API_KEY 环境变量')
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print('构建交易日历...')
    build_calendar()

    date_filter = args.date if len(args.date) > 4 else None
    target_year = int(args.date[:4]) if args.date else 0
    years = sorted({int(f.parent.name[:4]) for f in FILE_DIR.glob('*/*.csv')})
    years = [y for y in years if not target_year or y == target_year]

    for year in years:
        t0 = time.time()
        print(f'\n=== cffex {year} ===')
        fut, opt = load_year_data(year)
        if fut.empty:
            print(f'  {year}: 无数据')
            continue
        products = sorted({c[:2] for c in fut['合约代码'].astype(str)})
        opt_products = sorted({c[:2] for c in opt['合约代码'].astype(str)}) if not opt.empty else []
        print(f'  期货: {products}, 期权: {opt_products} ({time.time()-t0:.0f}s)')

        # 只处理有期权的股指品种: IF+IO, IH+HO, IM+MO
        all_syms = []
        for opt_prod, fut_prod in OPT_UNDERLYING.items():
            if opt_prod in opt_products and fut_prod in products:
                all_syms.append(fut_prod)
        if args.symbol:
            all_syms = [s for s in all_syms if s == args.symbol.upper()]

        output = {}
        for sym in sorted(all_syms):
            opt_prod = {v: k for k, v in OPT_UNDERLYING.items()}[sym]
            fut_df = fut[fut['合约代码'].astype(str).str.startswith(sym)]
            opt_df = opt[opt['合约代码'].astype(str).str.startswith(opt_prod)]
            entries = process_one(sym, fut_df, opt_df, mult=OPT_MULT.get(opt_prod, 100))
            if entries:
                records = sorted(entries.values(), key=lambda r: r['d'])
                output[sym] = records
                print(f'  {sym}: {len(records)} 天')

        if date_filter:
            dl = len(date_filter)
            if dl >= 8:  # YYYYMMDD
                ds = f'{date_filter[:4]}-{date_filter[4:6]}-{date_filter[6:8]}'
                for sym in list(output.keys()):
                    output[sym] = [r for r in output[sym] if r['d'] == ds]
                    if not output[sym]: del output[sym]
            elif dl == 6:  # YYYYMM
                ym = f'{date_filter[:4]}-{date_filter[4:6]}'
                for sym in list(output.keys()):
                    output[sym] = [r for r in output[sym] if r['d'].startswith(ym)]
                    if not output[sym]: del output[sym]

        if output:
            out_path = DATA_DIR / f'{year}.json'
            if out_path.exists():
                existing = json.loads(out_path.read_text())
            else:
                existing = {}
            for sym in list(existing.keys()):
                if sym not in output:
                    del existing[sym]
            for sym, records in output.items():
                if sym not in existing:
                    existing[sym] = records
                else:
                    by_date = {r['d']: i for i, r in enumerate(existing[sym])}
                    for rec in records:
                        if rec['d'] in by_date:
                            existing[sym][by_date[rec['d']]] = rec
                        else:
                            existing[sym].append(rec)
                    existing[sym].sort(key=lambda r: r['d'])
            out_path.write_text(json.dumps(existing, ensure_ascii=False))
            total = sum(len(v) for v in existing.values())
            print(f'✅ {year}.json ({total} 条, {len(existing)} 品种, {time.time()-t0:.0f}s)')
            if args.upload:
                uploaded = 0
                for sym, records in output.items():
                    print(f'  ↗ {sym} ({len(records)} 条)...')
                    for i in range(0, len(records), 500):
                        n = upload_batch(sym, records[i:i+500], args.worker_url, args.api_key, args.gh_token)
                        uploaded += n
                print(f'  📤 已上传 {uploaded} 条到 D1')

    print(f'\n📦 data/cffex/')


if __name__ == '__main__':
    main()
