# MaxPain D1 — 数据流水线

GitHub Actions 手动触发从 AKShare 获取期货/期权数据，计算 Max Pain / GEX，写入 Cloudflare D1 或导出为前端静态 JSON。

## 架构

```
                   ┌→ D1 数据库 → Worker API → (历史 API 路径)
AKShare / 官网 XLSX ┤
                   └→ data/*.json → build_web_data.py → web/data/*.json → 前端直接加载（当前路径）
```

## 交易所 & 数据来源

| 交易所 | 品种 | 来源 | 更新方式 |
|--------|------|------|----------|
| CZCE 郑商所 | TA/MA/SA/SR/CF/RM/OI 等 | AKShare ✅ | Workflow 手动触发 |
| DCE 大商所 | M/C/I/P/Y/EG/EB/V 等 | 官网 XLSX | 下载 → 本地转换 → Workflow 上传 |
| SHFE 上期所 | CU/AL/ZN/RB/HC/AU/AG/RU 等 | 官网 XLSX | 下载 → 本地转换 → Workflow 上传 (数据质量差) |
| INE 能源中心 | SC/NR | 官网 XLSX | 同 SHFE |

> DCE 和 SHFE/INE 的 AKShare API 均已挂（DCE WAF 反爬、SHFE 网站变动），走官网下载 XLSX → 本地脚本转换 → Workflow 上传。

## 文件结构

```
├── .github/workflows/
│   └── daily-update.yml    # 全量数据更新（手动触发，AKShare 或 local）
├── scripts/
│   ├── fetch_data.py       # AKShare → Max Pain → JSON
│   ├── upload_to_d1.py     # JSON → Worker API → D1
│   ├── convert_dce_xlsx.py # DCE XLSX → data/dce/*.json
│   ├── convert_shfe.py     # SHFE XLSX → data/shfe/*.json
│   ├── convert_czce.py     # CZCE TXT → data/czce/*.json
│   ├── build_web_data.py   # data/*/*.json → web/data/*.json（按品种拆分 + 计算衍生指标）
│   └── requirements.txt
├── data/
│   ├── czce/               # CZCE 预处理后的 JSON (按年)
│   ├── dce/                # DCE 预处理后的 JSON (按年)
│   └── shfe/               # SHFE 预处理后的 JSON (按年)
├── file/                   # 官网下载的原始 XLSX/TXT (不入 git)
│   ├── czce/               # CZCE 官网 TXT
│   ├── dce/                # DCE 官网 XLSX
│   └── shfe/               # SHFE 官网 XLSX
├── worker/                 # Cloudflare Worker (D1 API，当前不活跃使用)
├── web/
│   ├── data/               # 前端静态数据（按品种拆分，由 build_web_data.py 生成）
│   │   ├── index.json      # 品种列表
│   │   ├── TA.json         # PTA
│   │   ├── MA.json         # 甲醇
│   │   └── ...
│   ├── index.html          # 回测页面（从 web/data/ 加载静态 JSON）
│   └── train.html          # 模拟训练（从 web/data/ 加载静态 JSON）
└── CLAUDE.md               # 详细项目文档
```

## 数据流

```
交易所官网 TXT/XLSX → 转换脚本 → data/{czce,dce,shfe}/*.json（按年拆分）
                                       ↓
                              build_web_data.py（跨年合并去重）
                                       ↓
                              web/data/{symbol}.json（按品种，全量数据）
```

前端（`index.html` / `train.html`）直接从 `web/data/` 目录加载 JSON 文件，不走 Worker API。适合 GitHub Pages 或任何静态托管。

### 构建前端静态数据

转换脚本跑完后，执行一次构建即可更新前端数据：

```bash
python scripts/build_web_data.py
```

流程：
1. 读取 `data/` 下所有 `*.json`（不限目录层级、不限年份）
2. 按品种+日期去重合并，排序后写入 `web/data/{symbol}.json`
3. 同时生成 `web/data/index.json`（品种列表 + 合约乘数）
4. 每个记录包含：`d, o, c, h, l, mp, co, po, bec, bep, vr, ivs, gex`

> 前端所有计算（MP 趋势、OI 偏度、信号判定等）均在浏览器端完成，数据文件只提供原始指标。

## 日常操作

### CZCE 数据更新（AKShare — Workflow 手动触发）

