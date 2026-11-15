## Summary

<!-- What changed and why? -->

## Compatibility Impact

<!-- backward-compatible | breaking -->

## Security And Permissions Review

<!-- Note permission changes, secret handling, and trust boundaries -->

## Release Label

Apply exactly one label before merge when a release is needed:
- `release:patch` (within target major lineage)
- `release:minor` (within target major lineage)
- `release:major` (starts a new major lineage on `main` without moving prior floating major tags; required for breaking default/interface changes)

No release label means no release action.

## Code Review Checklist

- [ ] Every workflow/action input has description, type, required flag, and default (if optional)
- [ ] Workflow permissions are least-privilege
- [ ] All `shell: bash` steps use `set -euo pipefail`
- [ ] No hardcoded secrets or tokens
- [ ] Header comment includes purpose and example caller (for reusable workflows)
- [ ] `docs/USAGE.md` updated (if interface/behavior changed)
- [ ] `README.md` updated (if interface/behavior changed)
- [ ] Relevant example in `examples/` added or updated (if interface/behavior changed)
