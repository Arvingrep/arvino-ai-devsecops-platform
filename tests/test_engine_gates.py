"""CI/gate/deploy semantics on trusted inputs, separation of duties, fail-closed behaviour."""

from __future__ import annotations

from typing import Any

from arvino.domain import ArtifactSpec, Profile
from arvino.fakes import StaticPolicy
from arvino.policy import FailClosedPolicy
from arvino.ports import Decision, StepOutcome
from tests.conftest import AGENT, ALL_CHECKS, BASE_CHECKS, REPO, SHA, Env, build_env


def at_ci(env: Env, profile: Profile = Profile.FAST, paths: list[str] | None = None) -> str:
    env.executor.script("pull_request", env.pr_script(paths))
    run = env.start(profile)
    return run.id


# ----------------------------------------------------------------------------- CI


def test_ci_waits_until_all_required_checks_succeed(env: Env) -> None:
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.checks(run_id, ["unit-tests", "lint"])
    assert env.engine.get_run(run_id).step("ci").status == "running"
    env.checks(run_id, ["secret-scan"])
    assert env.engine.get_run(run_id).step("ci").status == "succeeded"


def test_failed_required_check_fails_run_and_blocks_gate_and_deploy(env: Env) -> None:
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.checks(run_id, ["unit-tests", "lint"])
    env.checks(run_id, ["secret-scan"], "failure")
    run = env.engine.get_run(run_id)
    assert run.status == "failed" and "secret-scan" in (run.failure_reason or "")
    assert (
        run.step("merge_gate").status == "cancelled" and run.step("preview").status == "cancelled"
    )
    assert env.promoter.requests == []


def test_checks_for_a_stale_commit_do_not_satisfy_ci(env: Env) -> None:
    run_id = at_ci(env)
    env.checks(run_id, BASE_CHECKS, sha="f" * 40)  # results for some other commit
    env.webhook_pr(run_id)  # head is SHA
    assert env.engine.get_run(run_id).step("ci").status == "running"


def test_results_that_arrive_before_the_pr_step_completes_are_picked_up_later(env: Env) -> None:
    env.executor.script("pull_request", StepOutcome(status="running"))  # agent still finishing up
    run = env.start(Profile.FAST)
    env.checks(run.id, BASE_CHECKS)  # webhook ordering: CI finished before the agent reported back
    env.webhook_pr(run.id)
    assert env.engine.get_run(run.id).step("ci").status == "pending"
    env.git.register_pr(REPO, 1, run.id, ["src/app.py"], SHA)
    pr_out = env.pr_script()(env.executor.calls_for("pull_request")[0])
    env.engine.complete_step(run.id, "pull_request", 1, pr_out, AGENT)
    assert env.engine.get_run(run.id).step("ci").status == "succeeded"


def test_new_commit_after_ci_invalidates_green_checks_for_the_gate() -> None:
    env = build_env()
    # Hold the gate by making the git provider unavailable once, so we can push a new commit in between.
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.git.fail_changed_paths = True
    env.checks(run_id, BASE_CHECKS)
    run = env.engine.get_run(run_id)
    assert run.step("ci").status == "succeeded"
    assert run.step("merge_gate").status == "pending"  # retry scheduled after provider error
    env.engine.update_git_state(run_id, head_sha="c" * 40, source="test")  # force-push after CI
    env.git.fail_changed_paths = False
    env.clock.advance(31)
    run = env.engine.advance(run_id)
    assert run.step("merge_gate").status == "failed"
    assert "not green" in (run.step("merge_gate").last_error or "")
    assert "pr.head_moved" in [e.type for e in env.engine.events(run_id)]


# ----------------------------------------------------------------------------- gate / policy


