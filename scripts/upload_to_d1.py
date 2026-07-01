"""将预处理后的 JSON 数据上传到 Cloudflare D1 Worker

用法:
  python scripts/upload_to_d1.py --input data.json                        # 单文件
  python scripts/upload_to_d1.py --input data.json --symbol TA            # 指定品种
  python scripts/upload_to_d1.py --data-dir data/dce                      # 目录（所有 JSON）
  python scripts/upload_to_d1.py --data-dir data/dce --symbol M           # 目录指定品种
  python scripts/upload_to_d1.py --data-dir data --year 20260630          # 仅上传指定日期
  python scripts/upload_to_d1.py --data-dir data --year 202606            # 仅上传指定月份
"""
import argparse, calendar, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def parse_year_range(y: str) -> tuple[str, str] | None:
    """解析日期范围参数: 同 fetch_data.py"""
    if not y or y == '0':
        return None
    y = y.strip()
    if len(y) == 4:
        return (f'{y}-01-01', f'{y}-12-31')
    elif len(y) == 6:
        yr, mo = y[:4], y[4:6]
        last_day = calendar.monthrange(int(yr), int(mo))[1]
        return (f'{yr}-{mo}-01', f'{yr}-{mo}-{last_day:02d}')
    elif len(y) == 8:
        yr, mo, dy = y[:4], y[4:6], y[6:8]
        return (f'{yr}-{mo}-{dy}', f'{yr}-{mo}-{dy}')
    print(f'⚠ 无法解析日期参数: {y}，支持 4/6/8 位格式')
    sys.exit(1)


def filter_by_year(records: list[dict], year_param: str) -> list[dict]:
    """按日期范围过滤记录，record['d'] 格式为 'YYYY-MM-DD'"""
    yr = parse_year_range(year_param)
    if yr is None:
        return records
    start, end = yr
    return [r for r in records if start <= r['d'] <= end]


def upload(symbol: str, records: list[dict], worker_url: str, api_key: str, gh_token: str = ''):
    """上传单个品种数据到 D1"""
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
            result = json.loads(resp.read())
            if result.get('ok'):
                print(f'  ✅ {symbol}: {result["count"]} 条上传成功')
            else:
                print(f'  ❌ {symbol}: {result}')
    except HTTPError as e:
        body = e.read().decode()
        print(f'  ❌ {symbol}: HTTP {e.code} {body}')


def main():
    parser = argparse.ArgumentParser(description='上传数据到 Cloudflare D1')
    parser.add_argument('--input', '-i', default='', help='输入 JSON 文件')
    parser.add_argument('--data-dir', default='', help='数据目录，读取所有 JSON (如 data/dce)')
    parser.add_argument('--symbol', '-s', help='品种，默认全部')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', ''),
                        help='Worker API 地址')
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''),
                        help='API 密钥')
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''),
                        help='GitHub Token (X-GitHub-Token)')
    parser.add_argument('--year', default='0',
                        help='日期过滤: 2026(全年) / 202606(6月) / 20260626(单日), 默认全部')
    args = parser.parse_args()

    if not args.worker_url:
        print('❌ 需要 WORKER_URL 或 --worker-url')
        sys.exit(1)
    if not args.api_key:
        print('❌ 需要 D1_API_KEY 或 --api-key')
        sys.exit(1)

    # 收集数据
    all_data: dict[str, list] = {}

    if args.data_dir:
        data_dir = Path(args.data_dir)
        if not data_dir.exists():
            print(f'❌ 目录不存在: {data_dir}')
            sys.exit(1)
        for f in sorted(data_dir.glob('**/*.json')):
            data = json.loads(f.read_text())
            for sym, records in data.items():
                if sym not in all_data:
                    all_data[sym] = []
                all_data[sym].extend(records)
        # 去重：同品种+同日保留最后一条
        for sym in all_data:
            seen = {}
            for r in all_data[sym]:
                seen[r['d']] = r
            all_data[sym] = sorted(seen.values(), key=lambda r: r['d'])
    elif args.input:
        all_data = json.loads(Path(args.input).read_text())
    else:
        print('❌ 需要 --input 或 --data-dir')
        sys.exit(1)

    if args.symbol:
        symbols = [s.strip().upper() for s in args.symbol.split(',')]
    else:
        symbols = list(all_data.keys())

    # 日期过滤
    if args.year and args.year != '0':
        for sym in symbols:
            all_data[sym] = filter_by_year(all_data.get(sym, []), args.year)

    for sym in symbols:
        records = all_data.get(sym, [])
        if not records:
            print(f'  ⚠ {sym}: 无数据，跳过')
            continue
        # 分批上传，每批最多 500 条
        batch_size = 500
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            upload(sym, batch, args.worker_url, args.api_key, args.gh_token)


if __name__ == '__main__':
    main()
