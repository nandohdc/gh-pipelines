# Contributing Guide

How to add, modify, and release workflows in this repository.

## Repository Structure

```
.github/workflows/    Reusable workflows (workflow_call triggers)
actions/              Composite actions for shared steps
docs/                 Usage and contribution guides
examples/             Copy-paste caller workflow examples
```

## Adding a New Reusable Workflow

1. **Create the workflow file** in `.github/workflows/<name>.yml`.
2. Use `workflow_call` as the trigger — NOT `push`, `pull_request`, etc.
3. Document every input with a `description`, `type`, `required`, and `default`.
4. Add a header comment block explaining the workflow purpose and an example caller snippet.
5. Use composite actions from `actions/` where possible to avoid duplication.
6. Add an example caller in `examples/<name>.yml`.
7. Update `docs/USAGE.md` with the full input/output reference.
8. Update the root `README.md` table.

### Workflow Template

```yaml
name: my-workflow

on:
  workflow_call:
    inputs:
      my-input:
        description: "What this input does."
        type: string
        required: true
    secrets:
      my-secret:
        description: "What this secret is for."
        required: false

permissions:
  contents: read

jobs:
  my-job:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # ...
```

## Adding a New Composite Action

1. Create `actions/<name>/action.yml`.
2. Use `composite` as the `runs.using` value.
3. Document every input and output.
4. Prefer wrapping well-known third-party actions with opinionated defaults.

## Modifying Existing Workflows

- **Backward-compatible changes** (new optional inputs, bug fixes): merge directly.
- **Breaking changes** (removing/renaming inputs, changing defaults that affect existing callers): bump the major version tag.

## Versioning & Releases

### Tag Strategy

We use **semantic versioning** with a floating major tag:

| Tag | Purpose | Updated when |
| --- | --- | --- |
| `v1.0.0` | Exact release | Never moves |
| `v1` | Floating major | Moves to latest `v1.x.x` |

### Creating a Release

```bash
# Tag the commit
git tag v1.2.0
git push origin v1.2.0

# Move the floating major tag
git tag -f v1 v1.2.0
git push origin v1 --force
```

### When to Bump Major

- Removing or renaming a workflow input/output
- Changing a default value that would break existing callers
- Removing a workflow or action entirely

## Testing

### Local Validation

```bash
# Install actionlint (https://github.com/rhysd/actionlint)
brew install actionlint

# Lint all workflow files
actionlint .github/workflows/*.yml
```

### Integration Testing

1. Create a test branch in a consumer repository.
2. Point the `uses:` reference to your branch: `nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch`.
3. Push and verify the workflow runs correctly.
4. Once validated, merge to main and tag.

## Code Review Checklist

- [ ] Every input has a description, type, required flag, and default (if optional)
- [ ] Permissions follow least-privilege (only what the workflow needs)
- [ ] All `shell: bash` steps use `set -euo pipefail`
- [ ] No hardcoded secrets or tokens
- [ ] Header comment with example caller
- [ ] `docs/USAGE.md` updated
- [ ] `README.md` table updated
- [ ] Example caller added/updated in `examples/`
