# Max Pain 趋势跟踪策略规则（v5）

**实际代码逻辑**：以 `web/index.html` 前端实现为准。以下策略规则文档保持同步。

---

## 1. 策略架构

```
MP 趋势方向 → EMA 方向判断 + 价格同侧过滤 → ATR 追踪止损 + 保本锁 + 方向反转出场 + 连胜跳过
```

MP 趋势跟踪。核心逻辑：MP 方向代表期权市场"最痛"位置漂移方向，跟随这个方向交易。

---

## 2. 开仓逻辑

### 2.1 MP 趋势方向 — 21 日 EMA

使用 21 日 EMA 判断 MP 趋势方向（非线性回归），带 buffer 阈值过滤：

```
ema = (close × k) + (prevEma × (1 - k)), k = 2 / (period + 1)
```

| latest > ema × (1 + threshold) | up |
| latest < ema × (1 - threshold) | down |
| 其余 | null |

### 2.2 入场条件

```
MP趋势 up   + prevClose > prevMp  → 开多
MP趋势 down + prevClose < prevMp  → 开空
```

- 使用前日收盘价（`prevClose`）和前日 MP（`prevMp`）避免预知未来
- 价格必须在 MP 同侧：做多时前收盘在 MP 之上，做空时前收盘在 MP 之下
- 额外检查盈亏平衡点穿破过滤（`bec`/`bep` 穿透判定结合 BE 结构过滤）

### 2.3 波动率自适应过滤

ATR/价格比率计算百分位，过滤过低/过高波动率区间：
- 默认过滤 5% 以下和 95% 以上分位的波动率
- 低波动 → 趋势弱；高波动 → 噪音多，均不做

### 2.4 仓位计算

```
dn = open × mult × margin%       # 单手保证金
eq < 500000 → alloc = eq         # 50万以下满仓
eq >= 500000 → alloc = eq × maxPos%  # 50万以上按比例
if capLimit > 0 → alloc = min(alloc, capLimit)
qty = floor(alloc / dn)          # 向下取整
```

### 2.5 入场止损

开仓瞬间检查当前 K 线的 lo/hi 是否已触发出场价，触发则立即止损：

```
long:  stop = open - atr * entryAtrMult
short: stop = open + atr * entryAtrMult
```

### 2.6 连胜跳过

某方向盈利出场后，跳过后 N 次反方向信号。

| 场景 | 行为 |
|------|------|
| 开多盈利 → | 跳过后面 N 次做空信号 |
| 开空盈利 → | 跳过后面 N 次做多信号 |

N 默认 0（关闭）。用意：顺方向赚到后暂停反方向开仓，避免反向信号频繁磨损。

---

## 3. 出场规则

### 3.1 方向反转出场（优先级 1）

| 方向 | 出场条件 |
|------|---------|
| 多头 | MP 趋势由 `up` 变为 `down` 或 `null` |
| 空头 | MP 趋势由 `down` 变为 `up` 或 `null` |

趋势不存在时持仓逻辑失效。出场后同一根 K 线可反向开仓。

### 3.2 ATR 追踪止损（优先级 2）

追踪止损线每日计算，写入 `stopCurve`。执行时按日期区分：

**入场第 2 天**（前一条 stopCurve 记录的 stop 仍是 entryStopLevel）：
- 重新根据昨日收盘计算追踪止损
- 做多：`stop = max(prevClose - atr × atrMult, entryStopLevel)`
- 做空：`stop = min(prevClose + atr × atrMult, entryStopLevel)`

**第 3 天起**：
- 直接使用前一天 stopCurve 中已计算好的止损值
- 不再重新计算

### 3.3 保本锁

价格朝有利方向移动超过 `保本%/锁定%` 后激活：

```
做多: be_th / lock_pct = 1% / 50% = 2% 涨幅 → 激活
做空: be_th / lock_pct = 1% / 50% = 2% 跌幅 → 激活
```

激活后，最低锁定止损 `entryPx + (peakPx - entryPx) × lockPct`

### 3.4 执行顺序

逐日检查：
1. 方向反转 → 开盘价出场
2. ATR 追踪止损 → 触发出场
3. 入场日立即检查（防跳空击穿）

---

## 4. 参数表

| 参数 | 默认 | 范围 | 说明 |
|------|------|------|------|
| lookback | 21 | 5~60 | 趋势计算窗口 |
| minPct | 0.1% | 0.2~5% | MP 趋势分类阈值（越大越难出方向） |
| maxPos | 50% | 5~60% | 单笔最大资金占用（50万以上生效） |
| margin | 15% | — | 交易所标准保证金率 |
| beTh | 1.0% | 0~5% | 激活保本锁所需盈利 |
| entryAtrMult | 1.0 | 0.5~3 | 入场止损 ATR 乘数 |
| atrPeriod | 14 | 7~30 | ATR 计算周期 |
| atrMult | 1.3 | 0.5~3 | 追踪止损 ATR 乘数 |
| lockPct | 50% | 10~70% | 锁定峰值利润比例 |
| volFilterLow | 5% | 0~50 | 波动率过滤下限百分位 |
| volFilterHigh | 95% | 50~100 | 波动率过滤上限百分位 |
| capital | 100,000 | — | 初始权益 |
| capLimit | 200 | 0~1000 | 单笔名义本金上限（万元），0=不限 |
| skipCount | 0 | 0~5 | 盈利后跳过同向信号次数 |

---

## 5. 设计演进

| 阶段 | 方向信号 | 过滤 | 出场 | 备注 |
|------|---------|------|------|------|
| 初始 | 3信号加权(VR+IVS+BE) | — | 多条件 | 过拟合 |
| 简化 | MP趋势斜率 | OI偏度确信度 | trail+结构击穿 | — |
| v1 | MP趋势斜率 | 发散门+IVS确信度 | trail+锁定+结构击穿 | — |
| v2 | MP趋势斜率 | 发散门 | trail+锁定+结构击穿+方向反转 | — |
| v3 | MP趋势+R² | 趋势MP附近/震荡BE附近 | 结构+痛点止盈+方向反转+trail | 自适应双模式 |
| v4 | MP趋势+ADX | ADX筛状态+RSI筛入场 | 结构+痛点止盈+方向反转+ATR | ADX+RSI+ATR |
| **v5** | **MP趋势(EMA)** | **价格同侧+波动率过滤** | **方向反转+ATR追踪+保本锁+连胜跳过** | **纯MP** |

**当前 v5 实际实现**：
- 方向：21 日 EMA 而非线性回归
- 出场：ATR 追踪 stopCurve（第 2 天重算，第 3 天起用缓存值）
- 入场第 1 天：entryStopLevel = open ± atr × entryAtrMult
- 入场第 2 天起：trackingStop = prevClose ± atr × atrMult（保底 entryStopLevel）
- 趋势分析/结构击穿仅显示参考，不参与出场决策

---

## 6. 策略特征

| 方面 | 说明 |
|------|------|
| **风格** | MP 趋势跟踪 |
| **数据频率** | 日线 |
| **核心逻辑** | MP 漂移方向即趋势方向，跟随交易 |
| **优势** | 极简，参数少，过拟合风险低 |
| **风险** | MP 在震荡市中频繁变向导致磨损；趋势快速反转时方向反转出场可能滞后 |
