# Product Sourcing Pipeline

自动化选品管线：从利基关键词 → 真实竞品/批发价/趋势/合规 → 9 道硬门槛评分 → 仪表盘审核 → Shopify 上架。

---

## 🎯 核心能力

| 阶段 | 数据源 | 真实度 |
|------|--------|--------|
| 竞品挖掘 | Amazon 公开搜索 | ✅ 真实（标题、价格、ASIN、链接） |
| 趋势验证 | Google Trends (pytrends) | ✅ 真实（受频率限制） |
| 痛点词挖掘 | Google Suggest / PAA | ✅ 真实 |
| 销量估算 / 历史价格 / BSR | **Keepa API**（需 Key） | ✅ 真实（配 Key 后） |
| 批发价 / MOQ / 供应商 | AliExpress / Alibaba 公开搜索 | ⚠️ 受反爬限制，建议配代理 |
| 合规预检 | Google Patents / USPTO / TMview | ✅ 真实（需 API Key） |

---

## 🚀 快速开始

### 1. 环境准备
```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# 克隆并安装依赖
git clone git@github.com:mylifeinn/product-sourcing.git
cd product-sourcing
uv sync

# 安装 Playwright 浏览器
uv run playwright install chromium
```

### 2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 填入：
# KEEPA_API_KEY=xxx          # 免费注册 keepa.com
# GOOGLE_PATENTS_API_KEY=xxx # 可选
# USPTO_API_KEY=xxx          # 可选
# SHOPIFY_SHOP=xxx.myshopify.com
# SHOPIFY_TOKEN=shpat_xxx
# NOTION_TOKEN=secret_xxx
# NOTION_DB_ID=xxx
```

### 3. 初始化数据库
```bash
uv run python -m sourcing init
```

### 4. 跑选品管线
```bash
# 真实数据源（推荐）
uv run python -m sourcing fetch "portable neck massager" --source real --limit 10

# 或用 Mock 测试
uv run python -m sourcing fetch "portable neck massager" --source mock --limit 10

# 导出 CSV 审核
uv run python -m sourcing export --status pending --format csv --output candidates.csv
```

### 5. 启动 Web 仪表盘
```bash
uv run python -m sourcing.web.app
# 访问 http://localhost:8085
```

---

## 📊 9 道硬门槛（全部通过才得 100 分）

| # | 门槛 | 阈值 |
|---|------|------|
| 1 | 痛点关键词 | ≥3 个长尾词（月搜≥500、KD≤30） |
| 2 | 趋势 | Google Trends 90d YoY ≥20% |
| 3 | 毛利 | ≥45%（含运费、平台费） |
| 4 | 轻便 | ≤500g、≤30×20×10cm、可走 ePacket/4PX |
| 5 | 质量 | 供应商评分≥4.7、退款率≤3%、有实拍 |
| 6 | 独特性 | Amazon/TEMU/SHEIN 前 3 页无同款 |
| 7 | 长青/季节 | 长青波动≤30% 或 季节旺季≥90天 |
| 8 | 市场验证 | 竞品 90 天销量≥50 或 评论≥200 |
| 9 | 客户价值 | AOV≥$60、复购周期≤90天、LTV≥3单 |

---

## 🐳 Docker 部署

```bash
# 构建镜像
docker build -t product-sourcing .

# 运行（挂载 .env 和 data）
docker run -d \
  --name product-sourcing \
  -p 8085:8085 \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  product-sourcing

# 或用 docker-compose
docker-compose up -d
```

---

## ⏰ 定时任务（cron）

```bash
# 每天 02:00 UTC 跑种子利基
0 2 * * * cd /app && uv run python -m sourcing fetch "portable neck massager" --source real --limit 10

# 每天 04:00 UTC 健康检查
0 4 * * * cd /app && uv run python -m sourcing healthcheck --days 30
```

---

## 📁 目录结构

```
product-sourcing/
├── src/sourcing/
│   ├── __main__.py          # CLI 入口
│   ├── web/app.py           # FastAPI 仪表盘
│   ├── config.py            # 配置加载
│   ├── models.py            # 数据模型
│   ├── database.py          # SQLite 持久化
│   ├── pipeline/
│   │   ├── fetch.py         # 真实数据源聚合
│   │   ├── enrich.py        # 价格/毛利计算
│   │   ├── score.py         # 9 门槛评分
│   │   ├── dedup.py         # 去重
│   │   ├── wholesale_fetch.py # AliExpress/Alibaba 爬虫
│   │   ├── keepa_api.py     # Keepa 客户端
│   │   └── public_fetch.py  # Amazon/Google Trends 爬虫
│   ├── compliance/          # 专利/商标预检
│   ├── seo/                 # Schema.org 模板
│   ├── notion/              # Notion 同步
│   ├── shopify/             # Shopify API
│   └── health/              # 健康检查
├── tests/
├── data/                    # SQLite DB（运行时生成）
├── templates/               # Jinja2 模板
├── .github/workflows/ci.yml # CI/CD
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── config.yaml
├── .env.example
└── README.md
```

---

## 🔐 关键配置说明

| 变量 | 必填 | 来源 |
|------|------|------|
| `KEEPA_API_KEY` | 强烈建议 | https://keepa.com/#!api（免费 100 token/天） |
| `GOOGLE_PATENTS_API_KEY` | 可选 | Google Cloud Console |
| `USPTO_API_KEY` | 可选 | USPTO Developer Portal |
| `SHOPIFY_SHOP` / `SHOPIFY_TOKEN` | 推送上架时需 | Shopify Admin → Apps |
| `NOTION_TOKEN` / `NOTION_DB_ID` | 仪表盘审核时需 | Notion Integration |

---

## 📝 版本历史

- **v0.1.0** (2026-08-16) — 首个可运行版本：真实 Amazon 爬取 + Google Trends + 9 门槛评分 + Web 仪表盘

---

## ⚠️ 已知限制

1. **AliExpress/Alibaba 爬虫** 受反爬限制，生产环境建议配住宅代理
2. **Google Trends** 有频率限制，已加本地缓存，大规模跑需轮换 IP
3. **Keepa 免费版** 每天 100 token，批量查询需控制频率
3. **竞品评论数** 当前 Amazon 列表页解析可能为 0，需进一步优化选择器

---

## 📄 License

Private — Internal Use Only