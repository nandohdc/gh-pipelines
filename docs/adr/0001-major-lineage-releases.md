# Use `release/v<N>` maintenance branches and ancestry-scoped major lineages

status: accepted

Breaking changes in reusable workflows or composite actions require a new Major lineage (`v2`, `v3`, …) without moving the existing `v1` Floating major tag. To keep older lineages maintainable after `main` advances to a newer major, automated releases isolate version calculation and Floating major tag updates per Major lineage: `main` resolves its lineage from the highest Exact release tag reachable in the commit's ancestry (establishing `v(M+1).0.0` and `v(M+1)` on `release:major`), while older lineages publish `patch` and `minor` updates through `release/v<N>` Maintenance branches whose branch creation pushes are skipped and where `release:major` is rejected.

## Considered Options

- **`main`-only automation with manual tagging for prior majors**: avoids extra workflow branch triggers, but forces maintainers off the PR-label CI/release path when backporting fixes to `v1` after `v2` ships.
- **`workflow_dispatch` release overrides**: allows targeting any major from a manual run, but bypasses normal PR-merge label correlation and mixes release paths.
- **Lineage-scoped `main` + `release/v[0-9]+` Maintenance branches (chosen)**: preserves PR-label release automation across active and prior lineages and keeps `v1` commits physically separated from `v2` commits on `main`.

## Consequences

- Creating a `release/v<N>` Maintenance branch requires mirroring `main`'s branch-protection rules on `release/v<N>` and guarding against initial branch-creation `push` events so existing tagged commits are not re-released.
- Cutting a new Major lineage on `main` requires updating internal `actions/<name>@v<N>` references inside reusable workflows on that branch so consumers on `@v2` do not transitively execute `@v1` composite actions.
