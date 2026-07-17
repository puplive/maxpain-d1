"""将 DCE xlsx 原始数据转为 JSON，按年份存入 data/ 目录，可选直接上传 D1
用法:
  python scripts/convert_dce_xlsx.py --date 2026              # 全年
  python scripts/convert_dce_xlsx.py --date 202606            # 某月
  python scripts/convert_dce_xlsx.py --date 20260629          # 某天
  python scripts/convert_dce_xlsx.py --date 20260629 --upload # 转换并上传
输出:
  data/dce/2025.json  → { "M": [...], "C": [...], ... }
"""
import argparse, json, os, re, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'dce'
XLSX_DIR = ROOT / 'file' / 'dce'

DCE_SYMBOLS = {
    'M': 'm', 'C': 'c', 'L': 'l', 'V': 'v', 'PP': 'pp',
    'I': 'i', 'PG': 'pg', 'Y': 'y', 'P': 'p', 'A': 'a',
    'B': 'b', 'EG': 'eg', 'EB': 'eb', 'JD': 'jd', 'CS': 'cs',
    'LH': 'lh', 'LG': 'lg', 'JM': 'jm', 'FB': 'fb',
}

DCE_MULT = {
    'M': 10, 'C': 10, 'L': 5, 'V': 5, 'PP': 5,
    'I': 100, 'PG': 20, 'Y': 10, 'P': 10, 'A': 10,
    'B': 10, 'EG': 10, 'EB': 5, 'JD': 5, 'CS': 10,
    'LH': 16, 'LG': 90,
}


def parse_opt_code(code: str):
    m = re.search(r'[CP]-(\d+\.?\d*)$', str(code))
    if m:
        return float(m.group(1)), code[m.start()]
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


def _dce_expiry(calendar, contract_code):
    """DCE 到期日：交割月前一个月的第12个交易日
    特殊品种(C,M)：交割月前二个月的第12个交易日
    """
    m = re.search(r'[a-z]+(\d{4})$', str(contract_code))
    if not m:
        return 30
    sym = re.match(r'^[a-z]+', str(contract_code))
    sym = sym.group(0).upper() if sym else ''
    ym = m.group(1)
    yr = 2000 + int(ym[:2])
    mo = int(ym[2:4])
    ref_yr = 2000 + int(ym[:2])
    if yr < ref_yr - 2:
        yr += 100
    # Special: C (玉米), M (豆粕) → 前二个月
    special = sym in ('C', 'M')
    if special:
        if mo <= 2:
            mo += 10; yr -= 1
        else:
            mo -= 2
    else:
        if mo == 1:
            mo = 12; yr -= 1
        else:
            mo -= 1
    exp_ym = f'{yr:04d}-{mo:02d}'
    trade_dates = calendar.get(exp_ym, [])
    if not trade_dates:
        return 30
    if len(trade_dates) < 12:
        return 30
    # 第12个交易日
    return trade_dates[11]


def _calc_T_dce(contract_code, trade_date_str, calendar):
    """Compute T (years) for DCE using exact expiry
    日历来不到时(未来月份)回退到交割月前一个月的15号近似
    """
    from datetime import datetime, date
    expiry_str = _dce_expiry(calendar, contract_code)
    if not isinstance(expiry_str, (int, float)):
        td = datetime.strptime(trade_date_str, '%Y-%m-%d').date()
        ex = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return max((ex - td).days / 365, 7 / 365)
    # Fallback: 未来月份无日历 → 近似15号
    m = re.search(r'[a-z]+(\d{4})$', str(contract_code))
    if m:
        ym = m.group(1)
        yr = 2000 + int(ym[:2])
        mo = int(ym[2:4])
        ref_yr = int(trade_date_str[:4])
        if yr < ref_yr - 2:
            yr += 100
        if mo == 1:
            mo = 12; yr -= 1
        else:
            mo -= 1
        expiry = date(yr, mo, 15)
        td = datetime.strptime(trade_date_str, '%Y-%m-%d').date()
        return max((expiry - td).days / 365, 7 / 365)
    return 30 / 365


