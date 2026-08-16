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

# 全量抓取 + 评分 + 去重 + 落库(带 CPU 限流)
.venv/bin/python -m sourcing fetch-all --limit "$LIMIT" --sleep 15

# Notion 清空 + ASIN 去重 + 全量同步(序号按 score 降序)
echo "[$STAMP] 同步 Notion (清空+去重)..."
.venv/bin/python -c "
from sourcing.notion.sync import NotionSync
n = NotionSync()
result = n.sync_all_deduped(clear_first=True)
print(f'同步结果: {result}')
" 2>&1 | grep -v WARNING

# 可选: 健康检查(有发布产品后启用)
# .venv/bin/python -m sourcing healthcheck --days 30

STAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== [$STAMP] 完成 ==="
