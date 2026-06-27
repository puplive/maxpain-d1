"""将 DCE xlsx 原始数据转为 JSON，按年份存入 data/ 目录，可选直接上传 D1
用法:
  python scripts/convert_dce_xlsx.py                     # 全部年份
  python scripts/convert_dce_xlsx.py --year 2026         # 仅某年
  python scripts/convert_dce_xlsx.py --year 2026 --upload  # 转换并上传
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


def parse_opt_code(code: str):
    m = re.search(r'[CP]-(\d+\.?\d*)$', str(code))
    if m:
        return float(m.group(1)), code[m.start()]
    return None


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


def process_symbol(sym, prefix, year):
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

    fut_dates = {}
    for date, group in fut.groupby('date'):
        idx = group['volume'].idxmax()
        row = group.loc[idx]
        if row['close'] > 0:
            fut_dates[date] = row

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

    result = {}
    for date, row in sorted(fut_dates.items()):
        if date not in opt_by_date:
            continue
        opt = opt_by_date[date]
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

        result[date] = {
            'd': date, 'o': round(float(row['open']), 2), 'c': round(px, 2),
            'h': round(float(row['high']), 2), 'l': round(float(row['low']), 2),
            'mp': mp, 'co': co, 'po': po,
            'bec': bec, 'bep': bep, 'vr': vr, 'ivs': ivs,
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
    parser.add_argument('--year', type=int, default=0, help='仅处理某年，默认全部')
    parser.add_argument('--symbol', default='', help='仅处理某品种')
    parser.add_argument('--upload', action='store_true', help='转换后直接上传到 D1')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', 'https://api.starrysay.com'))
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''))
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''), help='GitHub Token')
    args = parser.parse_args()

    if args.upload and not args.api_key:
        print('❌ --upload 需要 D1_API_KEY 环境变量')
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    years = [args.year] if args.year else sorted(
        set(d.name.replace('allVarietyFtr', '') for d in XLSX_DIR.glob('allVarietyFtr*') if d.is_dir())
    )

    for year in years:
        output = {}
        for sym, prefix in DCE_SYMBOLS.items():
            if args.symbol and sym != args.symbol.upper():
                continue
            entries = process_symbol(sym, prefix, year)
            if entries:
                records = sorted(entries.values(), key=lambda r: r['d'])
                output[sym] = records
                print(f'  {sym}: {len(records)} 天')

        if output:
            out_path = DATA_DIR / f'{year}.json'
            out_path.write_text(json.dumps(output, ensure_ascii=False))
            total = sum(len(v) for v in output.values())
            print(f'✅ {year}.json ({total} 条, {len(output)} 品种)')

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
