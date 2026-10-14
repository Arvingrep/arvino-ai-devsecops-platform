# Decision point used by the orchestration engine. Contract:
#   POST /v1/data/arvino/authz/decision  {"input": {...}}
#   -> {"result": {"allow": bool, "deny": [string, ...]}}
# Any malformed input produces a denial; an undefined result is treated as deny by
# the client (fail closed).
package arvino.authz

import rego.v1

import data.arvino.config

decision := {"allow": count(deny) == 0, "deny": sort(deny)}

known_actions := {"gate.evaluate", "approval.decide", "deploy.request", "git.write"}

valid_action if input.action in known_actions

deny contains "unknown or missing action" if not valid_action

deny contains "malformed input: principal" if {
	not valid_principal
}

valid_principal if {
	is_string(input.principal.id)
	input.principal.kind in {"human", "agent", "system"}
	is_array(input.principal.roles)
}

# ---------------------------------------------------------------- helpers

owner_approved(kind) if {
	some a in input.run.approvals
	a.kind == kind
	a.status == "approved"
	a.decided_by.kind == "human"
	"owner" in a.decided_by.roles
}

path_matches(path, patterns) := [pat | some pat in patterns; glob.match(pat, ["/"], path)]

all_required_checks := array.concat(
	config.baseline_required_checks,
	[c | some c in object.get(input, ["gate", "required_checks"], object.get(input, ["deploy", "required_checks"], [])); not c in config.baseline_required_checks],
)

check_denials(checks) := {msg |
	some c in all_required_checks
	state := object.get(checks, c, "missing")
	state != "success"
	msg := sprintf("required check %q is %s", [c, state])
}

# ---------------------------------------------------------------- gate.evaluate

deny contains "malformed input: gate" if {
	input.action == "gate.evaluate"
	not gate_input_ok
}

gate_input_ok if {
	is_array(input.gate.changed_paths)
	is_array(input.gate.required_checks)
	is_object(input.gate.checks)
	is_array(input.run.approvals)
}

deny contains msg if {
	input.action == "gate.evaluate"
	gate_input_ok
	some p in input.gate.changed_paths
	hits := path_matches(p, config.agent_forbidden_paths)
	count(hits) > 0
	msg := sprintf("path %q is protected from agent changes (matches %q)", [p, hits[0]])
}

deny contains msg if {
	input.action == "gate.evaluate"
	gate_input_ok
	some p in input.gate.changed_paths
	hits := path_matches(p, config.restricted_paths)
	count(hits) > 0
	not owner_approved("architecture")
	msg := sprintf("path %q is restricted and requires an owner-approved architecture approval (matches %q)", [p, hits[0]])
}

deny contains msg if {
	input.action == "gate.evaluate"
	gate_input_ok
	some msg in check_denials(input.gate.checks)
}

# ---------------------------------------------------------------- approval.decide

deny contains "malformed input: approval" if {
	input.action == "approval.decide"
	not approval_input_ok
}

approval_input_ok if {
	is_string(input.approval.kind)
	input.approval.decision in {"approved", "rejected"}
	is_array(input.run.actors)
}

deny contains "only human principals may decide approvals" if {
	input.action == "approval.decide"
	input.principal.kind != "human"
}

deny contains "approver must hold the owner role" if {
	input.action == "approval.decide"
	not "owner" in input.principal.roles
}

deny contains "approver acted on this run (separation of duties)" if {
	input.action == "approval.decide"
	approval_input_ok
	input.principal.id in input.run.actors
}

deny contains msg if {
	input.action == "approval.decide"
	approval_input_ok
	not input.approval.kind in config.approval_kinds
	msg := sprintf("unknown approval kind %q", [input.approval.kind])
}

# ---------------------------------------------------------------- deploy.request

deny contains "malformed input: deploy" if {
	input.action == "deploy.request"
	not deploy_input_ok
}

deploy_input_ok if {
	is_string(input.deploy.environment)
	is_string(input.deploy.method)
	is_array(input.deploy.required_checks)
	is_object(input.deploy.checks)
	is_array(input.run.approvals)
	is_array(input.run.deployed_envs)
	is_string(input.run.profile)
}

deny contains msg if {
	input.action == "deploy.request"
	deploy_input_ok
	input.deploy.method != "gitops"
	msg := sprintf("direct writes to %s are forbidden; changes must flow through GitOps", [input.deploy.environment])
}

deny contains msg if {
	input.action == "deploy.request"
	deploy_input_ok
	not input.deploy.environment in config.environments
	msg := sprintf("unknown environment %q", [input.deploy.environment])
}

deny contains "agents may not request deployments" if {
	input.action == "deploy.request"
	input.principal.kind == "agent"
}

deny contains msg if {
	input.action == "deploy.request"
	deploy_input_ok
	some msg in check_denials(input.deploy.checks)
}

deny contains "production deploy requires an approved production-deploy approval from a human owner" if {
	input.action == "deploy.request"
	deploy_input_ok
	input.deploy.environment == "production"
	not owner_approved("production-deploy")
}

deny contains "governed production deploy requires an approved architecture approval" if {
	input.action == "deploy.request"
	deploy_input_ok
	input.deploy.environment == "production"
	input.run.profile == "governed"
	not owner_approved("architecture")
}

deny contains "governed production deploy requires a prior successful staging deploy" if {
	input.action == "deploy.request"
	deploy_input_ok
	input.deploy.environment == "production"
	input.run.profile == "governed"
	not "staging" in input.run.deployed_envs
}

# ---------------------------------------------------------------- git.write

deny contains "malformed input: git" if {
	input.action == "git.write"
	not git_input_ok
}

git_input_ok if {
	input.git.operation in {"push", "force_push", "delete_branch", "merge"}
	is_string(input.git.branch)
}

protected_branch(branch) if {
	some pat in config.protected_branches
	glob.match(pat, ["/"], branch)
}

deny contains msg if {
	input.action == "git.write"
	git_input_ok
	protected_branch(input.git.branch)
	input.principal.kind != "human"
	msg := sprintf("only humans may write to protected branch %q", [input.git.branch])
}

deny contains "agents may not merge" if {
	input.action == "git.write"
	git_input_ok
	input.git.operation == "merge"
	input.principal.kind != "human"
}

deny contains msg if {
	input.action == "git.write"
	git_input_ok
	input.principal.kind == "agent"
	not startswith(input.git.branch, config.agent_branch_prefix)
	msg := sprintf("agents may only write branches under %q", [config.agent_branch_prefix])
}

deny contains "agents may not force-push" if {
	input.action == "git.write"
	git_input_ok
	input.git.operation == "force_push"
	input.principal.kind == "agent"
}
