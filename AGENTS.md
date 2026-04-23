# Repository Guidelines

## Project Structure & Module Organization
This repository is a shared CI/CD library, not an application runtime.

- `.github/workflows/*.yml`: reusable workflows (`workflow_call`) plus internal repo workflows (`lint.yml`, `test-callers.yml`, `release.yml`).
- `actions/<name>/action.yml`: composite actions used by workflows.
- `.github/dependabot.yml`: GitHub Actions dependency update policy for workflows and composite actions.
- `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`: repository governance and contribution templates.
- `examples/*.yml`: caller workflow templates for consumer repositories.
- `tests/fixtures/{go,node,python}`: minimal fixtures used by internal self-tests.
- `docs/USAGE.md`, `docs/CONTRIBUTING.md`, `SECURITY.md`, and `CHANGELOG.md`: canonical behavior and repository policy docs.

Keep workflow/action names in kebab-case (for example, `ci-go.yml`, `setup-helm`).

## Build, Test, and Development Commands
Use `actionlint` as the primary local validation tool.

- `actionlint .github/workflows/*.yml`: lint all workflow files.
- `actionlint .github/workflows/ci-go.yml`: lint one workflow while iterating.

There is no full local runtime harness for GitHub-hosted execution semantics. For functional validation:

- Use the internal self-test workflow (`.github/workflows/test-callers.yml`) on PRs to `main` / pushes to `main`.
- For consumer-level validation, point a downstream repo to your branch ref.
- For release automation changes (`.github/workflows/release.yml`), validate push-to-`main` behavior for:
  - commits associated with merged PRs carrying `release:patch|release:minor|release:major`,
  - commits associated with merged PRs with no release label (no-op path),
  - rapid consecutive merges (release serialization behavior).

```yaml
uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch
```

Release tags are the delivery mechanism:

- `git tag vX.Y.Z && git push origin vX.Y.Z`
- `git tag -f v1 vX.Y.Z && git push origin v1 --force`

## Coding Style & Naming Conventions
- Use YAML with 2-space indentation and clear step names.
- In `workflow_call`, every input must define `description`, `type`, `required`, and `default` when optional.
- Set workflow `permissions` to least privilege.
- For `shell: bash` steps, start scripts with `set -euo pipefail`.
- Prefer shared logic in `actions/` over duplicating steps across workflows.
- Pin third-party action refs in workflows/composite actions to full commit SHA with trailing `# v<semver>` comments.
- Keep official GitHub-owned action refs tag-pinned for readability unless a change explicitly requires otherwise.

## Testing Guidelines
- Run `actionlint` before opening a PR.
- If you changed `ci-go.yml`, `ci-node.yml`, or `ci-python.yml`, ensure matching fixture coverage under `tests/fixtures/`.
- If you changed `docker-build.yml` hardening inputs or `actions/supply-chain-attest`, verify both default-path behavior (all hardening flags false) and hardened-path behavior in a non-production repository.
- If you changed `release.yml`, verify:
  - merged-PR label handling (`release:patch|release:minor|release:major`) on push to `main`,
  - no-label skip path,
  - release serialization and idempotent tag handling.
- Ensure `test-callers.yml` remains green for PRs that touch reusable CI workflows.
- For functional validation, run the workflow from a consumer repository using a branch-pinned `uses:` reference.
- If inputs/outputs change, update all coupled docs and examples in the same PR (`README.md`, `docs/USAGE.md`, `examples/`).
- If adding a new composite action directory under `actions/`, add/update its matching block in `.github/dependabot.yml`.
- Keep `AGENTS.md` and `.codex/*.md` aligned when repository standards or tracker status change.

## Commit & Pull Request Guidelines
Use Conventional Commit style (for example, `feat(workflows): add trivy input`).

PRs should include:
- what changed and why,
- compatibility impact (backward-compatible vs breaking),
- updated documentation/examples for any interface change,
- least-privilege permission review and secret-handling review.

Breaking workflow/action interfaces require a new major release tag; do not move `v1` for breaking changes.
