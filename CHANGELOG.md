# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Notes:

- Entries below are backfilled from shipped tags currently visible in the repository.
- Floating major tag `v1` is an alias and is not listed as a separate release entry.

## [Unreleased]

### Added

- `supply-chain-attest` action: new `attest-sbom` input that emits the SPDX SBOM as a cosign attestation (`cosign attest --type spdxjson`) on the image digest, so downstream consumers can `cosign verify-attestation --type spdxjson`. Adds an `envsubst`/`gettext-base` preflight before `cosign-installer` (robust cosign install on self-hosted/ARC runners) and widens the SPDX-generate + cosign-install gates to cover the attest path (`nandohdc/financial-events-ingestion-api#78`).
- `docker-build.yml`: new `attest-sbom` input that runs a dedicated `attest-sbom` job (GHCR login + `cosign attest --type spdxjson`) for `:latest`/default-branch pushes, plus a GHCR login added to the `sign-image` job so cosign can push signatures for **private** images.

### Fixed

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
