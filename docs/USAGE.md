# Usage Guide

Complete reference for every reusable workflow and composite action in this repository.

---

## Consumer Terminology

A **Consumer repository** adopts a published gh-pipelines artifact. A
**Workflow consumer** owns one or more **Caller workflows**; each job-level use
of a reusable workflow is a **Workflow invocation**. A
**Composite-action consumer** uses a composite action directly and is not
necessarily a Workflow consumer.

---

## Versioning, Compatibility, and Major Migration

`gh-pipelines` publishes independent compatible **Major lineages** (`v1`, `v2`, …):

- **Floating major tags (`@v1`, `@v2`, …)**: Recommended for both Workflow consumers and Composite-action consumers. Compatible `patch` and `minor` releases advance only the matching Floating major tag (`v1.x.y` -> `v1`, `v2.x.y` -> `v2`).
- **Exact release tags (`@v1.10.0`, `@v2.0.0`, …)**: Immutable point tags that never move once published.
- **Breaking changes & deliberate migration**: Breaking interface or default-behavior changes (for example, tightening default deployment restrictions) always start a new Major lineage (`v2.0.0` + `v2`) and never move `v1`. Existing consumers stay on `@v1` safely and opt into `@v2` by updating their `uses:` references after checking [CHANGELOG.md](../CHANGELOG.md) and the workflow inputs below.
- **Consistent adoption**: When migrating a Caller workflow to a new major, update both reusable Workflow invocations (`.github/workflows/*.yml@v2`) and any direct Composite-action `uses:` (`actions/*@v2`) together. Internal composite-action references inside reusable workflows stay pinned to their own Major lineage.

---

## Runner Selection

Every reusable workflow exposes an optional `runner-label` input. Jobs resolve `runs-on` to the supplied label, or fall back to GitHub-hosted `ubuntu-latest` when the input is empty or omitted.

### Workflow consumer usage

In a Caller workflow, pass the label explicitly in each Workflow invocation:

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

Repository-owned automation selects GitHub-hosted `ubuntu-latest` explicitly. This includes fixture callers, release, security scanning, dependency automation, and scheduled/manual probes. A repository or organization `RUNNER_LABEL` does not affect internal jobs. The integration probe retains its deprecated `runner-label` dispatch input for compatibility and ignores it. Existing jobs queued on an older revision keep their original runner selection.

### Self-hosted runner prerequisites

| Workflow | Required extras |
| --- | --- |
| `ci-go.yml` | `build-essential` (installed on demand), `CGO_ENABLED=1` set automatically |
| `helm-deploy.yml` | `kubectl` — installed by `actions/setup-doctl` composite |
| `docker-build.yml`, `docker-release.yml` | Docker daemon |

Use a runner image broad enough for all jobs the workflow triggers.

---

## SonarQube Scanner/Public URL Contract

Sonar workflows are deprecated across the environment. The following reference preserves existing `v1` behavior for consumers during migration; do not adopt it for new integrations.

SonarQube workflows use two different URL roles. Keep them separate in Consumer repositories:

| Role | Preferred setting | Local-kind ARC value | Used by |
| --- | --- | --- | --- |
| Scanner server URL | `sonar-scanner-host-url` input or repository/org variable `SONAR_SCANNER_HOST_URL` | `http://sonarqube.sonarqube.svc.cluster.local:9000` | SonarSource scan action and SonarQube quality-gate action |
| Public base URL | `sonar-public-base-url` input or repository/org variable `SONAR_PUBLIC_BASE_URL` | Stable HTTPS hostname for browser UI and generated links | Documentation/link context only; not scanner traffic |
| Legacy scanner fallback | `SONAR_HOST_URL` secret/value | Same value as the scanner server URL when compatibility fallback is needed | Backward-compatible fallback only |
| Runner placement | `runner-label` input fed from `RUNNER_LABEL` | ARC label that can reach the `sonarqube` Service, for example `arc-runners-<service>` | GitHub Actions runner selection |

