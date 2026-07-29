# MaxPain D1 项目

期权 Max Pain 数据流水线 → 前端回测/训练页面。

## 项目结构

```
├── .github/workflows/daily-update.yml  # 全量数据更新 workflow（手动触发）
├── scripts/
│   ├── convert_czce.py                 # CZCE 官网 TXT → data/czce/*.json
│   ├── convert_dce_xlsx.py             # DCE 官网 XLSX → data/dce/*.json
│   ├── convert_shfe.py                 # SHFE 官网 XLSX → data/shfe/*.json
│   ├── fetch_data.py                   # AKShare 拉取（次要路径，仅 CZCE，不稳定）
│   ├── build_web_data.py               # data/*.json → web/data/*.json（合并去重+计算衍生指标）
│   ├── upload_to_d1.py                 # JSON → POST /api/update → D1
│   └── requirements.txt
├── data/
│   ├── czce/                           # CZCE 预处理后的 JSON（按年）
│   ├── dce/                            # DCE 预处理后的 JSON（按年）
│   └── shfe/                           # SHFE 预处理后的 JSON（按年）
├── file/                               # 官网下载的原始文件（不入 git）
│   ├── czce/                           # CZCE 官网 TXT 文件
│   ├── dce/                            # DCE 官网 XLSX 文件
│   └── shfe/                           # SHFE 官网 XLSX 文件
├── worker/
│   ├── wrangler.toml                   # Worker 名: maxpain-api, D1 绑定, API_KEY
│   ├── schema.sql                      # daily_data 表结构
│   ├── package.json                    # wrangler ^4.0.0
│   └── src/index.ts                    # Worker 代码（含 backtest_params 表 CRUD）
├── web/
│   ├── data/                           # 前端静态数据（由 build_web_data.py 生成）
│   │   ├── index.json                  # 品种列表 + 合约乘数
│   │   └── {symbol}.json               # 按品种拆分的全量数据
│   ├── index.html                      # 回测页面
│   └── train.html                      # 模拟训练
├── strategy_rules.md                   # 策略规则文档（可能滞后）
└── .gitignore
```

## 核心 API（Worker）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/data?symbol=TA` | 品种全部数据 |
| POST | `/api/update` | 上传数据（需 Authorization Bearer / X-GitHub-Token） |
| GET | `/api/symbols` | DB 已有品种列表 |
| DELETE | `/api/data?symbol=TA` | 删除品种数据（需鉴权） |
| GET | `/api/stats` | 各品种统计 |
| POST | `/api/params` | 保存参数配置（含 vol_filter_low/vol_filter_high） |
| GET | `/api/params?symbol=TA` | 读取参数配置 |

Worker 域名: `maxpain-api.136320309workersdev.workers.dev`
自定义域名: `api.starrysay.com`（DNS 解析到 Cloudflare，Worker 路由绑定）

## 数据流（主要路径）

```
交易所官网 TXT/XLSX → 转换脚本 → data/{czce,dce,shfe}/*.json（按年拆分）
                                        ↓
                               build_web_data.py（跨年合并去重，全量写入）
                                        ↓
                               web/data/{symbol}.json（按品种）
```

前端（`index.html` / `train.html`）直接从 `web/data/` 目录加载静态 JSON 文件，不走 Worker API。支持 GitHub Pages 或任何静态托管。

### 构建前端静态数据

```bash
python scripts/build_web_data.py
```

流程：
1. 读取 `data/` 下所有 `*.json`（不限目录层级、不限年份）
2. 按品种+日期去重合并，排序后写入 `web/data/{symbol}.json`
3. 同时生成 `web/data/index.json`（品种列表 + 合约乘数 + 名称映射）
4. 每个记录包含：`d, o, c, h, l, mp, co, po, bec, bep, vr, ivs, gex, oc_chain, expiry, dte, oi_total, oi_pcr, oi_max_strike, vol_call, vol_put, vol_total, fut_vol, fut_oi, fut_turnover, atm_iv`

> 前端所有计算（MP 趋势、止损、信号判定等）均在浏览器端完成，数据文件只提供原始指标。

### 次要路径（AKShare 拉取 CZCE）

如遇到 `fetch_data.py`，它仍通过 AKShare 拉取 CZCE 数据，直接计算后上传 D1 或导出 JSON，但 AKShare 不稳定，不推荐作为日常使用。

## 数据获取流程

### CZCE（郑商所）

