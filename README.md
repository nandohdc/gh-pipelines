# gh-pipelines

Centralized, reusable GitHub Actions workflows and composite actions for the `nandohdc` ecosystem.

## Why

Instead of duplicating CI/CD logic across every repository, this repo provides **reusable workflows** (`workflow_call`) and **composite actions** that any repository can reference. Update pipeline logic once, every Consumer repository inherits the change.

## Consumer Terminology

A **Consumer repository** is an external repository that adopts a published
gh-pipelines artifact through a versioned reference. A **Workflow consumer**
uses at least one reusable workflow; its **Caller workflow** is the
consumer-owned workflow file, and each job-level use of a reusable workflow is
a Workflow invocation (also called a Caller job). A Consumer repository that uses a composite action
directly is a **Composite-action consumer**, not necessarily a Workflow
consumer.

## Available Workflows

Sonar workflows are deprecated across the environment. Existing `v1` interfaces remain available for compatibility; use the language CI workflows for new callers.

| Workflow | File | Purpose |
| --- | --- | --- |
| **Go CI** | `ci-go.yml` | Lint, test, coverage, vuln scan for Go services |
| **Node CI** | `ci-node.yml` | Lint, test, coverage, and audit for Node.js services |
| **Python CI** | `ci-python.yml` | Lint, test, coverage, and audit for Python services |
| **Go CI + Sonar Bundle** | `ci-with-sonar-go.yml` | Go CI plus SonarQube scan/quality gate with Doppler token and scanner/public URL separation |
| **Node CI + Sonar Bundle** | `ci-with-sonar-node.yml` | Node.js CI plus SonarQube scan/quality gate with Doppler token and scanner/public URL separation |
| **Python CI + Sonar Bundle** | `ci-with-sonar-python.yml` | Python CI plus SonarQube scan/quality gate with Doppler token and scanner/public URL separation |
| **Terraform CI** | `ci-terraform.yml` | Terraform fmt, validate, plan, and IaC audit |
| **Docker Build** | `docker-build.yml` | Build + push images to GHCR* |
| **Docker Release** | `docker-release.yml` | SBOM, cosign signing, SLSA provenance |
| **Helm Deploy** | `helm-deploy.yml` | Helm upgrade --install (`@v2` guarded default-branch + protected manual deploy; `@v1` legacy) |
| **Security Scan** | `security-scan.yml` | Trivy container vulnerability scan |
| **Sonar Scan** | `sonar-scan.yml` | SonarQube scan with preflighted scanner URL, quality gate enforcement, and optional pre-scan command hook |

*`docker-build.yml` supports optional hardening inputs (`generate-sbom`, `sign-image`, `attest-sbom`, `generate-provenance`) that run conditional jobs with expanded permissions only when enabled. The `sign-image` and `attest-sbom` jobs self-verify in the workflow (`cosign verify` / `cosign verify-attestation`), so they fail closed instead of silently passing when no signature/attestation is produced. The SBOM attestation uses cosign's legacy `.att` bundle format (`--new-bundle-format=false`) for GHCR compatibility — Consumer repositories verifying it on cosign v3 must pass the same flag (see `docs/USAGE.md`).

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
# A Caller workflow in your Go Workflow consumer
# .github/workflows/ci.yml
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

