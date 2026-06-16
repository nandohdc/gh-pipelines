# Usage Guide

Complete reference for every reusable workflow and composite action in this repository.

---

## Runner Selection

Every reusable workflow exposes an optional `runner-label` input. Jobs resolve `runs-on` to the supplied label, or fall back to GitHub-hosted `ubuntu-latest` when the input is empty or omitted.

### Consumer usage

Pass the label explicitly in each `workflow_call` block:

```yaml
jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1
    with:
      go-version: "1.26.2"
      runner-label: ${{ vars.RUNNER_LABEL }}
```

Set `RUNNER_LABEL` once at the repository or organization level and every workflow that forwards it picks up the same pool:

```bash
gh variable set RUNNER_LABEL --body "arc-runners-myorg"
```

### Internal repo usage

Internal workflows in this repository (lint, security-self-scan, release, integration-smoke, dependabot-auto-merge) resolve their own `runs-on` directly from `${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}`, so the gh-pipelines repo itself can run on the same pool without requiring callers to pass anything.

`test-callers.yml` forwards the variable to the reusable workflows it calls:

```yaml
with:
  runner-label: ${{ vars.RUNNER_LABEL }}
```

For the `nandohdc/gh-pipelines` repository, set `RUNNER_LABEL` to `arc-runners-gh-pipelines`.

### Self-hosted runner prerequisites

| Workflow | Required extras |
| --- | --- |
| `ci-go.yml` | `build-essential` (installed on demand), `CGO_ENABLED=1` set automatically |
| `helm-deploy.yml` | `kubectl` — installed by `actions/setup-doctl` composite |
| `docker-build.yml`, `docker-release.yml` | Docker daemon |
| `lint.yml` | `yamllint` — installed via pip before the action step |

Use a runner image broad enough for all jobs the workflow triggers.

---

## Reusable Workflows

### ci-go.yml — Go CI Pipeline

Full CI pipeline for Go services including linting, testing, vulnerability scanning, and optional Kubernetes/Prometheus manifest validation.

**Trigger:** `workflow_call`

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `go-version` | string | no | `1.26.2` | Go version to install |
| `golangci-lint-version` | string | no | `v1.64.8` | golangci-lint version |
| `coverage-min` | string | no | `65.0` | Minimum coverage percentage |
| `coverage-packages` | string | no | `""` | Space-separated packages for coverage (empty = all) |
| `run-integration` | boolean | no | `false` | Run integration tests |
| `integration-tags` | string | no | `integration` | Build tags for integration tests |
| `integration-packages` | string | no | `./internal/...` | Package pattern for integration tests |
| `run-contract` | boolean | no | `false` | Run contract checks (`make contract`) |
| `run-import-direction` | boolean | no | `false` | Run import direction checks |
| `run-k8s-validate` | boolean | no | `false` | Validate Kubernetes manifests |
| `k8s-manifests-dir` | string | no | `deploy/kubernetes` | Path to K8s manifests |
| `run-prometheus-validate` | boolean | no | `false` | Validate Prometheus rule files |
| `prometheus-rules-dir` | string | no | `deploy/prometheus` | Path to Prometheus rules |

#### Jobs

1. **lint** — `gofmt` check, `go vet`, `golangci-lint`, optional import direction
2. **test** — unit tests with race detector, coverage threshold, optional contract checks
3. **integration** — integration tests with Testcontainers (conditional)
4. **vuln** — `govulncheck` scan
5. **validate-manifests** — K8s dry-run + Prometheus promtool (conditional)

#### Example

```yaml
name: ci
on: [push, pull_request]

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

---

### ci-node.yml — Node.js CI Pipeline

Reusable CI workflow for Node.js/TypeScript services with lint, tests, coverage threshold gating, and optional dependency audit.

**Trigger:** `workflow_call`

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `node-version` | string | no | `20` | Node.js version to install |
| `package-manager` | string | no | `npm` | Package manager (`npm`, `pnpm`, `yarn`) |
| `lint-script` | string | no | `lint` | Script name used for linting |
| `test-script` | string | no | `test` | Script name used for tests |
| `coverage-min` | number | no | `0` | Minimum line coverage percentage (`0` disables gate) |
| `run-audit` | boolean | no | `true` | Run dependency audit job |
| `audit-level` | string | no | `high` | Audit severity threshold |

#### Jobs

1. **lint** — install dependencies + run lint script
2. **test** — install dependencies + run test script + optional coverage gate
3. **audit** — install dependencies + run audit scan (conditional)

Coverage gate behavior:
- If `coverage-min` is `0`, the gate is skipped.
- If `coverage-min` is greater than `0`, workflow reads coverage from `coverage/coverage-summary.json` (preferred) or `coverage/lcov.info`.

#### Example

```yaml
name: ci
on: [push, pull_request]

jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-node.yml@v1
    with:
      node-version: "20"
      package-manager: "npm"
      lint-script: "lint"
      test-script: "test:coverage"
      coverage-min: 70
      run-audit: true
      audit-level: "high"
```

---

### ci-python.yml — Python CI Pipeline

Reusable CI workflow for Python services with ruff linting, pytest coverage threshold gating, and optional dependency audit.

**Trigger:** `workflow_call`

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `python-version` | string | no | `3.12` | Python version to install |
| `dependency-file` | string | no | `requirements.txt` | Primary dependency file (supports detection of `requirements.txt`, `pyproject.toml`, `poetry.lock`) |
| `lint-paths` | string | no | `.` | Space-separated paths passed to ruff |
| `test-paths` | string | no | `tests/` | Space-separated paths passed to pytest |
| `coverage-min` | number | no | `0` | Minimum pytest coverage percentage |
| `run-audit` | boolean | no | `true` | Run `pip-audit` dependency scan job |

#### Jobs

1. **lint** — install dependencies + run `ruff check` and `ruff format --check`
2. **test** — install dependencies + run `pytest` with `--cov-fail-under`
3. **audit** — install dependencies + run `pip-audit` (conditional)

Coverage gate behavior:
- `coverage-min` is passed directly to `pytest --cov-fail-under=<value>`.
- `0` keeps the coverage gate effectively disabled while still collecting coverage output.

Dependency file behavior:
- Uses `dependency-file` when it exists.
- Falls back to `requirements.txt`, then `poetry.lock`, then `pyproject.toml`.
- `pyproject.toml` / `poetry.lock` paths install project dependencies via `pip install .`.

#### Example

```yaml
name: ci
on: [push, pull_request]

jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-python.yml@v1
    with:
      python-version: "3.12"
      dependency-file: "requirements.txt"
      lint-paths: "."
      test-paths: "tests/"
      coverage-min: 80
      run-audit: true
```

---

### ci-with-sonar-go.yml / ci-with-sonar-node.yml / ci-with-sonar-python.yml — Language CI + Sonar Bundle

Reusable bundle workflows that run language CI and SonarQube in parallel. Sonar credentials are fetched per-run from Doppler, so consumer repositories only store `DOPPLER_TOKEN`.

**Trigger:** `workflow_call`

Each bundle re-declares every input from its underlying workflow:

| Bundle workflow | Underlying CI workflow |
| --- | --- |
| `ci-with-sonar-go.yml` | `ci-go.yml` |
| `ci-with-sonar-node.yml` | `ci-node.yml` |
| `ci-with-sonar-python.yml` | `ci-python.yml` |

#### Additional Inputs (all bundles)

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `sonar-working-directory` | string | no | `.` | Directory containing source and `sonar-project.properties` |
| `sonar-quality-gate-timeout-minutes` | number | no | `5` | Minutes to wait for quality gate result |
| `sonar-extra-scan-args` | string | no | `""` | Extra scanner args passed to SonarQube scan action |
| `doppler-project` | string | no | `sonar` | Doppler project containing `SONAR_TOKEN` and `SONAR_HOST_URL` |
| `doppler-config` | string | no | `prd` | Doppler config name |
| `__test_skip_sonar_call` | boolean | no | `false` | Internal test harness escape hatch; skip Sonar job when `true` |

#### Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `DOPPLER_TOKEN` | **yes** | Read-only Doppler service token used to fetch Sonar credentials at runtime |

#### Behavior

1. `ci` job calls the underlying language workflow (`ci-go.yml`, `ci-node.yml`, or `ci-python.yml`) using local relative refs.
2. `sonar-scan` job installs `dopplerhq/cli-action`, fetches and masks `SONAR_TOKEN` / `SONAR_HOST_URL`, then runs Sonar scan + quality gate.
3. Sonar status-check context in consumer repositories is expected to appear as `ci / sonar-scan`.

#### Example

```yaml
name: ci
on: [push, pull_request]

jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-go.yml@v1
    with:
      go-version: "1.26.2"
      coverage-min: "65.0"
      sonar-working-directory: "."
      sonar-quality-gate-timeout-minutes: 5
      sonar-extra-scan-args: ""
      doppler-project: "sonar"
      doppler-config: "prd"
    secrets:
      DOPPLER_TOKEN: ${{ secrets.DOPPLER_TOKEN }}
```

---

### ci-terraform.yml — Terraform CI Pipeline

Reusable CI workflow for Terraform projects with formatting checks, validation, planning, and optional IaC security audit.

**Trigger:** `workflow_call`

Cloud credentials are caller-managed and should be configured in the consumer workflow (environment variables or OIDC).

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `terraform-version` | string | no | `1.9.8` | Terraform version to install |
| `working-directory` | string | no | `.` | Directory containing Terraform configuration |
| `var-file` | string | no | `""` | Optional var-file passed to `terraform plan` |
| `backend-config` | string | no | `""` | Optional backend config (`key=value` or file path) passed to `terraform init` |
| `run-audit` | boolean | no | `true` | Run IaC security audit job |
| `audit-tool` | string | no | `tfsec` | Audit tool (`tfsec` or `checkov`) |
| `upload-plan` | boolean | no | `true` | Upload `tfplan` artifact on pull request events |

#### Jobs

1. **fmt** — `terraform fmt -check -recursive`
2. **validate** — `terraform init -backend=false` and `terraform validate`
3. **plan** — `terraform init`, `terraform plan -out=tfplan -input=false`, optional `tfplan` artifact upload on PR events
4. **audit** — optional `tfsec` (default) or `checkov` scan

#### Example

```yaml
name: terraform-ci
on: [push, pull_request]

jobs:
  terraform:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-terraform.yml@v1
    with:
      terraform-version: "1.9.8"
      working-directory: "infra"
      var-file: "envs/dev.tfvars"
      backend-config: "envs/dev.backend.hcl"
      run-audit: true
      audit-tool: "tfsec"
      upload-plan: true
```

---

### docker-build.yml — Docker Build + Push

Builds a Docker image with Buildx, extracts standard metadata/tags, and pushes to GHCR on the default branch. Optional inputs can also generate SBOMs, sign by digest, attest the SBOM, and emit SLSA provenance. The `sign-image` and `attest-sbom` jobs self-verify (`cosign verify` / `cosign verify-attestation --type spdxjson`) against the pushed digest, so they fail closed if no signature/attestation is produced.

The SBOM attestation is published in cosign's **legacy `.att` bundle format** (`--new-bundle-format=false`). cosign v3 defaults to the new Sigstore bundle format attached via the OCI 1.1 referrers API, which is not reliably retrievable on GHCR (`cosign verify-attestation` fails with `no valid bundles exist in registry`). Consumers verifying the attestation on cosign v3 must therefore pass the same flag:

```bash
cosign verify-attestation \
  --new-bundle-format=false \
  --type spdxjson \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/docker-build\.yml@refs/.+$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/<owner>/<image>@sha256:<digest>
