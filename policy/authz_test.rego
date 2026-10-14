package arvino.authz_test

import data.arvino.authz

owner := {"id": "owner", "kind": "human", "roles": ["owner"]}

agent := {"id": "agent:developer", "kind": "agent", "roles": []}

system := {"id": "system:engine", "kind": "system", "roles": ["orchestrator"]}

green := {"unit-tests": "success", "lint": "success", "secret-scan": "success", "trivy": "success"}

approved(kind) := {"kind": kind, "status": "approved", "decided_by": owner}

gate_input(paths, checks, approvals) := {
	"action": "gate.evaluate",
	"principal": system,
	"run": {"profile": "standard", "actors": [], "approvals": approvals, "deployed_envs": []},
	"gate": {"changed_paths": paths, "required_checks": ["unit-tests", "lint", "secret-scan", "trivy"], "checks": checks},
}

deploy_input(env, method, approvals, deployed, profile) := {
	"action": "deploy.request",
	"principal": system,
	"run": {"profile": profile, "actors": [], "approvals": approvals, "deployed_envs": deployed},
	"deploy": {"environment": env, "method": method, "required_checks": ["unit-tests", "lint", "secret-scan", "trivy"], "checks": green},
}

approval_input(principal, actors, kind) := {
	"action": "approval.decide",
	"principal": principal,
	"run": {"actors": actors},
	"approval": {"kind": kind, "decision": "approved"},
}

git_input(principal, op, branch) := {
	"action": "git.write",
	"principal": principal,
	"git": {"operation": op, "branch": branch},
}

# ------------------------------------------------------------------ gate

test_gate_allows_clean_change if {
	authz.decision.allow with input as gate_input(["src/app.py", "README.md"], green, [])
}

test_gate_denies_policy_dir_even_with_approval if {
	d := authz.decision with input as gate_input(["policy/authz.rego"], green, [approved("architecture")])
	not d.allow
	some m in d.deny
	contains(m, "protected from agent changes")
}

test_gate_denies_ci_config if {
	not authz.decision.allow with input as gate_input([".gitea/workflows/ci.yaml"], green, [])
	not authz.decision.allow with input as gate_input([".github/workflows/ci.yaml"], green, [])
}

test_gate_denies_production_overlay if {
	not authz.decision.allow with input as gate_input(["deploy/overlays/production/kustomization.yaml"], green, [approved("architecture")])
}

test_gate_restricted_path_needs_architecture_approval if {
	not authz.decision.allow with input as gate_input(["terraform/main.tf"], green, [])
	not authz.decision.allow with input as gate_input(["db/migrations/001.sql"], green, [])
	authz.decision.allow with input as gate_input(["terraform/main.tf"], green, [approved("architecture")])
}

test_gate_restricted_path_ignores_agent_or_pending_approval if {
	pending := {"kind": "architecture", "status": "pending", "decided_by": owner}
	by_agent := {"kind": "architecture", "status": "approved", "decided_by": agent}
	not authz.decision.allow with input as gate_input(["terraform/main.tf"], green, [pending])
	not authz.decision.allow with input as gate_input(["terraform/main.tf"], green, [by_agent])
}

test_gate_denies_missing_or_failed_required_check if {
	failing := object.union(green, {"trivy": "failure"})
	missing := object.remove(green, ["trivy"])
	not authz.decision.allow with input as gate_input(["src/a.py"], failing, [])
	not authz.decision.allow with input as gate_input(["src/a.py"], missing, [])
}

test_gate_baseline_checks_cannot_be_dropped_by_workflow if {
	inp := object.union(gate_input(["src/a.py"], {"trivy": "success"}, []), {"gate": {"changed_paths": ["src/a.py"], "required_checks": ["trivy"], "checks": {"trivy": "success"}}})
	d := authz.decision with input as inp
	not d.allow
	"required check \"unit-tests\" is missing" in d.deny
}

test_gate_malformed_input_denied if {
	not authz.decision.allow with input as {"action": "gate.evaluate", "principal": system}
	not authz.decision.allow with input as {"action": "gate.evaluate", "principal": system, "gate": {"changed_paths": "src/a.py"}, "run": {"approvals": []}}
}

