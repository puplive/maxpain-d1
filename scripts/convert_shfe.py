"""SHFE 上期所 xls/xlsx → JSON，存入 data/shfe/
一批读完所有 xlsx, 再按品种分组处理
用法: python scripts/convert_shfe.py --year 2026
"""
import argparse, json, os, re, sys, time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'shfe'
FILE_DIR = ROOT / 'file' / 'shfe'

SHFE_PREFIX_MAP = {s: s for s in [
    'AG','AL','AO','AU','BR','BU','CU','FU','HC','LU','NI','NR','PB',
    'RB','RU','SC','SN','SP','SS','WR','ZN','BC',
]}

SHFE_MULT = {
    'CU': 5, 'AL': 5, 'ZN': 5, 'PB': 5, 'RB': 10,
    'NI': 1, 'SN': 1, 'AU': 1000, 'AG': 15, 'RU': 10,
    'BR': 5, 'AO': 20, 'HC': 10, 'BU': 10, 'FU': 10,
    'SP': 10, 'SS': 5, 'WR': 10, 'LU': 10, 'BC': 5,
    'SC': 1000, 'NR': 10,
}


def parse_opt_code(code):
    m = re.search(r'([CP])(\d+\.?\d*)$', str(code))
    if m:
        return float(m.group(2)), m.group(1)
    m2 = re.search(r'(\d+\.?\d*)\s*(看涨|看跌|C|P)', str(code))
    if m2:
        t = 'C' if m2.group(2) in ('看涨','C') else 'P'
        return float(m2.group(1)), t
    return None


def calc_max_pain(opt_df):
    if opt_df.empty: return 0
    strikes = sorted(opt_df['strike'].unique())
    best_s, best_val = 0, float('inf')
    for s in strikes:
        calls = opt_df[(opt_df['strike'] < s) & (opt_df['type'] == 'C')]
        puts = opt_df[(opt_df['strike'] > s) & (opt_df['type'] == 'P')]
        val = 0.0
        if not calls.empty: val += ((s - calls['strike']) * calls['oi']).sum()
        if not puts.empty: val += ((puts['strike'] - s) * puts['oi']).sum()
        if val < best_val: best_val, best_s = val, s
    return int(best_s)


def calc_gex(opt_df, px, mult):
    """计算总 Gamma Exposure (GEX)"""
    total = 0.0
    T = 30 / 365
    sqrt_T = np.sqrt(T)
    for _, row in opt_df.iterrows():
        iv = row.get('iv', 0)
        oi = row.get('oi', 0)
        K = row.get('strike', 0)
        if pd.isna(iv) or iv <= 1e-6 or oi <= 0 or K <= 0:
            continue
        d1 = (np.log(px / K) + 0.5 * iv**2 * T) / (iv * sqrt_T)
        pdf = np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi)
        gamma = pdf / (px * iv * sqrt_T)
        total += gamma * oi * mult * px
    return round(total, 2)


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


def _read_shfe_monthly(path):
    df = pd.read_excel(path)
    for i in range(min(5, len(df))):
        if str(df.iloc[i, 0]).strip() == '合约':
            df.columns = [str(v).strip() for v in df.iloc[i].values]
            # 跳过中文 header + 英文 header + 可能的空行
            skip = i + 1
            while skip < len(df) and str(df.iloc[skip, 0]).strip() in ('', 'Contract', '合约'):
                skip += 1
            df = df.iloc[skip:].reset_index(drop=True)
            break
    if '合约' not in df.columns:
        return pd.DataFrame()
    df['合约'] = df['合约'].astype(str).str.strip().str.upper()
    # 统一日期列名: 老文件用 '日期'，新文件用 '交易日期'
    if '日期' in df.columns and '交易日期' not in df.columns:
        df['交易日期'] = df['日期']
    # 过滤非合约行: 合约代码必须是纯ASCII且以字母开头数字结尾 (或含C/P期权标识)
    import re
    df = df[df['合约'].str.match(r'^[A-Z]{1,2}\d+[CP]?\d*$', na=False)].reset_index(drop=True)
    return df


def _clean_num(series):
    return pd.to_numeric(series.astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)


