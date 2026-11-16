#!/usr/bin/env python3
"""Behavior tests for scripts/release.py at the module and CLI seams."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest import mock

from scripts.release import (
    REQUIRED_CI_CHECKS,
    GhCliGitHub,
    ReleaseConfig,
    ReleaseError,
    ReleaseOutcome,
    ReleasePlan,
    _publish_tags,
    run_release,
    verify_promotion_gate,
)


def _make_passing_ci_evidence(
    repository: str,
    sha: str,
    *,
    extra_workflow_runs: Optional[List[dict]] = None,
    extra_check_runs: Optional[List[dict]] = None,
) -> Tuple[List[dict], List[dict]]:
    workflow_ids: Dict[str, int] = {}
    workflow_runs: List[dict] = []
    check_runs: List[dict] = []
    next_run_id = 1000
    for idx, (workflow_path, job_name) in enumerate(REQUIRED_CI_CHECKS, start=1):
        if workflow_path not in workflow_ids:
            workflow_ids[workflow_path] = next_run_id
            workflow_runs.append(
                {
                    "id": next_run_id,
                    "path": workflow_path,
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                    "event": "push",
                }
            )
            next_run_id += 1
        run_id = workflow_ids[workflow_path]
        check_runs.append(
            {
                "id": 5000 + idx,
                "name": job_name,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "details_url": f"https://github.com/{repository}/actions/runs/{run_id}/job/{7000 + idx}",
            }
        )
    if extra_workflow_runs:
        workflow_runs.extend(extra_workflow_runs)
    if extra_check_runs:
        check_runs.extend(extra_check_runs)
    return workflow_runs, check_runs


class FakeGitHub:
    """In-memory adapter implementing the GitHubClient seam for tests."""

    def __init__(
        self,
        *,
        commit_prs: Optional[Dict[str, List[dict]]] = None,
        pr_labels: Optional[Dict[int, List[str]]] = None,
        existing_releases: Optional[Dict[str, dict]] = None,
        commit_check_runs: Optional[Dict[str, List[dict]]] = None,
        commit_workflow_runs: Optional[Dict[str, List[dict]]] = None,
        check_runs_sequence: Optional[Dict[str, List[Tuple[List[dict], List[dict]]]]] = None,
        candidate_smoke_results: Optional[Dict[str, object]] = None,
        fail_on: Optional[str] = None,
    ) -> None:
        self.commit_prs = commit_prs or {}
        self.pr_labels = pr_labels or {}
        self.releases: Dict[str, dict] = dict(existing_releases or {})
        self.commit_check_runs = commit_check_runs or {}
        self.commit_workflow_runs = commit_workflow_runs or {}
        self.check_runs_sequence = {
            sha: list(seq) for sha, seq in (check_runs_sequence or {}).items()
        }
        self.candidate_smoke_results = dict(candidate_smoke_results or {})
        self.fail_on = fail_on
        self.smoke_calls: List[str] = []

    def find_merged_pr_for_commit(
        self, repository: str, sha: str, base_ref: str = "main"
    ) -> Optional[int]:
        if self.fail_on == "find_merged_pr_for_commit":
            raise ReleaseError("simulated gh api commit pulls failure")
        for pr in self.commit_prs.get(sha, []):
            base = pr.get("base") or {}
            if pr.get("merged_at") is not None and base.get("ref") == base_ref:
                return int(pr["number"])
        return None

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        if self.fail_on == "list_pr_labels":
            raise ReleaseError("simulated gh api labels failure")
        return list(self.pr_labels.get(pr_number, []))

    def release_exists(self, tag: str) -> bool:
        return self.get_release(tag) is not None

    def get_release(self, tag: str) -> Optional[dict]:
        if self.fail_on in ("release_exists", "get_release"):
            raise ReleaseError("simulated gh release view failure")
        return self.releases.get(tag)

    def create_release(self, tag: str, target_sha: str, title: str) -> None:
        if self.fail_on == "create_release":
            raise ReleaseError("simulated gh release create failure")
        self.releases[tag] = {"tag": tag, "target_sha": target_sha, "title": title}

    def list_workflow_runs_for_sha(self, repository: str, sha: str) -> List[dict]:
        if self.fail_on == "list_workflow_runs_for_sha":
            raise ReleaseError("simulated gh api workflow runs failure")
        if sha in self.commit_workflow_runs:
            return list(self.commit_workflow_runs[sha])
        if sha in self.check_runs_sequence and self.check_runs_sequence[sha]:
            wf_runs, _ = self.check_runs_sequence[sha][0]
            return list(wf_runs)
        wf_runs, _ = _make_passing_ci_evidence(repository, sha)
        return wf_runs

    def list_commit_check_runs(self, repository: str, sha: str) -> List[dict]:
        if self.fail_on == "list_commit_check_runs":
            raise ReleaseError("simulated gh api check runs failure")
        if sha in self.commit_check_runs:
            return list(self.commit_check_runs[sha])
        if sha in self.check_runs_sequence and self.check_runs_sequence[sha]:
            _, ck_runs = self.check_runs_sequence[sha].pop(0)
            return list(ck_runs)
        _, ck_runs = _make_passing_ci_evidence(repository, sha)
        return ck_runs

    def ensure_candidate_smoke(
        self,
        candidate_sha: str,
        *,
        consumer_repo: str = "nandohdc/gh-pipelines-hosted-acceptance",
    ) -> dict:
        self.smoke_calls.append(candidate_sha)
        if self.fail_on == "ensure_candidate_smoke":
            raise ReleaseError("simulated candidate smoke outage")
        if candidate_sha in self.candidate_smoke_results:
            override = self.candidate_smoke_results[candidate_sha]
            if isinstance(override, Exception):
                raise override
            assert isinstance(override, dict)
            return dict(override)
        return {
            "status": "passed",
            "candidate_sha": candidate_sha,
            "consumer_repo": consumer_repo,
            "run_id": 88001,
            "run_url": f"https://github.com/{consumer_repo}/actions/runs/88001",
            "reused": True,
        }


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_repo_with_bare_remote(
    root: Path,
    *,
    with_attest_sbom: bool = True,
) -> Tuple[Path, Path, str]:
    bare = root / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main")

    work = root / "work"
    _git(root, "clone", str(bare), str(work))
    _git(work, "config", "user.name", "Test Author")
    _git(work, "config", "user.email", "test@example.com")

    manifest = work / "actions" / "supply-chain-attest" / "action.yml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if with_attest_sbom:
        manifest.write_text("inputs:\n  attest-sbom:\n    default: 'false'\n", encoding="utf-8")
    else:
        manifest.write_text("inputs:\n  sign-image:\n    default: 'false'\n", encoding="utf-8")

    _git(work, "add", ".")
    _git(work, "commit", "-m", "chore: initial commit")
    _git(work, "push", "-u", "origin", "main")
    sha = _git(work, "rev-parse", "HEAD")
    return bare, work, sha


def _remote_tags(bare: Path) -> Dict[str, str]:
    output = _git(bare, "show-ref", "--tags")
    tags: Dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split(None, 1)
        tags[ref.replace("refs/tags/", "", 1)] = sha
    return tags


def _try_remote_tags(bare: Path) -> Dict[str, str]:
    result = subprocess.run(
        ["git", "show-ref", "--tags"],
        cwd=bare,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    return _remote_tags(bare)


def _install_pre_receive_hook(bare: Path, rejected_ref: str) -> None:
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while read -r _old _new ref; do\n"
        f'  if [[ "${{ref}}" == "{rejected_ref}" ]]; then\n'
        f'    echo "rejected ref ${{ref}}" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


class ReleaseModuleBehaviorTest(unittest.TestCase):
    def test_skips_when_commit_has_no_associated_merged_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            github = FakeGitHub()
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="chore: direct push without PR",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "skipped")
            self.assertEqual(outcome.reason, "no_merged_pr")
            self.assertEqual(_try_remote_tags(bare), {})
            self.assertEqual(github.releases, {})

    def test_skips_when_merged_pr_has_no_release_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            github = FakeGitHub(pr_labels={42: ["documentation", "ci"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="docs: update readme (#42)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "skipped")
            self.assertEqual(outcome.reason, "no_release_label")
            self.assertEqual(outcome.pr_number, 42)
            self.assertEqual(_try_remote_tags(bare), {})
            self.assertEqual(github.releases, {})

    def test_publishes_initial_patch_release_tags_and_github_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            github = FakeGitHub(pr_labels={101: ["release:patch"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="fix: tighten workflow guard (#101)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.pr_number, 101)
            self.assertEqual(outcome.release_type, "patch")
            self.assertEqual(outcome.latest_tag, "v0.0.0")
            self.assertEqual(outcome.next_tag, "v0.0.1")
            self.assertEqual(outcome.target_sha, sha)
            self.assertEqual(
                outcome.plan,
                ReleasePlan(
                    pr_number=101,
                    release_type="patch",
                    target_major=0,
                    target_lineage="v0",
                    latest_tag="v0.0.0",
                    next_tag="v0.0.1",
                    floating_tag="v0",
                    target_sha=sha,
                ),
            )
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v0": sha})
            self.assertEqual(
                github.releases,
                {"v0.0.1": {"tag": "v0.0.1", "target_sha": sha, "title": "v0.0.1"}},
            )

    def test_resolves_classic_merge_commit_and_bumps_minor_from_highest_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.2.3", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.2.3", "refs/tags/v1")

            (work / "README.md").write_text("updated\n", encoding="utf-8")
            _git(work, "add", "README.md")
            _git(work, "commit", "-m", "Merge pull request #77 from nandohdc/feat-x\n\nAdd feature X")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={77: ["release:minor", "enhancement"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="Merge pull request #77 from nandohdc/feat-x\n\nAdd feature X",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.pr_number, 77)
            self.assertEqual(outcome.latest_tag, "v1.2.3")
            self.assertEqual(outcome.next_tag, "v1.3.0")
            self.assertEqual(_remote_tags(bare), {"v1.2.3": first_sha, "v1.3.0": second_sha, "v1": second_sha})

    def test_uses_api_fallback_and_major_minor_patch_label_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.2.3", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.2.3", "refs/tags/v1")

            (work / "CHANGELOG.md").write_text("breaking\n", encoding="utf-8")
            _git(work, "add", "CHANGELOG.md")
            _git(work, "commit", "-m", "feat!: drop legacy input")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                commit_prs={
                    second_sha: [
                        {"number": 88, "merged_at": None, "base": {"ref": "main"}},
                        {"number": 89, "merged_at": "2026-11-14T20:00:00Z", "base": {"ref": "dev"}},
                        {"number": 90, "merged_at": "2026-11-14T21:00:00Z", "base": {"ref": "main"}},
                    ]
                },
                pr_labels={90: ["release:patch", "release:minor", "release:major"]},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="feat!: drop legacy input",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.pr_number, 90)
            self.assertEqual(outcome.release_type, "major")
            self.assertEqual(outcome.next_tag, "v2.0.0")
            self.assertEqual(
                outcome.plan,
                ReleasePlan(
                    pr_number=90,
                    release_type="major",
                    target_major=2,
                    target_lineage="v2",
                    latest_tag="v1.2.3",
                    next_tag="v2.0.0",
                    floating_tag="v2",
                    target_sha=second_sha,
                ),
            )
            self.assertEqual(
                _remote_tags(bare),
                {"v1.2.3": first_sha, "v1": first_sha, "v2.0.0": second_sha, "v2": second_sha},
            )

    def test_skips_release_creation_when_github_release_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v0.0.2", first_sha)
            _git(work, "push", "origin", "refs/tags/v0.0.2")

            (work / "next.txt").write_text("next\n", encoding="utf-8")
            _git(work, "add", "next.txt")
            _git(work, "commit", "-m", "fix: retry (#11)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={11: ["release:patch"]},
                existing_releases={"v0.0.3": {"tag": "v0.0.3", "target_sha": second_sha, "title": "existing"}},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: retry (#11)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v0.0.3")
            self.assertEqual(github.releases["v0.0.3"]["title"], "existing")
            self.assertEqual(_remote_tags(bare)["v0"], second_sha)

    def test_fails_when_attestation_manifest_lacks_attest_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp), with_attest_sbom=False)
            github = FakeGitHub(pr_labels={12: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=sha,
                        head_commit_message="fix: missing attest-sbom (#12)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_try_remote_tags(bare), {})
            self.assertEqual(github.releases, {})

    def test_fails_when_version_tag_push_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            (work / "file.txt").write_text("next\n", encoding="utf-8")
            _git(work, "add", "file.txt")
            _git(work, "commit", "-m", "fix: second commit (#13)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            _install_pre_receive_hook(bare, "refs/tags/v0.0.1")
            github = FakeGitHub(pr_labels={13: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="fix: second commit (#13)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_try_remote_tags(bare), {})
            self.assertEqual(github.releases, {})

    def test_interrupts_deterministically_on_floating_tag_push_leaving_version_tag_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            _install_pre_receive_hook(bare, "refs/tags/v0")
            github = FakeGitHub(pr_labels={14: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=sha,
                        head_commit_message="fix: push interrupt (#14)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha})
            self.assertEqual(github.releases, {})

    def test_interrupts_deterministically_on_github_release_creation_leaving_both_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            github = FakeGitHub(pr_labels={15: ["release:patch"]}, fail_on="create_release")
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=sha,
                        head_commit_message="fix: release interrupt (#15)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v0": sha})
            self.assertEqual(github.releases, {})

    def test_propagates_github_api_errors_during_resolution_and_release_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for fail_point, message, expect_tags in (
                ("find_merged_pr_for_commit", "feat: commit without pr number", {}),
                ("list_pr_labels", "fix: commit with (#16)", {}),
            ):
                with self.subTest(fail_point=fail_point):
                    sub_dir = Path(tmp) / fail_point
                    sub_dir.mkdir()
                    bare, work, sha = _create_repo_with_bare_remote(sub_dir)
                    github = FakeGitHub(pr_labels={16: ["release:patch"]}, fail_on=fail_point)
                    with self.assertRaises(ReleaseError):
                        run_release(
                            ReleaseConfig(
                                repository="nandohdc/gh-pipelines",
                                sha=sha,
                                head_commit_message=message,
                                workdir=work,
                            ),
                            github,
                        )
                    self.assertEqual(_try_remote_tags(bare), expect_tags)
                    self.assertEqual(github.releases, {})

            sub_dir = Path(tmp) / "release_exists"
            sub_dir.mkdir()
            bare, work, sha = _create_repo_with_bare_remote(sub_dir)
            github = FakeGitHub(pr_labels={16: ["release:patch"]}, fail_on="release_exists")
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=sha,
                        head_commit_message="fix: commit with (#16)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v0": sha})
            self.assertEqual(github.releases, {})

    def test_major_release_from_v0_creates_v1_leaving_v0_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v0.0.1", first_sha)
            _git(work, "tag", "v0", first_sha)
            _git(work, "push", "origin", "refs/tags/v0.0.1", "refs/tags/v0")

            (work / "breaking.txt").write_text("v1\n", encoding="utf-8")
            _git(work, "add", "breaking.txt")
            _git(work, "commit", "-m", "feat!: cut v1 (#20)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={20: ["release:major"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="feat!: cut v1 (#20)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v1.0.0")
            self.assertIsNotNone(outcome.plan)
            assert outcome.plan is not None
            self.assertEqual(outcome.plan.target_lineage, "v1")
            self.assertEqual(
                _remote_tags(bare),
                {"v0.0.1": first_sha, "v0": first_sha, "v1.0.0": second_sha, "v1": second_sha},
            )

    def test_interleaved_old_and_new_major_releases_stay_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, v1_10_0_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "-a", "v1.10.0", "-m", "v1.10.0", v1_10_0_sha)
            _git(work, "tag", "v1", v1_10_0_sha)
            _git(work, "push", "origin", "refs/tags/v1.10.0", "refs/tags/v1")

            # Create maintenance branch rooted at v1.10.0 before v2 lands on main.
            _git(work, "checkout", "-b", "release/v1", v1_10_0_sha)
            _git(work, "push", "-u", "origin", "release/v1")
            _git(work, "checkout", "main")

            # 1. Breaking change on main cuts v2.0.0 + v2 while leaving v1 at v1.10.0.
            (work / "v2.txt").write_text("breaking\n", encoding="utf-8")
            _git(work, "add", "v2.txt")
            _git(work, "commit", "-m", "feat!: breaking v2 (#200)")
            _git(work, "push", "origin", "main")
            v2_0_0_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={
                    200: ["release:major"],
                    201: ["release:patch"],
                    202: ["release:minor"],
                    203: ["release:minor"],
                }
            )
            out_v2 = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=v2_0_0_sha,
                    head_commit_message="feat!: breaking v2 (#200)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(out_v2.next_tag, "v2.0.0")
            self.assertEqual(out_v2.plan.target_major, 2)  # type: ignore[union-attr]
            self.assertEqual(_remote_tags(bare)["v1"], v1_10_0_sha)
            self.assertEqual(_remote_tags(bare)["v2"], v2_0_0_sha)

            # 2. Maintenance patch on release/v1 publishes v1.10.1 + moves v1, leaves v2 untouched.
            _git(work, "checkout", "release/v1")
            (work / "v1_fix.txt").write_text("fix 1\n", encoding="utf-8")
            _git(work, "add", "v1_fix.txt")
            _git(work, "commit", "-m", "fix: patch v1 (#201)")
            _git(work, "push", "origin", "release/v1")
            v1_10_1_sha = _git(work, "rev-parse", "HEAD")

            out_v1_patch = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=v1_10_1_sha,
                    head_commit_message="fix: patch v1 (#201)",
                    base_ref="release/v1",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(out_v1_patch.latest_tag, "v1.10.0")
            self.assertEqual(out_v1_patch.next_tag, "v1.10.1")
            self.assertEqual(out_v1_patch.plan.target_lineage, "v1")  # type: ignore[union-attr]
            self.assertEqual(_remote_tags(bare)["v1"], v1_10_1_sha)
            self.assertEqual(_remote_tags(bare)["v2"], v2_0_0_sha)

            # 3. Minor on main publishes v2.1.0 (from v2.0.0, not v1.10.1) + moves v2, leaves v1 at v1.10.1.
            _git(work, "checkout", "main")
            (work / "v2_feat.txt").write_text("feat\n", encoding="utf-8")
            _git(work, "add", "v2_feat.txt")
            _git(work, "commit", "-m", "feat: minor v2 (#202)")
            _git(work, "push", "origin", "main")
            v2_1_0_sha = _git(work, "rev-parse", "HEAD")

            out_v2_minor = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=v2_1_0_sha,
                    head_commit_message="feat: minor v2 (#202)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(out_v2_minor.latest_tag, "v2.0.0")
            self.assertEqual(out_v2_minor.next_tag, "v2.1.0")
            self.assertEqual(out_v2_minor.plan.target_lineage, "v2")  # type: ignore[union-attr]
            self.assertEqual(_remote_tags(bare)["v1"], v1_10_1_sha)
            self.assertEqual(_remote_tags(bare)["v2"], v2_1_0_sha)

            # 4. Minor on release/v1 publishes v1.11.0 (from v1.10.1, not v2.1.0) + moves v1, leaves v2 at v2.1.0.
            _git(work, "checkout", "release/v1")
            (work / "v1_feat.txt").write_text("feat 1\n", encoding="utf-8")
            _git(work, "add", "v1_feat.txt")
            _git(work, "commit", "-m", "feat: minor v1 (#203)")
            _git(work, "push", "origin", "release/v1")
            v1_11_0_sha = _git(work, "rev-parse", "HEAD")

            out_v1_minor = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=v1_11_0_sha,
                    head_commit_message="feat: minor v1 (#203)",
                    base_ref="release/v1",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(out_v1_minor.latest_tag, "v1.10.1")
            self.assertEqual(out_v1_minor.next_tag, "v1.11.0")
            self.assertEqual(out_v1_minor.plan.target_lineage, "v1")  # type: ignore[union-attr]

            tags = _remote_tags(bare)
            self.assertEqual(tags["v1.10.1"], v1_10_1_sha)
            self.assertEqual(tags["v1.11.0"], v1_11_0_sha)
            self.assertEqual(tags["v1"], v1_11_0_sha)
            self.assertEqual(tags["v2.0.0"], v2_0_0_sha)
            self.assertEqual(tags["v2.1.0"], v2_1_0_sha)
            self.assertEqual(tags["v2"], v2_1_0_sha)
            self.assertEqual(
                set(github.releases.keys()),
                {"v2.0.0", "v1.10.1", "v2.1.0", "v1.11.0"},
            )

    def test_main_uses_ancestry_to_stay_on_v1_when_v2_is_on_side_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, v1_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", v1_sha)
            _git(work, "tag", "v1", v1_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            # Side branch tagged v2.0.0 that is not merged into main.
            _git(work, "checkout", "-b", "v2-preview", v1_sha)
            (work / "preview.txt").write_text("v2\n", encoding="utf-8")
            _git(work, "add", "preview.txt")
            _git(work, "commit", "-m", "feat!: preview v2")
            v2_sha = _git(work, "rev-parse", "HEAD")
            _git(work, "tag", "v2.0.0", v2_sha)
            _git(work, "tag", "v2", v2_sha)
            _git(work, "push", "origin", "refs/tags/v2.0.0", "refs/tags/v2")

            _git(work, "checkout", "main")
            (work / "main_fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "main_fix.txt")
            _git(work, "commit", "-m", "fix: compatible main fix (#204)")
            _git(work, "push", "origin", "main")
            main_fix_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={204: ["release:patch"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=main_fix_sha,
                    head_commit_message="fix: compatible main fix (#204)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.next_tag, "v1.0.1")
            self.assertEqual(outcome.plan.target_major, 1)  # type: ignore[union-attr]
            self.assertEqual(_remote_tags(bare)["v1"], main_fix_sha)
            self.assertEqual(_remote_tags(bare)["v2"], v2_sha)

    def test_skips_initial_branch_creation_on_maintenance_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.10.0", sha)
            _git(work, "tag", "v1", sha)
            _git(work, "push", "origin", "refs/tags/v1.10.0", "refs/tags/v1")

            github = FakeGitHub(pr_labels={156: ["release:minor"]})
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="feat: earlier main release (#156)",
                    base_ref="release/v1",
                    is_branch_creation=True,
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "skipped")
            self.assertEqual(outcome.reason, "branch_created")
            self.assertEqual(_remote_tags(bare), {"v1.10.0": sha, "v1": sha})
            self.assertEqual(github.releases, {})

    def test_rejects_invalid_maintenance_lineage_configurations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "v2.txt").write_text("v2\n", encoding="utf-8")
            _git(work, "add", "v2.txt")
            _git(work, "commit", "-m", "feat!: v2 (#210)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")
            _git(work, "tag", "v2.0.0", second_sha)
            _git(work, "tag", "v2", second_sha)
            _git(work, "push", "origin", "refs/tags/v2.0.0", "refs/tags/v2")

            github = FakeGitHub(
                pr_labels={
                    211: ["release:major"],
                    212: ["release:patch"],
                    213: ["release:patch"],
                }
            )
            for base_ref, sha, pr_num, msg in (
                ("release/v1", first_sha, 211, "feat!: major on v1 (#211)"),
                ("release/v1", second_sha, 212, "fix: v2 ancestor on v1 (#212)"),
                ("release/v9", first_sha, 213, "fix: missing v9 lineage (#213)"),
            ):
                with self.subTest(base_ref=base_ref, pr_num=pr_num):
                    with self.assertRaises(ReleaseError):
                        run_release(
                            ReleaseConfig(
                                repository="nandohdc/gh-pipelines",
                                sha=sha,
                                head_commit_message=msg,
                                base_ref=base_ref,
                                workdir=work,
                            ),
                            github,
                        )
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1": first_sha, "v2.0.0": second_sha, "v2": second_sha},
            )
            self.assertEqual(github.releases, {})

    def test_rejects_conflicting_immutable_version_tag_without_force_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "next.txt").write_text("next\n", encoding="utf-8")
            _git(work, "add", "next.txt")
            _git(work, "commit", "-m", "fix: next (#220)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            # Pre-existing local conflicting v1.0.1 tag must fail clearly rather than force-tag.
            _git(work, "tag", "v1.0.1", first_sha)
            github = FakeGitHub(pr_labels={220: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="fix: next (#220)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v1.0.0": first_sha, "v1": first_sha})
            self.assertEqual(github.releases, {})

    def test_fails_when_attestation_manifest_lacks_attest_sbom_for_major_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            manifest = work / "actions" / "supply-chain-attest" / "action.yml"
            manifest.write_text("inputs:\n  sign-image:\n    default: 'false'\n", encoding="utf-8")
            _git(work, "add", str(manifest))
            _git(work, "commit", "-m", "feat!: remove attest-sbom (#221)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={221: ["release:major"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="feat!: remove attest-sbom (#221)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v1.0.0": first_sha, "v1": first_sha})
            self.assertEqual(github.releases, {})

    def test_retry_after_completed_release_is_noop_even_if_labels_changed_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: single release (#230)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={230: ["release:patch"]})
            first_outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: single release (#230)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(first_outcome.status, "published")
            self.assertEqual(first_outcome.next_tag, "v1.0.1")

            # Retry with edited label: must remain a no-op for v1.0.1 (no v1.1.0 or v1.0.2).
            github.pr_labels[230] = ["release:minor"]
            second_outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: single release (#230)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(second_outcome.status, "noop")
            self.assertEqual(second_outcome.reason, "already_released")
            self.assertEqual(second_outcome.next_tag, "v1.0.1")
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": second_sha},
            )
            self.assertEqual(
                github.releases,
                {"v1.0.1": {"tag": "v1.0.1", "target_sha": second_sha, "title": "v1.0.1"}},
            )

            # Retry after label removal: still a no-op for v1.0.1.
            github.pr_labels[230] = []
            third_outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: single release (#230)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(third_outcome.status, "noop")
            self.assertEqual(third_outcome.reason, "already_released")
            self.assertEqual(third_outcome.next_tag, "v1.0.1")

    def test_resumes_after_floating_tag_interruption_even_after_label_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: partial tag push (#231)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            _install_pre_receive_hook(bare, "refs/tags/v1")
            github = FakeGitHub(pr_labels={231: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="fix: partial tag push (#231)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": first_sha},
            )
            self.assertEqual(github.releases, {})

            # Clear the hook failure, mutate PR label, and retry: must resume v1.0.1.
            (bare / "hooks" / "pre-receive").unlink()
            github.pr_labels[231] = ["release:minor"]
            resumed = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: partial tag push (#231)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(resumed.status, "published")
            self.assertEqual(resumed.reason, "resumed")
            self.assertEqual(resumed.next_tag, "v1.0.1")
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": second_sha},
            )
            self.assertEqual(
                github.releases,
                {"v1.0.1": {"tag": "v1.0.1", "target_sha": second_sha, "title": "v1.0.1"}},
            )

    def test_resumes_after_github_release_creation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: partial release note (#232)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(pr_labels={232: ["release:patch"]}, fail_on="create_release")
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="fix: partial release note (#232)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": second_sha},
            )
            self.assertEqual(github.releases, {})

            github.fail_on = None
            resumed = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: partial release note (#232)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(resumed.status, "published")
            self.assertEqual(resumed.reason, "resumed")
            self.assertEqual(resumed.next_tag, "v1.0.1")
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": second_sha},
            )
            self.assertEqual(
                github.releases,
                {"v1.0.1": {"tag": "v1.0.1", "target_sha": second_sha, "title": "v1.0.1"}},
            )

    def test_stale_retry_after_newer_release_never_moves_floating_tag_backward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "newer.txt").write_text("newer\n", encoding="utf-8")
            _git(work, "add", "newer.txt")
            _git(work, "commit", "-m", "fix: newer release (#234)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={233: ["release:patch"], 234: ["release:patch"]},
                existing_releases={"v1.0.0": {"tag": "v1.0.0", "target_sha": first_sha, "title": "v1.0.0"}},
            )
            run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=second_sha,
                    head_commit_message="fix: newer release (#234)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(_remote_tags(bare)["v1"], second_sha)

            # Stale retry of already-completed first_sha is a no-op and keeps v1 at second_sha.
            stale_noop = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=first_sha,
                    head_commit_message="fix: older release (#233)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(stale_noop.status, "noop")
            self.assertEqual(stale_noop.next_tag, "v1.0.0")
            self.assertEqual(_remote_tags(bare)["v1"], second_sha)

            # Stale retry of first_sha with missing GitHub release repairs release note without moving v1 back.
            del github.releases["v1.0.0"]
            stale_repaired = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=first_sha,
                    head_commit_message="fix: older release (#233)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(stale_repaired.status, "published")
            self.assertEqual(stale_repaired.reason, "resumed")
            self.assertEqual(stale_repaired.next_tag, "v1.0.0")
            self.assertEqual(github.releases["v1.0.0"]["target_sha"], first_sha)
            self.assertEqual(_remote_tags(bare)["v1"], second_sha)

    def test_stale_pre_tag_retry_fails_visibly_when_descendant_already_tagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            (work / "newer.txt").write_text("newer\n", encoding="utf-8")
            _git(work, "add", "newer.txt")
            _git(work, "commit", "-m", "fix: newer already tagged (#235)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")
            _git(work, "tag", "v1.0.1", second_sha)
            _git(work, "tag", "v1", second_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.1", "refs/tags/v1")

            github = FakeGitHub(pr_labels={234: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=first_sha,
                        head_commit_message="fix: stale untagged commit (#234)",
                        workdir=work,
                    ),
                    github,
                )
            self.assertEqual(_remote_tags(bare), {"v1.0.1": second_sha, "v1": second_sha})

    def test_rejects_conflicting_github_release_target_and_duplicate_exact_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1. Conflicting existing GitHub release target SHA fails visibly.
            sub1 = root / "conflict_release"
            sub1.mkdir()
            bare, work, first_sha = _create_repo_with_bare_remote(sub1)
            (work / "fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: mismatched release target (#236)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={236: ["release:patch"]},
                existing_releases={
                    "v0.0.1": {"tag": "v0.0.1", "targetCommitish": first_sha, "name": "v0.0.1"}
                },
            )
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=second_sha,
                        head_commit_message="fix: mismatched release target (#236)",
                        workdir=work,
                    ),
                    github,
                )

            # 2. Multiple exact semver tags on the same commit fails visibly.
            sub2 = root / "duplicate_tags"
            sub2.mkdir()
            bare2, work2, sha2 = _create_repo_with_bare_remote(sub2)
            _git(work2, "tag", "v1.0.0", sha2)
            _git(work2, "tag", "v1.0.1", sha2)
            _git(work2, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1.0.1")

            github2 = FakeGitHub(pr_labels={237: ["release:patch"]})
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=sha2,
                        head_commit_message="fix: duplicate tags (#237)",
                        workdir=work2,
                    ),
                    github2,
                )

    def test_reconciles_rapid_labeled_burst_in_commit_order_without_coalescing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            commits = {}
            for name, msg in (
                ("c1", "fix: first patch (#301)"),
                ("c2", "feat: second minor (#302)"),
                ("c3", "feat!: breaking major (#303)"),
                ("c4", "fix: follow-up on v2 (#304)"),
            ):
                (work / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
                _git(work, "add", f"{name}.txt")
                _git(work, "commit", "-m", msg)
                commits[name] = _git(work, "rev-parse", "HEAD")
            _git(work, "push", "origin", "main")

            github = FakeGitHub(
                pr_labels={
                    301: ["release:patch"],
                    302: ["release:minor"],
                    303: ["release:major"],
                    304: ["release:patch"],
                }
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=commits["c4"],
                    head_commit_message="fix: follow-up on v2 (#304)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v2.0.1")
            self.assertEqual(
                _remote_tags(bare),
                {
                    "v1.0.0": first_sha,
                    "v1.0.1": commits["c1"],
                    "v1.1.0": commits["c2"],
                    "v1": commits["c2"],
                    "v2.0.0": commits["c3"],
                    "v2.0.1": commits["c4"],
                    "v2": commits["c4"],
                },
            )
            self.assertEqual(
                set(github.releases.keys()),
                {"v1.0.1", "v1.1.0", "v2.0.0", "v2.0.1"},
            )
            self.assertEqual(github.releases["v1.0.1"]["target_sha"], commits["c1"])
            self.assertEqual(github.releases["v1.1.0"]["target_sha"], commits["c2"])
            self.assertEqual(github.releases["v2.0.0"]["target_sha"], commits["c3"])
            self.assertEqual(github.releases["v2.0.1"]["target_sha"], commits["c4"])

            # Delayed out-of-order run arriving later for c2 is a safe no-op and keeps v1 at c2.
            delayed_outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=commits["c2"],
                    head_commit_message="feat: second minor (#302)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(delayed_outcome.status, "noop")
            self.assertEqual(delayed_outcome.reason, "already_released")
            self.assertEqual(_remote_tags(bare)["v1"], commits["c2"])
            self.assertEqual(_remote_tags(bare)["v2"], commits["c4"])

    def test_unlabeled_merge_replaces_cancelled_labeled_run_and_still_reconciles_prior_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, first_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "c1.txt").write_text("c1\n", encoding="utf-8")
            _git(work, "add", "c1.txt")
            _git(work, "commit", "-m", "fix: labeled before replacement (#310)")
            c1_sha = _git(work, "rev-parse", "HEAD")

            (work / "c2.txt").write_text("c2\n", encoding="utf-8")
            _git(work, "add", "c2.txt")
            _git(work, "commit", "-m", "docs: unlabeled replacement (#311)")
            _git(work, "push", "origin", "main")
            c2_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={
                    310: ["release:patch"],
                    311: ["documentation"],
                    312: ["chore"],
                }
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=c2_sha,
                    head_commit_message="docs: unlabeled replacement (#311)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v1.0.1")
            self.assertEqual(outcome.target_sha, c1_sha)
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": c1_sha, "v1": c1_sha},
            )
            self.assertEqual(
                github.releases,
                {"v1.0.1": {"tag": "v1.0.1", "target_sha": c1_sha, "title": "v1.0.1"}},
            )

            (work / "c3.txt").write_text("c3\n", encoding="utf-8")
            _git(work, "add", "c3.txt")
            _git(work, "commit", "-m", "chore: another unlabeled commit (#312)")
            _git(work, "push", "origin", "main")
            c3_sha = _git(work, "rev-parse", "HEAD")

            later_outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=c3_sha,
                    head_commit_message="chore: another unlabeled commit (#312)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(later_outcome.status, "skipped")
            self.assertEqual(later_outcome.reason, "no_release_label")
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": c1_sha, "v1": c1_sha},
            )

    def test_reconciles_maintenance_branch_burst_isolated_from_main_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, v1_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.10.0", v1_sha)
            _git(work, "tag", "v1", v1_sha)
            _git(work, "push", "origin", "refs/tags/v1.10.0", "refs/tags/v1")

            _git(work, "checkout", "-b", "release/v1", v1_sha)
            (work / "b1.txt").write_text("b1\n", encoding="utf-8")
            _git(work, "add", "b1.txt")
            _git(work, "commit", "-m", "fix: backport patch (#320)")
            b1_sha = _git(work, "rev-parse", "HEAD")

            (work / "b2.txt").write_text("b2\n", encoding="utf-8")
            _git(work, "add", "b2.txt")
            _git(work, "commit", "-m", "feat: backport minor without suffix")
            _git(work, "push", "-u", "origin", "release/v1")
            b2_sha = _git(work, "rev-parse", "HEAD")

            _git(work, "checkout", "main")
            (work / "v2.txt").write_text("v2\n", encoding="utf-8")
            _git(work, "add", "v2.txt")
            _git(work, "commit", "-m", "feat!: main v2")
            _git(work, "push", "origin", "main")
            v2_sha = _git(work, "rev-parse", "HEAD")
            _git(work, "tag", "v2.0.0", v2_sha)
            _git(work, "tag", "v2", v2_sha)
            _git(work, "push", "origin", "refs/tags/v2.0.0", "refs/tags/v2")

            _git(work, "checkout", "release/v1")
            github = FakeGitHub(
                commit_prs={
                    b2_sha: [
                        {"number": 999, "merged_at": "2026-11-15T01:00:00Z", "base": {"ref": "main"}},
                        {"number": 321, "merged_at": "2026-11-15T01:05:00Z", "base": {"ref": "release/v1"}},
                    ]
                },
                pr_labels={
                    320: ["release:patch"],
                    321: ["release:minor"],
                },
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=b2_sha,
                    head_commit_message="feat: backport minor without suffix",
                    base_ref="release/v1",
                    is_branch_creation=False,
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v1.11.0")
            self.assertEqual(
                _remote_tags(bare),
                {
                    "v1.10.0": v1_sha,
                    "v1.10.1": b1_sha,
                    "v1.11.0": b2_sha,
                    "v1": b2_sha,
                    "v2.0.0": v2_sha,
                    "v2": v2_sha,
                },
            )
            self.assertEqual(set(github.releases.keys()), {"v1.10.1", "v1.11.0"})

    def test_failed_or_blocked_earlier_intent_blocks_newer_commit_until_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Case A: C1 fails pre-tag via API error -> C2 must not overtake C1; retry publishes both.
            sub1 = root / "pre_tag_block"
            sub1.mkdir()
            bare1, work1, base1 = _create_repo_with_bare_remote(sub1)
            _git(work1, "tag", "v1.0.0", base1)
            _git(work1, "tag", "v1", base1)
            _git(work1, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work1 / "c1.txt").write_text("c1\n", encoding="utf-8")
            _git(work1, "add", "c1.txt")
            _git(work1, "commit", "-m", "fix: blocked commit")
            c1_sha = _git(work1, "rev-parse", "HEAD")

            (work1 / "c2.txt").write_text("c2\n", encoding="utf-8")
            _git(work1, "add", "c2.txt")
            _git(work1, "commit", "-m", "feat: later minor (#331)")
            _git(work1, "push", "origin", "main")
            c2_sha = _git(work1, "rev-parse", "HEAD")

            github1 = FakeGitHub(
                commit_prs={c1_sha: [{"number": 330, "merged_at": "2026-11-15T01:00:00Z", "base": {"ref": "main"}}]},
                pr_labels={330: ["release:patch"], 331: ["release:minor"]},
                fail_on="find_merged_pr_for_commit",
            )
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=c2_sha,
                        head_commit_message="feat: later minor (#331)",
                        workdir=work1,
                    ),
                    github1,
                )
            self.assertEqual(_remote_tags(bare1), {"v1.0.0": base1, "v1": base1})
            self.assertEqual(github1.releases, {})

            github1.fail_on = None
            outcome1 = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=c2_sha,
                    head_commit_message="feat: later minor (#331)",
                    workdir=work1,
                ),
                github1,
            )
            self.assertEqual(outcome1.next_tag, "v1.1.0")
            self.assertEqual(
                _remote_tags(bare1),
                {
                    "v1.0.0": base1,
                    "v1.0.1": c1_sha,
                    "v1.1.0": c2_sha,
                    "v1": c2_sha,
                },
            )
            self.assertEqual(set(github1.releases.keys()), {"v1.0.1", "v1.1.0"})

            # Case B: C1 pushed v1.0.1 and moved v1 -> C1, then failed release creation -> C2 must not publish v1.1.0 until C1's release is created.
            sub2 = root / "partial_block"
            sub2.mkdir()
            bare2, work2, base2 = _create_repo_with_bare_remote(sub2)
            _git(work2, "tag", "v1.0.0", base2)
            _git(work2, "tag", "v1", base2)
            _git(work2, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work2 / "p1.txt").write_text("p1\n", encoding="utf-8")
            _git(work2, "add", "p1.txt")
            _git(work2, "commit", "-m", "fix: partial c1 (#332)")
            p1_sha = _git(work2, "rev-parse", "HEAD")

            (work2 / "p2.txt").write_text("p2\n", encoding="utf-8")
            _git(work2, "add", "p2.txt")
            _git(work2, "commit", "-m", "feat: queued c2 (#333)")
            _git(work2, "push", "origin", "main")
            p2_sha = _git(work2, "rev-parse", "HEAD")

            github2 = FakeGitHub(
                pr_labels={332: ["release:patch"], 333: ["release:minor"]},
                fail_on="create_release",
            )
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=p2_sha,
                        head_commit_message="feat: queued c2 (#333)",
                        workdir=work2,
                    ),
                    github2,
                )
            self.assertEqual(
                _remote_tags(bare2),
                {"v1.0.0": base2, "v1.0.1": p1_sha, "v1": p1_sha},
            )
            self.assertEqual(github2.releases, {})

            github2.fail_on = None
            outcome2 = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=p2_sha,
                    head_commit_message="feat: queued c2 (#333)",
                    workdir=work2,
                ),
                github2,
            )
            self.assertEqual(outcome2.next_tag, "v1.1.0")
            self.assertEqual(
                _remote_tags(bare2),
                {
                    "v1.0.0": base2,
                    "v1.0.1": p1_sha,
                    "v1.1.0": p2_sha,
                    "v1": p2_sha,
                },
            )
            self.assertEqual(set(github2.releases.keys()), {"v1.0.1", "v1.1.0"})


_FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
fail_cmd = state.get("fail_command")

REQUIRED_CHECKS = [
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
]


def _default_ci(sha):
    wf_ids = {}
    wf_runs = []
    ck_runs = []
    next_id = 2000
    for idx, (wf_path, job_name) in enumerate(REQUIRED_CHECKS, start=1):
        if wf_path not in wf_ids:
            wf_ids[wf_path] = next_id
            wf_runs.append({
                "id": next_id,
                "path": wf_path,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
                "event": "push",
            })
            next_id += 1
        rid = wf_ids[wf_path]
        ck_runs.append({
            "id": 6000 + idx,
            "name": job_name,
            "head_sha": sha,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
            "details_url": f"https://github.com/nandohdc/gh-pipelines/actions/runs/{rid}/job/{8000 + idx}",
        })
    return wf_runs, ck_runs


def _git_commits():
    res = subprocess.run(["git", "rev-list", "HEAD"], capture_output=True, text=True)
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


if args[:1] == ["api"]:
    endpoint = args[1]
    if fail_cmd == "api":
        print("simulated gh api failure", file=sys.stderr)
        sys.exit(1)
    if endpoint.endswith("/pulls"):
        sha = endpoint.split("/commits/", 1)[1].split("/", 1)[0]
        print(json.dumps(state.get("commit_prs", {}).get(sha, [])))
        sys.exit(0)
    if endpoint.endswith("/labels"):
        pr_num = endpoint.split("/issues/", 1)[1].split("/", 1)[0]
        labels = [{"name": name} for name in state.get("pr_labels", {}).get(pr_num, [])]
        print(json.dumps(labels))
        sys.exit(0)
    if "/check-runs" in endpoint:
        sha = endpoint.split("/commits/", 1)[1].split("/", 1)[0]
        custom = state.get("check_runs", {})
        if sha in custom:
            print(json.dumps({"check_runs": custom[sha]}))
        else:
            _, ck_runs = _default_ci(sha)
            print(json.dumps({"check_runs": ck_runs}))
        sys.exit(0)
    if "/actions/runs?" in endpoint and "head_sha=" in endpoint:
        sha = endpoint.split("head_sha=", 1)[1].split("&", 1)[0]
        custom = state.get("workflow_runs", {})
        if sha in custom:
            print(json.dumps({"workflow_runs": custom[sha]}))
        else:
            wf_runs, _ = _default_ci(sha)
            print(json.dumps({"workflow_runs": wf_runs}))
        sys.exit(0)
    if "/actions/workflows/candidate-smoke.yml/runs" in endpoint:
        if "smoke_runs" in state:
            print(json.dumps({"workflow_runs": state["smoke_runs"]}))
        else:
            commits = _git_commits()
            runs = [
                {
                    "id": 99000 + idx,
                    "status": "completed",
                    "conclusion": "success",
                    "event": "workflow_dispatch",
                    "html_url": f"https://github.com/nandohdc/gh-pipelines-hosted-acceptance/actions/runs/{99000 + idx}",
                }
                for idx, _sha in enumerate(commits)
            ]
            print(json.dumps({"workflow_runs": runs}))
        sys.exit(0)
    if "/actions/runs/" in endpoint and "/jobs" in endpoint:
        run_id = int(endpoint.split("/actions/runs/", 1)[1].split("/", 1)[0])
        custom_jobs = state.get("smoke_jobs", {})
        if str(run_id) in custom_jobs:
            print(json.dumps({"jobs": custom_jobs[str(run_id)]}))
        else:
            commits = _git_commits()
            idx = run_id - 99000
            sha = commits[idx] if 0 <= idx < len(commits) else ""
            jobs = [
                {"name": "smoke-ci-go / lint", "status": "completed", "conclusion": "success"},
                {"name": "smoke-ci-node / lint", "status": "completed", "conclusion": "success"},
                {"name": "smoke-ci-python / lint", "status": "completed", "conclusion": "success"},
                {"name": "smoke-ci-terraform / fmt", "status": "completed", "conclusion": "success"},
                {"name": "smoke-direct-actions", "status": "completed", "conclusion": "success"},
                {"name": f"candidate-smoke-result / {sha}", "status": "completed", "conclusion": "success"},
            ]
            print(json.dumps({"jobs": jobs}))
        sys.exit(0)
    print(f"unexpected gh api endpoint: {endpoint}", file=sys.stderr)
    sys.exit(2)

if args[:2] == ["release", "view"]:
    tag = args[2]
    if fail_cmd == "release_view":
        print("HTTP 500: internal server error", file=sys.stderr)
        sys.exit(1)
    if tag in state.get("releases", {}):
        print(json.dumps(state["releases"][tag]))
        sys.exit(0)
    print(f"release not found: {tag}", file=sys.stderr)
    sys.exit(1)

if args[:2] == ["release", "create"]:
    if fail_cmd == "release_create":
        print("simulated gh release create failure", file=sys.stderr)
        sys.exit(1)
    tag = args[2]
    target = args[args.index("--target") + 1]
    title = args[args.index("--title") + 1]
    state.setdefault("releases", {})[tag] = {"tag": tag, "target_sha": target, "title": title}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

print(f"unexpected gh args: {args}", file=sys.stderr)
sys.exit(2)
"""