# ------------------------------------------------------------------ approvals

test_owner_can_approve if {
	authz.decision.allow with input as approval_input(owner, ["agent:developer"], "production-deploy")
}

test_agent_cannot_approve if {
	not authz.decision.allow with input as approval_input(agent, [], "production-deploy")
}

test_non_owner_human_cannot_approve if {
	qa_human := {"id": "someone", "kind": "human", "roles": ["qa"]}
	not authz.decision.allow with input as approval_input(qa_human, [], "production-deploy")
}

test_self_approval_denied if {
	d := authz.decision with input as approval_input(owner, ["owner"], "production-deploy")
	not d.allow
	"approver acted on this run (separation of duties)" in d.deny
}

test_unknown_approval_kind_denied if {
	not authz.decision.allow with input as approval_input(owner, [], "yolo")
}

# ------------------------------------------------------------------ deploy

test_preview_deploy_allowed_via_gitops if {
	authz.decision.allow with input as deploy_input("preview", "gitops", [], [], "fast")
}

test_direct_write_denied_everywhere if {
	not authz.decision.allow with input as deploy_input("preview", "kubectl", [], [], "fast")
	not authz.decision.allow with input as deploy_input("production", "kubectl", [approved("production-deploy")], [], "standard")
}

test_production_requires_owner_approval if {
	not authz.decision.allow with input as deploy_input("production", "gitops", [], [], "standard")
	authz.decision.allow with input as deploy_input("production", "gitops", [approved("production-deploy")], [], "standard")
}

test_production_approval_must_be_from_human_owner if {
	forged := {"kind": "production-deploy", "status": "approved", "decided_by": agent}
	not authz.decision.allow with input as deploy_input("production", "gitops", [forged], [], "standard")
}

test_governed_production_needs_architecture_and_staging if {
	both := [approved("production-deploy"), approved("architecture")]
	not authz.decision.allow with input as deploy_input("production", "gitops", [approved("production-deploy")], ["staging"], "governed")
	not authz.decision.allow with input as deploy_input("production", "gitops", both, [], "governed")
	authz.decision.allow with input as deploy_input("production", "gitops", both, ["staging"], "governed")
}

test_agent_cannot_request_deploy if {
	inp := object.union(deploy_input("preview", "gitops", [], [], "fast"), {"principal": agent})
	not authz.decision.allow with input as inp
}

test_deploy_denied_when_checks_not_green if {
	inp := object.union(deploy_input("preview", "gitops", [], [], "fast"), {"deploy": {"environment": "preview", "method": "gitops", "required_checks": [], "checks": {"unit-tests": "failure", "lint": "success", "secret-scan": "success"}}})
	not authz.decision.allow with input as inp
}

# ------------------------------------------------------------------ git

test_agent_can_push_own_branch if {
	authz.decision.allow with input as git_input(agent, "push", "arvino/run-abc/develop")
}

test_agent_cannot_push_protected_or_foreign_branch if {
	not authz.decision.allow with input as git_input(agent, "push", "main")
	not authz.decision.allow with input as git_input(agent, "push", "release/1.0")
	not authz.decision.allow with input as git_input(agent, "push", "feature/x")
}

test_agent_cannot_merge_anything if {
	not authz.decision.allow with input as git_input(agent, "merge", "arvino/run-abc/develop")
	not authz.decision.allow with input as git_input(agent, "merge", "main")
}

test_agent_cannot_force_push if {
	not authz.decision.allow with input as git_input(agent, "force_push", "arvino/run-abc/develop")
}

test_system_cannot_write_protected if {
	not authz.decision.allow with input as git_input(system, "push", "main")
}

test_human_may_merge_main if {
	authz.decision.allow with input as git_input(owner, "merge", "main")
}

# ------------------------------------------------------------------ meta

test_unknown_or_missing_action_denied if {
	not authz.decision.allow with input as {"action": "rm.rf", "principal": owner}
	not authz.decision.allow with input as {"principal": owner}
	not authz.decision.allow with input as {}
}

test_bad_principal_denied if {
	not authz.decision.allow with input as {"action": "git.write", "principal": {"id": "x"}, "git": {"operation": "push", "branch": "arvino/x/y"}}
}
