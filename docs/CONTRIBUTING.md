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

- **Backward-compatible changes** (new optional inputs, bug fixes): merge to `main` (or the target `release/v<N>` Maintenance branch) with `release:patch` or `release:minor`.
- **Breaking changes** (removing/renaming inputs, changing defaults that affect existing Caller workflows, or narrowing deployment policies): must start a new Major lineage via `release:major` on `main`. Breaking default changes cannot be released through the existing `v1` lineage.

## Versioning & Releases

### Tag Strategy

We use **semantic versioning** with independent compatible **Major lineages** and per-lineage **Floating major tags**:

| Tag | Purpose | Updated when |
| --- | --- | --- |
| `v1.10.0`, `v2.0.0` | Immutable **Exact release tag** | Never moves (conflicts fail release) |
| `v1`, `v2`, … | **Floating major tag** | Moves only to latest `vM.x.y` within Major lineage `M` |

### Creating a Release

Primary flow (automated):

1. Add exactly one label to the PR before merge:
   - `release:patch` — bumps patch within the branch's target Major lineage (`vM.Y.Z -> vM.Y.(Z+1)`) and moves `vM`
   - `release:minor` — bumps minor within the branch's target Major lineage (`vM.Y.Z -> vM.(Y+1).0`) and moves `vM`
   - `release:major` — allowed on `main` only; creates `v(M+1).0.0` and `v(M+1)` while leaving prior `vM` unchanged
2. Merge the PR into `main` (or into a `release/v<N>` Maintenance branch for prior-major `patch`/`minor` fixes).
3. `.github/workflows/release.yml` delegates to `scripts/release.py` to resolve merged-PR labels, enforce release guards, plan the target Major lineage, compute the next semver from `v<M>.*.*` tags in that lineage, and publish tags and release notes.
4. The workflow creates and pushes the immutable `vX.Y.Z` tag, force-moves only the matching `v<M>` Floating major tag, and publishes a GitHub Release with generated notes.

No release label means no release action (workflow exits successfully without tagging).

#### Retries, partial failures, and recovery

- Every `release.yml` execution prints a `Release recovery summary:` line containing the **Release intent** (`PR#<n>@<sha>`), target `Major lineage`, chosen `Exact release tag`, completed effects (`exact_tag`, `floating_tag`, `github_release`), and retry outcome (`published:published`, `published:resumed`, or `noop:already_released`) without exposing credentials.
- Retrying an already-completed release is a safe no-op even if PR labels are modified afterward.
- Retrying after an interruption (for example after pushing `vX.Y.Z` or moving `v<N>` but before creating the GitHub Release) reuses compatible existing tags/releases and completes the original `vX.Y.Z` without allocating another version.
- Stale retries of older releases never move a lineage's `Floating major tag` backward past a newer descendant commit (`floating_tag:kept_newer`).
- Conflicting immutable tags, duplicate semver tags on the same commit, or mismatched GitHub Release target SHAs fail visibly (`::error::`) without force-overwriting; inspect the conflicting tag/release target reported in the error and `Release recovery summary:` before resolving and re-running the workflow.

#### Maintaining an existing major after a new major exists

Once a new Major lineage (for example `v2.0.0`) exists on `main`, publish `patch` or `minor` fixes for an older Major lineage (such as `v1`) through a dedicated Maintenance branch `release/v<N>`:

1. Create `release/v1` from the latest compatible `v1.x.y` commit (before `v2` breaking changes landed on `main`) and apply matching branch-protection rules and required status checks (`lint`, `test-callers`, `security-self-scan`) to `release/v1` on GitHub.
   - Pushing the new `release/v1` branch (`github.event.created == true`) is automatically skipped by `release.yml` so the existing commit is not re-tagged.
2. Author or cherry-pick the compatible fix in a PR targeting `release/v1`, add `release:patch` or `release:minor`, and merge once required checks pass (`release:major` is rejected on `release/v<N>` branches).
3. `release.yml` resolves `target_major = 1` from `release/v1`, computes the next version strictly from `v1.*.*` tags, moves only `v1`, and leaves `v2` unchanged.

#### Manual release (fallback)

```bash
# Replace <N> with the Exact release tag's own major number (never move v1 for v2.x.y)
git tag vX.Y.Z
git push origin vX.Y.Z

# Move only the matching Floating major tag
git tag -f v<N> vX.Y.Z
git push origin v<N> --force
```

### When to Bump Major

- Removing or renaming a workflow input/output
- Changing a default value or policy that would break existing Caller workflows
- Removing a workflow or action entirely

When cutting a new Major lineage (for example `v2`), update internal composite-action references inside reusable workflows (`uses: nandohdc/gh-pipelines/actions/<name>@v<N>`) on `main` to `@v2` so `@v2` callers do not transitively execute `@v1` composite actions.

## Testing

### Local Validation

```bash
# Install actionlint (https://github.com/rhysd/actionlint)
brew install actionlint

# Lint all workflow files
actionlint .github/workflows/*.yml

# Run release behavior tests against temporary bare Git remotes and scoped adapters
python3 -m unittest -v scripts/release_test.py
```

If `pytest` is installed (as in `.github/workflows/lint.yml`), you can also run:

```bash
python3 -m pytest scripts/release_test.py -q
```

### Running Self-Tests

Repository-owned jobs and local reusable-workflow callers select GitHub-hosted `ubuntu-latest`; `RUNNER_LABEL` is a downstream-only opt-in. Sonar workflows are deprecated and the bundle fixtures below retain existing required check contexts without executing Sonar.

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
