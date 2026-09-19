# Integration Consumer Canaries

This document tracks the real consumer repositories used by `.github/workflows/integration-smoke.yml`.

## Current Canaries

| Consumer | Language | Why this canary |
| --- | --- | --- |
| `nandohdc/financial-events-ingestion-api` | Go | Active backend workload with strict CI requirements (lint, contract checks, integration tests, supply-chain checks). |

Selection criteria used:

- Repository is active (recent commits, maintained workload).
- Repository behavior is representative for its ecosystem.
- Repository owner is the same maintainer of `gh-pipelines`, making it a safe early-warning canary.

## Multi-module Go Consumer Contract

Consumers with independent Go modules call `ci-go.yml` once per module. Each
caller must pass its module toolchain, `go.sum` cache path, compatible
golangci-lint major, and unique unit and integration coverage artifact names.
When SonarQube needs the aggregate unit coverage report, its `sonar-scan.yml`
caller passes the unique artifact prefix through `coverage-artifact-pattern` and
merges the downloaded profiles in `pre-scan-command`. This preserves isolation
until the one consumer-owned aggregation step and lets integration smoke detect
the Sonar workflow reference.

## Smoke Implementation (Current)

`integration-smoke.yml` runs a parse-based integration gate against each canary:

1. Clone the selected consumer at `main`.
2. Rewrite any `uses: nandohdc/gh-pipelines/.github/workflows/...@<ref>` references to `@main`.
3. Require at least one Sonar integration path:
   - `sonar-scan.yml`, or
   - a `ci-with-sonar-<lang>.yml` bundle, or
   - `.github/SONAR_EXEMPT` marker file at repo root.
4. Run `actionlint` on the rewritten workflow set.
5. On failure, open an issue in `gh-pipelines` with idempotent title-based dedup:
   - `sonar-missing` label for missing Sonar coverage
   - `drift` label for all other parse/lint failures

The consumer repository is private, so the workflow requires `INTEGRATION_PAT` with read access to the active canary repo.

## Sonar Bundle Secret Model

Consumer repos using `ci-with-sonar-*` should only store:

- `DOPPLER_TOKEN` (read-only service token scoped to Doppler project `sonar`, config `prd`)

The bundles always fetch `SONAR_TOKEN` from Doppler and resolve the scanner URL from non-secret input/variable values before falling back to Doppler `SONAR_SCANNER_HOST_URL` or legacy Doppler `SONAR_HOST_URL`. Secret URL values are masked in logs and exported only for the active workflow run.

Recommended non-secret variables for local-kind consumers (passed to reusable workflows as `runner-label`, `sonar-scanner-host-url`, and `sonar-public-base-url` inputs):

| Variable | Recommended value | Purpose |
| --- | --- | --- |
| `RUNNER_LABEL` | ARC runner scale-set label for the consumer, for example `arc-runners-<service>` | Places scan and quality-gate work on runners that can reach the in-cluster Service |
| `SONAR_SCANNER_HOST_URL` | `http://sonarqube.sonarqube.svc.cluster.local:9000` | Private scanner server URL used by both SonarSource scan and quality-gate actions |
| `SONAR_PUBLIC_BASE_URL` | Stable public HTTPS SonarQube hostname | Browser UI/base URL for generated links; not used by scanner traffic |

`SONAR_HOST_URL` remains a legacy scanner URL fallback for older consumers. Do not set it to a public UI hostname unless that hostname is the selected scanner endpoint; new local-kind consumers should prefer `SONAR_SCANNER_HOST_URL`.

## Local ARC Availability Behavior

When the local Mac/kind/ARC runtime is offline, GitHub has no matching self-hosted runner for `RUNNER_LABEL`; jobs queue, stay queued, or remain pending until the runner returns. When a matching runner is available but SonarQube is unreachable, starting, or unhealthy, the preflight fails before SonarSource actions run and the SonarQube quality gate cannot report green without contacting SonarQube.

Use `pull_request` rather than `pull_request_target` in consumer examples. Untrusted fork pull requests do not receive `DOPPLER_TOKEN` or Sonar secrets, and the bundle workflows refuse before Doppler access.

Do not configure custom tunnel/auth headers in consumer workflows; direct scanner reachability from the selected runner is the supported contract.

## Branch Protection Helper

Use `scripts/enforce-sonar-branch-protection.sh` from this repository to append the Sonar required-check context without replacing existing required checks:

```bash
./scripts/enforce-sonar-branch-protection.sh nandohdc/financial-events-ingestion-api "ci / sonar-scan" main
```

If the context already exists, the script no-ops.

## Add Or Rotate Consumers

1. Pick a candidate repository and confirm it is active and representative.
2. Confirm the owner agrees to canary participation.
3. Add (or replace) the consumer entry in the matrix inside `.github/workflows/integration-smoke.yml`.
4. Add (or replace) the corresponding row in this document.
5. Run `integration-smoke` manually (`workflow_dispatch`) with `consumer=<new-consumer-key>` and confirm it completes.
6. Keep the old canary for at least one green nightly cycle before removing it, unless an urgent rotation is needed.

## Secret Rotation

- Secret name: `INTEGRATION_PAT`.
- Minimum scope: repository read access to canary repos.
- Rotation cadence: at least every 90 days or immediately after a token exposure event.
- For consumer Sonar integration, rotate each repository `DOPPLER_TOKEN` at least every 90 days.
