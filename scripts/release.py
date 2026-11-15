#!/usr/bin/env python3
"""Internal semver release orchestration for .github/workflows/release.yml."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, Sequence, Tuple

REQUIRED_CI_CHECKS: Tuple[Tuple[str, str], ...] = (
    (".github/workflows/lint.yml", "lint"),
    (".github/workflows/security-self-scan.yml", "zizmor"),
    (".github/workflows/security-self-scan.yml", "shellcheck"),
    (".github/workflows/test-callers.yml", "test ci-go caller / lint"),
    (".github/workflows/test-callers.yml", "test ci-go caller / test"),
    (".github/workflows/test-callers.yml", "test ci-node caller / lint"),
    (".github/workflows/test-callers.yml", "test ci-node caller / test"),
    (".github/workflows/test-callers.yml", "test ci-python caller / lint"),
    (".github/workflows/test-callers.yml", "test ci-python caller / test"),
    (".github/workflows/test-callers.yml", "test ci-terraform caller / fmt"),
    (".github/workflows/test-callers.yml", "test ci-terraform caller / validate"),
    (".github/workflows/test-callers.yml", "test ci-terraform caller / plan"),
    (".github/workflows/test-callers.yml", "test ci-with-sonar-go caller / ci / lint"),
    (".github/workflows/test-callers.yml", "test ci-with-sonar-node caller / ci / lint"),
    (".github/workflows/test-callers.yml", "test ci-with-sonar-python caller / ci / lint"),
)
_RELEASE_WORKFLOW_PATH = ".github/workflows/release.yml"
_DEFAULT_CONSUMER_REPO = "nandohdc/gh-pipelines-hosted-acceptance"
_GITHUB_ACTIONS_APP_SLUG = "github-actions"
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
)
_DETAILS_RUN_ID_RE = re.compile(r"/actions/runs/([0-9]+)(?:/|$)")
_SQUASH_PR_RE = re.compile(r"\(#([0-9]+)\)[ \t\r\f\v]*$")
_MERGE_PR_RE = re.compile(r"^Merge\s+pull\s+request\s+#([0-9]+)")
_SEMVER_TAG_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
_MAINTENANCE_BRANCH_RE = re.compile(r"^release/v([0-9]+)$")
_ATTEST_SBOM_RE = re.compile(r"^[ \t]*attest-sbom:", re.MULTILINE)
_HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_RELEASE_LABEL_PRECEDENCE = (
    ("release:major", "major"),
    ("release:minor", "minor"),
    ("release:patch", "patch"),
)
_BOT_NAME = "github-actions[bot]"
_BOT_EMAIL = "github-actions[bot]@users.noreply.github.com"


class ReleaseError(RuntimeError):
    """Raised when release resolution, guards, or publication fail."""


class GitHubClient(Protocol):
    """Port for GitHub pull-request, CI evidence, and release operations."""

    def find_merged_pr_for_commit(
        self, repository: str, sha: str, base_ref: str = "main"
    ) -> Optional[int]:
        ...

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        ...

    def get_release(self, tag: str) -> Optional[Dict[str, Any]]:
        ...

    def create_release(self, tag: str, target_sha: str, title: str) -> None:
        ...

    def list_workflow_runs_for_sha(
        self, repository: str, sha: str
    ) -> List[Dict[str, Any]]:
        ...

    def list_commit_check_runs(
        self, repository: str, sha: str
    ) -> List[Dict[str, Any]]:
        ...

    def ensure_candidate_smoke(
        self,
        candidate_sha: str,
        *,
        consumer_repo: str = _DEFAULT_CONSUMER_REPO,
    ) -> Dict[str, Any]:
        ...


@dataclass(frozen=True)
class ReleaseConfig:
    """Inputs required to resolve, validate, plan, and publish a release."""

    repository: str
    sha: str
    head_commit_message: str = ""
    base_ref: str = "main"
    is_branch_creation: bool = False
    workdir: Path = Path(".")
    remote: str = "origin"
    manifest_relpath: str = "actions/supply-chain-attest/action.yml"
    ci_gate_timeout_seconds: int = 600
    ci_gate_poll_seconds: int = 10
    consumer_repo: str = _DEFAULT_CONSUMER_REPO
    allow_dispatch_smoke: bool = True


@dataclass(frozen=True)
class ReleasePlan:
    """Resolved lineage and semver version plan for a single release."""

    pr_number: int
    release_type: str
    target_major: int
    target_lineage: str
    latest_tag: str
    next_tag: str
    floating_tag: str
    target_sha: str


@dataclass(frozen=True)
class ReleaseOutcome:
    """Observable outcome of a release invocation."""

    status: str
    reason: str
    pr_number: Optional[int] = None
    release_type: Optional[str] = None
    latest_tag: Optional[str] = None
    next_tag: Optional[str] = None
    target_sha: Optional[str] = None
    plan: Optional[ReleasePlan] = None
    reconciled_outcomes: Tuple["ReleaseOutcome", ...] = ()


def _run_cmd(cmd: Sequence[str], workdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=workdir,
        capture_output=True,
        text=True,
    )


def _run_checked(cmd: Sequence[str], workdir: Path) -> str:
    result = _run_cmd(cmd, workdir)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"{' '.join(cmd)} failed: {detail}")
    return result.stdout


def _load_candidate_smoke() -> Any:
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from scripts import candidate_smoke

    return candidate_smoke


def _smoke_outcome_to_dict(outcome: Any) -> Dict[str, Any]:
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "candidate_sha": outcome.candidate_sha,
        "consumer_repo": outcome.consumer_repo,
        "run_id": outcome.run_id,
        "run_url": outcome.run_url,
    }


class GhCliGitHub:
    """Production GitHubClient adapter backed by the gh CLI."""

    def __init__(
        self,
        workdir: Path = Path("."),
        *,
        repository: str = "nandohdc/gh-pipelines",
        allow_gh_token: bool = False,
    ) -> None:
        self._workdir = workdir
        self._repository = repository
        self._allow_gh_token = allow_gh_token

    def _api_json(self, endpoint: str) -> Any:
        raw = _run_checked(["gh", "api", endpoint], self._workdir)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"Invalid JSON from gh api {endpoint}: {exc}") from exc

    def find_merged_pr_for_commit(
        self, repository: str, sha: str, base_ref: str = "main"
    ) -> Optional[int]:
        pulls = self._api_json(f"/repos/{repository}/commits/{sha}/pulls")
        if not isinstance(pulls, list):
            raise ReleaseError(f"Expected PR list from gh api for commit {sha}")
        for pr in pulls:
            if not isinstance(pr, dict):
                continue
            base = pr.get("base") or {}
            if pr.get("merged_at") is not None and base.get("ref") == base_ref:
                number = pr.get("number")
                if number is not None:
                    return int(number)
        return None

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        labels = self._api_json(f"/repos/{repository}/issues/{pr_number}/labels")
        if not isinstance(labels, list):
            raise ReleaseError(f"Expected label list from gh api for PR #{pr_number}")
        return [str(item["name"]) for item in labels if isinstance(item, dict) and "name" in item]

    def get_release(self, tag: str) -> Optional[Dict[str, Any]]:
        result = _run_cmd(
            ["gh", "release", "view", tag, "--json", "tagName,targetCommitish,name"],
            self._workdir,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ReleaseError(f"Invalid JSON from gh release view {tag}: {exc}") from exc
            if not isinstance(data, dict):
                raise ReleaseError(f"Expected JSON object from gh release view {tag}")
            return data
        detail = (result.stderr or result.stdout).strip()
        lowered = detail.lower()
        if "release not found" in lowered or "http 404" in lowered:
            return None
        raise ReleaseError(f"gh release view {tag} failed: {detail}")

    def create_release(self, tag: str, target_sha: str, title: str) -> None:
        _run_checked(
            [
                "gh",
                "release",
                "create",
                tag,
                "--target",
                target_sha,
                "--title",
                title,
                "--generate-notes",
            ],
            self._workdir,
        )

    def _paginated_collection(
        self, base_endpoint: str, key: str
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        while True:
            endpoint = base_endpoint if page == 1 else f"{base_endpoint}&page={page}"
            payload = self._api_json(endpoint)
            if not isinstance(payload, dict):
                raise ReleaseError(f"Expected JSON object from gh api {endpoint}")
            batch = payload.get(key)
            if not isinstance(batch, list):
                raise ReleaseError(f"Expected '{key}' list from gh api {endpoint}")
            dict_batch = [item for item in batch if isinstance(item, dict)]
            items.extend(dict_batch)
            total_count = payload.get("total_count")
            if not dict_batch or len(batch) < 100:
                break
            if isinstance(total_count, int) and len(items) >= total_count:
                break
            page += 1
        return items

    def list_workflow_runs_for_sha(
        self, repository: str, sha: str
    ) -> List[Dict[str, Any]]:
        return self._paginated_collection(
            f"/repos/{repository}/actions/runs?head_sha={sha}&per_page=100",
            "workflow_runs",
        )

    def list_commit_check_runs(
        self, repository: str, sha: str
    ) -> List[Dict[str, Any]]:
        return self._paginated_collection(
            f"/repos/{repository}/commits/{sha}/check-runs?per_page=100",
            "check_runs",
        )

    def find_candidate_smoke(
        self,
        candidate_sha: str,
        *,
        consumer_repo: str = _DEFAULT_CONSUMER_REPO,
    ) -> Optional[Dict[str, Any]]:
        candidate_smoke = _load_candidate_smoke()
        try:
            token = candidate_smoke.resolve_consumer_token(
                allow_gh_token=self._allow_gh_token
            )
            consumer_github = candidate_smoke.GhCliConsumerGitHub(token)
            existing = candidate_smoke.find_verified_candidate_smoke_run(
                consumer_github, consumer_repo, candidate_sha
            )
            return _smoke_outcome_to_dict(existing) if existing is not None else None
        except candidate_smoke.CandidateSmokeError as exc:
            raise ReleaseError(str(exc)) from exc

    def ensure_candidate_smoke(
        self,
        candidate_sha: str,
        *,
        consumer_repo: str = _DEFAULT_CONSUMER_REPO,
    ) -> Dict[str, Any]:
        candidate_smoke = _load_candidate_smoke()
        try:
            token = candidate_smoke.resolve_consumer_token(
                allow_gh_token=self._allow_gh_token
            )
            consumer_github = candidate_smoke.GhCliConsumerGitHub(token)
            existing = candidate_smoke.find_verified_candidate_smoke_run(
                consumer_github, consumer_repo, candidate_sha
            )
            if existing is not None:
                return _smoke_outcome_to_dict(existing)
            with _candidate_checkout_dir(self._workdir, candidate_sha) as candidate_dir:
                smoke_config = candidate_smoke.CandidateSmokeConfig(
                    candidate_sha=candidate_sha,
                    source_repo=self._repository,
                    consumer_repo=consumer_repo,
                    candidate_dir=candidate_dir,
                )
                outcome = candidate_smoke.run_candidate_smoke(
                    smoke_config, consumer_github, token=token
                )
                return _smoke_outcome_to_dict(outcome)
        except candidate_smoke.CandidateSmokeError as exc:
            raise ReleaseError(str(exc)) from exc


def _resolve_pr_number(config: ReleaseConfig, github: GitHubClient) -> Optional[int]:
    subject = config.head_commit_message.split("\n", 1)[0]
    match = _SQUASH_PR_RE.search(subject)
    if match:
        return int(match.group(1))
    merge_match = _MERGE_PR_RE.match(config.head_commit_message)
    if merge_match:
        return int(merge_match.group(1))
    return github.find_merged_pr_for_commit(config.repository, config.sha, config.base_ref)


def _resolve_release_type(labels: List[str]) -> Optional[str]:
    label_set = set(labels)
    for label_name, release_type in _RELEASE_LABEL_PRECEDENCE:
        if label_name in label_set:
            return release_type
    return None


@contextlib.contextmanager
def _candidate_checkout_dir(workdir: Path, candidate_sha: str) -> Iterator[Path]:
    head_result = _run_cmd(["git", "rev-parse", "HEAD"], workdir)
    if head_result.returncode == 0 and head_result.stdout.strip().lower() == candidate_sha.lower():
        yield workdir
        return
    exists_result = _run_cmd(["git", "cat-file", "-e", f"{candidate_sha}^{{commit}}"], workdir)
    if exists_result.returncode != 0:
        yield workdir
        return
    with tempfile.TemporaryDirectory(prefix="release-smoke-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        add_result = _run_cmd(
            ["git", "worktree", "add", "--detach", str(tmp_path), candidate_sha],
            workdir,
        )
        if add_result.returncode != 0:
            yield workdir
            return
        try:
            yield tmp_path
        finally:
            _run_cmd(["git", "worktree", "remove", "--force", str(tmp_path)], workdir)


def _extract_run_id_from_details_url(details_url: Any) -> Optional[int]:
    if not isinstance(details_url, str):
        return None
    match = _DETAILS_RUN_ID_RE.search(details_url)
    return int(match.group(1)) if match else None


def _index_workflow_runs_by_sha(
    workflow_runs: Sequence[Dict[str, Any]], expected_sha: str
) -> Tuple[Dict[int, Tuple[str, int]], Dict[int, Tuple[str, int]]]:
    runs_by_id: Dict[int, Tuple[str, int]] = {}
    latest_by_suite: Dict[int, Tuple[str, int]] = {}
    for run in workflow_runs:
        if not isinstance(run, dict):
            continue
        head_sha = run.get("head_sha")
        if isinstance(head_sha, str) and head_sha.strip() and head_sha.strip().lower() != expected_sha:
            continue
        run_id = run.get("id")
        workflow_path = run.get("path")
        if not isinstance(run_id, int) or not isinstance(workflow_path, str) or not workflow_path:
            continue
        runs_by_id[run_id] = (workflow_path, run_id)
        suite_id = run.get("check_suite_id")
        if isinstance(suite_id, int):
            previous = latest_by_suite.get(suite_id)
            if previous is None or run_id > previous[1]:
                latest_by_suite[suite_id] = (workflow_path, run_id)
    return runs_by_id, latest_by_suite


def _collect_candidate_checks(
    workflow_runs: Sequence[Dict[str, Any]],
    check_runs: Sequence[Dict[str, Any]],
    expected_sha: str,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    runs_by_id, latest_by_suite = _index_workflow_runs_by_sha(workflow_runs, expected_sha)
    best_by_identity: Dict[Tuple[str, str], Tuple[Tuple[int, int], Dict[str, Any]]] = {}

    for check in check_runs:
        if not isinstance(check, dict):
            continue
        check_sha = check.get("head_sha")
        if isinstance(check_sha, str) and check_sha.strip() and check_sha.strip().lower() != expected_sha:
            continue
        app = check.get("app")
        if not isinstance(app, dict) or app.get("slug") != _GITHUB_ACTIONS_APP_SLUG:
            continue
        name = check.get("name")
        if not isinstance(name, str) or not name:
            continue

        resolved: Optional[Tuple[str, int]] = None
        details_run_id = _extract_run_id_from_details_url(check.get("details_url"))
        if details_run_id is not None:
            resolved = runs_by_id.get(details_run_id)
        if resolved is None:
            suite = check.get("check_suite")
            suite_id = suite.get("id") if isinstance(suite, dict) else None
            if isinstance(suite_id, int):
                resolved = latest_by_suite.get(suite_id)
        if resolved is None:
            continue

        workflow_path, resolved_run_id = resolved
        if workflow_path == _RELEASE_WORKFLOW_PATH:
            continue

        identity = (workflow_path, name)
        check_id = check.get("id")
        rank = (resolved_run_id, check_id if isinstance(check_id, int) else 0)
        previous = best_by_identity.get(identity)
        if previous is None or rank > previous[0]:
            best_by_identity[identity] = (rank, check)

    return {identity: item[1] for identity, item in best_by_identity.items()}


def _evaluate_ci_checks(
    mapped_checks: Dict[Tuple[str, str], Dict[str, Any]],
) -> Tuple[List[str], List[str], List[str]]:
    required_set = set(REQUIRED_CI_CHECKS)
    verified: List[str] = []
    pending_or_missing: List[str] = []
    terminal_failures: List[str] = []

    for workflow_path, job_name in REQUIRED_CI_CHECKS:
        check = mapped_checks.get((workflow_path, job_name))
        label = f"{workflow_path}:{job_name}"
        if check is None:
            pending_or_missing.append(f"{label} (missing)")
            continue
        status = str(check.get("status") or "")
        conclusion = str(check.get("conclusion") or "")
        if status != "completed":
            pending_or_missing.append(f"{label} (status={status or 'unknown'})")
            continue
        if conclusion != "success":
            terminal_failures.append(f"{label} (conclusion={conclusion or 'unknown'})")
            continue
        verified.append(label)

    for (workflow_path, job_name), check in mapped_checks.items():
        if (workflow_path, job_name) in required_set:
            continue
        status = str(check.get("status") or "")
        conclusion = str(check.get("conclusion") or "")
        if status == "completed" and conclusion in _FAILING_CONCLUSIONS:
            terminal_failures.append(
                f"{workflow_path}:{job_name} (conclusion={conclusion})"
            )

    return verified, pending_or_missing, terminal_failures


def _smoke_field(outcome: Any, field_name: str) -> Any:
    if isinstance(outcome, dict):
        return outcome.get(field_name)
    return getattr(outcome, field_name, None)


def _block_promotion(target_sha: str, reason: str) -> ReleaseError:
    print(f"Release promotion gate: candidate={target_sha} status=blocked reason={reason}")
    return ReleaseError(f"Release promotion blocked for {target_sha}: {reason}")


def verify_promotion_gate(
    config: ReleaseConfig,
    github: GitHubClient,
    target_sha: str,
    *,
    dispatch_smoke: Optional[bool] = None,
) -> Dict[str, Any]:
    """Verify exact-SHA required CI and consumer smoke evidence before release promotion."""
    expected_sha = target_sha.strip().lower()
    if not expected_sha:
        raise ReleaseError("Cannot verify promotion gate without a candidate SHA.")

    deadline = time.monotonic() + max(0.0, float(config.ci_gate_timeout_seconds))
    verified_names: List[str] = []

    while True:
        workflow_runs = github.list_workflow_runs_for_sha(config.repository, target_sha)
        check_runs = github.list_commit_check_runs(config.repository, target_sha)
        mapped = _collect_candidate_checks(workflow_runs, check_runs, expected_sha)
        verified_names, pending_or_missing, terminal_failures = _evaluate_ci_checks(mapped)

        if terminal_failures:
            raise _block_promotion(
                target_sha,
                f"required CI checks failed ({'; '.join(terminal_failures)})",
            )
        if not pending_or_missing:
            break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _block_promotion(
                target_sha,
                f"required CI checks incomplete or missing ({'; '.join(pending_or_missing)})",
            )
        sleep_seconds = min(max(0.01, float(config.ci_gate_poll_seconds)), remaining)
        time.sleep(sleep_seconds)

    should_dispatch = (
        config.allow_dispatch_smoke if dispatch_smoke is None else dispatch_smoke
    )
    try:
        if not should_dispatch and hasattr(github, "find_candidate_smoke"):
            smoke_outcome = getattr(github, "find_candidate_smoke")(
                target_sha, consumer_repo=config.consumer_repo
            )
        else:
            smoke_outcome = github.ensure_candidate_smoke(
                target_sha,
                consumer_repo=config.consumer_repo,
            )
    except ReleaseError as exc:
        print(f"Release promotion gate: candidate={target_sha} status=blocked reason={exc}")
        raise

    if not should_dispatch and smoke_outcome is None:
        raise _block_promotion(
            target_sha,
            f"missing verified candidate smoke run for {target_sha} in "
            f"{config.consumer_repo} (smoke dispatch disabled)",
        )

    smoke_status = _smoke_field(smoke_outcome, "status")
    smoke_sha = str(_smoke_field(smoke_outcome, "candidate_sha") or "")
    smoke_run_id = _smoke_field(smoke_outcome, "run_id")
    smoke_url = _smoke_field(smoke_outcome, "run_url")
    if smoke_outcome is None or smoke_status != "passed" or smoke_sha.lower() != expected_sha:
        raise _block_promotion(
            target_sha,
            f"candidate smoke invalid for {target_sha} "
            f"(status={smoke_status}, smoke_sha={smoke_sha or 'missing'})",
        )

    print(
        f"Release promotion gate: candidate={target_sha} status=authorized "
        f"ci_checks={len(verified_names)} consumer={config.consumer_repo} "
        f"smoke_run_id={smoke_run_id} smoke_url={smoke_url}"
    )
    return {
        "status": "authorized",
        "candidate_sha": target_sha,
        "ci_checks": tuple(verified_names),
        "consumer_repo": config.consumer_repo,
        "smoke_run_id": smoke_run_id,
        "smoke_url": smoke_url,
    }


def _verify_attestation_guard(config: ReleaseConfig) -> None:
    manifest = config.workdir / config.manifest_relpath
    if not manifest.is_file():
        raise ReleaseError(
            f"{config.manifest_relpath} at the release commit is missing; "
            "aborting release."
        )
    content = manifest.read_text(encoding="utf-8")
    if not _ATTEST_SBOM_RE.search(content):
        raise ReleaseError(
            f"{config.manifest_relpath} at the release commit does not declare "
            "'attest-sbom'; docker-build.yml's attest-sbom job would silently no-op "
            "behind a floating major tag. Aborting release."
        )
    print(f"Release guard OK: {config.manifest_relpath} declares attest-sbom.")


def _parse_semver(tag: str) -> Optional[Tuple[int, int, int]]:
    match = _SEMVER_TAG_RE.match(tag)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _resolve_branch_major(base_ref: str) -> Optional[int]:
    if base_ref == "main":
        return None
    match = _MAINTENANCE_BRANCH_RE.match(base_ref)
    if match:
        return int(match.group(1))
    raise ReleaseError(
        f"Unsupported release branch '{base_ref}'; expected 'main' or 'release/v<N>'."
    )


def _list_remote_semver_tags(config: ReleaseConfig) -> Dict[str, Tuple[Tuple[int, int, int], str]]:
    """Return remote exact semver tags mapped to (version_tuple, peeled_commit_sha)."""
    output = _run_checked(
        ["git", "ls-remote", "--tags", config.remote, "v*"],
        config.workdir,
    )
    tags: Dict[str, Tuple[Tuple[int, int, int], str]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split(None, 1)
        raw_tag = ref.replace("refs/tags/", "", 1)
        peeled = raw_tag.endswith("^{}")
        tag = raw_tag[:-3] if peeled else raw_tag
        parsed = _parse_semver(tag)
        if parsed is None:
            continue
        if peeled or tag not in tags:
            tags[tag] = (parsed, sha)
    return tags


def _is_ancestor(workdir: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    result = _run_cmd(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        workdir,
    )
    return result.returncode == 0


def _ensure_commit_available(config: ReleaseConfig, sha: str) -> None:
    result = _run_cmd(["git", "cat-file", "-e", f"{sha}^{{commit}}"], config.workdir)
    if result.returncode != 0:
        _run_cmd(["git", "fetch", "--no-tags", "--quiet", config.remote, sha], config.workdir)


def _ensure_full_history(config: ReleaseConfig) -> None:
    _ensure_commit_available(config, config.sha)
    shallow = _run_cmd(["git", "rev-parse", "--is-shallow-repository"], config.workdir)
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        _run_cmd(["git", "fetch", "--unshallow", "--no-tags", "--quiet", config.remote], config.workdir)


def _commit_message_for_sha(config: ReleaseConfig, commit_sha: str) -> str:
    if commit_sha == config.sha and config.head_commit_message:
        return config.head_commit_message
    return _run_checked(["git", "log", "-1", "--format=%B", commit_sha], config.workdir)


def _list_first_parent_commits(config: ReleaseConfig) -> List[str]:
    output = _run_checked(
        ["git", "rev-list", "--first-parent", "--reverse", config.sha],
        config.workdir,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _is_anchor_incomplete(
    config: ReleaseConfig,
    github: GitHubClient,
    anchor_sha: str,
    anchor_tags: List[Tuple[str, Tuple[int, int, int]]],
) -> bool:
    anchor_tag, anchor_ver = anchor_tags[0]
    commit_msg = _commit_message_for_sha(config, anchor_sha)
    commit_config = replace(config, sha=anchor_sha, head_commit_message=commit_msg)
    pr_number = _resolve_pr_number(commit_config, github)
    if pr_number is None:
        return False
    labels = github.list_pr_labels(config.repository, pr_number)
    if _resolve_release_type(labels) is None:
        return False

    current_floating_sha = _resolve_remote_tag_commit(config, f"v{anchor_ver[0]}")
    floating_covered = current_floating_sha == anchor_sha or (
        current_floating_sha is not None
        and _is_ancestor(config.workdir, anchor_sha, current_floating_sha)
    )
    if not floating_covered:
        return True
    return github.get_release(anchor_tag) is None


def _list_pending_lineage_commits(
    config: ReleaseConfig,
    github: GitHubClient,
    remote_tags: Dict[str, Tuple[Tuple[int, int, int], str]],
) -> List[str]:
    """Return ordered first-parent commits to reconcile for the target branch lineage."""
    branch_major: Optional[int] = None
    if config.base_ref != "main":
        match = _MAINTENANCE_BRANCH_RE.match(config.base_ref)
        if not match:
            return [config.sha]
        branch_major = int(match.group(1))

    first_parent = _list_first_parent_commits(config)
    if not first_parent:
        return [config.sha]

    first_parent_index = {sha: idx for idx, sha in enumerate(first_parent)}
    by_sha: Dict[str, List[Tuple[str, Tuple[int, int, int]]]] = {}
    for tag, (ver, tag_sha) in remote_tags.items():
        if branch_major is not None and ver[0] != branch_major:
            continue
        if tag_sha in first_parent_index:
            by_sha.setdefault(tag_sha, []).append((tag, ver))

    anchor_idx: Optional[int] = None
    anchor_tags: List[Tuple[str, Tuple[int, int, int]]] = []
    for tag_sha, items in by_sha.items():
        idx = first_parent_index[tag_sha]
        if anchor_idx is None or idx > anchor_idx:
            anchor_idx = idx
            anchor_tags = sorted(items, key=lambda item: item[1])

    if anchor_idx is None:
        return first_parent

    if anchor_idx == len(first_parent) - 1:
        return [config.sha]

    anchor_sha = first_parent[anchor_idx]
    tail_commits = first_parent[anchor_idx + 1 :]
    if len(anchor_tags) == 1 and _is_anchor_incomplete(config, github, anchor_sha, anchor_tags):
        return [anchor_sha] + tail_commits
    return tail_commits


def _find_existing_exact_tags_for_sha(
    remote_tags: Dict[str, Tuple[Tuple[int, int, int], str]], sha: str
) -> List[Tuple[str, Tuple[int, int, int]]]:
    matches = [(tag, ver) for tag, (ver, tag_sha) in remote_tags.items() if tag_sha == sha]
    matches.sort(key=lambda item: item[1])
    return matches


def _find_descendant_tags(
    workdir: Path,
    sha: str,
    remote_tags: Dict[str, Tuple[Tuple[int, int, int], str]],
    *,
    major: Optional[int] = None,
) -> List[Tuple[str, str]]:
    descendants: List[Tuple[str, str]] = []
    for tag, (ver, tag_sha) in sorted(remote_tags.items()):
        if major is not None and ver[0] != major:
            continue
        if tag_sha != sha and _is_ancestor(workdir, sha, tag_sha):
            descendants.append((tag, tag_sha))
    return descendants


def _infer_release_type(latest_tag: str, existing_ver: Tuple[int, int, int]) -> str:
    latest_ver = _parse_semver(latest_tag)
    if existing_ver[1] == 0 and existing_ver[2] == 0 and (
        latest_tag == "v0.0.0" or (latest_ver is not None and existing_ver[0] > latest_ver[0])
    ):
        return "major"
    if existing_ver[2] == 0:
        return "minor"
    return "patch"


def _extract_release_target_sha(release: Dict[str, Any]) -> Optional[str]:
    raw_target = release.get("targetCommitish") or release.get("target_sha") or ""
    if not isinstance(raw_target, str):
        return None
    candidate = raw_target.strip()
    if _HEX_SHA_RE.match(candidate):
        return candidate.lower()
    return None


def _validate_existing_release(tag: str, target_sha: str, release: Dict[str, Any]) -> None:
    recorded_sha = _extract_release_target_sha(release)
    if recorded_sha is not None and recorded_sha != target_sha.lower():
        raise ReleaseError(
            f"Inconsistent recovery state for {tag}: existing GitHub release targets "
            f"{recorded_sha}, expected {target_sha}. Inspect the conflicting release record before retrying."
        )


def _resolve_target_major_for_patch_or_minor(
    config: ReleaseConfig,
    branch_major: Optional[int],
    remote_tags: Dict[str, Tuple[Tuple[int, int, int], str]],
) -> int:
    if branch_major is not None:
        has_lineage_tags = any(ver[0] == branch_major for ver, _ in remote_tags.values())
        if not has_lineage_tags:
            raise ReleaseError(
                f"No existing releases found for maintenance lineage v{branch_major}."
            )
        for tag, (ver, tag_sha) in sorted(remote_tags.items()):
            if ver[0] > branch_major and _is_ancestor(config.workdir, tag_sha, config.sha):
                raise ReleaseError(
                    f"Commit {config.sha} contains {tag} from higher major lineage "
                    f"v{ver[0]}; refusing release on maintenance branch {config.base_ref}."
                )
        return branch_major

    reachable_majors = [
        ver[0]
        for ver, tag_sha in remote_tags.values()
        if _is_ancestor(config.workdir, tag_sha, config.sha)
    ]
    if reachable_majors:
        return max(reachable_majors)
    if remote_tags:
        return max(ver[0] for ver, _ in remote_tags.values())
    return 0


def _plan_release(
    config: ReleaseConfig,
    pr_number: int,
    release_type: Optional[str],
    remote_tags: Optional[Dict[str, Tuple[Tuple[int, int, int], str]]] = None,
) -> Tuple[ReleasePlan, bool]:
    branch_major = _resolve_branch_major(config.base_ref)
    if remote_tags is None:
        remote_tags = _list_remote_semver_tags(config)

    existing_for_sha = _find_existing_exact_tags_for_sha(remote_tags, config.sha)
    if len(existing_for_sha) > 1:
        names = ", ".join(tag for tag, _ in existing_for_sha)
        raise ReleaseError(
            f"Inconsistent recovery state for PR #{pr_number} at {config.sha}: "
            f"multiple exact release tags point to this commit ({names}). "
            "Resolve conflicting tags manually before retrying."
        )

    if len(existing_for_sha) == 1:
        existing_tag, existing_ver = existing_for_sha[0]
        target_major = existing_ver[0]
        if branch_major is not None and target_major != branch_major:
            raise ReleaseError(
                f"Inconsistent recovery state for PR #{pr_number} at {config.sha}: "
                f"existing tag {existing_tag} belongs to lineage v{target_major}, "
                f"incompatible with branch {config.base_ref}."
            )
        ancestor_lineage = [
            (ver, tag)
            for tag, (ver, tsha) in remote_tags.items()
            if ver[0] == target_major
            and tsha != config.sha
            and _is_ancestor(config.workdir, tsha, config.sha)
        ]
        latest_tag = max(ancestor_lineage, key=lambda item: item[0])[1] if ancestor_lineage else "v0.0.0"
        effective_type = _infer_release_type(latest_tag, existing_ver)
        if branch_major is not None and (release_type == "major" or effective_type == "major"):
            raise ReleaseError(
                f"release:major is not allowed on maintenance branch {config.base_ref}; "
                "breaking changes must be released from main."
            )
        target_lineage = f"v{target_major}"
        plan = ReleasePlan(
            pr_number=pr_number,
            release_type=effective_type,
            target_major=target_major,
            target_lineage=target_lineage,
            latest_tag=latest_tag,
            next_tag=existing_tag,
            floating_tag=target_lineage,
            target_sha=config.sha,
        )
        print(
            f"Resuming release intent: pr=#{pr_number} sha={config.sha} "
            f"lineage={plan.target_lineage} version={plan.next_tag}"
        )
        return plan, True

    if release_type is None:
        raise ReleaseError("Cannot plan a new release without a release label.")

    descendants = _find_descendant_tags(config.workdir, config.sha, remote_tags, major=branch_major)
    if descendants:
        desc_tag, desc_sha = descendants[0]
        raise ReleaseError(
            f"Stale pre-tag release attempt for PR #{pr_number} at {config.sha}: "
            f"newer commit {desc_sha} already holds {desc_tag}. "
            "No tags were modified; verify release order before retrying."
        )

    prior_tags = {
        tag: (ver, tsha)
        for tag, (ver, tsha) in remote_tags.items()
        if tsha != config.sha and not _is_ancestor(config.workdir, config.sha, tsha)
    }

    if release_type == "major":
        if branch_major is not None:
            raise ReleaseError(
                f"release:major is not allowed on maintenance branch {config.base_ref}; "
                "breaking changes must be released from main."
            )
        highest_major = max((ver[0] for ver, _ in prior_tags.values()), default=0)
        highest_lineage = [
            (ver, tag) for tag, (ver, _) in prior_tags.items() if ver[0] == highest_major
        ]
        latest_tag = max(highest_lineage, key=lambda item: item[0])[1] if highest_lineage else "v0.0.0"
        target_major = highest_major + 1
        next_tag = f"v{target_major}.0.0"
    elif release_type in ("patch", "minor"):
        target_major = _resolve_target_major_for_patch_or_minor(config, branch_major, prior_tags)
        lineage_tags = [
            (ver, tag) for tag, (ver, _) in prior_tags.items() if ver[0] == target_major
        ]
        if lineage_tags:
            (major, minor, patch), latest_tag = max(lineage_tags, key=lambda item: item[0])
        else:
            major, minor, patch = target_major, 0, 0
            latest_tag = "v0.0.0"
        if release_type == "patch":
            next_tag = f"v{major}.{minor}.{patch + 1}"
        else:
            next_tag = f"v{major}.{minor + 1}.0"
    else:
        raise ReleaseError(f"Unsupported release type: {release_type}")

    target_lineage = f"v{target_major}"
    plan = ReleasePlan(
        pr_number=pr_number,
        release_type=release_type,
        target_major=target_major,
        target_lineage=target_lineage,
        latest_tag=latest_tag,
        next_tag=next_tag,
        floating_tag=target_lineage,
        target_sha=config.sha,
    )
    print(
        f"Planned release: lineage={plan.target_lineage} "
        f"latest={plan.latest_tag} next={plan.next_tag} floating={plan.floating_tag}"
    )
    return plan, False


def _resolve_remote_tag_commit(config: ReleaseConfig, tag: str) -> Optional[str]:
    output = _run_checked(
        ["git", "ls-remote", "--tags", config.remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        config.workdir,
    ).strip()
    if not output:
        return None
    resolved: Optional[str] = None
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split(None, 1)
        if ref.endswith("^{}"):
            return sha
        resolved = sha
    return resolved


def _resolve_local_tag_commit(config: ReleaseConfig, tag: str) -> Optional[str]:
    result = _run_cmd(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
        config.workdir,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _publish_tags(config: ReleaseConfig, plan: ReleasePlan) -> Tuple[str, str]:
    _run_checked(["git", "config", "user.name", _BOT_NAME], config.workdir)
    _run_checked(["git", "config", "user.email", _BOT_EMAIL], config.workdir)

    remote_sha = _resolve_remote_tag_commit(config, plan.next_tag)
    if remote_sha is not None and remote_sha != config.sha:
        raise ReleaseError(
            f"Immutable tag {plan.next_tag} already exists on {config.remote} "
            f"pointing to {remote_sha}, expected {config.sha}."
        )

    local_sha = _resolve_local_tag_commit(config, plan.next_tag)
    if local_sha is not None and local_sha != config.sha:
        raise ReleaseError(
            f"Immutable tag {plan.next_tag} already exists locally pointing to "
            f"{local_sha}, expected {config.sha}."
        )

    if remote_sha is not None:
        print(f"Tag {plan.next_tag} already exists at target SHA; continuing.")
        exact_effect = "reused"
    else:
        if local_sha is None:
            _run_checked(["git", "tag", plan.next_tag, config.sha], config.workdir)
        _run_checked(["git", "push", config.remote, f"refs/tags/{plan.next_tag}"], config.workdir)
        exact_effect = "created"

    current_floating_sha = _resolve_remote_tag_commit(config, plan.floating_tag)
    if current_floating_sha == config.sha:
        print(f"Floating tag {plan.floating_tag} already points to {config.sha}; skipping update.")
        floating_effect = "reused"
    elif current_floating_sha is not None:
        _ensure_commit_available(config, current_floating_sha)
        if _is_ancestor(config.workdir, config.sha, current_floating_sha):
            print(
                f"Floating tag {plan.floating_tag} already points to newer commit "
                f"{current_floating_sha}; leaving {plan.floating_tag} unchanged."
            )
            floating_effect = "kept_newer"
        elif not _is_ancestor(config.workdir, current_floating_sha, config.sha):
            raise ReleaseError(
                f"Floating tag {plan.floating_tag} points to {current_floating_sha}, "
                f"which is outside {config.sha}'s ancestry. Resolve manually before retrying."
            )
        else:
            _run_checked(["git", "tag", "-f", plan.floating_tag, config.sha], config.workdir)
            _run_checked(
                ["git", "push", config.remote, f"refs/tags/{plan.floating_tag}", "--force"],
                config.workdir,
            )
            floating_effect = "moved"
    else:
        _run_checked(["git", "tag", "-f", plan.floating_tag, config.sha], config.workdir)
        _run_checked(
            ["git", "push", config.remote, f"refs/tags/{plan.floating_tag}", "--force"],
            config.workdir,
        )
        floating_effect = "created"

    return exact_effect, floating_effect


def _publish_github_release(github: GitHubClient, next_tag: str, target_sha: str) -> str:
    existing = github.get_release(next_tag)
    if existing is not None:
        _validate_existing_release(next_tag, target_sha, existing)
        print(f"Release {next_tag} already exists; skipping creation.")
        return "reused"
    github.create_release(next_tag, target_sha, next_tag)
    return "created"


def _log_recovery_summary(
    plan: ReleasePlan,
    *,
    base_ref: str,
    exact_effect: str,
    floating_effect: str,
    release_effect: str,
    outcome_status: str,
    outcome_reason: str,
) -> None:
    print(
        f"Release recovery summary: intent=PR#{plan.pr_number}@{plan.target_sha} "
        f"branch={base_ref} lineage={plan.target_lineage} version={plan.next_tag} "
        f"effects=exact_tag:{exact_effect},floating_tag:{floating_effect},"
        f"github_release:{release_effect} outcome={outcome_status}:{outcome_reason}"
    )


def _run_single_commit_release(
    config: ReleaseConfig,
    github: GitHubClient,
    *,
    remote_tags: Optional[Dict[str, Tuple[Tuple[int, int, int], str]]] = None,
) -> ReleaseOutcome:
    """Resolve release intent, verify guards, plan target lineage, and publish one commit."""
    pr_number = _resolve_pr_number(config, github)
    if pr_number is None:
        print(f"No merged PR associated with commit {config.sha}; skipping release.")
        return ReleaseOutcome(status="skipped", reason="no_merged_pr", target_sha=config.sha)

    labels = github.list_pr_labels(config.repository, pr_number)
    release_type = _resolve_release_type(labels)

    if remote_tags is None:
        remote_tags = _list_remote_semver_tags(config)
    existing_for_sha = _find_existing_exact_tags_for_sha(remote_tags, config.sha)
    if release_type is None and not existing_for_sha:
        print("No release label found; skipping release.")
        return ReleaseOutcome(
            status="skipped",
            reason="no_release_label",
            pr_number=pr_number,
            target_sha=config.sha,
        )

    if release_type is not None:
        print(f"Detected release type: {release_type} (PR #{pr_number})")

    plan, resumed_from_existing_tag = _plan_release(
        config, pr_number, release_type, remote_tags=remote_tags
    )

    if resumed_from_existing_tag:
        current_floating_sha = _resolve_remote_tag_commit(config, plan.floating_tag)
        floating_effect: Optional[str] = None
        if current_floating_sha == config.sha:
            floating_effect = "reused"
        elif current_floating_sha is not None:
            _ensure_commit_available(config, current_floating_sha)
            if _is_ancestor(config.workdir, config.sha, current_floating_sha):
                floating_effect = "kept_newer"

        existing_release = github.get_release(plan.next_tag)
        if existing_release is not None:
            _validate_existing_release(plan.next_tag, config.sha, existing_release)

        if floating_effect is not None and existing_release is not None:
            _log_recovery_summary(
                plan,
                base_ref=config.base_ref,
                exact_effect="reused",
                floating_effect=floating_effect,
                release_effect="reused",
                outcome_status="noop",
                outcome_reason="already_released",
            )
            return ReleaseOutcome(
                status="noop",
                reason="already_released",
                pr_number=pr_number,
                release_type=plan.release_type,
                latest_tag=plan.latest_tag,
                next_tag=plan.next_tag,
                target_sha=config.sha,
                plan=plan,
            )

    _verify_attestation_guard(config)
    verify_promotion_gate(config, github, config.sha)
    exact_effect, floating_effect_written = _publish_tags(config, plan)
    release_effect = _publish_github_release(github, plan.next_tag, config.sha)
    is_resumed = (
        resumed_from_existing_tag
        or exact_effect == "reused"
        or floating_effect_written in ("reused", "kept_newer")
        or release_effect == "reused"
    )
    outcome_reason = "resumed" if is_resumed else "published"
    _log_recovery_summary(
        plan,
        base_ref=config.base_ref,
        exact_effect=exact_effect,
        floating_effect=floating_effect_written,
        release_effect=release_effect,
        outcome_status="published",
        outcome_reason=outcome_reason,
    )
    return ReleaseOutcome(
        status="published",
        reason=outcome_reason,
        pr_number=pr_number,
        release_type=plan.release_type,
        latest_tag=plan.latest_tag,
        next_tag=plan.next_tag,
        target_sha=config.sha,
        plan=plan,
    )


def run_release(config: ReleaseConfig, github: GitHubClient) -> ReleaseOutcome:
    """Reconcile pending lineage release intents in commit order up to config.sha."""
    if config.base_ref != "main" and config.is_branch_creation:
        print(
            f"Skipping release for initial branch creation on {config.base_ref} "
            f"at {config.sha}."
        )
        return ReleaseOutcome(status="skipped", reason="branch_created")

    _ensure_full_history(config)
    remote_tags = _list_remote_semver_tags(config)
    candidates = _list_pending_lineage_commits(config, github, remote_tags)
    if len(candidates) > 1:
        print(
            f"Reconciling lineage commits in order: branch={config.base_ref} "
            f"count={len(candidates)} head={config.sha}"
        )

    outcomes: List[ReleaseOutcome] = []
    for idx, commit_sha in enumerate(candidates):
        current_tags = remote_tags if idx == 0 else _list_remote_semver_tags(config)
        commit_msg = _commit_message_for_sha(config, commit_sha)
        commit_config = replace(config, sha=commit_sha, head_commit_message=commit_msg)
        try:
            outcome = _run_single_commit_release(
                commit_config,
                github,
                remote_tags=current_tags,
            )
        except ReleaseError as exc:
            if len(candidates) > 1:
                print(
                    f"Release reconciliation blocked: commit={commit_sha} "
                    f"branch={config.base_ref} remaining_commits={len(candidates) - idx - 1} "
                    f"error={exc}"
                )
            raise
        outcomes.append(outcome)

    head_outcome = outcomes[-1]
    published_outcomes = [item for item in outcomes if item.status == "published"]
    if head_outcome.status == "skipped" and published_outcomes:
        return replace(
            published_outcomes[-1],
            reason="reconciled",
            reconciled_outcomes=tuple(outcomes),
        )
    return replace(head_outcome, reconciled_outcomes=tuple(outcomes))


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ReleaseError(f"{name} must be an integer, got {raw!r}.") from exc


def _config_from_env() -> ReleaseConfig:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if not repository:
        raise ReleaseError("GITHUB_REPOSITORY environment variable is required.")
    if not sha:
        raise ReleaseError("GITHUB_SHA environment variable is required.")
    base_ref = os.environ.get("GITHUB_REF_NAME", "").strip() or "main"
    created_env = os.environ.get("GITHUB_EVENT_CREATED", "").strip().lower()
    before_env = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
    is_branch_creation = created_env == "true" or (
        bool(before_env) and set(before_env) == {"0"}
    )
    consumer_repo = (
        os.environ.get("RELEASE_CONSUMER_REPO", "").strip() or _DEFAULT_CONSUMER_REPO
    )
    dispatch_env = os.environ.get("RELEASE_DISPATCH_SMOKE", "true").strip().lower()
    return ReleaseConfig(
        repository=repository,
        sha=sha,
        head_commit_message=os.environ.get("HEAD_COMMIT_MESSAGE", ""),
        base_ref=base_ref,
        is_branch_creation=is_branch_creation,
        workdir=Path("."),
        ci_gate_timeout_seconds=_int_env("RELEASE_CI_GATE_TIMEOUT_SECONDS", 600),
        ci_gate_poll_seconds=_int_env("RELEASE_CI_GATE_POLL_SECONDS", 10),
        consumer_repo=consumer_repo,
        allow_dispatch_smoke=dispatch_env != "false",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for .github/workflows/release.yml."""
    parser = argparse.ArgumentParser(description="Internal release orchestration CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Resolve, guard, plan, and publish a release.")

    verify_parser = subparsers.add_parser(
        "verify-gate",
        help="Verify exact-commit required CI and consumer smoke evidence.",
    )
    verify_parser.add_argument(
        "--sha",
        default=os.environ.get("GITHUB_SHA", "").strip(),
        help="Candidate commit SHA to validate (defaults to GITHUB_SHA).",
    )
    verify_parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "").strip() or "nandohdc/gh-pipelines",
        help="Source repository owner/name.",
    )
    verify_parser.add_argument(
        "--consumer-repo",
        default=os.environ.get("RELEASE_CONSUMER_REPO", "").strip() or _DEFAULT_CONSUMER_REPO,
        help="Synthetic consumer repository owner/name.",
    )
    verify_parser.add_argument(
        "--ci-timeout",
        type=int,
        default=_int_env("RELEASE_CI_GATE_TIMEOUT_SECONDS", 600),
        help="Timeout in seconds while waiting for incomplete required CI checks.",
    )
    verify_parser.add_argument(
        "--ci-poll-interval",
        type=int,
        default=_int_env("RELEASE_CI_GATE_POLL_SECONDS", 10),
        help="Poll interval in seconds for required CI checks.",
    )
    verify_parser.add_argument(
        "--allow-gh-token",
        action="store_true",
        help="Allow falling back to `gh auth token` when CANDIDATE_SMOKE_TOKEN is unset.",
    )
    verify_parser.add_argument(
        "--no-dispatch-smoke",
        action="store_true",
        help="Require an existing passing consumer smoke run without dispatching a new one.",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            config = _config_from_env()
            run_release(
                config,
                GhCliGitHub(workdir=config.workdir, repository=config.repository),
            )
        elif args.command == "verify-gate":
            if not args.sha:
                raise ReleaseError("Either --sha or GITHUB_SHA is required for verify-gate.")
            config = ReleaseConfig(
                repository=args.repository,
                sha=args.sha,
                workdir=Path("."),
                ci_gate_timeout_seconds=args.ci_timeout,
                ci_gate_poll_seconds=args.ci_poll_interval,
                consumer_repo=args.consumer_repo,
                allow_dispatch_smoke=not args.no_dispatch_smoke,
            )
            github = GhCliGitHub(
                workdir=config.workdir,
                repository=config.repository,
                allow_gh_token=args.allow_gh_token,
            )
            verify_promotion_gate(
                config, github, args.sha, dispatch_smoke=not args.no_dispatch_smoke
            )
    except ReleaseError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
