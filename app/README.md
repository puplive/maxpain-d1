# MaxPain 工作台（重构）

旧 `web/` 页面保持不变；新系统在 `app/` 下独立开发。

## 技术栈

- 前端：Vue 3 + TypeScript + Vite + Pinia + Vue Router + Naive UI
- 后端：FastAPI + SQLAlchemy 2 + SQLite
- 数据：历史行情只读现有 `web/data/*.json`；数据库只存用户、策略参数、扫描信号、回测与订单

## 目录

```
app/
├── frontend/                 # Vue SPA
│   └── src/
│       ├── api/              # 后端请求封装
│       ├── components/       # 图表等通用组件
│       ├── layouts/          # 主框架
│       ├── router/           # 路由
│       ├── stores/           # Pinia
│       └── views/            # 看板 / 今日机会 / 期权日历 / 策略参数 / 回测
└── backend/                  # FastAPI 服务
    └── app/
        ├── routers/          # symbols / expiries / screen / notes / params / signals / scan / backtest / optimize
        ├── services/         # 数据源适配
        ├── strategy/         # 策略核心（回测、扫描、实盘共用）
        ├── config.py
        ├── db.py
        ├── models.py
        └── main.py
```

## 本地启动

后端（Python 3.12+）：

```bash
cd app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

前端（Node 22.12+，推荐 24）：

```bash
cd app/frontend
npm install
npm run dev
```

Vite 已把 `/api` 代理到 `http://127.0.0.1:8000`，浏览器打开 `http://localhost:5173`。

## 当前 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/symbols` | 品种列表（来自 `web/data/index.json`） |
| GET | `/api/expiries` | 各品种期权到期日历（来自 `web/data/expiries.json`） |
| GET | `/api/screen/meta` | 条件构建器选项（窗口 / 指标 / 行为 / 聚合） |
| POST | `/api/screen` | 按原子条件筛选品种（条件可 AND / OR 组合） |
| GET | `/api/notes` | 研究笔记列表（含验证记录） |
| POST | `/api/notes` | 新建笔记 |
| PUT | `/api/notes/{id}` | 编辑笔记 |
| DELETE | `/api/notes/{id}` | 删除笔记（验证记录一起删除） |
| POST | `/api/notes/{id}/checks` | 追加验证记录，通过/未通过同步笔记状态 |
| POST | `/api/notes/{id}/review` | 间隔回顾：记住升档 / 模糊重置 |
| GET | `/api/symbols/{symbol}/bars?limit=300` | 轻量日线（d/o/h/l/c/mp/gpk/co/po/bec/bep/atm_iv/expiry） |
| GET | `/api/params/{strategy}` | 某策略的所有品种参数 |
| GET | `/api/params/{strategy}/{symbol}` | 单品种参数 |
| PUT | `/api/params/{strategy}/{symbol}` | 保存参数 |
| GET | `/api/signals?date=&strategy=` | 扫描结果 |
| POST | `/api/scan` | 扫描全部品种，写入 `daily_signals` |
| GET | `/api/backtest/{symbol}?start=&end=` | 逐日撮合回测，参数取保存值 |
| POST | `/api/backtest/{symbol}` | 同 GET，请求体 `params` 覆盖保存值（回测页微调用，不写库） |
| GET | `/api/backtest/{symbol}/chain?date=` | 单日期权链（OI 分布图，仅绘图字段） |
| POST | `/api/optimize/{symbol}` | 参数网格扫描，按期末权益返回前 N 组 |

参数以 JSON 存 `strategy_params` 表，键为 `(strategy, symbol)`；扫描结果存 `daily_signals`，键为 `(trade_date, strategy, symbol)`。

## 扫描口径（MaxPain v5）

- `trade_date` = 最后一根日线日期；信号对应**次日开盘**，盘后运行即可。
- 参数优先级：`strategy_params` 表按品种保存的优化参数 > `default_params`（取 `web/index.html` 的 `defaultParams`）。
  旧页面已保存的优化参数可同步到本地库：
  ```bash
  cd app/backend
  python -m scripts.sync_legacy_params              # 只补本地没有的品种
  python -m scripts.sync_legacy_params --overwrite  # 覆盖本地参数
  ```
