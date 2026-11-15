#!/usr/bin/env python3
"""Unit and contract tests for scripts/candidate_smoke.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import candidate_smoke  # noqa: E402
from scripts.contract_lib import assert_contains, read  # noqa: E402


SAMPLE_SHA = "62c1b195251edc1b61ab532ac3ae86ef810af9be"
OTHER_SHA = "1111111111111111111111111111111111111111"


class FakeConsumerGitHub:
    """In-memory adapter stubbing GitHub workflow dispatch and run queries."""

    def __init__(
        self,
        *,
        dispatched_runs: Optional[List[Dict[str, Any]]] = None,
        jobs_by_run: Optional[Dict[int, List[Dict[str, Any]]]] = None,
        fail_dispatch: Optional[str] = None,
    ) -> None:
        self.dispatched_runs = list(dispatched_runs or [])
        self.jobs_by_run = dict(jobs_by_run or {})
        self.fail_dispatch = fail_dispatch
        self.dispatch_calls: List[Dict[str, Any]] = []
        self._poll_count = 0

    def dispatch_workflow(
        self,
        repo: str,
        workflow: str,
        ref: str,
        inputs: Dict[str, str],
    ) -> None:
        if self.fail_dispatch is not None:
            raise candidate_smoke.CandidateSmokeError(self.fail_dispatch)
        self.dispatch_calls.append(
            {"repo": repo, "workflow": workflow, "ref": ref, "inputs": dict(inputs)}
        )

    def list_workflow_runs(
        self,
        repo: str,
        workflow: str,
        *,
        branch: Optional[str] = None,
        event: str = "workflow_dispatch",
        per_page: int = 20,
    ) -> List[Dict[str, Any]]:
        self._poll_count += 1
        runs = self.dispatched_runs
        if branch is not None:
            runs = [run for run in runs if run.get("head_branch") == branch]
        if event:
            runs = [run for run in runs if run.get("event") == event]
        return runs[:per_page]

    def list_run_jobs(self, repo: str, run_id: int) -> List[Dict[str, Any]]:
        return list(self.jobs_by_run.get(run_id, []))


def _make_success_jobs(sha: str, include_cross_repo: bool = True) -> List[Dict[str, Any]]:
    jobs = [
        {"name": "smoke-ci-go / lint", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-go / test", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-node / lint", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-node / test", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-python / lint", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-python / test", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-terraform / fmt", "status": "completed", "conclusion": "success"},
        {"name": "smoke-ci-terraform / validate", "status": "completed", "conclusion": "success"},
        {"name": "smoke-direct-actions", "status": "completed", "conclusion": "success"},
        {
            "name": f"candidate-smoke-result / {sha}",
            "status": "completed",
            "conclusion": "success",
        },
    ]
    if include_cross_repo:
        jobs.append(
            {
                "name": "smoke-cross-repo-ci-go / lint",
                "status": "completed",
                "conclusion": "success",
            }
        )
    return jobs


def _create_bare_consumer_remote(tmp_path: Path) -> Path:
    bare_repo = tmp_path / "consumer-remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)
    seed_dir = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare_repo), str(seed_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed_dir), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed_dir), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    (seed_dir / "README.md").write_text("seed consumer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed_dir), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed_dir), "commit", "-m", "chore: seed"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed_dir), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed_dir), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return bare_repo


class CandidateSmokeTests(unittest.TestCase):
    """Behavior tests for synthetic candidate consumer generation and verification."""

    def test_prepare_candidate_smoke_tree_rewrites_nested_actions_and_injects_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "consumer"
            workspace.mkdir()
            config = candidate_smoke.CandidateSmokeConfig(
                source_repo="nandohdc/gh-pipelines",
                consumer_repo="nandohdc/gh-pipelines-hosted-acceptance",
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-test-1",
            )
            smoke_branch = "smoke/candidate-62c1b195251e-req-test-1"

            candidate_smoke.prepare_candidate_smoke_tree(workspace, config, smoke_branch)

            # Nested composite actions inside candidate reusable workflows must resolve to
            # the staged candidate branch rather than published @v1 tags.
            for lang in ("go", "python", "terraform"):
                wf_doc = yaml.safe_load(
                    (workspace / ".github" / "workflows" / f"candidate-ci-{lang}.yml").read_text(
                        encoding="utf-8"
                    )
                )
                step_uses = [
                    str(step["uses"])
                    for job in wf_doc["jobs"].values()
                    for step in job.get("steps", [])
                    if isinstance(step, dict) and "uses" in step
                ]
                self.assertFalse(
                    any("nandohdc/gh-pipelines/actions/" in ref for ref in step_uses),
                    f"unexpected unrewritten action ref in candidate-ci-{lang}.yml: {step_uses}",
                )
                self.assertTrue(
                    any(
                        ref.startswith("nandohdc/gh-pipelines-hosted-acceptance/actions/")
                        and ref.endswith(f"@{smoke_branch}")
                        for ref in step_uses
                    ),
                    f"expected rewritten candidate action ref in candidate-ci-{lang}.yml: {step_uses}",
                )

            # Every staged composite action must record candidate SHA provenance.
            for action_name in (
                "setup-go",
                "setup-node",
                "setup-python",
                "setup-terraform",
                "setup-helm",
                "docker-meta",
            ):
                action_data = yaml.safe_load(
                    (workspace / "actions" / action_name / "action.yml").read_text(encoding="utf-8")
                )
                first_step = action_data["runs"]["steps"][0]
                self.assertEqual(first_step["env"]["CANDIDATE_ACTION_NAME"], action_name)
                self.assertEqual(first_step["env"]["CANDIDATE_ACTION_SHA"], SAMPLE_SHA)

            smoke_wf = yaml.safe_load(
                (workspace / ".github" / "workflows" / "candidate-smoke.yml").read_text(
                    encoding="utf-8"
                )
            )
            jobs = smoke_wf["jobs"]
            self.assertEqual(
                jobs["smoke-ci-go"]["uses"],
                f"nandohdc/gh-pipelines-hosted-acceptance/.github/workflows/candidate-ci-go.yml@{smoke_branch}",
            )
            self.assertEqual(
                jobs["smoke-cross-repo-ci-go"]["uses"],
                f"nandohdc/gh-pipelines/.github/workflows/ci-go.yml@{SAMPLE_SHA}",
            )
            self.assertEqual(
                jobs["candidate-smoke-result"]["name"],
                "candidate-smoke-result / ${{ inputs.candidate_sha }}",
            )
            self.assertTrue((workspace / "tests" / "fixtures" / "helm" / "Chart.yaml").is_file())

    def test_prepare_candidate_smoke_tree_supports_negative_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_input_dir = Path(tmp) / "bad-input"
            bad_input_dir.mkdir()
            bad_cfg = candidate_smoke.CandidateSmokeConfig(
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-bad-input",
                scenario="nonexistent-input",
            )
            candidate_smoke.prepare_candidate_smoke_tree(bad_input_dir, bad_cfg, "smoke/test-bad")
            wf_bad = yaml.safe_load(
                (bad_input_dir / ".github" / "workflows" / "candidate-smoke.yml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                wf_bad["jobs"]["smoke-ci-go"]["with"]["nonexistent-caller-input"], "true"
            )
            self.assertNotIn("smoke-cross-repo-ci-go", wf_bad["jobs"])

            reg_dir = Path(tmp) / "regression"
            reg_dir.mkdir()
            reg_cfg = candidate_smoke.CandidateSmokeConfig(
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-reg",
                scenario="action-regression",
            )
            candidate_smoke.prepare_candidate_smoke_tree(reg_dir, reg_cfg, "smoke/test-reg")
            setup_go = yaml.safe_load(
                (reg_dir / "actions" / "setup-go" / "action.yml").read_text(encoding="utf-8")
            )
            step_runs = "\n".join(str(step.get("run", "")) for step in setup_go["runs"]["steps"])
            self.assertIn("Simulated affected composite action regression", step_runs)
            self.assertIn("exit 1", step_runs)

    def test_verify_candidate_smoke_run_accepts_matching_candidate_and_rejects_wrong_or_stale_sha(
        self,
    ) -> None:
        run_ok = {
            "id": 9001,
            "html_url": "https://github.com/nandohdc/gh-pipelines-hosted-acceptance/actions/runs/9001",
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
        }
        outcome = candidate_smoke.verify_candidate_smoke_run(
            run_ok,
            _make_success_jobs(SAMPLE_SHA),
            expected_candidate_sha=SAMPLE_SHA,
            consumer_repo="nandohdc/gh-pipelines-hosted-acceptance",
            smoke_branch="smoke/candidate-62c1b195251e-req-1",
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.candidate_sha, SAMPLE_SHA)
        self.assertEqual(outcome.run_id, 9001)

        with self.assertRaises(candidate_smoke.CandidateSmokeError) as ctx_wrong:
            candidate_smoke.verify_candidate_smoke_run(
                run_ok,
                _make_success_jobs(OTHER_SHA),
                expected_candidate_sha=SAMPLE_SHA,
                consumer_repo="nandohdc/gh-pipelines-hosted-acceptance",
            )
        self.assertIn("wrong_candidate_sha", str(ctx_wrong.exception))

        run_failed = {**run_ok, "conclusion": "failure"}
        with self.assertRaises(candidate_smoke.CandidateSmokeError) as ctx_fail:
            candidate_smoke.verify_candidate_smoke_run(
                run_failed,
                _make_success_jobs(SAMPLE_SHA),
                expected_candidate_sha=SAMPLE_SHA,
                consumer_repo="nandohdc/gh-pipelines-hosted-acceptance",
            )
        self.assertIn("run_failed", str(ctx_fail.exception))

        adapter = FakeConsumerGitHub(
            dispatched_runs=[run_ok],
            jobs_by_run={9001: _make_success_jobs(OTHER_SHA)},
        )
        self.assertIsNone(
            candidate_smoke.find_verified_candidate_smoke_run(
                adapter,
                "nandohdc/gh-pipelines-hosted-acceptance",
                SAMPLE_SHA,
            )
        )
        matched = candidate_smoke.find_verified_candidate_smoke_run(
            adapter,
            "nandohdc/gh-pipelines-hosted-acceptance",
            OTHER_SHA,
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.run_id, 9001)

    def test_unprovisioned_credentials_fail_closed(self) -> None:
        clean_env = {k: v for k, v in os.environ.items() if k not in (
            "CANDIDATE_SMOKE_TOKEN",
            "INTEGRATION_PAT",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        )}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            with self.assertRaises(candidate_smoke.CandidateSmokeError) as ctx:
                candidate_smoke.resolve_consumer_token(allow_gh_token=False)
            self.assertIn("unprovisioned", str(ctx.exception))

            rc = candidate_smoke.main(["verify", "--candidate-sha", SAMPLE_SHA])
            self.assertEqual(rc, 2)

    def test_run_candidate_smoke_cleans_up_ephemeral_branch_on_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare_remote = _create_bare_consumer_remote(tmp_path)
            smoke_branch_seen: List[str] = []

            class BranchCapturingGitHub(FakeConsumerGitHub):
                def dispatch_workflow(
                    self,
                    repo: str,
                    workflow: str,
                    ref: str,
                    inputs: Dict[str, str],
                ) -> None:
                    super().dispatch_workflow(repo, workflow, ref, inputs)
                    smoke_branch_seen.append(ref)
                    branches = subprocess.run(
                        ["git", "ls-remote", "--heads", str(bare_remote)],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                    assert f"refs/heads/{ref}" in branches
                    self.dispatched_runs.append(
                        {
                            "id": 777,
                            "html_url": "https://example.invalid/runs/777",
                            "status": "completed",
                            "conclusion": "success",
                            "event": "workflow_dispatch",
                            "head_branch": ref,
                        }
                    )
                    self.jobs_by_run[777] = _make_success_jobs(SAMPLE_SHA)

            cfg = candidate_smoke.CandidateSmokeConfig(
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-clean",
                poll_interval_seconds=0,
            )
            github = BranchCapturingGitHub()
            outcome = candidate_smoke.run_candidate_smoke(
                cfg,
                github,
                remote_url=str(bare_remote),
                token="dummy-token-for-local-remote",
            )
            self.assertEqual(outcome.status, "passed")
            self.assertEqual(len(smoke_branch_seen), 1)
            branches_after = subprocess.run(
                ["git", "ls-remote", "--heads", str(bare_remote)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn(smoke_branch_seen[0], branches_after)

            # Even when dispatch fails, the ephemeral remote branch must be deleted.
            failing_gh = BranchCapturingGitHub(fail_dispatch="simulated dispatch outage")
            cfg_fail = candidate_smoke.CandidateSmokeConfig(
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-fail-cleanup",
                poll_interval_seconds=0,
            )
            with self.assertRaises(candidate_smoke.CandidateSmokeError):
                candidate_smoke.run_candidate_smoke(
                    cfg_fail,
                    failing_gh,
                    remote_url=str(bare_remote),
                    token="dummy-token-for-local-remote",
                )
            branches_after_fail = subprocess.run(
                ["git", "ls-remote", "--heads", str(bare_remote)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn("req-fail-cleanup", branches_after_fail)

    def test_run_candidate_smoke_uses_client_token_and_disables_terminal_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare_remote = _create_bare_consumer_remote(tmp_path)
            observed_envs: List[Dict[str, str]] = []
            original_run_cmd = candidate_smoke._run_cmd

            def spying_run_cmd(cmd: List[str], *, cwd=None, env=None, token=None) -> str:
                if cmd[:2] == ["git", "clone"] or cmd[:2] == ["git", "push"]:
                    observed_envs.append(dict(env or {}))
                return original_run_cmd(cmd, cwd=cwd, env=env, token=token)

            class TokenCarryingGitHub(FakeConsumerGitHub):
                def __init__(self, token: str) -> None:
                    super().__init__()
                    self._token = token

                def dispatch_workflow(
                    self,
                    repo: str,
                    workflow: str,
                    ref: str,
                    inputs: Dict[str, str],
                ) -> None:
                    super().dispatch_workflow(repo, workflow, ref, inputs)
                    self.dispatched_runs.append(
                        {
                            "id": 778,
                            "html_url": "https://example.invalid/runs/778",
                            "status": "completed",
                            "conclusion": "success",
                            "event": "workflow_dispatch",
                            "head_branch": ref,
                        }
                    )
                    self.jobs_by_run[778] = _make_success_jobs(SAMPLE_SHA)

            cfg = candidate_smoke.CandidateSmokeConfig(
                candidate_sha=SAMPLE_SHA,
                candidate_dir=ROOT,
                request_id="req-tok",
                poll_interval_seconds=0,
            )
            with mock.patch.object(candidate_smoke, "_run_cmd", side_effect=spying_run_cmd):
                outcome = candidate_smoke.run_candidate_smoke(
                    cfg,
                    TokenCarryingGitHub("client-tok-123"),
                    remote_url=str(bare_remote),
                )
            self.assertEqual(outcome.status, "passed")
            self.assertGreaterEqual(len(observed_envs), 2)
            for captured in observed_envs:
                self.assertEqual(captured.get("GIT_TERMINAL_PROMPT"), "0")
                self.assertEqual(captured.get("CANDIDATE_SMOKE_GIT_TOKEN"), "client-tok-123")
                self.assertTrue(captured.get("GIT_ASKPASS"))

            # When no token is available at all, GIT_TERMINAL_PROMPT=0 is still enforced.
            observed_envs.clear()
            with mock.patch.object(candidate_smoke, "_run_cmd", side_effect=spying_run_cmd):
                candidate_smoke.run_candidate_smoke(
                    candidate_smoke.CandidateSmokeConfig(
                        candidate_sha=SAMPLE_SHA,
                        candidate_dir=ROOT,
                        request_id="req-notok",
                        timeout_seconds=5,
                        poll_interval_seconds=0,
                    ),
                    TokenCarryingGitHub(""),
                    remote_url=str(bare_remote),
                )
            self.assertGreaterEqual(len(observed_envs), 2)
            for captured in observed_envs:
                self.assertEqual(captured.get("GIT_TERMINAL_PROMPT"), "0")
                self.assertNotIn("CANDIDATE_SMOKE_GIT_TOKEN", captured)

    def test_lint_and_docs_wire_candidate_smoke(self) -> None:
        lint_text = read(ROOT / ".github" / "workflows" / "lint.yml")
        assert_contains(
            lint_text,
            "python -m pytest scripts/candidate_smoke_test.py",
            "lint workflow must run Candidate smoke tests",
        )
        consumers_doc = read(ROOT / "docs" / "INTEGRATION_CONSUMERS.md")
        assert_contains(
            consumers_doc,
            "gh-pipelines-hosted-acceptance",
            "docs/INTEGRATION_CONSUMERS.md must document the Synthetic consumer repository",
        )
        contrib_doc = read(ROOT / "docs" / "CONTRIBUTING.md")
        assert_contains(
            contrib_doc,
            "scripts/candidate_smoke.py",
            "docs/CONTRIBUTING.md must document scripts/candidate_smoke.py",
        )


if __name__ == "__main__":
    unittest.main()
