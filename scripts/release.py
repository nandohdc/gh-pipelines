#!/usr/bin/env python3
"""Internal semver release orchestration for .github/workflows/release.yml."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

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
    """Port for GitHub pull-request and release operations."""

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


class GhCliGitHub:
    """Production GitHubClient adapter backed by the gh CLI."""

    def __init__(self, workdir: Path = Path(".")) -> None:
        self._workdir = workdir

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


def run_release(config: ReleaseConfig, github: GitHubClient) -> ReleaseOutcome:
    """Resolve release intent, verify guards, plan target lineage, and publish."""
    if config.base_ref != "main" and config.is_branch_creation:
        print(
            f"Skipping release for initial branch creation on {config.base_ref} "
            f"at {config.sha}."
        )
        return ReleaseOutcome(status="skipped", reason="branch_created")

    pr_number = _resolve_pr_number(config, github)
    if pr_number is None:
        print(f"No merged PR associated with commit {config.sha}; skipping release.")
        return ReleaseOutcome(status="skipped", reason="no_merged_pr")

    labels = github.list_pr_labels(config.repository, pr_number)
    release_type = _resolve_release_type(labels)

    remote_tags = _list_remote_semver_tags(config)
    existing_for_sha = _find_existing_exact_tags_for_sha(remote_tags, config.sha)
    if release_type is None and not existing_for_sha:
        print("No release label found; skipping release.")
        return ReleaseOutcome(status="skipped", reason="no_release_label", pr_number=pr_number)

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
    return ReleaseConfig(
        repository=repository,
        sha=sha,
        head_commit_message=os.environ.get("HEAD_COMMIT_MESSAGE", ""),
        base_ref=base_ref,
        is_branch_creation=is_branch_creation,
        workdir=Path("."),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for .github/workflows/release.yml."""
    parser = argparse.ArgumentParser(description="Internal release orchestration CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Resolve, guard, plan, and publish a release.")

    args = parser.parse_args(argv)
    if args.command == "run":
        try:
            config = _config_from_env()
            run_release(config, GhCliGitHub(workdir=config.workdir))
        except ReleaseError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
