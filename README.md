# MaxPain D1 — 数据流水线

GitHub Actions 定时从 AKShare 获取郑商所期货/期权数据，计算 Max Pain，写入 Cloudflare D1。

## 架构

```
AKShare → Python 预处理 → Cloudflare Worker API → D1 数据库 → 前端查询
```

## 文件结构

```
├── .github/workflows/daily-update.yml   # 定时任务 (工作日 16:00)
├── scripts/
│   ├── fetch_data.py      # AKShare → Max Pain 计算 → JSON
│   ├── upload_to_d1.py    # JSON → Worker API → D1
│   └── requirements.txt
├── worker/
│   ├── wrangler.toml      # Cloudflare Worker 配置
│   ├── package.json
│   ├── schema.sql         # D1 建表
│   └── src/
│       └── index.ts       # API 路由
```

## 部署

### 1. 初始化 Cloudflare D1 + Worker

```bash
cd worker

# 安装依赖
npm install

# 创建 D1 数据库
npx wrangler d1 create maxpain-db

# ↑ 输出 database_id，填入 wrangler.toml

# 创建表
npx wrangler d1 execute maxpain-db --file schema.sql

# 生成 API 密钥
# 并设置到 wrangler.toml 的 vars.API_KEY

# 部署 Worker
npx wrangler deploy

# 记录部署后输出的 Worker URL
```

### 2. 设置 GitHub Secrets

| Secret | 值 |
|--------|---|
| `WORKER_URL` | `https://maxpain-api.xxx.workers.dev` |
| `D1_API_KEY` | 与 wrangler.toml 中 API_KEY 一致 |

### 3. 本地测试

```bash
# 首次全量拉取（可能较慢，AKShare 有频率限制）
python scripts/fetch_data.py

# 或限定期望范围
python scripts/fetch_data.py --symbol TA

# 上传到 D1
python scripts/upload_to_d1.py --input data.json \
  --worker-url http://localhost:8787 \
  --api-key test-key

# 验证
curl http://localhost:8787/api/data?symbol=TA | head
```

## 首次数据加载（推荐方案）

AKShare 逐日请求较慢。首次可用现有 JSON 数据初始化 D1：

```bash
# 从现有项目中复制数据
cp ../MaxPain/web/data/*.json ./
```

或用 `upload_to_d1.py` 分批上传。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/data?symbol=TA` | 获取品种全部数据 |
| POST | `/api/update` | 上传数据 (需 Bearer Token) |
| GET | `/api/symbols` | 获取数据库已有品种列表 |
| GET | `/api/stats` | 查看各品种数据统计 |

## 前端集成

在 `web/index.html` 中将：
```html
<script src="data.js"></script>
```
改为：
```html
<script>
const resp = await fetch('https://maxpain-api.xxx.workers.dev/api/data?symbol=TA');
const { data } = await resp.json();
const ALL_DATA = { TA: data, MA: [], SA: [] };
</script>
```
