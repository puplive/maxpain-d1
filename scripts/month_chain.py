"""按月拆分期权轻量链（记录字段 ocm），供 T 敏感指标逐月计算再汇总。

oc_chain 把各月份合约按行权价合并，后端只能统一用近月 dte 算 Gamma 类指标，
远月会被当近月处理。这里按「交割月系列」保留各自的到期天数：

    [{"dte": 12, "k": [[strike, co, po, civ, piv], ...]}, ...]

只保留 OI 占比 >= min_share 的月份（最近月始终保留），按 dte 升序；
civ/piv 缺失时为 null。旧数据没有该字段时后端回退到近月 dte 口径。
"""
import json

import pandas as pd


def expiry_dte(expiry, trade_date, fallback):
    """到期日为 'YYYY-MM-DD' 时返回距到期自然日（到期当天为 0）。

    日历缺失（如 2027+ 无交易日历）时回退到转换脚本的 T×365。
    """
    if isinstance(expiry, str) and expiry < '9000-01-01':
        return (pd.Timestamp(expiry) - pd.Timestamp(trade_date)).days
    return int(round(float(fallback)))


def build_month_chain(series, strike, oi, typ, iv, dte_of, min_share=0.01):
    """返回 ocm 的紧凑 JSON 字符串；没有有效持仓时返回 None。

    series: 月份合约系列（如 TA602 / m2601 / cu2601）
    typ:    C / P
    iv:     期权隐含波动率，可为 None（SHFE 等反推失败时）
    dte_of: 系列代码 -> 距到期自然日（精确到日历日，缺失时回退 T×365）
    """
    df = pd.DataFrame({
        'ser': series,
        'k': strike,
        'type': typ,
        'oi': oi,
        'iv': iv if iv is not None else float('nan'),
    })
    df['k'] = pd.to_numeric(df['k'], errors='coerce')
    df['oi'] = pd.to_numeric(df['oi'], errors='coerce').fillna(0)
    df['iv'] = pd.to_numeric(df['iv'], errors='coerce')
    df = df.dropna(subset=['ser', 'k', 'type'])
    df = df[(df['oi'] > 0) & (df['k'] > 0)]
    if df.empty:
        return None

    total = float(df['oi'].sum())
    groups = []
    for ser, g in df.groupby('ser', sort=False):
        try:
            dte = int(round(float(dte_of(ser))))
        except (TypeError, ValueError):
            continue
        if dte < 0:
            continue
        groups.append((ser, g, dte, float(g['oi'].sum()) / total))
    if not groups:
        return None

    # 临近到期时近月 OI 占比可能不足 min_share，但 Gamma ∝ 1/√T，贡献不可忽略，
    # 所以最近月无论占比多小都保留；其余月份按占比过滤
    nearest_dte = min(info[2] for info in groups)
    months = []
    for ser, g, dte, share in groups:
        if share < min_share and dte != nearest_dte:
            continue
        pt_oi = g.pivot_table(index='k', columns='type', values='oi',
                              aggfunc='sum', fill_value=0)
        pt_iv = g.pivot_table(index='k', columns='type', values='iv', aggfunc='mean')
        ks = []
        for k in pt_oi.index:
            ro = pt_oi.loc[k]
            co = int(ro.get('C', 0))
            po = int(ro.get('P', 0))
            if co <= 0 and po <= 0:
                continue
            civ = piv = None
            if k in pt_iv.index:
                riv = pt_iv.loc[k]
                if pd.notna(riv.get('C')) and riv['C'] > 0:
                    civ = round(float(riv['C']), 4)
                if pd.notna(riv.get('P')) and riv['P'] > 0:
                    piv = round(float(riv['P']), 4)
            ks.append([int(k), co, po, civ, piv])
        if ks:
            months.append({'dte': dte, 'k': ks})

    if not months:
        return None
    months.sort(key=lambda m: m['dte'])
    return json.dumps(months, separators=(',', ':'))
