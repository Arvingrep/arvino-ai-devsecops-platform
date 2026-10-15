# ADR 0008: BMAD artifacts as typed ArtifactSpec in the run ledger

Status: Accepted (2026-10-15)

## Context

BMAD v6.12 is installed in the firebird-sea repository and produces structured
planning and implementation outputs under `_bmad-output/`:

```
_bmad-output/planning-artifacts/prds/<dir>/prd.md
_bmad-output/planning-artifacts/architecture/<dir>/<name>.md
_bmad-output/planning-artifacts/briefs/<dir>/brief.md
_bmad-output/implementation-artifacts/spec-<N>-<M>-<slug>.md
```

When the orchestration engine dispatches a `product` (spec) or `architect`
(architecture/adr) step, the executor writes these files into the branch and
then posts a `StepOutcome`.  Without any registration hook, the downstream
steps (architect, frontend, backend, developer) receive no typed reference to
the PRD or architecture document — they must either re-discover them by
convention or the executor embeds the full text in its summary output.

Referencing by re-discovery is fragile (path changes, multiple PRD files) and
embedding full text in the output column is not immutable nor queryable.

## Decision

1. **`BmadArtifactKind` enum** is added to `arvino.domain`:
   - `bmad-prd` — Product Requirements Document
   - `bmad-architecture` — Architecture spine / ADR
   - `bmad-story` — Individual user story / acceptance criteria file
   - `bmad-brief` — Project brief / discovery document

2. **`arvino.bmad` module** provides factory functions (`prd_artifact`,
   `architecture_artifact`, `story_artifact`, `brief_artifact`) that produce
   correctly typed `ArtifactSpec` objects with a canonical URI:

   ```
   git://<repo_full_name>/<git_ref>/<path_in_repo>
   ```

   e.g. `git://Arvingrep/firebird-sea/main/_bmad-output/planning-artifacts/prds/prd.md`

3. **Executors are expected** to include the relevant `ArtifactSpec` objects in
   `StepOutcome.artifacts` when a product or architect step completes:

   ```python
   from arvino.bmad import prd_artifact, architecture_artifact
   outcome = StepOutcome(
       status="succeeded",
       artifacts=[
           prd_artifact(repo, branch_ref, "_bmad-output/planning-artifacts/prds/prd.md",
                        sha256=sha256_of_blob),
       ],
   )
   ```

4. **`Engine._upstream_artifacts`** is changed from direct-dep-only to
   **transitive ancestor** lookup.  The original direct-dep design silently
   dropped BMAD artifacts when a non-agent step (APPROVAL, CI, GATE) sat
   between the producer and consumer.  In Standard/Governed workflows,
   `architecture_approval` separates `adr` from `implementation`; transitive
   traversal ensures `implementation` receives the architecture artifact even
   though `architecture_approval` produces none.

   The change is safe because:
   - Artifacts are immutable once registered (uniqueness on `(run_id, step_key, name)`).
   - All 358 existing tests pass without modification.
   - A step can still filter by kind/name if it only wants specific context.

5. **`filter_bmad(upstream, kind)`** utility is provided in `arvino.bmad` for
   executors that want to extract only a specific BMAD kind from `upstream_artifacts`.

## Artifact naming convention

The ledger's uniqueness constraint is `(run_id, step_key, name)`.

| BMAD output                                         | Recommended `name` |
|-----------------------------------------------------|--------------------|
| `prds/.../prd.md`                                   | `"prd"`            |
| `architecture/.../ARCHITECTURE-SPINE.md`            | `"ARCHITECTURE-SPINE"` (stem) |
| `briefs/.../brief.md`                               | `"brief"`          |
| `implementation-artifacts/spec-N-M-slug.md`         | `"N-M"` (story id) |

The factory functions derive the `name` automatically from the path; pass
`name=` explicitly to override.

## Consequences

- Executors that write BMAD artifacts (product, architect roles) must include
  them in `StepOutcome.artifacts`.  Executors that do not register artifacts
  continue to work unchanged.
- The architecture and developer steps receive typed references they can resolve
  via `git show <ref>:<path>` without any path-discovery logic.
- Artifact URIs are git-scheme logical pointers; they are never fetched by the
  engine (that is the executor's responsibility).
- The transitive upstream change is backward-compatible; existing tests and the
  existing `pull-request` / `note` artifact kinds are unaffected.
