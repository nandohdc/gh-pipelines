# Delivery Checklist

Use this checklist before finishing any Codex task in this repository.

## Scope and boundaries

- Confirm the change fits this repo purpose (shared workflows/actions, examples, docs).
- Confirm reusable interface changes are intentional and backward-compatible unless explicitly scoped as breaking.
- Confirm no secrets or sensitive values are added to tracked files.

## Behavior and safety

- Confirm workflow permissions are least-privilege.
- Confirm every `shell: bash` script uses `set -euo pipefail`.
- Confirm reusable workflow inputs have complete interface metadata.
- Confirm internal-only workflows are not accidentally exposed as `workflow_call`.
- Confirm release workflow keeps expected label handling (`release:patch|release:minor|release:major`) and no-label skip behavior for commits on `main` associated with merged PRs.
- Confirm release workflow keeps serialization (`concurrency`) to avoid overlapping semver/tag races.
- Confirm release workflow tag creation remains idempotent when the computed tag already exists at the intended target SHA.
- Confirm third-party action refs are pinned to full SHA with trailing `# v<semver>` comments.
- Confirm GitHub-owned action refs that should remain tag-pinned were not unintentionally converted.
- Confirm `.github/dependabot.yml` still covers `/` and each composite action directory under `actions/`.
- Confirm release permissions remain minimal (`contents: write` + `pull-requests: read` only where required).
- Confirm `v1` movement policy remains aligned with backward-compatibility rules.
- If touching docker hardening surfaces (`docker-build.yml` inputs or `actions/supply-chain-attest`), confirm both default and hardened paths are validated.

## Tests and checks

- Run:
  - `actionlint .github/workflows/*.yml`
- If touching one workflow repeatedly, also run:
  - `actionlint .github/workflows/<name>.yml`
- If touching `ci-go.yml`, `ci-node.yml`, `ci-python.yml`, or `ci-terraform.yml`, ensure matching fixture coverage exists under `tests/fixtures/`.
- If touching `ci-terraform.yml` or `actions/setup-terraform`, run Terraform fixture checks in `tests/fixtures/terraform/` (`fmt`, `init`, `validate`, `plan`).
- If touching `.github/workflows/release.yml`, validate in a non-production repository:
  - push-to-`main` path for commits associated with merged PRs that carry release labels,
  - no-label skip path,
  - rapid consecutive merge path (serialized release execution).
- If behavior changed (not just docs/style), validate from a consumer repo using a branch-pinned `uses:` reference.

## Documentation and tracker sync

- If interface changed, update:
  - `README.md`
  - `docs/USAGE.md`
  - relevant `examples/*.yml`
- Keep `.codex/README.md` and `.codex/CONTEXT.md` aligned with current issue phase status.
- Keep `AGENTS.md` aligned with `.codex/*.md` when repository standards are adjusted.

## Completion

- Ensure the diff contains only intended files.
- Summarize what changed and which checks were executed.