Coverage uses unit tests by default. Callers may opt into combined unit and
tagged integration coverage with `coverage-tags` and
`coverage-instrument-packages`; see [Go CI inputs](docs/USAGE.md#ci-goyml--go-ci-pipeline).

### Using a composite action

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: nandohdc/gh-pipelines/actions/setup-go@v1
    with:
      go-version: "1.26.2"
```

### Multi-module Go Workflow consumers

In a Caller workflow, invoke `ci-go.yml` once for each independent module,
passing its declared Go toolchain, `go.sum` cache path, compatible golangci-lint
major, and a unique unit and integration coverage artifact name. A later
`sonar-scan.yml` Workflow invocation can download matching unit artifacts before
its pre-scan command prepares the coverage report. This keeps module toolchains
isolated while retaining one reusable CI contract.

### Terraform Authentication & Caller Composition

`workflow_call` jobs in `ci-terraform.yml` run on separate runner VMs and do not
inherit caller job steps, environment variables, or OIDC tokens:

- **Credential-free / static validation**: call `nandohdc/gh-pipelines/.github/workflows/ci-terraform.yml@v1` with `run-plan: false` (or run with a downstream self-hosted `runner-label` that preconfigures ambient cloud identity).
- **Authenticated remote-backend `init` / `plan`**: compose a caller-owned job with `permissions: { contents: read, id-token: write }`, authenticate with your cloud provider in that same job, invoke `nandohdc/gh-pipelines/actions/setup-terraform@v1`, and run Terraform CLI steps there (see [examples/terraform-service-ci.yml](examples/terraform-service-ci.yml) and [docs/USAGE.md](docs/USAGE.md)).

## Runner Selection

Repository-owned automation runs on GitHub-hosted `ubuntu-latest` and ignores `RUNNER_LABEL`. The integration probe retains its deprecated `runner-label` dispatch input but ignores it. The selection below applies to downstream Consumer repositories.

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

## SonarQube URL Contract

The following is compatibility guidance for deprecated Sonar workflows, not a recommendation for new integrations.

SonarQube workflows separate the private scanner endpoint from the public UI/base URL:

| Setting | Purpose |
| --- | --- |
| `SONAR_SCANNER_HOST_URL` / `sonar-scanner-host-url` | Scanner server URL used by SonarSource scan and quality-gate actions. For local-kind ARC runners, prefer `http://sonarqube.sonarqube.svc.cluster.local:9000`. |
| `SONAR_PUBLIC_BASE_URL` / `sonar-public-base-url` | Stable public HTTPS SonarQube UI/base URL for generated links and browser callbacks. It is not used for scanner connectivity. |
| `SONAR_HOST_URL` | Legacy scanner URL fallback kept for backward compatibility only. Existing callers can use `SONAR_SCANNER_HOST_URL`. |
| `RUNNER_LABEL` / `runner-label` | Selects the runner pool. Use the appropriate ARC runner label when the scanner URL is the local-kind Service URL. |

If the local ARC runtime is offline, GitHub jobs remain queued or pending until a matching runner becomes available. If a runner starts but SonarQube is unavailable, starting, or unhealthy, the preflight fails before scanning and the SonarQube check cannot turn green without contacting SonarQube.

Do not add custom tunnel/auth headers to SonarSource actions; this contract assumes the configured scanner URL is directly reachable from the selected runner.

## Versioning

This repository uses **tag-based versioning** with independent compatible **Major lineages**:

- `v1`, `v2`, … — **Floating major tags** (recommended for Consumer repositories, both Workflow consumers and Composite-action consumers). Each Floating major tag advances only for backward-compatible `patch` and `minor` releases within its own Major lineage.
- `v1.0.0`, `v2.0.0`, … — immutable **Exact release tags** that never move once published.

Use `@v1` for normal backward-compatible delivery. Breaking changes (such as removing/renaming inputs or changing defaults that break existing callers, including the `@v2` `helm-deploy.yml` default-branch deployment restriction) are never released through `v1`; instead, they establish a new Major lineage (`v2.0.0` and floating `v2`) while leaving `v1` untouched so existing Consumer repositories can migrate deliberately. An exact release reference fixes the declared workflow reference, not every transitive dependency. Branch references and `@main` are validation references, not production onboarding references. The `@main` reference is reserved for the integration-smoke probe.

To adopt a new major release when ready (for example moving a `helm-deploy.yml` Workflow consumer to `@v2` to enforce default-branch push deployment and pre-credential authorization), update every Workflow invocation and direct composite action `uses:` reference in your Caller workflows together (for example `@v1` -> `@v2`) after reviewing the release notes and [Usage Guide](docs/USAGE.md#versioning-compatibility-and-major-migration).

## Repository Layout

```
.github/workflows/    Reusable workflows (workflow_call) + internal workflows (lint, test-callers, release, security-self-scan, dependabot-auto-merge, integration-smoke)
actions/              Composite actions for shared steps
docs/                 Usage, contribution, and integration-canary guides
examples/             Copy-paste Caller workflow examples
scripts/              Workflow contract tests and maintenance helpers
tests/fixtures/       Minimal fixtures exercised by the internal regression harness (go, node, python, terraform)
```

## Documentation

- [Usage Guide](docs/USAGE.md) — Detailed reference for every workflow and action
- [Runner Selection](docs/USAGE.md#runner-selection) — Opt into self-hosted runners with `RUNNER_LABEL`
- [Contributing Guide](docs/CONTRIBUTING.md) — How to add or modify workflows
- [Integration Consumers](docs/INTEGRATION_CONSUMERS.md) — Canary registry for the scheduled integration-smoke probe

## License

[MIT](LICENSE)
