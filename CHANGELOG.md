# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Notes:

- Entries below are backfilled from tags currently visible via `git tag --sort=-v:refname`.
- Floating major tag `v1` is an alias and is not listed as a separate release entry.

## [Unreleased]

### Added

- Added reusable Terraform CI workflow (`ci-terraform.yml`) with fmt/validate/plan jobs plus optional `tfsec`/`checkov` audit.
- Added `actions/setup-terraform` composite action with SHA-pinned Terraform setup and optional plugin cache.
- Added Terraform caller example and minimal fixture coverage in `test-callers.yml`.
- Added Dependabot coverage for `/actions/setup-terraform`.
- Added internal Dependabot auto-merge workflow for patch/minor updates (`.github/workflows/dependabot-auto-merge.yml`) with maintainer-review fallback for majors.
- Added nightly integration canary workflow (`integration-smoke.yml`) and consumer runbook (`docs/INTEGRATION_CONSUMERS.md`) for drift detection against real repositories.
- Added `ci-with-sonar-go.yml`, `ci-with-sonar-node.yml`, and `ci-with-sonar-python.yml` reusable bundle workflows that pair language CI with Sonar scan/gate using Doppler-fetched secrets.
- Added Sonar bundle caller examples in `examples/ci-with-sonar-go.yml`, `examples/ci-with-sonar-node.yml`, and `examples/ci-with-sonar-python.yml`.
- Added `scripts/enforce-sonar-branch-protection.sh` to append Sonar required-check contexts without replacing existing branch-protection checks.

### Changed

- Validated no-release-label behavior in release automation (`chore(ci): validate no-label release behavior`).
- Protected `main` with PR + required checks, enabled auto-delete head branches, and cleaned up stale merged branches.
- Documented Dependabot policy in `docs/CONTRIBUTING.md`: patch/minor auto-merge on green required checks; majors require maintainer review.
- Updated `integration-smoke.yml` to enforce Sonar presence (`sonar-scan` or `ci-with-sonar-*` or `.github/SONAR_EXEMPT`), open `sonar-missing` issues when absent, and deduplicate both `sonar-missing` and `drift` issues by title.
- Expanded `test-callers.yml` coverage to include all new `ci-with-sonar-*` bundle workflows via internal `__test_skip_sonar_call` toggles.
- Updated security and usage docs for Doppler-backed Sonar credential flow (`DOPPLER_TOKEN`) in bundle workflows.

## [v1.5.2] - 2026-04-22

### Fixed

- Made release tag discovery robust by deriving next version from remote semantic tags.
- Replaced non-portable semver matching in `release.yml` with a portable regex approach.

## [v0.0.1] - 2026-04-22

### Added

- Added automated semver release workflow triggered by merged PR labels (`release:patch|minor|major`).

### Changed

- Updated repository standards/documentation for third-party action SHA pinning and Dependabot policy.

## [v1.5.1] - 2026-04-22

### Added

- Added internal reusable-workflow self-test harness (`test-callers.yml`) for Go/Node/Python fixture callers.
- Added and refined reusable workflow coverage for Go, Node.js, Python, and SonarQube scanning.

### Changed

- Pinned third-party GitHub Actions to full SHAs and added Dependabot update coverage for action directories.

[Unreleased]: https://github.com/nandohdc/gh-pipelines/compare/v1.5.2...HEAD
[v1.5.2]: https://github.com/nandohdc/gh-pipelines/compare/v0.0.1...v1.5.2
[v0.0.1]: https://github.com/nandohdc/gh-pipelines/compare/v1.5.1...v0.0.1
[v1.5.1]: https://github.com/nandohdc/gh-pipelines/releases/tag/v1.5.1
