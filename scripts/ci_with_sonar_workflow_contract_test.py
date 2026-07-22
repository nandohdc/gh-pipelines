#!/usr/bin/env python3
"""Static contract tests for the ci-with-sonar bundle workflows.

These tests intentionally do not contact GitHub, Doppler, SonarQube, or any
network endpoint. They assert that the Go, Node, and Python Sonar bundles share
one fail-closed local-kind scanner connectivity contract.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "ci-with-sonar-go.yml",
    ROOT / ".github" / "workflows" / "ci-with-sonar-node.yml",
    ROOT / ".github" / "workflows" / "ci-with-sonar-python.yml",
]
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
EXAMPLES = [
    ROOT / "examples" / "ci-with-sonar-go.yml",
    ROOT / "examples" / "ci-with-sonar-node.yml",
    ROOT / "examples" / "ci-with-sonar-python.yml",
]


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


def workflow_texts() -> list[tuple[Path, str]]:
    return [(path, read(path)) for path in WORKFLOWS]


def test_all_bundle_workflows_expose_same_url_contract() -> None:
    for path, text in workflow_texts():
        for expected in (
            "sonar-scanner-host-url:",
            "Preferred non-secret scanner URL",
            "sonar-public-base-url:",
            "Public SonarQube UI/base URL",
            "SONAR_SCANNER_HOST_URL_VAR: ${{ vars.SONAR_SCANNER_HOST_URL }}",
            "SONAR_PUBLIC_BASE_URL_VAR: ${{ vars.SONAR_PUBLIC_BASE_URL }}",
            "doppler_get_required SONAR_TOKEN",
            "doppler_get_optional SONAR_SCANNER_HOST_URL",
            "doppler_get_required SONAR_HOST_URL",
            "doppler_get_optional SONAR_PUBLIC_BASE_URL",
            "printf 'SONAR_SCANNER_HOST_URL=%s\\n' \"${scanner_host_url}\"",
            "printf 'SONAR_HOST_URL=%s\\n' \"${scanner_host_url}\"",
        ):
            assert_contains(text, expected, f"{path} missing URL contract text: {expected}")

        assert_order(
            text,
            'scanner_host_url="${SONAR_SCANNER_HOST_URL_INPUT}"',
            'scanner_host_url="${SONAR_SCANNER_HOST_URL_VAR}"',
            'doppler_scanner_host_url="$(doppler_get_optional SONAR_SCANNER_HOST_URL)"',
            'doppler_legacy_host_url="$(doppler_get_required SONAR_HOST_URL)"',
        )
        assert_not_contains(
            text,
            'sonar_host_url="$(doppler secrets get SONAR_HOST_URL',
            f"{path} must not unconditionally fetch the legacy scanner URL",
        )


def test_preflight_order_failure_classes_and_bounded_retry() -> None:
    for path, text in workflow_texts():
        assert_order(
            text,
            "      - name: Resolve SonarQube credentials and URLs",
            "      - name: Preflight SonarQube scanner URL",
            "      - name: Install Sonar scanner signature dependencies",
            "      - name: Setup Node for Sonar JS analyzer",
            "      - name: SonarQube scan",
            "      - name: Validate SonarQube scanner report",
            "      - name: SonarQube quality gate",
        )

        preflight = step_block(text, "Preflight SonarQube scanner URL")
        for expected in (
            "curl --silent --show-error --connect-timeout 5 --max-time 20",
            "/api/system/status",
            "/api/server/version",
            "max_attempts=3",
            "sleep_seconds=5",
            "while [ \"${attempt}\" -le \"${max_attempts}\" ]; do",
            "DNS resolution failed",
            "TCP connection",
            "Timed out connecting",
            "tunnel or upstream is unavailable",
            "requires authentication or proxy access headers",
            "STARTING|RESTARTING|DB_MIGRATION_NEEDED|DB_MIGRATION_RUNNING",
            "reachable but unhealthy",
            "preflight exhausted bounded retry policy",
        ):
            assert_contains(preflight, expected, f"{path} preflight missing guard: {expected}")


def test_scanner_signature_verification_dependencies_are_installed() -> None:
    for path, text in workflow_texts():
        install = step_block(text, "Install Sonar scanner signature dependencies")
        for expected in (
            "command -v dirmngr",
            "apt-get -o Acquire::Retries=3 install -y --no-install-recommends dirmngr",
            "not root and passwordless sudo is unavailable",
        ):
            assert_contains(install, expected, f"{path} signature dependency step missing guard: {expected}")
        assert_not_contains(
            text,
            "skipSignatureVerification: true",
            f"{path} must keep scanner signature verification enabled; install dirmngr instead",
        )


def test_report_task_validation_is_fail_closed_before_quality_gate() -> None:
    for path, text in workflow_texts():
        validation = step_block(text, "Validate SonarQube scanner report")
        for expected in (
            ".scannerwork/report-task.txt",
            "was not created",
            "file is empty",
            "for required_key in ceTaskId ceTaskUrl serverUrl; do",
            "report serverUrl does not match configured scanner URL",
            "exit 1",
        ):
            assert_contains(validation, expected, f"{path} missing report-task validation: {expected}")
        assert_order(text, "      - name: Validate SonarQube scanner report", "      - name: SonarQube quality gate")
        assert_not_contains(text, "continue-on-error", f"{path} must not mask scan or quality-gate failures")


def test_fork_safety_secret_handling_and_permissions() -> None:
    for path, text in workflow_texts():
        guard = step_block(text, "Refuse untrusted fork secret context")
        resolver = step_block(text, "Resolve SonarQube credentials and URLs")

        assert_contains(text, "on:\n  workflow_call:", f"{path} must remain reusable-only")
        assert_contains(text, "runs-on: ${{ inputs.runner-label != '' && inputs.runner-label || 'ubuntu-latest' }}", f"{path} runner-label input must control placement")
        assert_contains(text, "permissions:\n  contents: read", f"{path} top-level permissions must stay read-only")
        assert_contains(text, "pull-requests: read", f"{path} sonar job needs only PR read permission")
        assert_contains(text, "persist-credentials: false", f"{path} checkout should not persist credentials")
        assert_contains(guard, "PULL_REQUEST_HEAD_REPOSITORY", f"{path} must inspect PR head repository before fetching secrets")
        assert_contains(guard, "refuses to fetch Doppler secrets for untrusted fork", f"{path} must fail before Doppler on fork PRs")
        assert_order(text, "      - name: Refuse untrusted fork secret context", "      - name: Install Doppler CLI", "      - name: Resolve SonarQube credentials and URLs")

        for expected in (
            "DOPPLER_TOKEN: ${{ secrets.DOPPLER_TOKEN }}",
            "echo \"::add-mask::${sonar_token}\"",
            "mask_if_present \"${doppler_scanner_host_url}\"",
            "mask_if_present \"${doppler_legacy_host_url}\"",
            "mask_if_present \"${doppler_public_base_url}\"",
        ):
            assert_contains(resolver, expected, f"{path} missing secret handling: {expected}")

        for forbidden in ("pull_request_target:", "workflow_run:", "schedule:", "secrets.SONAR_TOKEN", "secrets.SONAR_HOST_URL"):
            assert_not_contains(text, forbidden, f"{path} weakens fork safety or legacy secret boundary: {forbidden}")


def test_actions_are_pinned_and_runtime_env_is_not_referenced_statically() -> None:
    for path, text in workflow_texts():
        for action in (
            "dopplerhq/cli-action",
            "SonarSource/sonarqube-scan-action",
            "SonarSource/sonarqube-quality-gate-action",
        ):
            pattern = rf"uses: {re.escape(action)}@[0-9a-f]{{40}} # v[0-9]+\.[0-9]+\.[0-9]+"
            assert re.search(pattern, text), f"{path} {action} must be pinned to full SHA with version comment"
        assert_contains(text, "uses: actions/checkout@v7", f"{path} official checkout should stay tag-pinned")
        assert_contains(text, "uses: actions/setup-node@v7", f"{path} Sonar job should provide Node for JS/CSS analysis")
        assert_contains(text, 'node-version: "22"', f"{path} Sonar job should install a supported Node runtime")
        assert_not_contains(text, "SONAR_TOKEN: ${{ env.SONAR_TOKEN }}", f"{path} runtime GITHUB_ENV token must not be referenced via expression env")
        assert_not_contains(text, "SONAR_HOST_URL: ${{ env.SONAR_HOST_URL }}", f"{path} runtime GITHUB_ENV URL must not be referenced via expression env")


def test_no_quick_tunnel_or_stale_public_scanner_urls() -> None:
    stale_host_pattern = re.compile(r"https?://[^\s'\")]*?(?:digitalocean|doks|trycloudflare)[^\s'\")]*", re.IGNORECASE)
    for path, text in workflow_texts():
        lower = text.lower()
        for forbidden in (
            "trycloudflare.com",
            "cloudflared tunnel --url",
            "digitalocean",
        ):
            assert_not_contains(lower, forbidden, f"{path} contains forbidden active scanner config: {forbidden}")
        assert not stale_host_pattern.search(text), f"{path} contains a stale public scanner URL"


def test_docs_examples_and_lint_contract_are_updated() -> None:
    lint = read(LINT_WORKFLOW)
    assert_contains(
        lint,
        "python scripts/ci_with_sonar_workflow_contract_test.py",
        "lint workflow should run bundle workflow contract tests",
    )

    for path in EXAMPLES:
        text = read(path)
        assert_contains(text, "sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}", f"{path} missing scanner variable example")
        assert_contains(text, "sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}", f"{path} missing public base URL example")
        assert_contains(text, "DOPPLER_TOKEN: ${{ secrets.DOPPLER_TOKEN }}", f"{path} must keep only Doppler token secret")


def main() -> None:
    test_all_bundle_workflows_expose_same_url_contract()
    test_preflight_order_failure_classes_and_bounded_retry()
    test_scanner_signature_verification_dependencies_are_installed()
    test_report_task_validation_is_fail_closed_before_quality_gate()
    test_fork_safety_secret_handling_and_permissions()
    test_actions_are_pinned_and_runtime_env_is_not_referenced_statically()
    test_no_quick_tunnel_or_stale_public_scanner_urls()
    test_docs_examples_and_lint_contract_are_updated()
    print("ci_with_sonar_workflow_contract_test: ok")


if __name__ == "__main__":
    main()
