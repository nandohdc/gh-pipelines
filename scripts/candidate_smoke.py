#!/usr/bin/env python3
"""Stage and run candidate smoke verification in a dedicated hosted Consumer repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

DEFAULT_SOURCE_REPO = "nandohdc/gh-pipelines"


def _require_yaml() -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CandidateSmokeError(
            "PyYAML is required to stage candidate smoke workflows/actions (pip install PyYAML)."
        ) from exc
    return yaml
DEFAULT_CONSUMER_REPO = "nandohdc/gh-pipelines-hosted-acceptance"
CANDIDATE_SMOKE_WORKFLOW = "candidate-smoke.yml"

_HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_RESULT_JOB_PREFIX = "candidate-smoke-result / "
_CANDIDATE_ACTION_USES_RE = re.compile(
    r"(?m)^([ \t]*(?:-[ \t]*)?uses:[ \t]*)(?:nandohdc/gh-pipelines|\.)/actions/([A-Za-z0-9_.-]+)(?:@[^\s#]+)?(.*)$"
)
_CORE_REQUIRED_PREFIXES = (
    "smoke-ci-go",
    "smoke-ci-node",
    "smoke-ci-python",
    "smoke-ci-terraform",
    "smoke-direct-actions",
)
_DIRECT_ACTION_NAMES = (
    "setup-go",
    "setup-node",
    "setup-python",
    "setup-terraform",
    "setup-helm",
    "docker-meta",
)
_CANDIDATE_WORKFLOW_FILES = (
    ("ci-go.yml", "candidate-ci-go.yml"),
    ("ci-node.yml", "candidate-ci-node.yml"),
    ("ci-python.yml", "candidate-ci-python.yml"),
    ("ci-terraform.yml", "candidate-ci-terraform.yml"),
)


class CandidateSmokeError(RuntimeError):
    """Raised when candidate smoke provisioning, execution, or verification fails."""


@dataclass(frozen=True)
class CandidateSmokeConfig:
    """Configuration for executing or verifying candidate smoke in the synthetic consumer."""

    candidate_sha: str
    source_repo: str = DEFAULT_SOURCE_REPO
    consumer_repo: str = DEFAULT_CONSUMER_REPO
    candidate_dir: Path = Path(".")
    request_id: str = ""
    scenario: str = "ordinary"
    include_cross_repo_call: bool = True
    timeout_seconds: int = 600
    poll_interval_seconds: int = 10


@dataclass(frozen=True)
class CandidateSmokeOutcome:
    """Structured result from a candidate smoke execution or verification lookup."""

    status: str
    reason: str
    candidate_sha: str
    consumer_repo: str
    run_id: Optional[int] = None
    run_url: Optional[str] = None
    smoke_branch: Optional[str] = None
    verified_jobs: Tuple[str, ...] = ()


class ConsumerGitHubClient(Protocol):
    """Port for GitHub Actions workflow dispatch and run inspection."""

    def dispatch_workflow(
        self,
        repo: str,
        workflow: str,
        ref: str,
        inputs: Dict[str, str],
    ) -> None:
        ...

    def list_workflow_runs(
        self,
        repo: str,
        workflow: str,
        *,
        branch: Optional[str] = None,
        event: str = "workflow_dispatch",
        per_page: int = 20,
    ) -> List[Dict[str, Any]]:
        ...

    def list_run_jobs(self, repo: str, run_id: int) -> List[Dict[str, Any]]:
        ...


def _redact_token(text: str, token: Optional[str]) -> str:
    if token and token in text:
        return text.replace(token, "***")
    return text


def resolve_consumer_token(*, allow_gh_token: bool = False) -> str:
    """Resolve the cross-repository credential without printing secret material."""
    for env_name in ("CANDIDATE_SMOKE_TOKEN", "INTEGRATION_PAT"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    if allow_gh_token:
        for env_name in ("GH_TOKEN", "GITHUB_TOKEN"):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise CandidateSmokeError(
        "unprovisioned: Missing required CANDIDATE_SMOKE_TOKEN / INTEGRATION_PAT for "
        "Synthetic consumer repository access."
    )


def _run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    token: Optional[str] = None,
) -> str:
    result = subprocess.run(
        list(cmd),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = _redact_token((result.stderr or result.stdout).strip(), token)
        raise CandidateSmokeError(f"{' '.join(cmd)} failed: {detail}")
    return result.stdout


class GhCliConsumerGitHub:
    """Production ConsumerGitHubClient backed by the GitHub CLI."""

    def __init__(self, token: str) -> None:
        self._token = token

    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["GH_TOKEN"] = self._token
        return env

    def dispatch_workflow(
        self,
        repo: str,
        workflow: str,
        ref: str,
        inputs: Dict[str, str],
    ) -> None:
        cmd: List[str] = [
            "gh",
            "workflow",
            "run",
            workflow,
            "--repo",
            repo,
            "--ref",
            ref,
        ]
        for key, value in inputs.items():
            cmd.extend(["-f", f"{key}={value}"])
        _run_cmd(cmd, env=self._env(), token=self._token)

    def _api_json(self, endpoint: str) -> Any:
        raw = _run_cmd(["gh", "api", endpoint], env=self._env(), token=self._token)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CandidateSmokeError(f"Invalid JSON from gh api {endpoint}: {exc}") from exc

    def list_workflow_runs(
        self,
        repo: str,
        workflow: str,
        *,
        branch: Optional[str] = None,
        event: str = "workflow_dispatch",
        per_page: int = 20,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {"per_page": str(per_page)}
        if branch is not None:
            params["branch"] = branch
        if event:
            params["event"] = event
        query = urllib.parse.urlencode(params)
        data = self._api_json(f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}")
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise CandidateSmokeError(f"Expected workflow_runs list for {repo}/{workflow}.")
        return [item for item in runs if isinstance(item, dict)]

    def list_run_jobs(self, repo: str, run_id: int) -> List[Dict[str, Any]]:
        data = self._api_json(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise CandidateSmokeError(f"Expected jobs list for run {run_id} in {repo}.")
        return [item for item in jobs if isinstance(item, dict)]


def _validate_sha(sha: str) -> str:
    normalized = sha.strip().lower()
    if not _HEX_SHA_RE.match(normalized):
        raise CandidateSmokeError(
            f"Invalid candidate SHA '{sha}'; expected 40-character hexadecimal commit SHA."
        )
    return normalized


def _ensure_helm_fixture(fixtures_dst: Path) -> None:
    helm_dir = fixtures_dst / "helm"
    if (helm_dir / "Chart.yaml").is_file():
        return
    templates_dir = helm_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    (helm_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: candidate-smoke-fixture\nversion: 0.1.0\nappVersion: \"0.1.0\"\n",
        encoding="utf-8",
    )
    (helm_dir / "values.yaml").write_text("message: candidate-smoke\n", encoding="utf-8")
    (templates_dir / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}\ndata:\n  message: {{ .Values.message | quote }}\n",
        encoding="utf-8",
    )


def _stage_composite_actions(
    candidate_actions_dir: Path,
    dst_actions_dir: Path,
    candidate_sha: str,
    scenario: str,
) -> None:
    if dst_actions_dir.exists():
        shutil.rmtree(dst_actions_dir)
    shutil.copytree(candidate_actions_dir, dst_actions_dir)

    yaml = _require_yaml()
    for action_yml in sorted(dst_actions_dir.glob("*/action.yml")):
        action_name = action_yml.parent.name
        data = yaml.safe_load(action_yml.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        runs = data.get("runs")
        if not isinstance(runs, dict) or runs.get("using") != "composite":
            continue
        steps = runs.get("steps")
        if not isinstance(steps, list):
            continue
        provenance_step = {
            "name": "Record candidate composite action revision",
            "shell": "bash",
            "env": {
                "CANDIDATE_ACTION_NAME": action_name,
                "CANDIDATE_ACTION_SHA": candidate_sha,
            },
            "run": (
                "set -euo pipefail\n"
                'mkdir -p "${RUNNER_TEMP}"\n'
                'printf \'%s=%s\\n\' "${CANDIDATE_ACTION_NAME}" "${CANDIDATE_ACTION_SHA}" >> '
                '"${RUNNER_TEMP}/gh-pipelines-candidate-actions.log"\n'
            ),
        }
        updated_steps = [provenance_step] + list(steps)
        if scenario == "action-regression" and action_name == "setup-go":
            updated_steps.append(
                {
                    "name": "Simulated affected composite action regression",
                    "shell": "bash",
                    "env": {"CANDIDATE_ACTION_SHA": candidate_sha},
                    "run": (
                        "set -euo pipefail\n"
                        'echo "::error::Simulated affected composite action regression in '
                        'actions/setup-go for candidate ${CANDIDATE_ACTION_SHA}"\n'
                        "exit 1\n"
                    ),
                }
            )
        runs["steps"] = updated_steps
        action_yml.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )


def _rewrite_action_refs_for_branch(
    text: str,
    consumer_repo: str,
    smoke_branch: str,
) -> str:
    def _replace(match: re.Match[str]) -> str:
        prefix, action_name, suffix = match.groups()
        return f"{prefix}{consumer_repo}/actions/{action_name}@{smoke_branch}{suffix}"

    return _CANDIDATE_ACTION_USES_RE.sub(_replace, text)


def _build_candidate_smoke_workflow(
    config: CandidateSmokeConfig,
    candidate_sha: str,
    smoke_branch: str,
) -> str:
    go_with: Dict[str, Any] = {
        "working-directory": "tests/fixtures/go",
        "coverage-min": "0",
        "coverage-artifact-name": "smoke-ci-go-coverage",
        "run-build": True,
        "runner-label": "ubuntu-latest",
    }
    if config.scenario == "nonexistent-input":
        go_with["nonexistent-caller-input"] = "true"

    jobs: Dict[str, Any] = {
        "smoke-ci-go": {
            "uses": f"{config.consumer_repo}/.github/workflows/candidate-ci-go.yml@{smoke_branch}",
            "with": go_with,
        },
        "smoke-ci-node": {
            "uses": f"{config.consumer_repo}/.github/workflows/candidate-ci-node.yml@{smoke_branch}",
            "with": {
                "working-directory": "tests/fixtures/node",
                "package-manager": "npm",
                "run-audit": False,
                "runner-label": "ubuntu-latest",
            },
        },
        "smoke-ci-python": {
            "uses": f"{config.consumer_repo}/.github/workflows/candidate-ci-python.yml@{smoke_branch}",
            "with": {
                "dependency-file": "tests/fixtures/python/requirements.txt",
                "lint-paths": "tests/fixtures/python/src tests/fixtures/python/tests",
                "test-paths": "tests/fixtures/python/tests",
                "run-audit": False,
                "runner-label": "ubuntu-latest",
            },
        },
        "smoke-ci-terraform": {
            "uses": f"{config.consumer_repo}/.github/workflows/candidate-ci-terraform.yml@{smoke_branch}",
            "with": {
                "working-directory": "tests/fixtures/terraform",
                "run-plan": False,
                "run-audit": False,
                "runner-label": "ubuntu-latest",
            },
        },
    }

    if config.include_cross_repo_call and config.scenario == "ordinary":
        jobs["smoke-cross-repo-ci-go"] = {
            "uses": f"{config.source_repo}/.github/workflows/ci-go.yml@{candidate_sha}",
            "with": {
                "working-directory": "tests/fixtures/go",
                "coverage-min": "0",
                "coverage-artifact-name": "smoke-cross-repo-go-coverage",
                "run-build": True,
                "runner-label": "ubuntu-latest",
            },
        }

    direct_steps: List[Dict[str, Any]] = [
        {
            "name": "Checkout synthetic consumer branch",
            "uses": "actions/checkout@v7",
            "with": {"persist-credentials": False},
        },
        {
            "name": "Test actions/setup-go (candidate revision)",
            "uses": f"{config.consumer_repo}/actions/setup-go@{smoke_branch}",
            "with": {"working-directory": "tests/fixtures/go"},
        },
        {
            "name": "Test actions/setup-node (candidate revision)",
            "uses": f"{config.consumer_repo}/actions/setup-node@{smoke_branch}",
            "with": {"package-manager": "npm"},
        },
        {
            "name": "Test actions/setup-python (candidate revision)",
            "uses": f"{config.consumer_repo}/actions/setup-python@{smoke_branch}",
            "with": {"dependency-file": "tests/fixtures/python/requirements.txt"},
        },
        {
            "name": "Test actions/setup-terraform (candidate revision)",
            "uses": f"{config.consumer_repo}/actions/setup-terraform@{smoke_branch}",
        },
        {
            "name": "Test actions/setup-helm (candidate revision)",
            "uses": f"{config.consumer_repo}/actions/setup-helm@{smoke_branch}",
        },
        {
            "name": "Test actions/docker-meta (candidate revision)",
            "id": "docker_meta",
            "uses": f"{config.consumer_repo}/actions/docker-meta@{smoke_branch}",
            "with": {"image-name": "ghcr.io/nandohdc/candidate-smoke-fixture"},
        },
        {
            "name": "Verify candidate composite action provenance and record runner metadata",
            "shell": "bash",
            "env": {
                "EXPECTED_CANDIDATE_SHA": candidate_sha,
                "RUNNER_NAME": "${{ runner.name }}",
                "RUNNER_OS": "${{ runner.os }}",
                "DOCKER_META_VERSION": "${{ steps.docker_meta.outputs.version }}",
            },
            "run": (
                "set -euo pipefail\n"
                'log="${RUNNER_TEMP}/gh-pipelines-candidate-actions.log"\n'
                'test -f "${log}"\n'
                f"for action in {' '.join(_DIRECT_ACTION_NAMES)}; do\n"
                '  grep -qx "${action}=${EXPECTED_CANDIDATE_SHA}" "${log}"\n'
                "done\n"
                'echo "Candidate direct composite actions verified for ${EXPECTED_CANDIDATE_SHA} '
                '(docker-meta version=${DOCKER_META_VERSION})"\n'
                'echo "Runner metadata: runner_name=${RUNNER_NAME} runner_os=${RUNNER_OS} '
                'image_os=${ImageOS:-unknown}"\n'
            ),
        },
    ]

    jobs["smoke-direct-actions"] = {
        "runs-on": "ubuntu-latest",
        "steps": direct_steps,
    }

    needed_jobs = list(jobs.keys())
    jobs["candidate-smoke-result"] = {
        "name": "candidate-smoke-result / ${{ inputs.candidate_sha }}",
        "needs": needed_jobs,
        "if": "always()",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Verify correlated candidate smoke result",
                "shell": "bash",
                "env": {
                    "EXPECTED_CANDIDATE_SHA": candidate_sha,
                    "INPUT_CANDIDATE_SHA": "${{ inputs.candidate_sha }}",
                    "REQUEST_ID": "${{ inputs.request_id }}",
                    "NEEDS_JSON": "${{ toJSON(needs) }}",
                    "RUNNER_NAME": "${{ runner.name }}",
                    "RUNNER_OS": "${{ runner.os }}",
                },
                "run": (
                    "set -euo pipefail\n"
                    'if [ "${INPUT_CANDIDATE_SHA}" != "${EXPECTED_CANDIDATE_SHA}" ]; then\n'
                    '  echo "::error::Candidate SHA mismatch: input=${INPUT_CANDIDATE_SHA}, '
                    'expected=${EXPECTED_CANDIDATE_SHA}"\n'
                    "  exit 1\n"
                    "fi\n"
                    "python3 -c '\n"
                    "import json, os, sys\n"
                    'needs = json.loads(os.environ["NEEDS_JSON"])\n'
                    'bad = {name: info.get("result") for name, info in needs.items() '
                    'if info.get("result") != "success"}\n'
                    'sha = os.environ["EXPECTED_CANDIDATE_SHA"]\n'
                    'req = os.environ["REQUEST_ID"]\n'
                    'runner = os.environ["RUNNER_NAME"]\n'
                    'image_os = os.environ.get("ImageOS", "unknown")\n'
                    "if bad:\n"
                    '    print(f"Candidate smoke result: status=failure candidate_sha={sha} '
                    'request_id={req} failed={bad}")\n'
                    "    sys.exit(1)\n"
                    'print(f"Candidate smoke result: status=success candidate_sha={sha} '
                    'request_id={req} runner_name={runner} image_os={image_os}")\n'
                    "'\n"
                ),
            }
        ],
    }

    doc = {
        "name": "candidate-smoke",
        "on": {
            "workflow_dispatch": {
                "inputs": {
                    "candidate_sha": {
                        "description": "Exact candidate commit SHA in source repository",
                        "required": True,
                        "type": "string",
                    },
                    "request_id": {
                        "description": "Unique candidate smoke request identifier",
                        "required": True,
                        "type": "string",
                    },
                }
            }
        },
        "permissions": {"contents": "read"},
        "jobs": jobs,
    }
    yaml = _require_yaml()
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def build_baseline_candidate_smoke_workflow() -> str:
    """Return the default-branch workflow_dispatch stub registered on consumer main."""
    doc = {
        "name": "candidate-smoke",
        "on": {
            "workflow_dispatch": {
                "inputs": {
                    "candidate_sha": {
                        "description": "Exact candidate commit SHA in source repository",
                        "required": True,
                        "type": "string",
                    },
                    "request_id": {
                        "description": "Unique candidate smoke request identifier",
                        "required": True,
                        "type": "string",
                    },
                }
            }
        },
        "permissions": {"contents": "read"},
        "jobs": {
            "baseline-guard": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {
                        "name": "Require ephemeral candidate smoke branch",
                        "shell": "bash",
                        "env": {"GITHUB_REF_NAME": "${{ github.ref_name }}"},
                        "run": (
                            "set -euo pipefail\n"
                            'echo "::error::candidate-smoke.yml must be dispatched from a '
                            'smoke/candidate-* branch, not ${GITHUB_REF_NAME}."\n'
                            "exit 1\n"
                        ),
                    }
                ],
            }
        },
    }
    yaml = _require_yaml()
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def prepare_candidate_smoke_tree(
    workspace: Path,
    config: CandidateSmokeConfig,
    smoke_branch: str,
) -> None:
    """Stage fixtures, candidate composite actions, and workflows inside workspace."""
    candidate_sha = _validate_sha(config.candidate_sha)
    if config.scenario not in ("ordinary", "nonexistent-input", "action-regression"):
        raise CandidateSmokeError(f"Unsupported candidate smoke scenario: {config.scenario}")

    src_fixtures = config.candidate_dir / "tests" / "fixtures"
    if not src_fixtures.is_dir():
        raise CandidateSmokeError(f"Missing fixtures directory at {src_fixtures}")
    dst_fixtures = workspace / "tests" / "fixtures"
    if dst_fixtures.exists():
        shutil.rmtree(dst_fixtures)
    shutil.copytree(src_fixtures, dst_fixtures)
    _ensure_helm_fixture(dst_fixtures)

    src_actions = config.candidate_dir / "actions"
    if not src_actions.is_dir():
        raise CandidateSmokeError(f"Missing actions directory at {src_actions}")
    _stage_composite_actions(src_actions, workspace / "actions", candidate_sha, config.scenario)

    src_workflows = config.candidate_dir / ".github" / "workflows"
    dst_workflows = workspace / ".github" / "workflows"
    dst_workflows.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in _CANDIDATE_WORKFLOW_FILES:
        src_path = src_workflows / src_name
        if not src_path.is_file():
            raise CandidateSmokeError(f"Missing candidate workflow at {src_path}")
        rewritten = _rewrite_action_refs_for_branch(
            src_path.read_text(encoding="utf-8"),
            config.consumer_repo,
            smoke_branch,
        )
        (dst_workflows / dst_name).write_text(rewritten, encoding="utf-8")

    (dst_workflows / CANDIDATE_SMOKE_WORKFLOW).write_text(
        _build_candidate_smoke_workflow(config, candidate_sha, smoke_branch),
        encoding="utf-8",
    )


def verify_candidate_smoke_run(
    run_data: Dict[str, Any],
    jobs_data: List[Dict[str, Any]],
    *,
    expected_candidate_sha: str,
    consumer_repo: str = DEFAULT_CONSUMER_REPO,
    smoke_branch: Optional[str] = None,
) -> CandidateSmokeOutcome:
    """Validate that a completed workflow_dispatch run proves expected_candidate_sha passed."""
    expected_sha = _validate_sha(expected_candidate_sha)
    run_id = int(run_data["id"]) if run_data.get("id") is not None else None
    run_url = str(run_data.get("html_url") or "") or None
    status = str(run_data.get("status") or "")
    conclusion = str(run_data.get("conclusion") or "")

    if status != "completed":
        raise CandidateSmokeError(
            f"run_incomplete: Candidate smoke run {run_id} in {consumer_repo} has status '{status}'."
        )

    result_shas: List[str] = []
    for job in jobs_data:
        name = str(job.get("name") or "")
        if name.startswith(_RESULT_JOB_PREFIX):
            result_shas.append(name[len(_RESULT_JOB_PREFIX) :].strip().lower())

    wrong_shas = [sha for sha in result_shas if sha != expected_sha]
    if wrong_shas and expected_sha not in result_shas:
        raise CandidateSmokeError(
            f"wrong_candidate_sha: Candidate smoke run {run_id} ran for candidate(s) "
            f"{', '.join(wrong_shas)}, expected {expected_sha}."
        )

    if conclusion != "success":
        raise CandidateSmokeError(
            f"run_failed: Candidate smoke run {run_id} for {expected_sha} in {consumer_repo} "
            f"completed with conclusion '{conclusion}'."
        )

    expected_result_name = f"{_RESULT_JOB_PREFIX}{expected_sha}"
    result_job = next(
        (
            job
            for job in jobs_data
            if str(job.get("name") or "").lower() == expected_result_name.lower()
        ),
        None,
    )
    if result_job is None or result_job.get("status") != "completed" or result_job.get("conclusion") != "success":
        raise CandidateSmokeError(
            f"missing_result_job: Candidate smoke run {run_id} lacks passing '{expected_result_name}' job."
        )

    verified_names: List[str] = []
    for job in jobs_data:
        job_name = str(job.get("name") or "")
        job_conclusion = str(job.get("conclusion") or "")
        if job.get("status") != "completed" or job_conclusion not in ("success", "skipped"):
            raise CandidateSmokeError(
                f"run_failed: Job '{job_name}' in run {run_id} ended with '{job_conclusion}'."
            )
        if job_conclusion == "success":
            verified_names.append(job_name)

    for prefix in _CORE_REQUIRED_PREFIXES:
        has_success = any(
            name == prefix or name.startswith(f"{prefix} ") or name.startswith(f"{prefix} /")
            for name in verified_names
        )
        if not has_success:
            raise CandidateSmokeError(
                f"missing_smoke_job: Candidate smoke run {run_id} missing successful '{prefix}' job."
            )

    return CandidateSmokeOutcome(
        status="passed",
        reason="verified",
        candidate_sha=expected_sha,
        consumer_repo=consumer_repo,
        run_id=run_id,
        run_url=run_url,
        smoke_branch=smoke_branch or run_data.get("head_branch"),
        verified_jobs=tuple(verified_names),
    )


def find_verified_candidate_smoke_run(
    github: ConsumerGitHubClient,
    consumer_repo: str,
    candidate_sha: str,
    *,
    per_page: int = 20,
) -> Optional[CandidateSmokeOutcome]:
    """Return the first completed passing Candidate smoke outcome tied to candidate_sha."""
    expected_sha = _validate_sha(candidate_sha)
    runs = github.list_workflow_runs(
        consumer_repo,
        CANDIDATE_SMOKE_WORKFLOW,
        event="workflow_dispatch",
        per_page=per_page,
    )
    for run in runs:
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        run_id = run.get("id")
        if run_id is None:
            continue
        jobs = github.list_run_jobs(consumer_repo, int(run_id))
        try:
            return verify_candidate_smoke_run(
                run,
                jobs,
                expected_candidate_sha=expected_sha,
                consumer_repo=consumer_repo,
            )
        except CandidateSmokeError:
            continue
    return None


def _make_git_askpass(tmp_path: Path, token: str) -> Dict[str, str]:
    askpass = tmp_path / "git-askpass.sh"
    askpass.write_text("#!/bin/sh\necho \"$CANDIDATE_SMOKE_GIT_TOKEN\"\n", encoding="utf-8")
    askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    env = dict(os.environ)
    env["CANDIDATE_SMOKE_GIT_TOKEN"] = token
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _delete_remote_branch(
    workdir: Path,
    branch: str,
    git_env: Dict[str, str],
    token: Optional[str],
) -> None:
    subprocess.run(
        ["git", "push", "origin", "--delete", branch],
        cwd=workdir,
        env=git_env,
        capture_output=True,
        text=True,
    )


def _poll_dispatched_run(
    github: ConsumerGitHubClient,
    config: CandidateSmokeConfig,
    smoke_branch: str,
    smoke_commit_sha: Optional[str],
) -> Dict[str, Any]:
    deadline = time.monotonic() + config.timeout_seconds
    while True:
        runs = github.list_workflow_runs(
            config.consumer_repo,
            CANDIDATE_SMOKE_WORKFLOW,
            branch=smoke_branch,
            event="workflow_dispatch",
            per_page=10,
        )
        matching = [
            run
            for run in runs
            if smoke_commit_sha is None or str(run.get("head_sha") or "").lower() == smoke_commit_sha.lower()
        ]
        if matching:
            latest = matching[0]
            if latest.get("status") == "completed":
                return latest
        if time.monotonic() >= deadline:
            raise CandidateSmokeError(
                f"timeout: Candidate smoke run on {config.consumer_repo}@{smoke_branch} did not "
                f"complete within {config.timeout_seconds}s."
            )
        if config.poll_interval_seconds > 0:
            time.sleep(config.poll_interval_seconds)


def run_candidate_smoke(
    config: CandidateSmokeConfig,
    github: ConsumerGitHubClient,
    *,
    remote_url: Optional[str] = None,
    token: Optional[str] = None,
) -> CandidateSmokeOutcome:
    """Stage candidate artifacts on an ephemeral consumer branch, execute, verify, and clean up."""
    candidate_sha = _validate_sha(config.candidate_sha)
    request_id = config.request_id.strip() or f"req-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    if not _SAFE_ID_RE.match(request_id):
        raise CandidateSmokeError(f"Invalid request_id '{request_id}'.")

    smoke_branch = f"smoke/candidate-{candidate_sha[:12]}-{request_id}"
    resolved_remote = remote_url or f"https://x-access-token@github.com/{config.consumer_repo}.git"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        git_env = _make_git_askpass(tmp_path, token) if token else dict(os.environ)
        consumer_dir = tmp_path / "consumer"
        _run_cmd(
            ["git", "clone", "--depth", "1", "--branch", "main", resolved_remote, str(consumer_dir)],
            env=git_env,
            token=token,
        )
        _run_cmd(["git", "config", "user.name", "github-actions[bot]"], cwd=consumer_dir, env=git_env)
        _run_cmd(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            cwd=consumer_dir,
            env=git_env,
        )
        _run_cmd(["git", "checkout", "-b", smoke_branch], cwd=consumer_dir, env=git_env)

        prepare_candidate_smoke_tree(consumer_dir, config, smoke_branch)
        _run_cmd(["git", "add", "-A"], cwd=consumer_dir, env=git_env)
        _run_cmd(
            ["git", "commit", "-m", f"chore(smoke): stage {candidate_sha[:12]} ({request_id})"],
            cwd=consumer_dir,
            env=git_env,
        )
        smoke_commit_sha = _run_cmd(
            ["git", "rev-parse", "HEAD"], cwd=consumer_dir, env=git_env
        ).strip()

        pushed = False
        try:
            _run_cmd(
                ["git", "push", "-u", "origin", smoke_branch],
                cwd=consumer_dir,
                env=git_env,
                token=token,
            )
            pushed = True
            github.dispatch_workflow(
                config.consumer_repo,
                CANDIDATE_SMOKE_WORKFLOW,
                smoke_branch,
                {"candidate_sha": candidate_sha, "request_id": request_id},
            )
            run_data = _poll_dispatched_run(
                github,
                config,
                smoke_branch,
                None if remote_url else smoke_commit_sha,
            )
            run_id = int(run_data["id"])
            jobs = github.list_run_jobs(config.consumer_repo, run_id)
            return verify_candidate_smoke_run(
                run_data,
                jobs,
                expected_candidate_sha=candidate_sha,
                consumer_repo=config.consumer_repo,
                smoke_branch=smoke_branch,
            )
        finally:
            if pushed:
                _delete_remote_branch(consumer_dir, smoke_branch, git_env, token)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for executing or verifying Synthetic consumer repository runs."""
    parser = argparse.ArgumentParser(
        description="Execute or verify candidate smoke in the Synthetic consumer repository."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for sub_name in ("run", "verify"):
        sub = subparsers.add_parser(sub_name)
        sub.add_argument("--candidate-sha", required=True, help="40-hex candidate commit SHA")
        sub.add_argument("--source-repo", default=DEFAULT_SOURCE_REPO)
        sub.add_argument("--consumer-repo", default=DEFAULT_CONSUMER_REPO)
        sub.add_argument(
            "--allow-gh-token",
            action="store_true",
            help="Allow falling back to local GH_TOKEN / gh auth token for maintainer runs.",
        )
        if sub_name == "run":
            sub.add_argument("--candidate-dir", default=".")
            sub.add_argument("--request-id", default="")
            sub.add_argument(
                "--scenario",
                default="ordinary",
                choices=("ordinary", "nonexistent-input", "action-regression"),
            )
            sub.add_argument("--no-cross-repo-call", action="store_true")
            sub.add_argument("--timeout-seconds", type=int, default=600)
            sub.add_argument("--poll-interval-seconds", type=int, default=10)

    args = parser.parse_args(argv)
    try:
        token = resolve_consumer_token(allow_gh_token=args.allow_gh_token)
        github = GhCliConsumerGitHub(token=token)
        if args.command == "verify":
            outcome = find_verified_candidate_smoke_run(
                github, args.consumer_repo, args.candidate_sha
            )
            if outcome is None:
                raise CandidateSmokeError(
                    f"no_verified_candidate_smoke_run: No completed passing candidate smoke run "
                    f"for {args.candidate_sha} in {args.consumer_repo}."
                )
        else:
            config = CandidateSmokeConfig(
                candidate_sha=args.candidate_sha,
                source_repo=args.source_repo,
                consumer_repo=args.consumer_repo,
                candidate_dir=Path(args.candidate_dir),
                request_id=args.request_id,
                scenario=args.scenario,
                include_cross_repo_call=not args.no_cross_repo_call,
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
            )
            outcome = run_candidate_smoke(config, github, token=token)
        print(
            f"Candidate smoke: status={outcome.status} reason={outcome.reason} "
            f"candidate_sha={outcome.candidate_sha} consumer_repo={outcome.consumer_repo} "
            f"run_id={outcome.run_id} run_url={outcome.run_url}"
        )
        return 0
    except CandidateSmokeError as exc:
        msg = str(exc)
        status = "unprovisioned" if "unprovisioned" in msg else "failed"
        print(f"Candidate smoke: status={status} detail={msg}", file=sys.stderr)
        return 2 if status == "unprovisioned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
