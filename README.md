# MaxPain D1 — 数据流水线

GitHub Actions 定时从 AKShare 获取期货/期权数据，计算 Max Pain，写入 Cloudflare D1。

## 架构

```
AKShare / DCE官网 xlsx → Python 预处理 → Cloudflare Worker API → D1 数据库 → 前端查询
```

## 交易所 & 数据来源

| 交易所 | 品种 | 来源 | 更新方式 |
|--------|------|------|----------|
| CZCE 郑商所 | TA/MA/SA/SR/CF/RM/OI | AKShare ✅ | 定时任务自动 |
| SHFE 上期所 | CU/AL/ZN/RB/HC/AU/AG/RU | AKShare ✅ | 定时任务自动 |
| INE 能源中心 | SC | AKShare ✅ | 定时任务自动 |
| DCE 大商所 | M/C/L/V/PP/I/PG/Y... | DCE官网 xlsx | 手动下载 → 脚本上传 |

> DCE（大商所）官网加了 WAF，AKShare 接口挂了。走本地 xlsx 转 JSON 上传。

## 文件结构

```
├── .github/workflows/
│   ├── daily-update.yml    # 定时任务 (CZCE/SHFE/INE, 工作日每30分钟)
│   └── upload-dce.yml      # DCE 手动上传
├── scripts/
│   ├── fetch_data.py       # AKShare → Max Pain → JSON
│   ├── upload_to_d1.py     # JSON → Worker API → D1
│   ├── convert_dce_xlsx.py # DCE xlsx → data/dce/*.json
│   ├── upload_dce_local.py # data/dce/*.json → D1
│   └── requirements.txt
├── data/
│   └── dce/                # DCE 预处理后的 JSON (按年)
│       ├── 2025.json
│       └── 2026.json
├── file/dce/               # DCE 官网下载的原始 xlsx (不入 git)
│   ├── allVarietyFtr2026/  # 2026 期货
│   └── allVarietyOpt2026/  # 2026 期权
├── worker/
│   ├── wrangler.toml
│   ├── schema.sql
│   └── src/index.ts
└── web/
    ├── index.html          # 回测页面
    └── train.html          # 模拟训练
```

## 日常操作

### 定时任务（自动）

GitHub Actions 工作日在 16:00-21:00 每 30 分钟触发一次：

1. 查 GitHub API — 今日已有成功运行 → 跳过
2. 查 DB 品种列表，跳过 DCE
3. AKShare 拉数据 → `fetch_data.py --recent 5` → `upload_to_d1.py` → D1

### DCE 数据更新（手动）

每 1-2 周操作一次：

```bash
# 1. 去 DCE 官网下载
#    http://www.dce.com.cn → 历史数据
#    下载 allVarietyFtr2026.zip (期货)
#    下载 allVarietyOpt2026.zip (期权)

# 2. 解压到 file/dce/
unzip -o allVarietyFtr2026.zip -d file/dce/allVarietyFtr2026
unzip -o allVarietyOpt2026.zip -d file/dce/allVarietyOpt2026

# 3. 转为 JSON (只转 2026)
python scripts/convert_dce_xlsx.py --year 2026

# 4. 上传到 D1
D1_API_KEY="your-key" python scripts/upload_dce_local.py --year 2026

# 5. 提交 JSON
git add data/dce/2026.json && git commit -m "DCE 2026 数据更新" && git push
```

## 首次部署

### 1. Cloudflare D1 + Worker

```bash
cd worker
npm install
npx wrangler d1 create maxpain-db
# ↑ 输出 database_id，填入 wrangler.toml
npx wrangler d1 execute maxpain-db --file schema.sql
# 设置 wrangler.toml 中的 API_KEY
npx wrangler deploy
```

### 2. GitHub Secrets

| Secret | 值 |
|--------|---|
| `WORKER_URL` | `https://maxpain-api.xxx.workers.dev` |
| `D1_API_KEY` | 与 wrangler.toml 中 `API_KEY` 一致 |
| `GH_UPLOAD_TOKEN` | GitHub PAT（可选） |

### 3. 本地测试

```bash
# AKShare 拉取
python scripts/fetch_data.py --symbol TA

# 上传
python scripts/upload_to_d1.py --input data.json \
  --worker-url http://localhost:8787 --api-key test-key

# DCE 上传
D1_API_KEY="your-key" python scripts/upload_dce_local.py --year 2026
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/data?symbol=TA` | 获取品种全部数据 |
| POST | `/api/update` | 上传数据 (需 Bearer Token) |
| GET | `/api/symbols` | 获取数据库已有品种列表 |
| GET | `/api/stats` | 查看各品种数据统计 |

## 已知问题

- **DCE API 挂了**：大商所官网加了 WAF 反爬，`get_dce_daily` / `option_hist_dce` 返回 412。等 AKShare 适配或换数据源。
- 恢复后：删除 `scripts/fetch_data.py` 中 `_BROKEN_EXCHANGES` 的 `'dce'` 即可。
