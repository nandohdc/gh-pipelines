# Integration Canary Consumers

This document tracks the real **Canary consumer repositories** used by
`.github/workflows/integration-smoke.yml`. A Canary consumer repository is an
opted-in Workflow consumer used for monitoring; it is not a different product
or execution mode.

Sonar is deprecated across the environment. Existing `v1` workflows remain available while consumers migrate; the compatibility details below are not onboarding guidance.

## Current Canaries

| Canary consumer repository | Language | Why this canary |
| --- | --- | --- |
| `nandohdc/financial-events-ingestion-api` | Go | Active backend workload with strict CI requirements (lint, contract checks, integration tests, supply-chain checks). |

Selection criteria used:

- Repository is active (recent commits, maintained workload).
- Repository behavior is representative for its ecosystem.
- Repository owner is the same maintainer of `gh-pipelines`, making it a safe early-warning canary.

## Multi-module Go Workflow Consumer Contract

A Workflow consumer with independent Go modules can use a Caller workflow with
one `ci-go.yml` Workflow invocation per module. Each invocation must pass its
module toolchain, `go.sum` cache path, compatible golangci-lint major, and
unique unit and integration coverage artifact names. When SonarQube needs the
aggregate unit coverage report, a `sonar-scan.yml` Workflow invocation passes
the unique artifact prefix through `coverage-artifact-pattern` and merges the
downloaded profiles in `pre-scan-command`. This preserves isolation until the
one aggregation step owned by the Workflow consumer. The deprecated Sonar integration is no longer required by integration smoke.

## Smoke Implementation (Current)

Integration smoke is a parse-and-lint drift probe against each Canary consumer
repository. It does not execute a consumer pipeline. `integration-smoke.yml`:

1. Clone the selected Canary consumer repository at `main`.
2. Rewrite any `uses: nandohdc/gh-pipelines/.github/workflows/...@<ref>` references to `@main`.
3. Run `actionlint` on the rewritten workflow set.
4. On failure, open a `drift` issue in `gh-pipelines` with idempotent title-based deduplication.

Sonar references and `.github/SONAR_EXEMPT` are no longer inspected. Both scheduled and manual probes run on GitHub-hosted `ubuntu-latest`; the deprecated manual `runner-label` input is ignored.

`@main` is a validation reference used only by integration smoke. It is not a
production onboarding reference for Canary consumer repositories or other
Workflow consumers.

The active Canary consumer repository is private, so the workflow requires
`INTEGRATION_PAT` with read access to it.

## Sonar Bundle Secret Model

Workflow consumers using `ci-with-sonar-*` should only store:

- `DOPPLER_TOKEN` (read-only service token scoped to Doppler project `sonar`, config `prd`)

The bundles always fetch `SONAR_TOKEN` from Doppler and resolve the scanner URL from non-secret input/variable values before falling back to Doppler `SONAR_SCANNER_HOST_URL` or legacy Doppler `SONAR_HOST_URL`. Secret URL values are masked in logs and exported only for the active workflow run.

Legacy non-secret variables for existing local-kind Workflow consumers (passed to reusable workflows as `runner-label`, `sonar-scanner-host-url`, and `sonar-public-base-url` inputs):

| Variable | Recommended value | Purpose |
| --- | --- | --- |
| `RUNNER_LABEL` | ARC runner scale-set label for the Workflow consumer, for example `arc-runners-<service>` | Places scan and quality-gate work on runners that can reach the in-cluster Service |
| `SONAR_SCANNER_HOST_URL` | `http://sonarqube.sonarqube.svc.cluster.local:9000` | Private scanner server URL used by both SonarSource scan and quality-gate actions |
| `SONAR_PUBLIC_BASE_URL` | Stable public HTTPS SonarQube hostname | Browser UI/base URL for generated links; not used by scanner traffic |

`SONAR_HOST_URL` remains a legacy scanner URL fallback for older Workflow consumers. Do not set it to a public UI hostname unless that hostname is the selected scanner endpoint; existing local-kind Workflow consumers can use `SONAR_SCANNER_HOST_URL`.

## Local ARC Availability Behavior

When the local Mac/kind/ARC runtime is offline, GitHub has no matching self-hosted runner for `RUNNER_LABEL`; jobs queue, stay queued, or remain pending until the runner returns. When a matching runner is available but SonarQube is unreachable, starting, or unhealthy, the preflight fails before SonarSource actions run and the SonarQube quality gate cannot report green without contacting SonarQube.

Use `pull_request` rather than `pull_request_target` in Workflow consumer examples. Untrusted fork pull requests do not receive `DOPPLER_TOKEN` or Sonar secrets, and the bundle workflows refuse before Doppler access.

Do not configure custom tunnel/auth headers in Caller workflows; direct scanner reachability from the selected runner is the supported contract.

## Retiring Sonar Checks

Do not add new Sonar required checks. For each existing Consumer repository, verify its remaining required tests and security checks before coordinating removal of its obsolete Sonar context. Preserve all unrelated branch protections. Shared Sonar service and credential retirement requires a separate consumer inventory and plan.

## Add Or Rotate Canary Consumer Repositories

1. Pick a candidate Workflow consumer repository and confirm it is active and representative.
2. Confirm the owner agrees to Canary consumer participation.
3. Add (or replace) the Canary consumer repository entry in the matrix inside `.github/workflows/integration-smoke.yml`.
4. Add (or replace) the corresponding row in this document.
5. Run `integration-smoke` manually (`workflow_dispatch`) with `consumer=<new-consumer-key>` and confirm it completes.
6. Keep the old canary for at least one green nightly cycle before removing it, unless an urgent rotation is needed.

## Secret Rotation

- Secret name: `INTEGRATION_PAT`.
- Minimum scope: repository read access to canary repos.
- Rotation cadence: at least every 90 days or immediately after a token exposure event.
- For Workflow consumer Sonar integration, rotate each repository `DOPPLER_TOKEN` at least every 90 days.