def calc_max_pain(opt_df):
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
        opt_name = str(row.get('合约名称', ''))
        m = re.search(r'^(.+?)-', opt_name)
        px = main_px
        T = 30 / 365
        if m:
            contract_code = m.group(1)
            px = fut_prices.get(contract_code)
            if px is None or px <= 0:
                px = main_px
            else:
                T = _calc_T_dce(contract_code, trade_date, calendar)
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
    total_cost = (filtered['opt_close'] * filtered['oi']).sum()
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
            if intrinsic < total_cost: low = mid
            else: high = mid
        else:
            if intrinsic < total_cost: high = mid
            else: low = mid
        if high - low < 0.01:
            break
    return round((low + high) / 2, 2)


def process_symbol(sym, prefix, year, date=None):
    """处理单个品种某一年"""
    ftr_year = f'allVarietyFtr{year}'
    opt_year = f'allVarietyOpt{year}'

    ftr_file = XLSX_DIR / ftr_year / f'{prefix}_ftr.xlsx'
    opt_file = XLSX_DIR / opt_year / f'{prefix}_opt.xlsx'

    if not ftr_file.exists():
        return {}

    # 读期货
    fut = pd.read_excel(ftr_file)
    fut['date'] = pd.to_datetime(fut['交易日期'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    for c in ['成交量', '收盘价', '开盘价', '最高价', '最低价']:
        fut[c] = fut[c].astype(str).str.replace(',', '')
    fut['volume'] = pd.to_numeric(fut['成交量'], errors='coerce').fillna(0)
    fut['close'] = pd.to_numeric(fut['收盘价'], errors='coerce').fillna(0)
    fut['open'] = pd.to_numeric(fut['开盘价'], errors='coerce').fillna(0)
    fut['high'] = pd.to_numeric(fut['最高价'], errors='coerce').fillna(0)
    fut['low'] = pd.to_numeric(fut['最低价'], errors='coerce').fillna(0)

    # 解析合约月份，用于近月连续
    fut['contract_ym'] = fut['合约名称'].str.extract(r'(\d{4})$')[0]

    fut_dates = {}
    fut_nc = {}
    for dt, group in fut.groupby('date'):
        idx = group['volume'].idxmax()
        row = group.loc[idx]
        if row['close'] > 0:
            fut_dates[dt] = row
        # 近月连续：取成交量>0中交割月最早的且收盘价>0
        active = group[(group['volume'] > 0) & (group['close'] > 0)]
        if not active.empty:
            front = active.sort_values('contract_ym').iloc[0]
            fut_nc[dt] = float(front['close'])
    for dt, group in fut.groupby('date'):
        idx = group['volume'].idxmax()
        row = group.loc[idx]
        if row['close'] > 0:
            fut_dates[dt] = row

    if not opt_file.exists():
        return {}

    # 读期权
    opt_all = pd.read_excel(opt_file)
    opt_all['date'] = pd.to_datetime(opt_all['交易日期'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')

    parsed = opt_all['合约名称'].apply(parse_opt_code)
    opt_all['strike'] = parsed.apply(lambda x: x[0] if x else None)
    opt_all['type'] = parsed.apply(lambda x: x[1] if x else None)
    opt_all = opt_all.dropna(subset=['strike'])
    opt_all['strike'] = opt_all['strike'].astype(float)

    for src in ['持仓量', '收盘价', '成交量']:
        opt_all[src] = opt_all[src].astype(str).str.replace(',', '')
    opt_all['oi'] = pd.to_numeric(opt_all['持仓量'], errors='coerce').fillna(0)
    opt_all['opt_close'] = pd.to_numeric(opt_all['收盘价'], errors='coerce').fillna(0)
    opt_all['opt_volume'] = pd.to_numeric(opt_all['成交量'], errors='coerce').fillna(0)
    opt_all['delta'] = pd.to_numeric(opt_all['Delta'], errors='coerce')
    opt_all['iv'] = pd.to_numeric(opt_all['隐含波动率(%)'], errors='coerce').fillna(0) / 100

    opt_by_date = {str(d): g for d, g in opt_all.groupby('date')}

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
    for dt, row in sorted(fut_dates.items()):
        if dt not in opt_by_date:
            continue
        opt = opt_by_date[dt]
        px = float(row['close'])

        rng = 0.20
        do_mp = opt[opt['strike'].between(px * (1 - rng), px * (1 + rng))]
        mp = calc_max_pain(do_mp if len(do_mp) > 0 else opt)

        co = float(opt[(opt['strike'] > px) & (opt['type'] == 'C')]['oi'].sum())
        po = float(opt[(opt['strike'] < px) & (opt['type'] == 'P')]['oi'].sum())

        do_be = opt[opt['strike'].between(px * (1 - rng), px * (1 + rng))]
        bec = calc_be(do_be, px, True) if len(do_be) > 0 else None
        bep = calc_be(do_be, px, False) if len(do_be) > 0 else None

        cv = float(opt[opt['type'] == 'C']['opt_volume'].sum())
        pv = float(opt[opt['type'] == 'P']['opt_volume'].sum())
        vr = round(cv / pv, 2) if pv > 0 else None

        civ = opt[(opt['type'] == 'C') & (opt['delta'].between(0.20, 0.30))]['iv'].mean()
        piv = opt[(opt['type'] == 'P') & (opt['delta'].between(-0.30, -0.20))]['iv'].mean()
        ivs = round(piv - civ, 4) if (pd.notna(civ) and pd.notna(piv)) else None
        fut_prices = {r['合约名称']: float(r['close'])
                      for _, r in fut[fut['date'] == dt].iterrows()
                      if float(r['close']) > 0}
        gex = calc_gex(opt, px, DCE_MULT.get(sym, 10), fut_prices, dt, calendar)

        # oc: 期权对应标的期货的收盘价（按期权持仓量最大的标的合约）
        oc_val = None
        opt_underlying = opt['合约名称'].str.extract(r'^(.+?)-')[0]
        if not opt_underlying.isna().all():
            top_u = opt.groupby(opt_underlying)['oi'].sum().idxmax()
            fut_match = fut[(fut['date'] == dt) & (fut['合约名称'] == top_u)]
            if not fut_match.empty:
                oc_val = round(float(fut_match['close'].iloc[0]), 2)

        result[dt] = {
            'd': dt, 'o': round(float(row['open']), 2), 'c': round(px, 2),
            'h': round(float(row['high']), 2), 'l': round(float(row['low']), 2),
            'nc': round(fut_nc.get(dt, px), 2), 'oc': oc_val,
            'mp': mp, 'co': co, 'po': po,
            'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs, 'gex': gex,
        }

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
    parser.add_argument('--symbol', default='', help='仅处理某品种')
    parser.add_argument('--upload', action='store_true', help='转换后直接上传到 D1')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', 'https://api.starrysay.com'))
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''))
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''), help='GitHub Token')
    args = parser.parse_args()

    if not args.date:
        print('❌ 请指定 --date（2026 / 202606 / 20260629）')
        sys.exit(1)

    if args.upload and not args.api_key:
        print('❌ --upload 需要 D1_API_KEY 环境变量')
        sys.exit(1)

    date_filter = args.date if len(args.date) > 4 else None
    year = int(args.date[:4])

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    output = {}
    for sym, prefix in DCE_SYMBOLS.items():
        if args.symbol and sym != args.symbol.upper():
            continue
        entries = process_symbol(sym, prefix, year, date=date_filter)
        if entries:
            records = sorted(entries.values(), key=lambda r: r['d'])
            output[sym] = records
            print(f'  {sym}: {len(records)} 天')

    if output:
        out_path = DATA_DIR / f'{year}.json'
        # 合并到已有文件（追加/更新，不丢失旧数据）
        if out_path.exists():
            existing = json.loads(out_path.read_text())
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
        out_path.write_text(json.dumps(existing, ensure_ascii=False))
        total = sum(len(v) for v in existing.values())
        print(f'✅ {year}.json ({total} 条, {len(existing)} 品种)')

        if args.upload:
            uploaded = 0
            for sym, records in output.items():
                print(f'  ↗ {sym} ({len(records)} 条)...')
                for i in range(0, len(records), 500):
                    n = upload_batch(sym, records[i:i+500], args.worker_url, args.api_key, args.gh_token)
                    uploaded += n
            print(f'  📤 已上传 {uploaded} 条到 D1')

    print(f'\n📦 data/dce/')


if __name__ == '__main__':
    main()
