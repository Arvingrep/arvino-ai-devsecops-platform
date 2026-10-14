#!/usr/bin/env bash
# agent-harness.sh — External agent harness for Arvino
#
# Polls the Arvino /work endpoint, picks up agent steps, runs claude -p
# locally on the MacBook, and reports results back via /complete.
#
# This bypasses the need to install the Claude Code CLI inside the GKE
# container. The worker pod keeps ARVINO_ADAPTERS=queue (steps go to
# "running" state and wait for external completion), and THIS script is
# the external executor.
#
# Required env:
#   ARVINO_API_TOKEN     — Bearer token for the Arvino API (agent role)
#   CLAUDE_CODE_OAUTH_TOKEN — OAuth token for Claude Code CLI
#
# Optional env:
#   ARVINO_API_URL       — Base URL (default: https://arvino.k8shome.com)
#   ARVINO_POLL_INTERVAL — Seconds between polls (default: 30)
#   ARVINO_WORK_DIR      — Directory to clone repos into (default: /tmp/arvino-agent)
#   ARVINO_ROLE          — Only pick up work for this role (default: all roles)
#   CLAUDE_MODEL         — Model to use (default: sonnet)
#   CLAUDE_MAX_TURNS     — Max agentic turns (default: 30)
#   CLAUDE_TIMEOUT       — Timeout in seconds (default: 600)
#   LOG_LEVEL            — debug|info|warn (default: info)

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────
API_URL="${ARVINO_API_URL:-https://arvino.k8shome.com}"
POLL_INTERVAL="${ARVINO_POLL_INTERVAL:-30}"
WORK_DIR="${ARVINO_WORK_DIR:-/tmp/arvino-agent}"
ROLE="${ARVINO_ROLE:-}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
CLAUDE_MAX_TURNS="${CLAUDE_MAX_TURNS:-30}"
CLAUDE_TIMEOUT="${CLAUDE_TIMEOUT:-600}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# ── Validate prerequisites ──────────────────────────────────────────────────
if [[ -z "${ARVINO_API_TOKEN:-}" ]]; then
  echo "FATAL: ARVINO_API_TOKEN is required" >&2
  exit 1
fi
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  echo "FATAL: CLAUDE_CODE_OAUTH_TOKEN is required" >&2
  exit 1
fi
if ! command -v claude &>/dev/null; then
  echo "FATAL: 'claude' CLI not found on PATH" >&2
  exit 1
fi
if ! command -v jq &>/dev/null; then
  echo "FATAL: 'jq' is required" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"

# ── Logging helpers ─────────────────────────────────────────────────────────
_ts() { date "+%Y-%m-%dT%H:%M:%S%z"; }
log_debug() { [[ "$LOG_LEVEL" == "debug" ]] && echo "$(_ts) [DEBUG] $*" >&2 || true; }
log_info()  { [[ "$LOG_LEVEL" != "warn" ]]  && echo "$(_ts) [INFO]  $*" >&2 || true; }
log_warn()  { echo "$(_ts) [WARN]  $*" >&2; }
log_error() { echo "$(_ts) [ERROR] $*" >&2; }

AUTH_HEADER="Authorization: Bearer ${ARVINO_API_TOKEN}"

# ── API helpers ─────────────────────────────────────────────────────────────
api_get() {
  local path="$1"
  curl -sS --fail-with-body \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    "${API_URL}${path}" 2>&1
}

api_post() {
  local path="$1" body="$2"
  curl -sS --fail-with-body \
    -X POST \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "${API_URL}${path}" 2>&1
}

# ── Fetch context for a work item ───────────────────────────────────────────
fetch_run_context() {
  local run_id="$1"
  # Get run details (includes steps, project_id, issue_id)
  local run_json
  run_json=$(api_get "/runs/${run_id}")
  echo "$run_json"
}

fetch_issue_context() {
  local issue_id="$1"
  local issue_json
  issue_json=$(api_get "/issues/${issue_id}")
  echo "$issue_json"
}

fetch_project_context() {
  local project_id="$1"
  local project_json
  project_json=$(api_get "/projects/${project_id}")
  echo "$project_json"
}

