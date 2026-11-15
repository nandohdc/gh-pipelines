# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Notes:

- Entries below are backfilled from shipped tags currently visible in the repository.
- Floating major tag `v1` is an alias and is not listed as a separate release entry.

## [Unreleased]

### Deprecated

- Sonar workflows and examples are deprecated across the environment. Existing `v1` behavior remains available during consumer migration.

### Removed

- Repository-owned Sonar scanning, its project configuration, and Sonar-presence enforcement in the integration probe. Existing `sonar-missing` issues are not automatically changed.
- `GEMINI.md`: removed the Gemini CLI guidance file; `AGENTS.md`, `CLAUDE.md`, and `.codex/*.md` remain the authoritative agent-instruction sources.

### Added

- `scripts/helm_environment.py` (`#188`): added GitHub Environment protection verifier (`verify`) checking environment existence, `required_reviewers`, and default-branch deployment policies so merely named/unprotected environments cannot satisfy protected manual deployment acceptance.
- `examples/helm-deploy-manual.yml` (`#188`): added Caller workflow example for explicitly opted-in protected manual Helm deployments (`allow-manual-deploy: true` + `environment`).
- `docs/adr/0001-major-lineage-releases.md` and `CONTEXT.md`: documented the multi-lineage versioning model (`Major lineage`, `Floating major tag`, `Exact release tag`, `Maintenance branch`) and `release/v[0-9]+` maintenance route.
- `scripts/contract_lib.py`: extracted shared helper functions (`read`, `assert_contains`, `assert_not_contains`, `assert_order`, `step_block`) from the three contract test scripts into a coverable module that produces SonarQube coverage data.
- `scripts/contract_lib_test.py`: pytest unit tests for `contract_lib.py` with 100% coverage, wired into `lint.yml`.
- `sonar-scan.yml`: new optional `pre-scan-command` input that runs an arbitrary shell command in `working-directory` before the scan step. Default empty (no-op).
- `sonar-scan.yml`: new optional `pre-scan-python-version` input that installs a caller-selected Python runtime before `pre-scan-command`. Default empty, so existing consumers do not add Python setup work.
- `supply-chain-attest` action: new `attest-sbom` input that emits the SPDX SBOM as a cosign attestation (`cosign attest --type spdxjson`) on the image digest, so downstream consumers can `cosign verify-attestation --type spdxjson --new-bundle-format=false`. Adds an `envsubst`/`gettext-base` preflight before `cosign-installer` (robust cosign install on self-hosted/ARC runners) and widens the SPDX-generate + cosign-install gates to cover the attest path (`nandohdc/financial-events-ingestion-api#78`).
- `docker-build.yml`: new `attest-sbom` input that runs a dedicated `attest-sbom` job (GHCR login + `cosign attest --type spdxjson`) for `:latest`/default-branch pushes, plus a GHCR login added to the `sign-image` job so cosign can push signatures for **private** images.
- `test-callers.yml`: new `docker-build` caller exercising `attest-sbom: true` against a `tests/fixtures/docker` image — build-only on PRs, full build + push + attest + self-verify on push to main.

### Changed

