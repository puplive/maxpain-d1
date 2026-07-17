"""CZCE 郑商所 txt → JSON，存入 data/czce/
一批读完所有 txt，再按品种分组处理
用法:
  python scripts/convert_czce.py --date 2026          # 全年
  python scripts/convert_czce.py --date 202606        # 某月
  python scripts/convert_czce.py --date 20260629      # 某天
"""
import argparse, json, os, re, sys, time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'czce'
FILE_DIR = ROOT / 'file' / 'czce'

CZCE_PREFIX_MAP = {
    'TA': 'TA', 'MA': 'MA', 'SA': 'SA', 'SR': 'SR', 'CF': 'CF',
    'RM': 'RM', 'OI': 'OI', 'PK': 'PK', 'PF': 'PF', 'SM': 'SM',
    'SF': 'SF', 'UR': 'UR', 'AP': 'AP', 'CJ': 'CJ', 'FG': 'FG',
    'PX': 'PX', 'SH': 'SH', 'CY': 'CY',
}

CZCE_MULT = {
    'TA': 5, 'MA': 10, 'SA': 20, 'SR': 10, 'CF': 5,
    'RM': 10, 'OI': 10, 'PK': 5, 'PF': 5, 'SM': 5,
    'SF': 5, 'UR': 20, 'AP': 10, 'CJ': 5, 'FG': 20,
    'PX': 5, 'SH': 10,
}


def parse_opt_code(code: str):
    """CZCE 期权合约代码: TA602C4000 → (4000.0, 'C')"""
    m = re.search(r'([CP])(\d+)$', str(code))
    if m:
        return float(m.group(2)), m.group(1)
    return None


def _build_calendar(fut):
    """构建交易日历: {YYYY-MM: [date1, date2, ...]}
    优先从AKShare获取全年交易日(含未来月份)，再以期货实际数据补充
    """
    cal = {}
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        for d in pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d'):
            ym = d[:7]
            cal.setdefault(ym, []).append(d)
    except Exception:
        pass
    for d in sorted(fut['date'].unique()):
        ym = d[:7]
        if d not in cal.setdefault(ym, []):
            cal[ym].append(d)
    return cal


def _czce_expiry(calendar, contract_code):
    """CZCE 到期日：交割月前一个月第15个日历日之前(含)的倒数第3个交易日
    特殊品种(CJ,PX)：交割月前二个月最后一个日历日之前(含)的倒数第3个交易日
    """
    m = re.search(r'[A-Z]+(\d{3})$', str(contract_code))
    if not m:
        return 30
    sym = re.match(r'^[A-Z]+', str(contract_code))
    sym = sym.group(0) if sym else ''
    ym = m.group(1)
    yr = int(ym[0]) + 2020
    mo = int(ym[1:3])
    special = sym.upper() in ('CJ', 'PX')
    if special:
        # 交割月前二个月
        if mo <= 2:
            mo += 10
            yr -= 1
        else:
            mo -= 2
    else:
        # 交割月前一个月
        if mo == 1:
            mo = 12
            yr -= 1
        else:
            mo -= 1
    exp_ym = f'{yr:04d}-{mo:02d}'
    # Get trading dates in expiry month
    trade_dates = calendar.get(exp_ym, [])
    if not trade_dates:
        return 30
    # Rule: 第15个日历日之前(含)的倒数第3个交易日
    cutoff = date(yr, mo, 15)
    # Filter trading dates <= cutoff
    before = [d for d in trade_dates if datetime.strptime(d, '%Y-%m-%d').date() <= cutoff]
    if len(before) < 3:
        return 30
    # 倒数第3个 = 3rd from end
    expiry = before[-3]
    return expiry


def calc_max_pain(opt_df):
    if opt_df.empty:
        return 0
    strikes = sorted(opt_df['strike'].unique())
    best_s, best_val = 0, float('inf')
    for s in strikes:
        calls = opt_df[(opt_df['strike'] < s) & (opt_df['type'] == 'C')]
        puts = opt_df[(opt_df['strike'] > s) & (opt_df['type'] == 'P')]
        val = 0.0
        if not calls.empty:
            val += ((s - calls['strike']) * calls['oi']).sum()
        if not puts.empty:
            val += ((puts['strike'] - s) * puts['oi']).sum()
        if val < best_val:
            best_val, best_s = val, s
    return int(best_s)


