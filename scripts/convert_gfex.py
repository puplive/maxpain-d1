"""GFEX 广期所 CSV → JSON，存入 data/gfex/
读 file/gfex/ALLFUTURES{year}.csv + ALLOPTIONS{year}.csv
用法: python scripts/convert_gfex.py --date 2026
"""
import argparse, json, os, re, sys, time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'gfex'
FILE_DIR = ROOT / 'file' / 'gfex'

# 合约代码前缀是小写 (lc2601 / lc2602-C-100000), 这里映射到前端符号
GFEX_PREFIX_MAP = {
    'LC': '碳酸锂',
    'SI': '工业硅',
    'PS': '多晶硅',
    'PD': '钯',
    'PT': '铂',
}

# 乘数: 工业硅5吨/手, 碳酸锂1吨/手, 多晶硅3吨/手, 钯/铂1000克/手
GFEX_MULT = {'LC': 1, 'SI': 5, 'PS': 3, 'PD': 1000, 'PT': 1000}

# ==== Black 76 期货期权定价（GFEX 期权无 IV 列, 用成交价反推） ====
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


def _build_calendar(fut):
    """构建交易日历: {YYYY-MM: [date1, ...]}，完全取自期货数据的实际交易日"""
    cal = {}
    for d in sorted(fut['date'].unique()):
        cal.setdefault(d[:7], []).append(d)
    return cal


def _gfex_expiry(calendar, contract_code):
    """GFEX 期权到期日: 标的期货交割月份前一个月的第5个交易日
    contract_code 如 lc2602 / lc2602-C-100000
    """
    m = re.search(r'([A-Za-z]+)(\d{4})', str(contract_code))
    if not m:
        return 30
    ym = m.group(2)
    yr = 2000 + int(ym[:2])
    mo = int(ym[2:4])
    if mo == 1:
        mo = 12; yr -= 1
    else:
        mo -= 1
    exp_ym = f'{yr:04d}-{mo:02d}'
    trade_dates = calendar.get(exp_ym, [])
    if len(trade_dates) < 5:
        return 30
    return trade_dates[4]   # 第5个交易日


def _gfex_T(contract_code, trade_date_str, calendar):
    """到期年限 T。日历缺失(如跨年合约)回退到交割月前一月的15号近似"""
    m = re.search(r'([A-Za-z]+)(\d{4})', str(contract_code))
    if not m:
        return 30 / 365
    ym = m.group(2)
    yr = 2000 + int(ym[:2])
    mo = int(ym[2:4])
    td = date.fromisoformat(trade_date_str)
    if mo == 1:
        mo = 12; yr -= 1
    else:
        mo -= 1
    exp_ym = f'{yr:04d}-{mo:02d}'
    trade_dates = calendar.get(exp_ym, [])
    if len(trade_dates) >= 5:
        ex = date.fromisoformat(trade_dates[4])
        return max((ex - td).days / 365, 7 / 365)
    # 回退: 交割月前一个月15号
    expiry = date(yr, mo, 15)
    return max((expiry - td).days / 365, 7 / 365)


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


def calc_gex(opt_df, main_px, mult, fut_prices, trade_date, calendar):
    """计算净 Gamma Exposure (GEX)，向量化 + 到期日缓存"""
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

    contract_codes = codes.str.extract(r'^([A-Za-z]+\d{4})', expand=False)

    for ccode in contract_codes.dropna().unique():
        ckey = (ccode, trade_date)
        if ckey in _T_CACHE:
            t_val, p_val = _T_CACHE[ckey]
        else:
            p_val = fut_prices.get(ccode.lower(), main_px)
            if p_val is None or p_val <= 0:
                p_val = main_px
                t_val = 30 / 365
            else:
                t_val = _gfex_T(ccode, trade_date, calendar)
            _T_CACHE[ckey] = (t_val, p_val)

        mask = (contract_codes == ccode)
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


