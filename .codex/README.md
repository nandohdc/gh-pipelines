# Codex Setup

This folder defines Codex operating context for `gh-pipelines`.

Current baseline:

- Reusable workflows live in `.github/workflows/` and are consumed via `uses: nandohdc/gh-pipelines/.github/workflows/<name>.yml@v1`.
- Composite actions live in `actions/<name>/action.yml` and are consumed via `uses: nandohdc/gh-pipelines/actions/<name>@v1`.
- Internal self-CI lint workflow is in `.github/workflows/lint.yml` (issue `#1`, Phase 1, closed on `2026-04-21`).
- SonarQube reusable workflow is in `.github/workflows/sonar-scan.yml` (issue `#2`, Phase 2, closed on `2026-04-21`).
- Node.js reusable workflow is in `.github/workflows/ci-node.yml` (issue `#3`, Phase 3, closed on `2026-04-21`).
- Python reusable workflow is in `.github/workflows/ci-python.yml` (issue `#4`, Phase 4, closed on `2026-04-21`).
- Internal self-test harness is in `.github/workflows/test-callers.yml` (issue `#6`, Phase 6, closed on `2026-04-22`).
- Active roadmap issues:
  - `#7` Phase 7: pin third-party actions to SHAs + add Dependabot
  - `#8` Phase 8: release automation for `v1.x.y` and `v1`
  - `#9` Phase 9: Terraform reusable CI workflow
  - `#10` Phase 10: repository hygiene/docs/governance templates
  - `#11` Phase 11: docker-build hardening (SBOM/provenance/signing)
- Release model is semver tags plus floating major (`v1.x.y` and `v1`).

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