- 方向：`lookback` 日 MP 的 EMA 偏离超过 `minPct`，且窗口净变化同向 → 多/空；否则震荡。
  以 `web/index.html` 实际代码为准，`strategy_rules.md` 已同步，没有“价格与 MP 同侧”过滤。
- 过滤：ATR/价格历史分位（`0/100` 关闭）、EWMA 快/慢波动比、价格斜率，参数来自保存值或默认值。
- 止损：`收盘价 ∓ ATR × entryAtrMult`，作为次日入场止损参考。
- 手数：按 `capital`（权益）和 `margin`（保证金率）估算；权益 ≥ 50 万时改用 `maxPos`，再按波动率目标化缩放。
- 重复扫描是幂等的：同键覆盖，信号消失时删除当天记录。
- `skipCount`、回踩挂单（`pbSlopeTh`）、信号准确率门控（`accMode`）已在回测引擎实现；
  扫描入口目前只做最后一天的过滤，尚未读取这些逐日状态，后续统一到同一份核心。

## 回测口径（MaxPain v5）

- `GET /api/backtest/{symbol}` 读取全量历史逐日撮合；`start/end` 只限制开仓区间，指标预热仍用全量数据。
- 参数优先用 `strategy_params` 里保存的优化值，回退 `default_params`。
- 回测页顶部“回测参数”表单可直接微调：“运行回测”走 `POST /api/backtest/{symbol}`，`params` 覆盖保存值，
  只影响本次回测、不写库；“保存参数”走 `PUT /api/params/{strategy}/{symbol}` 写回 `strategy_params`。
  合约乘数由品种信息决定，不在表单内。
- 已覆盖：方向反转、ATR 追踪、保本锁、期货换月、波动率分位、EWMA、价格斜率、`skipCount`、回踩挂单、准确率门控。
- MP 直接使用 `web/data` 里已算好的值；暂不在后端按 `mp_band` 从 `oc_chain` 重算（旧页面在浏览器端做）。
- 成本固定 10 元/手 × 2；期末持仓按最后收盘价计入权益。
- 返回交易明细、权益曲线、止损曲线和方向准确率；另返回 `bars`（K 线/BEC/BEP、到期日/天数、期货主力合约）、
  `indicators`（ATR、ATR/价格、EWMA 快慢比、斜率、MP 偏离、次日信号与门控状态）、
  `derivatives`（Gamma峰值、Call/Put Wall、ZeroGamma、Magnet/Pin、Δ25、OI 重心）、
  `subchart`（附图指标，见下）和本次生效 `params`。

## 看板筛选（条件构建器）

- 一个原子条件 = **窗口 + 左指标 + 行为 + 目标 + 聚合**，例如
  「近3日 / 价格区间 / 突破过 / Gamma峰值 / 任一日」。
- 左指标分两类：
  - `range`：价格区间，用当日最高/最低价判定；行为有 突破过（low≤目标≤high）、站上（low>目标）、跌破（high<目标）。
  - `value`：单值指标（收盘价、MP、BEC、BEP、Gamma峰值、GEX、OI PCR、IV偏斜、量比、ATM IV）；
    行为有 站上、跌破、上穿、下穿（后两者与前一交易日比较，筛选时多读一根 K 线）。
- 目标可以是另一个单值指标（逐日取它的值对比），或固定数值。
- 聚合决定窗口内如何命中：`any` 任一日（取最近一次命中）、`last` 最近一日、`all` 每日（说明附「连续N日」）。
- 多个条件按 `combine`（同时满足 / 任一满足）组合，只在上方下拉框选中的品种范围内筛选；
  看板卡片右上角显示命中说明（日期 + 指标 + 价位）。
- 顶部先选交易所（全部 / 大商所 / 郑商所 / 上期所 / 能源中心 / 中金所 / 广期所），
  品种下拉只列该交易所的品种，并自动选中该所全部品种（可在下拉里手动取消）；
  切回「全部交易所」时保留当前已选。交易所字段来自 `index.json` 的 `exchange`。
- 指标/行为/聚合白名单与判定逻辑统一在 `app/backend/app/services/screener.py`，
  前端选项来自 `GET /api/screen/meta`；后续扩展行为只需在 `BEHAVIORS` 注册表加一项。
