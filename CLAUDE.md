# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`gh-pipelines` is **not** an application. It is a library of reusable GitHub Actions artifacts consumed by other repositories in the `nandohdc` ecosystem via `uses: nandohdc/gh-pipelines/...@v1`. There is no build, no runtime, and no test suite beyond YAML linting — correctness is validated by (a) `actionlint` + `yamllint` (via `.github/workflows/lint.yml`), (b) `zizmor` + `actionlint -shellcheck=shellcheck` on workflows and composite actions (via `.github/workflows/security-self-scan.yml`), (c) the internal regression harness (`.github/workflows/test-callers.yml`) that calls the reusable CI workflows against `tests/fixtures/{go,node,python,terraform}` on every PR to `main`, (d) the scheduled consumer canary (`.github/workflows/integration-smoke.yml`) that rewrites a real consumer's workflows to `@main` and lints them, and (e) pointing a consumer repo's workflow at a branch and running it in GitHub.

The repo exposes two artifact kinds:

- **Reusable workflows** in `.github/workflows/` — each uses `on: workflow_call` and is referenced as `nandohdc/gh-pipelines/.github/workflows/<name>.yml@v1`.
- **Composite actions** in `actions/<name>/action.yml` — referenced as `nandohdc/gh-pipelines/actions/<name>@v1`.

Reusable workflows compose composite actions (e.g. `ci-go.yml` calls `actions/setup-go`, `helm-deploy.yml` calls both `actions/setup-doctl` and `actions/setup-helm`, `docker-build.yml` delegates SBOM/provenance/cosign work to `actions/supply-chain-attest`). When editing, prefer pushing shared logic down into a composite action rather than duplicating steps across workflows.

Current artifacts:

- **Reusable workflows** (consumer-facing, `workflow_call`): `ci-go.yml`, `ci-node.yml`, `ci-python.yml`, `ci-terraform.yml`, `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, `ci-with-sonar-python.yml`, `docker-build.yml`, `docker-release.yml`, `helm-deploy.yml`, `security-scan.yml`, `sonar-scan.yml`.
- **Internal workflows** (run only in this repo, do not expose `workflow_call`): `lint.yml` (actionlint + yamllint on PRs/pushes to `main`), `test-callers.yml` (regression harness — Go/Node/Python/Terraform fixtures), `security-self-scan.yml` (zizmor high-severity gate + actionlint+shellcheck on PRs, weekly cron, and `workflow_dispatch`), `release.yml` (automated semver tag + `v1` move on merged PRs), `dependabot-auto-merge.yml` (auto-labels Dependabot patch/minor PRs `release:patch` and enables squash-merge; leaves major updates for maintainer review), `integration-smoke.yml` (daily consumer canary against `nandohdc/financial-events-ingestion-api` — see `docs/INTEGRATION_CONSUMERS.md`).
- **Composite actions**: `docker-meta`, `setup-doctl`, `setup-go`, `setup-helm`, `setup-node`, `setup-python`, `setup-terraform`, `supply-chain-attest`.
- **Examples** in `examples/` — one per reusable workflow: `go-service-ci.yml`, `node-service-ci.yml`, `python-service-ci.yml`, `terraform-service-ci.yml`, `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, `ci-with-sonar-python.yml`, `docker-build.yml`, `docker-build-hardened.yml`, `docker-release.yml`, `helm-deploy.yml`, `sonar-scan.yml`.
- **Scripts**: `scripts/enforce-sonar-branch-protection.sh` — idempotent helper to append a Sonar required-check context to a consumer repo's branch-protection rules (used when onboarding a new consumer to the Sonar bundle).

## Commands