def load_all_futures(year):
    """读所有期货 xlsx，返回 {sym: pd.DataFrame}"""
    fut_dir = FILE_DIR / f'fu{year}'
    all_df = []
    for f in sorted(fut_dir.rglob('*.xls*')):
        df = _read_shfe_monthly(f)
        if not df.empty and ('交易日期' in df.columns or '日期' in df.columns):
            all_df.append(df)
    if not all_df: return {}
    df = pd.concat(all_df, ignore_index=True)
    df['date'] = pd.to_datetime((df['交易日期'] if '交易日期' in df.columns else df['日期']).astype(str)).dt.strftime('%Y-%m-%d')
    for c in ['开盘价','最高价','最低价','收盘价','成交量']:
        if c in df.columns: df[c] = _clean_num(df[c])

    symbols = {}
    for sym, grp in df.groupby(df['合约'].str.extract(r'^([A-Z]+)', expand=False)):
        if sym in SHFE_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def load_all_options(year):
    """读所有期权 xlsx，返回 {sym: pd.DataFrame}"""
    opt_dir = FILE_DIR / f'opt{year}'
    all_df = []
    for f in sorted(opt_dir.rglob('*.xls*')):
        df = _read_shfe_monthly(f)
        if not df.empty and ('交易日期' in df.columns or '日期' in df.columns):
            all_df.append(df)
    if not all_df: return {}
    df = pd.concat(all_df, ignore_index=True)
    df['date'] = pd.to_datetime((df['交易日期'] if '交易日期' in df.columns else df['日期']).astype(str)).dt.strftime('%Y-%m-%d')

    # 解析期权
    parsed = df['合约'].apply(parse_opt_code)
    df['strike'] = parsed.apply(lambda x: x[0] if x else None)
    df['type'] = parsed.apply(lambda x: x[1] if x else None)
    df = df.dropna(subset=['strike'])
    df['strike'] = df['strike'].astype(float)

    for c, src in [('oi','持仓量'),('close','收盘价'),('volume','成交量')]:
        if src in df.columns: df[c] = _clean_num(df[src])

    df['delta'] = pd.to_numeric(df.get('Delta', pd.Series([0]*len(df))), errors='coerce')
    iv_col = '隐含波动率' if '隐含波动率' in df.columns else None
    df['iv'] = pd.to_numeric(df[iv_col], errors='coerce').fillna(0) / 100 if iv_col else 0

    # 按品种分组
    symbols = {}
    for sym, grp in df.groupby(df['合约'].str.extract(r'^([A-Z]+)', expand=False)):
        if sym in SHFE_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def process_one(sym, fut, opt, mult=10):
    """从已过滤的 fut/opt df 计算指标"""
    if fut is None or opt is None: return {}

    fut_dates = {}
    for date, g in fut.groupby('date'):
        if '成交量' in g.columns:
            idx = g['成交量'].idxmax()
        else:
            idx = g.index[0]
        r = g.loc[idx]
        if r.get('收盘价', 0) > 0:
            fut_dates[date] = {'o': float(r.get('开盘价', 0)), 'c': float(r.get('收盘价', 0)),
                               'h': float(r.get('最高价', 0)), 'l': float(r.get('最低价', 0))}

    opt_by_date = {str(d): g for d, g in opt.groupby('date')}

    result = {}
    for date, row in sorted(fut_dates.items()):
        if date not in opt_by_date: continue
        o = opt_by_date[date]
        px = row['c']
        rng = 0.20
        do_mp = o[o['strike'].between(px*(1-rng), px*(1+rng))]
        mp = calc_max_pain(do_mp if len(do_mp) > 0 else o)
        co = float(o[(o['strike'] > px) & (o['type'] == 'C')]['oi'].sum())
        po = float(o[(o['strike'] < px) & (o['type'] == 'P')]['oi'].sum())
        do_be = o[o['strike'].between(px*(1-rng), px*(1+rng))]
        bec = calc_be(do_be, px, True) if len(do_be) > 0 else None
        bep = calc_be(do_be, px, False) if len(do_be) > 0 else None
        cv = float(o[o['type'] == 'C']['volume'].sum())
        pv = float(o[o['type'] == 'P']['volume'].sum())
        vr = round(cv/pv, 2) if pv > 0 else None
        civ = o[(o['type'] == 'C') & (o['delta'].between(0.20, 0.30))]['iv'].mean()
        piv = o[(o['type'] == 'P') & (o['delta'].between(-0.30, -0.20))]['iv'].mean()
        ivs = round(piv - civ, 4) if (pd.notna(civ) and pd.notna(piv)) else None
        gex = calc_gex(o, px, mult)
        result[date] = {'d': date, 'o': row['o'], 'c': px, 'h': row['h'], 'l': row['l'],
                        'mp': mp, 'co': co, 'po': po, 'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs, 'gex': gex}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, default=0)
    parser.add_argument('--symbol', default='')
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    years = sorted(set(int(d.name.replace('fu','')) for d in FILE_DIR.glob('fu*') if d.is_dir() and d.name.replace('fu','').isdigit()))
    if args.year: years = [y for y in years if y == args.year]

    for year in years:
        t0 = time.time()
        print(f'\n=== {year} ===')
        fut_by_sym = load_all_futures(year)
        opt_by_sym = load_all_options(year)
        print(f'  期货: {len(fut_by_sym)} 品种, 期权: {len(opt_by_sym)} 品种 ({time.time()-t0:.0f}s)')

        all_syms = set(fut_by_sym) & set(opt_by_sym)
        if args.symbol:
            all_syms = {s for s in all_syms if s == args.symbol.upper()}

        output = {}
        for sym in sorted(all_syms):
            entries = process_one(sym, fut_by_sym[sym], opt_by_sym[sym], mult=SHFE_MULT.get(sym, 10))
            if entries:
                records = sorted(entries.values(), key=lambda r: r['d'])
                output[sym] = records
                print(f'  {sym}: {len(records)} 天')

        if output:
            out = DATA_DIR / f'{year}.json'
            out.write_text(json.dumps(output, ensure_ascii=False))
            print(f'✅ {year}.json ({sum(len(v) for v in output.values())} 条, {len(output)} 品种, {time.time()-t0:.0f}s)')

    print(f'\n📦 data/shfe/')


if __name__ == '__main__':
    main()
