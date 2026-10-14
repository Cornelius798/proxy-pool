#!/bin/bash
# 代理池定时维护：扫新 + 复验 + 踢死 + 有变化才推 GitHub
# 由 cron 每 6 小时调用一次
set -e
cd "$(dirname "$0")"

# 1. 先拉远程（防止手动改过）
git pull --rebase --quiet 2>/dev/null || true

# 2. 跑维护器
OUTPUT=$(python3 maintainer.py 2>&1)
echo "$OUTPUT" | tail -5

# 3. 有变化才提交推送
if echo "$OUTPUT" | grep -q "CHANGED=yes"; then
    SIZE=$(echo "$OUTPUT" | grep "POOL_SIZE=" | cut -d= -f2)
    git add pool.txt
    git commit -qm "pool update: ${SIZE} alive proxies ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
    git push --quiet
    echo "Pushed: ${SIZE} proxies"
else
    echo "No change, skip push"
fi