def test_gate_passes_with_allowing_policy_and_sends_trusted_inputs() -> None:
    env = build_env()
    run_id = at_ci(env, paths=["src/app.py", "README.md"])
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    inp = next(i for i in env.policy.inputs if i["action"] == "gate.evaluate")
    assert inp["gate"]["changed_paths"] == ["src/app.py", "README.md"]  # from the Git provider
    assert inp["gate"]["checks"] == dict.fromkeys(BASE_CHECKS, "success")  # from CI webhooks
    assert inp["principal"]["kind"] == "system"


def test_policy_denial_fails_gate_without_retry_and_never_deploys() -> None:
    env = build_env(policy=StaticPolicy(allow=False, reasons=["path 'policy/x' is protected"]))
    run_id = at_ci(env, paths=["policy/x.rego"])
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    run = env.engine.get_run(run_id)
    gate = run.step("merge_gate")
    assert gate.status == "failed" and gate.attempt == 1  # a denial is not retried
    assert "policy denied" in (gate.last_error or "") and "protected" in (gate.last_error or "")
    assert run.status == "failed" and env.promoter.requests == []


def test_policy_outage_fails_closed() -> None:
    class Broken:
        def decide(self, policy_input: dict[str, Any]) -> Decision:
            raise TimeoutError("opa down")

    env = build_env(policy=FailClosedPolicy(Broken()))
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    run = env.engine.get_run(run_id)
    assert run.step("merge_gate").status == "failed"
    assert "policy unavailable" in (run.step("merge_gate").last_error or "")
    assert env.promoter.requests == []


def test_gate_does_not_trust_agent_claims_about_paths_or_checks() -> None:
    env = build_env()
    lying = StepOutcome(
        status="succeeded",
        output={"changed_paths": ["README.md"], "checks": {"unit-tests": "success"}},
        artifacts=[
            ArtifactSpec(
                kind="pull-request",
                name="pr",
                uri="x",
                metadata={"number": 1, "paths": ["README.md"]},
            )
        ],
    )

    def script(req: Any) -> StepOutcome:
        env.git.register_pr(REPO, 1, req.run_id, ["policy/authz.rego"], SHA)
        return lying

    env.executor.script("pull_request", script)
    run = env.start(Profile.FAST)
    env.webhook_pr(run.id)
    env.checks(run.id, BASE_CHECKS)
    inp = next(i for i in env.policy.inputs if i["action"] == "gate.evaluate")
    assert inp["gate"]["changed_paths"] == ["policy/authz.rego"]


def test_pull_request_outside_run_namespace_is_rejected(env: Env) -> None:
    def script(req: Any) -> StepOutcome:
        env.git.register_pr(REPO, 7, "run-000000000000", ["src/a.py"], SHA)  # somebody else's PR
        return StepOutcome(
            status="succeeded",
            artifacts=[
                ArtifactSpec(kind="pull-request", name="pr", uri="x", metadata={"number": 7})
            ],
        )

    env.executor.script("pull_request", script)
    run = env.start(Profile.FAST)
    assert run.step("pull_request").status == "failed"
    assert "not under" in (run.step("pull_request").last_error or "")
    assert run.pr_number is None


def test_pull_request_artifact_without_number_is_rejected(env: Env) -> None:
    env.executor.script(
        "pull_request",
        StepOutcome(
            status="succeeded", artifacts=[ArtifactSpec(kind="pull-request", name="pr", uri="x")]
        ),
    )
    run = env.start(Profile.FAST)
    assert run.step("pull_request").status == "failed"


# ----------------------------------------------------------------------------- separation of duties


def _standard_until_qa(env: Env) -> str:
    env.executor.script("pull_request", env.pr_script())
    run = env.start(Profile.STANDARD)
    env.webhook_pr(run.id)
    return run.id