```bash
# Lint every workflow (requires: brew install actionlint)
actionlint .github/workflows/*.yml

# Lint a single workflow
actionlint .github/workflows/ci-go.yml

# Lint inline bash with shellcheck integration (matches security-self-scan.yml)
actionlint -shellcheck=shellcheck .github/workflows/*.yml

# Run security self-scan gate locally (zizmor high-severity threshold)
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools UV_PYTHON_INSTALL_DIR=/tmp/uv-python \
  uvx --from zizmor zizmor --min-severity=high --config .zizmor.yml \
  .github/workflows/** actions/**/action.yml

# Inspect / set the runner-selection variable (used by internal workflows and test-callers)
gh variable list --repo nandohdc/gh-pipelines
gh variable set RUNNER_LABEL --body "arc-runners-gh-pipelines"  # opt into self-hosted
gh variable delete RUNNER_LABEL                                  # revert to ubuntu-latest
# Consumers pass runner-label as a workflow_call input instead:
#   with:
#     runner-label: ${{ vars.RUNNER_LABEL }}
```

For functional validation:

- `test-callers.yml` runs automatically on every PR to `main` and on pushes to `main`; it calls `ci-go.yml`, `ci-node.yml`, `ci-python.yml`, `ci-terraform.yml`, and `ci-with-sonar-{go,node,python}.yml` against `tests/fixtures/{go,node,python,terraform}`. If you change any of those workflows or the fixtures, expect that suite to catch regressions before release.
- `security-self-scan.yml` runs on every PR, weekly (`cron: 0 6 * * 1`), and on `workflow_dispatch`; it enforces zizmor high-severity and runs `actionlint -shellcheck=shellcheck` plus shellcheck on any `actions/**/*.sh` scripts. Re-run the local zizmor/actionlint commands above when touching workflow or composite-action files.
- `integration-smoke.yml` runs daily (`cron: 0 7 * * *`) and on `workflow_dispatch`; it clones the canary consumer, rewrites `uses:` refs to `@main`, enforces Sonar presence, and `actionlint`s the rewritten workflows. On failure it opens (or reuses) an idempotent issue labeled `drift` or `sonar-missing`. Requires `INTEGRATION_PAT`; see `docs/INTEGRATION_CONSUMERS.md`.
- For full consumer-level validation, point a downstream repo at your branch — e.g. `uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch`. There is no way to exercise these workflows locally in this repo.

## Release model (important)

Consumers pin to the floating major tag `@v1`, so **moving that tag is what ships changes to every consumer simultaneously**. Releases are **automated** by `release.yml` — when a PR to `main` is merged, the workflow inspects the PR's labels:

- `release:patch` → bump patch (`vX.Y.Z` → `vX.Y.(Z+1)`)
- `release:minor` → bump minor
- `release:major` → bump major
- no `release:*` label → no-op, the workflow exits successfully without tagging

When a release label is present, `release.yml` creates the new `vX.Y.Z` tag, force-moves `v1` to the merge commit, and publishes a GitHub Release with auto-generated notes. `release.yml` derives the current version by listing remote tags (`git ls-remote --tags origin 'v*'`) and filtering with a portable semver regex — it does not rely on local tags.

Prefer the automation. Only run the two-tag dance by hand as a break-glass:

```bash
git tag v1.2.0 && git push origin v1.2.0
git tag -f v1 v1.2.0 && git push origin v1 --force
```

Backward-compatible additions (new optional inputs, bug fixes) should use `release:patch` or `release:minor`. Breaking changes (removing/renaming inputs, changing defaults that affect callers) require a new major tag lineage (e.g. `v2`) — do not use `release:major` to move `v1` across a compatibility boundary.

## Cross-cutting conventions

These are enforced across every workflow/action — match them when adding or editing:

