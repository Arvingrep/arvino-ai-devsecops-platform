#!/usr/bin/env bash
# Read-only Gitea open-PR summary for the Arvino inspection job.
#
# Credential source: macOS keychain item for git.k8shome.com, read at runtime.
# The token is never echoed, never written to disk, and is unset on exit.
# Output is limited to PR number/state/mergeable and a short title prefix so
# the inspection ledger never contains credentials or private task bodies.
set -euo pipefail

TOKEN="$(/usr/bin/security find-internet-password -s git.k8shome.com -w 2>/dev/null || true)"
if [ -z "${TOKEN}" ]; then
  echo "GITEA_CRED_UNAVAILABLE"
  exit 0
fi
trap 'TOKEN=' EXIT

for repo in gitea_admin/homelab gitea_admin/firebird-sea; do
  body="$(/usr/bin/curl -sS --max-time 12 \
    -H "Authorization: token ${TOKEN}" \
    "https://git.k8shome.com/api/v1/repos/${repo}/pulls?state=open&limit=50" 2>/dev/null || true)"
  if [ -z "${body}" ]; then
    echo "${repo}: GITEA_API_EMPTY"
    continue
  fi
  printf '%s' "${body}" | REPO="${repo}" python3 -c '
import json, os, sys
repo = os.environ.get("REPO", "?")
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    print(f"{repo}: GITEA_API_UNPARSEABLE")
    sys.exit(0)
if not isinstance(data, list):
    print(f"{repo}: GITEA_API_ERROR")
    sys.exit(0)
mergeable = sum(1 for p in data if p.get("mergeable") is True)
print(f"{repo}: open={len(data)} mergeable={mergeable}")
for p in data:
    num = p.get("number", "?")
    state = p.get("state", "?")
    mg = p.get("mergeable", "?")
    title = (p.get("title") or "").replace("\n", " ")[:60]
    print(f"  #{num} {state} mergeable={mg} {title}")
'
done