def test_security_role_cannot_be_the_implementing_actor(env: Env) -> None:
    env.executor.script("backend", StepOutcome(status="succeeded", actor="agent:shared"))
    env.executor.script("security", StepOutcome(status="succeeded", actor="agent:shared"))
    run_id = _standard_until_qa(env)
    env.checks(run_id, ALL_CHECKS)
    run = env.engine.get_run(run_id)
    assert run.step("security").status == "failed"
    assert run.step("security").attempt == 1  # not retried
    assert "separation of duties" in (run.step("security").last_error or "")
    assert run.status == "failed"
    assert not env.executor.calls_for("review")


def test_qa_role_cannot_be_the_implementing_actor_even_when_it_finishes_first(env: Env) -> None:
    env.executor.script("frontend", StepOutcome(status="succeeded", actor="agent:same"))
    env.executor.script("qa", StepOutcome(status="succeeded", actor="agent:same"))
    run_id = _standard_until_qa(env)
    env.checks(run_id, ALL_CHECKS)
    assert env.engine.get_run(run_id).step("qa").status == "failed"


def test_default_role_scoped_identities_satisfy_separation(env: Env) -> None:
    run_id = _standard_until_qa(env)
    env.checks(run_id, ALL_CHECKS)
    run = env.engine.get_run(run_id)
    assert run.step("qa").actor == "agent:qa" and run.step("backend").actor == "agent:backend"
    assert run.step("review").status == "succeeded"


# ----------------------------------------------------------------------------- deploy / git state


def test_preview_waits_for_matching_revision_and_healthy_synced(env: Env) -> None:
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    assert env.engine.get_run(run_id).step("preview").status == "running"
    env.converge("acme-preview", revision="9" * 40)  # some other revision
    assert env.engine.get_run(run_id).step("preview").status == "running"
    env.converge("acme-preview", health="Progressing", sync="Synced")
    assert env.engine.get_run(run_id).step("preview").status == "running"
    env.converge("acme-preview")
    assert env.engine.get_run(run_id).status == "succeeded"


def test_deploy_observation_recorded_before_step_start_still_counts() -> None:
    env = build_env()
    run_id = at_ci(env)
    env.converge("acme-preview")  # Argo reported before the engine got there
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    assert env.engine.get_run(run_id).status == "succeeded"


def test_degraded_deploy_fails_run(env: Env) -> None:
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.checks(run_id, BASE_CHECKS)
    env.converge("acme-preview", health="Degraded", sync="Synced")
    run = env.engine.get_run(run_id)
    assert run.step("preview").status == "failed" and run.status == "failed"


def test_missing_argocd_app_mapping_fails_deploy_closed(env: Env) -> None:
    proj = env.engine.create_project("bare", "Bare", repo_full_name="acme/bare")
    issue, _ = env.engine.upsert_issue(proj.id, "api", "1", "t")
    env.executor.script("pull_request", env.pr_script(repo="acme/bare"))
    run, _ = env.engine.start_run(proj.id, issue.id, Profile.FAST, "bare-1", "owner")
    env.webhook_pr(run.id)
    env.checks(run.id, BASE_CHECKS)
    run = env.engine.get_run(run.id)
    assert run.step("preview").status == "failed"
    assert "no Argo CD application" in (run.step("preview").last_error or "")
    assert env.promoter.requests == []


def test_agent_account_merging_is_a_violation_that_fails_the_run() -> None:
    env = build_env(agent_accounts=frozenset({"arvino-bot"}))
    run_id = at_ci(env)
    env.webhook_pr(run_id)
    env.engine.update_git_state(
        run_id, merged=True, merge_sha="d" * 40, merged_by="arvino-bot", source="test"
    )
    run = env.engine.get_run(run_id)
    assert run.status == "failed" and "policy violation" in (run.failure_reason or "")
    assert run.merge_sha is None
    assert "violation.agent_merge" in [e.type for e in env.engine.events(run_id)]


def test_tracker_writeback_and_notifier_poke_on_state_change(env: Env) -> None:
    run = env.start(Profile.FAST)
    assert any("is running" in m for _, m in env.tracker.messages)
    assert run.id in env.notifier.pokes