GitHub Actions → Daily Data Update → Run workflow：
- mode: `full`
- data_source: `akshare`
- symbol: 空=取 DB 已有品种，或指定如 `TA,MA,SA`
- year: `2026`(全年) / `202606`(6月) / `20260626`(单日) / `0`(不限)

也可本地转换 CZCE 官网 TXT 文件（需提前下载到 `file/czce/`）：

```bash
python scripts/convert_czce.py --date 2026            # 全年
python scripts/convert_czce.py --date 202606          # 某月
python scripts/convert_czce.py --date 20260629        # 某天
python scripts/convert_czce.py --date 20260629 --symbol TA  # 单品种
```

转换后执行以下命令更新前端数据：

```bash
python scripts/build_web_data.py                      # 刷新 web/data/
```

### DCE 数据更新（手动下载 + Workflow 上传）

每 1-2 周操作一次：

```bash
# 1. 去 DCE 官网下载
#    http://www.dce.com.cn → 历史数据
#    下载 allVarietyFtr2026.zip (期货)
#    下载 allVarietyOpt2026.zip (期权)

# 2. 解压到 file/dce/
unzip -o allVarietyFtr2026.zip -d file/dce/allVarietyFtr2026
unzip -o allVarietyOpt2026.zip -d file/dce/allVarietyOpt2026

# 3. 转为 JSON
python scripts/convert_dce_xlsx.py --date 2026              # 全年
python scripts/convert_dce_xlsx.py --date 202606            # 某月
python scripts/convert_dce_xlsx.py --date 20260629          # 某天
python scripts/convert_dce_xlsx.py --date 20260629 --symbol M  # 单品种
```

转换后执行以下命令更新前端数据：

```bash
python scripts/build_web_data.py                      # 刷新 web/data/
```

也可通过 GitHub Actions → Daily Data Update → Run workflow 上传到 D1：
- mode: `full`
- data_source: `local`
- symbol: 空=全部，或指定如 `M,C,I`

### SHFE 数据更新（同上）

```bash
# 1. 去 SHFE 官网下载
#    https://www.shfe.com.cn → 数据 → 历史数据
#    期货放 file/shfe/fu2026/，期权放 file/shfe/opt2026/

# 2. 转为 JSON
python scripts/convert_shfe.py --date 2026          # 全年
python scripts/convert_shfe.py --date 202606        # 某月
python scripts/convert_shfe.py --date 20260629      # 某天
python scripts/convert_shfe.py --date 202606 --upload  # 转换后直接上传 D1

# 3. 刷新前端数据
python scripts/build_web_data.py
```

> ⚠️ SHFE 的 XLSX 缺少 Delta/隐含波动率字段，IV 偏斜(IVS)指标不可用。

### 本地操作

```bash
# AKShare 拉取
python scripts/fetch_data.py --symbol TA --year 202606

# 上传
python scripts/upload_to_d1.py --input data.json -s TA \
  --worker-url https://api.starrysay.com --api-key <key>

# DCE 目录上传
python scripts/upload_to_d1.py --data-dir data/dce -s M \
  --worker-url https://api.starrysay.com --api-key <key>
```

## 部署

### Worker

```bash
cd worker
npm install
npx wrangler login         # 首次需要，认证存在 .wrangler/
npx wrangler deploy
```

Worker 域名: `maxpain-api.136320309workersdev.workers.dev`
自定义域名: `api.starrysay.com`

### D1 数据库操作

查询数据：

```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "SELECT * FROM daily_data WHERE symbol = 'TA' LIMIT 5"
```

新增字段（表结构变更）：

```bash
cd worker
npx wrangler d1 execute maxpain-db --remote --command "ALTER TABLE backtest_params ADD COLUMN vol_filter_low REAL DEFAULT 0; ALTER TABLE backtest_params ADD COLUMN vol_filter_high REAL DEFAULT 0;"
```

> `schema.sql` 是表结构参考，已有表需用 ALTER TABLE 变更，`CREATE TABLE IF NOT EXISTS` 仅对新表生效。

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | TEXT | 品种代码 |
| date | TEXT | 日期 YYYY-MM-DD |
| open/close/high/low | REAL | OHLC |
| mp | INTEGER | Max Pain |
| co | REAL | Call OI |
| po | REAL | Put OI |
| bec/bep | REAL | Call/Put 盈亏平衡点 |
| vr | REAL | Put/Call 成交量比 |
| ivs | REAL | IV 偏斜 |

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
| GET | `/api/stats` | 查看各品种数据统计 |