- Every `workflow_call` input has `description`, `type`, `required`, and a `default` if optional.
- **Reusable workflows** expose a `runner-label` input (type: string, required: false, default: `""`). Jobs resolve `runs-on` with `${{ inputs.runner-label != '' && inputs.runner-label || 'ubuntu-latest' }}`. Consumers pass the label at the call site — see the example in `README.md`. Don't hardcode `ubuntu-latest` in a reusable-workflow job; always use this pattern so consumers can opt in to a self-hosted pool.
- **Internal workflows** (lint, security-self-scan, release, integration-smoke, dependabot-auto-merge) resolve their own `runs-on` from `${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}` directly, since they are never called from external repos. `test-callers.yml` bridges the two: it forwards `runner-label: ${{ vars.RUNNER_LABEL }}` to each reusable workflow it calls, so the regression suite also runs on the self-hosted pool.
- `permissions:` is set at the workflow (and per-job where feasible) to least-privilege. `docker-build.yml` scopes the extra permissions needed for hardening (`id-token: write`, `attestations: write`) to the individual SBOM / sign / provenance jobs, not the whole workflow. `docker-release.yml` is the one workflow that needs the broad set at workflow level because of cosign keyless signing + SLSA provenance + GitHub Release asset upload.
- All `shell: bash` steps start with `set -euo pipefail`.
- Every reusable workflow file has a header comment block with purpose + example caller snippet, and a matching example file in `examples/`.
- Third-party (non-`actions/*`, non-`github/*`) action refs are pinned to full commit SHAs with a trailing `# v<semver>` comment. GitHub-owned actions (`actions/checkout`, `actions/setup-*`, `actions/attest-build-provenance`) stay tag-pinned for readability. Dependabot watches `/` and every `actions/<name>/` directory weekly (see `.github/dependabot.yml`).
- Consumer-interface changes (inputs, outputs, secrets, default behavior) must update **all** of: the table in `README.md`, the matching section in `docs/USAGE.md`, the matching example in `examples/`, and `CHANGELOG.md` under `## [Unreleased]`.
- Adding a new composite action directory under `actions/` requires adding a matching `package-ecosystem: github-actions` block in `.github/dependabot.yml`.
- Keep `AGENTS.md` and `.codex/*.md` in sync when repository standards change.

## Notable cross-file coupling

