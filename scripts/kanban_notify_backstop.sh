#!/bin/bash
# 独立于gateway内置notifier的保险丝:每次运行扫描arvino board上所有blocked/done任务,
# 对比本地"已通知"记录,把漏掉的直接curl发到群。不依赖gateway内部状态,gateway挂了也不受影响。
set -euo pipefail
cd /Users/arvin/Documents/arvino/ai-devsecops-platform
STATE_FILE="$HOME/.hermes/kanban_notify_backstop.state"
touch "$STATE_FILE"
TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)
CHAT_ID="-1004348153434"

hermes kanban list --json 2>/dev/null | python3 -c "
import json,sys
tasks = json.load(sys.stdin)
for t in tasks:
    if t.get('status') in ('blocked','done','gave_up'):
        print(f\"{t['id']}|{t['status']}|{t.get('title','')}\")
" | while IFS='|' read -r tid status title; do
  key="${tid}:${status}"
  if ! grep -qF "$key" "$STATE_FILE"; then
    reason=$(hermes kanban show "$tid" 2>/dev/null | grep -A1 "blocked {" | tail -1 | head -c 300 || true)
    msg="【补发保险丝】${tid} 状态变为 ${status}: ${title}"
    [ -n "$reason" ] && msg="${msg}\n原因: ${reason}"
    curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d chat_id="$CHAT_ID" \
      --data-urlencode "text=${msg}" > /dev/null
    echo "$key" >> "$STATE_FILE"
    echo "notified: $key"
  fi
done
