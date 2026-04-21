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

- Pin internal lint/security third-party actions to commit SHA when feasible.
- For external-facing reusable workflows, preserve compatibility expectations for callers pinned to `@v1`.

## Documentation standards

- If workflow inputs/outputs change, update in the same PR:
  - `README.md`
  - `docs/USAGE.md`
  - `examples/*.yml` as needed
- Keep `.codex/*.md` aligned with issue tracker phase status.

## Verification standards

- Run `actionlint .github/workflows/*.yml` before completion.
- If a single workflow is under active iteration, run `actionlint .github/workflows/<name>.yml`.
- Validate functional behavior from a consumer repo by pinning `uses:` to a branch ref when behavior changes.

## Release standards

- Backward-compatible changes ship as new minor/patch tags and can move `v1`.
- Breaking changes require a new major; do not move `v1` for breaking changes.
