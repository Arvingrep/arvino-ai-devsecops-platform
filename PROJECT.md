# Arvino AI DevSecOps Platform

## Goal
Build a multi-project agile AI development, operations, and maintenance platform for a solo DevOps/Kubernetes architect.

## Platform architecture
- Plane: project portfolio, issues, cycles, roadmaps
- BMAD Method v6.12: discovery, PRD, architecture, stories, acceptance criteria
- Temporal: durable cross-project workflows, retries, timeouts, human approval signals
- OpenHands Agent Canvas and Claude Code: isolated coding-agent execution
- Gitea/GitHub: repositories, branches, pull requests, CI status
- OPA/Conftest and Trivy: deterministic policy and security gates
- Argo CD: GitOps deployment and promotion
- Grafana/Nightingale: runtime feedback and incident intake

## Workflow profiles
### Fast
Issue -> triage -> developer -> tests -> PR -> CI -> preview.

### Standard
Issue -> product/spec -> architect -> frontend/backend in parallel -> QA/security in parallel -> review -> preview -> approval -> release.

### Governed
Research -> product -> ADR/architecture -> owner approval -> implementation -> QA/security/IaC review -> staging -> owner approval -> production -> post-deploy verification.

## Non-negotiable rules
- Git and machine-readable artifacts are the source of truth; chat is not.
- Each task gets an isolated worktree/branch and sandbox.
- Agents may create branches and pull requests but may not merge protected branches.
- Production deployment, destructive database migration, Terraform apply/destroy, architecture boundaries, technology-stack changes, and spending changes require explicit owner approval.
- Agents never receive production cluster-admin, Argo CD admin, or long-lived cloud-admin credentials.
- CI policy gates cannot be bypassed by an LLM.
- Security and QA roles cannot approve their own implementation.
- No secrets are committed or printed.

## Tonight MVP acceptance criteria
1. Repository has an Architecture Decision Record and phased implementation plan.
2. Local development stack is reproducible with Docker Compose or documented substitutes.
3. Kubernetes/GitOps manifests are provided for Plane, Temporal, and the orchestration service, with secrets represented only by examples/placeholders.
4. A minimal orchestration service models project, issue, workflow run, step, approval, and artifact.
5. The service implements Fast/Standard/Governed workflow definitions and dependency-aware execution interfaces.
6. Webhook adapters or contracts exist for Plane/Gitea, CI completion, Argo CD status, and Nightingale alerts.
7. Policy-as-code examples enforce protected paths, required checks, no direct production writes, and mandatory approvals.
8. Unit/integration tests exercise dependency ordering, parallel gates, retries/idempotency, and approval blocking.
9. CI runs lint, tests, secret scanning, policy tests, and manifest validation.
10. README includes bootstrap, verification, recovery, and next-phase instructions.

## Scope guardrails
- Do not deploy or write to production clusters.
- Do not modify other repositories.
- Do not create paid cloud resources.
- Do not push to remotes or merge PRs.
- Prefer a thin integration layer over rebuilding Plane, Temporal, OpenHands, Git, or Argo CD.
