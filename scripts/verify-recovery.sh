#!/usr/bin/env bash
# Crash-recovery drill against the Compose stack: block a run on approval, kill -9 the
# orchestrator container, bring it back, and prove the ledger + approval flow survived.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
python3 scripts/smoke.py --recovery-prepare
docker compose kill -s KILL orchestrator
docker compose up -d orchestrator
python3 scripts/smoke.py --recovery-verify
