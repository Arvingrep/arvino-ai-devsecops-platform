# Tunable policy data. Changing this file is a protected-path change: it can only
# be modified by a human via reviewed PR (see agent_forbidden_paths below).
package arvino.config

# Branches an agent may never write to or merge into (glob, "/" delimited).
protected_branches := ["main", "master", "release/**", "production", "staging"]

# Agents may only push to branches under this prefix.
agent_branch_prefix := "arvino/"

# Paths no agent-authored change may touch. The CI/policy machinery must not be
# editable by the thing it constrains.
agent_forbidden_paths := [
	"policy/**",
	".github/**",
	".gitea/**",
	".gitleaks.toml",
	"CODEOWNERS",
	"**/CODEOWNERS",
	"deploy/overlays/production/**",
]

# Paths that need an approved `architecture` owner approval on the run
# (infrastructure, schema, dependency/stack and spending boundaries).
restricted_paths := [
	"terraform/**",
	"**/*.tf",
	"**/migrations/**",
	"migrations/**",
	"deploy/base/**",
	"deploy/argocd/**",
	"pyproject.toml",
	"**/Dockerfile",
	"Dockerfile",
	"compose.yaml",
]

# Checks that must be green for any gate/deploy regardless of what the workflow
# definition lists. A workflow YAML can add checks but never remove these.
baseline_required_checks := ["unit-tests", "lint", "secret-scan"]

environments := ["preview", "staging", "production"]

approval_kinds := [
	"architecture",
	"production-deploy",
	"destructive-migration",
	"terraform-apply",
	"spending",
]
