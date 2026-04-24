# GEMINI.md

This file provides guidance to Gemini CLI when working with code in this repository.

## Repository Overview

`gh-pipelines` is a library of reusable GitHub Actions artifacts. It is **not** an application.

### Artifact Types
- **Reusable Workflows** (`.github/workflows/*.yml`): Exposed via `on: workflow_call`.
- **Composite Actions** (`actions/<name>/action.yml`): Encapsulate shared logic.

### Key Internal Workflows
- `lint.yml`: Runs `actionlint` and `yamllint`.
- `test-callers.yml`: Regression harness using fixtures in `tests/fixtures/`.
- `release.yml`: Automated semver tagging based on PR labels.
- `security-self-scan.yml`: Runs `zizmor` and `shellcheck`.
- `integration-smoke.yml`: Nightly drift detection against consumers.

## Development Workflow

### Commands
```bash
# Lint all workflows
actionlint .github/workflows/*.yml

# Lint with shellcheck integration
actionlint -shellcheck=shellcheck .github/workflows/*.yml

# Run security self-scan locally
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools UV_PYTHON_INSTALL_DIR=/tmp/uv-python \
  uvx --from zizmor zizmor --min-severity=high --config .zizmor.yml \
  .github/workflows/** actions/**/action.yml
```

### Validation Strategy
1. **Static Analysis**: Always run `actionlint` before finishing.
2. **Regression Testing**: Ensure `test-callers.yml` covers any changes to language-specific workflows.
3. **Manual Verification**: For significant changes, point a downstream repo at your branch:
   `uses: nandohdc/gh-pipelines/.github/workflows/<name>.yml@<your-branch>`

## Engineering Standards

- **Naming**: Use `kebab-case` for workflow and action filenames.
- **Inputs**: Every `workflow_call` input MUST have `description`, `type`, `required`, and `default` (if optional).
- **Permissions**: Set `permissions:` to least-privilege at the workflow or job level.
- **Shell**: Always start `shell: bash` steps with `set -euo pipefail`.
- **Versioning**:
  - Pin third-party actions to full **commit SHA** with a `# v<semver>` comment.
  - GitHub-owned actions (`actions/*`) can remain tag-pinned.
- **Documentation**: Update `README.md`, `docs/USAGE.md`, `examples/`, and `CHANGELOG.md` for any interface changes.
- **Dependabot**: Adding an action under `actions/` requires an entry in `.github/dependabot.yml`.

## Release Model

Releases are triggered by merging a PR to `main` with one of these labels:
- `release:patch` -> `vX.Y.(Z+1)`
- `release:minor` -> `vX.(Y+1).0`
- `release:major` -> `v(X+1).0.0` (Note: Only for new lineage, do not move `v1` for breaking changes).

The `v1` tag is a floating major tag that is force-moved automatically.

## Reference Materials

For deeper architectural context and standards, refer to:
1. `.codex/CONTEXT.md` (Authority #1)
2. `.codex/STANDARDS.md`
3. `AGENTS.md` / `CLAUDE.md`
