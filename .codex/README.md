# Codex Setup

This folder defines Codex operating context for `gh-pipelines`.

Current baseline:

- Reusable workflows live in `.github/workflows/` and are consumed via `uses: nandohdc/gh-pipelines/.github/workflows/<name>.yml@v1`.
- Composite actions live in `actions/<name>/action.yml` and are consumed via `uses: nandohdc/gh-pipelines/actions/<name>@v1`.
- Internal self-CI lint workflow is in `.github/workflows/lint.yml` (issue `#1`, Phase 1, closed on `2026-04-21`).
- SonarQube reusable workflow is in `.github/workflows/sonar-scan.yml` (issue `#2`, Phase 2, closed on `2026-04-21`).
- Active roadmap issues:
  - `#3` open (Phase 3: Node.js CI reusable workflow)
  - `#4` open (Phase 4: Python CI reusable workflow)
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
