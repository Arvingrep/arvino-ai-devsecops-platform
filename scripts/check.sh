#!/usr/bin/env bash
# One command for every local gate CI runs: lint, types, tests, workflow lint, policy tests,
# manifest rendering + schema + policy validation, negative controls, and secret scanning.
#   scripts/check.sh            # everything
#   scripts/check.sh --fast     # skip network-dependent steps (kubeconform schemas, helm charts)
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PWD/.runtime/tools/bin:$PATH"
FAST=0; [[ "${1:-}" == "--fast" ]] && FAST=1
FAILED=()
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

step() { # step "<name>" cmd...
  local name="$1"; shift
  if out=$("$@" 2>&1); then printf 'PASS  %s\n' "$name"; else printf 'FAIL  %s\n%s\n' "$name" "$(sed 's/^/      /' <<<"$out" | tail -25)"; FAILED+=("$name"); fi
}
need() { command -v "$1" >/dev/null 2>&1 || { echo "FAIL  missing tool: $1"; FAILED+=("tool:$1"); return 1; }; }
expect_fail() { # expect_fail "<name>" cmd...   (negative control: the command MUST fail)
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then printf 'FAIL  %s (bad fixture was accepted!)\n' "$name"; FAILED+=("$name"); else printf 'PASS  %s\n' "$name"; fi
}

for t in ruff mypy pytest yamllint opa conftest gitleaks kubectl; do need "$t"; done
(( FAST )) || { need kubeconform; need helm; }

step "ruff lint"            ruff check src tests
step "ruff format"          ruff format --check src tests
step "mypy --strict"        mypy
step "yamllint"             yamllint -s .
step "workflow definitions" arvino validate-workflows
step "pytest"               pytest -q --cov=arvino --cov-report=term:skip-covered --cov-fail-under=85
step "opa check --strict"   opa check --strict policy
step "opa fmt"              bash -c 'test -z "$(opa fmt -l policy)"'
step "opa test (rego unit tests)" opa test policy
step "OPA ConfigMap is in sync with policy/" python scripts/gen_opa_configmap.py --check

for env in dev staging production; do
  kubectl kustomize "deploy/overlays/$env" > "$TMP/$env.yaml" 2>"$TMP/$env.err" \
    && printf 'PASS  kustomize build %s\n' "$env" \
    || { printf 'FAIL  kustomize build %s\n' "$env"; cat "$TMP/$env.err"; FAILED+=("kustomize:$env"); }
  step "conftest workloads+no-secrets ($env)" conftest test --no-color --policy policy/k8s --namespace k8s.workloads --namespace k8s.nosecrets "$TMP/$env.yaml"
  (( FAST )) || step "kubeconform strict ($env)" kubeconform -strict -summary -kubernetes-version 1.30.0 "$TMP/$env.yaml"
done
step "conftest secret examples are placeholders only" conftest test --no-color --policy policy/k8s --namespace k8s.secrets deploy/secrets-examples
step "conftest argocd guardrails" conftest test --no-color --policy policy/k8s --namespace k8s.argocd deploy/argocd/appproject.yaml deploy/argocd/applications.yaml
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
(( FAST )) || step "kubeconform argocd CRDs" kubeconform -strict -summary -schema-location default -schema-location "$CRDS" deploy/argocd
if (( ! FAST )); then
  step "helm template temporal + kubeconform" bash -c "helm template temporal temporal --repo https://go.temporal.io/helm-charts --version 1.7.0 -n temporal -f deploy/helm-values/temporal.yaml > $TMP/temporal.yaml && kubeconform -strict -summary -kubernetes-version 1.30.0 $TMP/temporal.yaml"
  step "helm template plane + kubeconform"    bash -c "helm template plane-app plane-ce --repo https://helm.plane.so/ --version 1.8.5 -n plane -f deploy/helm-values/plane.yaml > $TMP/plane.yaml && kubeconform -strict -summary -kubernetes-version 1.30.0 $TMP/plane.yaml"
fi

# Negative controls: the policies must actually reject known-bad manifests.
expect_fail "negative control: insecure workload rejected"       conftest test --no-color --policy policy/k8s --namespace k8s.workloads tests/fixtures/bad/deployment.yaml
expect_fail "negative control: real-looking Secret rejected"     conftest test --no-color --policy policy/k8s --namespace k8s.secrets tests/fixtures/bad/secret.yaml
expect_fail "negative control: production auto-sync rejected"    conftest test --no-color --policy policy/k8s --namespace k8s.argocd tests/fixtures/bad/argocd.yaml

# Secret scanning: committed history + the working tree (tracked + untracked, excluding git-ignored files).
step "gitleaks (git history)" gitleaks git --no-banner --redact -c .gitleaks.toml .
mkdir -p "$TMP/tree" && git ls-files -z --cached --others --exclude-standard | rsync -a -0 --files-from=- ./ "$TMP/tree/"
step "gitleaks (working tree)" gitleaks dir --no-banner --redact -c .gitleaks.toml "$TMP/tree"

echo
if (( ${#FAILED[@]} )); then printf 'FAILED (%d): %s\n' "${#FAILED[@]}" "${FAILED[*]}"; exit 1; fi
echo "ALL CHECKS PASSED"
