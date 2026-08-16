#!/usr/bin/env bash
# 检查爬虫批量任务是否结束, 结束后执行 Notion 清空+去重+全量同步
# 用法: cron 每 30 分钟跑一次 (幂等: 爬虫没结束就退出, 结束后只同步一次)
set -uo pipefail
cd /root/product-sourcing

# 爬虫还在跑就不动 (proc_0c7927239b50 是当前批量任务)
if pgrep -f "sourcing fetch-all" > /dev/null 2>&1; then
  exit 0
fi

# 锁文件防重复同步
LOCK=/tmp/notion-sync.lock
if [ -f "$LOCK" ]; then
  exit 0
fi
touch "$LOCK"

echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] 爬虫已结束, 同步 Notion ==="
.venv/bin/python -c "
from sourcing.notion.sync import NotionSync
n = NotionSync()
result = n.sync_all_deduped(clear_first=True)
print(f'同步结果: {result}')
" 2>&1 | grep -v WARNING

rm -f "$LOCK"
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ==="