- 筛选数据来自 `scripts/build_web_data.py` 预生成的 `web/data/screen.json`
  （每个品种最近 61 根日线的轻量字段）；后端按文件 mtime 缓存整份读取。
  不要改成解析完整品种文件：单文件含 `oc_chain`，全量解析 64 个品种会让筛选卡死。
- 条件和组合方式写在 localStorage（键 `maxpain.screenFilter`），刷新页面后自动还原；
  清空筛选时删除该记录。
- 交易所和已选品种由 Pinia `dashboard` store 管理并持久化
  （键 `maxpain.dashboardExchange` / `maxpain.dashboardSymbols`），写法与 `market.selectedSymbol` 一致。
  恢复时自动剔除数据中已不存在的交易所/品种；过滤后为空则默认选前 6 个。
- 筛选请求体：
  ```json
  { "combine": "and", "symbols": ["TA", "MA"], "conditions": [
    { "window": 3, "left": "price", "behavior": "break",
      "target": { "type": "indicator", "value": "gpk" },
      "aggregate": "any" }
  ] }
  ```
  `symbols` 为空数组时表示筛选全部品种。

## 研究笔记

- 三类笔记共用一套模板：`knowledge` 知识点 / `case` 案例 / `experience` 经验；
  每条带标题、标签、内容、可选关联品种与日期区间，以及状态
  `pending` 待验证 / `verified` 已验证 / `invalid` 已失效。
- 验证记录存 `note_checks` 表，只追加不覆盖；结果 `pass`/`fail` 会自动把笔记状态改成
  已验证/已失效，`observe` 不动状态。
- 间隔回顾用 `REVIEW_INTERVALS = (1, 3, 7, 30)` 天：新建时 1 天后提醒；点「记住了」升一档，
  「模糊」重置回 1 天；「今日回顾」按钮筛选 `next_review <= 今天` 的笔记。
- 列表的搜索和类型/状态/标签筛选在前端做（笔记量小）；关联了品种的案例可一键跳到回测页。
- 表结构在 `app/backend/app/models.py`，启动时 `Base.metadata.create_all` 自动建表。

## 期权日历

- 数据由 `scripts/build_web_data.py` 生成 `web/data/expiries.json`：按品种对「当日最近到期日」去重，
  记录历史至今的到期日；近月合约逐日滚动，每个品种最新一条就是下一个到期日。
- 日历页用 Naive UI `NCalendar` 展示，品种标签按交易所配色；点品种可直接跳到回测页。

## 复盘与调参

- 回测页图表：K 线叠加 MP/BEC/BEP/止损（虚线；最后一根 K 线收盘仍持仓时，向右延伸一格显示明日止损），
  最后再加一根开=收=最后收盘价的「明日」临时 K 线，按门控后的 `trade_pred` 画多/空信号；
  权益线也画在主图内（独立刻度 + 本金虚线，
  默认开启，可关闭）；鼠标拖动平移、单击 K 线锁定十字线（再点解除）、按住+滚轮或 Ctrl+滚轮缩放；
  触摸板双指横滑平移、捏合缩放（纵向双指滑动仍滚页面），触屏单指平移、单击锁定、双指捏合缩放；
  双击复位，
  悬停显示当天 OHLC、指标、衍生线、附图指标、权益、持仓信息（整个持仓区间都显示，浮动亏损标红）、
  次日信号（含门控状态；持仓中若明日方向反转会标注“次日开盘离场”）和当日成交。
  拖动不限制数据两端，可以把 K 线拖完后继续留白查看边缘；完全拖出数据时用最近一根 K 线
  收盘价 ± ATR 维持价格轴，双击恢复默认视图。
- 指标线可逐组显隐（MP、BE、止损、开平仓、收益、换月、到期、Gamma峰值、Call/Put Wall、
  ZeroGamma、Magnet/Pin、Δ25、OI重心），常用线默认开启，其余按需勾选。
- 日期轴放在主图 K 线正下方；下方附图与主图共用 x 轴，迁移旧 `#subChart` 指标：
  GEX/归一化GEX、OI偏度、OI净差、偏度差、量比、IV偏度、期货成交量/持仓量、ATR/TR、MP置信、噪音、
  净对冲、DEX方向、Vanna/Charm流、Gamma场景、Vega。
  每条指标按自身可见范围内的极值独立映射（对称类以 0 为中心），默认只开净对冲和 DEX；
  开关是附图顶部的一行文字（开启=指标颜色，关闭=灰色），悬停 tooltip 只列出当前开启且有值的附图指标。