For local-kind SonarQube, set `SONAR_SCANNER_HOST_URL` to `http://sonarqube.sonarqube.svc.cluster.local:9000` and pass a `RUNNER_LABEL` value for the ARC runner scale set that runs inside the cluster. Use `SONAR_PUBLIC_BASE_URL` only for the stable public HTTPS UI/base URL; do not make scanners depend on the public tunnel when an ARC runner can use the private Service URL.

Availability behavior is intentionally fail-closed:

- When the local Mac/kind/ARC runtime is offline, GitHub cannot assign a matching self-hosted runner, so jobs stay queued or pending until the runner returns.
- When a matching runner is online but SonarQube is unreachable, starting, or unhealthy, the workflow preflight fails before scanning and the quality gate cannot produce a green check without a contacted SonarQube server.
- Untrusted fork pull requests do not receive Sonar or Doppler secrets. Bundle workflows refuse before Doppler access; examples use `pull_request` (not `pull_request_target`) and read-only permissions.

Do not configure custom tunnel/auth headers in these reusable workflows. The selected contract is direct scanner reachability from the chosen runner plus a separate public base URL for UI/link generation.

---

## Reusable Workflows

### ci-go.yml — Go CI Pipeline

Full CI pipeline for Go services including linting, testing, vulnerability scanning, and optional Kubernetes/Prometheus manifest validation.

**Trigger:** `workflow_call`

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `go-version` | string | no | `1.26.2` | Go version to install |
| `cache-dependency-path` | string | no | `""` | Module `go.sum` used for caching; empty resolves to `<working-directory>/go.sum` and disables caching when absent |
| `golangci-lint-version` | string | no | `v1.64.8` | golangci-lint version |
| `golangci-lint-major` | string | no | `1` | golangci-lint major (`1` or `2`), selecting the corresponding Go module path |
| `coverage-min` | string | no | `65.0` | Minimum coverage percentage |
| `coverage-packages` | string | no | `""` | Space-separated packages for coverage (empty = all) |
| `coverage-artifact-name` | string | no | `coverage` | Published coverage artifact name; make it unique for parallel Caller jobs |
| `integration-coverage-artifact-name` | string | no | `integration-coverage` | Published integration coverage artifact name; make it unique for parallel Caller jobs |
| `run-build` | boolean | no | `false` | Run `go build ./...` before unit tests |
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

For a repository with several independent Go modules, call `ci-go.yml` once
per module. Pass that module's toolchain and `go.sum` path, choose the
golangci-lint major that matches its configuration, and give every Caller job
unique unit and integration coverage artifact names. A later `sonar-scan.yml`
Workflow invocation can download the unit artifacts with `coverage-artifact-pattern` before
its `pre-scan-command` merges or otherwise prepares the coverage report.

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

Package manager activation and cache order:
- Each job initializes Node.js first, enables `corepack` (setting `COREPACK_ENABLE_DOWNLOAD_PROMPT=0` when `package-manager` is `pnpm` or `yarn`), and only then restores/configures the package-manager dependency cache so clean runners succeed without preinstalled `pnpm` or `yarn` binaries.
- When using `pnpm` or `yarn`, declare `"packageManager"` (for example `"pnpm@9.15.4"` or `"yarn@1.22.22"`) and commit the corresponding lockfile (`pnpm-lock.yaml` or `yarn.lock`).

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

**Deprecated:** existing `v1` callers retain their behavior; use language CI without Sonar for new integrations.

Reusable bundle workflows that run language CI and SonarQube in parallel. Sonar credentials are fetched per-run from Doppler, so Workflow consumer repositories only store `DOPPLER_TOKEN`.

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
| `sonar-scanner-host-url` | string | no | `""` | Preferred non-secret scanner URL used by both scan and quality gate; empty falls back to `SONAR_SCANNER_HOST_URL` variable/Doppler, then legacy `SONAR_HOST_URL` in Doppler |
| `sonar-public-base-url` | string | no | `""` | Optional public SonarQube UI/base URL for generated links; not used by scanner calls |
| `doppler-project` | string | no | `sonar` | Doppler project containing `SONAR_TOKEN` and optional Sonar URL secrets |
| `doppler-config` | string | no | `prd` | Doppler config name |
| `__test_skip_sonar_call` | boolean | no | `false` | Internal test harness escape hatch; skip Sonar job when `true` |
| `runner-label` | string | no | `""` | Self-hosted runner label; set to the local ARC label when using the local-kind scanner Service URL |

#### Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `DOPPLER_TOKEN` | **yes** | Read-only Doppler service token used to fetch Sonar credentials at runtime |

#### Behavior

1. `ci` job calls the underlying language workflow (`ci-go.yml`, `ci-node.yml`, or `ci-python.yml`) using local relative refs.
2. `sonar-scan` job refuses untrusted fork PR contexts before Doppler access, installs `dopplerhq/cli-action`, fetches and masks `SONAR_TOKEN` plus any Doppler URL values, then resolves the scanner URL in order: input `sonar-scanner-host-url`, repository/org variable `SONAR_SCANNER_HOST_URL`, Doppler `SONAR_SCANNER_HOST_URL`, legacy Doppler `SONAR_HOST_URL`.
3. The optional public base URL resolves from input `sonar-public-base-url`, repository/org variable `SONAR_PUBLIC_BASE_URL`, or Doppler `SONAR_PUBLIC_BASE_URL`; it is diagnostic/link context only and is not used by scanner or quality-gate calls.
4. Scanner URL preflight validates syntax, DNS/connectivity, `/api/system/status` readiness (with `/api/server/version` fallback), and bounded retries before scan; `.scannerwork/report-task.txt` must exist and match the resolved scanner URL before the quality gate runs.
5. For local-kind ARC deployments, pass `runner-label: ${{ vars.RUNNER_LABEL }}` and set `SONAR_SCANNER_HOST_URL` to `http://sonarqube.sonarqube.svc.cluster.local:9000` so scan and quality gate traffic stay private.
6. If the local ARC runtime is offline, jobs stay queued/pending. If the runner is available but SonarQube is unreachable or unhealthy, the preflight fails before scanning and cannot yield a green SonarQube check.
7. Sonar status-check context in Workflow consumer repositories is expected to appear as `ci / sonar-scan`.

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
      sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}
      sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}
      runner-label: ${{ vars.RUNNER_LABEL }}
      doppler-project: "sonar"
      doppler-config: "prd"
    secrets:
      DOPPLER_TOKEN: ${{ secrets.DOPPLER_TOKEN }}
```

---

### ci-terraform.yml — Terraform CI Pipeline

Reusable CI workflow for Terraform projects with formatting checks, validation, optional planning, and optional IaC security audit.

**Trigger:** `workflow_call`

`workflow_call` jobs run on isolated runner VMs and **do not inherit** caller job steps, environment variables, or OIDC tokens (hosted runners never inherit caller secrets or ambient cloud credentials across reusable workflow boundaries).

- **Use `ci-terraform.yml@v1` when:** running credential-free static checks (`run-plan: false` skips remote backend init and `plan` while running `fmt`, `init -backend=false`, `validate`, and audit), running `plan` without external credentials (such as a providerless configuration), or targeting a downstream `runner-label` whose runner preconfigures ambient identity.
- **Compose a caller-owned job with `nandohdc/gh-pipelines/actions/setup-terraform@v1` when:** `terraform init` (remote backend) or `terraform plan` requires cloud OIDC or credentials. Keep your cloud auth step (provider-independent: AWS OIDC, GCP Workload Identity, Azure login, or `TF_TOKEN_*`), `actions/setup-terraform@v1`, and Terraform commands inside the **same** job with `permissions: { contents: read, id-token: write }`.
- **Verification boundary:** Providerless fixture runs (`tests/fixtures/terraform`) prove workflow syntax, env persistence, and Terraform CLI lifecycle execution; actual cloud OIDC acceptance requires your provider authentication step in a caller-owned job.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `terraform-version` | string | no | `1.9.8` | Terraform version to install |
| `working-directory` | string | no | `.` | Directory containing Terraform configuration |
| `var-file` | string | no | `""` | Optional var-file passed to `terraform plan` |
| `backend-config` | string | no | `""` | Optional backend config (`key=value` or file path) passed to `terraform init` |
| `run-plan` | boolean | no | `true` | Run `terraform plan` (backend init enabled; set `false` for credential-free static validation) |
| `run-audit` | boolean | no | `true` | Run IaC security audit job |
| `audit-tool` | string | no | `tfsec` | Audit tool (`tfsec` or `checkov`) |
| `tfsec-additional-args` | string | no | `""` | Additional `tfsec` CLI flags (`audit-tool=tfsec` only) |
| `upload-plan` | boolean | no | `true` | Upload `tfplan` artifact on pull request events |
| `runner-label` | string | no | `""` | Self-hosted runner label (empty uses `ubuntu-latest`) |

#### Jobs

1. **fmt** — `terraform fmt -check -recursive`
2. **validate** — `terraform init -backend=false` and `terraform validate`
3. **plan** (when `run-plan: true`) — `terraform init`, `terraform plan -out=tfplan -input=false`, optional `tfplan` artifact upload on PR events
4. **audit** (when `run-audit: true`) — optional `tfsec` (default) or `checkov` scan

#### Example 1 — Credential-free static validation via reusable workflow

```yaml
name: terraform-ci
on: [push, pull_request]

