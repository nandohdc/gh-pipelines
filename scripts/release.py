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
from typing import Any, List, Optional, Protocol, Sequence, Tuple

_SQUASH_PR_RE = re.compile(r"\(#([0-9]+)\)[ \t\r\f\v]*$")
_MERGE_PR_RE = re.compile(r"^Merge\s+pull\s+request\s+#([0-9]+)")
_SEMVER_TAG_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
_ATTEST_SBOM_RE = re.compile(r"^[ \t]*attest-sbom:", re.MULTILINE)
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

    def find_merged_pr_for_commit(self, repository: str, sha: str) -> Optional[int]:
        ...

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        ...

    def release_exists(self, tag: str) -> bool:
        ...

    def create_release(self, tag: str, target_sha: str, title: str) -> None:
        ...


@dataclass(frozen=True)
class ReleaseConfig:
    """Inputs required to resolve, validate, plan, and publish a release."""

    repository: str
    sha: str
    head_commit_message: str = ""
    workdir: Path = Path(".")
    remote: str = "origin"
    manifest_relpath: str = "actions/supply-chain-attest/action.yml"


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

    def find_merged_pr_for_commit(self, repository: str, sha: str) -> Optional[int]:
        pulls = self._api_json(f"/repos/{repository}/commits/{sha}/pulls")
        if not isinstance(pulls, list):
            raise ReleaseError(f"Expected PR list from gh api for commit {sha}")
        for pr in pulls:
            if not isinstance(pr, dict):
                continue
            base = pr.get("base") or {}
            if pr.get("merged_at") is not None and base.get("ref") == "main":
                number = pr.get("number")
                if number is not None:
                    return int(number)
        return None

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        labels = self._api_json(f"/repos/{repository}/issues/{pr_number}/labels")
        if not isinstance(labels, list):
            raise ReleaseError(f"Expected label list from gh api for PR #{pr_number}")
        return [str(item["name"]) for item in labels if isinstance(item, dict) and "name" in item]

    def release_exists(self, tag: str) -> bool:
        result = _run_cmd(["gh", "release", "view", tag], self._workdir)
        if result.returncode == 0:
            return True
        detail = (result.stderr or result.stdout).strip()
        lowered = detail.lower()
        if "release not found" in lowered or "http 404" in lowered:
            return False
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
    return github.find_merged_pr_for_commit(config.repository, config.sha)


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
            "behind @v1. Aborting release."
        )
    print(f"Release guard OK: {config.manifest_relpath} declares attest-sbom.")


def _parse_semver(tag: str) -> Optional[Tuple[int, int, int]]:
    match = _SEMVER_TAG_RE.match(tag)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _compute_next_tag(config: ReleaseConfig, release_type: str) -> Tuple[str, str]:
    output = _run_checked(
        ["git", "ls-remote", "--tags", "--refs", config.remote, "v*"],
        config.workdir,
    )
    versions: List[Tuple[Tuple[int, int, int], str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        _, ref = line.split(None, 1)
        tag = ref.replace("refs/tags/", "", 1)
        parsed = _parse_semver(tag)
        if parsed is not None:
            versions.append((parsed, tag))

    if versions:
        (major, minor, patch), latest_tag = max(versions, key=lambda item: item[0])
    else:
        major, minor, patch = 0, 0, 0
        latest_tag = "v0.0.0"

    if release_type == "patch":
        next_tag = f"v{major}.{minor}.{patch + 1}"
    elif release_type == "minor":
        next_tag = f"v{major}.{minor + 1}.0"
    elif release_type == "major":
        next_tag = f"v{major + 1}.0.0"
    else:
        raise ReleaseError(f"Unsupported release type: {release_type}")

    return latest_tag, next_tag


def _publish_tags(config: ReleaseConfig, next_tag: str) -> None:
    _run_checked(["git", "config", "user.name", _BOT_NAME], config.workdir)
    _run_checked(["git", "config", "user.email", _BOT_EMAIL], config.workdir)

    existing_output = _run_checked(
        ["git", "ls-remote", "--tags", "--refs", config.remote, f"refs/tags/{next_tag}"],
        config.workdir,
    ).strip()
    if existing_output:
        existing_sha = existing_output.split(None, 1)[0]
        if existing_sha != config.sha:
            raise ReleaseError(
                f"Tag {next_tag} already exists and points to {existing_sha}, "
                f"expected {config.sha}."
            )
        print(f"Tag {next_tag} already exists at target SHA; continuing.")
    else:
        _run_checked(["git", "tag", next_tag, config.sha], config.workdir)
        _run_checked(["git", "push", config.remote, f"refs/tags/{next_tag}"], config.workdir)

    _run_checked(["git", "tag", "-f", "v1", config.sha], config.workdir)
    _run_checked(["git", "push", config.remote, "refs/tags/v1", "--force"], config.workdir)


def _publish_github_release(github: GitHubClient, next_tag: str, target_sha: str) -> None:
    if github.release_exists(next_tag):
        print(f"Release {next_tag} already exists; skipping creation.")
        return
    github.create_release(next_tag, target_sha, next_tag)


def run_release(config: ReleaseConfig, github: GitHubClient) -> ReleaseOutcome:
    """Resolve release intent, verify guards, compute next semver, and publish."""
    pr_number = _resolve_pr_number(config, github)
    if pr_number is None:
        print(f"No merged PR associated with commit {config.sha}; skipping release.")
        return ReleaseOutcome(status="skipped", reason="no_merged_pr")

    labels = github.list_pr_labels(config.repository, pr_number)
    release_type = _resolve_release_type(labels)
    if release_type is None:
        print("No release label found; skipping release.")
        return ReleaseOutcome(status="skipped", reason="no_release_label", pr_number=pr_number)

    print(f"Detected release type: {release_type} (PR #{pr_number})")
    _verify_attestation_guard(config)
    latest_tag, next_tag = _compute_next_tag(config, release_type)
    _publish_tags(config, next_tag)
    _publish_github_release(github, next_tag, config.sha)
    return ReleaseOutcome(
        status="published",
        reason="published",
        pr_number=pr_number,
        release_type=release_type,
        latest_tag=latest_tag,
        next_tag=next_tag,
        target_sha=config.sha,
    )


def _config_from_env() -> ReleaseConfig:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if not repository:
        raise ReleaseError("GITHUB_REPOSITORY environment variable is required.")
    if not sha:
        raise ReleaseError("GITHUB_SHA environment variable is required.")
    return ReleaseConfig(
        repository=repository,
        sha=sha,
        head_commit_message=os.environ.get("HEAD_COMMIT_MESSAGE", ""),
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
