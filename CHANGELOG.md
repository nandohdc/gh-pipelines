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

### Changed

- Validated no-release-label behavior in release automation (`chore(ci): validate no-label release behavior`).

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
