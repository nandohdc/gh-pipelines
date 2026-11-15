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

from scripts.release import ReleaseConfig, ReleaseError, ReleaseOutcome, ReleasePlan, run_release


class FakeGitHub:
    """In-memory adapter implementing the GitHubClient seam for tests."""

    def __init__(
        self,
        *,
        commit_prs: Optional[Dict[str, List[dict]]] = None,
        pr_labels: Optional[Dict[int, List[str]]] = None,
        existing_releases: Optional[Dict[str, dict]] = None,
        fail_on: Optional[str] = None,
    ) -> None:
        self.commit_prs = commit_prs or {}
        self.pr_labels = pr_labels or {}
        self.releases: Dict[str, dict] = dict(existing_releases or {})
        self.fail_on = fail_on

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


_FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
fail_cmd = state.get("fail_command")

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


if __name__ == "__main__":
    unittest.main()
