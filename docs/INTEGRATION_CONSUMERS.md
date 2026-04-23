# Integration Consumer Canaries

This document tracks the real consumer repositories used by `.github/workflows/integration-smoke.yml`.

## Current Canaries

| Consumer | Language | Why this canary |
| --- | --- | --- |
| `nandohdc/financial-events-ingestion-api` | Go | Active backend workload with strict CI requirements (lint, contract checks, integration tests, supply-chain checks). |
| `nandohdc/binance-extractor` | Python | Active Python workload with dependency-management and lint/test expectations different from Go services. |

Selection criteria used:

- Repository is active (recent commits, maintained workload).
- Repository behavior is representative for its ecosystem.
- Repository owner is the same maintainer of `gh-pipelines`, making it a safe early-warning canary.

## Smoke Implementation (Current)

`integration-smoke.yml` currently runs the parse-based fallback from issue `#38`:

1. Clone the selected consumer at `main`.
2. Rewrite any `uses: nandohdc/gh-pipelines/.github/workflows/...@<ref>` references to `@main`.
3. Run `actionlint` on the rewritten workflow set.
4. If validation fails, create a `drift` issue in `gh-pipelines` linking to the failing run.

The consumer repositories are private, so the workflow requires `INTEGRATION_PAT` with read access to both canary repos.

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