def _write_fake_gh(root: Path, state: dict) -> Tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_path = bin_dir / "gh"
    gh_path.write_text(_FAKE_GH_SCRIPT, encoding="utf-8")
    gh_path.chmod(0o755)
    state_path = root / "gh_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return bin_dir, state_path


def _run_release_cli(
    work: Path,
    bin_dir: Path,
    state_path: Path,
    *,
    sha: str,
    message: str,
    base_ref: Optional[str] = None,
    event_created: Optional[str] = None,
) -> subprocess.CompletedProcess:
    script = Path(__file__).resolve()
    release_script = script.parent / "release.py"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_GH_STATE"] = str(state_path)
    env["GH_TOKEN"] = "test-token"
    env["CANDIDATE_SMOKE_TOKEN"] = "test-smoke-token"
    env["GITHUB_REPOSITORY"] = "nandohdc/gh-pipelines"
    env["GITHUB_SHA"] = sha
    env["HEAD_COMMIT_MESSAGE"] = message
    env["GITHUB_REF_NAME"] = base_ref if base_ref is not None else "main"
    env.pop("GITHUB_EVENT_BEFORE", None)
    if event_created is not None:
        env["GITHUB_EVENT_CREATED"] = event_created
    else:
        env.pop("GITHUB_EVENT_CREATED", None)
    return subprocess.run(
        [sys.executable, str(release_script), "run"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


class ReleaseCliProcessTest(unittest.TestCase):
    def test_cli_publishes_via_api_fallback_and_fake_gh_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, sha = _create_repo_with_bare_remote(root)
            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "commit_prs": {
                        sha: [
                            {"number": 55, "merged_at": "2026-11-14T22:00:00Z", "base": {"ref": "main"}}
                        ]
                    },
                    "pr_labels": {"55": ["release:patch"]},
                    "releases": {},
                },
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=sha,
                message="fix: commit without pr suffix",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v0": sha})
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                recorded["releases"],
                {"v0.0.1": {"tag": "v0.0.1", "target_sha": sha, "title": "v0.0.1"}},
            )

    def test_cli_exits_nonzero_with_error_annotation_on_non_404_gh_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, sha = _create_repo_with_bare_remote(root)
            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "pr_labels": {"56": ["release:patch"]},
                    "releases": {},
                    "fail_command": "release_view",
                },
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=sha,
                message="fix: trigger non-404 release view failure (#56)",
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("::error::", proc.stderr)
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v0": sha})

    def test_cli_exits_zero_without_writes_when_pr_has_no_release_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, sha = _create_repo_with_bare_remote(root)
            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "pr_labels": {"57": ["documentation"]},
                    "releases": {},
                },
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=sha,
                message="docs: clarify release tests (#57)",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(_try_remote_tags(bare), {})
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(recorded["releases"], {})

    def test_cli_publishes_maintenance_release_via_api_fallback_on_release_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, v1_sha = _create_repo_with_bare_remote(root)
            _git(work, "tag", "v1.10.0", v1_sha)
            _git(work, "tag", "v1", v1_sha)
            _git(work, "push", "origin", "refs/tags/v1.10.0", "refs/tags/v1")

            _git(work, "checkout", "-b", "release/v1", v1_sha)
            (work / "fix.txt").write_text("hotfix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: backport patch")
            _git(work, "push", "-u", "origin", "release/v1")
            v1_fix_sha = _git(work, "rev-parse", "HEAD")

            _git(work, "checkout", "main")
            (work / "v2.txt").write_text("v2\n", encoding="utf-8")
            _git(work, "add", "v2.txt")
            _git(work, "commit", "-m", "feat!: breaking v2")
            _git(work, "push", "origin", "main")
            v2_sha = _git(work, "rev-parse", "HEAD")
            _git(work, "tag", "v2.0.0", v2_sha)
            _git(work, "tag", "v2", v2_sha)
            _git(work, "push", "origin", "refs/tags/v2.0.0", "refs/tags/v2")

            _git(work, "checkout", "release/v1")
            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "commit_prs": {
                        v1_fix_sha: [
                            {"number": 71, "merged_at": "2026-11-15T00:10:00Z", "base": {"ref": "main"}},
                            {"number": 72, "merged_at": "2026-11-15T00:12:00Z", "base": {"ref": "release/v1"}},
                        ]
                    },
                    "pr_labels": {"72": ["release:patch"]},
                    "releases": {},
                },
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=v1_fix_sha,
                message="fix: backport patch",
                base_ref="release/v1",
                event_created="false",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            tags = _remote_tags(bare)
            self.assertEqual(tags["v1.10.1"], v1_fix_sha)
            self.assertEqual(tags["v1"], v1_fix_sha)
            self.assertEqual(tags["v2"], v2_sha)
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                recorded["releases"],
                {"v1.10.1": {"tag": "v1.10.1", "target_sha": v1_fix_sha, "title": "v1.10.1"}},
            )

    def test_cli_skips_branch_creation_push_on_maintenance_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, sha = _create_repo_with_bare_remote(root)
            _git(work, "tag", "v1.10.0", sha)
            _git(work, "tag", "v1", sha)
            _git(work, "push", "origin", "refs/tags/v1.10.0", "refs/tags/v1")
            bin_dir, state_path = _write_fake_gh(
                root,
                {"pr_labels": {"156": ["release:minor"]}, "releases": {}},
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=sha,
                message="feat: earlier release (#156)",
                base_ref="release/v1",
                event_created="true",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(_remote_tags(bare), {"v1.10.0": sha, "v1": sha})
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(recorded["releases"], {})

    def test_cli_resumes_partial_release_and_logs_recovery_summary_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, first_sha = _create_repo_with_bare_remote(root)
            _git(work, "tag", "v1.0.0", first_sha)
            _git(work, "tag", "v1", first_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "fix.txt").write_text("fix\n", encoding="utf-8")
            _git(work, "add", "fix.txt")
            _git(work, "commit", "-m", "fix: cli recovery (#240)")
            _git(work, "push", "origin", "main")
            second_sha = _git(work, "rev-parse", "HEAD")

            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "pr_labels": {"240": ["release:patch"]},
                    "releases": {},
                    "fail_command": "release_create",
                },
            )
            first_proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=second_sha,
                message="fix: cli recovery (#240)",
            )
            self.assertEqual(first_proc.returncode, 1)
            self.assertNotIn("test-token", first_proc.stdout + first_proc.stderr)

            # Retry after clearing gh release create failure and changing label.
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["fail_command"] = None
            state["pr_labels"]["240"] = ["release:minor"]
            state_path.write_text(json.dumps(state), encoding="utf-8")

            second_proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=second_sha,
                message="fix: cli recovery (#240)",
            )
            self.assertEqual(second_proc.returncode, 0, msg=second_proc.stderr)
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": first_sha, "v1.0.1": second_sha, "v1": second_sha},
            )
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                recorded["releases"],
                {"v1.0.1": {"tag": "v1.0.1", "target_sha": second_sha, "title": "v1.0.1"}},
            )
            combined = second_proc.stdout + second_proc.stderr
            self.assertIn(second_sha, combined)
            self.assertIn("v1.0.1", combined)
            self.assertIn("resumed", combined)
            self.assertNotIn("test-token", combined)

    def test_cli_reconciles_pending_burst_and_logs_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, work, base_sha = _create_repo_with_bare_remote(root)
            _git(work, "tag", "v1.0.0", base_sha)
            _git(work, "tag", "v1", base_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "c1.txt").write_text("c1\n", encoding="utf-8")
            _git(work, "add", "c1.txt")
            _git(work, "commit", "-m", "fix: api fallback burst commit")
            c1_sha = _git(work, "rev-parse", "HEAD")

            (work / "c2.txt").write_text("c2\n", encoding="utf-8")
            _git(work, "add", "c2.txt")
            _git(work, "commit", "-m", "feat: minor burst commit (#341)")
            c2_sha = _git(work, "rev-parse", "HEAD")

            (work / "c3.txt").write_text("c3\n", encoding="utf-8")
            _git(work, "add", "c3.txt")
            _git(work, "commit", "-m", "docs: trailing unlabeled commit (#342)")
            _git(work, "push", "origin", "main")
            c3_sha = _git(work, "rev-parse", "HEAD")

            bin_dir, state_path = _write_fake_gh(
                root,
                {
                    "commit_prs": {
                        c1_sha: [{"number": 340, "merged_at": "2026-11-15T01:00:00Z", "base": {"ref": "main"}}]
                    },
                    "pr_labels": {
                        "340": ["release:patch"],
                        "341": ["release:minor"],
                        "342": ["documentation"],
                    },
                    "releases": {},
                },
            )
            proc = _run_release_cli(
                work,
                bin_dir,
                state_path,
                sha=c3_sha,
                message="docs: trailing unlabeled commit (#342)",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(
                _remote_tags(bare),
                {
                    "v1.0.0": base_sha,
                    "v1.0.1": c1_sha,
                    "v1.1.0": c2_sha,
                    "v1": c2_sha,
                },
            )
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                recorded["releases"],
                {
                    "v1.0.1": {"tag": "v1.0.1", "target_sha": c1_sha, "title": "v1.0.1"},
                    "v1.1.0": {"tag": "v1.1.0", "target_sha": c2_sha, "title": "v1.1.0"},
                },
            )
            combined = proc.stdout + proc.stderr
            self.assertIn("v1.0.1", combined)
            self.assertIn("v1.1.0", combined)
            self.assertNotIn("test-token", combined)
            self.assertNotIn("test-smoke-token", combined)

    def test_promotion_gate_blocks_when_required_check_fails_or_cancels_or_times_out(self) -> None:
        for bad_conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=bad_conclusion), tempfile.TemporaryDirectory() as tmp:
                bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
                wf_runs, ck_runs = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
                ck_runs[0]["conclusion"] = bad_conclusion
                github = FakeGitHub(
                    pr_labels={401: ["release:patch"]},
                    commit_workflow_runs={sha: wf_runs},
                    commit_check_runs={sha: ck_runs},
                )
                with self.assertRaises(ReleaseError) as ctx:
                    run_release(
                        ReleaseConfig(
                            repository="nandohdc/gh-pipelines",
                            sha=sha,
                            head_commit_message="fix: failing check (#401)",
                            workdir=work,
                            ci_gate_timeout_seconds=0,
                            ci_gate_poll_seconds=0,
                        ),
                        github,
                    )
                self.assertIn(bad_conclusion, str(ctx.exception))
                self.assertEqual(_try_remote_tags(bare), {})
                self.assertEqual(github.releases, {})
                self.assertEqual(github.smoke_calls, [])

    def test_promotion_gate_blocks_when_check_is_missing_or_remains_queued_past_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            wf_runs, ck_runs = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
            ck_runs[1]["status"] = "queued"
            ck_runs[1]["conclusion"] = None
            ck_runs_missing = ck_runs[:-1]
            for label, runs in (("queued", ck_runs), ("missing", ck_runs_missing)):
                with self.subTest(state=label):
                    github = FakeGitHub(
                        pr_labels={402: ["release:patch"]},
                        commit_workflow_runs={sha: wf_runs},
                        commit_check_runs={sha: runs},
                    )
                    with self.assertRaises(ReleaseError) as ctx:
                        run_release(
                            ReleaseConfig(
                                repository="nandohdc/gh-pipelines",
                                sha=sha,
                                head_commit_message="fix: pending check (#402)",
                                workdir=work,
                                ci_gate_timeout_seconds=0,
                                ci_gate_poll_seconds=0,
                            ),
                            github,
                        )
                    self.assertIn("incomplete or missing", str(ctx.exception))
                    self.assertEqual(_try_remote_tags(bare), {})

    def test_promotion_gate_blocks_on_wrong_sha_or_untrusted_app_or_wrong_workflow_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            other_sha = "b" * 40
            for scenario in ("wrong_sha", "untrusted_app", "wrong_workflow_path"):
                with self.subTest(scenario=scenario):
                    wf_runs, ck_runs = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
                    if scenario == "wrong_sha":
                        ck_runs[0]["head_sha"] = other_sha
                    elif scenario == "untrusted_app":
                        ck_runs[0]["app"] = {"slug": "third-party-spoof-app"}
                    elif scenario == "wrong_workflow_path":
                        wf_runs[0]["path"] = ".github/workflows/spoof-lint.yml"
                    github = FakeGitHub(
                        pr_labels={403: ["release:patch"]},
                        commit_workflow_runs={sha: wf_runs},
                        commit_check_runs={sha: ck_runs},
                    )
                    with self.assertRaises(ReleaseError):
                        run_release(
                            ReleaseConfig(
                                repository="nandohdc/gh-pipelines",
                                sha=sha,
                                head_commit_message="fix: spoofed check (#403)",
                                workdir=work,
                                ci_gate_timeout_seconds=0,
                                ci_gate_poll_seconds=0,
                            ),
                            github,
                        )
                    self.assertEqual(_try_remote_tags(bare), {})

    def test_promotion_gate_ignores_non_push_events_and_non_required_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))

            # A passing schedule/pull_request run must not satisfy missing push CI evidence.
            for bad_event in ("schedule", "pull_request"):
                with self.subTest(disallowed_event=bad_event):
                    wf_runs, ck_runs = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
                    for wf in wf_runs:
                        if wf["path"] == ".github/workflows/security-self-scan.yml":
                            wf["event"] = bad_event
                    github = FakeGitHub(
                        pr_labels={412: ["release:patch"]},
                        commit_workflow_runs={sha: wf_runs},
                        commit_check_runs={sha: ck_runs},
                    )
                    with self.assertRaises(ReleaseError) as ctx:
                        run_release(
                            ReleaseConfig(
                                repository="nandohdc/gh-pipelines",
                                sha=sha,
                                head_commit_message="fix: schedule-only self-scan (#412)",
                                workdir=work,
                                ci_gate_timeout_seconds=0,
                                ci_gate_poll_seconds=0,
                            ),
                            github,
                        )
                    self.assertIn("incomplete or missing", str(ctx.exception))
                    self.assertEqual(_try_remote_tags(bare), {})

            # Later cancelled/failed cron runs on head_sha (integration-smoke, scheduled self-scan)
            # must NOT poison passing push runs for the candidate commit.
            extra_wf = [
                {
                    "id": 98001,
                    "path": ".github/workflows/integration-smoke.yml",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "cancelled",
                    "event": "schedule",
                },
                {
                    "id": 98002,
                    "path": ".github/workflows/security-self-scan.yml",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "cancelled",
                    "event": "schedule",
                },
            ]
            extra_checks = [
                {
                    "id": 98101,
                    "name": "parse ${{ matrix.id }}",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "cancelled",
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/nandohdc/gh-pipelines/actions/runs/98001/job/1",
                },
                {
                    "id": 98102,
                    "name": "zizmor",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "cancelled",
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/nandohdc/gh-pipelines/actions/runs/98002/job/2",
                },
            ]
            wf_ok, ck_ok = _make_passing_ci_evidence(
                "nandohdc/gh-pipelines",
                sha,
                extra_workflow_runs=extra_wf,
                extra_check_runs=extra_checks,
            )
            github_ok = FakeGitHub(
                pr_labels={413: ["release:patch"]},
                commit_workflow_runs={sha: wf_ok},
                commit_check_runs={sha: ck_ok},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="fix: ignore schedule noise (#413)",
                    workdir=work,
                    ci_gate_timeout_seconds=0,
                    ci_gate_poll_seconds=0,
                ),
                github_ok,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v0.0.1")

    def test_promotion_gate_excludes_own_unfinished_release_check_without_self_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            release_run = {
                "id": 9999,
                "path": ".github/workflows/release.yml",
                "head_sha": sha,
                "status": "in_progress",
                "conclusion": None,
                "event": "push",
            }
            release_check = {
                "id": 99991,
                "name": "release",
                "head_sha": sha,
                "status": "in_progress",
                "conclusion": None,
                "app": {"slug": "github-actions"},
                "details_url": f"https://github.com/nandohdc/gh-pipelines/actions/runs/9999/job/1",
            }
            wf_runs, ck_runs = _make_passing_ci_evidence(
                "nandohdc/gh-pipelines",
                sha,
                extra_workflow_runs=[release_run],
                extra_check_runs=[release_check],
            )
            github = FakeGitHub(
                pr_labels={404: ["release:patch"]},
                commit_workflow_runs={sha: wf_runs},
                commit_check_runs={sha: ck_runs},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="fix: self deadlock guard (#404)",
                    workdir=work,
                    ci_gate_timeout_seconds=1,
                    ci_gate_poll_seconds=0,
                ),
                github,
            )
            self.assertEqual(outcome.next_tag, "v0.0.1")
            self.assertEqual(_remote_tags(bare)["v0.0.1"], sha)

    def test_promotion_gate_polls_until_pending_checks_succeed_then_promotes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            wf_pending, ck_pending = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
            ck_pending[0]["status"] = "in_progress"
            ck_pending[0]["conclusion"] = None
            wf_ready, ck_ready = _make_passing_ci_evidence("nandohdc/gh-pipelines", sha)
            github = FakeGitHub(
                pr_labels={405: ["release:patch"]},
                check_runs_sequence={sha: [(wf_pending, ck_pending), (wf_ready, ck_ready)]},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="fix: polling promotion (#405)",
                    workdir=work,
                    ci_gate_timeout_seconds=5,
                    ci_gate_poll_seconds=0,
                ),
                github,
            )
            self.assertEqual(outcome.next_tag, "v0.0.1")
            self.assertEqual(_remote_tags(bare)["v0.0.1"], sha)
            self.assertEqual(github.smoke_calls, [sha])

    def test_promotion_gate_blocks_when_candidate_smoke_fails_or_is_unprovisioned_or_wrong_sha(
        self,
    ) -> None:
        other_sha = "c" * 40
        for case_name, smoke_result in (
            ("failed_outcome", {"status": "failed", "candidate_sha": None, "consumer_repo": "nandohdc/gh-pipelines-hosted-acceptance"}),
            ("wrong_sha", {"status": "passed", "candidate_sha": other_sha, "consumer_repo": "nandohdc/gh-pipelines-hosted-acceptance", "run_id": 55}),
            ("unprovisioned", ReleaseError("unprovisioned: Missing CANDIDATE_SMOKE_TOKEN")),
        ):
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as tmp:
                bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
                github = FakeGitHub(
                    pr_labels={406: ["release:patch"]},
                    candidate_smoke_results={sha: smoke_result},
                )
                with self.assertRaises(ReleaseError):
                    run_release(
                        ReleaseConfig(
                            repository="nandohdc/gh-pipelines",
                            sha=sha,
                            head_commit_message="fix: smoke gate block (#406)",
                            workdir=work,
                        ),
                        github,
                    )
                self.assertEqual(_try_remote_tags(bare), {})

    def test_blocked_earlier_commit_retains_intent_and_halts_later_commit_until_evidence_passes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, base_sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v1.0.0", base_sha)
            _git(work, "tag", "v1", base_sha)
            _git(work, "push", "origin", "refs/tags/v1.0.0", "refs/tags/v1")

            (work / "a.txt").write_text("a\n", encoding="utf-8")
            _git(work, "add", "a.txt")
            _git(work, "commit", "-m", "fix: blocked commit a (#410)")
            a_sha = _git(work, "rev-parse", "HEAD")

            (work / "b.txt").write_text("b\n", encoding="utf-8")
            _git(work, "add", "b.txt")
            _git(work, "commit", "-m", "feat: commit b (#411)")
            _git(work, "push", "origin", "main")
            b_sha = _git(work, "rev-parse", "HEAD")

            github = FakeGitHub(
                pr_labels={410: ["release:patch"], 411: ["release:minor"]},
                candidate_smoke_results={
                    a_sha: ReleaseError("simulated consumer outage"),
                },
            )
            with self.assertRaises(ReleaseError):
                run_release(
                    ReleaseConfig(
                        repository="nandohdc/gh-pipelines",
                        sha=b_sha,
                        head_commit_message="feat: commit b (#411)",
                        workdir=work,
                    ),
                    github,
                )
            # Commit b must NOT overtake blocked commit a.
            self.assertEqual(_remote_tags(bare), {"v1.0.0": base_sha, "v1": base_sha})

            # Once commit a's consumer evidence succeeds, retrying head reconciles both in order.
            github.candidate_smoke_results.pop(a_sha)
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=b_sha,
                    head_commit_message="feat: commit b (#411)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.next_tag, "v1.1.0")
            self.assertEqual(
                _remote_tags(bare),
                {"v1.0.0": base_sha, "v1.0.1": a_sha, "v1.1.0": b_sha, "v1": b_sha},
            )

    def test_cli_verify_gate_subcommand_reports_authorized_and_blocked_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, work, sha = _create_repo_with_bare_remote(root)
            bin_dir, state_path = _write_fake_gh(root, {})
            script_path = Path(__file__).resolve().parent / "release.py"
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
            env["FAKE_GH_STATE"] = str(state_path)
            env["GH_TOKEN"] = "test-token"
            env["CANDIDATE_SMOKE_TOKEN"] = "test-smoke-token"
            env["GITHUB_REPOSITORY"] = "nandohdc/gh-pipelines"

            ok_proc = subprocess.run(
                [sys.executable, str(script_path), "verify-gate", "--sha", sha],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ok_proc.returncode, 0, msg=ok_proc.stderr)
            self.assertIn("status=authorized", ok_proc.stdout)

            # Remove smoke runs so verify-gate blocks cleanly.
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["smoke_runs"] = []
            state_path.write_text(json.dumps(state), encoding="utf-8")
            bad_proc = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "verify-gate",
                    "--sha",
                    sha,
                    "--no-dispatch-smoke",
                ],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(bad_proc.returncode, 1)
            self.assertEqual(bad_proc.stdout.count("status=blocked"), 1, msg=bad_proc.stdout)

    def test_gh_cli_ensure_candidate_smoke_forwards_token_to_run_candidate_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, work, sha = _create_repo_with_bare_remote(root)
            from scripts import candidate_smoke

            captured_kwargs: dict = {}

            def fake_run_candidate_smoke(smoke_config, consumer_github, **kwargs):
                captured_kwargs["smoke_config"] = smoke_config
                captured_kwargs["consumer_github"] = consumer_github
                captured_kwargs.update(kwargs)
                return candidate_smoke.CandidateSmokeOutcome(
                    status="passed",
                    reason="verified",
                    candidate_sha=sha,
                    consumer_repo="nandohdc/gh-pipelines-hosted-acceptance",
                    run_id=99001,
                    run_url="https://github.com/nandohdc/gh-pipelines-hosted-acceptance/actions/runs/99001",
                )

            github = GhCliGitHub(workdir=work, repository="nandohdc/gh-pipelines")
            with (
                mock.patch.object(
                    candidate_smoke, "resolve_consumer_token", return_value="pat-secret"
                ),
                mock.patch.object(
                    candidate_smoke, "find_verified_candidate_smoke_run", return_value=None
                ),
                mock.patch.object(
                    candidate_smoke, "run_candidate_smoke", side_effect=fake_run_candidate_smoke
                ),
            ):
                outcome = github.ensure_candidate_smoke(sha)

            self.assertEqual(outcome["status"], "passed")
            self.assertEqual(captured_kwargs.get("token"), "pat-secret")

    def test_publish_tags_retries_with_release_or_integration_token_when_workflow_permission_rejected(
        self,
    ) -> None:
        hook_script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "token=''\n"
            "if [[ -n \"${GIT_ASKPASS:-}\" && -x \"${GIT_ASKPASS}\" ]]; then\n"
            "  token=$(\"${GIT_ASKPASS}\" \"Password for 'https://github.com':\")\n"
            "fi\n"
            "while read -r _oldrev _newrev refname; do\n"
            "  if [[ \"${token}\" != \"workflow-pat-secret\" ]]; then\n"
            "    echo \" ! [remote rejected] ${refname#refs/tags/} -> ${refname#refs/tags/} (refusing to allow a GitHub App to create or update workflow \\`.github/workflows/ci-go.yml\\` without \\`workflows\\` permission)\" >&2\n"
            "    exit 1\n"
            "  fi\n"
            "done\n"
        )
        for env_key in ("RELEASE_TOKEN", "INTEGRATION_PAT"):
            with self.subTest(fallback_env=env_key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bare, work, base_sha = _create_repo_with_bare_remote(root)
                _git(bare, "update-ref", "refs/tags/v2.1.0", base_sha)
                _git(bare, "update-ref", "refs/tags/v2", base_sha)
                (work / "README.md").write_text("bump\n", encoding="utf-8")
                _git(work, "add", ".")
                _git(work, "commit", "-m", "chore: bump (#162)")
                _git(work, "push", "origin", "main")
                sha2 = _git(work, "rev-parse", "HEAD")

                hook = bare / "hooks" / "pre-receive"
                hook.write_text(hook_script, encoding="utf-8")
                hook.chmod(0o755)

                config = ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha2,
                    workdir=work,
                    remote="origin",
                )
                plan = ReleasePlan(
                    pr_number=162,
                    release_type="patch",
                    target_major=2,
                    target_lineage="v2",
                    latest_tag="v2.1.0",
                    next_tag="v2.1.1",
                    floating_tag="v2",
                    target_sha=sha2,
                )
                env_patch = {
                    "GH_TOKEN": "ghs_unprivileged",
                    "GITHUB_TOKEN": "ghs_unprivileged",
                    env_key: "workflow-pat-secret",
                }
                if env_key == "INTEGRATION_PAT":
                    env_patch["RELEASE_TOKEN"] = ""
                else:
                    env_patch["INTEGRATION_PAT"] = ""
                with mock.patch.dict(os.environ, env_patch, clear=False):
                    exact_effect, floating_effect = _publish_tags(config, plan)

                self.assertEqual(exact_effect, "created")
                self.assertEqual(floating_effect, "moved")
                self.assertEqual(_git(bare, "rev-parse", "refs/tags/v2.1.1"), sha2)
                self.assertEqual(_git(bare, "rev-parse", "refs/tags/v2"), sha2)

    def test_publish_tags_fails_closed_and_redacts_when_fallback_token_missing_or_rejected(
        self,
    ) -> None:
        hook_script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "while read -r _oldrev _newrev refname; do\n"
            "  echo \" ! [remote rejected] ${refname#refs/tags/} -> ${refname#refs/tags/} (refusing to allow a GitHub App to create or update workflow \\`.github/workflows/ci-go.yml\\` without \\`workflows\\` permission)\" >&2\n"
            "  exit 1\n"
            "done\n"
        )
        cases = (
            ("missing", {"GH_TOKEN": "ghs_unpriv", "GITHUB_TOKEN": "ghs_unpriv", "RELEASE_TOKEN": "", "INTEGRATION_PAT": ""}),
            ("rejected", {"GH_TOKEN": "ghs_unpriv", "GITHUB_TOKEN": "ghs_unpriv", "RELEASE_TOKEN": "secret-rel-123", "INTEGRATION_PAT": "secret-int-456"}),
        )
        for label, env_patch in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bare, work, base_sha = _create_repo_with_bare_remote(root)
                _git(bare, "update-ref", "refs/tags/v2.1.0", base_sha)
                (work / "README.md").write_text("bump\n", encoding="utf-8")
                _git(work, "add", ".")
                _git(work, "commit", "-m", "chore: bump (#162)")
                _git(work, "push", "origin", "main")
                sha2 = _git(work, "rev-parse", "HEAD")

                hook = bare / "hooks" / "pre-receive"
                hook.write_text(hook_script, encoding="utf-8")
                hook.chmod(0o755)

                config = ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha2,
                    workdir=work,
                    remote="origin",
                )
                plan = ReleasePlan(
                    pr_number=162,
                    release_type="patch",
                    target_major=2,
                    target_lineage="v2",
                    latest_tag="v2.1.0",
                    next_tag="v2.1.1",
                    floating_tag="v2",
                    target_sha=sha2,
                )
                with mock.patch.dict(os.environ, env_patch, clear=False):
                    with self.assertRaises(ReleaseError) as ctx:
                        _publish_tags(config, plan)
                msg = str(ctx.exception)
                self.assertIn("RELEASE_TOKEN", msg)
                self.assertNotIn("secret-rel-123", msg)
                self.assertNotIn("secret-int-456", msg)
                self.assertNotIn("ghs_unpriv", msg)

    def test_gh_cli_create_release_retries_with_elevated_token_on_workflow_permission_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, work, sha = _create_repo_with_bare_remote(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_gh = bin_dir / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "tok = os.environ.get('GH_TOKEN', '')\n"
                "if tok != 'workflow-pat-secret':\n"
                "    print('! [remote rejected] refusing to allow a GitHub App to create or update workflow .github/workflows/ci-go.yml without workflows permission', file=sys.stderr)\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            github = GhCliGitHub(workdir=work, repository="nandohdc/gh-pipelines")
            env_patch = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "ghs_unprivileged",
                "RELEASE_TOKEN": "workflow-pat-secret",
            }
            with mock.patch.dict(os.environ, env_patch, clear=False):
                github.create_release("v2.1.1", sha, "v2.1.1")

    def test_gh_cli_create_release_retries_on_api_integration_403(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "with open(os.environ['ATTEMPTS_FILE'], 'a') as f:\n"
                "    f.write(('fallback' if os.environ.get('GH_TOKEN') == 'fallback-token' else 'baseline') + '\\n')\n"
                "if os.environ.get('GH_TOKEN') != 'fallback-token':\n"
                "    print('HTTP 403: Resource not accessible by integration (https://api.github.com/repos/nandohdc/gh-pipelines/releases)', file=sys.stderr)\n"
                "    sys.exit(1)\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            github = GhCliGitHub(workdir=root, repository="nandohdc/gh-pipelines")
            with mock.patch.dict(os.environ, {
                "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "baseline-token", "GITHUB_TOKEN": "baseline-token",
                "RELEASE_TOKEN": "", "INTEGRATION_PAT": "fallback-token",
                "ATTEMPTS_FILE": str(root / "attempts"),
            }):
                github.create_release("v2.1.1", "a" * 40, "v2.1.1")
            self.assertEqual((root / "attempts").read_text().splitlines(), ["baseline", "fallback"])

    def test_gh_cli_create_release_does_not_retry_unrelated_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "with open(os.environ['ATTEMPTS_FILE'], 'a') as f: f.write('attempt\\n')\n"
                "print('HTTP 403: API rate limit exceeded', file=sys.stderr)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            github = GhCliGitHub(workdir=root, repository="nandohdc/gh-pipelines")
            with mock.patch.dict(os.environ, {
                "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "baseline-token", "GITHUB_TOKEN": "baseline-token",
                "RELEASE_TOKEN": "fallback-token", "INTEGRATION_PAT": "",
                "ATTEMPTS_FILE": str(root / "attempts"),
            }):
                with self.assertRaisesRegex(ReleaseError, "API rate limit exceeded"):
                    github.create_release("v2.1.1", "a" * 40, "v2.1.1")
            self.assertEqual((root / "attempts").read_text().splitlines(), ["attempt"])

    def test_gh_cli_create_release_redacts_rejected_fallback_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "print('HTTP 403: Resource not accessible by integration ' + os.environ['GH_TOKEN'], file=sys.stderr)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            github = GhCliGitHub(workdir=root, repository="nandohdc/gh-pipelines")
            with mock.patch.dict(os.environ, {
                "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "baseline-secret", "GITHUB_TOKEN": "baseline-secret",
                "RELEASE_TOKEN": "release-secret", "INTEGRATION_PAT": "integration-secret",
            }):
                with self.assertRaises(ReleaseError) as error:
                    github.create_release("v2.1.1", "a" * 40, "v2.1.1")
            message = str(error.exception)
            self.assertIn("RELEASE_TOKEN, INTEGRATION_PAT", message)
            for secret in ("baseline-secret", "release-secret", "integration-secret"):
                self.assertNotIn(secret, message)

    def test_gh_cli_create_release_reports_missing_fallback_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "echo 'HTTP 403: Resource not accessible by integration' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            github = GhCliGitHub(workdir=root, repository="nandohdc/gh-pipelines")
            with mock.patch.dict(os.environ, {
                "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "baseline-token", "GITHUB_TOKEN": "baseline-token",
                "RELEASE_TOKEN": "", "INTEGRATION_PAT": "",
            }):
                with self.assertRaisesRegex(ReleaseError, "Configure repository secret RELEASE_TOKEN"):
                    github.create_release("v2.1.1", "a" * 40, "v2.1.1")

    def test_release_workflow_forwards_optional_release_token(self) -> None:
        workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("token: ${{ secrets.RELEASE_TOKEN || github.token }}", content)
        self.assertIn("RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}", content)
        self.assertIn("INTEGRATION_PAT: ${{ secrets.INTEGRATION_PAT }}", content)


if __name__ == "__main__":
    unittest.main()