- **Runner-label coupling** — `inputs.runner-label` (reusable workflows) and `vars.RUNNER_LABEL` (internal workflows) are both involved in runner selection. When adding a system-tool install that is only needed on self-hosted runners, gate it on `inputs.runner-label != ''` (reusable) or `vars.RUNNER_LABEL != ''` (internal) so GitHub-hosted runs don't reinstall what is already present. Current self-hosted prerequisites: `ci-go.yml` installs `build-essential` and sets `CGO_ENABLED=1`; `actions/setup-doctl` installs `kubectl`; `lint.yml` installs `yamllint` via pip; the cosign `envsubst`/`gettext-base` preflight in `actions/supply-chain-attest` and the `sign-image`/`attest-sbom` jobs of `docker-build.yml` is privilege-aware (root → direct `apt-get`, non-root → `sudo` only with passwordless sudo, else fail fast) — preinstall `gettext-base` on locked-down non-root images. When zizmor line numbers in `.zizmor.yml` go stale (typically after adding a new input block), re-run zizmor locally and update the ignore list — each +5-line input block shifts every flagged line below it by 5.
- `helm-deploy.yml` hardcodes a DigitalOcean `cluster-id` default (`adc9fd65-…`). Callers can override, but changing that default is a breaking change for repos that rely on it.
- `ci-go.yml` conditionally shells out to consumer-repo files that this repo doesn't own: `./scripts/check-gofmt.sh` (optional, falls back to inline `gofmt`), `./scripts/check-import-direction.sh` (when `run-import-direction: true`), and `make contract` (when `run-contract: true`). Preserve the opt-in toggles — consumers without those scripts rely on the defaults staying `false`.
- `ci-node.yml` is intentionally self-contained for cross-repo calls — it does not `uses:` the local `actions/setup-node` via a relative path. When editing, keep setup steps inline so consumers pulling `@v1` resolve everything from a single ref. The same constraint applies to `ci-python.yml`.
- `sonar-scan.yml` uses `workflow_call` secrets (`SONAR_TOKEN`, optional `SONAR_SCANNER_HOST_URL` / `SONAR_PUBLIC_BASE_URL`, legacy `SONAR_HOST_URL`) plus non-secret URL inputs. The caller job also needs `permissions: contents: read, pull-requests: read` for PR decoration. Both are documented in the workflow header, and `scripts/sonar_documentation_contract_test.py` guards the matching README/USAGE/integration-consumer/example guidance.
- `ci-with-sonar-*.yml` bundles require only `DOPPLER_TOKEN`; they fetch `SONAR_TOKEN`, prefer non-secret scanner/public URL inputs or repository variables, then fall back to Doppler `SONAR_SCANNER_HOST_URL` / legacy `SONAR_HOST_URL` at runtime (`sonar/prd` by default) and run an inline `sonar-scan` job. `test-callers.yml` uses `__test_skip_sonar_call: true` for these bundles. Local-kind consumers should pair `RUNNER_LABEL` with `SONAR_SCANNER_HOST_URL=http://sonarqube.sonarqube.svc.cluster.local:9000` and keep `SONAR_PUBLIC_BASE_URL` as UI/link context only.
- `docker-build.yml` exposes optional hardening inputs (`generate-sbom`, `generate-provenance`, `sign-image`) that all default to `false`. When enabled, each spawns a dedicated job that delegates to `actions/supply-chain-attest` and only runs when the image was actually pushed on the default branch with a resolved digest. Keep those defaults off so updates to `@v1` never change the behavior of existing consumers.
- `docker-release.yml` and `actions/supply-chain-attest` reference the image by digest (`${image}@${digest}`) for SBOM, cosign, and attestation steps. Keep that pattern — signing/attesting by tag is racy.
- `release.yml` parses PR labels with `jq` against `${GITHUB_EVENT_PATH}` and derives the next version from the remote tag list. If you change the labeling scheme, also update `.github/PULL_REQUEST_TEMPLATE.md`, `docs/CONTRIBUTING.md`, `AGENTS.md`, and this file.
- `dependabot-auto-merge.yml` runs on `pull_request_target` (never checks out PR code) and **adds the `release:patch` label** to Dependabot patch/minor PRs before enabling squash auto-merge. It is coupled to `release.yml`'s label scheme — if you change `release:patch|release:minor|release:major` semantics, update this workflow in the same PR. Major Dependabot updates are left to maintainer review with a comment.
- `integration-smoke.yml` is the consumer-canary probe. It depends on: (a) `INTEGRATION_PAT` with read access to the canary repo, (b) the matrix inside the workflow (currently one consumer — keep `docs/INTEGRATION_CONSUMERS.md` in sync when rotating), (c) the Sonar presence check, which looks for `sonar-scan.yml`, `ci-with-sonar-<lang>.yml`, or a `.github/SONAR_EXEMPT` marker. Failures open idempotent issues labeled `drift` (lint/parse failure) or `sonar-missing` (no Sonar coverage). The workflow is not a reusable workflow and must not be exposed via `workflow_call`.
- `ci-terraform.yml` references `setup-terraform` via `nandohdc/gh-pipelines/actions/setup-terraform@v1`, the same absolute pattern used by other workflows. A relative `./actions/setup-terraform` path resolves against the **caller's** checkout in cross-repo invocations, not the gh-pipelines checkout, and therefore fails for external consumers. The `audit` job accepts `tfsec` or `checkov` — validate both paths if you touch it.
- `security-self-scan.yml` runs zizmor at `min-severity: high` using `.zizmor.yml` and uploads SARIF (best-effort; warns if code scanning isn't enabled). If you add workflow or composite-action files, they are auto-covered by the `.github/workflows/**` and `actions/**/action.yml` globs. Changes to `.zizmor.yml` must keep the `high` gate green across all current files.
- `test-callers.yml` is **not** a reusable workflow — don't consume it from another repo. New self-tests belong there, one job per reusable workflow under test, pointed at a fixture under `tests/fixtures/`.
- `scripts/enforce-sonar-branch-protection.sh` is a consumer-side helper — it mutates another repo's branch protection to append a Sonar required check without replacing existing contexts. It is referenced from `docs/INTEGRATION_CONSUMERS.md` and is the recommended way to onboard a new consumer into the Sonar bundle without dropping their existing required checks.
