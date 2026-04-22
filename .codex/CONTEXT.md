# Project Context

## Product

`gh-pipelines` is a CI/CD library repository for reusable GitHub Actions workflows and composite actions.

## Active scope

- Reusable workflow definitions in `.github/workflows/`
- Composite actions in `actions/`
- Consumer examples in `examples/`
- Usage and contribution docs in `README.md` and `docs/`

## Tracker status

- GitHub issue `#1` (Phase 1: self-CI lint workflow) is closed.
- GitHub issue `#2` (Phase 2: SonarQube workflow) is closed.
- GitHub issue `#3` (Phase 3: Node.js CI workflow) is closed.
- GitHub issue `#4` (Phase 4: Python CI workflow) is closed.
- GitHub issue `#6` (Phase 6: self-test harness for `ci-go`/`ci-node`/`ci-python`) is closed.
- GitHub issue `#7` (Phase 7: third-party action SHA pinning + Dependabot) is closed.
- GitHub issue `#8` (Phase 8: release automation for `v1.x.y` and `v1`) is closed.
- GitHub issues `#9` to `#11` are open (next roadmap phases).
- Last tracker sync: `2026-04-22`.

## Implemented capabilities (current state)

- Reusable workflows:
  - `.github/workflows/ci-go.yml`
  - `.github/workflows/ci-node.yml`
  - `.github/workflows/ci-python.yml`
  - `.github/workflows/docker-build.yml`
  - `.github/workflows/docker-release.yml`
  - `.github/workflows/helm-deploy.yml`
  - `.github/workflows/security-scan.yml`
  - `.github/workflows/sonar-scan.yml`
- Internal repository self-CI lint workflow:
  - `.github/workflows/lint.yml`
  - triggers on `pull_request` to `main` and `push` to `main`
  - runs `actionlint` and `yamllint` actions pinned by SHA
  - uses least-privilege permissions (`contents: read`)
- Dependabot for GitHub Actions updates:
  - `.github/dependabot.yml`
  - monitors `/` and each composite action directory in `actions/`
  - weekly cadence, `dependencies` label, PR limit `10`
- Third-party action pinning baseline:
  - third-party `uses:` refs in workflows/composite actions are pinned to full commit SHA
  - pins include trailing `# v<semver>` comments
  - GitHub-owned actions stay tag-pinned per repo convention
- Internal reusable-workflow self-test harness:
  - `.github/workflows/test-callers.yml`
  - triggers on `pull_request` to `main` and `push` to `main`
  - calls `ci-go.yml`, `ci-node.yml`, and `ci-python.yml` via relative `uses:`
  - validates against fixtures in `tests/fixtures/{go,node,python}`
- Internal release automation workflow:
  - `.github/workflows/release.yml`
  - triggers on merged pull requests to `main`
  - derives semver bump from labels `release:patch|release:minor|release:major`
  - no-ops when no release label exists
  - creates immutable `vX.Y.Z`, force-moves `v1`, and creates GitHub release notes
- YAML lint config:
  - `.yamllint.yml` with line-length warnings and comments-indentation disabled
- Composite actions:
  - `actions/setup-go`
  - `actions/setup-node`
  - `actions/setup-python`
  - `actions/setup-doctl`
  - `actions/setup-helm`
  - `actions/docker-meta`

## Non-negotiable decisions

- This repo is not an app runtime; it is shared pipeline infrastructure.
- Reusable workflow and action names stay in kebab-case.
- `workflow_call` interfaces are stable contracts for consumer repos.
- Internal-only workflows (like self-CI linting) are not exposed via `workflow_call`.
- Internal-only workflows (like release automation) are not exposed via `workflow_call`.
- Workflow permissions must stay least-privilege.
- `shell: bash` scripts must start with `set -euo pipefail`.
- Third-party actions in workflows/composite actions are pinned by commit SHA with `# v<semver>` comments.
- Official GitHub-owned actions can stay tag-pinned for readability.
- Breaking interface changes require a new major release tag.

## Current constraints

- There is no full local runtime harness for GitHub-hosted execution semantics.
- Local validation is static linting (`actionlint`) plus fixture-level command checks.
- PR-level integration now includes internal caller validation through `.github/workflows/test-callers.yml`.
- Release workflow behavior requires GitHub-hosted execution for full end-to-end validation.
