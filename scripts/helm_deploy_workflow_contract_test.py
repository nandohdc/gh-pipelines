#!/usr/bin/env python3
"""Contract and executable behavior tests for `.github/workflows/helm-deploy.yml` (#187, #188)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_not_contains, assert_order, read
from scripts.helm_environment import HelmEnvironmentError, verify_protected_environment

HELM_DEPLOY = ROOT / ".github" / "workflows" / "helm-deploy.yml"
HELM_ENVIRONMENT_SCRIPT = ROOT / "scripts" / "helm_environment.py"
SETUP_DOCTL = ROOT / "actions" / "setup-doctl" / "action.yml"
HELM_EXAMPLE = ROOT / "examples" / "helm-deploy.yml"
HELM_MANUAL_EXAMPLE = ROOT / "examples" / "helm-deploy-manual.yml"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "USAGE.md"
CONTRIBUTING = ROOT / "docs" / "CONTRIBUTING.md"
CODEX_CONTEXT = ROOT / ".codex" / "CONTEXT.md"


@dataclass(frozen=True)
class SimulationResult:
    """Observed outputs and recorded external commands from one simulated workflow run."""

    authorized: bool
    authorize_returncode: int
    authorize_stdout: str
    authorize_stderr: str
    outputs: Dict[str, str]
    commands: List[List[str]]
    verify_returncode: int | None = None
    deploy_returncode: int | None = None
    post_deploy_returncode: int | None = None


class FakeEnvironmentClient:
    """In-memory adapter stubbing GitHub Environment REST responses."""

    def __init__(
        self,
        environments: Optional[Dict[str, Dict[str, Any]]] = None,
        branch_policies: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self.environments = dict(environments or {})
        self.branch_policies = dict(branch_policies or {})

    def get_environment(self, repo: str, environment: str) -> Optional[Dict[str, Any]]:
        return self.environments.get(f"{repo}:{environment}")

    def list_deployment_branch_policies(
        self, repo: str, environment: str
    ) -> List[Dict[str, Any]]:
        return list(self.branch_policies.get(f"{repo}:{environment}", []))


def _load_workflow() -> Dict[str, Any]:
    return yaml.load(read(HELM_DEPLOY), Loader=yaml.BaseLoader)


def _step_map(job_id: str) -> Dict[str, Dict[str, Any]]:
    data = _load_workflow()
    steps = data["jobs"][job_id]["steps"]
    return {str(step["name"]): step for step in steps if isinstance(step, dict) and "name" in step}


def _parse_kv_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip()
    return values


def _write_stub_binary(bin_dir: Path, name: str, log_file: Path) -> None:
    script = (
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"log_path = {str(log_file)!r}\n"
        "with open(log_path, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
    )
    target = bin_dir / name
    target.write_text(script, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _read_command_log(log_file: Path) -> List[List[str]]:
    if not log_file.is_file():
        return []
    return [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _setup_doctl_script() -> str:
    data = yaml.load(read(SETUP_DOCTL), Loader=yaml.BaseLoader)
    for step in data["runs"]["steps"]:
        if isinstance(step, dict) and "doctl kubernetes cluster kubeconfig save" in str(step.get("run", "")):
            return str(step["run"])
    raise AssertionError("actions/setup-doctl/action.yml missing doctl kubeconfig save step")


def _build_base_env(
    *,
    bin_dir: Path,
    github_output: Path,
    github_env: Path,
    event_name: str,
    ref: str,
    default_branch: str,
    repository: str,
    head_repo: str,
    head_fork: str,
    allow_manual_deploy: str,
    environment_input: str,
) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_ENV": str(github_env),
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_REF_FULL": ref,
            "DEFAULT_BRANCH": default_branch,
            "GITHUB_REPOSITORY_NAME": repository,
            "PULL_REQUEST_HEAD_REPOSITORY": head_repo,
            "PULL_REQUEST_HEAD_FORK": head_fork,
            "ALLOW_MANUAL_DEPLOY": allow_manual_deploy,
            "DEPLOY_ENVIRONMENT": environment_input,
            "INPUT_ENVIRONMENT": environment_input,
            "CLUSTER_ID": "cluster-nonprod-0000",
        }
    )
    return env


def _simulate_helm_workflow(
    *,
    event_name: str,
    ref: str,
    default_branch: str = "main",
    repository: str = "nandohdc/sample-app",
    head_repo: str = "",
    head_fork: str = "",
    allow_manual_deploy: str = "false",
    environment_input: str = "",
    environment_approved: bool = True,
    chart_version: str = "",
    extra_helm_args: str = "",
    post_deploy_kubectl: str = "kubectl get pods -n my-app",
) -> SimulationResult:
    """Execute the real helm-deploy.yml bash bodies across authorize and deploy jobs."""
    auth_steps = _step_map("authorize")
    deploy_steps = _step_map("deploy")
    auth_run = str(auth_steps["Authorize deployment context"]["run"])
    verify_run = str(deploy_steps["Verify deployment authorization"]["run"])
    deploy_run = str(deploy_steps["Deploy (or dry-run on PR)"]["run"])
    post_run = str(deploy_steps["Post-deploy kubectl commands"]["run"])
    doctl_run = _setup_doctl_script()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        log_file = tmp_path / "commands.log"
        auth_output = tmp_path / "auth_output.txt"
        verify_output = tmp_path / "verify_output.txt"
        github_env = tmp_path / "github_env.txt"
        auth_output.write_text("", encoding="utf-8")
        verify_output.write_text("", encoding="utf-8")
        github_env.write_text("", encoding="utf-8")

        for tool in ("doctl", "helm", "kubectl"):
            _write_stub_binary(bin_dir, tool, log_file)

        env = _build_base_env(
            bin_dir=bin_dir,
            github_output=auth_output,
            github_env=github_env,
            event_name=event_name,
            ref=ref,
            default_branch=default_branch,
            repository=repository,
            head_repo=head_repo,
            head_fork=head_fork,
            allow_manual_deploy=allow_manual_deploy,
            environment_input=environment_input,
        )

        auth_proc = subprocess.run(
            ["bash", "-c", auth_run],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        outputs = _parse_kv_file(auth_output)
        if auth_proc.returncode != 0:
            return SimulationResult(
                authorized=False,
                authorize_returncode=auth_proc.returncode,
                authorize_stdout=auth_proc.stdout,
                authorize_stderr=auth_proc.stderr,
                outputs=outputs,
                commands=_read_command_log(log_file),
            )

        mode = outputs.get("mode", "")
        manual_deploy = outputs.get("manual-deploy", "false")
        acquire_creds = outputs.get("acquire-credentials", "false")
        allow_post = outputs.get("allow-post-deploy", "false")
        resolved_env = outputs.get("environment", "")

        # Simulate GitHub Environment approval gate holding the `deploy` job before any step runs.
        if resolved_env and not environment_approved:
            return SimulationResult(
                authorized=True,
                authorize_returncode=auth_proc.returncode,
                authorize_stdout=auth_proc.stdout,
                authorize_stderr=auth_proc.stderr,
                outputs=outputs,
                commands=_read_command_log(log_file),
            )

        verify_env = dict(
            env,
            GITHUB_OUTPUT=str(verify_output),
            AUTHORIZED_MODE=mode,
            AUTHORIZED_MANUAL_DEPLOY=manual_deploy,
            AUTHORIZED_ACQUIRE_CREDENTIALS=acquire_creds,
            AUTHORIZED_ALLOW_POST_DEPLOY=allow_post,
            AUTHORIZED_ENVIRONMENT=resolved_env,
        )
        verify_proc = subprocess.run(
            ["bash", "-c", verify_run],
            env=verify_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if verify_proc.returncode != 0:
            return SimulationResult(
                authorized=True,
                authorize_returncode=auth_proc.returncode,
                authorize_stdout=auth_proc.stdout,
                authorize_stderr=auth_proc.stderr,
                outputs=outputs,
                commands=_read_command_log(log_file),
                verify_returncode=verify_proc.returncode,
            )

        verified = _parse_kv_file(verify_output)
        v_mode = verified.get("mode", "")
        v_manual = verified.get("manual-deploy", "false")
        v_creds = verified.get("acquire-credentials", "false")
        v_post = verified.get("allow-post-deploy", "false")
        v_env = verified.get("environment", "")

        if v_creds == "true" and v_mode in ("deploy", "dry-run"):
            subprocess.run(["bash", "-c", doctl_run], env=env, check=True, capture_output=True, text=True)

        deploy_env = dict(
            env,
            HELM_RELEASE_NAME="my-app",
            HELM_CHART="./chart",
            HELM_NAMESPACE="my-app",
            HELM_VALUES_FILE="values.yaml",
            HELM_TIMEOUT="10m",
            HELM_CHART_VERSION=chart_version,
            HELM_EXTRA_ARGS=extra_helm_args,
            HELM_DEPLOY_MODE=v_mode,
            HELM_MANUAL_DEPLOY=v_manual,
            HELM_ENVIRONMENT=v_env,
        )

        deploy_rc: int | None = None
        if v_mode in ("deploy", "dry-run"):
            deploy_proc = subprocess.run(
                ["bash", "-c", deploy_run],
                env=deploy_env,
                capture_output=True,
                text=True,
                check=False,
            )
            deploy_rc = deploy_proc.returncode

        post_rc: int | None = None
        if v_mode == "deploy" and v_post == "true" and post_deploy_kubectl != "":
            post_env = dict(
                deploy_env,
                HELM_DEPLOY_MODE=v_mode,
                HELM_ALLOW_POST_DEPLOY=v_post,
                POST_DEPLOY_KUBECTL=post_deploy_kubectl,
            )
            post_proc = subprocess.run(
                ["bash", "-c", post_run],
                env=post_env,
                capture_output=True,
                text=True,
                check=False,
            )
            post_rc = post_proc.returncode

        return SimulationResult(
            authorized=True,
            authorize_returncode=auth_proc.returncode,
            authorize_stdout=auth_proc.stdout,
            authorize_stderr=auth_proc.stderr,
            outputs=outputs,
            commands=_read_command_log(log_file),
            verify_returncode=verify_proc.returncode,
            deploy_returncode=deploy_rc,
            post_deploy_returncode=post_rc,
        )


def test_helm_deploy_workflow_structure_and_guards() -> None:
    text = read(HELM_DEPLOY)
    assert_order(
        text,
        "\njobs:\n",
        "\n  authorize:",
        "- name: Authorize deployment context",
        "\n  deploy:",
        "- name: Checkout",
        "- name: Verify deployment authorization",
        "- name: Setup doctl + kubeconfig",
        "- name: Setup Helm + repos",
        "- name: Deploy (or dry-run on PR)",
        "- name: Post-deploy kubectl commands",
    )

    assert_not_contains(
        text,
        "github.event_name != 'pull_request'",
        "helm-deploy.yml must not use event-only exclusion bypasses for post-deploy commands",
    )

    data = _load_workflow()
    workflow_call = data["on"]["workflow_call"]
    expected_inputs = {
        "release-name",
        "chart",
        "namespace",
        "values-file",
        "chart-version",
        "cluster-id",
        "timeout",
        "extra-helm-args",
        "post-deploy-kubectl",
        "extra-helm-repos",
        "runner-label",
        "allow-manual-deploy",
        "environment",
    }
    assert expected_inputs.issubset(set(workflow_call["inputs"].keys())), (
        f"missing expected inputs in helm-deploy.yml: {expected_inputs - set(workflow_call['inputs'].keys())}"
    )
    assert workflow_call["inputs"]["allow-manual-deploy"]["type"] == "boolean"
    assert workflow_call["inputs"]["allow-manual-deploy"]["default"] == "false"
    assert workflow_call["inputs"]["environment"]["type"] == "string"
    assert workflow_call["inputs"]["environment"]["default"] == ""
    assert "digitalocean-token" in workflow_call["secrets"], "missing digitalocean-token secret"
    assert data["permissions"] == {"contents": "read"}, "helm-deploy.yml must keep least-privilege permissions"

    auth_job = data["jobs"]["authorize"]
    assert "environment" not in auth_job, "authorize job must not hold environment gate before validation"
    assert "digitalocean-token" not in yaml.dump(auth_job), "authorize job must not receive deployment secret"
    assert "inputs.runner-label" in str(auth_job["runs-on"])

    deploy_job = data["jobs"]["deploy"]
    assert deploy_job["needs"] == "authorize"
    assert "needs.authorize.outputs.environment" in str(deploy_job.get("environment", ""))
    assert "inputs.runner-label" in str(deploy_job["runs-on"]), "must preserve downstream runner-label support"
    assert "deploy (dry-run)" in str(deploy_job["name"]), "job name must distinguish dry-run from real deploy"

    for job_id in ("authorize", "deploy"):
        for step_name, step in _step_map(job_id).items():
            if "run" in step:
                assert "${{" not in str(step["run"]), (
                    f"helm-deploy.yml {job_id}.{step_name} must pass context/inputs via env"
                )

    deploy_steps = _step_map("deploy")
    setup_doctl_step = deploy_steps["Setup doctl + kubeconfig"]
    setup_helm_step = deploy_steps["Setup Helm + repos"]
    assert str(setup_doctl_step["uses"]).endswith("@v2"), "v2 workflow must use @v2 setup-doctl"
    assert str(setup_helm_step["uses"]).endswith("@v2"), "v2 workflow must use @v2 setup-helm"

    setup_doctl_if = str(setup_doctl_step.get("if", ""))
    setup_helm_if = str(setup_helm_step.get("if", ""))
    deploy_if = str(deploy_steps["Deploy (or dry-run on PR)"].get("if", ""))
    post_if = str(deploy_steps["Post-deploy kubectl commands"].get("if", ""))

    assert_contains(setup_doctl_if, "steps.verify_auth.outputs.acquire-credentials == 'true'", "doctl step must require verified credential approval")
    assert_contains(setup_doctl_if, "steps.verify_auth.outputs.mode == 'deploy'", "doctl step must check verified mode")
    assert_contains(setup_helm_if, "steps.verify_auth.outputs.mode == 'deploy'", "setup-helm must check verified mode")
    assert_contains(deploy_if, "steps.verify_auth.outputs.mode == 'deploy'", "deploy step must check verified mode")
    assert_contains(post_if, "steps.verify_auth.outputs.mode == 'deploy'", "post-deploy step must require deploy mode")
    assert_contains(post_if, "steps.verify_auth.outputs.allow-post-deploy == 'true'", "post-deploy step must require allow-post-deploy")


def test_default_branch_push_executes_real_deploy_and_post_deploy() -> None:
    for branch in ("main", "trunk"):
        result = _simulate_helm_workflow(
            event_name="push",
            ref=f"refs/heads/{branch}",
            default_branch=branch,
            chart_version="1.2.3",
            extra_helm_args="--wait --cleanup-on-fail",
            post_deploy_kubectl="kubectl apply -f a.yaml\nkubectl rollout status deploy/my-app",
        )
        assert result.authorized and result.verify_returncode == 0
        assert result.outputs["mode"] == "deploy"
        assert result.outputs["manual-deploy"] == "false"
        assert result.outputs["acquire-credentials"] == "true"
        assert result.outputs["allow-post-deploy"] == "true"
        assert result.outputs["environment"] == ""
        assert result.deploy_returncode == 0
        assert result.post_deploy_returncode == 0

        helm_cmds = [cmd for cmd in result.commands if cmd[0] == "helm"]
        doctl_cmds = [cmd for cmd in result.commands if cmd[0] == "doctl"]
        kubectl_cmds = [cmd for cmd in result.commands if cmd[0] == "kubectl"]
        assert doctl_cmds == [
            ["doctl", "kubernetes", "cluster", "kubeconfig", "save", "cluster-nonprod-0000"]
        ], f"expected doctl kubeconfig save, got {doctl_cmds}"
        assert len(helm_cmds) == 1
        assert "--dry-run" not in helm_cmds[0]
        assert "--version" in helm_cmds[0] and "1.2.3" in helm_cmds[0]
        assert "--cleanup-on-fail" in helm_cmds[0]
        assert kubectl_cmds == [
            ["kubectl", "apply", "-f", "a.yaml"],
            ["kubectl", "rollout", "status", "deploy/my-app"],
        ]


def test_same_repo_pull_request_runs_dry_run_and_skips_post_deploy() -> None:
    result = _simulate_helm_workflow(
        event_name="pull_request",
        ref="refs/pull/42/merge",
        default_branch="main",
        repository="nandohdc/sample-app",
        head_repo="nandohdc/sample-app",
        head_fork="false",
        allow_manual_deploy="true",
        environment_input="production",
        post_deploy_kubectl="kubectl apply -f dangerous.yaml",
    )
    assert result.authorized and result.verify_returncode == 0
    assert result.outputs["mode"] == "dry-run"
    assert result.outputs["manual-deploy"] == "false"
    assert result.outputs["environment"] == ""
    assert result.outputs["allow-post-deploy"] == "false"
    assert result.deploy_returncode == 0
    assert result.post_deploy_returncode is None

    helm_cmds = [cmd for cmd in result.commands if cmd[0] == "helm"]
    kubectl_cmds = [cmd for cmd in result.commands if cmd[0] == "kubectl"]
    assert len(helm_cmds) == 1
    assert "--dry-run" in helm_cmds[0] and "--debug" in helm_cmds[0]
    assert kubectl_cmds == []


def test_protected_manual_dispatch_contract_pending_rejected_and_approved() -> None:
    # Pending or rejected environment approval halts before deploy job acquires credentials or runs Helm/kubectl.
    pending = _simulate_helm_workflow(
        event_name="workflow_dispatch",
        ref="refs/heads/main",
        default_branch="main",
        allow_manual_deploy="true",
        environment_input="nonprod-manual-deploy",
        environment_approved=False,
        post_deploy_kubectl="kubectl rollout status deploy/my-app",
    )
    assert pending.authorized
    assert pending.outputs["mode"] == "deploy"
    assert pending.outputs["manual-deploy"] == "true"
    assert pending.outputs["environment"] == "nonprod-manual-deploy"
    assert pending.commands == [], f"pending/rejected approval must execute zero commands: {pending.commands}"

    # Approved default-branch manual dispatch executes real deploy and post-deploy under the same guards.
    approved = _simulate_helm_workflow(
        event_name="workflow_dispatch",
        ref="refs/heads/main",
        default_branch="main",
        allow_manual_deploy="true",
        environment_input="nonprod-manual-deploy",
        environment_approved=True,
        post_deploy_kubectl="kubectl rollout status deploy/my-app",
    )
    assert approved.authorized and approved.verify_returncode == 0
    assert approved.outputs["mode"] == "deploy"
    assert approved.outputs["manual-deploy"] == "true"
    assert approved.outputs["environment"] == "nonprod-manual-deploy"
    assert approved.deploy_returncode == 0
    assert approved.post_deploy_returncode == 0
    assert [c[0] for c in approved.commands] == ["doctl", "helm", "kubectl"]
    assert "--dry-run" not in approved.commands[1]
    assert approved.commands[2] == ["kubectl", "rollout", "status", "deploy/my-app"]


def test_unauthorized_events_and_manual_misconfigurations_fail_before_credentials() -> None:
    blocked_cases = [
        ("feature push", {"event_name": "push", "ref": "refs/heads/feat/unsafe", "default_branch": "main"}),
        ("release branch push", {"event_name": "push", "ref": "refs/heads/release/v1", "default_branch": "main"}),
        ("tag push", {"event_name": "push", "ref": "refs/tags/v2.0.0", "default_branch": "main"}),
        ("empty default branch", {"event_name": "push", "ref": "refs/heads/main", "default_branch": ""}),
        ("multiline environment", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "true", "environment_input": "prod\nother"}),
        ("whitespace environment", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "true", "environment_input": "   "}),
        ("unopted manual dispatch (no env)", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "false", "environment_input": ""}),
        ("unopted manual dispatch (with env)", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "false", "environment_input": "production"}),
        ("opted-in manual dispatch missing env", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "true", "environment_input": ""}),
        ("feature branch manual dispatch (opted-in with env)", {"event_name": "workflow_dispatch", "ref": "refs/heads/feat/manual", "default_branch": "main", "allow_manual_deploy": "true", "environment_input": "production"}),
        (
            "fork PR (fork=true)",
            {
                "event_name": "pull_request",
                "ref": "refs/pull/7/merge",
                "default_branch": "main",
                "repository": "nandohdc/sample-app",
                "head_repo": "fork-user/sample-app",
                "head_fork": "true",
            },
        ),
        (
            "fork PR (cross-repo without flag)",
            {
                "event_name": "pull_request",
                "ref": "refs/pull/8/merge",
                "default_branch": "main",
                "repository": "nandohdc/sample-app",
                "head_repo": "other-org/sample-app",
                "head_fork": "false",
            },
        ),
        (
            "pull_request missing head repo",
            {
                "event_name": "pull_request",
                "ref": "refs/pull/9/merge",
                "default_branch": "main",
                "repository": "nandohdc/sample-app",
                "head_repo": "",
                "head_fork": "",
            },
        ),
        ("pull_request_target (even opted-in)", {"event_name": "pull_request_target", "ref": "refs/heads/main", "default_branch": "main", "allow_manual_deploy": "true", "environment_input": "production"}),
        ("schedule", {"event_name": "schedule", "ref": "refs/heads/main", "default_branch": "main"}),
    ]

    for label, kwargs in blocked_cases:
        result = _simulate_helm_workflow(**kwargs)
        assert not result.authorized, f"{label} unexpectedly authorized"
        assert result.authorize_returncode != 0, f"{label} expected non-zero exit"
        assert result.commands == [], f"{label} executed external commands despite rejection: {result.commands}"


def test_step_scripts_fail_closed_when_tampered_or_bypassed() -> None:
    deploy_steps = _step_map("deploy")
    verify_run = str(deploy_steps["Verify deployment authorization"]["run"])
    deploy_run = str(deploy_steps["Deploy (or dry-run on PR)"]["run"])
    post_run = str(deploy_steps["Post-deploy kubectl commands"]["run"])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        log_file = tmp_path / "commands.log"
        gh_out = tmp_path / "verify_out.txt"
        for tool in ("helm", "kubectl"):
            _write_stub_binary(bin_dir, tool, log_file)

        base_env = os.environ.copy()
        base_env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{base_env.get('PATH', '')}",
                "GITHUB_OUTPUT": str(gh_out),
                "HELM_RELEASE_NAME": "my-app",
                "HELM_CHART": "./chart",
                "HELM_NAMESPACE": "my-app",
                "HELM_VALUES_FILE": "values.yaml",
                "HELM_TIMEOUT": "10m",
                "HELM_CHART_VERSION": "",
                "HELM_EXTRA_ARGS": "",
                "POST_DEPLOY_KUBECTL": "kubectl apply -f bypass.yaml",
            }
        )

        # verify_auth must reject tampered handoff states.
        bad_verify_cases = [
            {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_FULL": "refs/heads/main", "DEFAULT_BRANCH": "main", "ALLOW_MANUAL_DEPLOY": "false", "INPUT_ENVIRONMENT": "prod", "AUTHORIZED_MODE": "deploy", "AUTHORIZED_MANUAL_DEPLOY": "true", "AUTHORIZED_ACQUIRE_CREDENTIALS": "true", "AUTHORIZED_ALLOW_POST_DEPLOY": "true", "AUTHORIZED_ENVIRONMENT": "prod"},
            {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_FULL": "refs/heads/main", "DEFAULT_BRANCH": "main", "ALLOW_MANUAL_DEPLOY": "true", "INPUT_ENVIRONMENT": "prod", "AUTHORIZED_MODE": "deploy", "AUTHORIZED_MANUAL_DEPLOY": "true", "AUTHORIZED_ACQUIRE_CREDENTIALS": "true", "AUTHORIZED_ALLOW_POST_DEPLOY": "true", "AUTHORIZED_ENVIRONMENT": ""},
            {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_FULL": "refs/heads/main", "DEFAULT_BRANCH": "main", "ALLOW_MANUAL_DEPLOY": "true", "INPUT_ENVIRONMENT": "prod", "AUTHORIZED_MODE": "deploy", "AUTHORIZED_MANUAL_DEPLOY": "true", "AUTHORIZED_ACQUIRE_CREDENTIALS": "true", "AUTHORIZED_ALLOW_POST_DEPLOY": "true", "AUTHORIZED_ENVIRONMENT": "staging"},
            {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_REF_FULL": "refs/heads/main", "DEFAULT_BRANCH": "main", "ALLOW_MANUAL_DEPLOY": "true", "INPUT_ENVIRONMENT": "prod", "AUTHORIZED_MODE": "deploy", "AUTHORIZED_MANUAL_DEPLOY": "true", "AUTHORIZED_ACQUIRE_CREDENTIALS": "true", "AUTHORIZED_ALLOW_POST_DEPLOY": "true", "AUTHORIZED_ENVIRONMENT": "prod"},
        ]
        for case in bad_verify_cases:
            proc = subprocess.run(["bash", "-c", verify_run], env=dict(base_env, **case), capture_output=True, text=True, check=False)
            assert proc.returncode != 0, f"verify_auth accepted tampered state: {case}"

        for bad_mode in ("", "rejected"):
            env = dict(base_env, HELM_DEPLOY_MODE=bad_mode, HELM_ALLOW_POST_DEPLOY="true")
            deploy_proc = subprocess.run(["bash", "-c", deploy_run], env=env, capture_output=True, text=True, check=False)
            assert deploy_proc.returncode != 0, f"deploy script accepted mode={bad_mode!r}"

        for mode, allow in (("dry-run", "false"), ("dry-run", "true"), ("deploy", "false"), ("", "true")):
            env = dict(base_env, HELM_DEPLOY_MODE=mode, HELM_ALLOW_POST_DEPLOY=allow)
            post_proc = subprocess.run(["bash", "-c", post_run], env=env, capture_output=True, text=True, check=False)
            assert post_proc.returncode != 0, f"post-deploy script accepted mode={mode!r} allow={allow!r}"

        assert _read_command_log(log_file) == [], "unauthorized direct step calls must not invoke helm or kubectl"


def test_consumer_environment_protection_contract() -> None:
    repo = "nandohdc/sample-consumer"

    # 1. Missing environment -> unprovisioned
    client_missing = FakeEnvironmentClient()
    res = verify_protected_environment(client_missing, repository=repo, environment="prod")
    assert not res.verified and res.status == "unprovisioned" and res.reason == "environment_not_found"

    # 2. Named environment with no reviewers -> unprotected
    client_unprotected = FakeEnvironmentClient(
        environments={f"{repo}:prod": {"name": "prod", "protection_rules": [], "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}}}
    )
    res = verify_protected_environment(client_unprotected, repository=repo, environment="prod")
    assert not res.verified and res.status == "unprotected" and res.reason == "missing_required_reviewers"

    # 3. Reviewers present, but unrestricted branches (All branches / null deployment_branch_policy) -> unprotected
    client_all_branches = FakeEnvironmentClient(
        environments={
            f"{repo}:prod": {
                "name": "prod",
                "protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}],
                "deployment_branch_policy": None,
            }
        }
    )
    res = verify_protected_environment(client_all_branches, repository=repo, environment="prod")
    assert not res.verified and res.status == "unprotected" and res.reason == "unrestricted_deployment_branches"

    # 4. Reviewers present, custom branch policy has broad wildcard -> rejected
    client_wildcard = FakeEnvironmentClient(
        environments={
            f"{repo}:prod": {
                "name": "prod",
                "protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}],
                "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            }
        },
        branch_policies={f"{repo}:prod": [{"name": "*", "type": "branch"}]},
    )
    res = verify_protected_environment(client_wildcard, repository=repo, environment="prod")
    assert not res.verified and res.status == "unprotected" and res.reason == "wildcard_branch_policy_not_allowed"

    # 5. Reviewers present, custom branch policy pinned strictly to default branch -> verified
    client_valid = FakeEnvironmentClient(
        environments={
            f"{repo}:prod": {
                "name": "prod",
                "protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}],
                "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            }
        },
        branch_policies={f"{repo}:prod": [{"name": "main", "type": "branch"}]},
    )
    res = verify_protected_environment(client_valid, repository=repo, environment="prod")
    assert res.verified and res.status == "protected" and res.reviewer_count == 1
    assert res.allowed_patterns == ("main",)

    # 6. Invalid empty/whitespace environment name raises clear error
    try:
        verify_protected_environment(client_valid, repository=repo, environment="  ")
    except HelmEnvironmentError:
        pass
    else:
        raise AssertionError("expected HelmEnvironmentError for whitespace environment")

    assert HELM_ENVIRONMENT_SCRIPT.is_file()


def test_docs_examples_and_lint_align_with_v2_helm_contract() -> None:
    example = read(HELM_EXAMPLE)
    manual_example = read(HELM_MANUAL_EXAMPLE)
    readme = read(README)
    usage = read(USAGE)
    contributing = read(CONTRIBUTING)
    codex_context = read(CODEX_CONTEXT)
    lint = read(LINT_WORKFLOW)

    assert_contains(example, "nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v2", "examples/helm-deploy.yml must use @v2")
    assert_not_contains(example, "\n  workflow_dispatch:", "examples/helm-deploy.yml must not enable unopted workflow_dispatch")

    assert_contains(manual_example, "nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v2", "examples/helm-deploy-manual.yml must use @v2")
    assert_contains(manual_example, "allow-manual-deploy: true", "examples/helm-deploy-manual.yml must opt into manual deploy")
    assert_contains(manual_example, "environment: production", "examples/helm-deploy-manual.yml must require a protected GitHub environment")
    assert_contains(manual_example, "Caller workflow", "examples/helm-deploy-manual.yml must identify Caller workflow role")
    assert_contains(manual_example, "Workflow consumer", "examples/helm-deploy-manual.yml must identify Workflow consumer role")

    assert_contains(readme, "helm-deploy.yml", "README must list helm-deploy.yml")
    assert_contains(readme, "@v2", "README must document @v2 for guarded Helm deployment")
    assert_contains(readme, "Use `@v1` for normal backward-compatible delivery.", "README must keep backward-compatible v1 guidance")

    assert_contains(usage, "nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v2", "USAGE.md must show @v2 helm-deploy example")
    assert_contains(usage, "allow-manual-deploy", "USAGE.md must document allow-manual-deploy")
    assert_contains(usage, "environment", "USAGE.md must document protected environment input")
    assert_contains(usage, "Migrating `helm-deploy.yml` from `@v1` to `@v2`", "USAGE.md must include v1 -> v2 migration guidance")
    assert_contains(usage, "workflow_dispatch", "USAGE.md must document manual dispatch policy in v2")

    assert_contains(contributing, "helm_deploy_workflow_contract_test.py", "CONTRIBUTING.md must list helm_deploy_workflow_contract_test.py")
    assert_contains(codex_context, "helm-deploy.yml@v2", ".codex/CONTEXT.md must document v2 Helm deploy policy")
    assert_contains(codex_context, "allow-manual-deploy", ".codex/CONTEXT.md must document protected manual Helm deploy opt-in")
    assert_contains(lint, "python scripts/helm_deploy_workflow_contract_test.py", "lint.yml must run helm_deploy_workflow_contract_test.py")


def main() -> None:
    test_helm_deploy_workflow_structure_and_guards()
    test_default_branch_push_executes_real_deploy_and_post_deploy()
    test_same_repo_pull_request_runs_dry_run_and_skips_post_deploy()
    test_protected_manual_dispatch_contract_pending_rejected_and_approved()
    test_unauthorized_events_and_manual_misconfigurations_fail_before_credentials()
    test_step_scripts_fail_closed_when_tampered_or_bypassed()
    test_consumer_environment_protection_contract()
    test_docs_examples_and_lint_align_with_v2_helm_contract()
    print("helm_deploy_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
