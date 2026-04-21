# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`gh-pipelines` is **not** an application. It is a library of reusable GitHub Actions artifacts consumed by other repositories in the `nandohdc` ecosystem via `uses: nandohdc/gh-pipelines/...@v1`. There is no build, no runtime, and no test suite beyond YAML linting — correctness is validated by (a) `actionlint` and (b) pointing a consumer repo's workflow at a branch and running it in GitHub.

The repo exposes two artifact kinds:

- **Reusable workflows** in `.github/workflows/` — each uses `on: workflow_call` and is referenced as `nandohdc/gh-pipelines/.github/workflows/<name>.yml@v1`.
- **Composite actions** in `actions/<name>/action.yml` — referenced as `nandohdc/gh-pipelines/actions/<name>@v1`.

Reusable workflows compose composite actions (e.g. `ci-go.yml` calls `actions/setup-go`, `helm-deploy.yml` calls both `actions/setup-doctl` and `actions/setup-helm`). When editing, prefer pushing shared logic down into a composite action rather than duplicating steps across workflows.

## Commands

```bash
# Lint every workflow (requires: brew install actionlint)
actionlint .github/workflows/*.yml

# Lint a single workflow
actionlint .github/workflows/ci-go.yml
```

To integration-test a change, push it to a branch and point a consumer repo at that ref — e.g. `uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@my-branch`. There is no way to exercise these workflows locally in this repo.

## Release model (important)

Consumers pin to the floating major tag `@v1`, so **moving that tag is what ships changes to every consumer simultaneously**. Releases are two tag operations, not just one:

```bash
git tag v1.2.0 && git push origin v1.2.0
git tag -f v1 v1.2.0 && git push origin v1 --force
```

Backward-compatible additions (new optional inputs, bug fixes) move `v1`. Breaking changes (removing/renaming inputs, changing defaults that affect callers) require a new major — do not move `v1` for those.

## Cross-cutting conventions

These are enforced across every workflow/action — match them when adding or editing:

- Every `workflow_call` input has `description`, `type`, `required`, and a `default` if optional.
- `permissions:` is set at the workflow level to least-privilege. `docker-release.yml` is the one workflow that needs the broad set (`contents: write`, `packages: write`, `id-token: write`, `attestations: write`) because of cosign keyless signing + SLSA provenance + GitHub Release asset upload.
- All `shell: bash` steps start with `set -euo pipefail`.
- Every reusable workflow file has a header comment block with purpose + example caller snippet, and a matching example file in `examples/`.
- Changes to a workflow's inputs/outputs must also update `docs/USAGE.md` and the table in `README.md`.

## Notable cross-file coupling

- `helm-deploy.yml` hardcodes a DigitalOcean `cluster-id` default (`adc9fd65-…`). Callers can override, but changing that default is a breaking change for repos that rely on it.
- `ci-go.yml` conditionally shells out to consumer-repo files that this repo doesn't own: `./scripts/check-gofmt.sh` (optional, falls back to inline `gofmt`), `./scripts/check-import-direction.sh` (when `run-import-direction: true`), and `make contract` (when `run-contract: true`). When modifying those jobs, preserve the opt-in toggles — consumers without those scripts rely on the defaults staying `false`.
- `docker-release.yml` references the image by digest (`${image}@${digest}`) for SBOM, cosign, and attestation steps. Keep that pattern — signing/attesting by tag is racy.