def _calc_T(contract_code, trade_date_str, calendar):
    """Compute T (years) using exact expiry from calendar
    日历来不到时(未来月份)回退到交割月前一个月的15号近似
    """
    from datetime import datetime, date
    expiry_str = _czce_expiry(calendar, contract_code)
    if not isinstance(expiry_str, (int, float)):
        td = datetime.strptime(trade_date_str, '%Y-%m-%d').date()
        ex = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return max((ex - td).days / 365, 7 / 365)
    # Fallback: 未来月份无日历 → 近似15号
    m = re.search(r'[A-Z]+(\d{3})$', str(contract_code))
    if m:
        ym = m.group(1)
        ref_yr = int(trade_date_str[:4])
        cy = (ref_yr // 10) * 10 + int(ym[0])
        mo = int(ym[1:3])
        if mo == 1:
            mo = 12; cy -= 1
        else:
            mo -= 1
        expiry = date(cy, mo, 15)
        td = datetime.strptime(trade_date_str, '%Y-%m-%d').date()
        return max((expiry - td).days / 365, 7 / 365)
    return 30 / 365


def calc_gex(opt_df, main_px, mult, fut_prices, trade_date, calendar):
    """计算净 Gamma Exposure (GEX) = Σ call_GEX - Σ put_GEX
    每张期权使用其对应合约月份的期货价格，T 用实际剩余天数
    """
    from datetime import datetime, date
    total = 0.0
    for _, row in opt_df.iterrows():
        iv = row.get('iv', 0)
        oi = row.get('oi', 0)
        K = row.get('strike', 0)
        cp = row.get('type', 'C')
        if pd.isna(iv) or iv <= 1e-6 or oi <= 0 or K <= 0:
            continue
        opt_code = str(row.get('合约代码', ''))
        m = re.match(r'^([A-Z]+\d{3})[CP]', opt_code)
        px = main_px
        T = 30 / 365
        if m:
            contract_code = m.group(1)
            px = fut_prices.get(contract_code)
            if px is None or px <= 0:
                px = main_px
            else:
                T = _calc_T(contract_code, trade_date, calendar)
        if px <= 0:
            continue
        sqrt_T = np.sqrt(T)
        d1 = (np.log(px / K) + 0.5 * iv**2 * T) / (iv * sqrt_T)
        pdf = np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi)
        gamma = pdf / (px * iv * sqrt_T)
        g = gamma * oi * mult * px * px * 0.01
        total += g if cp == 'C' else -g
    return round(total, 2)


def calc_be(opt_df, px, is_call):
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
            v = (np.maximum(mid - filtered['strike'].values, 0) * filtered['oi'].values).sum()
            if v < total_cost: low = mid
            else: high = mid
        else:
            v = (np.maximum(filtered['strike'].values - mid, 0) * filtered['oi'].values).sum()
            if v < total_cost: high = mid
            else: low = mid
        if high - low < 0.01:
            break
    return round((low + high) / 2, 2)