# ── Build the prompt (mirrors ClaudeCodeExecutor._build_prompt) ─────────────
build_prompt() {
  local role="$1" run_id="$2" step_key="$3" attempt="$4"
  local idempotency_key="$5" branch_prefix="$6"
  local issue_title="$7" issue_body="$8" repo="$9"

  cat <<PROMPT
You are acting as the **${role}** agent.
Run: ${run_id}  Step: ${step_key}  Attempt: ${attempt}
Idempotency key: ${idempotency_key}
Branch prefix: ${branch_prefix}

## Issue: ${issue_title}

${issue_body}

## Rules
- Only create branches under the prefix \`${branch_prefix}\`.
- Never merge a pull request.
- Commit your changes and push when done.
- Target repository: ${repo}
PROMPT
}

# ── Execute a single work item ─────────────────────────────────────────────
execute_work_item() {
  local item_json="$1"

  # Parse work item fields
  local run_id step_key role attempt actor branch_prefix idempotency_key
  run_id=$(echo "$item_json" | jq -r '.run_id')
  step_key=$(echo "$item_json" | jq -r '.step_key')
  role=$(echo "$item_json" | jq -r '.role')
  attempt=$(echo "$item_json" | jq -r '.attempt')
  actor=$(echo "$item_json" | jq -r '.actor')
  branch_prefix=$(echo "$item_json" | jq -r '.branch_prefix')
  idempotency_key=$(echo "$item_json" | jq -r '.idempotency_key')

  log_info "═══ Executing: run=${run_id} step=${step_key} role=${role} attempt=${attempt}"

  # Fetch run, issue, project context
  local run_json issue_json project_json
  run_json=$(fetch_run_context "$run_id")
  local project_id issue_id
  project_id=$(echo "$run_json" | jq -r '.project_id')
  issue_id=$(echo "$run_json" | jq -r '.issue_id')

  issue_json=$(fetch_issue_context "$issue_id")
  project_json=$(fetch_project_context "$project_id")

  local issue_title issue_body repo
  issue_title=$(echo "$issue_json" | jq -r '.title // "untitled"')
  issue_body=$(echo "$issue_json" | jq -r '.body // ""')
  repo=$(echo "$project_json" | jq -r '.repo_full_name // ""')

  log_info "  Issue: ${issue_title}"
  log_info "  Repo:  ${repo}"

  # Prepare working directory
  local work_dir="${WORK_DIR}/${run_id}/${step_key}"
  mkdir -p "$work_dir"

  # Clone the repo if it has one and it doesn't exist yet
  local repo_dir="${WORK_DIR}/repos/${repo##*/}"
  if [[ -n "$repo" && "$repo" != "null" ]]; then
    if [[ ! -d "$repo_dir/.git" ]]; then
      log_info "  Cloning https://github.com/${repo} → ${repo_dir}"
      git clone "https://github.com/${repo}.git" "$repo_dir" 2>&1 | while read -r line; do
        log_debug "    git: $line"
      done
    else
      log_info "  Repo already cloned at ${repo_dir}, fetching…"
      git -C "$repo_dir" fetch --all --prune 2>&1 | while read -r line; do
        log_debug "    git: $line"
      done
    fi
  fi

  # Build the prompt
  local prompt
  prompt=$(build_prompt \
    "$role" "$run_id" "$step_key" "$attempt" \
    "$idempotency_key" "$branch_prefix" \
    "$issue_title" "$issue_body" "$repo")

  log_debug "Prompt:\n$prompt"

  # Determine cwd for claude
  local claude_cwd="$repo_dir"
  if [[ ! -d "$claude_cwd" ]]; then
    claude_cwd="$work_dir"
  fi

  # Run claude -p
  log_info "  Running claude -p (model=${CLAUDE_MODEL}, max-turns=${CLAUDE_MAX_TURNS}, timeout=${CLAUDE_TIMEOUT}s)…"
  local claude_stdout claude_stderr claude_exit
  local output_file="${work_dir}/claude-output.json"
  local error_file="${work_dir}/claude-stderr.txt"

  set +e
  # Use perl alarm for timeout (macOS doesn't have coreutils timeout by default)
  perl -e "alarm $CLAUDE_TIMEOUT; exec @ARGV" \
    claude -p "$prompt" \
    --model "$CLAUDE_MODEL" \
    --max-turns "$CLAUDE_MAX_TURNS" \
    --dangerously-skip-permissions \
    --output-format json \
    --allowedTools "Read,Write,Edit,Bash" \
    >"$output_file" 2>"$error_file"
  claude_exit=$?
  set -e

  # Parse result and report back
  local status="succeeded"
  local output="{}"
  local error_msg=""

  if [[ $claude_exit -ne 0 ]]; then
    status="failed"
    error_msg=$(cat "$error_file" 2>/dev/null | head -c 2000)
    if [[ $claude_exit -eq 124 || $claude_exit -eq 142 ]]; then
      error_msg="Claude Code timed out after ${CLAUDE_TIMEOUT}s"
    fi
    log_warn "  Claude exited with code ${claude_exit}: ${error_msg}"
  else
    # Extract the last JSON object from output (Claude may emit JSONL)
    if [[ -s "$output_file" ]]; then
      output=$(python3 -c "
import json, sys
text = open('$output_file').read().strip()
if not text:
    print('{}')
    sys.exit(0)
# Try last line first (JSONL)
for line in reversed(text.splitlines()):
    line = line.strip()
    if line.startswith('{'):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                print(json.dumps(obj))
                sys.exit(0)
        except json.JSONDecodeError:
            continue
# Fallback: whole blob
try:
    obj = json.loads(text)
    print(json.dumps(obj if isinstance(obj, dict) else {'raw': obj}))
except json.JSONDecodeError:
    print(json.dumps({'raw_output': text[:4000]}))
" 2>/dev/null || echo '{}')
    fi
    log_info "  Claude succeeded."
  fi

  # Build completion payload
  local payload
  payload=$(jq -n \
    --argjson attempt "$attempt" \
    --arg status "$status" \
    --argjson output "$output" \
    --arg error "$error_msg" \
    --arg actor "$actor" \
    '{
      attempt: $attempt,
      status: $status,
      output: $output,
      actor: $actor
    } + (if $error != "" then {error: $error, retryable: true} else {} end)')

  log_info "  Reporting completion: status=${status}"
  log_debug "  Payload: ${payload}"

  local complete_response
  complete_response=$(api_post "/runs/${run_id}/steps/${step_key}/complete" "$payload")
  local result
  result=$(echo "$complete_response" | jq -r '.result // "unknown"' 2>/dev/null || echo "parse-error")

  log_info "  Server response: result=${result}"

  case "$result" in
    applied)  log_info "  ✓ Step ${step_key} completed successfully" ;;
    duplicate) log_warn "  Step ${step_key} was already completed (duplicate)" ;;
    stale)    log_warn "  Step ${step_key} completion was stale (wrong attempt)" ;;
    *)        log_error "  Unexpected result: ${complete_response}" ;;
  esac
}

# ── Main polling loop ───────────────────────────────────────────────────────
main() {
  log_info "╔══════════════════════════════════════════════════════════════╗"
  log_info "║  Arvino Agent Harness — External Claude Code Executor      ║"
  log_info "╚══════════════════════════════════════════════════════════════╝"
  log_info "API:        ${API_URL}"
  log_info "Poll:       every ${POLL_INTERVAL}s"
  log_info "Role:       ${ROLE:-all}"
  log_info "Model:      ${CLAUDE_MODEL}"
  log_info "Max turns:  ${CLAUDE_MAX_TURNS}"
  log_info "Timeout:    ${CLAUDE_TIMEOUT}s"
  log_info "Work dir:   ${WORK_DIR}"
  log_info ""

  local consecutive_errors=0

  while true; do
    # Build query path
    local work_path="/work"
    if [[ -n "$ROLE" ]]; then
      work_path="/work?role=${ROLE}"
    fi

    # Poll for work
    local work_json
    set +e
    work_json=$(api_get "$work_path")
    local poll_exit=$?
    set -e

    if [[ $poll_exit -ne 0 ]]; then
      consecutive_errors=$((consecutive_errors + 1))
      log_warn "Poll failed (attempt #${consecutive_errors}): ${work_json}"
      if [[ $consecutive_errors -ge 10 ]]; then
        log_error "Too many consecutive errors (${consecutive_errors}), backing off to 60s"
        sleep 60
      else
        sleep "$POLL_INTERVAL"
      fi
      continue
    fi
    consecutive_errors=0

    # Count work items
    local count
    count=$(echo "$work_json" | jq 'length' 2>/dev/null || echo 0)

    if [[ "$count" -eq 0 || "$count" == "0" ]]; then
      log_debug "No work items available"
      sleep "$POLL_INTERVAL"
      continue
    fi

    log_info "Found ${count} work item(s)"

    # Process each work item
    local i=0
    while [[ $i -lt $count ]]; do
      local item
      item=$(echo "$work_json" | jq ".[$i]")
      execute_work_item "$item" || {
        log_error "Failed to execute work item $i"
      }
      i=$((i + 1))
    done

    # Brief pause between batches (don't immediately re-poll)
    sleep 5
  done
}

# ── Signal handling ─────────────────────────────────────────────────────────
cleanup() {
  log_info "Shutting down agent harness…"
  exit 0
}
trap cleanup SIGINT SIGTERM

main "$@"
