"""将预处理后的 JSON 数据上传到 Cloudflare D1 Worker"""
import argparse, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def upload(symbol: str, records: list[dict], worker_url: str, api_key: str, gh_token: str = ''):
    """上传单个品种数据到 D1"""
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
                print(f'  ✅ {symbol}: {result["count"]} 条上传成功')
            else:
                print(f'  ❌ {symbol}: {result}')
    except HTTPError as e:
        body = e.read().decode()
        print(f'  ❌ {symbol}: HTTP {e.code} {body}')


def main():
    parser = argparse.ArgumentParser(description='上传数据到 Cloudflare D1')
    parser.add_argument('--input', '-i', default='data.json', help='输入 JSON 文件')
    parser.add_argument('--symbol', '-s',
                        help='品种，默认全部')
    parser.add_argument('--worker-url', default=os.getenv('WORKER_URL', ''),
                        help='Worker API 地址')
    parser.add_argument('--api-key', default=os.getenv('D1_API_KEY', ''),
                        help='API 密钥')
    parser.add_argument('--gh-token', default=os.getenv('GH_UPLOAD_TOKEN', ''),
                        help='GitHub Token')
    args = parser.parse_args()

    if not args.worker_url:
        print('❌ 需要 WORKER_URL 或 --worker-url')
        sys.exit(1)
    if not args.api_key:
        print('❌ 需要 D1_API_KEY 或 --api-key')
        sys.exit(1)

    data = json.loads(Path(args.input).read_text())
    symbols = [args.symbol] if args.symbol else list(data.keys())

    for sym in symbols:
        records = data.get(sym, [])
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