- `.github/workflows/helm-deploy.yml` (`@v2`, `#188`): added explicitly opted-in protected manual deployment (`allow-manual-deploy: true` + non-empty `environment`, safe defaults `false` / `""`), split preflight `authorize` (unprivileged, no secrets) from `deploy` (`environment:` approval gate before `Setup doctl + kubeconfig`, `Helm`, and `post-deploy-kubectl` plus defense-in-depth `Verify deployment authorization`), updated internal action refs to `@v2`, and extended `scripts/helm_deploy_workflow_contract_test.py` and `scripts/candidate_smoke.py`.
- `.github/workflows/helm-deploy.yml` (`@v2` breaking policy change; `#187`): authorize `github.event_name`, `github.ref`, `github.event.repository.default_branch`, and pull-request fork metadata **before** obtaining DigitalOcean credentials (`actions/setup-doctl`), running `helm upgrade --install`, or executing `post-deploy-kubectl`. Only default-branch pushes may perform automatic real deployments (`mode=deploy`); same-repository pull requests execute dry-runs (`mode=dry-run`, `deploy (dry-run) / <release-name>`) with `post-deploy-kubectl` disabled; feature pushes, fork pull requests, `pull_request_target`, `workflow_dispatch` (pending protected opt-in), and unsupported events fail closed before credential setup. Preserves existing `@v1` releases unchanged and adds `v1 -> v2` migration guidance plus `scripts/helm_deploy_workflow_contract_test.py` and hosted `candidate_smoke` verification.
- `scripts/release.py`, `.github/workflows/release.yml`, `.github/workflows/lint.yml`, and `.github/workflows/test-callers.yml`: isolated release version calculation and floating major tag updates per compatible major lineage (`v0`, `v1`, `v2`, …), introduced explicit `ReleasePlan` tracking, rejected conflicting immutable `vX.Y.Z` tags without force-rewriting, and added `release/v[0-9]+` maintenance branch support (with branch-creation push guards and `release:major` rejection on maintenance branches).
- `README.md`, `docs/USAGE.md`, `docs/CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `AGENTS.md`, and `.codex/STANDARDS.md`: updated versioning, compatibility, breaking-change, and consumer migration guidance for independent major lineages (`#182`).
- Repository-owned automation and fixture callers select GitHub-hosted runners. The integration probe retains its `runner-label` dispatch input but ignores it.
- `scripts/sonar_scan_workflow_contract_test.py`, `scripts/ci_with_sonar_workflow_contract_test.py`, `scripts/sonar_documentation_contract_test.py`: import helpers from `contract_lib` instead of defining them inline.
- `docs/USAGE.md`, `examples/sonar-scan.yml`, `README.md`: document the new `pre-scan-command` input.
- `lint.yml`: runs `test_contract_lib.py` unit tests in addition to the static contract tests.
- SonarQube reusable workflow docs and examples now document the scanner/public URL split: `SONAR_SCANNER_HOST_URL` (preferred scanner and quality-gate endpoint), `SONAR_PUBLIC_BASE_URL` (public UI/base URL only), legacy `SONAR_HOST_URL` fallback, and local-kind ARC `RUNNER_LABEL` behavior.

### Fixed

