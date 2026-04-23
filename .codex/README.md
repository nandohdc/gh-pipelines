# Codex Setup

This folder defines Codex operating context for `gh-pipelines`.

Current baseline:

- Reusable workflows live in `.github/workflows/` and are consumed via `uses: nandohdc/gh-pipelines/.github/workflows/<name>.yml@v1`.
- Composite actions live in `actions/<name>/action.yml` and are consumed via `uses: nandohdc/gh-pipelines/actions/<name>@v1`.
- Internal maintenance workflows are `.github/workflows/lint.yml`, `.github/workflows/test-callers.yml`, and `.github/workflows/release.yml`.
- Docker build hardening is available via optional inputs in `.github/workflows/docker-build.yml` and shared helper logic in `actions/supply-chain-attest/action.yml`.
- Repository governance assets are present in `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`, `SECURITY.md`, and `CHANGELOG.md`.
- Tracker snapshot (synced `2026-04-23`):
  - Closed phases/issues: `#1`, `#2`, `#3`, `#4`, `#6`, `#7`, `#8`, `#9`, `#10`, `#11`, `#33`
  - Open phase issues: `#34`, `#35`, `#36`, `#37`, `#38`
- Release model is semver tags plus floating major (`v1.x.y` and `v1`), executed on pushes to `main` based on labels from the merged PR linked to the pushed commit.

Files:

- `CONTEXT.md` - repo scope, boundaries, architecture decisions, tracker state
- `STANDARDS.md` - implementation and review standards
- `CHECKLIST.md` - pre-completion verification checklist

Authority order:

1. `CONTEXT.md`
2. `STANDARDS.md`
3. `CHECKLIST.md`
4. `AGENTS.md`, `CLAUDE.md`, and root docs (`README.md`, `docs/*`)

If a proposed change conflicts with `CONTEXT.md`, follow `CONTEXT.md`.
