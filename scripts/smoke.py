#!/usr/bin/env python3
"""End-to-end smoke test against a running Compose stack (sandbox adapters + real OPA + Postgres).

Reads credentials from the environment (same names as .env) - it never prints them.
Exercises: Fast pipeline, Standard pipeline with owner approval, protected-path denial,
agent approval denial, webhook auth/dedup. Exits non-zero on the first failed expectation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("ARVINO_URL", "http://127.0.0.1:28080")


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env var {name} (see .env.example)")
    return v


OWNER, AGENT = env("ARVINO_OWNER_TOKEN"), env("ARVINO_AGENT_TOKEN")
PLANE, GITEA = env("ARVINO_PLANE_WEBHOOK_SECRET"), env("ARVINO_GITEA_WEBHOOK_SECRET")
CI, ARGO = env("ARVINO_CI_WEBHOOK_TOKEN"), env("ARVINO_ARGOCD_WEBHOOK_TOKEN")
SUFFIX = uuid.uuid4().hex[:6]
FAILURES: list[str] = []


def call(
    method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def api(method: str, path: str, token: str, payload: object | None = None) -> tuple[int, object]:
    return call(
        method,
        path,
        json.dumps(payload).encode() if payload is not None else None,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def hook_hmac(
    path: str, secret: str, payload: dict, header: str, extra: dict[str, str] | None = None
) -> tuple[int, object]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return call(
        "POST", path, body, {header: sig, "Content-Type": "application/json", **(extra or {})}
    )


def hook_token(path: str, token: str, payload: dict) -> tuple[int, object]:
    return call(
        "POST",
        path,
        json.dumps(payload).encode(),
        {"X-Arvino-Token": token, "Content-Type": "application/json"},
    )


def check(label: str, cond: bool, detail: object = "") -> None:
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILURES.append(label)


def get_run(run_id: str) -> dict:
    code, run = api("GET", f"/runs/{run_id}", OWNER)
    assert code == 200 and isinstance(run, dict), (code, run)
    return run


def wait_status(run_id: str, want: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    run = get_run(run_id)
    while run["status"] != want and time.time() < end:
        time.sleep(0.5)
        run = get_run(run_id)
    return run


def step(run: dict, key: str) -> dict:
    return next(s for s in run["steps"] if s["key"] == key)


def start_plane_run(label: str, plane_issue: str, body: str = "") -> str:
    payload = {
        "event": "issue",
        "action": "created",
        "data": {
            "id": plane_issue,
            "name": f"smoke {label}",
            "description_stripped": body,
            "project": f"plane-{SUFFIX}",
            "state": {"group": "unstarted"},
            "labels": [{"name": f"arvino:{label}"}],
        },
    }
    code, res = hook_hmac(
        "/webhooks/plane",
        PLANE,
        payload,
        "X-Plane-Signature",
        {"X-Plane-Delivery": f"smoke-{plane_issue}"},
    )
    assert code == 200, (code, res)
    return res["results"][0]["run_ids"][0]  # type: ignore[index]


def pipeline_to_ci(run_id: str, repo: str) -> str:
    run = get_run(run_id)
    sha = run["head_sha"]
    assert sha, "sandbox PR step should have set head_sha"
    branch = f"arvino/{run_id}/pull_request"
    num = run["pr_number"]  # sandbox PR number (derived from the run id)
    pr = {
        "action": "opened",
        "number": num,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": num,
            "head": {"ref": branch, "sha": sha},
            "base": {"ref": "main"},
            "merged": False,
        },
    }
    code, _ = hook_hmac(
        "/webhooks/gitea", GITEA, pr, "X-Gitea-Signature", {"X-Gitea-Event": "pull_request"}
    )
    assert code == 200
    return sha


def ci_green(sha: str, repo: str, checks: list[str]) -> None:
    code, res = hook_token(
        "/webhooks/ci",
        CI,
        {
            "repo": repo,
            "sha": sha,
            "checks": [{"name": c, "conclusion": "success"} for c in checks],
        },
    )
    assert code == 200, (code, res)


STATE = os.path.join(os.environ.get("ARVINO_STATE_DIR", ".runtime"), "recovery-state.json")


def ensure_ready() -> None:
    for _ in range(60):
        try:
            if call("GET", "/readyz")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    sys.exit("service not ready")


def recovery_prepare() -> int:
    """Create a Standard run that is blocked on owner approval and remember it."""
    ensure_ready()
    repo = f"acme/rec-{SUFFIX}"
    code, proj = api(
        "POST",
        "/projects",
        OWNER,
        {
            "key": f"rec-{SUFFIX}",
            "name": "Recovery",
            "plane_project_id": f"plane-{SUFFIX}",
            "repo_full_name": repo,
            "argocd_apps": {"preview": f"rec-{SUFFIX}-preview", "production": f"rec-{SUFFIX}-prod"},
        },
    )
    assert code == 201, proj
    rid = start_plane_run("standard", f"rec-{SUFFIX}")
    sha = pipeline_to_ci(rid, repo)
    ci_green(sha, repo, ["unit-tests", "lint", "secret-scan", "trivy", "conftest"])
    hook_token(
        "/webhooks/argocd",
        ARGO,
        {
            "app": f"rec-{SUFFIX}-preview",
            "revision": sha,
            "health": "Healthy",
            "sync_status": "Synced",
        },
    )
    run = wait_status(rid, "waiting_approval")
    check(
        "recovery: run is blocked on approval before restart",
        run["status"] == "waiting_approval",
        run["status"],
    )
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump({"run_id": rid, "repo": repo, "sha": sha, "suffix": SUFFIX}, fh)
    return 1 if FAILURES else 0


def recovery_verify() -> int:
    """After a service restart: state survived, still blocked, approval still works, run completes."""
    ensure_ready()
    with open(STATE) as fh:
        st = json.load(fh)
    rid, sha, suffix = st["run_id"], st["sha"], st["suffix"]
    run = get_run(rid)
    check(
        "recovery: run and its history survived the restart",
        run["status"] == "waiting_approval" and len(run["steps"]) > 5,
        run["status"],
    )
    code, rec = api("POST", "/admin/reconcile", OWNER)
    check("recovery: reconcile is safe and idempotent", code == 200 and rid in rec["advanced"], rec)  # type: ignore[index]
    run = get_run(rid)
    check(
        "recovery: reconcile did not unblock or re-run anything",
        run["status"] == "waiting_approval" and step(run, "release")["status"] == "pending",
    )
    [appr] = [a for a in run["approvals"] if a["status"] == "pending"]
    num = run["pr_number"]
    merged = {
        "action": "closed",
        "number": num,
        "repository": {"full_name": st["repo"]},
        "pull_request": {
            "number": num,
            "head": {"ref": f"arvino/{rid}/pull_request", "sha": sha},
            "base": {"ref": "main"},
            "merged": True,
            "merge_commit_sha": "d" * 40,
            "merged_by": {"login": "owner"},
        },
    }
    hook_hmac(
        "/webhooks/gitea", GITEA, merged, "X-Gitea-Signature", {"X-Gitea-Event": "pull_request"}
    )
    code, _ = api("POST", f"/approvals/{appr['id']}/decision", OWNER, {"decision": "approved"})
    check("recovery: owner approval works after restart", code == 200)
    hook_token(
        "/webhooks/argocd",
        ARGO,
        {
            "app": f"rec-{suffix}-prod",
            "revision": "d" * 40,
            "health": "Healthy",
            "sync_status": "Synced",
        },
    )
    check(
        "recovery: run completes after restart",
        wait_status(rid, "succeeded")["status"] == "succeeded",
    )
    print(f"\n{'RECOVERY CHECKS PASSED' if not FAILURES else 'FAILED: ' + '; '.join(FAILURES)}")
    return 1 if FAILURES else 0


def main() -> int:
    if "--recovery-prepare" in sys.argv:
        return recovery_prepare()
    if "--recovery-verify" in sys.argv:
        return recovery_verify()
    for _ in range(60):
        try:
            if call("GET", "/readyz")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        sys.exit("service not ready")

    repo = f"acme/smoke-{SUFFIX}"
    code, proj = api(
        "POST",
        "/projects",
        OWNER,
        {
            "key": f"smoke-{SUFFIX}",
            "name": "Smoke",
            "plane_project_id": f"plane-{SUFFIX}",
            "repo_full_name": repo,
            "argocd_apps": {
                "preview": f"smoke-{SUFFIX}-preview",
                "staging": f"smoke-{SUFFIX}-staging",
                "production": f"smoke-{SUFFIX}-prod",
            },
        },
    )
    check("create project (owner)", code == 201, proj)
    check(
        "agent token cannot create project",
        api("POST", "/projects", AGENT, {"key": "x", "name": "x"})[0] == 403,
    )
    check("anonymous request rejected", call("GET", "/projects")[0] == 401)
    check("unsigned webhook rejected", call("POST", "/webhooks/plane", b"{}")[0] == 401)

    # ---- Fast ------------------------------------------------------------------------
    rid = start_plane_run("fast", f"fast-{SUFFIX}")
    sha = pipeline_to_ci(rid, repo)
    ci_green(sha, repo, ["unit-tests", "lint", "secret-scan"])
    run = get_run(rid)
    check(
        "fast: gate passed via real OPA and preview requested",
        step(run, "merge_gate")["status"] == "succeeded"
        and step(run, "preview")["status"] == "running",
        run["status"],
    )
    code, _ = hook_token(
        "/webhooks/argocd",
        ARGO,
        {
            "app": f"smoke-{SUFFIX}-preview",
            "revision": sha,
            "health": "Healthy",
            "sync_status": "Synced",
        },
    )
    check(
        "fast: run succeeds after Argo CD reports healthy",
        wait_status(rid, "succeeded")["status"] == "succeeded",
    )
    dup = hook_hmac(
        "/webhooks/plane",
        PLANE,
        {
            "event": "issue",
            "action": "updated",
            "data": {
                "id": f"fast-{SUFFIX}",
                "name": "smoke fast",
                "project": f"plane-{SUFFIX}",
                "state": {"group": "unstarted"},
                "labels": [{"name": "arvino:fast"}],
            },
        },
        "X-Plane-Signature",
        {"X-Plane-Delivery": f"other-{SUFFIX}"},
    )
    check(
        "fast: replayed business event does not create a second run",
        dup[0] == 200 and dup[1]["results"][0]["run_ids"] == [rid],
    )  # type: ignore[index]

    # ---- Protected path denial -------------------------------------------------------
    rid2 = start_plane_run(
        "fast", f"deny-{SUFFIX}", body="sandbox-changed-paths: src/app.py,policy/authz.rego"
    )
    sha2 = pipeline_to_ci(rid2, repo)
    ci_green(sha2, repo, ["unit-tests", "lint", "secret-scan"])
    run2 = wait_status(rid2, "failed")
    check(
        "protected path change is denied by policy gate and run fails",
        run2["status"] == "failed"
        and "protected from agent changes" in (step(run2, "merge_gate")["last_error"] or ""),
        run2.get("failure_reason"),
    )
    check("denied run never requested a deployment", step(run2, "preview")["status"] == "cancelled")

    # ---- Standard + approval -----------------------------------------------------------
    rid3 = start_plane_run("standard", f"std-{SUFFIX}")
    sha3 = pipeline_to_ci(rid3, repo)
    ci_green(sha3, repo, ["unit-tests", "lint", "secret-scan", "trivy", "conftest"])
    hook_token(
        "/webhooks/argocd",
        ARGO,
        {
            "app": f"smoke-{SUFFIX}-preview",
            "revision": sha3,
            "health": "Healthy",
            "sync_status": "Synced",
        },
    )
    run3 = wait_status(rid3, "waiting_approval")
    check(
        "standard: run blocks on owner approval",
        run3["status"] == "waiting_approval" and step(run3, "release")["status"] == "pending",
        run3["status"],
    )
    [appr] = [a for a in run3["approvals"] if a["status"] == "pending"]
    code, res = api("POST", f"/approvals/{appr['id']}/decision", AGENT, {"decision": "approved"})
    check("agent cannot approve (403)", code == 403, (code, res))
    check("run still blocked after agent attempt", get_run(rid3)["status"] == "waiting_approval")
    num = run3["pr_number"]
    merged = {
        "action": "closed",
        "number": num,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": num,
            "head": {"ref": f"arvino/{rid3}/pull_request", "sha": sha3},
            "base": {"ref": "main"},
            "merged": True,
            "merge_commit_sha": "c" * 40,
            "merged_by": {"login": "owner"},
        },
    }
    hook_hmac(
        "/webhooks/gitea", GITEA, merged, "X-Gitea-Signature", {"X-Gitea-Event": "pull_request"}
    )
    code, res = api(
        "POST",
        f"/approvals/{appr['id']}/decision",
        OWNER,
        {"decision": "approved", "reason": "smoke"},
    )
    check("owner approves (200)", code == 200, (code, res))
    run3 = get_run(rid3)
    check(
        "production deploy requested only after approval",
        step(run3, "release")["status"] == "running",
        run3["status"],
    )
    hook_token(
        "/webhooks/argocd",
        ARGO,
        {
            "app": f"smoke-{SUFFIX}-prod",
            "revision": "c" * 40,
            "health": "Healthy",
            "sync_status": "Synced",
        },
    )
    check(
        "standard: run succeeds after production converges",
        wait_status(rid3, "succeeded")["status"] == "succeeded",
    )

    code, ev = api("GET", f"/runs/{rid3}/events", OWNER)
    types = [e["type"] for e in ev]  # type: ignore[union-attr]
    check(
        "audit log records the denied agent approval",
        "approval.denied" in types and "approval.approved" in types,
        types,
    )

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILED: ' + '; '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
