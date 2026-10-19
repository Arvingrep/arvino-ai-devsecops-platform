#!/usr/bin/env bash
# scripts/test_director_guard.sh — director-guard.sh 验收测试
#
# 模拟总监在同一事件内执行两次非只读命令：
#   调用 1（git commit）→ 应打 WARNING，退出码 0
#   调用 2（kubectl apply）→ 应打 HARD BLOCK，退出码 2
# 中间穿插一条只读命令（git log）→ 不影响计数
#
# 还覆盖 kubectl 前置 flag 场景（QA 返修）：
#   kubectl --context gcp-gke -n arvino get pods       → 只读
#   kubectl --context mac-mini-orbstack -n argocd get applications → 只读
#   kubectl -n arvino logs deploy/arvino-api --tail=50  → 只读
#   kubectl --context gcp-gke -n arvino apply -f x.yaml → 非只读
#   kubectl --context gcp-gke -n arvino exec -it pod -- sh → 非只读
#
# 用法：bash scripts/test_director_guard.sh
# 全部断言通过则打印 "ALL PASS" 并退出 0；任一失败退出 1。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SCRIPT_DIR/director_guard.sh"

if [ ! -f "$GUARD" ]; then
  echo "❌ FAIL: guard script not found at $GUARD" >&2
  exit 1
fi

FAILS=0

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  if [ "$actual" -eq "$expected" ]; then
    printf '✅ PASS: %s (exit=%s)\n' "$label" "$actual"
  else
    printf '❌ FAIL: %s — expected exit %s, got %s\n' "$label" "$expected" "$actual" >&2
    FAILS=$((FAILS + 1))
  fi
}

# 使用唯一 event ID 隔离测试
EVENT_ID="test-$$"
export DIRECTOR_GUARD_EVENT_ID="$EVENT_ID"
COUNT_FILE="/tmp/director-guard-count-${EVENT_ID}"
LOG_FILE="/tmp/director-guard-${EVENT_ID}.log"
rm -f "$COUNT_FILE" "$LOG_FILE"

# Source guard 到当前进程（仅 steps 1-3；step 4 用子进程以捕获 exit 2）
source "$GUARD"

# --- 步骤 1：只读命令 git log — 不计数，exit 0 ---
director_check "git log --oneline -5"
assert_exit "readonly git log: exit 0" 0 $?

# --- 步骤 2：非只读 #1 (git commit) — WARNING + exit 0 ---
director_check "git commit -m 'wip'"
assert_exit "non-readonly #1 (git commit): WARNING, exit 0" 0 $?

# 断言计数文件 = 1
count=$(cat "$COUNT_FILE" 2>/dev/null || echo "")
if [ "$count" = "1" ]; then
  printf '✅ PASS: count file = 1 after first non-readonly\n'
else
  printf '❌ FAIL: count file expected 1, got "%s"\n' "$count" >&2
  FAILS=$((FAILS + 1))
fi

# --- 步骤 3：只读命令 kubectl logs — 不计数，exit 0 ---
director_check "kubectl logs pod/foo-123 -n production"
assert_exit "readonly kubectl logs: exit 0" 0 $?

count2=$(cat "$COUNT_FILE" 2>/dev/null || echo "")
if [ "$count2" = "1" ]; then
  printf '✅ PASS: count still 1 after readonly kubectl logs\n'
else
  printf '❌ FAIL: count should still be 1, got "%s"\n' "$count2" >&2
  FAILS=$((FAILS + 1))
fi

# --- 步骤 4：非只读 #2 (kubectl apply) — HARD BLOCK, exit 2 ---
# 在子进程中运行：guard 的 exit 2 只终止子进程，不影响当前测试进程
set +e
(
  source "$GUARD"
  director_check "kubectl apply -f deploy/helm/firebird-site/"
) 2>&1
step4_exit=$?
set -e
assert_exit "non-readonly #2 (kubectl apply): HARD BLOCK, exit 2" 2 "$step4_exit"

# --- 断言：日志文件写入了两条记录 ---
if [ -f "$LOG_FILE" ]; then
  log_lines=$(grep -c "director-guard" "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$log_lines" -ge 2 ]; then
    printf '✅ PASS: log file has %s entries (≥2)\n' "$log_lines"
  else
    printf '❌ FAIL: log file expected ≥2 entries, got %s\n' "$log_lines" >&2
    FAILS=$((FAILS + 1))
  fi