def _read_czce_txt(path) -> pd.DataFrame | None:
    """读取 CZCE 的管道分隔 txt 文件（兼容单文件和按品种拆分两种格式）"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) < 3:
            return None
        # 第一行标题，第二行表头，第三行起数据
        header_line = lines[1].rstrip('\n\r').rstrip('|')  # 2017-2023 末尾有 |
        headers = [h.strip() for h in header_line.split('|')]
        # 统一列名：2017-2023 用品种代码/空盘量，2024+ 用合约代码/持仓量
        col_rename = {'品种代码': '合约代码', '空盘量': '持仓量'}
        headers = [col_rename.get(h, h) for h in headers]
        rows = []
        for line in lines[2:]:
            line = line.rstrip('\n\r').rstrip('|')
            if not line:
                continue
            vals = [v.strip() for v in line.split('|')]
            if len(vals) >= len(headers):
                rows.append(vals[:len(headers)])
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=headers)
        return df
    except Exception:
        return None


def _clean_num(series):
    return pd.to_numeric(series.astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)


def load_all_futures(year):
    """读所有 CZCE 期货 txt，返回 {sym: pd.DataFrame}（兼容单文件和目录两种格式）"""
    fut_path = FILE_DIR / f'ALLFUTURES{year}'
    all_df = []
    if fut_path.is_dir():
        # 2024+ 按品种拆分目录
        files = sorted(fut_path.glob('*FUTURES*.txt'))
    elif fut_path.with_suffix('.txt').exists():
        # 2017-2023 单文件
        files = [fut_path.with_suffix('.txt')]
    else:
        return {}
    for f in files:
        df = _read_czce_txt(f)
        if df is not None and '交易日期' in df.columns and '合约代码' in df.columns:
            all_df.append(df)
    if not all_df:
        return {}
    df = pd.concat(all_df, ignore_index=True)
    df['date'] = df['交易日期'].astype(str).str.strip()
    for c in ['今开盘', '最高价', '最低价', '今收盘', '成交量(手)']:
        if c in df.columns:
            df[c] = _clean_num(df[c])

    symbols = {}
    for sym, grp in df.groupby(df['合约代码'].astype(str).str.extract(r'^([A-Z]+)', expand=False)):
        if sym in CZCE_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def load_all_options(year):
    """读所有 CZCE 期权 txt，返回 {sym: pd.DataFrame}（兼容单文件和目录两种格式）"""
    opt_path = FILE_DIR / f'ALLOPTIONS{year}'
    all_df = []
    if opt_path.is_dir():
        files = sorted(opt_path.glob('*OPTIONS*.txt'))
    elif opt_path.with_suffix('.txt').exists():
        files = [opt_path.with_suffix('.txt')]
    else:
        return {}
    for f in files:
        df = _read_czce_txt(f)
        if df is not None and '交易日期' in df.columns and '合约代码' in df.columns:
            all_df.append(df)
    if not all_df:
        return {}
    df = pd.concat(all_df, ignore_index=True)
    df['date'] = df['交易日期'].astype(str).str.strip()

    parsed = df['合约代码'].apply(parse_opt_code)
    df['strike'] = parsed.apply(lambda x: x[0] if x else None)
    df['type'] = parsed.apply(lambda x: x[1] if x else None)
    df = df.dropna(subset=['strike'])
    df['strike'] = df['strike'].astype(float)

    for c, src in [('oi', '持仓量'), ('close', '今收盘'), ('volume', '成交量(手)')]:
        if src in df.columns:
            df[c] = _clean_num(df[src])

    df['delta'] = pd.to_numeric(df.get('DELTA', pd.Series([0]*len(df))), errors='coerce').fillna(0)
    iv_col = '隐含波动率' if '隐含波动率' in df.columns else None
    df['iv'] = pd.to_numeric(df[iv_col], errors='coerce').fillna(0) / 100 if iv_col else 0

    symbols = {}
    for sym, grp in df.groupby(df['合约代码'].astype(str).str.extract(r'^([A-Z]+)', expand=False)):
        if sym in CZCE_PREFIX_MAP and len(grp) > 10:
            symbols[sym] = grp
    return symbols


def process_one(sym, fut, opt, date=None, mult=10):
    """从已过滤的 fut/opt df 计算指标"""
    if fut is None or opt is None:
        return {}

    # 解析合约月份用于近月连续（CZCE 合约码如 TA601 → 601）
    fut = fut.copy()
    fut['contract_ym'] = fut['合约代码'].astype(str).str.extract(r'(\d{3})$')[0]

    fut_dates = {}
    fut_nc = {}
    for date_i, g in fut.groupby('date'):
        vol_col = '成交量(手)' if '成交量(手)' in g.columns else None
        if '成交量(手)' in g.columns:
            idx = g['成交量(手)'].idxmax()
        else:
            idx = g.index[0]
        r = g.loc[idx]
        if r.get('今收盘', 0) > 0:
            fut_dates[date_i] = {'o': float(r.get('今开盘', 0)), 'c': float(r.get('今收盘', 0)),
                                 'h': float(r.get('最高价', 0)), 'l': float(r.get('最低价', 0))}
        # 近月连续：取成交量>0且收盘价>0中交割月最早的
        if vol_col:
            active = g[(pd.to_numeric(g[vol_col], errors='coerce') > 0) & (pd.to_numeric(g['今收盘'], errors='coerce') > 0)]
        else:
            active = g[pd.to_numeric(g['今收盘'], errors='coerce') > 0]
        if not active.empty:
            front = active.sort_values('contract_ym').iloc[0]
            fut_nc[date_i] = float(front.get('今收盘', 0))

    opt_by_date = {str(d): g for d, g in opt.groupby('date')}

    if date:
        dl = len(date)
        if dl >= 8:  # YYYYMMDD 或 YYYY-MM-DD
            date_s = f'{date[:4]}-{date[4:6]}-{date[6:8]}' if '-' not in date else date
            if date_s in fut_dates and date_s in opt_by_date:
                fut_dates = {date_s: fut_dates[date_s]}
                opt_by_date = {date_s: opt_by_date[date_s]}
                print(f'    {sym}: --date {date_s}')
            else:
                print(f'    {sym}: ⚠ {date_s} 无数据，跳过')
                return {}
        elif dl == 6:  # YYYYMM
            ym = f'{date[:4]}-{date[4:6]}'
            fut_dates = {d: v for d, v in fut_dates.items() if d.startswith(ym)}
            opt_by_date = {d: v for d, v in opt_by_date.items() if d.startswith(ym)}
            if not fut_dates:
                print(f'    {sym}: ⚠ {ym} 无数据，跳过')
                return {}
            print(f'    {sym}: --date {ym} ({len(fut_dates)} 天)')

    calendar = _build_calendar(fut)
    result = {}
    for date, row in sorted(fut_dates.items()):
        if date not in opt_by_date:
            continue
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
        fut_prices = {r['合约代码']: float(r.get('今收盘', 0))
                      for _, r in fut[fut['date'] == date].iterrows()
                      if float(r.get('今收盘', 0)) > 0}
        gex = calc_gex(o, px, mult, fut_prices, date, calendar)

        # oc: 期权对应标的期货的收盘价（按期权持仓量最大的标的合约）
        oc_val = None
        opt_underlying = o['合约代码'].str.extract(r'^([A-Z]+\d{3})[CP]')[0]
        if not opt_underlying.isna().all():
            top_u = o.groupby(opt_underlying)['oi'].sum().idxmax()
            fut_match = fut[(fut['date'] == date) & (fut['合约代码'] == top_u)]
            if not fut_match.empty:
                oc_val = round(float(fut_match['今收盘'].iloc[0]), 2)

        result[date] = {'d': date, 'o': row['o'], 'c': px, 'nc': round(fut_nc.get(date, px), 2), 'oc': oc_val,
                        'h': row['h'], 'l': row['l'],
                        'mp': mp, 'co': co, 'po': po, 'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs, 'gex': gex}
    return result


def main():
    parser = argparse.ArgumentParser(description='CZCE txt → JSON')
    parser.add_argument('--date', default='', help='2026(全年) / 202606(月) / 20260629(日)')
    parser.add_argument('--symbol', default='', help='仅处理某品种')
    args = parser.parse_args()

    if not args.date:
        print('❌ 请指定 --date（2026 / 202606 / 20260629）')
        sys.exit(1)

    date_filter = args.date if len(args.date) > 4 else None
    year = int(args.date[:4])

    DATA_DIR.mkdir(parents=True, exist_ok=True)

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
        entries = process_one(sym, fut_by_sym[sym], opt_by_sym[sym], date=date_filter, mult=CZCE_MULT.get(sym, 10))
        if entries:
            records = sorted(entries.values(), key=lambda r: r['d'])
            output[sym] = records
            print(f'  {sym}: {len(records)} 天')

    if output:
        out = DATA_DIR / f'{year}.json'
        # 合并到已有文件（追加/更新，不丢失旧数据）
        if out.exists() and out.stat().st_size > 0:
            existing = json.loads(out.read_text())
        else:
            existing = {}
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
        out.write_text(json.dumps(existing, ensure_ascii=False))
        print(f'✅ {year}.json ({sum(len(v) for v in existing.values())} 条, {len(existing)} 品种, {time.time()-t0:.0f}s)')

    if not date_filter:
        print(f'\n📦 data/czce/')


if __name__ == '__main__':
    main()
