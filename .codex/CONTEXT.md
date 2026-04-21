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
- Last tracker sync: `2026-04-21`.

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
- Workflow permissions must stay least-privilege.
- `shell: bash` scripts must start with `set -euo pipefail`.
- Third-party actions in internal security/lint paths should be pinned by commit SHA.
- Breaking interface changes require a new major release tag.

## Current constraints

- There is no local runtime harness for end-to-end workflow execution.
- Local validation is primarily static linting (`actionlint`) and then consumer-repo execution against a branch ref.