permissions:
  contents: read

jobs:
  terraform:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-terraform.yml@v1
    with:
      terraform-version: "1.9.8"
      working-directory: "infra"
      run-plan: false
      run-audit: true
      audit-tool: "tfsec"
```

#### Example 2 — Caller-composed authenticated job (same-job OIDC + `actions/setup-terraform@v1`)

Use a caller-owned job (`runs-on` + `steps`) instead of `workflow_call` whenever remote state `init` or `plan` needs cloud OIDC/credentials. Never combine `uses: .../ci-terraform.yml@v1` with `runs-on:` or `steps:` on the same job.

```yaml
name: terraform-plan
on: [push, pull_request]

jobs:
  terraform-plan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    defaults:
      run:
        working-directory: "infra"
    steps:
      - uses: actions/checkout@v7
      # Authenticate with your cloud provider in this same job before running Terraform
      # (for example: AWS OIDC, GCP Workload Identity, Azure login, or TF_TOKEN_* env).
      - uses: nandohdc/gh-pipelines/actions/setup-terraform@v1
        with:
          terraform-version: "1.9.8"
      - run: terraform fmt -check -recursive
      - run: terraform init -input=false -backend-config="envs/dev.backend.hcl"
      - run: terraform validate
      - run: terraform plan -input=false -no-color -var-file="envs/dev.tfvars"
