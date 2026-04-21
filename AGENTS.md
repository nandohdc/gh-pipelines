# Repository Guidelines

## Project Structure & Module Organization
This repository is a shared CI/CD library, not an application runtime.

- `.github/workflows/*.yml`: reusable workflows called via `workflow_call`.
- `actions/<name>/action.yml`: composite actions used by workflows.
- `examples/*.yml`: caller workflow templates for consumer repositories.
- `docs/USAGE.md` and `docs/CONTRIBUTING.md`: canonical behavior and contribution rules.

Keep workflow/action names in kebab-case (for example, `ci-go.yml`, `setup-helm`).

## Build, Test, and Development Commands
Use `actionlint` as the primary local validation tool.

- `actionlint .github/workflows/*.yml`: lint all reusable workflows.
- `actionlint .github/workflows/ci-go.yml`: lint one workflow while iterating.

There is no local runtime test harness in this repo. Validate behavior by pointing a consumer repo to your branch ref:

```yaml
uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch
```

Release tags are the delivery mechanism:

- `git tag v1.2.0 && git push origin v1.2.0`
- `git tag -f v1 v1.2.0 && git push origin v1 --force`

## Coding Style & Naming Conventions
- Use YAML with 2-space indentation and clear step names.
- In `workflow_call`, every input must define `description`, `type`, `required`, and `default` when optional.
- Set workflow `permissions` to least privilege.
- For `shell: bash` steps, start scripts with `set -euo pipefail`.
- Prefer shared logic in `actions/` over duplicating steps across workflows.

## Testing Guidelines
- Run `actionlint` before opening a PR.
- For functional validation, run the workflow from a consumer repository using a branch-pinned `uses:` reference.
- If inputs/outputs change, update all coupled docs and examples in the same PR (`README.md`, `docs/USAGE.md`, `examples/`).

## Commit & Pull Request Guidelines
This branch currently has no local commit history, so use Conventional Commit style going forward (for example, `feat(workflows): add trivy input`).

PRs should include:
- what changed and why,
- compatibility impact (backward-compatible vs breaking),
- updated documentation/examples for any interface change,
- least-privilege permission review and secret-handling review.

Breaking workflow/action interfaces require a new major release tag; do not move `v1` for breaking changes.
