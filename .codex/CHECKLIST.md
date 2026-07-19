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
- If touching `.github/workflows/dependabot-auto-merge.yml`, confirm it remains Dependabot-only on `pull_request_target` and does not check out PR code.
- If touching `.github/workflows/integration-smoke.yml`, confirm issue creation stays scoped to this repository (explicit `--repo`) and uses `drift`/`sonar-missing` labels correctly.
- Confirm release permissions remain minimal (`contents: write` + `pull-requests: read` only where required).
- Confirm `v1` movement policy remains aligned with backward-compatibility rules.
- If touching docker hardening surfaces (`docker-build.yml` inputs or `actions/supply-chain-attest`), confirm both default and hardened paths are validated.

## Tests and checks

- Run:
  - `actionlint .github/workflows/*.yml`
- Run:
  - `actionlint -shellcheck=shellcheck .github/workflows/*.yml`
- If touching one workflow repeatedly, also run:
  - `actionlint .github/workflows/<name>.yml`
- If touching `.github/workflows/security-self-scan.yml` or `.zizmor.yml`, run:
  - `actionlint -shellcheck=shellcheck .github/workflows/*.yml`
  - `UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools UV_PYTHON_INSTALL_DIR=/tmp/uv-python uvx --from zizmor zizmor --min-severity=high --config .zizmor.yml .github/workflows/** actions/**/action.yml`
- If touching `.github/workflows/dependabot-auto-merge.yml`, validate patch/minor auto-merge and major-update comment paths in a non-production repository.
- If touching `ci-go.yml`, `ci-node.yml`, `ci-python.yml`, `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, `ci-with-sonar-python.yml`, or `ci-terraform.yml`, ensure matching fixture coverage exists under `tests/fixtures/`.
- If touching `ci-terraform.yml` or `actions/setup-terraform`, run Terraform fixture checks in `tests/fixtures/terraform/` (`fmt`, `init`, `validate`, `plan`).
- If touching `.github/workflows/release.yml`, validate in a non-production repository:
  - push-to-`main` path for commits associated with merged PRs that carry release labels,
  - no-label skip path,
  - rapid consecutive merge path (serialized release execution).
- If touching `.github/workflows/integration-smoke.yml`, validate:
  - `workflow_dispatch` success path on `main`,
  - controlled failure path opens `drift` or `sonar-missing` issues in this repository as expected,
  - first scheduled run after merge (`0 7 * * *`) is observed green.
- If touching `lint.yml`, `test-callers.yml`, or `security-self-scan.yml` job names, keep required check contexts aligned (current `main` contexts: `lint`, `test ci-go caller / lint`, `test ci-go caller / test`, `test ci-node caller / lint`, `test ci-node caller / test`, `test ci-python caller / lint`, `test ci-python caller / test`, `test ci-terraform caller / fmt`, `test ci-terraform caller / validate`, `test ci-terraform caller / plan`, `zizmor`, `shellcheck`, `test ci-with-sonar-go caller / ci / lint`, `test ci-with-sonar-node caller / ci / lint`, `test ci-with-sonar-python caller / ci / lint`).
- If behavior changed (not just docs/style), validate from a consumer repo using a branch-pinned `uses:` reference.

## Documentation and tracker sync

- If interface changed, update:
  - `README.md`
  - `docs/USAGE.md`
  - `docs/INTEGRATION_CONSUMERS.md` when consumer Sonar/canary guidance changes
  - relevant `examples/*.yml`
  - `scripts/sonar_documentation_contract_test.py` expectations when Sonar docs/examples change
- Keep `.codex/README.md` and `.codex/CONTEXT.md` aligned with current issue phase status.
- Keep `AGENTS.md` aligned with `.codex/*.md` when repository standards are adjusted.

## Completion

- Ensure the diff contains only intended files.
- Summarize what changed and which checks were executed.