```

> **Self-hosted runner prerequisite (`sign-image` / `attest-sbom`):** cosign install needs `envsubst` (from `gettext-base`). The preflight installs it automatically when missing — running `apt-get` directly as root, or via `sudo` when passwordless sudo is available. On locked-down images that are non-root **and** lack passwordless sudo, preinstall `gettext-base` in the runner image; otherwise the job fails fast with an actionable message. GitHub-hosted `ubuntu-latest` already ships `gettext-base`.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `image-name` | string | **yes** | — | Full image name (e.g. `ghcr.io/nandohdc/my-app`) |
| `context` | string | no | `.` | Docker build context |
| `dockerfile` | string | no | `Dockerfile` | Dockerfile path |
| `push` | boolean | no | `true` | Push to registry |
| `platforms` | string | no | `linux/amd64` | Target platforms |
| `build-args` | string | no | `""` | Build arguments (newline-separated) |
| `extra-tags` | string | no | `""` | Extra metadata-action tag rules |
| `generate-sbom` | boolean | no | `false` | Generate SPDX + CycloneDX SBOM artifacts for pushed images |
| `generate-provenance` | boolean | no | `false` | Generate SLSA provenance attestation for pushed images |
| `sign-image` | boolean | no | `false` | Sign pushed image digest using cosign keyless mode (the job self-verifies with `cosign verify`) |
| `attest-sbom` | boolean | no | `false` | Emit the SPDX SBOM as a cosign attestation (`cosign attest --type spdxjson --new-bundle-format=false --use-signing-config=false`) on the pushed digest; the job self-verifies with `cosign verify-attestation` and fails if no attestation is produced |

#### Permissions

Default caller permissions stay minimal:
- `contents: read`
- `packages: write`

When optional hardening inputs are enabled, caller permissions must include:

| Input | Additional caller permissions |
| --- | --- |
| `generate-sbom` | `actions: write` |
| `sign-image` | `id-token: write` |
| `attest-sbom` | `actions: write`, `id-token: write` |
| `generate-provenance` | `id-token: write`, `attestations: write` |

#### Outputs

| Output | Description |
| --- | --- |
| `digest` | Image digest (`sha256:...`) |
| `tags` | Pushed image tags |
| `metadata` | JSON build result metadata |

#### Example

```yaml
name: docker
on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    uses: nandohdc/gh-pipelines/.github/workflows/docker-build.yml@v1
    with:
      image-name: ghcr.io/nandohdc/my-app
```

---

### docker-release.yml — Supply-Chain Security Release

Builds a release image on tag push with full supply-chain security: SBOM generation (SPDX + CycloneDX), cosign keyless signing, and SLSA provenance attestation.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `image-name` | string | **yes** | — | Full image name |
| `context` | string | no | `.` | Build context |
| `dockerfile` | string | no | `Dockerfile` | Dockerfile path |
| `platforms` | string | no | `linux/amd64` | Target platforms |
| `build-args` | string | no | `""` | Build arguments |
| `attach-sbom-to-release` | boolean | no | `true` | Attach SBOMs to GitHub Release |

#### Outputs

| Output | Description |
| --- | --- |
| `digest` | Image digest |

#### Example

```yaml
name: release
on:
  push:
    tags: ["v*"]

jobs:
  release:
    uses: nandohdc/gh-pipelines/.github/workflows/docker-release.yml@v1
    with:
      image-name: ghcr.io/${{ github.repository }}
```

---

### helm-deploy.yml — Helm Deploy

Runs `helm upgrade --install` against a DigitalOcean Kubernetes cluster. Performs dry-run on pull requests, actual deployment on push to main.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `release-name` | string | **yes** | — | Helm release name |
| `chart` | string | **yes** | — | Chart reference (repo or local path) |
| `namespace` | string | **yes** | — | Kubernetes namespace |
| `values-file` | string | **yes** | — | Path to values.yaml |
| `chart-version` | string | no | `""` | Chart version (omit for local charts) |
| `cluster-id` | string | no | `adc9fd65-...` | DO cluster UUID |
| `timeout` | string | no | `10m` | Helm timeout |
| `extra-helm-args` | string | no | `""` | Additional helm arguments |
| `post-deploy-kubectl` | string | no | `""` | Post-deploy kubectl commands |
| `extra-helm-repos` | string | no | `""` | Extra Helm repos (`name=url`) |

#### Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `digitalocean-token` | **yes** | DigitalOcean PAT |

#### Example

```yaml
name: deploy
on:
  push:
    branches: [main]
  pull_request:

jobs:
  deploy:
    uses: nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v1
    with:
      release-name: my-app
      chart: ./chart
      namespace: my-app
      values-file: values.yaml
    secrets:
      digitalocean-token: ${{ secrets.DIGITALOCEAN_ACCESS_TOKEN }}
```

---

### security-scan.yml — Trivy Container Scan

Scans a Docker image for known CVEs with configurable severity threshold.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `image-ref` | string | **yes** | — | Image to scan |
| `build-image` | boolean | no | `false` | Build from Dockerfile first |
| `dockerfile` | string | no | `Dockerfile` | Dockerfile path (if building) |
| `context` | string | no | `.` | Build context (if building) |
| `severity` | string | no | `HIGH,CRITICAL` | Severity filter |
| `exit-code` | string | no | `1` | Exit code on findings |
| `format` | string | no | `table` | Output format |
| `upload-sarif` | boolean | no | `false` | Upload to GitHub Security tab |
| `ignore-unfixed` | boolean | no | `false` | Ignore unfixed CVEs |

#### Example

```yaml
name: security
on: [push, pull_request]

