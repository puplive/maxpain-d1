# MaxPain D1 — 数据流水线

官网下载期货/期权历史数据，计算 Max Pain / GEX，生成前端静态 JSON，可选写入 Cloudflare D1。

## 架构

```
交易所官网 TXT/XLSX → 转换脚本 → data/{czce,dce,shfe}/*.json
                                        ↓
                               build_web_data.py（合并去重 + GEX 等计算）
                                        ↓
                               web/data/{symbol}.json → 前端直接加载
```

AKShare 曾作为数据源但因不稳定已弃用，全部统一为交易所官网下载。

## 交易所 & 数据来源

| 交易所 | 品种 | 来源 | 数据质量 |
|--------|------|------|----------|
| CZCE 郑商所 | TA/MA/SA/SR/CF/RM/OI 等 | 官网 TXT | ✅ 完整 |
| DCE 大商所 | M/C/I/P/Y/EG/EB/V 等 | 官网 XLSX | ✅ 完整 |
| SHFE 上期所 | CU/AL/ZN/RB/HC/AU/AG/RU 等 | 官网 XLSX | ⚠️ 缺 IV/Delta，GEX 无效 |

> SHFE 的 XLSX 缺少 Delta/隐含波动率字段，IV 偏斜(IVS)和 GEX 计算结果不可靠。

## 文件结构

```
├── .github/workflows/
│   └── daily-update.yml            # 数据上传 workflow（手动触发）
├── scripts/
│   ├── convert_czce.py             # CZCE TXT → data/czce/*.json
│   ├── convert_dce_xlsx.py         # DCE XLSX → data/dce/*.json
│   ├── convert_shfe.py             # SHFE XLSX → data/shfe/*.json
│   ├── fetch_data.py               # AKShare 拉取（次要路径，仅 CZCE，不稳定）
│   ├── build_web_data.py           # data/*.json → web/data/*.json（合并+计算）
│   ├── upload_to_d1.py             # JSON → Worker API → D1
│   └── requirements.txt
├── data/
│   ├── czce/                       # CZCE 预处理后 JSON（按年）
│   ├── dce/                        # DCE 预处理后 JSON（按年）
│   └── shfe/                       # SHFE 预处理后 JSON（按年）
├── file/                           # 官网下载原始文件（不入 git）
│   ├── czce/
│   ├── dce/
│   └── shfe/
├── worker/                         # Cloudflare Worker（D1 API）
├── web/
│   ├── data/                       # 前端静态数据（build_web_data.py 生成）
│   │   ├── index.json
│   │   ├── TA.json
│   │   └── ...
│   ├── index.html                  # 回测页面
│   └── train.html                  # 模拟训练
└── CLAUDE.md
```

## 数据流

```
交易所官网 TXT/XLSX → 转换脚本 → data/{czce,dce,shfe}/*.json（按年拆分）
                                       ↓
                              build_web_data.py（跨年合并去重）
                                       ↓
                              web/data/{symbol}.json（按品种，全量数据）
```

前端直接从 `web/data/` 加载静态 JSON，不走 Worker API。

每个记录包含：`d, o, c, h, l, mp, co, po, bec, bep, vr, ivs, gex, oc_chain, expiry, dte, oi_total, oi_pcr, oi_max_strike, vol_call, vol_put, vol_total, fut_vol, fut_oi, fut_turnover, atm_iv`

### 构建前端数据

转换脚本跑完后，执行一次构建即可更新前端数据：

```bash
python scripts/build_web_data.py
```

## 日常操作

### CZCE 数据更新

```bash
# 1. 去郑商所官网下载 TXT 文件到 file/czce/
# 2. 转换
python scripts/convert_czce.py --date 2026              # 全年
python scripts/convert_czce.py --date 202606            # 某月
python scripts/convert_czce.py --date 20260629          # 某天
# 3. 构建前端数据
python scripts/build_web_data.py
```

### DCE 数据更新

