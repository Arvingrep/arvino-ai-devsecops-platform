#!/usr/bin/env bash
# Offline contract test for agent-harness.sh lease handling.
#
# Uses stub curl/claude binaries: no network, no real repo, no API. Verifies:
#   1. claim happens before claude runs and before /complete;
#   2. /complete carries the lease token;
#   3. a rejected claim skips execution entirely (no claude, no /complete);
#   4. a started execution does not auto-release the lease on shutdown.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${ROOT}/scripts/agent-harness.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()   { echo "PASS: $*"; PASS=$((PASS + 1)); }
bad()  { echo "FAIL: $*"; FAIL=$((FAIL + 1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

mkdir -p "$TMP/bin" "$TMP/work"

cat >"$TMP/bin/curl" <<'STUB'
#!/usr/bin/env bash
url=""; method="GET"; body=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -d) body="$2"; shift 2 ;;
    -H) shift 2 ;;
    -sS|--fail-with-body) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s %s %s\n' "$method" "$url" "$body" >>"$CALL_LOG"
case "$url" in
  */work*)
    if [[ ! -f "$WORK_COUNTER" ]]; then
      echo 1 >"$WORK_COUNTER"
      cat "$WORK_RESPONSE"
    else
      echo '[]'
    fi ;;
  */claim)
    if [[ -n "${CLAIM_FAIL:-}" ]]; then
      echo '{"detail":"step already leased"}'
      exit 22
    fi
    echo '{"run_id":"r1","step_key":"implement","attempt":1,"executor_principal":"agent:test","lease_expires_at":"2026-10-21T01:00:00Z","repo_key":"acme/app","role_verified":true,"lease_token":"tok-123"}' ;;
  */renew)
    echo '{"run_id":"r1","step_key":"implement","attempt":1,"executor_principal":"agent:test","lease_expires_at":"2026-10-21T01:00:00Z","repo_key":"acme/app","role_verified":true,"lease_token":null}' ;;
  */complete) echo '{"result":"applied"}' ;;
  */release) echo '{"run_id":"r1","step_key":"implement","attempt":1,"executor_principal":"agent:test","lease_expires_at":null,"repo_key":null,"role_verified":true,"lease_token":null}' ;;
  */runs/r1) echo '{"project_id":"p1","issue_id":"i1"}' ;;
  */issues/i1) echo '{"title":"t","body":"b"}' ;;
  */projects/p1) echo '{"repo_full_name":""}' ;;
  *) echo '{}' ;;
esac
STUB

cat >"$TMP/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo "CLAUDE $*" >>"$CALL_LOG"
if [[ -n "${CLAUDE_DELAY:-}" && "${CLAUDE_DELAY}" != "0" ]]; then
  sleep "$CLAUDE_DELAY"
fi
echo '{"result":"ok"}'
exit 0
STUB

chmod +x "$TMP/bin/curl" "$TMP/bin/claude"
cat >"$TMP/work.json" <<'JSON'
[{"run_id":"r1","step_key":"implement","role":"developer","attempt":1,"actor":"agent:developer","branch_prefix":"arvino/r1/","idempotency_key":"r1:implement:1"}]
JSON

run_harness() {  # $1 = log file, $2 = claim fail flag, $3 = renew interval, $4 = claude delay
  local log="$1" claim_fail="$2" renew="${3:-60}" delay="${4:-0}"
  : >"$TMP/calls.log"
  rm -f "$TMP/work.counter"
  env -i \
    PATH="$TMP/bin:$PATH" \
    HOME="$HOME" \
    ARVINO_API_URL="http://stub.invalid" \
    ARVINO_AGENT_TOKEN="test-token" \
    CLAUDE_CODE_OAUTH_TOKEN="test-oauth" \
    ARVINO_WORK_DIR="$TMP/work" \
    ARVINO_POLL_INTERVAL="1" \
    ARVINO_LEASE_RENEW_INTERVAL="$renew" \
    CLAUDE_TIMEOUT="30" \
    CLAUDE_DELAY="$delay" \
    CALL_LOG="$TMP/calls.log" \
    WORK_RESPONSE="$TMP/work.json" \
    WORK_COUNTER="$TMP/work.counter" \
    CLAIM_FAIL="$claim_fail" \
    bash "$HARNESS" >"$log" 2>&1 &
  HARNESS_PID=$!
}

wait_for() {  # $1 = log file, $2 = pattern, $3 = attempts
  local i
  for ((i = 0; i < ${3:-50}; i++)); do
    grep -q "$2" "$1" && return 0
    sleep 0.2
  done
  return 1
}

# ── Scenario 1: claim then execute then complete with token ─────────────────
run_harness "$TMP/run1.log" ""
wait_for "$TMP/run1.log" "Server response: result=applied" 75 || true
kill "$HARNESS_PID" 2>/dev/null || true
wait "$HARNESS_PID" 2>/dev/null || true

claim_line=$(grep -n "POST .*claim" "$TMP/calls.log" | head -1 | cut -d: -f1 || true)
complete_line=$(grep -n "POST .*complete" "$TMP/calls.log" | head -1 | cut -d: -f1 || true)
claude_line=$(grep -n "^CLAUDE " "$TMP/calls.log" | head -1 | cut -d: -f1 || true)

check "scenario1 claim is called" "[[ -n '$claim_line' ]]"
check "scenario1 claim precedes claude" "[[ -n '$claim_line' && -n '$claude_line' && '$claim_line' -lt '$claude_line' ]]"
check "scenario1 claude precedes complete" "[[ -n '$claude_line' && -n '$complete_line' && '$claude_line' -lt '$complete_line' ]]"
check "scenario1 complete is called" "[[ -n '$complete_line' ]]"
check "scenario1 claim precedes complete" "[[ -n '$claim_line' && -n '$complete_line' && '$claim_line' -lt '$complete_line' ]]"
check "scenario1 complete carries lease_token" "grep -q '\"lease_token\": \"tok-123\"' '$TMP/calls.log'"
check "scenario1 applied is logged" "grep -q 'result=applied' '$TMP/run1.log'"
check "scenario1 does not auto-release a started lease" "! grep -q 'POST .*release' '$TMP/calls.log'"

# ── Scenario 2: rejected claim must not run claude or complete ──────────────
run_harness "$TMP/run2.log" "1"
wait_for "$TMP/run2.log" "Skipping r1/implement: no lease" 25 || true
kill "$HARNESS_PID" 2>/dev/null || true
wait "$HARNESS_PID" 2>/dev/null || true

check "scenario2 claim rejection is logged" "grep -q 'Claim rejected' '$TMP/run2.log'"
check "scenario2 skips execution" "grep -q 'Skipping r1/implement: no lease' '$TMP/run2.log'"
check "scenario2 never runs claude" "! grep -q '^CLAUDE ' '$TMP/calls.log'"
check "scenario2 never calls complete" "! grep -q 'POST .*complete' '$TMP/calls.log'"

# ── Scenario 3: a long run keeps renewing the lease ────────────────────────
run_harness "$TMP/run3.log" "" 1 2
wait_for "$TMP/run3.log" "Server response: result=applied" 75 || true
kill "$HARNESS_PID" 2>/dev/null || true
wait "$HARNESS_PID" 2>/dev/null || true

check "scenario3 renews during execution" "grep -q 'POST .*renew' '$TMP/calls.log'"
check "scenario3 still completes after renewal" "grep -q 'result=applied' '$TMP/run3.log'"

echo
echo "passed=${PASS} failed=${FAIL}"
[[ "$FAIL" -eq 0 ]]
