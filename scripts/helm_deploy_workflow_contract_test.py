#!/usr/bin/env python3
"""Contract and executable behavior tests for `.github/workflows/helm-deploy.yml` (#187)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_not_contains, assert_order, read

HELM_DEPLOY = ROOT / ".github" / "workflows" / "helm-deploy.yml"
SETUP_DOCTL = ROOT / "actions" / "setup-doctl" / "action.yml"
HELM_EXAMPLE = ROOT / "examples" / "helm-deploy.yml"
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
    deploy_returncode: int | None = None
    post_deploy_returncode: int | None = None


def _load_deploy_steps() -> Dict[str, Dict[str, Any]]:
    data = yaml.safe_load(read(HELM_DEPLOY))
    steps = data["jobs"]["deploy"]["steps"]
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
            "INPUT_DIGITALOCEAN_TOKEN": "do-pat-synthetic-nonprod",
            "INPUT_CLUSTER_ID": "cluster-nonprod-0000",
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
    chart_version: str = "",
    extra_helm_args: str = "",
    post_deploy_kubectl: str = "kubectl get pods -n my-app",
) -> SimulationResult:
    """Execute the real helm-deploy.yml bash bodies with stubbed doctl/helm/kubectl binaries."""
    steps = _load_deploy_steps()
    auth_run = str(steps["Authorize deployment context"]["run"])
    deploy_run = str(steps["Deploy (or dry-run on PR)"]["run"])
    post_run = str(steps["Post-deploy kubectl commands"]["run"])
    doctl_run = _setup_doctl_script()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        log_file = tmp_path / "commands.log"
        github_output = tmp_path / "github_output.txt"
        github_env = tmp_path / "github_env.txt"
        github_output.write_text("", encoding="utf-8")
        github_env.write_text("", encoding="utf-8")

        for tool in ("doctl", "helm", "kubectl"):
            _write_stub_binary(bin_dir, tool, log_file)

        env = _build_base_env(
            bin_dir=bin_dir,
            github_output=github_output,
            github_env=github_env,
            event_name=event_name,
            ref=ref,
            default_branch=default_branch,
            repository=repository,
            head_repo=head_repo,
            head_fork=head_fork,
        )

        auth_proc = subprocess.run(
            ["bash", "-c", auth_run],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        outputs = _parse_kv_file(github_output)
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
        acquire_creds = outputs.get("acquire-credentials", "false")
        allow_post = outputs.get("allow-post-deploy", "false")

        if acquire_creds == "true" and mode in ("deploy", "dry-run"):
            subprocess.run(["bash", "-c", doctl_run], env=env, check=True, capture_output=True, text=True)

        deploy_env = dict(env)
        deploy_env.update(_parse_kv_file(github_env))
        deploy_env.update(
            {
                "HELM_RELEASE_NAME": "my-app",
                "HELM_CHART": "./chart",
                "HELM_NAMESPACE": "my-app",
                "HELM_VALUES_FILE": "values.yaml",
                "HELM_TIMEOUT": "10m",
                "HELM_CHART_VERSION": chart_version,
                "HELM_EXTRA_ARGS": extra_helm_args,
                "HELM_DEPLOY_MODE": mode,
            }
        )

        deploy_rc: int | None = None
        if mode in ("deploy", "dry-run"):
            deploy_proc = subprocess.run(
                ["bash", "-c", deploy_run],
                env=deploy_env,
                capture_output=True,
                text=True,
                check=False,
            )
            deploy_rc = deploy_proc.returncode

        post_rc: int | None = None
        if mode == "deploy" and allow_post == "true" and post_deploy_kubectl != "":
            post_env = dict(deploy_env)
            post_env.update(
                {
                    "HELM_DEPLOY_MODE": mode,
                    "HELM_ALLOW_POST_DEPLOY": allow_post,
                    "POST_DEPLOY_KUBECTL": post_deploy_kubectl,
                }
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
            deploy_returncode=deploy_rc,
            post_deploy_returncode=post_rc,
        )


def test_helm_deploy_workflow_structure_and_guards() -> None:
    text = read(HELM_DEPLOY)
    assert_order(
        text,
        "- name: Checkout",
        "- name: Authorize deployment context",
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

    data = yaml.load(text, Loader=yaml.BaseLoader)
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
    }
    assert expected_inputs.issubset(set(workflow_call["inputs"].keys())), (
        f"missing expected inputs in helm-deploy.yml: {expected_inputs - set(workflow_call['inputs'].keys())}"
    )
    assert "digitalocean-token" in workflow_call["secrets"], "missing digitalocean-token secret"
    assert data["permissions"] == {"contents": "read"}, "helm-deploy.yml must keep least-privilege permissions"

    job = data["jobs"]["deploy"]
    assert "inputs.runner-label" in str(job["runs-on"]), "must preserve downstream runner-label support"
    assert "deploy (dry-run)" in str(job["name"]), "job name must distinguish dry-run from real deploy"

    steps = _load_deploy_steps()
    for step_name, step in steps.items():
        if "run" in step:
            assert "${{" not in str(step["run"]), (
                f"helm-deploy.yml step '{step_name}' must pass context/inputs via env, not inline template interpolation"
            )

    setup_doctl_if = str(steps["Setup doctl + kubeconfig"].get("if", ""))
    setup_helm_if = str(steps["Setup Helm + repos"].get("if", ""))
    deploy_if = str(steps["Deploy (or dry-run on PR)"].get("if", ""))
    post_if = str(steps["Post-deploy kubectl commands"].get("if", ""))

    assert_contains(setup_doctl_if, "steps.authorize.outputs.acquire-credentials == 'true'", "doctl step must require authorize credential approval")
    assert_contains(setup_doctl_if, "steps.authorize.outputs.mode == 'deploy'", "doctl step must check authorize mode")
    assert_contains(setup_helm_if, "steps.authorize.outputs.mode == 'deploy'", "setup-helm must check authorize mode")
    assert_contains(deploy_if, "steps.authorize.outputs.mode == 'deploy'", "deploy step must check authorize mode")
    assert_contains(post_if, "steps.authorize.outputs.mode == 'deploy'", "post-deploy step must require deploy mode")
    assert_contains(post_if, "steps.authorize.outputs.allow-post-deploy == 'true'", "post-deploy step must require allow-post-deploy")


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
        assert result.authorized, f"expected default branch {branch} push to authorize: {result.authorize_stdout}"
        assert result.outputs["mode"] == "deploy"
        assert result.outputs["acquire-credentials"] == "true"
        assert result.outputs["allow-post-deploy"] == "true"
        assert result.deploy_returncode == 0
        assert result.post_deploy_returncode == 0

        helm_cmds = [cmd for cmd in result.commands if cmd[0] == "helm"]
        doctl_cmds = [cmd for cmd in result.commands if cmd[0] == "doctl"]
        kubectl_cmds = [cmd for cmd in result.commands if cmd[0] == "kubectl"]
        assert doctl_cmds == [
            ["doctl", "kubernetes", "cluster", "kubeconfig", "save", "cluster-nonprod-0000"]
        ], f"expected doctl kubeconfig save, got {doctl_cmds}"
        assert len(helm_cmds) == 1
        assert "--dry-run" not in helm_cmds[0], f"real deploy must not pass --dry-run: {helm_cmds[0]}"
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
        post_deploy_kubectl="kubectl apply -f dangerous.yaml",
    )
    assert result.authorized, f"expected same-repo PR dry-run to authorize: {result.authorize_stdout}"
    assert result.outputs["mode"] == "dry-run"
    assert result.outputs["acquire-credentials"] == "true"
    assert result.outputs["allow-post-deploy"] == "false"
    assert result.deploy_returncode == 0
    assert result.post_deploy_returncode is None

    helm_cmds = [cmd for cmd in result.commands if cmd[0] == "helm"]
    kubectl_cmds = [cmd for cmd in result.commands if cmd[0] == "kubectl"]
    assert len(helm_cmds) == 1
    assert "--dry-run" in helm_cmds[0] and "--debug" in helm_cmds[0], f"PR must pass --dry-run --debug: {helm_cmds[0]}"
    assert kubectl_cmds == [], f"PR dry-run must never run post-deploy kubectl: {kubectl_cmds}"


def test_unauthorized_events_and_refs_fail_before_credentials_or_mutations() -> None:
    blocked_cases = [
        ("feature push", {"event_name": "push", "ref": "refs/heads/feat/unsafe", "default_branch": "main"}),
        ("release branch push", {"event_name": "push", "ref": "refs/heads/release/v1", "default_branch": "main"}),
        ("tag push", {"event_name": "push", "ref": "refs/tags/v2.0.0", "default_branch": "main"}),
        ("empty default branch", {"event_name": "push", "ref": "refs/heads/main", "default_branch": ""}),
        ("multiline default branch", {"event_name": "push", "ref": "refs/heads/main", "default_branch": "main\nextra"}),
        ("default branch manual dispatch", {"event_name": "workflow_dispatch", "ref": "refs/heads/main", "default_branch": "main"}),
        ("feature branch manual dispatch", {"event_name": "workflow_dispatch", "ref": "refs/heads/feat/manual", "default_branch": "main"}),
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
        ("pull_request_target", {"event_name": "pull_request_target", "ref": "refs/heads/main", "default_branch": "main"}),
        ("schedule", {"event_name": "schedule", "ref": "refs/heads/main", "default_branch": "main"}),
    ]

    for label, kwargs in blocked_cases:
        result = _simulate_helm_workflow(**kwargs)
        assert not result.authorized, f"{label} unexpectedly authorized"
        assert result.authorize_returncode != 0, f"{label} expected non-zero exit"
        assert result.commands == [], f"{label} executed external commands despite rejection: {result.commands}"


def test_step_scripts_fail_closed_when_invoked_without_deploy_authorization() -> None:
    steps = _load_deploy_steps()
    deploy_run = str(steps["Deploy (or dry-run on PR)"]["run"])
    post_run = str(steps["Post-deploy kubectl commands"]["run"])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        log_file = tmp_path / "commands.log"
        for tool in ("helm", "kubectl"):
            _write_stub_binary(bin_dir, tool, log_file)

        base_env = os.environ.copy()
        base_env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{base_env.get('PATH', '')}",
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

        for bad_mode in ("", "rejected"):
            env = dict(base_env, HELM_DEPLOY_MODE=bad_mode, HELM_ALLOW_POST_DEPLOY="true")
            deploy_proc = subprocess.run(["bash", "-c", deploy_run], env=env, capture_output=True, text=True, check=False)
            assert deploy_proc.returncode != 0, f"deploy script accepted mode={bad_mode!r}"

        for mode, allow in (("dry-run", "false"), ("dry-run", "true"), ("deploy", "false"), ("", "true")):
            env = dict(base_env, HELM_DEPLOY_MODE=mode, HELM_ALLOW_POST_DEPLOY=allow)
            post_proc = subprocess.run(["bash", "-c", post_run], env=env, capture_output=True, text=True, check=False)
            assert post_proc.returncode != 0, f"post-deploy script accepted mode={mode!r} allow={allow!r}"

        assert _read_command_log(log_file) == [], "unauthorized direct step calls must not invoke helm or kubectl"


def test_docs_examples_and_lint_align_with_v2_helm_contract() -> None:
    example = read(HELM_EXAMPLE)
    readme = read(README)
    usage = read(USAGE)
    contributing = read(CONTRIBUTING)
    codex_context = read(CODEX_CONTEXT)
    lint = read(LINT_WORKFLOW)

    assert_contains(example, "nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v2", "examples/helm-deploy.yml must use @v2")
    assert_not_contains(example, "\n  workflow_dispatch:", "examples/helm-deploy.yml must not enable unopted workflow_dispatch")

    assert_contains(readme, "helm-deploy.yml", "README must list helm-deploy.yml")
    assert_contains(readme, "@v2", "README must document @v2 for guarded Helm deployment")
    assert_contains(readme, "Use `@v1` for normal backward-compatible delivery.", "README must keep backward-compatible v1 guidance")

    assert_contains(usage, "nandohdc/gh-pipelines/.github/workflows/helm-deploy.yml@v2", "USAGE.md must show @v2 helm-deploy example")
    assert_contains(usage, "Migrating `helm-deploy.yml` from `@v1` to `@v2`", "USAGE.md must include v1 -> v2 migration guidance")
    assert_contains(usage, "workflow_dispatch", "USAGE.md must document manual dispatch restriction in v2")

    assert_contains(contributing, "helm_deploy_workflow_contract_test.py", "CONTRIBUTING.md must list helm_deploy_workflow_contract_test.py")
    assert_contains(codex_context, "helm-deploy.yml@v2", ".codex/CONTEXT.md must document v2 Helm deploy policy")
    assert_contains(lint, "python scripts/helm_deploy_workflow_contract_test.py", "lint.yml must run helm_deploy_workflow_contract_test.py")


def main() -> None:
    test_helm_deploy_workflow_structure_and_guards()
    test_default_branch_push_executes_real_deploy_and_post_deploy()
    test_same_repo_pull_request_runs_dry_run_and_skips_post_deploy()
    test_unauthorized_events_and_refs_fail_before_credentials_or_mutations()
    test_step_scripts_fail_closed_when_invoked_without_deploy_authorization()
    test_docs_examples_and_lint_align_with_v2_helm_contract()
    print("helm_deploy_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