1. 去官网下载 TXT 文件到 `file/czce/`
2. 运行转换：
```bash
python scripts/convert_czce.py --date 2026            # 全年
python scripts/convert_czce.py --date 202606          # 某月
python scripts/convert_czce.py --date 20260629        # 某天
```
3. 构建前端数据：
```bash
python scripts/build_web_data.py
```

### DCE（大商所）

1. 去官网下载 XLSX：
   - 期货: `allVarietyFtr2026.zip`
   - 期权: `allVarietyOpt2026.zip`
2. 解压到 `file/dce/`：
```bash
unzip -o allVarietyFtr2026.zip -d file/dce/allVarietyFtr2026
unzip -o allVarietyOpt2026.zip -d file/dce/allVarietyOpt2026
```
3. 运行转换：
```bash
python scripts/convert_dce_xlsx.py --date 2026        # 全年
python scripts/convert_dce_xlsx.py --date 20260629 --symbol M  # 单品种
```
4. 构建前端数据：
```bash
python scripts/build_web_data.py
```

### SHFE（上期所）

1. 去官网下载 XLSX 到 `file/shfe/`（期货 `fu2026/`，期权 `opt2026/`）
2. 运行转换：
```bash
python scripts/convert_shfe.py --date 2026            # 全年
python scripts/convert_shfe.py --date 20260629        # 某天
```
3. 构建前端数据：
```bash
python scripts/build_web_data.py
```

> ⚠️ SHFE 的 XLSX 缺少 Delta/隐含波动率字段，IV 偏斜(IVS)和 GEX 计算结果不可靠（GEX 为 0）。

## 部署

### Worker

Cloudflare Dashboard 已关联 GitHub 仓库，Worker 自动部署。配置在 Dashboard 中设置：
- **Root directory**: `worker`
- **Build command**: `npm install`（或空，视情况）
- **Deploy command**: `npx wrangler deploy`

推送到 main 分支后 Cloudflare 自动部署，无需本地手动执行 `wrangler deploy`，也**不要**使用 GitHub Actions 部署（Workers CI/CD 互斥）。

`wrangler.toml` 在 `worker/` 目录下，包含 D1 绑定和 API_KEY 环境变量。

### 前端的 API_BASE

`web/index.html` 的 `API_BASE` 指向 `https://api.starrysay.com/data/`（Workers 路由绑定）。静态部署时直接改为相对路径即可。

## GitHub Actions 工作流

`daily-update.yml` 支持两种模式：
- **data_source: local**（推荐）：上传 `data/` 目录下所有 JSON 到 D1
  ```bash
  python scripts/upload_to_d1.py --data-dir data --symbol TA --year 202606
  ```
- **data_source: akshare**（不稳定）：通过 AKShare 拉取 CZCE 数据后上传

### GitHub Secrets

| Secret | 值 |
|--------|---|
| `WORKER_URL` | `https://api.starrysay.com` |
| `D1_API_KEY` | 与 wrangler.toml 中 `API_KEY` 一致 |

## D1 数据库

表: `daily_data`，PRIMARY KEY (symbol, date)
字段: symbol, date, open, close, high, low, mp, co, po, bec, bep, vr, ivs, gex, oc_chain, expiry, dte, oi_total, oi_pcr, oi_max_strike, vol_call, vol_put, vol_total, fut_vol, fut_oi, fut_turnover, atm_iv

表: `backtest_params`，PRIMARY KEY (symbol)
字段: symbol, lookback, min_pct, max_pos, margin, be_th, entry_atr_mult, atr_period, atr_mult, lock_pct, vol_filter_low, vol_filter_high, capital, cap_limit, skip_count

远程操作：
```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "SELECT ..."
```

## 前端

两个 HTML 页面（`web/index.html`、`web/train.html`），内联 JS，无构建工具。

### 回测策略核心逻辑

- **方向判断**：21 日 MP 的 EMA 方向（非线性回归），带 threshold 缓冲
- **入场条件**：price 与 MP 同侧（做多：prevClose > prevMp；做空：prevClose < prevMp）
- **出场条件**：方向反转 / ATR 追踪止损 + 保本锁
- **停损计算**：
  - 入场第二天：重新算 ATR 追踪止损（prevClose ± ATR × ATR_MULT）
  - 第三天起：直接用前一天 stopCurve 算好的值
- **仓位**：权益 < 50万 → 满仓，>= 50万 → 权益 × 仓位%
- **交易成本**：固定 10 元/手 × 2（双边）