else
  printf '❌ FAIL: log file not found at %s\n' "$LOG_FILE" >&2
  FAILS=$((FAILS + 1))
fi

# 清理原始测试 state
rm -f "$COUNT_FILE" "$LOG_FILE"

# =============================================================================
# QA 返修：kubectl 前置 flag 场景（独立 event ID，避免干扰原有计数）
# =============================================================================
echo ""
echo "--- QA 返修：kubectl flag-before-subcommand 测试 ---"

PFLAG_EVENT="pflag-test-$$"
export DIRECTOR_GUARD_EVENT_ID="$PFLAG_EVENT"
COUNT_FILE2="/tmp/director-guard-count-${PFLAG_EVENT}"
LOG_FILE2="/tmp/director-guard-${PFLAG_EVENT}.log"
rm -f "$COUNT_FILE2" "$LOG_FILE2"

# Re-source guard 以重置内部变量
source "$GUARD"

# PF-1: kubectl --context gcp-gke -n arvino get pods → 只读
set +e
director_check "kubectl --context gcp-gke -n arvino get pods" 2>/dev/null
pf1_exit=$?
set -e
assert_exit "PF-1 kubectl --context gcp-gke -n arvino get pods → 只读" 0 "$pf1_exit"

count_pf=$(cat "$COUNT_FILE2" 2>/dev/null || echo "0")
if [ "$count_pf" = "0" ]; then
  printf '✅ PASS: count still 0 after PF-1 readonly\n'
else
  printf '❌ FAIL: count should be 0 after PF-1, got "%s"\n' "$count_pf" >&2
  FAILS=$((FAILS + 1))
fi

# PF-2: kubectl --context mac-mini-orbstack -n argocd get applications → 只读
set +e
director_check "kubectl --context mac-mini-orbstack -n argocd get applications" 2>/dev/null
pf2_exit=$?
set -e
assert_exit "PF-2 kubectl --context mac-mini-orbstack -n argocd get applications → 只读" 0 "$pf2_exit"

# PF-3: kubectl -n arvino logs deploy/arvino-api --tail=50 → 只读
set +e
director_check "kubectl -n arvino logs deploy/arvino-api --tail=50" 2>/dev/null
pf3_exit=$?
set -e
assert_exit "PF-3 kubectl -n arvino logs deploy/arvino-api --tail=50 → 只读" 0 "$pf3_exit"

# Verify count still 0 after 3 readonly calls
count_after_ro=$(cat "$COUNT_FILE2" 2>/dev/null || echo "0")
if [ "$count_after_ro" = "0" ]; then
  printf '✅ PASS: count still 0 after PF-1/2/3 readonly kubectl\n'
else
  printf '❌ FAIL: count should be 0 after readonly, got "%s"\n' "$count_after_ro" >&2
  FAILS=$((FAILS + 1))
fi

# PF-4: kubectl --context gcp-gke -n arvino apply -f x.yaml → 非只读 (WARNING, exit 0)
set +e
director_check "kubectl --context gcp-gke -n arvino apply -f x.yaml" 2>/dev/null
pf4_exit=$?
set -e
assert_exit "PF-4 kubectl --context gcp-gke -n arvino apply -f x.yaml → 非只读 WARNING" 0 "$pf4_exit"

count_pf4=$(cat "$COUNT_FILE2" 2>/dev/null || echo "0")
if [ "$count_pf4" = "1" ]; then
  printf '✅ PASS: count = 1 after PF-4 non-readonly apply\n'
else
  printf '❌ FAIL: count should be 1 after PF-4, got "%s"\n' "$count_pf4" >&2
  FAILS=$((FAILS + 1))
fi

# PF-5: kubectl --context gcp-gke -n arvino exec -it pod -- sh → 非只读 (HARD BLOCK, exit 2)
set +e
(
  source "$GUARD"
  director_check "kubectl --context gcp-gke -n arvino exec -it pod -- sh"
) 2>&1
pf5_exit=$?
set -e
assert_exit "PF-5 kubectl --context gcp-gke -n arvino exec -it pod -- sh → 非只读 HARD BLOCK" 2 "$pf5_exit"

# 清理
rm -f "$COUNT_FILE2" "$LOG_FILE2"

echo ""
if [ "$FAILS" -eq 0 ]; then
  echo "ALL PASS"
  exit 0
else
  echo "FAILED: $FAILS assertion(s)"
  exit 1
fi
