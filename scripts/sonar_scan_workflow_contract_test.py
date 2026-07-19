#!/usr/bin/env python3
"""Static contract tests for the reusable sonar-scan workflow.

These tests intentionally avoid contacting GitHub, SonarQube, Cloudflare, or
network endpoints. They assert the fail-closed workflow shape needed for local
SonarQube scanner connectivity.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "sonar-scan.yml"
EXAMPLE = ROOT / "examples" / "sonar-scan.yml"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, message: str) -> None:
    assert needle in text, message


def assert_not_contains(text: str, needle: str, message: str) -> None:
    assert needle not in text, message


def assert_order(text: str, *needles: str) -> None:
    positions: list[int] = []
    for needle in needles:
        position = text.find(needle)
        assert position != -1, f"missing expected text: {needle!r}"
        positions.append(position)
    assert positions == sorted(positions), "expected ordering not found: " + " -> ".join(needles)


def step_block(text: str, step_name: str) -> str:
    marker = f"      - name: {step_name}\n"
    start = text.find(marker)
    assert start != -1, f"missing step {step_name!r}"
    next_start = text.find("\n      - name: ", start + len(marker))
    if next_start == -1:
        return text[start:]
    return text[start:next_start]


def test_workflow_url_contract() -> None:
    text = read(WORKFLOW)

    for expected in (
        "sonar-scanner-host-url:",
        "sonar-public-base-url:",
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
        "report serverUrl does not match configured scanner URL",
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


def test_docs_example_and_lint_contract_are_updated() -> None:
    example = read(EXAMPLE)
    lint = read(LINT_WORKFLOW)

    for expected in (
        "sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}",
        "sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}",
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
    test_scan_and_quality_gate_use_resolved_scanner_url()
    test_report_task_validation_is_fail_closed()
    test_runner_label_fork_safety_and_permissions()
    test_no_hardcoded_urls_or_quick_tunnel_active_config()
    test_external_actions_are_pinned()
    test_docs_example_and_lint_contract_are_updated()
    print("sonar_scan_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
