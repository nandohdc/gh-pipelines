#!/usr/bin/env python3
"""Static contract tests for reusable-workflow consumer terminology."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_not_contains, read

CONTEXT = ROOT / "CONTEXT.md"
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "USAGE.md"
CONTRIBUTING = ROOT / "docs" / "CONTRIBUTING.md"
INTEGRATION_CONSUMERS = ROOT / "docs" / "INTEGRATION_CONSUMERS.md"
TEST_CALLERS = ROOT / ".github" / "workflows" / "test-callers.yml"
INTEGRATION_SMOKE = ROOT / ".github" / "workflows" / "integration-smoke.yml"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
GO_EXAMPLE = ROOT / "examples" / "go-service-ci.yml"
EXAMPLES = sorted((ROOT / "examples").glob("*.yml"))
WORKFLOWS = ROOT / ".github" / "workflows"


def test_glossary_defines_consumer_boundaries() -> None:
    context = read(CONTEXT)
    for term in (
        "**Consumer repository**:",
        "**Workflow consumer**:",
        "**Composite-action consumer**:",
        "**Caller workflow**:",
        "**Workflow invocation (or Caller job)**:",
        "**Canary consumer repository**:",
        "**Self-test caller**:",
        "**Integration smoke**:",
    ):
        assert_contains(context, term, f"CONTEXT.md missing glossary term: {term}")


def test_public_guidance_distinguishes_consumer_and_caller_roles() -> None:
    readme = read(README)
    usage = read(USAGE)
    contributing = read(CONTRIBUTING)

    assert_contains(readme, "Consumer repository", "README.md must define Consumer repository")
    assert_contains(readme, "Workflow consumer", "README.md must define Workflow consumer")
    assert_contains(
        readme,
        "every Consumer repository inherits the change",
        "README.md must not use bare consumer for a repository",
    )
    assert_not_contains(
        readme,
        "consumers verifying the attestation",
        "README.md must not use bare consumers for a repository",
    )
    assert_contains(
        readme,
        "Workflow invocation (also called a Caller job)",
        "README.md must define Caller job as a Workflow invocation",
    )
    assert_contains(usage, "Caller workflow", "USAGE.md must identify a caller workflow")
    assert_contains(usage, "Workflow invocation", "USAGE.md must identify a workflow invocation")
    assert_contains(
        contributing,
        "Caller workflow",
        "CONTRIBUTING.md must name caller workflows precisely",
    )


def test_validation_guidance_keeps_canary_and_self_test_boundaries_explicit() -> None:
    integration_consumers = read(INTEGRATION_CONSUMERS)
    test_callers = read(TEST_CALLERS)
    integration_smoke = read(INTEGRATION_SMOKE)

    assert_contains(
        integration_consumers,
        "Canary consumer repository",
        "integration guide must identify a canary consumer repository",
    )
    assert_contains(
        integration_consumers,
        "does not execute a consumer pipeline",
        "integration guide must describe the smoke probe boundary",
    )
    assert_contains(
        test_callers,
        "self-test caller",
        "test-callers workflow must identify its internal role",
    )
    assert_contains(
        integration_smoke,
        "parse-and-lint drift probe",
        "integration-smoke workflow must identify its validation role",
    )
    assert_contains(
        integration_smoke,
        'description: "Canary consumer repository to run"',
        "integration-smoke input must name the canonical canary role",
    )


def test_example_identifies_its_consumer_and_caller_roles() -> None:
    example = read(GO_EXAMPLE)

    assert_contains(
        example,
        "Caller workflow",
        "Go example must identify its caller-workflow role",
    )
    assert_contains(
        example,
        "Workflow consumer",
        "Go example must identify its workflow-consumer role",
    )
    for path in EXAMPLES:
        text = read(path)
        assert_contains(text, "Caller workflow", f"{path} must identify its caller-workflow role")
        assert_contains(text, "Workflow consumer", f"{path} must identify its workflow-consumer role")


def test_contributor_checklist_uses_canonical_caller_language() -> None:
    contributing = read(CONTRIBUTING)

    assert_not_contains(
        contributing,
        "Header comment with example caller",
        "contributor checklist must not use ambiguous caller language",
    )
    assert_not_contains(
        contributing,
        "Example caller added/updated",
        "contributor checklist must not use ambiguous caller language",
    )


def test_reusable_workflow_headers_identify_caller_workflows() -> None:
    reusable_workflows = [
        path for path in sorted(WORKFLOWS.glob("*.yml")) if "workflow_call:" in read(path)
    ]
    assert reusable_workflows, "expected at least one reusable workflow"

    for path in reusable_workflows:
        assert_contains(
            read(path),
            "# Example Caller workflow:",
            f"{path} must identify its example as a Caller workflow",
        )


def test_reference_guidance_separates_delivery_and_validation_roles() -> None:
    readme = read(README)
    integration_consumers = read(INTEGRATION_CONSUMERS)

    assert_contains(
        readme,
        "Use `@v1` for normal backward-compatible delivery.",
        "README.md must define the floating-major delivery role",
    )
    assert_contains(
        readme,
        "An exact release reference fixes the declared workflow reference, not every transitive dependency.",
        "README.md must qualify exact release-reference stability",
    )
    assert_contains(
        readme,
        "Branch references and `@main` are validation references, not production onboarding references.",
        "README.md must distinguish validation references from onboarding",
    )
    assert_contains(
        integration_consumers,
        "`@main` is a validation reference used only by integration smoke.",
        "integration guide must keep the main reference within the smoke-probe boundary",
    )


def test_lint_workflow_runs_consumer_terminology_contract() -> None:
    lint = read(LINT_WORKFLOW)
    assert_contains(
        lint,
        "python scripts/consumer_terminology_contract_test.py",
        "lint workflow must run the consumer terminology contract test",
    )


def main() -> None:
    test_glossary_defines_consumer_boundaries()
    test_public_guidance_distinguishes_consumer_and_caller_roles()
    test_validation_guidance_keeps_canary_and_self_test_boundaries_explicit()
    test_example_identifies_its_consumer_and_caller_roles()
    test_contributor_checklist_uses_canonical_caller_language()
    test_reusable_workflow_headers_identify_caller_workflows()
    test_reference_guidance_separates_delivery_and_validation_roles()
    test_lint_workflow_runs_consumer_terminology_contract()
    print("consumer_terminology_contract_test: ok")


if __name__ == "__main__":
    main()
