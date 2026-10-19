#!/usr/bin/env bash
# director-guard.sh — 总监(orchestrator)不得下场执行守卫
#
# 规则：orchestrator 角色在同一事件（DIRECTOR_GUARD_EVENT_ID）内
# 连续执行第 2 条非只读命令时，打印 HARD WARNING 并以退出码 2 终止。
#
# 只读命令白名单（不计数）：
#   kubectl get/describe/logs/events/top/explain
#   curl (无 -X POST/PUT/PATCH/DELETE 且无 --data/-d)
#   gh pr view / gh issue view / gh run view / gitea pr view / git log / git diff / git show / git status
#   cat / ls / find / grep / rg / head / tail / wc / stat / echo / printf / jq / yq / helm template / helm lint
#
# 使用方式（在 dev.sh / 任意 orchestrator 脚本里 source 后调用）：
#   source scripts/agent/director-guard.sh
#   director_check "kubectl apply -f deploy/"     # 非只读 → 第1次：WARNING，第2次：HARD BLOCK
#   director_check "git log --oneline -5"         # 只读 → 不计数
#
# 环境变量：
#   DIRECTOR_GUARD_EVENT_ID   — 同一事件的唯一标识（默认用 $$ 进程 ID）；
#                               跨调用共享同一计数时，外层脚本须设置相同值。
#   DIRECTOR_GUARD_LOG        — 追加日志的文件路径（默认 /tmp/director-guard-$EVENT_ID.log）
#   DIRECTOR_GUARD_DISABLE=1  — 临时旁路（仅 owner 可设，测试用）
#
# 验收：vendor/test-director-guard.sh 模拟两次非只读调用，guard 必须触发。

set -uo pipefail

DIRECTOR_GUARD_EVENT_ID="${DIRECTOR_GUARD_EVENT_ID:-$$}"
DIRECTOR_GUARD_LOG="${DIRECTOR_GUARD_LOG:-/tmp/director-guard-${DIRECTOR_GUARD_EVENT_ID}.log}"

# 计数文件：每个 event 一个，记录本 event 内已执行的非只读命令数
_GUARD_COUNT_FILE="/tmp/director-guard-count-${DIRECTOR_GUARD_EVENT_ID}"

# ---- 只读判断 ---------------------------------------------------------------
# 返回 0 = 只读（不计数），1 = 非只读（计数）
_director_is_readonly() {
  local cmd="$1"
  # 去除前导空格
  cmd="${cmd#"${cmd%%[![:space:]]*}"}"

  case "$cmd" in
    # git 只读操作
    git\ log*|git\ diff*|git\ show*|git\ status*|git\ branch*|git\ tag*|git\ stash\ list*) return 0 ;;
    # kubectl 只读
    kubectl\ get\ *|kubectl\ describe\ *|kubectl\ logs*|kubectl\ events*|kubectl\ top\ *|kubectl\ explain\ *) return 0 ;;
    # gh / gitea 只读 PR/issue/run view
    gh\ pr\ view*|gh\ issue\ view*|gh\ run\ view*|gh\ api\ -X\ GET*|gh\ release\ view*) return 0 ;;
    # curl 无写操作（简单判断：无 -X POST/PUT/PATCH/DELETE 且无 --data/-d 标志）
    curl\ *)
      if echo "$cmd" | grep -qiE '\-X[[:space:]]*(POST|PUT|PATCH|DELETE)|--data[^a-z]|-d[[:space:]]|--request[[:space:]]*(POST|PUT|PATCH|DELETE)'; then
        return 1
      fi
      return 0
      ;;
    # 纯诊断类
    cat\ *|ls\ *|ls|find\ *|grep\ *|rg\ *|head\ *|tail\ *|wc\ *|stat\ *|echo\ *|printf\ *|jq\ *|yq\ *) return 0 ;;
    helm\ template*|helm\ lint*|helm\ list*|helm\ status*|helm\ get\ *) return 0 ;;
    # 以上未匹配 = 非只读
    *) return 1 ;;
  esac
}

# ---- 主入口 -----------------------------------------------------------------
# director_check <command-string>
# 在每条即将执行的命令前调用。只读命令直接 return 0；非只读命令计数并在第二次触发 HARD BLOCK。
director_check() {
  local cmd="${1:-}"

  # 旁路：owner 测试专用
  if [ "${DIRECTOR_GUARD_DISABLE:-0}" = "1" ]; then
    return 0
  fi

  if _director_is_readonly "$cmd"; then
    return 0  # 只读，放行
  fi

  # 读取或初始化计数
  local count=0
  if [ -f "$_GUARD_COUNT_FILE" ]; then
    count=$(cat "$_GUARD_COUNT_FILE" 2>/dev/null || echo 0)
  fi

  count=$((count + 1))
  printf '%s' "$count" > "$_GUARD_COUNT_FILE"

  local ts; ts=$(date '+%Y-%m-%d %H:%M:%S')
  local msg="[director-guard] event=${DIRECTOR_GUARD_EVENT_ID} count=${count} cmd=$(printf '%s' "$cmd" | head -c 200)"

  printf '%s\n' "$msg" >> "$DIRECTOR_GUARD_LOG"

  if [ "$count" -eq 1 ]; then
    printf '\n⚠️  [DIRECTOR-GUARD] WARNING: 总监正在下场执行第 1 条非只读命令。\n' >&2
    printf '    cmd: %s\n' "$cmd" >&2
    printf '    → 下一条非只读命令将被 HARD BLOCK。请先开 Kanban 卡并移交。\n\n' >&2
    return 0
  fi

  # count >= 2: HARD BLOCK
  printf '\n🚫  [DIRECTOR-GUARD] HARD BLOCK: 总监在同一事件内执行了第 %s 条非只读命令。\n' "$count" >&2
  printf '    cmd: %s\n' "$cmd" >&2
  printf '    event_id: %s\n' "$DIRECTOR_GUARD_EVENT_ID" >&2
  printf '    log: %s\n' "$DIRECTOR_GUARD_LOG" >&2
  printf '    规则：orchestrator 在同一事件内不得连续执行第 2 条以上的非只读命令。\n' >&2
  printf '    → 停止。开 Kanban 卡，移交给 worker；由 worker 执行剩余操作。\n\n' >&2
  exit 2
}