- 附图数据由后端 `services/subchart.py` 从 `web/data` 计算：GEX/量比/IV 等直接读数据文件标量，
  MP置信/净对冲/DEX/Vanna/Charm 从 `oc_chain` 按显示带宽汇总，ATR 用 `atrPeriod` 做 Wilder 平滑，
  噪音用 `lookback` 日窗口；结果按品种 + 文件 mtime + 参数缓存。
- 附图2（旧 `#oiChart`）在画布最底部，横轴是行权价，随悬停 K 线切换到当日期权链：
  绿柱 Call OI、红柱 Put OI；`Δ对冲` 打开后柱高改为 |Δ×OI|（做市商需买卖的期货手数），
  并叠加 Δ 曲线与 ±0.25/±0.10 分界；有 IV 数据时画 Call/Put 微笑曲线。
  面板顶部概览显示最高柱、痛点/现价对应的卖方权利金，鼠标在面板内按行权价显示持仓与对冲明细。
  期权链按天从 `/api/backtest/{symbol}/chain` 拉取，前端按 品种|日期 缓存并做请求去重。
- 衍生线由后端 `services/derivatives.py` 从 `oc_chain` 计算，口径与旧页一致：
  Γ×OI 汇总当月全部月份，T 用近月 `dte`，S 用期权标的 `oc`（缺失回退收盘价）；
  显示带宽取参数 `band`（默认 20，现价 ±%，只影响展示，不参与信号），结果按
  品种 + 文件 mtime + 带宽缓存。SHFE 无 IV，Gamma 类线为空；有 OI 的线正常显示。
- 期权到期前 10 天在 K 线顶部显示倒计时数字（越近越红）；期货主力合约交割月前一个月
  画换月色带并标出目标合约，`fut_contract` 变化当天画“换”三角。
- 图表绘制按职责拆分：`components/chart/draw.ts`（折线/阶梯线）、`derivatives.ts`
  （主图衍生线定义、显隐、绘制）、`subchart.ts`（附图指标定义、刻度、显隐、绘制与悬停格式）、
  `oichart.ts`（附图2 OI 分布：持仓柱、Δ 曲线、IV 微笑、行权价悬停）、
  `markers.ts`（到期/换月/交易/告警标记），
  `BacktestChart.vue` 只保留布局、交互和 tooltip，后续加指标线不需要动主文件。
- 参数网格优化：`POST /api/optimize/{symbol}` 在保存参数基础上做笛卡尔积扫描，按期末权益排序返回前 40 组；
  候选范围沿用旧页面 `OPT_PARAMS`，组合数上限 10 万（同步请求，10 万组约 10~30 分钟）；
  回测页用多选下拉框选择要优化的参数，
  选项文字里标注候选范围；“填充最优值”把最优组合填回顶部参数表单（可再运行回测或保存），
  也可一键保存回 `strategy_params` 并重新回测。
  扫描时 ATR / EWMA / 逐日方向等预计算序列按参数缓存复用（斜率过滤关闭时不计算），
  方向预测与反转方向共用同一次窗口 EMA；组合数 ≥ 400 且 CPU > 1 时按分块交给
  `ProcessPoolExecutor`（spawn）并行。实测 TA：单次回测约 50ms，3000 组固定方向约 20 秒，
  1100 组（lookback × minPct）约 20 秒。
- 蒙特卡洛：前端对回测交易明细的 PnL 做 10000 次有放回重采样，显示实际结果百分位和分布直方图。
- 回测页选中的品种存 Pinia（`market.selectedSymbol`）并写入 localStorage，刷新后自动恢复并重跑；
  品种已不存在时回退 TA。
- “策略参数”页已覆盖策略核心的全部字段（趋势、仓位、止损保本、过滤、准确率门控）。

## 下一步

1. 让扫描入口复用回测引擎的逐日状态（`skipCount`/回踩/准确率门控），彻底统一扫描与回测。
2. 把回测复盘图接入看板多窗口和完整图表页。
3. 加入用户与认证，参数、信号、回测记录按用户隔离。
4. 独立执行服务（vn.py + CTP）与模拟撮合，先 paper trading 再接实盘。
