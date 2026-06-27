"""将预处理后的 JSON 数据上传到 Cloudflare D1 Worker

用法:
  python scripts/upload_to_d1.py --input data.json              # 单文件
  python scripts/upload_to_d1.py --input data.json --symbol TA  # 指定品种
  python scripts/upload_to_d1.py --data-dir data/dce            # 目录（所有 JSON）
  python scripts/upload_to_d1.py --data-dir data/dce --symbol M # 目录指定品种
"""
import argparse, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


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
