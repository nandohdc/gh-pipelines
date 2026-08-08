#!/usr/bin/env python3
"""Static contract tests for the reusable sonar-scan workflow.

These tests intentionally avoid contacting GitHub, SonarQube, Cloudflare, or
network endpoints. They assert the fail-closed workflow shape needed for local
SonarQube scanner connectivity.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.contract_lib import (
    assert_contains,
    assert_not_contains,
    assert_order,
    read,
    step_block,
)
WORKFLOW = ROOT / ".github" / "workflows" / "sonar-scan.yml"
REPOSITORY_WORKFLOW = ROOT / ".github" / "workflows" / "sonar.yml"
EXAMPLE = ROOT / "examples" / "sonar-scan.yml"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
PROJECT = ROOT / "sonar-project.properties"


def test_workflow_url_contract() -> None:
    text = read(WORKFLOW)

    for expected in (
        "sonar-scanner-host-url:",
        "sonar-public-base-url:",
        "pre-scan-command:",
        "SONAR_SCANNER_HOST_URL:",
        "SONAR_PUBLIC_BASE_URL:",
        "SONAR_HOST_URL:",
        "required: false",
        "SONAR_SCANNER_HOST_URL_INPUT",
        "SONAR_SCANNER_HOST_URL_SECRET",
        "SONAR_HOST_URL_LEGACY_SECRET",
        "printf 'SONAR_HOST_URL=%s\\n' \"${scanner_host_url}\"",
    ):
        assert_contains(text, expected, f"missing scanner/public URL contract text: {expected}")

    assert_order(
        text,
        'scanner_host_url="${SONAR_SCANNER_HOST_URL_INPUT}"',
        'scanner_host_url="${SONAR_SCANNER_HOST_URL_SECRET}"',
        'scanner_host_url="${SONAR_HOST_URL_LEGACY_SECRET}"',
    )
    assert_contains(
        text,
        "Set sonar-scanner-host-url, SONAR_SCANNER_HOST_URL, or legacy SONAR_HOST_URL.",
        "missing fail-closed scanner URL error",
    )


def test_preflight_runs_before_scan_and_distinguishes_failures() -> None:
    text = read(WORKFLOW)
    assert_order(
        text,
        "      - name: Resolve SonarQube URLs",
        "      - name: Preflight SonarQube scanner URL",
        "      - name: Install Sonar scanner signature dependencies",
        "      - name: Setup Node for Sonar JS analyzer",
        "      - name: Pre-scan command",
        "      - name: SonarQube scan",
        "      - name: Validate SonarQube scanner report",
        "      - name: SonarQube quality gate",
    )

    preflight = step_block(text, "Preflight SonarQube scanner URL")
    for expected in (
        "curl --silent --show-error --connect-timeout 5 --max-time 20",
        "/api/system/status",
        "/api/server/version",
        "DNS resolution failed",
        "TCP connection",
        "Timed out connecting",
        "tunnel or upstream is unavailable",
        "requires authentication or proxy access headers",
        "STARTING|RESTARTING|DB_MIGRATION_NEEDED|DB_MIGRATION_RUNNING",
        "reachable but unhealthy",
    ):
        assert_contains(preflight, expected, f"preflight does not distinguish: {expected}")


def test_scanner_signature_verification_dependencies_are_installed() -> None:
    text = read(WORKFLOW)
    install = step_block(text, "Install Sonar scanner signature dependencies")
    for expected in (
        "command -v dirmngr",
        "apt-get -o Acquire::Retries=3 install -y --no-install-recommends dirmngr",
        "not root and passwordless sudo is unavailable",
    ):
        assert_contains(install, expected, f"signature dependency step missing guard: {expected}")
    assert_not_contains(
        text,
        "skipSignatureVerification: true",
        "scanner signature verification must stay enabled; install dirmngr instead",
    )


def test_scan_and_quality_gate_use_resolved_scanner_url() -> None:
    text = read(WORKFLOW)
    scan = step_block(text, "SonarQube scan")
    gate = step_block(text, "SonarQube quality gate")

    assert_contains(scan, "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", "scan must receive the Sonar token")
    assert_contains(gate, "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", "quality gate must receive the Sonar token")
    assert_not_contains(scan, "secrets.SONAR_HOST_URL", "scan must not use the legacy URL secret directly")
    assert_not_contains(gate, "secrets.SONAR_HOST_URL", "quality gate must not use the legacy URL secret directly")
    assert_contains(text, "printf 'SONAR_HOST_URL=%s\\n' \"${scanner_host_url}\"", "resolved scanner URL must feed actions as SONAR_HOST_URL")


def test_report_task_validation_is_fail_closed() -> None:
    text = read(WORKFLOW)
    validation = step_block(text, "Validate SonarQube scanner report")

    for expected in (
        ".scannerwork/report-task.txt",
        "was not created",
        "file is empty",
        "for required_key in ceTaskId ceTaskUrl serverUrl; do",
        "disposable Quick Tunnel URL",
        "Normalizing scanner report URLs to the resolved scanner URL",
        'grep -qx "serverUrl=${configured_url}" "${report_file}"',
        'grep -qx "ceTaskUrl=${configured_url}/api/ce/task?id=${ce_task_id}" "${report_file}"',
        "exit 1",
    ):
        assert_contains(validation, expected, f"missing report-task validation guard: {expected}")
    assert_order(text, "      - name: Validate SonarQube scanner report", "      - name: SonarQube quality gate")
    assert_not_contains(text, "continue-on-error", "workflow must not mask scan or quality-gate failures")


def test_runner_label_fork_safety_and_permissions() -> None:
    text = read(WORKFLOW)

    assert_contains(text, "runs-on: ${{ inputs.runner-label != '' && inputs.runner-label || 'ubuntu-latest' }}", "runner-label input must control runner placement")
    assert_contains(text, "permissions:\n  contents: read\n  pull-requests: read", "workflow permissions must stay read-only")
    assert_contains(text, "persist-credentials: false", "checkout should not persist credentials")
    assert_contains(text, "on:\n  workflow_call:", "reusable workflow should only expose workflow_call")
    for forbidden_event in ("pull_request_target:", "workflow_run:", "schedule:"):
        assert_not_contains(text, forbidden_event, f"unexpected event weakens fork-safety: {forbidden_event}")


def test_no_hardcoded_urls_or_quick_tunnel_active_config() -> None:
    text = read(WORKFLOW)
    lower = text.lower()

    for forbidden in (
        "trycloudflare.com",
        "cloudflared tunnel --url",
        "digitalocean",
        "doks",
    ):
        assert_not_contains(lower, forbidden, f"forbidden active scanner config found: {forbidden}")

    hardcoded_urls = [
        match
        for match in re.findall(r"https?://[^\s'\")]+", text)
        if "*" not in match and "|" not in match and "}" not in match and match not in {"https://."}
    ]
    assert hardcoded_urls == [], f"hardcoded absolute URLs are not allowed in sonar-scan.yml: {hardcoded_urls}"


def test_external_actions_are_pinned() -> None:
    text = read(WORKFLOW)

    for action in (
        "SonarSource/sonarqube-scan-action",
        "SonarSource/sonarqube-quality-gate-action",
    ):
        pattern = rf"uses: {re.escape(action)}@[0-9a-f]{{40}} # v[0-9]+\.[0-9]+\.[0-9]+"
        assert re.search(pattern, text), f"{action} must be pinned to a full SHA with a version comment"
    assert_contains(text, "uses: actions/checkout@v7", "official checkout action should stay tag-pinned for readability")
    assert_contains(text, "uses: actions/setup-node@v7", "standalone Sonar scan should provide Node for JS/CSS analysis")
    assert_contains(text, 'node-version: "22"', "standalone Sonar scan should install a supported Node runtime")


def test_repository_sonar_workflow_uses_public_scanner_secret() -> None:
    workflow = read(REPOSITORY_WORKFLOW)
    sonar_project = read(PROJECT)

    assert_contains(sonar_project, "sonar.projectKey=nandohdc_gh-pipelines_7ee6bd5a-8a54-4996-a00d-668315012f38", "repo Sonar project key must stay stable")
    assert_contains(sonar_project, "sonar.projectName=gh-pipelines", "repo Sonar project name must be set")
    assert_contains(sonar_project, "sonar.sources=.github,actions,examples,scripts,docs,README.md,SECURITY.md,CHANGELOG.md,AGENTS.md,CLAUDE.md", "repo Sonar sources should be scoped to active library paths")
    assert_contains(sonar_project, "sonar.tests=tests,scripts", "repo Sonar tests should be classified separately")
    assert_contains(sonar_project, "sonar.test.inclusions=scripts/*_test.py", "contract tests must be classified as test code, not coverage-eligible sources")
    assert_contains(sonar_project, "sonar.python.version=3.12", "repo Sonar config must pin the analyzed Python version for precise analysis")
    assert_contains(sonar_project, "sonar.python.coverage.reportPaths=coverage.xml", "repo Sonar config must set coverage report path for contract_lib coverage")
    assert_contains(sonar_project, ",scripts/*_test.py", "contract tests must be excluded from Sonar sources")
    assert_contains(sonar_project, "**/node_modules/**", "repo Sonar exclusions should avoid dependency directories")

    assert_contains(workflow, "name: sonar", "missing internal Sonar workflow")
    assert_contains(workflow, "pull_request:", "internal Sonar workflow must run on PRs")
    assert_contains(workflow, "if: github.actor != 'dependabot[bot]'", "internal Sonar workflow must skip Dependabot PRs which lack secrets")
    assert_contains(workflow, "branches:\n      - main", "internal Sonar workflow must target main")
    assert_contains(workflow, "uses: ./.github/workflows/sonar-scan.yml", "internal Sonar workflow should reuse the library workflow")
    assert_not_contains(workflow, "svc.cluster.local", "internal Sonar workflow must not hardcode the in-cluster URL, which only resolves on ARC runners")
    assert_contains(workflow, "runner-label: ${{ vars.RUNNER_LABEL }}", "internal Sonar workflow must use the repo runner variable")
    assert_contains(workflow, "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", "internal Sonar workflow must forward the Sonar token secret")
    assert_contains(workflow, "SONAR_SCANNER_HOST_URL: ${{ secrets.SONAR_SCANNER_HOST_URL }}", "internal Sonar workflow must forward the preferred scanner URL secret")
    assert_contains(workflow, "SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}", "internal Sonar workflow must forward the public scanner URL secret so scans work on GitHub-hosted runners")
    assert_contains(workflow, "pre-scan-command:", "internal Sonar workflow must pass the pre-scan command")
    assert_contains(workflow, "pytest scripts/contract_lib_test.py", "internal Sonar workflow must run contract_lib unit tests for coverage")
    assert_not_contains(workflow, "trycloudflare.com", "internal Sonar workflow must not use disposable Quick Tunnel URLs")


def test_docs_example_and_lint_contract_are_updated() -> None:
    example = read(EXAMPLE)
    lint = read(LINT_WORKFLOW)

    for expected in (
        "sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}",
        "sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}",
        'pre-scan-command: ""',
        "SONAR_SCANNER_HOST_URL: ${{ secrets.SONAR_SCANNER_HOST_URL }}",
        "SONAR_PUBLIC_BASE_URL: ${{ secrets.SONAR_PUBLIC_BASE_URL }}",
        "SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}",
    ):
        assert_contains(example, expected, f"example missing URL separation contract: {expected}")

    assert_contains(
        lint,
        "python scripts/sonar_scan_workflow_contract_test.py",
        "lint workflow should run the static Sonar workflow contract test",
    )


def main() -> None:
    test_workflow_url_contract()
    test_preflight_runs_before_scan_and_distinguishes_failures()
    test_scanner_signature_verification_dependencies_are_installed()
    test_scan_and_quality_gate_use_resolved_scanner_url()
    test_report_task_validation_is_fail_closed()
    test_runner_label_fork_safety_and_permissions()
    test_no_hardcoded_urls_or_quick_tunnel_active_config()
    test_external_actions_are_pinned()
    test_repository_sonar_workflow_uses_public_scanner_secret()
    test_docs_example_and_lint_contract_are_updated()
    print("sonar_scan_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
