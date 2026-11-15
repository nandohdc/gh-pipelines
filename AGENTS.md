# Repository Guidelines

## Project Structure & Module Organization
This repository is a shared CI/CD library, not an application runtime.

- `.github/workflows/*.yml`: reusable workflows (`workflow_call`) plus internal repo workflows (`lint.yml`, `test-callers.yml`, `release.yml`, `security-self-scan.yml`, `dependabot-auto-merge.yml`, `integration-smoke.yml`).
- `actions/<name>/action.yml`: composite actions used by workflows.
- `.github/dependabot.yml`: GitHub Actions dependency update policy for workflows and composite actions.
- `.zizmor.yml`: policy configuration for internal workflow security scans.
- `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`: repository governance and contribution templates.
- `examples/*.yml`: caller workflow templates for consumer repositories.
- `tests/fixtures/{go,node,python,terraform}`: minimal fixtures used by internal self-tests.
- `docs/USAGE.md`, `docs/CONTRIBUTING.md`, `SECURITY.md`, and `CHANGELOG.md`: canonical behavior and repository policy docs.

Keep workflow/action names in kebab-case (for example, `ci-go.yml`, `setup-helm`).

## Build, Test, and Development Commands
Use `actionlint` as the primary local validation tool.

- `actionlint .github/workflows/*.yml`: lint all workflow files.
- `actionlint .github/workflows/ci-go.yml`: lint one workflow while iterating.
- `actionlint -shellcheck=shellcheck .github/workflows/*.yml`: lint inline bash with shellcheck integration.
- `UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools UV_PYTHON_INSTALL_DIR=/tmp/uv-python uvx --from zizmor zizmor --min-severity=high --config .zizmor.yml .github/workflows/** actions/**/action.yml`: run the security self-scan gate locally.

There is no full local runtime harness for GitHub-hosted execution semantics. For functional validation:

- Use the internal self-test workflow (`.github/workflows/test-callers.yml`) on PRs to `main` / pushes to `main`.
- For consumer-level validation, point a downstream repo to your branch ref.
- `main` is protected: direct pushes are rejected; ship changes through PRs with required checks.
- PR approvals are currently optional (`required_approving_review_count=0`) to avoid blocking solo-maintainer merges while preserving CI safeguards.
- For release automation changes (`.github/workflows/release.yml`), validate push-to-`main` behavior for:
  - commits associated with merged PRs carrying `release:patch|release:minor|release:major`,
  - commits associated with merged PRs with no release label (no-op path),
  - merge commits where commit-to-PR lookup needs fallback to parse `Merge pull request #...`,
  - rapid consecutive merges (release serialization behavior).

```yaml
uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch
```

Release tags are the delivery mechanism:

