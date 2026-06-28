# MaxPain D1 项目

期权 Max Pain 数据流水线 → Cloudflare D1 → 前端回测/训练页面。

## 项目结构

```
├── .github/workflows/daily-update.yml  # 全量数据更新 workflow（手动触发）
├── scripts/
│   ├── fetch_data.py                   # AKShare 拉期货/期权 → 计算 MP/BE/VR/IVS → JSON
│   ├── upload_to_d1.py                 # JSON → POST /api/update → D1
│   ├── convert_dce_xlsx.py             # DCE 官网 XLSX → data/dce/*.json
│   └── requirements.txt                # akshare, pandas, numpy
├── data/
│   └── dce/                            # DCE 预处理后的 JSON
│       └── 2026.json
├── file/                               # 原始 XLS/XLSX（不入 git）
│   ├── dce/                            # DCE 官网下载
│   └── shfe/                           # SHFE 官网下载
├── worker/
│   ├── wrangler.toml                   # Worker 名: maxpain-api, D1 绑定, API_KEY
│   ├── schema.sql                      # daily_data 表结构
│   ├── package.json                    # wrangler ^4.0.0
│   └── src/index.ts                    # Worker 代码
├── web/
│   ├── index.html                      # 回测页面（API_BASE: api.starrysay.com）
│   └── train.html                      # 模拟训练
├── strategy_rules.md                   # 策略规则文档
└── .gitignore                          # node_modules, __pycache__, file, .wrangler/
```

## 核心 API（Worker）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/data?symbol=TA` | 品种全部数据 |
| POST | `/api/update` | 上传数据（需 Authorization Bearer / X-GitHub-Token） |
| GET | `/api/symbols` | DB 已有品种列表 |
| DELETE | `/api/data?symbol=TA` | 删除品种数据（需鉴权） |
| GET | `/api/stats` | 各品种统计 |

Worker 域名: `maxpain-api.136320309workersdev.workers.dev`
自定义域名: `api.starrysay.com`（DNS 解析到 Cloudflare，Worker 路由绑定）

## 数据流

```
CZCE: AKShare → fetch_data.py → JSON → upload_to_d1.py → Worker API → D1
DCE:  官网 XLSX → convert_dce_xlsx.py → data/dce/*.json → upload_to_d1.py → Worker API → D1
SHFE: 官网 XLSX → convert_shfe.py → data/shfe/*.json → upload_to_d1.py → Worker API → D1
```

## 交易所 AKShare 状态

| 交易所 | AKShare 状态 | 备选方案 |
|--------|-------------|---------|
| CZCE 郑商所 | ✅ 可用 | - |
| DCE 大商所 | ❌ API 挂了 | 官网下载 XLSX → convert_dce_xlsx.py |
| SHFE 上期所 | ❌ API 挂了 | 官网下载 XLSX → convert_shfe.py（数据质量差，缺 IV/Delta） |
| INE 能源中心 | ❌ API 挂了 | 同上 |

## 部署

Worker 通过本地 wrangler 部署（不是 GitHub Actions）：

```bash
cd worker
npx wrangler login      # 首次需要，认证存在 .wrangler/ 中
npx wrangler deploy
```

⚠️ `.wrangler/` 存 wrangler 登录态，已在 .gitignore 中，不能删。

## 日常操作

### AKShare 全量拉取（CZCE 品种）
通过 GitHub Actions `Daily Data Update` workflow 手动触发：
- mode: full
- data_source: akshare
- symbol: 空=取 DB 已有品种，或指定如 `TA,MA,SA`
- year: `2026` / `202606` / `20260626` / `0`(不限)

### DCE/SHFE 数据上传
通过 GitHub Actions `Daily Data Update` workflow 手动触发：
- mode: full
- data_source: local
- symbol: 空=全部，或指定如 `M,C,I`
- 自动读取 `data/dce/`、`data/shfe/` 下所有 JSON

### 本地操作
```bash
# AKShare 拉取
cd scripts
python fetch_data.py --symbol TA --year 202606

# 上传
python upload_to_d1.py --input data.json -s TA \
  --worker-url https://api.starrysay.com --api-key <key>

# DCE 转换
python convert_dce_xlsx.py --year 2026
```

## 前端

两个 HTML 页面（`web/index.html`、`web/train.html`），内联 JS，无构建工具。

API_BASE: `https://api.starrysay.com`

`loadAllData()` 逻辑：
1. `GET /api/symbols` 获取 DB 已有品种
2. 失败则回退到 `SYM_CFG` 全部 48 个品种（会导致下拉框很多但加载不出来）
3. 逐个 `GET /api/data?symbol=X` 加载数据到 `ALL_DATA`

SYM_CFG 定义了两个页面各自独立的品种配置（约 48 个），覆盖 CZCE/DCE/SHFE/INE。

## D1 数据库

表: `daily_data`，PRIMARY KEY (symbol, date)
字段: symbol, date, open, close, high, low, mp, co, po, bec, bep, vr, ivs, expiry, dte

远程操作需要 `--remote`：
```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "SELECT ..."
```
