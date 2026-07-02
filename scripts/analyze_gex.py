"""分析 GEX 与价格走势的统计规律"""
import json, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'web' / 'data'

def load(sym):
    return json.loads((DATA_DIR / f'{sym}.json').read_text())

def analyze(sym):
    data = load(sym)
    n = len(data)

    closes = np.array([r['c'] for r in data], dtype=float)
    gex = np.array([r.get('gex', 0) or 0 for r in data], dtype=float)
    mp = np.array([r['mp'] for r in data], dtype=float)

    ret = closes[1:] / closes[:-1] - 1  # n-1
    gex_prev = gex[:-1]  # n-1

    print(f'\n===== {sym} ({n} 天) =====')

    # 1. GEX 方向 vs 次日收益
    pos = gex_prev > 0
    neg = gex_prev < 0
    for name, mask in [('GEX>0', pos), ('GEX<0', neg)]:
        sub = ret[mask]
        if len(sub) > 0:
            print(f'  {name} 次日: 均值 {np.mean(sub)*100:+.2f}%  中位数 {np.median(sub)*100:+.2f}%  胜率 {np.mean(sub>0)*100:.0f}%  ({len(sub)} 次)')

    # 2. |GEX| 极端分位 vs 次日收益
    abs_gex = np.abs(gex_prev)
    p80, p20 = np.percentile(abs_gex, [80, 20])
    for name, mask in [(f'|GEX|>p80({p80:.0f})', abs_gex >= p80), (f'|GEX|<p20({p20:.0f})', abs_gex <= p20)]:
        sub = ret[mask]
        if len(sub) > 0:
            print(f'  {name} 次日: 均值 {np.mean(sub)*100:+.2f}%  胜率 {np.mean(sub>0)*100:.0f}%  ({len(sub)} 次)')

    # 3. GEX 穿越零轴后3日
    gex_s = np.sign(gex)
    cross_up_idx = np.where((gex_s[:-1] <= 0) & (gex_s[1:] > 0))[0]
    cross_dn_idx = np.where((gex_s[:-1] >= 0) & (gex_s[1:] < 0))[0]
    for name, idxs in [('负转正', cross_up_idx), ('正转负', cross_dn_idx)]:
        cum = []
        for i in idxs:
            if i + 3 < n - 1:
                cum.append(np.sum(ret[i+1:i+4]))
        if cum:
            arr = np.array(cum)
            print(f'  GEX{name}后3日: 均值 {np.mean(arr)*100:+.2f}%  胜率 {np.mean(arr>0)*100:.0f}%  ({len(arr)} 次)')

    # 4. MP-GEX 方向一致性
    mp_d = np.sign(np.diff(mp))
    gex_d = np.sign(np.diff(gex))
    agree = (mp_d == gex_d) & (mp_d != 0)
    disagree = (mp_d != 0) & (gex_d != 0) & (mp_d != gex_d)
    a_ret = ret[1:][agree[:-1]]
    d_ret = ret[1:][disagree[:-1]]
    if len(a_ret) > 0:
        print(f'  MP与GEX同向 次日: 均值 {np.mean(a_ret)*100:+.2f}%  胜率 {np.mean(a_ret>0)*100:.0f}%  ({len(a_ret)} 次)')
    if len(d_ret) > 0:
        print(f'  MP与GEX反向 次日: 均值 {np.mean(d_ret)*100:+.2f}%  胜率 {np.mean(d_ret>0)*100:.0f}%  ({len(d_ret)} 次)')

    # 5. GEX 连续趋势
    runs = []
    cur_s, cur_l = 0, 0
    for v in gex:
        s = np.sign(v)
        if s == 0:
            continue
        if s == cur_s:
            cur_l += 1
        else:
            if cur_s != 0:
                runs.append((cur_s, cur_l))
            cur_s, cur_l = s, 1
    if cur_s != 0:
        runs.append((cur_s, cur_l))
    pos_lens = [l for s, l in runs if s > 0]
    neg_lens = [l for s, l in runs if s < 0]
    for label, lens in [('GEX>0', pos_lens), ('GEX<0', neg_lens)]:
        if lens:
            print(f'  {label} 持续: 最长{max(lens)}天  平均{np.mean(lens):.0f}天  中位数{np.median(lens):.0f}天')

    # 6. GEX 与价格相关性
    corr = np.corrcoef(gex, closes)[0, 1]
    print(f'  GEX-价格相关性: {corr:.3f}')


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['TA', 'MA', 'SA', 'SR', 'CF', 'RM', 'M', 'I', 'C']
    for sym in syms:
        try:
            analyze(sym)
        except Exception as e:
            import traceback
            print(f'{sym}: {e}')
            traceback.print_exc()
