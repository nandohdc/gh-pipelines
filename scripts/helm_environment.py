#!/usr/bin/env python3
"""Verify GitHub Environment provisioning and protection rules for Helm deployments (#188)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple


class HelmEnvironmentError(RuntimeError):
    """Raised when querying or validating a repository environment fails."""


@dataclass(frozen=True)
class EnvironmentProtectionStatus:
    """Result of validating a GitHub Environment against the protected-manual contract."""

    verified: bool
    status: str
    reason: str
    repository: str
    environment: str
    default_branch: str
    reviewer_count: int = 0
    branch_policy: str = "none"
    allowed_patterns: Tuple[str, ...] = ()


class EnvironmentGitHubClient(Protocol):
    """Port for querying repository environment configuration and branch policies."""

    def get_environment(self, repo: str, environment: str) -> Optional[Dict[str, Any]]:
        ...

    def list_deployment_branch_policies(
        self, repo: str, environment: str
    ) -> List[Dict[str, Any]]:
        ...


class GhCliEnvironmentClient:
    """GitHub CLI (`gh api`) adapter for inspecting environment protection rules."""

    def _api_json(self, path: str, *, allow_404: bool = False) -> Optional[Dict[str, Any]]:
        proc = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if allow_404 and ("404" in stderr or "Not Found" in stderr):
                return None
            raise HelmEnvironmentError(f"gh api {path} failed: {stderr or proc.stdout}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise HelmEnvironmentError(f"Invalid JSON returned for {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise HelmEnvironmentError(f"Expected JSON object for {path}.")
        return data

    def get_environment(self, repo: str, environment: str) -> Optional[Dict[str, Any]]:
        encoded = urllib.parse.quote(environment, safe="")
        return self._api_json(f"/repos/{repo}/environments/{encoded}", allow_404=True)

    def list_deployment_branch_policies(
        self, repo: str, environment: str
    ) -> List[Dict[str, Any]]:
        encoded = urllib.parse.quote(environment, safe="")
        data = self._api_json(f"/repos/{repo}/environments/{encoded}/deployment-branch-policies")
        assert data is not None
        policies = data.get("branch_policies")
        if not isinstance(policies, list):
            raise HelmEnvironmentError(
                f"Expected branch_policies list for {repo} environment {environment}."
            )
        return [item for item in policies if isinstance(item, dict)]


def _validate_nonempty_single_line(value: str, label: str) -> str:
    if "\n" in value or "\r" in value:
        raise HelmEnvironmentError(f"{label} must be a single line.")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise HelmEnvironmentError(
            f"{label} must be non-empty without leading or trailing whitespace."
        )
    return value


def _extract_reviewer_count(env_data: Dict[str, Any]) -> int:
    rules = env_data.get("protection_rules")
    if not isinstance(rules, list):
        return 0
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers":
            reviewers = rule.get("reviewers")
            if isinstance(reviewers, list):
                return len(reviewers)
    return 0


def _is_broad_wildcard(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern or "[" in pattern


def verify_protected_environment(
    client: EnvironmentGitHubClient,
    *,
    repository: str,
    environment: str,
    default_branch: str = "main",
) -> EnvironmentProtectionStatus:
    """Verify that repository/environment exists and enforces approval and branch protections."""
    repo = _validate_nonempty_single_line(repository, "repository")
    env_name = _validate_nonempty_single_line(environment, "environment")
    branch = _validate_nonempty_single_line(default_branch, "default_branch")

    env_data = client.get_environment(repo, env_name)
    if env_data is None:
        return EnvironmentProtectionStatus(
            verified=False,
            status="unprovisioned",
            reason="environment_not_found",
            repository=repo,
            environment=env_name,
            default_branch=branch,
        )

    reviewer_count = _extract_reviewer_count(env_data)
    if reviewer_count < 1:
        return EnvironmentProtectionStatus(
            verified=False,
            status="unprotected",
            reason="missing_required_reviewers",
            repository=repo,
            environment=env_name,
            default_branch=branch,
            reviewer_count=0,
        )

    branch_policy = env_data.get("deployment_branch_policy")
    if not isinstance(branch_policy, dict):
        return EnvironmentProtectionStatus(
            verified=False,
            status="unprotected",
            reason="unrestricted_deployment_branches",
            repository=repo,
            environment=env_name,
            default_branch=branch,
            reviewer_count=reviewer_count,
            branch_policy="all_branches",
        )

    if branch_policy.get("protected_branches") is True:
        return EnvironmentProtectionStatus(
            verified=True,
            status="protected",
            reason="verified_required_reviewers_and_protected_branches",
            repository=repo,
            environment=env_name,
            default_branch=branch,
            reviewer_count=reviewer_count,
            branch_policy="protected_branches",
        )

    if branch_policy.get("custom_branch_policies") is True:
        policies = client.list_deployment_branch_policies(repo, env_name)
        branch_patterns = tuple(
            str(item.get("name") or "")
            for item in policies
            if (item.get("type") in (None, "", "branch")) and item.get("name")
        )
        if not branch_patterns:
            return EnvironmentProtectionStatus(
                verified=False,
                status="unprotected",
                reason="empty_custom_branch_policies",
                repository=repo,
                environment=env_name,
                default_branch=branch,
                reviewer_count=reviewer_count,
                branch_policy="custom_branch_policies",
            )
        if any(_is_broad_wildcard(pattern) for pattern in branch_patterns):
            return EnvironmentProtectionStatus(
                verified=False,
                status="unprotected",
                reason="wildcard_branch_policy_not_allowed",
                repository=repo,
                environment=env_name,
                default_branch=branch,
                reviewer_count=reviewer_count,
                branch_policy="custom_branch_policies",
                allowed_patterns=branch_patterns,
            )
        if branch not in branch_patterns or len(branch_patterns) != 1:
            return EnvironmentProtectionStatus(
                verified=False,
                status="unprotected",
                reason="custom_branch_policy_not_restricted_to_default_branch",
                repository=repo,
                environment=env_name,
                default_branch=branch,
                reviewer_count=reviewer_count,
                branch_policy="custom_branch_policies",
                allowed_patterns=branch_patterns,
            )
        return EnvironmentProtectionStatus(
            verified=True,
            status="protected",
            reason="verified_required_reviewers_and_default_branch_policy",
            repository=repo,
            environment=env_name,
            default_branch=branch,
            reviewer_count=reviewer_count,
            branch_policy="custom_branch_policies",
            allowed_patterns=branch_patterns,
        )

    return EnvironmentProtectionStatus(
        verified=False,
        status="unprotected",
        reason="unrecognized_branch_policy",
        repository=repo,
        environment=env_name,
        default_branch=branch,
        reviewer_count=reviewer_count,
    )


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="Verify GitHub Environment protection rules.")
    verify.add_argument("--repo", required=True, help="Repository slug (<owner>/<repo>).")
    verify.add_argument("--environment", required=True, help="GitHub Environment name.")
    verify.add_argument(
        "--default-branch",
        default="main",
        help="Expected default branch name (default: main).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.command == "verify":
        client = GhCliEnvironmentClient()
        try:
            result = verify_protected_environment(
                client,
                repository=args.repo,
                environment=args.environment,
                default_branch=args.default_branch,
            )
        except HelmEnvironmentError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "verified": result.verified,
                    "status": result.status,
                    "reason": result.reason,
                    "repository": result.repository,
                    "environment": result.environment,
                    "default_branch": result.default_branch,
                    "reviewer_count": result.reviewer_count,
                    "branch_policy": result.branch_policy,
                    "allowed_patterns": list(result.allowed_patterns),
                },
                sort_keys=True,
            )
        )
        return 0 if result.verified else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