```bash
# 1. 去大商所官网下载
#    http://www.dce.com.cn → 历史数据
#    下载 allVarietyFtr2026.zip (期货)
#    下载 allVarietyOpt2026.zip (期权)
# 2. 解压到 file/dce/
unzip -o allVarietyFtr2026.zip -d file/dce/allVarietyFtr2026
unzip -o allVarietyOpt2026.zip -d file/dce/allVarietyOpt2026
# 3. 转换
python scripts/convert_dce_xlsx.py --date 2026
python scripts/convert_dce_xlsx.py --date 20260629 --symbol M
# 4. 构建
python scripts/build_web_data.py
```

### SHFE 数据更新

```bash
# 1. 去上期所官网下载 XLSX 到 file/shfe/
#    期货放 fu2026/，期权放 opt2026/
# 2. 转换
python scripts/convert_shfe.py --date 2026
python scripts/convert_shfe.py --date 20260629
# 3. 构建
python scripts/build_web_data.py
```

### 上传到 D1

```bash
# 目录上传
python scripts/upload_to_d1.py --data-dir data -s TA \
  --worker-url https://api.starrysay.com --api-key <key>
```

## 部署

### Worker

```bash
cd worker
npm install
npx wrangler login
npx wrangler deploy
```

Worker 域名: `maxpain-api.136320309workersdev.workers.dev`
自定义域名: `api.starrysay.com`

### D1 数据库操作

```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "SELECT * FROM daily_data WHERE symbol = 'TA' LIMIT 5"
```

新增字段（表结构变更）：
```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "ALTER TABLE backtest_params ADD COLUMN vol_filter_low REAL DEFAULT 0;"
```

#### daily_data 表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | TEXT | 品种代码 |
| date | TEXT | 日期 YYYY-MM-DD |
| open/close/high/low | REAL | OHLC |
| mp | INTEGER | Max Pain |
| co/po | REAL | Call/Put OI |
| bec/bep | REAL | Call/Put 盈亏平衡点 |
| vr | REAL | Put/Call 成交量比 |
| ivs | REAL | IV 偏斜 |
| gex | REAL | Gamma Exposure |
| oc_chain | TEXT | OI链 JSON |
| expiry | TEXT | 最近到期日 |
| dte | INTEGER | 距离到期天数 |
| oi_total | REAL | 总持仓 |
| oi_pcr | REAL | Put/Call OI 比 |
| oi_max_strike | INTEGER | 最大持仓行权价 |
| vol_call/vol_put/vol_total | REAL | 成交量 |
| fut_vol/fut_oi/fut_turnover | REAL | 期货成交量/持仓/成交额 |
| atm_iv | REAL | 平值 IV |

#### backtest_params 表字段

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| symbol | TEXT | — | 品种代码 |
| lookback | INTEGER | 21 | 趋势计算窗口 |
| min_pct | REAL | 0.1 | MP 趋势分类阈值(%) |
| max_pos | REAL | 50 | 仓位(%) |
| margin | REAL | 15 | 保证金(%) |
| be_th | REAL | 1.0 | 保本(%) |
| entry_atr_mult | REAL | 1.0 | 入场 ATR 乘数 |
| atr_period | INTEGER | 14 | ATR 计算周期 |
| atr_mult | REAL | 1.3 | 追踪 ATR 乘数 |
| lock_pct | REAL | 50 | 锁定(%) |
| vol_filter_low | REAL | 5 | 波动率过滤下限(%) |
| vol_filter_high | REAL | 95 | 波动率过滤上限(%) |
| capital | INTEGER | 100000 | 本金 |
| cap_limit | INTEGER | 200 | 封顶(万元)，0=不限 |
| skip_count | INTEGER | 0 | 盈利后跳过同向信号次数 |

### GitHub Secrets

| Secret | 值 |
|--------|---|
| `WORKER_URL` | `https://api.starrysay.com` |
| `D1_API_KEY` | 与 wrangler.toml 中 `API_KEY` 一致 |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/data?symbol=TA` | 获取品种全部数据 |
| POST | `/api/update` | 上传数据 (需 Bearer Token) |
| GET | `/api/symbols` | 获取数据库已有品种列表 |
| DELETE | `/api/data?symbol=TA` | 删除品种数据 (需鉴权) |
| GET | `/api/stats` | 各品种数据统计 |
| POST | `/api/params` | 保存参数 |
| GET | `/api/params?symbol=TA` | 读取参数 |