- `supply-chain-attest` action: new optional `platforms` input pins syft to the first built platform (`SYFT_PLATFORM`) when generating SBOMs, and `docker-build.yml` (`sbom`/`attest-sbom` jobs) + `docker-release.yml` now forward their `platforms` input to it. Syft resolves an image index at the runner's host platform by default, so SBOM generation/attestation failed whenever the runner arch was not among the built platforms — e.g. the arm64 ARC pool scanning the amd64-only fixture image broke every `test-callers` push run on `main` (`no child with platform linux/arm64 in index`). Empty `platforms` keeps syft's auto-detect behavior.
- Sonar scan workflows (`sonar-scan.yml` and all three `ci-with-sonar-*` bundles) now install `dirmngr` before the scan when it is missing, privilege-aware (root → direct `apt-get`, non-root → passwordless `sudo`, else fail fast — mirrors the `gh`/`gettext-base` preflights). `sonarqube-scan-action` v8.2.1 verifies the scanner CLI's GPG signature and `gpg --recv-keys` needs `dirmngr`, which the ARC runner image ships without, so every scan on self-hosted runners failed with `gpg: keyserver receive failed: No dirmngr`. Signature verification stays enabled (`skipSignatureVerification` is deliberately not used), and the contract tests now enforce the step and forbid disabling verification.
- `Validate SonarQube scanner report` (all four Sonar workflows) now **normalizes** `serverUrl`/`ceTaskUrl` in `report-task.txt` to the resolved scanner URL instead of hard-failing on mismatch. When SonarQube has `sonar.core.serverBaseUrl` set (e.g. a public tunnel base URL), the server writes that URL into the scanner report, so the previous exact-match check failed every scan against such a server — and the quality-gate action would otherwise query a `ceTaskUrl` that may be unreachable from the runner. The step stays fail-closed: missing/empty report, missing `ceTaskId`/`ceTaskUrl`/`serverUrl`, disposable Quick Tunnel report URLs, and a failed rewrite all still fail the job (rewrite is verified with exact-line `grep -qx`).
- Sonar scanner jobs now provision Node.js 22 before analysis, avoiding ARC/self-hosted runner failures when the Sonar JS/CSS analyzer sees JavaScript, TypeScript, CSS, or web files but the runner image does not include `node`.
- `release.yml`: harden merged-PR resolution so releases cannot silently no-op. The PR number is now taken from the squash-merge commit subject `… (#N)` first (GitHub's default squash format, used by Dependabot) — no API round-trip and immune to the `/commits/{sha}/pulls` eventual-consistency lag right after a squash merge — with the classic merge-commit message and the `/commits/{sha}/pulls` API as fallbacks. The `|| true` is dropped from both `gh api` calls so a genuine failure fails the run loudly instead of skipping the release.
- `supply-chain-attest` action + `docker-build.yml`: the `Ensure envsubst is available for cosign installer` preflight (all three copies — the action plus the `sign-image` and `attest-sbom` self-verify preflights in `docker-build.yml`) is now **privilege-aware** instead of running `sudo apt-get` unconditionally. As root (`id -u` = 0) it installs `gettext-base` with direct `apt-get`; as non-root it uses `sudo` only when passwordless sudo is available (`sudo -n true`); otherwise it fails fast with an actionable message naming `gettext-base` and the runner-image remedy (no hang, no confusing `sudo: command not found`). Fixes minimal root containers and locked-down ARC/self-hosted images where the previous unconditional `sudo` broke cosign install for **both** signing paths (issue #102).
- `docker-build.yml` + `supply-chain-attest`: the `attest-sbom` path now pins the legacy cosign `.att` bundle format end to end. cosign v3 defaults to the new Sigstore bundle format attached via the OCI 1.1 referrers API, which does not round-trip reliably on GHCR — `cosign attest` exits green but the in-job `cosign verify-attestation` fails closed with `no valid bundles exist in registry` (issue #104, unblocks #101). `cosign attest` is pinned with `--new-bundle-format=false --use-signing-config=false` (both required together: cosign v3 otherwise rejects `--new-bundle-format=false` with "must provide --new-bundle-format … with --signing-config or --use-signing-config"), and `cosign verify-attestation` with `--new-bundle-format=false`. Keyless identity/issuer and Rekor tlog verification are unchanged. **Consumer note:** verifying the published attestation on cosign v3 now requires `cosign verify-attestation --type spdxjson --new-bundle-format=false …` (see `docs/USAGE.md`).
- `docker-build.yml`: the `attest-sbom` and `sign-image` jobs now self-verify in the workflow (`cosign verify-attestation --type spdxjson` / `cosign verify`) after calling `supply-chain-attest`. Previously, if the floating `supply-chain-attest@v1` ref resolved to a manifest without the `attest-sbom` step (ref drift on a branch-pinned run or a lagging `v1`), the job logged in to GHCR, found no step to run, and **exited green with no attestation**. Verification lives in the workflow (not the action), so it cannot be skipped by an older action and fails the job if no valid signature/attestation is present (issue #101).
- `release.yml`: added a release-time guard that aborts the release unless `actions/supply-chain-attest/action.yml` at the release commit declares `attest-sbom`, so the floating `v1` tag can never advance onto a manifest the `docker-build.yml` attest path depends on.
- `docker-build.yml`: log in to GHCR before the existing `sbom` job calls Syft through `supply-chain-attest`, so `generate-sbom: true` works for private GHCR images instead of failing with `UNAUTHORIZED`.
- `ci-terraform.yml`: replace relative `./actions/setup-terraform` references with absolute `nandohdc/gh-pipelines/actions/setup-terraform@v1` so the workflow succeeds when called from external repositories (issue #83).

## [v1.7.0] - 2026-05-13

### Added

- Added QEMU setup for multi-arch Docker builds (`feat(docker): add QEMU setup for multi-arch builds`, PR #80).

## [v1.6.2] - 2026-04-26

### Changed

- Added explicit `runner-label` inputs for reusable workflows and aligned self-hosted runner selection behavior.
- Updated internal contributor guidance for runner-label conventions.

## [v1.6.1] - 2026-04-24

### Changed

- Enforced Sonar coverage checks in `integration-smoke.yml`.

## [v1.6.0] - 2026-04-24

### Added

- Added `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, and `ci-with-sonar-python.yml` reusable bundle workflows.
- Added Sonar bundle caller coverage in internal regression harnesses.

### Changed

- Aligned Sonar bundle caller permissions and follow-up workflow behavior.

## [v1.5.21] - 2026-04-23

### Changed

- Fixed integration drift issue creation to use explicit repository targeting.

## [v1.5.20] - 2026-04-23

### Added

- Added nightly consumer canary workflow (`integration-smoke.yml`) and its runbook (`docs/INTEGRATION_CONSUMERS.md`).

### Changed

- Hardened consumer workflow parsing and scoped integration-smoke issue permissions.

## [v1.5.19] - 2026-04-23

### Added

- Added `security-self-scan.yml` (zizmor + actionlint/shellcheck security gate).

## [v1.5.18] - 2026-04-23

### Added

- Added internal Dependabot auto-merge workflow for patch/minor updates with major-update maintainer-review fallback.

## [v1.5.17] - 2026-04-22

### Fixed

- Added merge-commit PR detection fallback in `release.yml`.

## [v1.5.16] - 2026-04-22

### Added

- Added reusable Terraform CI workflow (`ci-terraform.yml`) with fmt/validate/plan and optional `tfsec`/`checkov`.
- Added `actions/setup-terraform` composite action plus Terraform fixture/example coverage.
- Added Dependabot coverage for `/actions/setup-terraform`.

### Fixed

- Stabilized `release.yml` behavior for merged-PR release flows.

## [v1.5.15] - 2026-04-22

### Changed

- Bumped `github/codeql-action` from v3 to v4.

## [v1.5.14] - 2026-04-22

### Changed

- Bumped `sigstore/cosign-installer` in supply-chain attestations.

## [v1.5.13] - 2026-04-22

### Changed

- Bumped `actions/setup-node` to v6.

## [v1.5.12] - 2026-04-22

### Changed

- Bumped `actions/setup-python` to v6.

## [v1.5.11] - 2026-04-22

### Changed

- Bumped `actions/setup-go` to v6.

## [v1.5.10] - 2026-04-22

### Changed

- Bumped `actions/checkout` to v6.

## [v1.5.9] - 2026-04-22

### Changed

- Bumped `azure/setup-helm` to v5.

## [v1.5.8] - 2026-04-22

### Changed

- Bumped `docker/metadata-action` to v6.

## [v1.5.7] - 2026-04-22

### Changed

- Bumped `docker/setup-buildx-action` to v4.

## [v1.5.6] - 2026-04-22

### Changed

- Bumped `aquasecurity/trivy-action` from 0.35.0 to 0.36.0.

## [v1.5.5] - 2026-04-22

### Changed

- Bumped `SonarSource/sonarqube-quality-gate-action` to 1.2.0.

## [v1.5.4] - 2026-04-22

### Changed

- Bumped `digitalocean/action-doctl` from 2.5.1 to 2.5.2.

## [v1.5.3] - 2026-04-22

### Added

- Added repository hygiene and governance baseline assets (templates, security policy, standards sync).

### Changed

- Validated release automation behavior when merged PRs have no `release:*` label.

## [v1.5.2] - 2026-04-22

### Fixed

- Made release tag discovery robust by deriving next version from remote semantic tags.
- Replaced non-portable semver matching in `release.yml` with a portable regex approach.

## [v1.5.1] - 2026-04-22

### Added

- Added internal reusable-workflow self-test harness (`test-callers.yml`) for Go/Node/Python fixture callers.
- Added and refined reusable workflow coverage for Go, Node.js, Python, and SonarQube scanning.

### Changed

- Pinned third-party GitHub Actions to full SHAs and added Dependabot update coverage for action directories.

## [v0.0.1] - 2026-04-22

### Added

- Added automated semver release workflow triggered by merged PR labels (`release:patch|minor|major`).

### Changed

- Updated repository standards/documentation for third-party action SHA pinning and Dependabot policy.

[Unreleased]: https://github.com/nandohdc/gh-pipelines/compare/v1.7.0...HEAD
[v1.7.0]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.7.0
[v1.6.2]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.6.2
[v1.6.1]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.6.1
[v1.6.0]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.6.0
[v1.5.21]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.21
[v1.5.20]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.20
[v1.5.19]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.19
[v1.5.18]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.18
[v1.5.17]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.17
[v1.5.16]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.16
[v1.5.15]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.15
[v1.5.14]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.14
[v1.5.13]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.13
[v1.5.12]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.12
[v1.5.11]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.11
[v1.5.10]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.10
[v1.5.9]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.9
[v1.5.8]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.8
[v1.5.7]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.7
[v1.5.6]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.6
[v1.5.5]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.5
[v1.5.4]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.4
[v1.5.3]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.3
[v1.5.2]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.2
[v1.5.1]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.1
[v0.0.1]: https://github.com/nandohdc/gh-pipelines/releases/tag/v0.0.1
