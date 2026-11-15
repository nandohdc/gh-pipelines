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

from scripts.release import ReleaseConfig, ReleaseError, ReleaseOutcome, run_release


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

    def find_merged_pr_for_commit(self, repository: str, sha: str) -> Optional[int]:
        if self.fail_on == "find_merged_pr_for_commit":
            raise ReleaseError("simulated gh api commit pulls failure")
        for pr in self.commit_prs.get(sha, []):
            base = pr.get("base") or {}
            if pr.get("merged_at") is not None and base.get("ref") == "main":
                return int(pr["number"])
        return None

    def list_pr_labels(self, repository: str, pr_number: int) -> List[str]:
        if self.fail_on == "list_pr_labels":
            raise ReleaseError("simulated gh api labels failure")
        return list(self.pr_labels.get(pr_number, []))

    def release_exists(self, tag: str) -> bool:
        if self.fail_on == "release_exists":
            raise ReleaseError("simulated gh release view failure")
        return tag in self.releases

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
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v1": sha})
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
            _git(work, "push", "origin", "refs/tags/v1.2.3")

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
            # Known pre-existing v1 move behavior preserved in #181; #182 handles lineage isolation.
            self.assertEqual(_remote_tags(bare), {"v1.2.3": first_sha, "v2.0.0": second_sha, "v1": second_sha})

    def test_skips_release_creation_when_github_release_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare, work, sha = _create_repo_with_bare_remote(Path(tmp))
            _git(work, "tag", "v0.0.2", sha)
            _git(work, "push", "origin", "refs/tags/v0.0.2")

            github = FakeGitHub(
                pr_labels={11: ["release:patch"]},
                existing_releases={"v0.0.3": {"tag": "v0.0.3", "target_sha": sha, "title": "existing"}},
            )
            outcome = run_release(
                ReleaseConfig(
                    repository="nandohdc/gh-pipelines",
                    sha=sha,
                    head_commit_message="fix: retry (#11)",
                    workdir=work,
                ),
                github,
            )
            self.assertEqual(outcome.status, "published")
            self.assertEqual(outcome.next_tag, "v0.0.3")
            self.assertEqual(github.releases["v0.0.3"]["title"], "existing")
            self.assertEqual(_remote_tags(bare)["v1"], sha)

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
            _install_pre_receive_hook(bare, "refs/tags/v1")
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
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v1": sha})
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
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v1": sha})
            self.assertEqual(github.releases, {})


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
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v1": sha})
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
            self.assertEqual(_remote_tags(bare), {"v0.0.1": sha, "v1": sha})

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


if __name__ == "__main__":
    unittest.main()
