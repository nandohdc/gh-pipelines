# gh-pipelines

Centralized, reusable GitHub Actions workflows and composite actions for the `nandohdc` ecosystem.

## Why

Instead of duplicating CI/CD logic across every repository, this repo provides **reusable workflows** (`workflow_call`) and **composite actions** that any repository can reference. Update pipeline logic once, every consumer inherits the change.

## Available Workflows

| Workflow | File | Purpose |
| --- | --- | --- |
| **Go CI** | `ci-go.yml` | Lint, test, coverage, vuln scan for Go services |
| **Node CI** | `ci-node.yml` | Lint, test, coverage, and audit for Node.js services |
| **Python CI** | `ci-python.yml` | Lint, test, coverage, and audit for Python services |
| **Go CI + Sonar Bundle** | `ci-with-sonar-go.yml` | Go CI plus SonarQube scan/quality gate with Doppler-fetched secrets |
| **Node CI + Sonar Bundle** | `ci-with-sonar-node.yml` | Node.js CI plus SonarQube scan/quality gate with Doppler-fetched secrets |
| **Python CI + Sonar Bundle** | `ci-with-sonar-python.yml` | Python CI plus SonarQube scan/quality gate with Doppler-fetched secrets |
| **Terraform CI** | `ci-terraform.yml` | Terraform fmt, validate, plan, and IaC audit |
| **Docker Build** | `docker-build.yml` | Build + push images to GHCR* |
| **Docker Release** | `docker-release.yml` | SBOM, cosign signing, SLSA provenance |
| **Helm Deploy** | `helm-deploy.yml` | Helm upgrade --install (dry-run on PR) |
| **Security Scan** | `security-scan.yml` | Trivy container vulnerability scan |
| **Sonar Scan** | `sonar-scan.yml` | SonarQube scan with preflighted scanner URL and quality gate enforcement |

*`docker-build.yml` supports optional hardening inputs (`generate-sbom`, `sign-image`, `attest-sbom`, `generate-provenance`) that run conditional jobs with expanded permissions only when enabled. The `sign-image` and `attest-sbom` jobs self-verify in the workflow (`cosign verify` / `cosign verify-attestation`), so they fail closed instead of silently passing when no signature/attestation is produced. The SBOM attestation uses cosign's legacy `.att` bundle format (`--new-bundle-format=false`) for GHCR compatibility — consumers verifying it on cosign v3 must pass the same flag (see `docs/USAGE.md`).

## Available Composite Actions

| Action | Path | Purpose |
| --- | --- | --- |
| **Setup Go** | `actions/setup-go` | Go toolchain + module download + cache |
| **Setup Node** | `actions/setup-node` | Node.js toolchain + package-manager-aware cache |
| **Setup Python** | `actions/setup-python` | Python toolchain + dependency-aware pip cache |
| **Setup Terraform** | `actions/setup-terraform` | Terraform CLI + optional provider plugin cache |
| **Setup Helm** | `actions/setup-helm` | Helm CLI + standard chart repos |
| **Setup doctl** | `actions/setup-doctl` | DigitalOcean CLI + kubeconfig |
| **Docker Meta** | `actions/docker-meta` | Standard image tags + OCI labels |
| **Supply Chain Attest** | `actions/supply-chain-attest` | Shared SBOM/sign/provenance steps by digest |

## Quick Start

### Using a reusable workflow

```yaml
# .github/workflows/ci.yml in your Go service repo
name: ci

on:
  push:
  pull_request:

jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1
    with:
      go-version: "1.26.2"
      coverage-min: "65.0"
      run-integration: true
      run-contract: true
      run-import-direction: true
      run-k8s-validate: true
      run-prometheus-validate: true
```

### Using a composite action

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: nandohdc/gh-pipelines/actions/setup-go@v1
    with:
      go-version: "1.26.2"
```

## Runner Selection

Reusable workflows accept an optional `runner-label` input. Jobs default to GitHub-hosted `ubuntu-latest` when the input is empty. Pass your runner label at the call site to target a self-hosted runner:

```yaml
jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1
    with:
      go-version: "1.26.2"
      runner-label: ${{ vars.RUNNER_LABEL }}   # set this variable in your repo/org
```

Set the variable once per repository or organization and every workflow that forwards it picks up the same pool:

```bash
gh variable set RUNNER_LABEL --body "my-runner-label"
```

See [docs/USAGE.md](docs/USAGE.md#runner-selection) for per-workflow input tables and self-hosted runner prerequisites.

## Versioning

This repository uses **tag-based versioning**:

- `v1` — floating major tag (recommended for consumers)
- `v1.0.0` — exact semantic version

Pin to the major version (`@v1`) to automatically receive backward-compatible updates. Pin to an exact version (`@v1.0.0`) for maximum stability.

## Repository Layout

```
.github/workflows/    Reusable workflows (workflow_call) + internal workflows (lint, test-callers, release, security-self-scan, dependabot-auto-merge, integration-smoke)
actions/              Composite actions for shared steps
docs/                 Usage, contribution, and integration-canary guides
examples/             Copy-paste caller workflow examples
scripts/              Helper scripts for consumer onboarding (for example, enforce-sonar-branch-protection.sh)
tests/fixtures/       Minimal fixtures exercised by the internal regression harness (go, node, python, terraform)
```

## Documentation

- [Usage Guide](docs/USAGE.md) — Detailed reference for every workflow and action
- [Runner Selection](docs/USAGE.md#runner-selection) — Opt into self-hosted runners with `RUNNER_LABEL`
- [Contributing Guide](docs/CONTRIBUTING.md) — How to add or modify workflows
- [Integration Consumers](docs/INTEGRATION_CONSUMERS.md) — Canary registry for the scheduled integration-smoke probe

## License

[MIT](LICENSE)

