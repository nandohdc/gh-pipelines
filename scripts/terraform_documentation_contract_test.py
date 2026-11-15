#!/usr/bin/env python3
"""Static contract tests for Terraform caller authentication guidance.

Guards issue #194:
- ``ci-terraform.yml`` must not claim caller-job steps/OIDC/env propagate into
  ``workflow_call`` jobs on hosted runners.
- Coupled examples and docs must teach both the static/credential-free
  ``ci-terraform.yml`` route (for example ``run-plan: false``) and the
  provider-independent caller-composed same-job pattern using
  ``nandohdc/gh-pipelines/actions/setup-terraform@v1``.
- ``lint.yml`` and ``test-callers.yml`` must enforce the documentation contract
  and exercise the caller-composed pattern against ``tests/fixtures/terraform``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_not_contains, assert_order, read

CI_TERRAFORM = ROOT / ".github" / "workflows" / "ci-terraform.yml"
EXAMPLE_TERRAFORM = ROOT / "examples" / "terraform-service-ci.yml"
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "USAGE.md"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
TEST_CALLERS = ROOT / ".github" / "workflows" / "test-callers.yml"


def _validate_workflow_job_shapes(data: object, source: str) -> list[dict[str, object]]:
    assert isinstance(data, dict), f"{source}: expected YAML mapping"
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{source}: expected non-empty jobs mapping"
    collected: list[dict[str, object]] = []
    for job_id, job in jobs.items():
        assert isinstance(job, dict), f"{source}: job {job_id!r} must be a mapping"
        if "uses" in job:
            assert "steps" not in job, f"{source}: reusable job {job_id!r} must not declare steps"
            assert "runs-on" not in job, f"{source}: reusable job {job_id!r} must not declare runs-on"
        else:
            assert "runs-on" in job, f"{source}: standard job {job_id!r} must declare runs-on"
            assert isinstance(job.get("steps"), list) and job["steps"], (
                f"{source}: standard job {job_id!r} must declare steps"
            )
        collected.append(job)
    return collected


def test_ci_terraform_workflow_documents_actual_credential_constraints() -> None:
    text = read(CI_TERRAFORM)
    assert_not_contains(
        text,
        "before invoking this workflow",
        "ci-terraform.yml must not claim caller auth before workflow_call propagates into reusable jobs",
    )
    assert_not_contains(
        text,
        "configure cloud credentials via OIDC in the caller workflow before",
        "ci-terraform.yml must not imply OIDC in caller workflow propagates across workflow_call",
    )
    assert_contains(text, "do not inherit", "ci-terraform.yml must state jobs do not inherit caller steps or env")
    assert_contains(
        text,
        "actions/setup-terraform@v1",
        "ci-terraform.yml must direct authenticated callers to actions/setup-terraform@v1",
    )
    assert_contains(text, "run-plan: false", "ci-terraform.yml must mention credential-free validation via run-plan: false")
    assert_contains(text, "inputs.runner-label", "ci-terraform.yml must preserve downstream runner-label support")
    assert_contains(text, "permissions:\n  contents: read", "ci-terraform.yml must preserve least-privilege read permission")

    data = yaml.safe_load(text)
    workflow_call = data[True]["workflow_call"]
    inputs = set(workflow_call["inputs"].keys())
    assert inputs == {
        "terraform-version",
        "working-directory",
        "var-file",
        "backend-config",
        "run-plan",
        "run-audit",
        "audit-tool",
        "tfsec-additional-args",
        "upload-plan",
        "runner-label",
    }, f"ci-terraform.yml must keep provider-independent input contract without auth inputs; got {sorted(inputs)}"


def test_examples_and_docs_teach_static_and_caller_composed_terraform_paths() -> None:
    usage_text = read(USAGE)
    assert_not_contains(
        usage_text,
        "Cloud credentials are managed by the Caller workflow and should be configured in it (environment variables or OIDC).",
        "docs/USAGE.md must replace misleading cross-job credential propagation wording",
    )

    for path in (EXAMPLE_TERRAFORM, README, USAGE):
        text = read(path)
        assert_contains(
            text,
            "nandohdc/gh-pipelines/.github/workflows/ci-terraform.yml@v1",
            f"{path.name} must document static/credential-free ci-terraform.yml@v1 usage",
        )
        assert_contains(text, "run-plan: false", f"{path.name} must mention run-plan: false for static validation")
        assert_contains(
            text,
            "nandohdc/gh-pipelines/actions/setup-terraform@v1",
            f"{path.name} must document caller-composed actions/setup-terraform@v1 usage",
        )
        assert_contains(text, "id-token: write", f"{path.name} must show OIDC id-token: write permission in caller job")

    example_text = read(EXAMPLE_TERRAFORM)
    assert_contains(example_text, "terraform fmt -check -recursive", "example must show fmt command in caller-composed route")
    assert_contains(example_text, "terraform init -input=false", "example must show init command in caller-composed route")
    assert_contains(example_text, "terraform validate", "example must show validate command in caller-composed route")
    assert_contains(example_text, "terraform plan -input=false -no-color", "example must show plan command in caller-composed route")

    assert_contains(usage_text, "do not inherit", "docs/USAGE.md must explain why workflow_call does not inherit caller auth")
    assert_contains(usage_text, "terraform init -input=false", "docs/USAGE.md must document terraform init -input=false")
    assert_contains(usage_text, "terraform plan -input=false -no-color", "docs/USAGE.md must document terraform plan -input=false -no-color")
    assert_contains(usage_text, "providerless", "docs/USAGE.md must distinguish providerless fixture checks from cloud OIDC acceptance")


def test_terraform_yaml_examples_are_syntactically_valid_and_legal() -> None:
    example_text = read(EXAMPLE_TERRAFORM)
    example_data = yaml.safe_load(example_text)
    _validate_workflow_job_shapes(example_data, EXAMPLE_TERRAFORM.name)

    marker = "#  terraform-plan:"
    assert_contains(example_text, marker, "examples/terraform-service-ci.yml must include Option 2 terraform-plan example")
    option2_lines = []
    for line in example_text[example_text.index(marker):].splitlines():
        if line.startswith("#      # "):
            continue
        if line == "#":
            option2_lines.append("")
        elif line.startswith("# "):
            option2_lines.append(line[1:])
        elif line.startswith("#"):
            option2_lines.append(line[1:])
    option2_data = yaml.safe_load("jobs:\n" + "\n".join(option2_lines))
    option2_jobs = _validate_workflow_job_shapes(option2_data, "examples/terraform-service-ci.yml (Option 2)")
    assert any(
        isinstance(step, dict) and str(step.get("uses", "")).startswith("nandohdc/gh-pipelines/actions/setup-terraform@v1")
        for job in option2_jobs
        for step in (job.get("steps") or [])
    ), "examples/terraform-service-ci.yml Option 2 must invoke nandohdc/gh-pipelines/actions/setup-terraform@v1"

    usage_text = read(USAGE)
    tf_start = usage_text.find("### ci-terraform.yml")
    assert tf_start != -1, "docs/USAGE.md must retain ### ci-terraform.yml section"
    next_section = usage_text.find("\n### ", tf_start + 1)
    tf_section = usage_text[tf_start:next_section] if next_section != -1 else usage_text[tf_start:]

    yaml_blocks = re.findall(r"```yaml\n(.*?)```", tf_section, flags=re.DOTALL)
    assert len(yaml_blocks) >= 2, "docs/USAGE.md Terraform section must include both reusable and caller-composed YAML examples"

    saw_reusable = False
    saw_composed = False
    for index, block in enumerate(yaml_blocks, start=1):
        parsed = yaml.safe_load(block)
        jobs = _validate_workflow_job_shapes(parsed, f"docs/USAGE.md Terraform YAML block #{index}")
        for job in jobs:
            uses_val = str(job.get("uses", ""))
            if "ci-terraform.yml@v1" in uses_val:
                saw_reusable = True
            steps = job.get("steps") or []
            if any(
                isinstance(step, dict) and str(step.get("uses", "")).startswith("nandohdc/gh-pipelines/actions/setup-terraform@v1")
                for step in steps
            ):
                saw_composed = True

    assert saw_reusable, "docs/USAGE.md Terraform section must include valid ci-terraform.yml@v1 example"
    assert saw_composed, "docs/USAGE.md Terraform section must include valid caller-composed setup-terraform@v1 job example"


def test_lint_and_test_callers_cover_caller_composed_terraform_pattern() -> None:
    lint_text = read(LINT_WORKFLOW)
    assert_contains(
        lint_text,
        "python scripts/terraform_documentation_contract_test.py",
        "lint workflow must execute scripts/terraform_documentation_contract_test.py",
    )

    test_callers_text = read(TEST_CALLERS)
    job_marker = "  test-setup-terraform-caller-composed:\n"
    assert_contains(
        test_callers_text,
        job_marker,
        "test-callers.yml must define test-setup-terraform-caller-composed job",
    )
    job_block = test_callers_text[test_callers_text.index(job_marker):]
    assert_order(
        job_block,
        "  test-setup-terraform-caller-composed:",
        "    runs-on: ubuntu-latest",
        "CALLER_AUTH_CONTEXT",
        "uses: ./actions/setup-terraform",
        "terraform fmt -check -recursive",
        "terraform init -input=false",
        "terraform validate",
        "terraform plan -input=false -no-color",
    )


def main() -> None:
    test_ci_terraform_workflow_documents_actual_credential_constraints()
    test_examples_and_docs_teach_static_and_caller_composed_terraform_paths()
    test_terraform_yaml_examples_are_syntactically_valid_and_legal()
    test_lint_and_test_callers_cover_caller_composed_terraform_pattern()
    print("terraform_documentation_contract_test: ok")


if __name__ == "__main__":
    main()
