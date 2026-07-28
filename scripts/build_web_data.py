"""将 data/czce/*.json + data/dce/*.json 合并拆分成 web/data/{symbol}.json
并在 web/data/index.json 记录品种列表

用法: python scripts/build_web_data.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
OUT_DIR = ROOT / 'web' / 'data'

SYMBOL_CFG = {
    # CZCE
    'TA': {'name': 'PTA',     'exchange': 'czce', 'mult': 5},
    'MA': {'name': '甲醇',    'exchange': 'czce', 'mult': 10},
    'SA': {'name': '纯碱',    'exchange': 'czce', 'mult': 20},
    'SR': {'name': '白糖',    'exchange': 'czce', 'mult': 10},
    'CF': {'name': '棉花',    'exchange': 'czce', 'mult': 5},
    'RM': {'name': '菜粕',    'exchange': 'czce', 'mult': 10},
    'OI': {'name': '菜油',    'exchange': 'czce', 'mult': 10},
    'PK': {'name': '花生',    'exchange': 'czce', 'mult': 5},
    'PF': {'name': '短纤',    'exchange': 'czce', 'mult': 5},
    'SM': {'name': '锰硅',    'exchange': 'czce', 'mult': 5},
    'SF': {'name': '硅铁',    'exchange': 'czce', 'mult': 5},
    'UR': {'name': '尿素',    'exchange': 'czce', 'mult': 20},
    'AP': {'name': '苹果',    'exchange': 'czce', 'mult': 10},
    'CJ': {'name': '红枣',    'exchange': 'czce', 'mult': 5},
    'FG': {'name': '玻璃',    'exchange': 'czce', 'mult': 20},
    'PX': {'name': '对二甲苯', 'exchange': 'czce', 'mult': 5},
    'SH': {'name': '烧碱',    'exchange': 'czce', 'mult': 10},
    # DCE
    'C':  {'name': '玉米',     'exchange': 'dce', 'mult': 10},
    'M':  {'name': '豆粕',     'exchange': 'dce', 'mult': 10},
    'I':  {'name': '铁矿石',   'exchange': 'dce', 'mult': 100},
    'PG': {'name': '液化石油气','exchange': 'dce', 'mult': 20},
    'L':  {'name': '聚乙烯',   'exchange': 'dce', 'mult': 5},
    'V':  {'name': '聚氯乙烯', 'exchange': 'dce', 'mult': 5},
    'PP': {'name': '聚丙烯',   'exchange': 'dce', 'mult': 5},
    'P':  {'name': '棕榈油',   'exchange': 'dce', 'mult': 10},
    'A':  {'name': '豆一',     'exchange': 'dce', 'mult': 10},
    'B':  {'name': '豆二',     'exchange': 'dce', 'mult': 10},
    'Y':  {'name': '豆油',     'exchange': 'dce', 'mult': 10},
    'EG': {'name': '乙二醇',   'exchange': 'dce', 'mult': 10},
    'EB': {'name': '苯乙烯',   'exchange': 'dce', 'mult': 5},
    'JD': {'name': '鸡蛋',     'exchange': 'dce', 'mult': 5},
    'CS': {'name': '玉米淀粉', 'exchange': 'dce', 'mult': 10},
    'LH': {'name': '生猪',     'exchange': 'dce', 'mult': 16},
    'LG': {'name': '原木',     'exchange': 'dce', 'mult': 90},
}

# 各品种合约乘数（跨交易所统一）
SYM_MULT = {sym: cfg['mult'] for sym, cfg in SYMBOL_CFG.items()}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 收集所有数据: {sym: {date: record}}
    pool: dict[str, dict[str, dict]] = {}

    for json_path in sorted(DATA_DIR.rglob('*.json')):
        data = json.loads(json_path.read_text())
        for sym, records in data.items():
            if sym not in pool:
                pool[sym] = {}
            for rec in records:
                # 预计算权利金最高行权价
                if rec.get('oc_chain'):
                    try:
                        ch = json.loads(rec['oc_chain']) if isinstance(rec['oc_chain'], str) else rec['oc_chain']
                        ps = 0
                        pmax = -1
                        price = rec.get('c') or 0
                        for it in ch:
                            # 同 MP，只考虑现价 ±20% 范围内的行权价
                            if price > 0 and abs(it['s'] - price) / price > 0.2:
                                continue
                            prem = (it.get('cclose') or 0) * it.get('co', 0) + (it.get('pclose') or 0) * it.get('po', 0)
                            if prem > pmax:
                                pmax = prem
                                ps = it['s']
                        rec['ps'] = ps if pmax > 0 else 0
                    except Exception:
                        rec['ps'] = 0
                else:
                    rec['ps'] = 0
                pool[sym][rec['d']] = rec

    # 排序、去重后写出
    symbols = sorted(pool.keys())
    for sym in symbols:
        records = sorted(pool[sym].values(), key=lambda r: r['d'])
        (OUT_DIR / f'{sym}.json').write_text(json.dumps(records, ensure_ascii=False))

    # index.json
    names = {sym: SYMBOL_CFG[sym]['name'] for sym in symbols if sym in SYMBOL_CFG}
    mults = {sym: SYM_MULT[sym] for sym in symbols if sym in SYM_MULT}
    index = {'symbols': symbols, 'names': names, 'mult': mults}
    (OUT_DIR / 'index.json').write_text(json.dumps(index, ensure_ascii=False))

    total = sum(len(pool[sym]) for sym in symbols)
    print(f'✅ {len(symbols)} 品种, {total} 条 → {OUT_DIR}/')


if __name__ == '__main__':
    main()