```

---

### docker-build.yml — Docker Build + Push

Builds a Docker image with Buildx, extracts standard metadata/tags, and pushes to GHCR on the default branch. Optional inputs can also generate SBOMs, sign by digest, attest the SBOM, and emit SLSA provenance. The `sign-image` and `attest-sbom` jobs self-verify (`cosign verify` / `cosign verify-attestation --type spdxjson`) against the pushed digest, so they fail closed if no signature/attestation is produced.

The SBOM attestation is published in cosign's **legacy `.att` bundle format** (`--new-bundle-format=false`). cosign v3 defaults to the new Sigstore bundle format attached via the OCI 1.1 referrers API, which is not reliably retrievable on GHCR (`cosign verify-attestation` fails with `no valid bundles exist in registry`). Consumer repositories verifying the attestation on cosign v3 must therefore pass the same flag:

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

Default Caller job permissions stay minimal:
- `contents: read`
- `packages: write`

When optional hardening inputs are enabled, Caller job permissions must include:

| Input | Additional Caller job permissions |
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

**Deprecated:** existing `v1` callers retain their behavior; do not add new Sonar integrations.

Runs SonarQube analysis and waits for quality gate status. The workflow resolves a scanner URL, preflights it before scanning, validates `.scannerwork/report-task.txt`, and then runs the quality gate against the same scanner URL. A failed preflight, scan, report validation, or gate fails the job.

#### Inputs

| Input | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `working-directory` | string | no | `.` | Directory containing source and `sonar-project.properties` |
| `quality-gate-timeout-minutes` | number | no | `5` | Minutes to wait for quality gate result |
| `extra-scan-args` | string | no | `""` | Extra scanner args passed to SonarQube scan action |
| `pre-scan-command` | string | no | `""` | Shell command executed after checkout and tool setup but before the scan; use it to generate coverage reports (e.g. `pytest --cov --cov-report=xml`) |
| `pre-scan-python-version` | string | no | `""` | Optional Python version installed before `pre-scan-command`; empty skips Python setup |
| `coverage-artifact-pattern` | string | no | `""` | Optional artifact-name pattern downloaded before `pre-scan-command` (for example, `coverage-*`) |
| `coverage-artifact-path` | string | no | `coverage` | Workspace path where matching coverage artifacts are downloaded |
| `sonar-scanner-host-url` | string | no | `""` | Preferred non-secret scanner URL used by both scan and quality gate |
| `sonar-public-base-url` | string | no | `""` | Optional public SonarQube UI/base URL for generated links; not used for scanner calls |
| `runner-label` | string | no | `""` | Self-hosted runner label. Empty uses `ubuntu-latest` |

> [!NOTE]
> `pre-scan-command` runs inside `bash -eo pipefail -c`. Any intermediate command or pipeline failure aborts the job before SonarQube scanning starts. Callers that intentionally tolerate a non-zero preparation command should guard that command explicitly (for example, `my-tool || true`).

#### Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `SONAR_TOKEN` | **yes** | SonarQube token |
| `SONAR_SCANNER_HOST_URL` | no | Optional scanner URL secret, preferred over legacy `SONAR_HOST_URL` when the input is empty |
| `SONAR_PUBLIC_BASE_URL` | no | Optional public SonarQube UI/base URL secret; not used by scanner calls |
| `SONAR_HOST_URL` | no | Legacy scanner URL fallback |

#### Workflow Consumer Prerequisites

1. Create repository secret `SONAR_TOKEN`.
2. Provide one scanner URL source, in order of precedence: non-secret input `sonar-scanner-host-url`, secret `SONAR_SCANNER_HOST_URL`, or legacy secret `SONAR_HOST_URL`.
3. For local-kind ARC scanning, set `SONAR_SCANNER_HOST_URL` to `http://sonarqube.sonarqube.svc.cluster.local:9000` and pass `runner-label: ${{ vars.RUNNER_LABEL }}` for the ARC runner label that can reach the Service.
4. Optionally provide `sonar-public-base-url` / `SONAR_PUBLIC_BASE_URL` for UI/link terminology; scanner and quality-gate actions do not use it.
5. Create `sonar-project.properties` in the Workflow consumer repository with at least `sonar.projectKey=...`.
6. Keep `sonar.projectKey` in `sonar-project.properties`; this workflow intentionally does not expose a `project-key` input.
7. In the Caller job, set `permissions` to include `contents: read` and `pull-requests: read`.
8. Expect queued/pending jobs while the local ARC runtime is offline; expect fail-fast preflight errors, not a green check, when a runner is available but SonarQube is unreachable or unhealthy.

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
      pre-scan-command: ""
      pre-scan-python-version: ""
      coverage-artifact-pattern: ""
      coverage-artifact-path: "coverage"
      sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}
      sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}
      runner-label: ${{ vars.RUNNER_LABEL }}
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      SONAR_SCANNER_HOST_URL: ${{ secrets.SONAR_SCANNER_HOST_URL }}
      SONAR_PUBLIC_BASE_URL: ${{ secrets.SONAR_PUBLIC_BASE_URL }}
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

For `pnpm` and `yarn`, `actions/setup-node` installs Node.js, activates the requested package manager via `corepack enable` (with `COREPACK_ENABLE_DOWNLOAD_PROMPT=0`), and then initializes dependency caching when `cache` is `true`.

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
| `platforms` | no | `""` | Comma-separated platforms the image was built for (e.g. `linux/amd64,linux/arm64`). SBOMs are generated for the first listed platform, so runner arch no longer has to match the image. Empty lets syft auto-detect from the runner's platform |
| `sbom-artifact-suffix` | no | `run` | Suffix appended to SBOM artifact names |
| `sbom-spdx-output` | no | `sbom.spdx.json` | Output path for SPDX SBOM |
| `sbom-cyclonedx-output` | no | `sbom.cyclonedx.json` | Output path for CycloneDX SBOM |
