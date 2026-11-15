# gh-pipelines

gh-pipelines publishes reusable GitHub Actions artifacts for other repositories
to adopt through versioned references.

## Language

**Consumer repository**:
An external repository that adopts a published gh-pipelines artifact through a versioned reference.
_Avoid_: consumer, downstream repository

**Workflow consumer**:
A Consumer repository with at least one Workflow invocation.
_Avoid_: caller repository

**Composite-action consumer**:
A Consumer repository that adopts a composite action directly, without necessarily being a Workflow consumer.
_Avoid_: workflow consumer when the repository only uses a composite action

**Caller workflow**:
A workflow file owned by a Workflow consumer that contains one or more Workflow invocations.
_Avoid_: caller when the file and a job could both be meant

**Workflow invocation (or Caller job)**:
A single job-level use of a reusable workflow by a Caller workflow.
_Avoid_: caller when the repository, workflow file, or job must be distinguished

**Canary consumer repository**:
A Workflow consumer whose owner has agreed to use it as a monitored integration-smoke target.
_Avoid_: canary when the repository relationship must be explicit

**Self-test caller**:
The internal regression harness that invokes local reusable workflows against in-repository fixtures; it is not a Consumer repository.
_Avoid_: consumer

**Integration smoke**:
A parse-and-lint drift probe for Canary consumer repositories; it does not execute a consumer pipeline.
_Avoid_: end-to-end consumer test

**Major lineage**:
A compatibility line of releases sharing the same SemVer major number (for example `v1` or `v2`), isolated from breaking changes in other majors.
_Avoid_: version stream, release track

**Floating major tag**:
A mutable `v<N>` tag that advances only for compatible `patch` and `minor` releases within Major lineage `N`.
_Avoid_: moving tag, major alias

**Exact release tag**:
An immutable `vX.Y.Z` tag pointing permanently to one release commit.
_Avoid_: point tag, snapshot tag

**Maintenance branch**:
A `release/v<N>` branch rooted in an older Major lineage so compatible `patch` and `minor` releases can publish without inheriting newer-major commits from `main`.
_Avoid_: hotfix branch, support branch
