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

The bundles fetch `SONAR_TOKEN` and `SONAR_HOST_URL` from Doppler at runtime, mask both values in logs, and export them only for the active workflow run.

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
