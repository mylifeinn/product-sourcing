#!/usr/bin/env bash
# 每日自动选品: 遍历全部 seed_niches, 落库 + 同步 Notion
# 用法: ./run_daily_sourcing.sh [--limit N]
# cron 示例: 0 2 * * * /root/product-sourcing/run_daily_sourcing.sh >> /root/product-sourcing/logs/daily.log 2>&1
set -euo pipefail

cd /root/product-sourcing

LIMIT=8
if [[ "${1:-}" == "--limit" && -n "${2:-}" ]]; then
  LIMIT="$2"
fi

mkdir -p logs
STAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== [$STAMP] 开始每日选品 (limit=$LIMIT) ==="

# 全量抓取 + 评分 + 去重 + 落库 + Notion 同步
.venv/bin/python -m sourcing fetch-all --limit "$LIMIT"

# 可选: 健康检查(有发布产品后启用)
# .venv/bin/python -m sourcing healthcheck --days 30

STAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== [$STAMP] 完成 ==="
