#!/usr/bin/env python3
"""Guard hosted execution for repository-owned workflow entry points.

This static contract complements, but cannot replace, hosted runner evidence.
"""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
INPUT_RUNNER = "${{ inputs.runner-label }}"
DEFAULT_RUNNER = "${{ inputs.runner-label != '' && inputs.runner-label || 'ubuntu-latest' }}"
REVIEWED_ENTRY_POINTS = {
    "dependabot-auto-merge.yml",
    "integration-smoke.yml",
    "lint.yml",
    "release.yml",
    "security-self-scan.yml",
    "test-callers.yml",
}


def check_workflow(path: Path, runner_label: str | None, stack: tuple[Path, ...] = ()) -> None:
    assert path not in stack, f"recursive workflow call: {path.name}"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    for job_id, job in workflow.get("jobs", {}).items():
        location = f"{path.name}:{job_id}"
        if "runs-on" in job:
            selected = job["runs-on"]
            assert selected == "ubuntu-latest" or (selected == DEFAULT_RUNNER and runner_label == "ubuntu-latest"), (
                f"{location} does not select ubuntu-latest"
            )
            continue

        target = job.get("uses", "")
        prefix = "./.github/workflows/"
        assert target.startswith(prefix), f"{location} is an unreviewed caller"
        selected = job.get("with", {}).get("runner-label", "")
        if selected == INPUT_RUNNER:
            selected = runner_label
        assert selected == "ubuntu-latest", f"{location} must explicitly select ubuntu-latest"
        check_workflow(WORKFLOWS / target.removeprefix(prefix), selected, (*stack, path))


def test_internal_jobs_run_on_hosted_runners() -> None:
    entry_points = set()
    for path in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        events = workflow.get("on", {})
        if isinstance(events, str):
            events = {events: {}}
        if not set(events) - {"workflow_call"}:
            continue
        entry_points.add(path.name)
        assert "vars.RUNNER_LABEL" not in path.read_text(), f"{path.name} references RUNNER_LABEL"
        assert "inputs.runner-label" not in path.read_text(), f"{path.name} permits an internal runner override"
        check_workflow(path, None)
    assert entry_points == REVIEWED_ENTRY_POINTS, f"review internal entry point changes: {entry_points ^ REVIEWED_ENTRY_POINTS}"


if __name__ == "__main__":
    test_internal_jobs_run_on_hosted_runners()
    print("hosted_runner_workflow_contract_test: ok")