def load_all_futures(year):
    """读 ALLFUTURES{year}.csv → {sym: pd.DataFrame}"""
    path = FILE_DIR / f'ALLFUTURES{year}.csv'
    if not path.exists():
        return {}
    df = pd.read_csv(path, skiprows=1)
    df = df[df['合约代码'].astype(str).str.match(r'^[A-Za-z]+\d{4}$', na=False)].copy()
    df['date'] = pd.to_datetime(df['交易日期'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    for c in ['开盘价','最高价','最低价','收盘价','结算价','成交量','持仓量','成交额']:
        if c in df.columns:
            df[c] = _clean_num(df[c])
    symbols = {}
    sym_series = df['合约代码'].str.extract(r'^([A-Za-z]+)', expand=False).str.upper()
    for sym, grp in df.groupby(sym_series):
        if sym in GFEX_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def load_all_options(year):
    """读 ALLOPTIONS{year}.csv → {sym: pd.DataFrame}"""
    path = FILE_DIR / f'ALLOPTIONS{year}.csv'
    if not path.exists():
        return {}
    df = pd.read_csv(path, skiprows=1)
    df['date'] = pd.to_datetime(df['交易日期'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')

    # 解析期权: lc2602-C-100000
    parsed = df['合约代码'].astype(str).str.extract(r'^([A-Za-z]+)(\d{4})-[CP]-(\d+)$')
    df['sym'] = parsed[0].str.upper()
    df['strike'] = pd.to_numeric(parsed[2], errors='coerce')
    df['type'] = np.where(df['合约代码'].astype(str).str.contains(r'-C-', regex=True), 'C', 'P')
    df = df.dropna(subset=['strike'])
    df['strike'] = df['strike'].astype(float)

    for c, src in [('oi','持仓量'),('close','收盘价'),('volume','成交量')]:
        if src in df.columns:
            df[c] = _clean_num(df[src])
    df['delta'] = pd.to_numeric(df.get('DELTA'), errors='coerce')
    df['iv'] = 0.0   # GFEX 无 IV 列, 后续反推

    symbols = {}
    for sym, grp in df.groupby('sym'):
        if sym in GFEX_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def process_one(sym, fut, opt, mult=10):
    """从已过滤的 fut/opt df 计算指标"""
    if fut is None or opt is None: return {}

    fut = fut.copy()
    fut['contract_ym'] = fut['合约代码'].str.extract(r'(\d{4})$')[0]

    fut_dates = {}
    fut_nc = {}
    for date, g in fut.groupby('date'):
        if '成交量' in g.columns:
            idx = g['成交量'].idxmax()
        else:
            idx = g.index[0]
        r = g.loc[idx]
        if r.get('收盘价', 0) > 0:
            fut_dates[date] = {'o': float(r.get('开盘价', 0)), 'c': float(r.get('收盘价', 0)),
                               'h': float(r.get('最高价', 0)), 'l': float(r.get('最低价', 0)),
                               'fv': float(r.get('成交量', 0)),
                               'foi': float(r.get('持仓量', 0)),
                               'fto': float(r.get('成交额', 0)) / 10000,   # 元 → 万元, 与 SHFE 一致
                               'fc': str(r.get('合约代码', ''))}
        vol_col = '成交量'
        close_col = '收盘价'
        if vol_col:
            active = g[(pd.to_numeric(g[vol_col], errors='coerce') > 0) & (pd.to_numeric(g[close_col], errors='coerce') > 0)]
        else:
            active = g[pd.to_numeric(g[close_col], errors='coerce') > 0]
        if not active.empty:
            front = active.sort_values('contract_ym').iloc[0]
            fut_nc[date] = float(front.get(close_col, 0))

    opt_by_date = {str(d): g for d, g in opt.groupby('date')}

    calendar = _build_calendar(fut)
    result = {}
    for date, row in sorted(fut_dates.items()):
        if date not in opt_by_date: continue
        o = opt_by_date[date].copy()
        px = row['c']

        # ---- 反推 IV/Delta（GFEX 无 IV 列） ----
        day_fut = fut[fut['date'] == date]
        fut_price_map = {str(k).lower(): float(v) for k, v in zip(day_fut['合约代码'], day_fut['收盘价'])}
        codes = o['合约代码'].astype(str)
        parsed_parts = codes.str.extract(r'^([A-Za-z]+)(\d{4})-[CP]-', expand=False)
        fcodes = parsed_parts[0].fillna('').str.lower() + parsed_parts[1].fillna('')
        S_arr = np.where(fcodes.isin(fut_price_map),
                         fcodes.map(fut_price_map).fillna(px).values, px)
        K_arr = o['strike'].values
        mkt_arr = o['close'].values
        is_call = (o['type'] == 'C').values
        unique_codes = codes.str.extract(r'^([A-Za-z]+\d{4})', expand=False).dropna().unique()
        T_map = {c: _gfex_T(c, date, calendar) for c in unique_codes}
        T_map_key = codes.str.extract(r'^([A-Za-z]+\d{4})', expand=False)
        T_arr = T_map_key.map(T_map).fillna(30/365).values

        iv_arr = _calc_iv_batch(mkt_arr, S_arr, K_arr, T_arr, _RATE, is_call)
        delta_arr = _black76_delta(S_arr, K_arr, T_arr, _RATE, iv_arr, is_call)
        # 优先用交易所给的 DELTA, 无效时用反推值
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
        gex = calc_gex(o, px, mult, fut_price_map, date, calendar)

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
        seen_codes = set()
        for code in codes.str.extract(r'^([A-Za-z]+\d{4})', expand=False).dropna().unique():
            if code in seen_codes:
                continue
            seen_codes.add(code)
            expiry_str = _gfex_expiry(calendar, code)
            if isinstance(expiry_str, str):
                if nearest_expiry is None or expiry_str < nearest_expiry:
                    nearest_expiry = expiry_str
                    nearest_code = code
            else:
                m2 = re.search(r'([A-Za-z]+)(\d{4})', str(code))
                if m2:
                    ym2 = m2.group(2)
                    yr2 = 2000 + int(ym2[:2])
                    mo2 = int(ym2[2:4])
                    if mo2 == 1:
                        mo2 = 12; yr2 -= 1
                    else:
                        mo2 -= 1
                    approx = f'{yr2:04d}-{mo2:02d}-15'
                    if nearest_expiry is None or approx < nearest_expiry:
                        nearest_expiry = approx
                        nearest_code = code
        if nearest_expiry:
            td = pd.Timestamp(date).date()
            ex = pd.Timestamp(nearest_expiry).date()
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
        opt_underlying = codes.str.extract(r'^([A-Za-z]+\d{4})')[0]
        if not opt_underlying.isna().all():
            top_u = o.groupby(opt_underlying)['oi'].sum().idxmax()
            fut_match = fut[(fut['date'] == date) & (fut['合约代码'].astype(str).str.lower() == top_u.lower())]
            if not fut_match.empty:
                oc_val = round(float(fut_match['收盘价'].iloc[0]), 2)

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
    date_filter = args.date if len(args.date) > 4 else None
    target_year = int(args.date[:4]) if args.date else 0
    all_years = sorted({int(f.name.replace('ALLFUTURES', '').replace('.csv', ''))
                        for f in FILE_DIR.glob('ALLFUTURES*.csv')})
    years = [y for y in all_years if not target_year or y == target_year]

    for year in years:
        t0 = time.time()
        print(f'\n=== gfex {year} ===')
        fut_by_sym = load_all_futures(year)
        opt_by_sym = load_all_options(year)
        print(f'  期货: {len(fut_by_sym)} 品种, 期权: {len(opt_by_sym)} 品种 ({time.time()-t0:.0f}s)')

        all_syms = set(fut_by_sym) & set(opt_by_sym)
        if args.symbol:
            all_syms = {s for s in all_syms if s == args.symbol.upper()}

        output = {}
        for sym in sorted(all_syms):
            entries = process_one(sym, fut_by_sym[sym], opt_by_sym[sym], mult=GFEX_MULT.get(sym, 10))
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

    print(f'\n📦 data/gfex/')


if __name__ == '__main__':
    main()