- `git tag vX.Y.Z && git push origin vX.Y.Z`
- `git tag -f v<N> vX.Y.Z && git push origin v<N> --force` (matching the tag's own major `N`)

## Coding Style & Naming Conventions
- Use YAML with 2-space indentation and clear step names.
- In `workflow_call`, every input must define `description`, `type`, `required`, and `default` when optional.
- Repository-owned jobs and internal reusable-workflow callers explicitly select GitHub-hosted `ubuntu-latest`; downstream `runner-label` support remains available.
- Sonar workflows are deprecated; preserve existing `v1` behavior while consumers migrate.
- Set workflow `permissions` to least privilege.
- For `shell: bash` steps, start scripts with `set -euo pipefail`.
- Prefer shared logic in `actions/` over duplicating steps across workflows.
- Pin third-party action refs in workflows/composite actions to full commit SHA with trailing `# v<semver>` comments.
- Keep official GitHub-owned action refs tag-pinned for readability unless a change explicitly requires otherwise.

## Testing Guidelines
- Run `actionlint` before opening a PR.
- Static workflow contract tests live under `scripts/*_workflow_contract_test.py`; `scripts/helm_deploy_workflow_contract_test.py` validates `helm-deploy.yml@v2` default-branch, same-repo-PR, and protected-manual (`allow-manual-deploy` + `environment` + `scripts/helm_environment.py`) policies and executes its step scripts with stubbed `doctl`/`helm`/`kubectl` binaries; `scripts/sonar_documentation_contract_test.py` guards the Sonar consumer docs/examples contract; `scripts/release_test.py` exercises `scripts/release.py` against temporary bare Git remotes and scoped GitHub adapters (`python3 -m unittest -v scripts/release_test.py`); `scripts/candidate_smoke_test.py` exercises `scripts/candidate_smoke.py` (`python3 -m unittest -v scripts/candidate_smoke_test.py`); `lint.yml` runs the hosted-runner, standalone Sonar workflow, language bundle, Sonar documentation, Terraform documentation, Helm deploy policy, and consumer terminology contract tests, `contract_lib_test.py`, `canary_probe_test.py`, `candidate_smoke_test.py`, and `release_test.py`.
- If you changed `ci-go.yml`, `ci-node.yml`, `ci-python.yml`, `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, `ci-with-sonar-python.yml`, or `ci-terraform.yml`, ensure matching fixture coverage under `tests/fixtures/`.
- If you changed `ci-terraform.yml` or `actions/setup-terraform`, validate Terraform fixture behavior (`fmt`, `init`, `validate`, `plan`) in `tests/fixtures/terraform/`.
- If you changed `docker-build.yml` hardening inputs or `actions/supply-chain-attest`, verify both default-path behavior (all hardening flags false) and hardened-path behavior in a non-production repository. The `sign-image` and `attest-sbom` jobs must keep their in-workflow self-verify steps (`cosign verify` / `cosign verify-attestation`) so they fail closed when the floating `supply-chain-attest@v1` ref drifts; the `release.yml` guard that requires `attest-sbom` in the action manifest must stay in sync. The `attest-sbom` path pins the legacy `.att` bundle format because cosign v3's default new bundle format (OCI 1.1 referrers) does not round-trip reliably on GHCR: `cosign attest` (action) uses `--new-bundle-format=false --use-signing-config=false` (both required together — cosign v3 rejects `--new-bundle-format=false` alone) and `cosign verify-attestation` (workflow) uses `--new-bundle-format=false`. Keep these in sync (issue #104).
- If you changed `security-self-scan.yml` or `.zizmor.yml`, run:
  - `actionlint -shellcheck=shellcheck .github/workflows/*.yml`
  - `UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools UV_PYTHON_INSTALL_DIR=/tmp/uv-python uvx --from zizmor zizmor --min-severity=high --config .zizmor.yml .github/workflows/** actions/**/action.yml`
- If you changed `dependabot-auto-merge.yml`, validate Dependabot-only behavior on `pull_request_target`, patch/minor auto-merge enablement, and major-update comment path.
- If you changed `release.yml` or `scripts/release.py`, verify:
  - merged-PR label handling (`release:patch|release:minor|release:major`) on push to `main` and `patch`/`minor` on `release/v<N>` maintenance branches,
  - no-label skip path and `release/v<N>` initial branch-creation skip path,
  - merge-commit fallback PR detection path (`base.ref` matched to branch),
  - exact-commit promotion gating (`verify_promotion_gate`, `REQUIRED_CI_CHECKS`, `app.slug == "github-actions"`, workflow-path correlation excluding `release.yml`, and exact-SHA `Candidate smoke` evidence / `verify-gate` CLI),
  - lineage isolation (interleaved old/new-major releases without cross-lineage floating-tag movement, and immutable exact-tag conflict rejection),
  - release serialization, `workflow_dispatch` retry recovery, and idempotent tag handling.
- If you changed `integration-smoke.yml`, verify:
  - `workflow_dispatch` succeeds on `main` (for example `consumer=all`),
  - controlled failing path opens a `drift`-labeled issue in a non-production repository,
  - first scheduled run after merge (`cron: 0 7 * * *`) is green.
- If you changed `lint.yml`, `test-callers.yml`, or `security-self-scan.yml` job names, keep protected required-check contexts aligned (current `main` contexts: `lint`, `test ci-go caller / lint`, `test ci-go caller / test`, `test ci-node caller / lint`, `test ci-node caller / test`, `test ci-python caller / lint`, `test ci-python caller / test`, `test ci-terraform caller / fmt`, `test ci-terraform caller / validate`, `test ci-terraform caller / plan`, `zizmor`, `shellcheck`, `test ci-with-sonar-go caller / ci / lint`, `test ci-with-sonar-node caller / ci / lint`, `test ci-with-sonar-python caller / ci / lint`).
- Ensure `test-callers.yml` remains green for PRs that touch reusable CI workflows.
- For functional validation, run the workflow from a consumer repository using a branch-pinned `uses:` reference.
- If inputs/outputs change, update all coupled docs and examples in the same PR (`README.md`, `docs/USAGE.md`, `docs/INTEGRATION_CONSUMERS.md`, `examples/`) and keep `scripts/sonar_documentation_contract_test.py` green when Sonar consumer guidance changes.
- If adding a new composite action directory under `actions/`, add/update its matching block in `.github/dependabot.yml`.
- Keep `AGENTS.md` and `.codex/*.md` aligned when repository standards or tracker status change.

## Commit & Pull Request Guidelines
Use Conventional Commit style (for example, `feat(workflows): add trivy input`).

PRs should include:
- what changed and why,
- compatibility impact (backward-compatible vs breaking),
- updated documentation/examples for any interface change,
- protected-check readiness for merge to `main` (approvals optional under current branch protection),
- least-privilege permission review and secret-handling review.

Breaking workflow/action interfaces require a new major release tag; do not move `v1` for breaking changes.
