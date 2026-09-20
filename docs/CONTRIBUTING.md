# Contributing Guide

How to add, modify, and release workflows in this repository.

## Repository Structure

```
.github/workflows/    Reusable workflows (workflow_call triggers)
actions/              Composite actions for shared steps
docs/                 Usage and contribution guides
examples/             Copy-paste Caller workflow examples
```

## Adding a New Reusable Workflow

1. **Create the workflow file** in `.github/workflows/<name>.yml`.
2. Use `workflow_call` as the trigger — NOT `push`, `pull_request`, etc.
3. Document every input with a `description`, `type`, `required`, and `default`.
4. Add a header comment block explaining the workflow purpose and an example Caller workflow snippet.
5. Use composite actions from `actions/` where possible to avoid duplication.
6. Add an example Caller workflow in `examples/<name>.yml`.
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
- **Breaking changes** (removing/renaming inputs, changing defaults that affect existing Caller workflows): bump the major version tag.

## Versioning & Releases

### Tag Strategy

We use **semantic versioning** with a floating major tag:

| Tag | Purpose | Updated when |
| --- | --- | --- |
| `v1.0.0` | Exact release | Never moves |
| `v1` | Floating major | Moves to latest `v1.x.x` |

### Creating a Release

Primary flow (automated):

1. Add exactly one label to the PR before merge:
   - `release:patch`
   - `release:minor`
   - `release:major`
2. Merge the PR into `main`.
3. `.github/workflows/release.yml` computes the next semver from existing `v*.*.*` tags.
4. The workflow creates and pushes the new `vX.Y.Z` tag, force-moves `v1`, and publishes a GitHub Release with generated notes.

No release label means no release action (workflow exits successfully without tagging).

#### Manual release (fallback)

```bash
# Tag the commit
git tag vX.Y.Z
git push origin vX.Y.Z

# Move the floating major tag
git tag -f v1 vX.Y.Z
git push origin v1 --force
```

### When to Bump Major

- Removing or renaming a workflow input/output
- Changing a default value that would break existing Caller workflows
- Removing a workflow or action entirely

## Testing

### Local Validation

```bash
# Install actionlint (https://github.com/rhysd/actionlint)
brew install actionlint

# Lint all workflow files
actionlint .github/workflows/*.yml
```

### Running Self-Tests

This repository includes an internal workflow (`.github/workflows/test-callers.yml`) that calls
`ci-go.yml`, `ci-node.yml`, `ci-python.yml`, and the `ci-with-sonar-*` bundles against fixtures in
`tests/fixtures/`.

- `tests/fixtures/go/`: minimal Go module used by `ci-go`.
- `tests/fixtures/node/`: minimal Node package used by `ci-node`.
- `tests/fixtures/python/`: minimal Python package used by `ci-python`.

When adding inputs or behavior to any `ci-*` workflow:

1. Update the corresponding fixture so lint/test expectations still reflect real Caller workflow behavior.
2. Keep fixture dependencies minimal and deterministic (`npm ci`, pinned/locked deps where applicable).
3. Run `actionlint .github/workflows/*.yml` locally before opening a PR.
4. Verify the `test-callers` workflow is green on your branch PR before merge.

For Sonar bundle tests, `test-callers.yml` sets `__test_skip_sonar_call: true` and passes a dummy
secret value to satisfy the required `DOPPLER_TOKEN` contract without making live Doppler calls.

### Integration Testing

1. Create a test branch in a Workflow consumer repository.
2. In its Caller workflow, point the Workflow invocation at your branch: `nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch`.
3. Push and verify the workflow runs correctly.
4. Once validated, merge to main and tag.

## Code Review Checklist

- [ ] Every input has a description, type, required flag, and default (if optional)
- [ ] Permissions follow least-privilege (only what the workflow needs)
- [ ] All `shell: bash` steps use `set -euo pipefail`
- [ ] No hardcoded secrets or tokens
- [ ] Header comment with example Caller workflow
- [ ] `docs/USAGE.md` updated
- [ ] `README.md` table updated
- [ ] Example Caller workflow added/updated in `examples/`

## Branch Protection & Branch Hygiene

- `main` is protected: all changes must go through a pull request and required status checks must pass.
- Required approving reviews are currently set to `0` to avoid solo-maintainer self-approval deadlocks.
- Required checks must be green before merge.
- Head branches are auto-deleted on merge.
- Maintainers should sweep leftover remote branches weekly and remove stale merged branches.

## Dependabot Auto-Merge Policy

- `.github/workflows/dependabot-auto-merge.yml` handles Dependabot pull requests using `pull_request_target` without checking out untrusted code.
- Patch and minor dependency updates (`version-update:semver-patch` and `version-update:semver-minor`) automatically:
  - add `release:patch`,
  - enable auto-merge with squash,
  - and merge only after required checks are green via branch protection.
- Major dependency updates (`version-update:semver-major`) are not auto-merged; the workflow posts: `Major update; maintainer review required.`