jobs:
  scan:
    uses: nandohdc/gh-pipelines/.github/workflows/security-scan.yml@v1
    with:
      image-ref: my-app:ci
      build-image: true
      upload-sarif: true
```

---

### sonar-scan.yml — SonarQube Scan + Quality Gate

Runs SonarQube analysis and waits for quality gate status. The workflow always executes both scan and quality gate steps; a failed gate fails the job.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `working-directory` | string | no | `.` | Directory containing source and `sonar-project.properties` |
| `quality-gate-timeout-minutes` | number | no | `5` | Minutes to wait for quality gate result |
| `extra-scan-args` | string | no | `""` | Extra scanner args passed to SonarQube scan action |

#### Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `SONAR_TOKEN` | **yes** | SonarQube token |
| `SONAR_HOST_URL` | **yes** | SonarQube server URL |

#### Consumer Prerequisites

1. Create repository secrets `SONAR_TOKEN` and `SONAR_HOST_URL`.
2. Create `sonar-project.properties` in the consumer repository with at least `sonar.projectKey=...`.
3. Keep `sonar.projectKey` in `sonar-project.properties`; this workflow intentionally does not expose a `project-key` input.
4. In the caller job, set `permissions` to include `contents: read` and `pull-requests: read`.

#### Example

```yaml
name: sonar
on: [push, pull_request]

jobs:
  sonar:
    permissions:
      contents: read
      pull-requests: read
    uses: nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1
    with:
      working-directory: "."
      quality-gate-timeout-minutes: 5
      extra-scan-args: ""
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
```

---

## Composite Actions

### actions/setup-go

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `go-version` | no | `1.26.2` | Go version |
| `cache` | no | `true` | Enable module cache |

### actions/setup-node

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `node-version` | no | `20` | Node.js version |
| `package-manager` | no | `npm` | Package manager (`npm`, `pnpm`, `yarn`) |
| `cache` | no | `true` | Enable dependency cache for selected manager |

### actions/setup-python

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `python-version` | no | `3.12` | Python version |
| `dependency-file` | no | `requirements.txt` | Primary dependency file used for pip cache detection |
| `cache` | no | `true` | Enable pip cache |

### actions/setup-terraform

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `terraform-version` | no | `1.9.8` | Terraform version |
| `cache` | no | `true` | Enable provider plugin cache |

### actions/setup-helm

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `helm-version` | no | `latest` | Helm version |
| `extra-repos` | no | `""` | Extra repos (`name=url`, newline-separated) |

Pre-configured repos: `ingress-nginx`, `jetstack`, `external-secrets`, `sonarqube`.

### actions/setup-doctl

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `digitalocean-token` | **yes** | — | DO PAT |
| `cluster-id` | **yes** | — | Cluster UUID |

### actions/docker-meta

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `image-name` | **yes** | — | Full image name |
| `extra-tags` | no | `""` | Additional tag rules |

Default tags: `sha-<short>`, `latest` (on default branch), `ref:tag`.

| Output | Description |
| --- | --- |
| `tags` | Generated tags |
| `labels` | OCI labels |
| `version` | Resolved version |

### actions/supply-chain-attest

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `image-name` | **yes** | — | Full image name |
| `image-digest` | **yes** | — | Image digest (`sha256:...`) |
| `generate-sbom` | no | `false` | Generate SPDX + CycloneDX SBOM artifacts |
| `generate-provenance` | no | `false` | Generate SLSA provenance attestation |
| `sign-image` | no | `false` | Sign image by digest using cosign keyless mode |
| `attest-sbom` | no | `false` | Emit the SPDX SBOM as a cosign attestation (`cosign attest --type spdxjson --new-bundle-format=false --use-signing-config=false`) on the image digest |
| `sbom-artifact-suffix` | no | `run` | Suffix appended to SBOM artifact names |
| `sbom-spdx-output` | no | `sbom.spdx.json` | Output path for SPDX SBOM |
| `sbom-cyclonedx-output` | no | `sbom.cyclonedx.json` | Output path for CycloneDX SBOM |
