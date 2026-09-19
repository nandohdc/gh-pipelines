#!/usr/bin/env python3
"""Static contracts for multi-module Go consumers of reusable workflows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_order, read, step_block

CI_GO = ROOT / ".github" / "workflows" / "ci-go.yml"
SETUP_GO = ROOT / "actions" / "setup-go" / "action.yml"
SONAR_SCAN = ROOT / ".github" / "workflows" / "sonar-scan.yml"
TEST_CALLERS = ROOT / ".github" / "workflows" / "test-callers.yml"


def test_ci_go_supports_independent_module_contracts() -> None:
    workflow = read(CI_GO)
    setup_action = read(SETUP_GO)

    for expected in (
        "cache-dependency-path:",
        "golangci-lint-major:",
        "coverage-artifact-name:",
        "integration-coverage-artifact-name:",
        "run-build:",
        "cache-dependency-path: ${{ inputs.cache-dependency-path }}",
        "name: ${{ inputs.coverage-artifact-name }}",
        "name: ${{ inputs.integration-coverage-artifact-name }}",
        "if: inputs.run-build",
        "github.com/golangci/golangci-lint/v2/cmd/golangci-lint",
    ):
        assert_contains(workflow, expected, f"ci-go multi-module contract missing: {expected}")

    assert_contains(
        setup_action,
        "cache-dependency-path:",
        "setup-go must expose an explicit cache dependency path",
    )
    assert_contains(
        setup_action,
        "cache-dependency-path: ${{ steps.cache.outputs.dependency-path }}",
        "setup-go must forward the resolved cache dependency path",
    )

    lint = step_block(workflow, "golangci-lint")
    assert_contains(lint, "case \"${GOLANGCI_LINT_MAJOR}\" in", "lint must select a supported major explicitly")
    assert_contains(lint, "1)", "lint must preserve golangci-lint v1 compatibility")
    assert_contains(lint, "2)", "lint must support golangci-lint v2 configurations")


def test_sonar_scan_can_consume_caller_coverage_artifacts() -> None:
    workflow = read(SONAR_SCAN)

    for expected in (
        "coverage-artifact-pattern:",
        "coverage-artifact-path:",
        "uses: actions/download-artifact@v7",
        "pattern: ${{ inputs.coverage-artifact-pattern }}",
        "path: ${{ inputs.coverage-artifact-path }}",
    ):
        assert_contains(workflow, expected, f"sonar coverage-artifact contract missing: {expected}")

    assert_order(
        workflow,
        "      - name: Download coverage artifacts",
        "      - name: Pre-scan command",
        "      - name: SonarQube scan",
    )


def test_internal_fixture_exercises_optional_build_and_artifact_name() -> None:
    callers = read(TEST_CALLERS)

    assert_contains(callers, "coverage-artifact-name: fixture-go-coverage", "fixture must exercise custom artifact naming")
    assert_contains(callers, "run-build: true", "fixture must exercise optional build validation")
    for expected in (
        "test-ci-go-multimodule-api:",
        "test-ci-go-multimodule-cli:",
        "test-ci-go-multimodule-handoff:",
        "coverage-artifact-name: fixture-multi-coverage-api",
        "coverage-artifact-name: fixture-multi-coverage-cli",
        "integration-coverage-artifact-name: fixture-multi-integration-api",
        "integration-coverage-artifact-name: fixture-multi-integration-cli",
        "pattern: fixture-multi-coverage-*",
    ):
        assert_contains(callers, expected, f"multimodule fixture contract missing: {expected}")


def main() -> None:
    test_ci_go_supports_independent_module_contracts()
    test_sonar_scan_can_consume_caller_coverage_artifacts()
    test_internal_fixture_exercises_optional_build_and_artifact_name()
    print("multimodule_go_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
