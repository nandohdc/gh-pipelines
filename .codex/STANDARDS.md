# Engineering Standards

These standards are mandatory for Codex changes in this repository.

## General rules

- Keep changes minimal, explicit, and backward-compatible by default.
- Do not commit secrets, credentials, tokens, or private config.
- Preserve existing repo layout and naming conventions.
- Use ASCII unless a file already requires Unicode.

## Workflow standards

- Reusable workflows use `on: workflow_call`.
- Every `workflow_call` input includes:
  - `description`
  - `type`
  - `required`
  - `default` (when optional)
- Set workflow `permissions` to least privilege.
- Prefer shared logic in `actions/` over duplicated workflow steps.
- Keep workflow/action filenames in kebab-case.

## Script standards

- For every `shell: bash` block, start with `set -euo pipefail`.
- Avoid unsafe shell expansions; quote values and use arrays for multi-arg inputs.
- Keep step names clear and operationally specific.

## Action versioning standards

- Pin third-party actions in `.github/workflows/` and `actions/*/action.yml` to full commit SHA.
- Add a trailing semver comment for each SHA pin (`# v<semver>`).
- Keep official GitHub-owned actions tag-pinned for readability unless explicitly scoped otherwise.
- For external-facing reusable workflows, preserve compatibility expectations for callers pinned to `@v1`.

## Dependabot standards

- Keep `.github/dependabot.yml` aligned with repo structure.
- Ensure a `github-actions` update block exists for `/` and each composite action directory under `actions/`.

## Release workflow standards

- Keep `.github/workflows/release.yml` internal (do not convert it to `workflow_call`).
- Keep release trigger on `push` to `main` and derive release intent from labels on the merged PR associated with the pushed commit.
- Preserve release label contract: `release:patch`, `release:minor`, and `release:major`.
- Preserve successful no-op behavior when no release label is present on the associated merged PR.
- Keep release workflow permissions minimal and scoped to required release operations (`contents: write` and `pull-requests: read`).
- Keep release serialization (`concurrency`) enabled to prevent semver/tag races during rapid merge bursts.
- Keep tag publication idempotent when the computed tag already exists at the same target SHA.
- Continue creating immutable `vX.Y.Z` tags and moving `v1` only for backward-compatible changes.

## Documentation standards

- If workflow inputs/outputs change, update in the same PR:
  - `README.md`
  - `docs/USAGE.md`
  - `examples/*.yml` as needed
- Keep `.codex/*.md` aligned with issue tracker phase status.
- Keep `AGENTS.md` aligned with `.codex/*.md` when repository-level standards or validation rules change.

## Verification standards

- Run `actionlint .github/workflows/*.yml` before completion.
- If a single workflow is under active iteration, run `actionlint .github/workflows/<name>.yml`.
- Validate functional behavior from a consumer repo by pinning `uses:` to a branch ref when behavior changes.
- If touching `.github/workflows/docker-build.yml` hardening inputs or `actions/supply-chain-attest`, validate both default-path and hardened-path behavior in a non-production repository.
- If touching `.github/workflows/release.yml`, validate labeled and unlabeled merged-PR flows on pushes to `main`, including consecutive merge behavior.

## Branch protection standards

- Keep `main` protected with pull-request flow and required status checks.
- Keep strict status checks enabled so required checks must be green on the latest merge base.
- For this solo-maintainer repository, keep required approvals disabled (`required_approving_review_count=0`) to avoid self-approval deadlocks.

## Release standards

- Backward-compatible changes ship as new minor/patch tags and can move `v1`.
- Breaking changes require a new major; do not move `v1` for breaking changes.
