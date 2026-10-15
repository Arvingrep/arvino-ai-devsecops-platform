# Custom Workflow Definitions

Drop a YAML file here to propose a new workflow profile.  Every file must
contain exactly one workflow definition in the same shape as the built-in
definitions (see `src/arvino/workflows/definitions/` for examples).

## Required fields

```yaml
profile: fast | standard | governed
description: one-line summary
steps:
  - key: my_step
    title: Human-readable title
    kind: agent | ci | gate | approval | deploy
    # depends_on: [other_step_key]
    # role: triage | ...          # required for agent steps
    # approval_kind: architecture | production-deploy | ...  # required for approval steps
    # environment: preview | staging | production             # required for deploy steps
    # required_checks: [check-name, ...]                      # required for ci and gate steps
```

## Validation

Every PR that adds or changes a file under `contrib/workflows/` is
automatically validated for:

- **DAG structure** — acyclic, no unknown dependencies, no duplicate keys
- **Step kinds** — correct roles, approval kinds, environments, and required checks
- **Safety constraints** — deploys must descend from CI+gate steps,
  production requires an owner approval, governed profiles need
  architecture approval before implementation and staging before production

A PR that weakens any gate is **rejected** by CI.